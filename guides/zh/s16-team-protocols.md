# S16 实操教学指南：用请求—响应协议协调 Agent 团队

> 对应课程：[s16_team_protocols](../../s16_team_protocols/)
> 核心代码：[code.py](../../s16_team_protocols/code.py)
> 前置课程：[S15 Agent Teams](s15-agent-teams.md)
> 建议用时：150–190 分钟
> 本课产物：带 request ID 的协议状态、类型路由、计划审批消息和优雅关闭握手

## 1. 学完这一课，你应该能做到什么

完成 S16 后，你应该能够：

1. 区分普通消息与需要关联响应的协议消息；
2. 解释 `ProtocolState` 的字段和 pending→approved/rejected 状态转换；
3. 逐步追踪 shutdown request 从 Lead 到 teammate 再回 Lead 的完整链路；
4. 说明计划申请、审批回复和执行门控是三个不同问题；
5. 解释 `request_id` 怎样关联请求与响应，以及当前还缺哪些身份校验；
6. 说明统一 Lead inbox consumer 为什么能避免协议回复被普通读取吞掉；
7. 分析 teammate idle loop 怎样接收新任务和 shutdown；
8. 复现计划批准在 idle 中不会唤醒模型、错误 request type 也能被 review 的实现缺陷；
9. 识别随机 ID 碰撞、无超时、无持久化、无锁和假完成等协议风险；
10. 把教学版扩展成有 schema、认证、超时、幂等、执行 gate、持久状态和监督恢复的协议引擎。

本课最重要的一句话是：

> 协议不是几种特殊文本，而是“谁对谁发起了哪种请求、哪个响应能解决它、在什么条件下状态才允许改变”的可验证状态机。

## 2. 为什么普通消息不够

普通消息：

```text
Lead → Alice: 请关机
Alice → Lead: 好的
```

当同时有多个请求时无法回答：

- “好的”对应哪次关机？
- 是批准计划还是批准关机？
- 是否重复回复？
- 请求是否已经过期？
- 回复者真的是 Alice 吗？
- Alice是否真的退出？

请求—响应协议增加：

```text
request_id
request_type
sender
target
state
response_type
decision
```

才能可靠关联。

## 3. 本课演示的两个协议

### 3.1 Shutdown

```text
Lead → teammate: shutdown_request
teammate → Lead: shutdown_response approve=true
```

### 3.2 Plan Approval

```text
teammate → Lead: plan_approval_request
Lead → teammate: plan_approval_response approve=true/false
```

共同状态：

```text
pending
  ├─ approve → approved
  └─ reject  → rejected
```

教学价值在通用关联机制，不在这两个具体业务。

## 4. 本课的能力边界与回退

Lead 工具仍为 14 个，但组成改变了。

包含：

- Bash、read、write；
- 五个 Task 工具；
- Background；
- spawn/send/check；
- request_shutdown；
- request_plan；
- review_plan。

不再包含 S15 的三个 Cron 工具，代码中也没有 Cron Scheduler。

S16 还移除了 S15 的 input/poller event queue，重新使用阻塞式主输入循环。

因此：

- teammate inbox不会自动唤醒 Lead；
- background完成不会自动唤醒 Lead；
- Lead只在一次用户 Turn结束后消费 inbox；
- Cron能力不在本章。

课程机制仍是切片，不是所有前章能力的单调累加。

## 5. `ProtocolState`

```python
@dataclass
class ProtocolState:
    request_id: str
    type: str
    sender: str
    target: str
    status: str
    payload: str
    created_at: float
```

字段：

| 字段 | 含义 |
|---|---|
| request_id | 请求与响应的关联键 |
| type | `shutdown` 或 `plan_approval` |
| sender | 原请求发起者 |
| target | 原请求接收者 |
| status | pending/approved/rejected |
| payload | 计划或原因 |
| created_at | 创建时间 |

保存位置：

```python
pending_requests: dict[str, ProtocolState]
```

仅进程内存，没有磁盘持久化。

## 6. Request ID

```python
req_{random.randint(0, 999999):06d}
```

示例：

```text
req_004281
```

只有 100 万空间，没有冲突检查。

碰撞时：

```python
pending_requests[req_id] = new_state
```

静默覆盖旧请求。

原请求未来的回复可能错误解决新请求。

应使用 UUID/ULID或带原子唯一约束的序列。

## 7. Protocol State 没有 Lock

可能访问它的线程：

- Lead主线程创建 shutdown；
- teammate线程创建 plan request；
- Lead消费回复更新状态；
- 多个 teammate并发 submit plan。

单个 dict操作在常见 CPython下不会撕裂，但：

- 生成 ID→检查→写不是原子事务；
- 冲突无法防止；
- 状态读取与更新无 compare-and-set；
- 两个 reviewer可能同时决定；
- 跨进程完全不共享。

协议状态需要锁或事务存储。

## 8. Shutdown 请求的创建

Lead工具：

```python
run_request_shutdown(teammate)
```

步骤：

1. 生成 request ID；
2. 创建 type=shutdown；
3. sender=lead；
4. target=teammate；
5. status=pending；
6. 写入 pending_requests；
7. 发 `shutdown_request` 到 teammate inbox；
8. 返回 ID。

它不检查：

- teammate是否 active；
- recipient是否存在；
- 是否已有 pending shutdown；
- teammate是否已经 stopped；
- request ID冲突。

给不存在的人发请求会永久 pending。

## 9. Teammate 怎样处理 Shutdown

每次检查 inbox 时，类型为：

```text
shutdown_request
```

处理器：

1. 读取 metadata.request_id；
2. 向 Lead发送 `shutdown_response`；
3. metadata含同一 ID和 `approve=True`；
4. 返回 True；
5. teammate循环退出。

它总是批准，没有 reject分支。

如果 request ID缺失，仍回复空 ID，Lead会记录 unknown。

## 10. “优雅”具体保证了什么

Shutdown只在：

- 下一次 outer loop顶部；
- 或 idle loop每秒检查

时被处理。

如果 teammate正在：

- 模型 API调用；
- 长 Bash；
- 大文件写入

不会中途强杀，而会等当前同步操作返回。

这比直接终止 thread安全，但没有：

- deadline；
- cancel 当前子进程；
- flush确认；
- Task release；
- artifact验证；
-强制 shutdown fallback。

## 11. Shutdown 会产生两条 Lead 消息

处理 shutdown 时先发：

```text
shutdown_response
```

退出循环后统一 summary逻辑还会发：

```text
result
```

所以 Lead可能收到：

1. Shutting down gracefully；
2. teammate此前最后一个 assistant文本，或 `Done.`。

`shutdown_response` 表示协议确认；`result` 表示最终工作汇报。两者概念不同，但当前 UI未清晰关联。

## 12. Lead 怎样匹配 Response

`consume_lead_inbox()` 读取所有消息。

对：

```text
metadata有 request_id
且 type 以 _response 结尾
```

调用：

```python
match_response(type, id, approve)
```

然后仍把原消息返回给调用方，用于显示或注入。

这保证 `check_inbox` 和主循环不管谁先读，都会先路由协议。

## 13. 类型匹配规则

若 state.type：

```text
shutdown
```

只接受：

```text
shutdown_response
```

若：

```text
plan_approval
```

只接受：

```text
plan_approval_response
```

错误 response type只打印 mismatch，不改变状态。

这避免某种协议的回复直接解决另一种协议。

## 14. Duplicate Response

若状态已经 approved/rejected：

```text
忽略重复
```

这提供基本幂等性。

但消息仍被 inbox consumer返回并可能注入模型，所以：

- 状态不重复改变；
- LLM仍可能看到重复文本并重复行动。

状态幂等与业务副作用幂等是两回事。

## 15. Response Sender 没有校验

`match_response()` 不接收完整 message，只接收 type/id/approve。

它不检查：

- msg.from是否等于原 target；
- msg.to是否为原 sender；
- team ID；
- sender generation；
- 签名。

离线实验证明：

```text
Mallory知道 Alice shutdown request ID
→ 发 shutdown_response approve=true
→ 状态变 approved
```

所以 request ID当前既是关联键，又被错误当作近似授权 token。

## 16. 缺失 `approve` 默认 Reject

consumer：

```python
meta.get("approve", False)
```

正确 type和ID、但没有 approve的响应，会把 pending改为 rejected。

更安全是 schema校验失败并保持 pending，而不是把 malformed message解释为业务拒绝。

## 17. Pending Request 永不清理

没有：

- timeout；
- expires_at；
- cancel；
- garbage collection；
-持久化；
-终态归档。

已解决请求也继续留在名为 `pending_requests` 的 dict。

长期运行会不断增长。

进程重启后所有状态丢失，而邮箱 response可能仍在，随后显示 unknown request_id。

## 18. Plan Solicitation 不是协议请求

Lead的：

```python
run_request_plan(teammate, task)
```

只发送普通 `message`：

```text
Please submit a plan for ...
```

它不创建 ProtocolState，也不返回 request ID。

真正的 plan approval request只有 teammate主动调用：

```text
submit_plan(plan)
```

时创建。

如果模型不调用 submit_plan，Lead没有 pending记录可追踪。

## 19. `submit_plan` 的行为

Teammate线程调用：

1. new request ID；
2. 创建 type=plan_approval；
3. sender=teammate；
4. target=lead；
5. status=pending；
6. payload=完整 plan；
7. 向 Lead发送 `plan_approval_request`；
8. tool result说 Waiting for approval。

由于所有 teammate在同进程，直接写共享 `pending_requests`。

跨进程团队需要由接收端根据消息持久创建状态，而不能依赖共享内存。

## 20. Plan 只有消息流程，没有执行 Gate

`submit_plan` 返回后，同一个 response中的其他 tool use仍会顺序执行。

例如模型一次发：

```text
submit_plan
write_file
bash
```

代码不会阻止 write/bash。

下一轮模型也可无视“Waiting”继续行动。

批准只是一条提示：

```text
[Plan approved] Proceed
```

不是执行层授权。

生产实现必须让危险 handler检查 approval capability/state。

## 21. `review_plan()` 缺少 Request Type 校验

它只检查：

- ID存在；
- status=pending。

不检查：

```python
state.type == "plan_approval"
```

因此把一个 pending shutdown ID传给 `review_plan()`：

- shutdown state直接变 approved/rejected；
- 发送 `plan_approval_response`；
- recipient使用 state.sender，即 lead；
- Lead给自己写一条 plan response。

离线验证已复现。

这绕过了 `match_response()` 本来实现的类型安全。

## 22. Review Recipient 为什么用 `state.sender`

Plan request方向：

```text
teammate(sender) → lead(target)
```

回复应回原 sender，所以：

```python
BUS.send("lead", state.sender, ...)
```

对正常 plan状态是正确的。

对错误类型的 shutdown state：

```text
sender=lead
```

就暴露了第21节问题。

通用协议框架应由 response routing规则决定 recipient，并先验证类型。

## 23. Teammate 的 Idle Loop

与 S15 不同，非 tool-use response后不退出。

它：

1. 每秒 sleep；
2. read inbox；
3. shutdown → 回复并退出；
4. plan response → route；
5. 普通消息 → append并返回外层模型循环。

这让 teammate理论上可以长期等待新任务。

没有 idle_notification，所以 Lead不知道它何时空闲。

## 24. Plan Approval 在 Idle 中不会唤醒模型

Idle收到 `plan_approval_response`：

```python
handle_inbox_message()
```

会把：

```text
[Plan approved] Proceed
```

追加进 messages。

但内层 loop只在：

```python
if non_protocol:
    break
```

时返回 LLM循环。

Plan response被归类为 protocol，不进入 `non_protocol`。

结果：

- approval已经写进 messages；
- teammate继续 idle；
- 模型调用数不增加；
- 直到另一个普通 message到达才继续。

离线测试：批准前后 call count都为2；再发普通 message后才变3。

这是本课核心流程的实际实现缺陷。

## 25. `non_protocol` 在 Idle 中没有重置

变量在 outer loop顶部创建。

如果 outer阶段已有普通 inbox：

1. append到 messages；
2. 列表没有 clear；
3. 模型 final进入 idle；
4. 后续新 inbox处理时继续复用旧列表；
5. 旧消息可能再次 append；
6. 新消息与旧消息一起重复。

每批 inbox都应使用新的局部数组。

## 26. Idle 醒来后会处理旧 Response

模型已返回 non-tool-use response，随后 idle等待普通消息。

普通消息到达后 inner loop break，代码继续向下执行：

```python
# Execute tool calls
for block in response.content
```

这里的 `response` 仍是之前那个 final response。

通常没有 tool use，于是：

```python
results=[]
messages.append(role=user, content=[])
```

然后才回 outer loop调用模型。

所以 idle唤醒会产生一个空 user content message。

更清晰的结构应在醒来后直接 `continue` outer loop。

## 27. Teammate 没有 10 轮上限了

S16使用：

```python
while not shutdown_requested
```

只要：

- 不 shutdown；
- API不异常；
-工具不异常；
-进程不退出

就可长期工作/idle。

这是真正生命周期上的提升，但带来：

- 上下文持续增长；
- `messages[-20:]`破坏 pair；
-成本无总上限；
-永远 stale线程；
-需要心跳和supervisor。

## 28. API 与 Tool 异常仍不可靠

API异常：

```python
except Exception:
    break
```

然后可能发 `Done.`，和 S15一样假成功。

Tool handler异常：

- 跳出整个 thread；
- summary不发；
- active不清；
-没有 finally。

协议层增加了状态，但没有修复 worker可靠性。

## 29. Lead 没有自动 Inbox Poller

主程序是：

```text
等待用户输入
→ agent_loop
→ consume_lead_inbox
→ 注入 history
→ 再等待用户输入
```

队友在 Lead等待 input时回复 shutdown：

- 文件出现；
- ProtocolState仍 pending；
- Lead不会自动 route；
- 用户必须再发送一次输入，或此前模型主动调用 check_inbox。

而且主循环把 inbox注入 history后不会立即再调用模型；要到下一次用户 query才让模型看到。

这是相对 S15 event queue的回退。

## 30. `check_inbox` 的优势与限制

Lead模型可以在同一 Agent Turn主动调用 `check_inbox`。

它：

- 调统一 consumer；
- routes response；
- 显示 type和request ID；
-每条内容截断200。

但 teammate回复时机异步。模型可能检查太早得到 empty，然后 final。

没有 wait/poll background工具，不能稳定完成同步握手。

## 31. Main Loop 注入丢失协议细节

主循环格式：

```text
From {from}: {content[:200]}
```

没有：

- type；
- request ID；
- approve；
- feedback结构。

状态已经在 Python中更新，但 LLM看到的上下文不够完整。

`run_check_inbox`反而会显示 type/req。

同一消息的两种消费路径呈现不一致。

## 32. Shutdown 状态何时真正 Approved

Teammate发 response时，pending_requests不会自动更新。

只有 Lead：

- `consume_lead_inbox()`；
- 或 `run_check_inbox()`

消费后才调用 match。

Teammate thread已可能退出，而 state仍 pending。

所以协议参与者生命周期和协调器观测状态可以短暂不一致。

## 33. Shutdown Approved 不证明清理成功

Teammate先发送 approve response，再继续：

- summary；
-active registry pop；
-thread退出。

response只是“接受请求”，不是“已完全 terminated”。

需要第三个事件：

```text
shutdown_requested
→ shutdown_accepted
→ teammate_stopped
```

或 response在清理完成后才发送。

## 34. MessageBus 继承 S15 所有风险

仍然：

- Agent名路径穿越；
- append无锁；
- read+unlink竞态；
-坏JSON卡死；
-无message ID；
-无ack；
-无sender认证；
-未知recipient静默创建；
-默认编码；
-消息超过200后显示丢失。

结构化 metadata不自动让传输可靠。

## 35. Protocol Message 的安全边界

消息 type和metadata来自文件内容，可被手工伪造。

当前没有 schema：

```json
{
  "type": "shutdown_response",
  "metadata": {
    "request_id": "known",
    "approve": true
  }
}
```

任何 sender都可写。

必须把：

- transport authentication；
- message validation；
- protocol authorization；
- state transition validation

组合起来。

## 36. 运行前准备隔离目录

### 36.1 Windows PowerShell

```powershell
cd D:\Projects\learn-claude-code
$lab = Join-Path $env:TEMP "learn-claude-s16"
New-Item -ItemType Directory -Force $lab | Out-Null
Set-Location $lab
$env:PYTHONUTF8 = "1"
$env:MODEL_ID = "你的模型 ID"
$env:ANTHROPIC_API_KEY = "你的 API Key"
& "D:\Projects\learn-claude-code\.venv\Scripts\python.exe" `
  "D:\Projects\learn-claude-code\s16_team_protocols\code.py"
```

### 36.2 macOS / Linux

```bash
LAB_DIR="$(mktemp -d)"
cd "$LAB_DIR"
export MODEL_ID="你的模型 ID"
export ANTHROPIC_API_KEY="你的 API Key"
/path/to/learn-claude-code/.venv/bin/python \
  /path/to/learn-claude-code/s16_team_protocols/code.py
```

第一次只操作临时文本文件。

## 37. 最小成功路径：Shutdown

第一条用户输入：

```text
Spawn alice as a researcher.
Ask her to create alice.txt, then remain idle.
```

等待文件完成。

第二条：

```text
Request graceful shutdown for alice.
Then check the inbox for her response.
```

模型可能立即 check得太早。如果 empty，再发送：

```text
Check the inbox again and report the shutdown request state.
```

验收：

- request ID相同；
- teammate发 shutdown_response；
- state从 pending到 approved；
- Alice thread最终移除；
- Lead还可能收到 result summary。

## 38. 最小成功路径：Plan

输入：

```text
Spawn bob as a developer.
Ask bob to submit a plan before editing any file.
```

Bob应调用 `submit_plan`。

下一次 Lead输入：

```text
Check the inbox. Find Bob's plan request ID.
Approve it with feedback: edit only bob.txt.
Then send Bob a normal message saying continue.
```

最后普通消息是当前实现绕过第24节 idle唤醒bug所需。

验收：

- plan state approved；
- Bob最终继续调用模型；
- 只编辑指定文件；
- 认识到“只因模型遵循”，不是代码 gate。

## 39. 离线验证 Response Type

直接创建：

```python
state = ProtocolState(
    "req_x",
    "shutdown",
    "lead",
    "alice",
    "pending",
    "",
)
```

调用：

```python
match_response(
    "plan_approval_response",
    "req_x",
    True,
)
```

预期：

```text
type mismatch
state仍 pending
```

再调用正确 shutdown response，变 approved。

## 40. 离线验证伪造 Sender

用 BUS从 Mallory给 Lead发送：

```text
shutdown_response
相同 request ID
approve=true
```

消费后当前 state变 approved。

这证明 type检查存在，但 sender检查缺失。

## 41. 离线验证错误 Review

创建 pending shutdown state，把 ID传给：

```python
run_review_plan(id, True)
```

当前预期：

- 返回 Plan approved；
- shutdown state变 approved；
- `plan_approval_response` 被写给 lead自己。

修复后应返回：

```text
Request is not a plan_approval request
```

## 42. 离线验证 Idle Approval Deadlock

Fake teammate：

1. 第一次 response调用 submit_plan；
2. 第二次 response final并进入 idle；
3. Lead approve；
4. 等超过一次 idle轮询。

当前模型调用次数不增加。

再给 teammate一条普通 message：

- 模型调用次数才增加；
- messages中还可能出现空 user content。

这是非常适合写成回归测试的场景。

## 43. 十个观察实验

### 实验 1：未知 Teammate Shutdown

预期请求永久 pending，邮箱文件存在。

### 实验 2：Request ID碰撞

固定 random，两次请求。

预期 dict只剩后者。

### 实验 3：缺 approve

伪造正确 response但无字段。

预期当前变 rejected。

### 实验 4：重复 Response

预期第一次改变状态，第二次忽略。

### 实验 5：进程重启

保留 response邮箱但丢 pending_requests。

预期 unknown request ID。

### 实验 6：Plan同轮绕过

Fake response同时包含 submit_plan和write_file。

预期写文件仍执行。

### 实验 7：Approval Feedback

approve=true且 feedback非空。

Teammate只收到固定 Proceed文字，批准反馈被忽略。

### 实验 8：Reject Feedback

reject时 content会进入 `[Plan rejected] Feedback`。

### 实验 9：Idle普通消息

预期能唤醒，但会追加空 results消息。

### 实验 10：Lead等待时回复

预期没有自动处理，直到下一次用户Turn/check_inbox。

## 44. 修改实验：唯一且持久的 Request ID

```python
request_id = str(uuid4())
```

State Store加唯一约束。

记录：

- protocol version；
- attempt；
- parent request；
- created；
- expires；
- resolved；
- resolution message ID。

进程重启后仍能关联邮箱回复。

## 45. 修改实验：Schema 与 Sender 验证

每种消息有单独 schema：

```text
ShutdownResponse:
  requestId
  decision
  senderAgentId
  generation
```

匹配时验证：

```text
response.sender == state.target
response.recipient == state.sender
response.team == state.team
response.protocol == expected
state.status == pending
now <= expires
```

任何失败都进入审计，不改变业务状态。

## 46. 修改实验：统一 Transition Table

```text
shutdown:
  pending + shutdown_response(accepted) → accepted
  accepted + teammate_stopped → completed
  pending + reject → rejected
  pending + timeout → timed_out

plan:
  pending + approve → approved
  pending + reject → rejected
  rejected + resubmit → superseded + new request
```

不要把不同协议强行压成只有 approved/rejected。

每条 transition定义允许 actor和副作用。

## 47. 修改实验：修复 `review_plan` Type Gate

```python
if state.type != "plan_approval":
    return (
        f"Request {request_id} is {state.type}, "
        "not plan_approval"
    )
```

再验证：

```text
state.target == lead
state.sender是active teammate generation
```

状态更新和 response持久化应在事务中完成。

## 48. 修改实验：让 Protocol Response 真正唤醒

Idle处理完 plan response后返回一个 dispatch结果：

```text
STOP
WAKE_MODEL
CONSUMED
```

若 `WAKE_MODEL`：

```python
continue outer_loop
```

不要依赖 `non_protocol` 非空。

同时每次 inbox batch新建列表，修复 stale列表。

## 49. 修改实验：修复 Idle 空 Tool Result

重构为：

```text
response=LLM
if tool_use:
  execute and append results
  continue

emit idle
event = wait_inbox()
dispatch(event)
if shutdown:
  break
continue
```

final response后不再落入旧 response的工具执行代码。

验收：idle普通消息后，下一条直接是新输入，不出现 `content=[]`。

## 50. 修改实验：Plan 执行 Gate

给 teammate状态：

```text
permission_mode = plan_required
approved_plan_id = None
```

在 tool dispatch前：

```text
若 tool是 bash/write/edit
且无 approved plan
→ 返回 blocked tool_result
→ 要求 submit_plan
```

批准 response携带：

- request ID；
- plan hash；
- allowed scope；
- expiry。

修改计划或超出路径后必须重新审批。

## 51. 修改实验：Protocol Timeout

每个 request：

```text
expires_at
```

Supervisor定期：

```text
pending 且过期 → timed_out
```

触发：

- 通知 sender；
- 可重试新 request；
- 不接受迟到 response；
- 保留审计。

Shutdown timeout后可选择强制终止，但必须明确可能损坏工作。

## 52. 修改实验：Plan Resubmit

Reject response含 feedback。

Teammate修正后创建新 request，旧 state：

```text
rejected
superseded_by=new_id
```

不要复用旧 request ID重新 pending，这会破坏审计和幂等。

Lead能查看版本链和 diff。

## 53. 修改实验：三阶段 Shutdown

```text
shutdown_request
  → shutdown_accepted
  → 停止接新任务
  → 完成/取消当前工具
  → flush messages/results
  → release Task
  → teammate_stopped
```

Lead只有收到 `teammate_stopped` 才移除 registry并认为完成。

超时后显示：

```text
accepted but not stopped
```

而不是笼统 approved。

## 54. 修改实验：统一 Lead Event Loop

恢复并扩展 S15的事件队列：

- user；
- Lead inbox；
- background；
- cron；
- protocol timeout；
- teammate stopped。

Protocol response到达立即 route并可启动新的 Lead Turn。

事件合并去重，避免 poller重复 wake。

## 55. 修改实验：可靠传输与 State Transaction

理想事务：

```text
读取 pending state
验证 response
写 resolution
标记 incoming message acked
写 outbound follow-up
commit
```

若无法跨文件事务，SQLite比 JSONL read/unlink更合适。

重启后能够：

- 重投未 ack response；
- 保持已 resolved幂等；
-恢复 pending timer。

## 56. 修改实验：Protocol Authorization

为每个 message type定义 actor：

| 类型 | 允许发送者 |
|---|---|
| shutdown_request | Lead/管理员 |
| shutdown_response | 请求 target |
| plan_approval_request | teammate |
| plan_approval_response | Lead/审批者 |
| permission_response | 用户授权代理 |

运行时身份来自 thread/session token，不来自模型参数。

## 57. 修改实验：可观察协议

日志：

```text
team_id
request_id
protocol
from/to
old_status
event
new_status
latency
message_id
```

不记录完整敏感 plan或命令，使用 hash/安全摘要。

指标：

- pending数；
- approval latency；
- timeout率；
- duplicate率；
- mismatch/forgery；
- shutdown duration；
- gate block次数。

## 58. 测试矩阵

至少覆盖：

| 场景 | 期望 |
|---|---|
| 正常 shutdown | accepted→stopped |
| shutdown reject | rejected |
| unknown target | 创建拒绝 |
| wrong sender | 忽略+审计 |
| wrong type | 忽略 |
| duplicate | 幂等 |
| missing approve | schema错误 |
| ID碰撞 | 不可能/创建失败 |
| timeout | timed_out |
| restart | 状态恢复 |
| plan approve | gate开放 |
| plan reject | gate保持 |
| plan同轮+write | write被阻止 |
| review shutdown ID | 拒绝 |
| approval idle | 立即唤醒 |
| idle普通消息 | 无空result |
| tool异常 | failed+清理 |
| shutdown中长工具 | deadline策略 |
| forged mailbox | 不改变状态 |
| result delivery crash | 可重投 |

用 fake clock、fake Bus、fake client和临时状态库。

## 59. 本课综合挑战：通用 Agent 协议引擎

最低要求：

1. 唯一持久 request ID；
2. 版本化 message schema；
3. sender/target/team认证；
4. 每协议 transition table；
5. compare-and-set状态更新；
6. timeout/cancel；
7. duplicate幂等；
8. plan type gate；
9. code-level execution gate；
10. idle protocol response自动唤醒；
11. 无空 tool result；
12.三阶段 shutdown；
13.可靠 transport与ack；
14.统一 Lead事件循环；
15.审计和指标；
16. 第58节自动化测试。

最终验收：

- 错类型、错sender、迟到或重复回复不改变状态；
- approve真正控制高风险工具；
- teammate在idle收到批准后立即继续；
- shutdown只有清理完成才成为 stopped；
- 崩溃和重启后请求仍可解释；
-不存在永久 pending无告警。

## 60. 常见问题与定位

### Shutdown 一直 pending

Lead尚未消费 response。当前没有自动 poller，发送新用户输入或让模型调用 check_inbox。

### Alice 已退出但 state仍 pending

Response文件已写，Python状态只有 consumer route后才更新。

### Plan approved但 Bob不继续

当前 idle loop不会因纯 plan response break。再发普通消息可验证该 bug；正式修复按第48节。

### `review_plan` 居然批准了 Shutdown

当前缺 state.type检查，属于实现缺陷。

### 收到 Shutdown response后又收到 Result

协议确认和退出summary各发一条，是当前行为。

### API失败却收到 Done

Teammate异常被吞掉，summary fallback误报。

### Teammate永久 active

Tool handler异常没有 finally清理。

### 重启后显示 unknown request ID

pending_requests只在内存，邮箱文件可能持久。

### 给不存在 Teammate发请求仍成功

没有 team registry验证，Bus会创建邮箱。

### Approval后仍可超范围写文件

没有 code-level gate或scope。

### Background完成没有自动通知

S16移除了S15 poller，只在 Lead工具轮collect。

### Cron工具不见了

S16代码没有继承Cron模块，14个工具用protocol工具替代了cron。

## 61. 设计层面的延伸思考

### Request ID 是关联，不是认证

知道 ID不能代表有权响应。身份和授权必须独立。

### 状态先于自然语言

LLM可以解释协议，但合法 transition应由确定性代码决定。

### Approval 必须控制执行层

只向模型说“等待批准”不是权限机制。

### 传输、协议、业务是三层

JSONL负责送达；ProtocolState负责关联；write/deploy gate负责业务权限。任何一层都不可省略。

### Idle 是生命周期状态

Idle Agent应能接收事件而不产生空消息，也不应靠模型轮询。

### Shutdown 是协作，不是一个布尔值

接受、drain、停止和清理需要不同状态。

### 异步协议必须考虑迟到

Timeout后迟到 approve不能复活已取消操作。

### 审批内容需要绑定

批准的应是 plan hash+scope，不是“某个 Agent以后所有行为”。

## 62. 结课自测

不看代码，回答：

1. 普通消息为什么无法可靠完成关机协商？
2. ProtocolState七个字段作用是什么？
3. Request ID怎样生成，有什么风险？
4. pending_requests是否持久？
5. Shutdown sender/target分别是谁？
6. teammate什么时候处理shutdown？
7. 当前能拒绝shutdown吗？
8. 为什么会收到shutdown response和result两条？
9. consume_lead_inbox为何统一？
10. `_response`消息如何路由？
11. 类型匹配防住了什么？
12. duplicate状态如何处理？
13. sender伪造为什么仍能通过？
14. 缺approve为什么会变rejected？
15. resolved request何时删除？
16. request_plan本身是否创建ProtocolState？
17. 真正plan request何时创建？
18. submit_plan是否阻止同轮write？
19. review_plan缺少什么关键检查？
20. 为什么错误shutdown ID会收到plan response？
21. teammate何时进入idle？
22. plan approval为何不能唤醒idle模型？
23. `non_protocol`复用会怎样？
24. idle普通消息为何产生空content？
25. S16还有10轮上限吗？
26. API/tool异常各产生什么错误状态？
27. Lead是否自动poll inbox？
28. inbox注入后模型会立即看到吗？
29. approved是否等于teammate已停止？
30. S16保留Cron了吗？
31. 如何把plan approval变成真正gate？
32. 如何验证response sender？
33. timeout后迟到response应怎样处理？
34. shutdown为何需要三阶段？
35.怎样用事务保证state和ack一致？

如果你能回答至少30题，并完成综合挑战，就真正掌握了本课。

## 63. 完成本课后的状态

你现在拥有：

```text
Protocol Request
  ├─ request_id
  ├─ type/sender/target
  ├─ pending state
  └─ MessageBus发送
          ↓
Teammate dispatch
  ├─ shutdown → response + exit
  └─ plan response → messages
          ↓
Lead unified inbox consumer
  ├─ type match
  ├─ duplicate check
  └─ approved/rejected
```

Teammate也从短命十轮循环升级为可 idle等待的线程。

也应该清楚教学版还缺少：

- 唯一持久ID；
- sender认证；
- timeout；
- type-safe review；
-plan执行gate；
-idle approval唤醒；
-正确消息控制流；
-自动Lead poller；
-三阶段shutdown；
-异常终态；
-可靠Bus；
-审计与恢复。

下一课 S17 会让 teammate不再等待 Lead逐个分配：它们会观察共享 Task Store，自主发现、认领和完成可执行任务。
