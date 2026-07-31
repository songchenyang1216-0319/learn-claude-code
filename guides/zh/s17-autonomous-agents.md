# S17 实操教学指南：让队友自己发现、认领并完成任务

> 对应课程：[s17_autonomous_agents](../../s17_autonomous_agents/)
> 核心代码：[code.py](../../s17_autonomous_agents/code.py)
> 前置课程：[S16 Team Protocols](s16-team-protocols.md)
> 建议用时：160–210 分钟
> 本课产物：可扫描任务板、自动认领任务并在 WORK/IDLE 间切换的自主队友

## 1. 学完这一课，你应该能做到什么

完成 S17 后，你应该能够：

1. 区分“Lead 分配任务”和“队友从共享任务板拉取任务”；
2. 解释 `scan_unclaimed_tasks()` 的三个筛选条件；
3. 逐步追踪 pending→in_progress→completed 的状态变化；
4. 解释 `WORK → IDLE → WORK/SHUTDOWN` 生命周期；
5. 说明 inbox 为什么比任务板拥有更高检查优先级；
6. 观察依赖任务完成后，下游任务怎样变成可认领状态；
7. 解释 owner 检查为什么仍不能防止两个线程同时认领；
8. 复现自动认领只注入 subject、不注入 description 的信息缺口；
9. 复现 IDLE 中 shutdown 吞掉同批消息、计划回复未被语义路由等边界；
10. 把教学版扩展成有原子 claim、事件唤醒、租约、优先级、公平性和监督恢复的执行器。

本课最重要的一句话是：

> 自主不是“让模型随便做事”，而是让执行者在明确的候选集合、状态转换和权限边界内，自己决定何时领取下一份工作。

## 2. S17 解决的扩展性问题

S16 的协作方式仍以 Lead 推送为主：

```text
Lead 找一个任务
  → Lead 找一个空闲队友
  → Lead 发消息
  → 队友开始工作
```

如果有十个任务、四个队友，Lead 需要持续承担调度工作：

- 谁现在空闲；
- 哪个任务已解除依赖；
- 谁适合这个任务；
- 某次分配是否被接收；
- 某个队友完成后应该做什么。

S17 改成共享队列的 pull 模型：

```text
Lead 创建任务
  → 队友空闲时扫描任务板
  → 队友自己认领一个可执行任务
  → 完成后再次扫描
```

Lead 仍负责：

- 定义任务；
- 建立依赖；
- 启动队友；
- 审批计划；
- 请求关闭；
- 观察全局结果。

队友新增的自主权只是：

- 看见未认领任务；
- 判断依赖是否完成；
- 领取任务；
- 标记完成；
- 再找下一项工作。

## 3. 从 Push 到 Pull

两种调度模式可以这样比较：

| 维度 | Push：Lead 分配 | Pull：队友认领 |
|---|---|---|
| 调度发起者 | Lead | 空闲队友 |
| Lead 负担 | 随任务数增长 | 主要负责建模和监督 |
| 空闲响应 | 等 Lead 发现 | 队友周期性检查 |
| 负载分散 | Lead 显式决定 | 谁先空闲谁领取 |
| 专业匹配 | Lead 可人工判断 | 需要任务元数据和匹配规则 |
| 并发风险 | 重复分配 | 并发 claim |
| 公平性 | Lead 决定 | 取决于扫描顺序和竞争 |

S17 只实现了最小 Pull：

```text
扫描 → 取列表第一个 → 尝试 claim
```

它没有实现：

- 优先级；
- 技能匹配；
- 任务成本估计；
- 每个 Agent 的负载；
- 公平队列；
- 失败重试；
- 任务租约；
- 抢占。

## 4. 本课相对 S16 的增量与回退

新增：

- `scan_unclaimed_tasks()`；
- `idle_poll()`；
- 队友 WORK/IDLE 外层循环；
- 队友 `list_tasks`、`claim_task`、`complete_task` 三个工具；
- `claim_task()` 的 owner 检查；
- 60 秒空闲超时；
- 短上下文时的 identity 重注入。

沿用：

- S12 的 JSON 任务板；
- S15 的文件邮箱和线程式队友；
- S16 的 shutdown 与 plan 协议；
- Lead 的 14 个工具；
- 阻塞式主输入循环。

没有恢复：

- S11 的错误恢复状态机；
- S13 的 Background 工具；
- S14 的 Cron；
- S15 的 Lead 自动事件 poller；
- S16 README 所描述但代码中并不完整的无限 idle 机制。

课程代码是机制切片，不是每一章都把此前所有能力合并进去。

## 5. 先画出完整生命周期

```text
spawn teammate
      │
      ▼
┌─────────────┐
│ WORK        │
│ inbox → LLM │
│ → tools     │
└──────┬──────┘
       │ 模型不再调用工具，或达到 10 次调用
       ▼
┌─────────────┐
│ IDLE        │
│ 每 5 秒检查 │
└───┬─────┬───┘
    │     │
 inbox   task 可认领
    │     │
    └──┬──┘
       ▼
      WORK

IDLE 收到 shutdown ──────────┐
IDLE 60 秒无事可做 ─────────┤
WORK 收到 shutdown ─────────┤
                             ▼
                        发 result summary
                             │
                             ▼
                         移除 active
```

注意：

- “SHUTDOWN”没有独立函数或状态对象；
- 它只是跳出外层循环后的收尾代码；
- timeout 和正常 shutdown 都会发一条 `result`；
- API 异常也可能最终进入同一收尾路径。

## 6. 任务数据结构没有改变

```python
@dataclass
class Task:
    id: str
    subject: str
    description: str
    status: str
    owner: str | None
    blockedBy: list[str]
```

字段语义：

| 字段 | 用途 |
|---|---|
| `id` | 文件名和引用依赖时使用的标识 |
| `subject` | 列表中显示的短标题 |
| `description` | 完整工作要求 |
| `status` | pending、in_progress、completed |
| `owner` | 当前认领者 |
| `blockedBy` | 必须先完成的任务 ID |

任务保存在当前工作目录：

```text
.tasks/
  task_....json
```

队友、Lead 和所有线程共享这一目录。

## 7. “可认领”是三个条件的交集

`scan_unclaimed_tasks()` 的核心判断是：

```python
if (task.get("status") == "pending"
        and not task.get("owner")
        and can_start(task["id"])):
    unclaimed.append(task)
```

一个任务必须同时满足：

1. `status == "pending"`；
2. `owner` 为空；
3. `can_start(id)` 为真。

可以把候选集合写成：

```text
claimable
= pending
∩ unowned
∩ dependencies_completed
```

缺少任何一个条件都不会出现在返回列表里。

## 8. `pending` 与 `owner=None` 为什么都要检查

理想状态下，字段应保持一致：

```text
pending      ↔ owner is None
in_progress  ↔ owner is not None
completed    ↔ owner保留原认领者
```

但 JSON 文件可以被人工编辑，也可能在并发写入中产生不一致：

```json
{
  "status": "pending",
  "owner": "alice"
}
```

只检查 status 会把它交给另一个队友。

反过来：

```json
{
  "status": "in_progress",
  "owner": null
}
```

只检查 owner 会重新领取一个正在进行的任务。

S17 同时检查两个字段，能过滤部分坏状态，但不会主动修复它们。

## 9. `can_start()` 的依赖语义

```python
def can_start(task_id):
    task = load_task(task_id)
    for dep_id in task.blockedBy:
        if not _task_path(dep_id).exists():
            return False
        if load_task(dep_id).status != "completed":
            return False
    return True
```

含义不是“任务没有依赖”，而是：

> 每一个依赖文件都存在，而且每一个依赖都已 completed。

因此：

```text
A completed
B pending, blockedBy=[A]
```

B 可开始。

而下面两种都不可开始：

```text
A in_progress
B blockedBy=[A]
```

```text
A 文件不存在
B blockedBy=[A]
```

## 10. 扫描顺序不是优先级

```python
for f in sorted(TASKS_DIR.glob("task_*.json")):
```

候选任务按文件路径的字典序排列。

任务 ID 包含：

```text
task_<秒级时间戳>_<四位随机数>
```

通常较早创建的文件排在前面，但同一秒创建时会按随机后缀排序。

所以“取第一个”不等于：

- 最高优先级；
- 最紧急；
- 最短任务；
- 最适合当前角色；
- 创建顺序严格最早。

它只是稳定、简单的教学排序。

## 11. 扫描会重复读取文件

每个候选文件先读取一次：

```python
task = json.loads(f.read_text())
```

然后：

```python
can_start(task["id"])
```

又会读取当前任务，并逐个读取依赖。

若有 `n` 个任务、每项平均 `d` 个依赖，一轮扫描的文件读取量大致为：

```text
O(n × (1 + d))
```

当多个队友每五秒同时扫描时，读放大会更明显。

本课数据量很小，因此可以接受。

## 12. 一个坏文件会中断整个扫描

`scan_unclaimed_tasks()` 没有逐文件 `try/except`：

```python
task = json.loads(f.read_text())
```

只要 `.tasks/` 中有一个匹配 `task_*.json` 的损坏文件：

```text
{bad json
```

就会抛出 `JSONDecodeError`。

这个异常来自 IDLE 线程，而 `idle_poll()` 也不捕获它。

结果可能是：

- teammate thread 异常退出；
- 没有发送 result summary；
- `active_teammates` 没有清理；
- Lead 仍以为该队友活跃。

因此，持久化任务板需要隔离坏记录，而不是让一条记录击穿整个调度器。

## 13. 文件名里的 ID 与 JSON 里的 ID 可以不一致

扫描读取某个文件：

```text
.tasks/task_visible.json
```

但后续相信文件内部：

```python
can_start(task["id"])
```

如果 JSON 写成：

```json
{
  "id": "../other",
  ...
}
```

`_task_path()` 没有像 `safe_path()` 那样做工作区边界检查。

风险包括：

- 读取与当前文件不同的路径；
- 文件名与状态对象身份不一致；
- 使用 `../` 影响 `.tasks` 之外的 JSON 路径；
- 诊断时看到的文件与实际 claim 目标不同。

教学时不要把 `.tasks` 当成不可信输入；工程化时必须验证 ID。

## 14. `claim_task()` 的正常状态转换

成功路径：

```python
task = load_task(task_id)
...
task.owner = owner
task.status = "in_progress"
save_task(task)
```

磁盘变化：

```diff
- "status": "pending"
- "owner": null
+ "status": "in_progress"
+ "owner": "alice"
```

返回值：

```text
Claimed task_... (任务标题)
```

终端还会打印：

```text
[claim] 任务标题 → in_progress
```

## 15. `claim_task()` 的四种拒绝

### 已不是 pending

```text
Task <id> is completed, cannot claim
```

或：

```text
Task <id> is in_progress, cannot claim
```

### 已有 owner

```text
Task <id> already owned by alice
```

### 未完成依赖

```text
Cannot start — blocked by: [...]
```

### 缺失依赖

```text
Cannot start — missing deps: [...]
```

当两种依赖问题同时存在时，返回值会同时列出。

## 16. Owner 检查不是原子认领

代码的读—检查—写分为多个步骤：

```text
Alice load pending/unowned
Bob   load pending/unowned
Alice 检查依赖
Bob   检查依赖
Alice 写 owner=alice
Bob   写 owner=bob
```

两人都能返回：

```text
Claimed ...
```

但磁盘最终只保留后写者。

这叫 TOCTOU：

```text
Time Of Check
   与
Time Of Use
之间状态发生变化
```

S17 新增 owner 检查能阻止“晚到、重新加载后再认领”，不能阻止“同时读取后竞争写入”。

## 17. `save_task()` 也不是原子写入

```python
_task_path(task.id).write_text(json.dumps(asdict(task), indent=2))
```

问题包括：

- 没有文件锁；
- 没有写临时文件再 rename；
- 进程中断可能留下截断 JSON；
- 两个写者可能互相覆盖；
- 没有版本号或 compare-and-swap；
- 没有 fsync。

教学版的可靠性边界是单进程、小规模、低竞争。

## 18. `complete_task()` 没有校验完成者

函数只检查：

```python
task.status == "in_progress"
```

它不接收 owner，也不验证调用者。

所以：

```text
Alice 认领任务
Bob 调用 complete_task(task_id)
```

仍能完成。

队友 handler：

```python
def _run_complete_task(task_id):
    return complete_task(task_id)
```

也没有传入自己的名字。

“谁可以完成任务”目前完全依赖模型自律。

## 19. 完成任务怎样解除下游阻塞

完成后：

```python
task.status = "completed"
save_task(task)
```

随后扫描：

```python
unblocked = [
    t.subject for t in list_tasks()
    if t.status == "pending"
    and t.blockedBy
    and can_start(t.id)
]
```

返回结果可能是：

```text
Completed task_A (设计 schema)
Unblocked: 实现 API, 写集成测试
```

这只是一段提示：

- 不会主动通知其他队友；
- 不会触发事件；
- 不会立即启动下游任务；
- 下游任务要等某个队友下一轮扫描。

## 20. 队友工具从 5 个增加到 8 个

S17 teammate 工具：

1. `bash`
2. `read_file`
3. `write_file`
4. `send_message`
5. `submit_plan`
6. `list_tasks`
7. `claim_task`
8. `complete_task`

新增后三个让队友可以操作共享任务板。

但有一个重要缺口：

> 队友没有 `get_task` 工具。

Lead 有完整的 `get_task`，teammate 没有。

## 21. `list_tasks` 也不返回 description

队友版列表格式：

```python
f"{t.id}: {t.subject} [{t.status}]"
```

只包含：

- ID；
- subject；
- status。

不包含：

- description；
- owner；
- blockedBy。

因此，即使队友主动调用 `list_tasks`，也无法看到完整任务要求。

它可以用 `read_file` 读取 `.tasks/<id>.json`，但系统没有明确教它这样做。

## 22. 自动认领注入的信息也不完整

成功 claim 后只追加：

```python
<auto-claimed>
Task <id>: <subject>
</auto-claimed>
```

不包含 description。

于是一个任务：

```text
subject: 修复登录
description: 只允许修改 auth.py，并补三个失败用例
```

模型只会自动看到：

```text
修复登录
```

如果它不主动读取任务 JSON，就会丢失：

- 范围限制；
- 验收要求；
- 参考文件；
- 风险说明；
- 完成定义。

这是 S17 最值得亲手修复的信息链路缺口之一。

## 23. `idle_poll()` 的时间计算

默认常量：

```python
IDLE_POLL_INTERVAL = 5
IDLE_TIMEOUT = 60
```

循环：

```python
for _ in range(IDLE_TIMEOUT // IDLE_POLL_INTERVAL):
```

默认是：

```text
60 // 5 = 12 次
```

每次先 sleep 五秒，再检查。

所以：

- 新消息可能等待接近五秒；
- 新任务可能等待接近五秒；
- 第一次检查不是立即发生；
- 实际退出时间还要加上扫描和文件读取耗时。

若把值改成：

```text
timeout=10
interval=6
```

只会循环：

```text
10 // 6 = 1 次
```

大约六秒就超时，不会精确等十秒。

## 24. Inbox 优先于任务板

每轮顺序：

```text
sleep
  → read_inbox
  → 若无消息，scan_unclaimed_tasks
```

这样设计是合理的，因为 inbox 可能包含：

- shutdown 请求；
- Lead 新指令；
- 审批结果；
- 协作消息。

但它也意味着：

- 持续收到消息时，任务扫描可能一直推迟；
- 一条低价值消息也会先返回 WORK；
- 队友处理消息后还要经历一轮模型调用，才再次进入 IDLE。

优先级本身没有错，但需要避免 inbox 饥饿任务队列。

## 25. IDLE 收到普通消息

若 inbox 中没有 shutdown：

```python
messages.append({
    "role": "user",
    "content": "<inbox>" + json.dumps(inbox) + "</inbox>"
})
return "work"
```

整批消息作为原始 JSON 注入。

然后外层循环回到 WORK，模型能看到它们。

这里没有只筛选 `type == "message"`。

因此注释里的“Non-protocol inbox”与实际行为不完全一致：

- 普通消息会注入；
- plan approval response 也会原样注入；
- result 或未知类型也会原样注入；
- 只有 shutdown 被专门处理。

## 26. IDLE 中的 Plan Response 没有语义路由

WORK 阶段使用：

```python
handle_inbox_message(...)
```

它能把 plan response 转换为：

```text
[Plan approved] Proceed with the task.
```

或：

```text
[Plan rejected] Feedback: ...
```

IDLE 阶段没有调用这个 handler。

它只把原始消息放入：

```text
<inbox>[{... "type": "plan_approval_response" ...}]</inbox>
```

模型也许能自己理解，但：

- 行为依赖模型解析；
- approve 缺失时的默认含义不明显；
- WORK 与 IDLE 对同类消息的语义不一致；
- 无法保证审批 gate 被执行。

## 27. IDLE 中 Shutdown 会吞掉同批其他消息

`BUS.read_inbox()` 一次读完并删除邮箱文件。

如果批次是：

```text
1. message: 请先保存进度
2. shutdown_request
```

`idle_poll()` 扫到 shutdown 后：

```text
发送 shutdown_response
立即 return "shutdown"
```

同批普通消息：

- 已从文件中删除；
- 没有注入 messages；
- 没有重新排队；
- 没有记录为未处理。

shutdown 优先是对的，但“优先处理”不应该等于“丢弃其他消息”。

## 28. `read_inbox()` 是破坏性消费

```python
msgs = [...]
inbox.unlink()
return msgs
```

没有：

- acknowledgement；
- inflight 状态；
- retry；
- dead-letter queue；
- consumer lock；
- crash recovery。

一旦读出，消息就被视为完成。

如果调用者在处理前崩溃，消息永久丢失。

自主 Agent 的调度可靠性不仅取决于任务板，也取决于消息传输语义。

## 29. 自动 Claim 的成功判断依赖字符串

```python
result = claim_task(...)
if "Claimed" in result:
```

这不是结构化状态。

风险：

- 返回文案修改会破坏控制流；
- 错误信息若包含 `Claimed` 会被误判；
- 国际化后判断失效；
- 调用者无法区分冲突、阻塞、缺依赖等失败类型。

更稳妥的返回值：

```python
ClaimResult(
    ok=True,
    code="claimed",
    task=...
)
```

## 30. Claim 失败后的行为

扫描获得候选后，另一个队友可能抢先认领。

当前队友：

```text
claim failed
  → 不尝试候选列表中的第二个任务
  → 本轮结束
  → 再 sleep 5 秒
  → 重新扫描
```

如果有大量队友竞争同一个列表首项，就会产生：

- 同步唤醒；
- 同步扫描；
- 同步抢第一个；
- 失败者统一等待；
- 后面的可执行任务短暂闲置。

这就是 polling 加固定排序产生的惊群效应。

## 31. WORK 阶段最多调用模型十次

```python
for _ in range(10):
```

每一轮最多一次模型请求，可能伴随多个 tool call。

若第十次模型仍返回 `tool_use`：

- 工具照常执行；
- tool result 被追加；
- for 循环耗尽；
- 直接进入 IDLE；
- 不会先让模型读取最后一批结果。

IDLE 若发现消息或任务才返回 WORK。

若没有新事件：

- 60 秒后退出；
- 模型可能从未看到最后工具结果；
- 当前任务也可能仍是 in_progress。

十轮是预算，不是“任务已完成”的证明。

## 32. 模型说完话不等于任务完成

当：

```python
response.stop_reason != "tool_use"
```

WORK 阶段结束并进入 IDLE。

代码不会自动：

- 查找当前 owner 的任务；
- 检查产物；
- 调用 `complete_task`；
- 标记失败；
- 向 Lead 报告任务状态。

只有模型主动调用：

```text
complete_task(task_id)
```

任务才会 completed。

如果模型只说“完成了”，磁盘状态仍可能是 in_progress。

## 33. 初始 Prompt 先触发 WORK

teammate 创建时：

```python
messages = [{"role": "user", "content": prompt}]
```

它不是一启动就直接 IDLE 扫任务板。

先发生：

1. 插入 identity；
2. 调用模型处理 spawn prompt；
3. 模型可能调用工具或给出文本；
4. WORK 结束；
5. 才进入 IDLE。

因此推荐给 autonomous teammate 的初始 prompt 应简短：

```text
检查共享任务板，领取适合你的可执行任务；完成后标记 completed。
```

如果 prompt 本身要求一项具体工作，它可能先做 prompt，再去自动认领。

## 34. Identity 重注入实际做了什么

每次进入 WORK 前：

```python
if len(messages) <= 3:
    messages.insert(0, {
        "role": "user",
        "content": "<identity>...</identity>"
    })
```

它根据消息数量猜测“上下文很短”。

但 S17 teammate 本身没有调用 S08 的自动压缩逻辑。

因此这里并不能真正检测 compaction，只能检测长度。

此外：

- identity 是 user message，不是 system；
- name 和 role 未转义；
- 消息短不一定代表发生过压缩；
- 压缩后消息长也不一定小于等于三；
- system 字符串本来已包含姓名和角色。

这是一种提示性补丁，不是可靠身份机制。

## 35. Teammate 只发送最后一段文本摘要

退出后逆序扫描 messages：

```python
summary = "Done."
```

找到最近的 assistant text block 就用它。

它不保证这段文本：

- 是最终状态；
- 包含所有完成任务；
- 与磁盘一致；
- 不是 API 异常前的旧回复；
- 不是审批前的计划；
- 包含失败信息。

如果没有任何 text block，就发送：

```text
Done.
```

summary 是自然语言报告，不是任务板事实。

## 36. API 异常会被误装成空闲

WORK 中：

```python
try:
    response = client.messages.create(...)
except Exception:
    break
```

异常没有：

- 打印；
- 写入 messages；
- 发 failure；
- 重试；
- 标记任务失败；
- 触发监督器。

它只是结束 WORK，随后进入 IDLE。

若 60 秒没有新事件：

```text
发送旧 summary 或 Done.
移除 active
```

这会制造“任务正常结束”的假象。

## 37. Tool Handler 异常更严重

工具执行没有包在 `try/except` 内：

```python
output = handler(**block.input)
```

如果 handler 抛异常：

- teammate thread 直接异常终止；
- 不会进入 summary；
- 不会发送 result；
- 不会 `active_teammates.pop(...)`；
- 队友可能永久显示 active；
- 已认领任务可能永久留在 in_progress。

S17 的 autonomous lifecycle 没有 finally 清理。

## 38. 60 秒 Timeout 是存活窗口

IDLE timeout 后队友退出。

场景：

```text
Alice等待任务
  → 59秒时仍无任务
  → 60秒退出
  → 61秒时 Lead创建任务
```

任务不会被 Alice 认领。

又如：

```text
Alice等待被 Bob 的任务解除依赖
Bob 用了 61 秒
Alice 已退出
```

下游任务仍 pending，除非：

- 还有其他存活队友；
- Lead 再 spawn；
- 用户再介入。

timeout 限制资源占用，也缩短了自主性。

## 39. 没有 Idle Notification

S17 teammate 进入 IDLE 时不会向 Lead 发：

```text
alice is idle
```

Lead 只可能看到：

- 主动 `send_message`；
- plan request；
- shutdown response；
- 线程退出后的 result。

因此 Lead 不知道：

- 队友正等待；
- 队友已连续 claim 失败；
- 队友还有多少 timeout；
- 队友是否被某个坏任务文件卡死。

自主执行不应等于不可观察执行。

## 40. Lead 仍没有被后台事件自动唤醒

主循环使用阻塞：

```python
query = input(...)
```

只有用户输入一轮、`agent_loop()` 返回后，才：

```python
consume_lead_inbox(...)
```

即使 teammate 已经发来 result：

- 终端可能打印 bus 日志；
- Lead 的模型不会立刻运行；
- 消息要等用户下一次输入后才被消费；
- 被追加到 history 后，还要再下一次模型调用才会真正影响决策。

所以“teammate 自主”不等于“整个团队是自动推进的事件系统”。

## 41. Lead Inbox 的协议路由仍不验证发送者

`consume_lead_inbox()` 只检查：

```python
request_id
msg_type.endswith("_response")
```

然后调用：

```python
match_response(msg_type, req_id, approve)
```

没有验证：

- `msg["from"] == state.target`；
- `msg["to"] == state.sender`；
- 消息是否重复；
- 请求是否过期；
- payload 是否被篡改。

知道 request ID 的任意发件人都可以尝试解决状态。

## 42. S17 的重复 Response 可以反转状态

S17 `match_response()` 没有：

```python
if state.status != "pending":
    return
```

因此：

```text
第一次 approve=true  → approved
第二次 approve=false → rejected
```

状态可被迟到或重复消息反转。

这比“重复处理同一决定”更危险，因为最终结果取决于到达顺序。

相比之下，`run_review_plan()` 会拒绝 review 已非 pending 的状态；两条状态入口的规则并不一致。

## 43. `review_plan()` 仍缺少 Request Type Gate

函数只检查：

```text
request存在
状态为pending
```

不检查：

```python
state.type == "plan_approval"
```

把 shutdown request ID 传给 `review_plan()`：

- shutdown state 可变成 approved/rejected；
- `plan_approval_response` 会发给 `state.sender`；
- shutdown 的 sender 是 Lead；
- 于是 Lead 给自己的 inbox 发一条 plan response。

S17 继承了 S16 的这个协议缺陷。

## 44. WORK 与 IDLE 的 Inbox 语义不一致

| 消息类型 | WORK 阶段 | IDLE 阶段 |
|---|---|---|
| `shutdown_request` | handler 回复并退出 | 直接回复并退出 |
| `plan_approval_response` | 转成 approved/rejected 文本 | 原始 JSON 注入 |
| `message` | 筛选后注入 | 整批注入 |
| `result` | 被读取，但不进入 `non_protocol` | 原始 JSON 注入 |
| 未知类型 | 被读取后丢弃 | 原始 JSON 注入 |

一个协议的含义不应该取决于接收者当时恰好处于哪个生命周期阶段。

更好的设计是：

```text
read transport batch
  → route every envelope once
  → lifecycle receives normalized events
```

## 45. 多个队友共享同一个 Client 和工作目录

所有 thread 使用模块级：

```python
client
WORKDIR
TASKS_DIR
MAILBOX_DIR
```

因此：

- 模型请求并发使用同一个 client；
- Bash 在同一目录执行；
- read/write 修改同一文件；
- 任务板共享；
- 邮箱目录共享；
- 任意队友都能读写同一业务文件。

任务 claim 只解决“谁负责哪条任务记录”，不隔离文件系统修改。

这正是 S18 要解决的问题。

## 46. S17 的自主性边界

当前队友能自主：

- 在空闲时检查邮箱；
- 扫描任务板；
- 认领列表第一项；
- 调用模型处理工作；
- 自己标记任务完成；
- 等待下一项任务；
- 超时退出；
- 响应 shutdown。

当前队友不能可靠自主：

- 从自动 claim 获得完整 description；
- 根据角色匹配任务；
- 原子认领；
- 恢复崩溃任务；
- 续租；
- 报告健康状态；
- 自动重试 API；
- 处理死信；
- 安全隔离文件修改；
- 在 Lead 无用户输入时推进全局协调。

## 47. 运行前准备隔离目录

本课会：

- 创建 `.tasks/`；
- 创建 `.mailboxes/`；
- 允许模型运行 Shell；
- 允许多个线程写文件。

请在临时练习目录运行，不要直接把真实工作仓库交给多个 autonomous teammate。

### 47.1 Windows PowerShell

```powershell
cd D:\Projects\learn-claude-code
$lab = Join-Path $env:TEMP ("s17-lab-" + [guid]::NewGuid())
New-Item -ItemType Directory -Path $lab | Out-Null
Set-Location $lab
$env:PYTHONUTF8 = "1"
& D:\Projects\learn-claude-code\.venv\Scripts\python.exe `
  D:\Projects\learn-claude-code\s17_autonomous_agents\code.py
```

期望：

```text
s17: autonomous agents
Enter a question, press Enter to send. Type q to quit.

s17 >>
```

### 47.2 macOS / Linux

```bash
cd /path/to/learn-claude-code
lab="$(mktemp -d)"
cd "$lab"
PYTHONUTF8=1 /path/to/learn-claude-code/.venv/bin/python \
  /path/to/learn-claude-code/s17_autonomous_agents/code.py
```

如果使用自己的虚拟环境，也可以：

```bash
python /path/to/learn-claude-code/s17_autonomous_agents/code.py
```

## 48. 开始前检查环境

仓库根目录的 `.env` 至少需要：

```dotenv
ANTHROPIC_API_KEY=...
MODEL_ID=...
```

如果使用兼容服务，还可能需要：

```dotenv
ANTHROPIC_BASE_URL=...
```

出现：

```text
KeyError: 'MODEL_ID'
```

说明没有加载到模型 ID。

出现认证错误：

- 检查 API key；
- 检查 base URL；
- 检查 provider 配置；
- 不要把真实 key 写进课程产物。

## 49. 最小成功路径：一个队友自动认领

输入：

```text
Create one task named "write hello.txt" with a description saying the
file must contain exactly hello. Then spawn alice as a developer and ask
her to work from the shared task board. Do not claim the task yourself.
```

理想工具顺序：

```text
Lead create_task
Lead spawn_teammate
Alice 初始 WORK
Alice IDLE
Alice scan
Alice claim_task
Alice WORK
Alice write_file
Alice complete_task
Alice IDLE
Alice timeout
Alice result → Lead
```

终端关键日志：

```text
[teammate] alice spawned as developer
[claim] write hello.txt → in_progress
[idle] alice auto-claimed: write hello.txt
[complete] write hello.txt ✓
```

约 60 秒无新任务后：

```text
[idle] alice timeout (60s)
[bus] alice → lead: (result) ...
[teammate] alice finished
```

验收：

- `hello.txt` 存在；
- 内容符合要求；
- 对应任务 JSON 是 completed；
- owner 是 alice；
- Lead 没有先 claim。

模型的具体措辞和工具组合可能不同，以磁盘结果和任务状态为准。

## 50. 检查任务文件的三个时刻

创建后：

```json
{
  "status": "pending",
  "owner": null
}
```

Alice 认领后：

```json
{
  "status": "in_progress",
  "owner": "alice"
}
```

Alice 完成后：

```json
{
  "status": "completed",
  "owner": "alice"
}
```

Windows：

```powershell
Get-ChildItem .tasks
Get-Content .tasks\task_*.json
```

macOS/Linux：

```bash
ls -la .tasks
sed -n '1,160p' .tasks/task_*.json
```

不要只看终端的“Done”；任务文件才是持久状态证据。

## 51. 最小成功路径：依赖解锁

输入：

```text
Create task A "write plan.txt".
Create task B "write implementation.txt" blocked by A.
Spawn alice and bob as developers and let them pull work from the board.
Do not manually claim either task.
```

预期：

1. A 是 pending、unowned、可开始；
2. B 是 pending、unowned，但 blocked；
3. 某个队友认领 A；
4. 另一个队友扫描时看不到 B；
5. A completed；
6. B 的 `can_start()` 变为 true；
7. 下一轮扫描有人认领 B；
8. 最终两项 completed。

日志中可能出现：

```text
Completed ... (write plan.txt)
Unblocked: write implementation.txt
```

但队友分工不保证固定：

- Alice 可能连续完成 A 和 B；
- Bob 可能完成 A，Alice 完成 B；
- 扫描时机不同会产生不同结果。

验收只要求依赖顺序正确。

## 52. 最小成功路径：Inbox 唤醒

先输入：

```text
Spawn alice as a developer. Ask her to wait for instructions.
```

观察她结束初始 WORK 并进入 IDLE。

在 60 秒以内，给 Lead 新提示：

```text
Send alice a message asking her to create note.txt containing inbox-wake.
```

预期：

```text
[bus] lead → alice: (message) ...
```

Alice 最迟在约五秒后的轮询读到消息：

```text
[idle] alice found inbox messages
```

然后回到 WORK。

验收：

- `note.txt` 存在；
- 内容正确；
- 日志先显示 found inbox，后显示工具行为。

注意：若用户第二次输入太晚，Alice 可能已 timeout，需要重新 spawn。

## 53. 最小成功路径：IDLE Shutdown

输入：

```text
Spawn alice as a developer and let her become idle. Then request a graceful
shutdown for alice.
```

如果请求在 IDLE 阶段被读到，期望：

```text
[protocol] alice approved shutdown in idle (...)
[bus] alice → lead: (shutdown_response) Shutting down gracefully.
[bus] alice → lead: (result) ...
[teammate] alice finished
```

Lead 的 request state 只有在消费 response 后才会更新。

验证点：

- shutdown response 和 result 是两条消息；
- active teammate 最终移除；
- shutdown 批次中的其他消息目前可能丢失。

## 54. 离线验证：不调用真实模型

下面的实验只导入模块并调用调度函数。

Windows PowerShell：

```powershell
$env:PYTHONUTF8 = "1"
$env:MODEL_ID = "offline-test"
$env:ANTHROPIC_API_KEY = "offline-test"
python -c "import s17_autonomous_agents.code as c; print(len(c.TOOLS))"
```

从仓库根目录运行时，期望：

```text
14
```

为了避免污染仓库，实际实验最好先切到新建临时目录再 import。

导入会在当前目录创建：

```text
.tasks/
.mailboxes/
```

## 55. 离线实验：扫描三类任务

在临时目录中运行：

```python
import s17_autonomous_agents.code as c

a = c.create_task("A")
b = c.create_task("B", blockedBy=[a.id])
d = c.create_task("D")
c.claim_task(d.id, "alice")

print([t["subject"] for t in c.scan_unclaimed_tasks()])
```

预期：

```text
['A']
```

原因：

- A pending、unowned、无阻塞；
- B 依赖 A，暂不可开始；
- D 已 in_progress。

然后：

```python
c.complete_task(d.id)
c.claim_task(a.id, "bob")
c.complete_task(a.id)
print([t["subject"] for t in c.scan_unclaimed_tasks()])
```

预期：

```text
['B']
```

## 56. 离线实验：复现双重 Claim

使用 barrier 让两个线程在依赖检查处同时等待：

```python
import threading
import s17_autonomous_agents.code as c

task = c.create_task("race")
barrier = threading.Barrier(2)
original = c.can_start

def synchronized_can_start(task_id):
    barrier.wait()
    return True

c.can_start = synchronized_can_start
results = []

threads = [
    threading.Thread(
        target=lambda owner=owner:
            results.append(c.claim_task(task.id, owner))
    )
    for owner in ("alice", "bob")
]

for thread in threads:
    thread.start()
for thread in threads:
    thread.join()

c.can_start = original
print(results)
print(c.load_task(task.id).owner)
```

典型结果：

```text
['Claimed ...', 'Claimed ...']
alice
```

也可能最终 owner 是 bob。

关键不是最后谁赢，而是两次调用都报告成功。

## 57. 离线实验：坏任务文件击穿扫描

```python
bad = c.TASKS_DIR / "task_bad.json"
bad.write_text("{bad json")

try:
    c.scan_unclaimed_tasks()
except Exception as exc:
    print(type(exc).__name__)
```

预期：

```text
JSONDecodeError
```

清理坏文件后扫描才恢复：

```python
bad.unlink()
```

## 58. 离线实验：自动认领丢失 Description

为了不真等五秒，可以在一次性实验进程里：

```python
c.IDLE_POLL_INTERVAL = 1
c.IDLE_TIMEOUT = 1
c.time.sleep = lambda seconds: None

description = "SECRET-DESCRIPTION"
c.create_task("visible subject", description)
messages = []
result = c.idle_poll("alice", messages, "developer")

print(result)
print(messages[-1]["content"])
print(description in messages[-1]["content"])
```

预期：

```text
work
<auto-claimed>Task ...: visible subject</auto-claimed>
False
```

这说明 description 确实没有进入模型消息。

## 59. 离线实验：Shutdown 丢弃同批消息

```python
c.BUS.send("lead", "alice", "save progress", "message")
c.BUS.send(
    "lead", "alice", "stop",
    "shutdown_request",
    {"request_id": "req_demo"},
)

messages = []
result = c.idle_poll("alice", messages, "developer")

print(result)
print(messages)
print((c.MAILBOX_DIR / "alice.jsonl").exists())
```

在把 sleep 替换为空操作后，预期：

```text
shutdown
[]
False
```

普通消息既不在 messages，也不在邮箱文件。

## 60. 离线实验：IDLE Plan Response 未标准化

```python
c.BUS.send(
    "lead", "alice", "approved",
    "plan_approval_response",
    {"request_id": "req_plan", "approve": True},
)

messages = []
result = c.idle_poll("alice", messages, "developer")
text = messages[-1]["content"]

print(result)
print("[Plan approved]" in text)
print("plan_approval_response" in text)
```

预期：

```text
work
False
True
```

消息被原样注入，而不是走 WORK 阶段的规范化 handler。

## 61. 离线实验：重复 Response 反转状态

```python
c.pending_requests["dup"] = c.ProtocolState(
    request_id="dup",
    type="plan_approval",
    sender="alice",
    target="lead",
    status="approved",
    payload="demo",
)

c.match_response("plan_approval_response", "dup", False)
print(c.pending_requests["dup"].status)
```

预期：

```text
rejected
```

已 approved 的状态被迟到 reject 反转。

## 62. 十二个观察实验

这些实验先不改代码，只改变输入或任务文件。

### 实验 1：同时创建三个无依赖任务

启动 Alice 和 Bob。

观察：

- 谁认领第一个不固定；
- 两人可能并行处理；
- 同一个人也可能连续认领多个；
- 列表顺序受任务 ID 影响。

不要把“平均分配”当验收条件。

### 实验 2：创建一条依赖链

```text
A → B → C
```

预期：

- 同一时刻最多一个任务可认领；
- A completed 后 B 才出现；
- B completed 后 C 才出现。

### 实验 3：创建一个缺失依赖

令 B 的 `blockedBy` 包含不存在的 ID。

预期：

- B 永远不出现在扫描结果；
- `claim_task(B)` 返回 missing deps；
- timeout 前没有人能自动修复。

### 实验 4：手工制造 pending+owner

编辑任务 JSON：

```json
"status": "pending",
"owner": "ghost"
```

预期：

- 扫描不会返回它；
- 没有自动租约过期；
- 需要人工清 owner 或增加恢复器。

### 实验 5：手工制造 in_progress+owner=null

预期：

- 扫描也不会返回；
- 它成为孤儿任务；
- `claim_task` 因 status 拒绝。

### 实验 6：让任务 description 与 subject 差异很大

例如 subject 是“修复测试”，description 限制只能改一个文件。

预期：

- 自动 claim 消息没有限制内容；
- 模型是否自己读 JSON 不确定；
- 产物可能违反 description。

### 实验 7：在 Alice IDLE 时连续发送三条普通消息

预期：

- 同一批被放入一个 `<inbox>`；
- Alice 一次回到 WORK；
- 消息数组按文件中的行顺序出现；
- 没有逐条 ack。

### 实验 8：先发普通消息再发 Shutdown

预期：

- shutdown 被处理；
- 普通消息未注入；
- 邮箱文件被删除。

### 实验 9：让 API 请求失败

使用无效模型配置或 fake client 抛异常。

预期：

- WORK 静默结束；
- teammate 进入 IDLE；
- 最终可能发送 `Done.`；
- 任务不一定完成。

### 实验 10：让工具抛异常

用离线 fake handler 抛 `RuntimeError`。

预期：

- thread 终止；
- active 条目可能残留；
- Lead 收不到 result。

### 实验 11：在 timeout 后创建任务

等待：

```text
[idle] alice timeout
```

再创建 pending task。

预期：

- Alice 不会复活；
- 任务保持 pending；
- 需要启动新 teammate。

### 实验 12：两个任务同一秒创建

观察文件名随机后缀。

预期：

- scan 顺序按完整文件名；
- 不保证函数调用顺序；
- “先创建的先领取”可能不成立。

## 63. 修改实验：把 Description 注入 Auto-Claim

找到：

```python
messages.append({"role": "user",
    "content": f"<auto-claimed>Task {task['id']}: "
               f"{task['subject']}</auto-claimed>"})
```

改成：

```python
messages.append({
    "role": "user",
    "content": (
        "<auto-claimed>\n"
        f"id: {task['id']}\n"
        f"subject: {task['subject']}\n"
        f"description: {task.get('description', '')}\n"
        f"blockedBy: {task.get('blockedBy', [])}\n"
        "</auto-claimed>"
    ),
})
```

再运行第 58 节实验。

修改前：

```text
SECRET-DESCRIPTION in message → False
```

修改后：

```text
SECRET-DESCRIPTION in message → True
```

验收：

- 模型拿到完整要求；
- ID 与 subject 仍存在；
- 不把整个任意 JSON 无边界地拼进 prompt；
- 对 description 长度设置合理上限。

## 64. 修改实验：给 Teammate 增加 `get_task`

在 `sub_tools` 增加：

```python
{
    "name": "get_task",
    "description": "Get full task details.",
    "input_schema": {
        "type": "object",
        "properties": {
            "task_id": {"type": "string"}
        },
        "required": ["task_id"],
    },
}
```

在 `sub_handlers` 增加：

```python
"get_task": get_task,
```

修改 teammate system：

```text
After claiming a task, call get_task before editing files.
```

尝试：

- subject 写得很短；
- description 写明确的文件名、内容和验收要求；
- 观察模型是否先调用 get_task。

修改后期望：

```text
claim_task
get_task
read/write/bash
complete_task
```

注意：有工具不等于模型必然使用。若它是安全关键步骤，应在执行层强制，而不只写在 prompt。

## 65. 修改实验：单进程原子 Claim

最小教学改法是在进程内加锁：

```python
TASK_LOCK = threading.Lock()

def claim_task(task_id: str, owner: str = "agent") -> str:
    with TASK_LOCK:
        task = load_task(task_id)
        if task.status != "pending":
            return ...
        if task.owner:
            return ...
        if not can_start(task_id):
            return ...
        task.owner = owner
        task.status = "in_progress"
        save_task(task)
        return ...
```

重新运行第 56 节 barrier 实验时要调整 barrier 位置，避免 barrier 被锁内的第一个线程永久等待。

更直接的并发测试：

```python
results = []
threads = [
    threading.Thread(
        target=lambda owner=owner:
            results.append(claim_task(task.id, owner))
    )
    for owner in ("alice", "bob")
]
```

修改后期望：

- 只有一个结果以 `Claimed` 开头；
- 另一个看到 in_progress 或 owner；
- 磁盘 owner 与唯一成功者一致。

边界：

> `threading.Lock` 只保护同一个 Python 进程，不能保护两个独立进程。

## 66. 修改实验：跨进程 Claim Lock

可选方案：

- 平台文件锁；
- `proper-lockfile` 类库；
- SQLite transaction；
- 数据库 `SELECT ... FOR UPDATE`；
- 原子创建 lease 文件；
- 带版本号的 compare-and-swap。

推荐的状态转换接口：

```text
claim(task_id, expected_version, owner)
```

事务内部：

```text
读取状态
  → 验证 pending/unowned/deps
  → 更新 owner/status/version
  → commit
```

失败返回结构化冲突：

```json
{
  "ok": false,
  "code": "claim_conflict",
  "currentOwner": "alice",
  "version": 4
}
```

验收必须使用多进程，而不只是多线程。

## 67. 修改实验：原子保存任务 JSON

基本模式：

```python
def save_task(task):
    target = _task_path(task.id)
    temp = target.with_suffix(".json.tmp")
    temp.write_text(json.dumps(asdict(task), indent=2))
    temp.replace(target)
```

这样可以减少读者看到半份 JSON 的窗口。

仍需注意：

- 两个 writer 仍会 last-write-wins；
- Windows 上 replace 行为需实测；
- 临时文件名不能被多个 writer 共用；
- durability 还需要 flush/fsync；
- lock 与 atomic replace 解决的是不同问题。

修改后实验：

1. 循环保存较大任务 JSON；
2. 另一个线程持续读取；
3. 统计 `JSONDecodeError`；
4. 人为在 write 和 replace 之间终止进程；
5. 检查正式文件是否仍完整。

## 68. 修改实验：Task Schema 与 ID 校验

新增：

```python
ALLOWED_STATUS = {"pending", "in_progress", "completed"}

def validate_task_record(task, source_path):
    required = {
        "id", "subject", "description",
        "status", "owner", "blockedBy",
    }
    missing = required - set(task)
    if missing:
        raise ValueError(f"missing fields: {sorted(missing)}")
    if task["status"] not in ALLOWED_STATUS:
        raise ValueError("invalid status")
    if not task["id"].startswith("task_"):
        raise ValueError("invalid task id")
    if _task_path(task["id"]).resolve() != source_path.resolve():
        raise ValueError("task id does not match filename")
```

扫描时逐文件隔离：

```python
try:
    task = json.loads(f.read_text())
    validate_task_record(task, f)
except Exception as exc:
    quarantine(f, exc)
    continue
```

修改后期望：

- 一条坏记录不阻止其他任务被认领；
- 错误被记录；
- 坏文件进入 quarantine；
- path traversal ID 被拒绝；
- Lead 能看到数据质量告警。

## 69. 修改实验：校验 Complete Owner

接口改成：

```python
def complete_task(task_id: str, owner: str) -> str:
    task = load_task(task_id)
    if task.owner != owner:
        return (
            f"Task {task_id} owned by {task.owner}, "
            f"not {owner}"
        )
    ...
```

teammate handler：

```python
def _run_complete_task(task_id):
    return complete_task(task_id, owner=name)
```

Lead 是否能强制完成需要单独权限：

```text
complete_as_owner
force_complete_by_lead
```

不要把两者混在一个默认参数中。

修改后验收：

- Alice 认领；
- Bob complete 被拒绝；
- Alice complete 成功；
- Lead 强制动作产生审计日志。

## 70. 修改实验：统一 Inbox Router

建立一个阶段无关的路由函数：

```python
def route_teammate_inbox(name, inbox, messages):
    events = []
    for msg in inbox:
        msg_type = msg.get("type", "message")
        if msg_type == "shutdown_request":
            events.append({"kind": "shutdown", "message": msg})
        elif msg_type == "plan_approval_response":
            events.append({
                "kind": "plan_decision",
                "approved": msg.get("metadata", {}).get("approve", False),
                "message": msg,
            })
        elif msg_type == "message":
            events.append({"kind": "work_message", "message": msg})
        else:
            events.append({"kind": "unknown", "message": msg})
    return events
```

WORK 和 IDLE 都消费相同的 normalized event。

shutdown 时：

1. 记录 shutdown event；
2. 把同批非 shutdown 消息放回 pending queue，或标为 unprocessed；
3. 完成必要清理；
4. ack shutdown；
5. 退出。

修改后，第 59 节实验应看到普通消息仍可恢复，而不是消失。

## 71. 修改实验：可靠 Mailbox

将：

```text
读文件 → 删除文件
```

改成：

```text
pending
  → claim batch
  → inflight
  → process
  → ack
  → archive/delete
```

处理失败：

```text
inflight
  → retry_count + 1
  → pending
```

超过上限：

```text
dead-letter
```

每条消息至少需要：

```text
message_id
created_at
sender
recipient
type
payload
attempt
idempotency_key
```

验收：

- consumer 在处理前崩溃，消息仍可重投；
- 重投不会重复完成任务；
- shutdown 同批消息不丢；
- malformed message 不阻塞整个 mailbox。

## 72. 修改实验：用 Event 唤醒代替固定 Poll

单进程可用：

```python
WAKE_EVENTS: dict[str, threading.Event] = {}
```

发消息或创建任务后：

```python
event.set()
```

IDLE：

```python
event.wait(timeout=remaining)
event.clear()
```

仍需保留周期性扫描作为修复路径，避免丢失 wake signal。

预期改进：

- 新任务无需平均等待 2.5 秒；
- 没有工作时减少反复读盘；
- 多队友不会每五秒整齐惊群；
- timeout 可以按 deadline 精确计算。

跨进程可考虑：

- 文件 watcher；
- SQLite change counter；
- Redis stream/pubsub；
- 消息队列；
- 操作系统事件机制。

## 73. 修改实验：Deadline 而非整数轮数

当前：

```python
range(timeout // interval)
```

改成：

```python
deadline = time.monotonic() + IDLE_TIMEOUT

while True:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return "timeout"
    time.sleep(min(IDLE_POLL_INTERVAL, remaining))
    ...
```

使用 `time.monotonic()`，避免系统时钟调整影响 elapsed time。

验收：

- timeout=10、interval=6 时接近十秒退出；
- 扫描耗时被计入总预算；
- interval 大于 timeout 时仍会在 timeout 附近退出；
- 不发生除零。

## 74. 修改实验：Claim 失败后尝试下一个

当前只尝试：

```python
unclaimed[0]
```

改成：

```python
for task in unclaimed:
    result = claim_task(task["id"], name)
    if result.ok:
        ...
        return "work"
    if result.code not in {"claim_conflict"}:
        report_problem(task, result)
```

再增加随机起点或公平排序：

```python
random.shuffle(unclaimed)
```

随机只是缓解竞争，不提供公平保证。

更稳妥：

- 队列服务原子 pop；
- 数据库按 priority 和 created_at 选择并锁定；
- 每个角色独立队列；
- task-list 级调度锁。

验收：

- 多个候选时，首项竞争失败者会立即尝试第二项；
- 不再额外空等五秒；
- 每项只有一个 owner；
- 高优先级不会被随机长期饿死。

## 75. 修改实验：优先级、技能与亲和性

扩展 Task：

```python
priority: int
required_skills: list[str]
preferred_role: str | None
estimated_minutes: int | None
```

候选过滤：

```text
status/deps/owner
  → required skills
  → worktree or resource conflicts
  → policy
```

排序：

```text
priority desc
deadline asc
created_at asc
estimated cost asc
```

尝试创建：

- 一个高优先级 backend 任务；
- 一个低优先级通用任务；
- Alice role=frontend；
- Bob role=backend。

预期：

- Bob 优先拿 backend；
- Alice 拿通用或 frontend；
- 没有合适角色时任务保持 pending 或走 fallback；
- 调度原因可被解释。

## 76. 修改实验：Lease 与 Heartbeat

把永久 owner 改成租约：

```text
owner
lease_id
lease_expires_at
heartbeat_at
attempt
```

认领：

```text
pending → leased/in_progress
```

执行中：

```text
每 N 秒 heartbeat
```

队友崩溃：

```text
lease 到期
  → supervisor 验证
  → requeue
```

必须考虑：

- 原 owner 在网络暂停后恢复；
- 新 owner 已开始；
- 两者不能同时提交结果；
- complete 必须携带当前 lease token。

这叫 fencing token：

```text
只有最新 lease version 可以写最终状态
```

## 77. 修改实验：明确 Task Failure 状态

只用三种状态不够：

```text
pending
in_progress
completed
```

建议扩展：

```text
pending
in_progress
completed
failed_retryable
failed_terminal
cancelled
blocked
```

失败记录：

```json
{
  "attempt": 2,
  "last_error": {
    "type": "APIError",
    "message": "...",
    "at": 123.45
  },
  "next_retry_at": 130.00
}
```

API 异常不再进入“像正常 IDLE 一样的路径”。

验收：

- retryable 失败按 backoff 重试；
- terminal 失败不无限重试；
- 下游依赖知道失败而非永远等待；
- Lead 收到结构化 failure。

## 78. 修改实验：Supervisor 与 `finally`

最小清理：

```python
def run():
    try:
        ...
    except Exception as exc:
        BUS.send(
            name, "lead",
            f"{type(exc).__name__}: {exc}",
            "failure",
        )
    finally:
        active_teammates.pop(name, None)
```

更完整的 supervisor 记录：

```text
agent_id
thread/process id
phase
current_task
last_heartbeat
last_error
restart_count
```

注意：

- finally 清 active 不等于任务已恢复；
- 还要释放或过期 lease；
- result 与 failure 应分开；
- restart 需要幂等工具和 attempt 限制。

修改后重做工具异常实验。

预期：

- active 不残留；
- Lead 收到 failure；
- task 进入 retryable failure 或 lease 到期；
- 监督器决定重启，而非悄悄 `Done.`。

## 79. 修改实验：结构化 Summary

自然语言 summary 改成：

```json
{
  "agent": "alice",
  "reason": "idle_timeout",
  "tasks_completed": ["task_A"],
  "tasks_in_progress": [],
  "tasks_failed": [],
  "artifacts": ["hello.txt"],
  "last_error": null
}
```

Lead 使用机器字段决策，文本只用于补充。

验收时交叉核对：

```text
summary tasks_completed
  vs
.tasks 中的 completed/owner
```

不一致时以持久状态为事实，并产生 reconciliation 告警。

## 80. 修改实验：Plan Approval 变成真正 Gate

状态：

```text
draft
submitted
approved
executing
completed
```

工具执行前验证：

```python
def authorize_tool(agent, task, tool_call):
    if task.requires_plan and task.plan_state != "approved":
        return Denied("plan_not_approved")
```

而不是只给模型一条：

```text
[Plan approved] Proceed...
```

还要绑定：

- plan hash；
- task ID；
-允许的文件；
-允许的命令；
-批准者；
-审批时间；
-过期时间。

这样修改 plan 后不能继续复用旧 approval。

## 81. 修改实验：幂等与完成提交

任务可能因为 timeout、重试或消息重投执行多次。

每次 attempt 使用：

```text
attempt_id
idempotency_key
```

提交：

```text
prepare result
  → validate artifacts
  → compare current lease/version
  → commit completed
```

对外部副作用：

- 发邮件；
- 发布包；
- 创建 PR；
- 修改数据库；

必须使用幂等键或显式审批。

“任务函数可以重跑”是自主恢复的前提之一。

## 82. 测试矩阵

| 场景 | 初始状态 | 动作 | 预期 |
|---|---|---|---|
| 普通认领 | pending/unowned | Alice claim | in_progress/owner=Alice |
| 已有 owner | pending/Alice | Bob claim | 拒绝 |
| 已进行 | in_progress/Alice | Bob claim | 拒绝 |
| 完成任务 | completed | Alice claim | 拒绝 |
| 依赖完成 | B blockedBy A completed | scan | B 可见 |
| 依赖未完成 | A pending | scan B | B 不可见 |
| 依赖缺失 | missing ID | claim | missing deps |
| 双线程认领 | pending/unowned | 同时 claim | 原代码两者可能成功 |
| 错 owner 完成 | Alice owner | Bob complete | 原代码成功；修复后拒绝 |
| 坏 JSON | 一条损坏记录 | scan | 原代码抛异常 |
| ID 不匹配 | 文件名≠内部 ID | scan | 原代码信内部 ID |
| 自动注入 | 有 description | idle claim | 原代码不含 description |
| 普通 inbox | Alice idle | send message | 回到 WORK |
| plan response | Alice idle | approve | 原始 JSON 注入 |
| shutdown batch | message+shutdown | idle read | 原代码丢 message |
| API 异常 | WORK | create 抛异常 | 原代码转 IDLE |
| tool 异常 | WORK | handler 抛异常 | 原代码线程退出、active 残留 |
| timeout | 无工作 60 秒 | idle | result 后退出 |
| timeout 后任务 | 已退出 | create task | 不会自动复活 |
| 重复 response | approved | reject response | 原代码变 rejected |

最低自动化测试建议：

```text
unit
  scan filters
  dependency rules
  claim transitions
  inbox routing

concurrency
  simultaneous claim
  simultaneous mailbox append/read

fault injection
  corrupt JSON
  API failure
  tool failure
  crash after read before ack

integration
  A→B dependency
  two teammates
  shutdown during idle
  timeout and requeue
```

## 83. 本课综合挑战：实现一个小型 Worker Pool

目标：不要依赖模型偶然做对，构建可验证的自主任务执行器。

### 必做要求

1. Task 增加 version、priority、attempt；
2. claim 是原子的；
3. complete 校验 owner 和 lease token；
4. 自动认领注入完整任务要求；
5. WORK/IDLE 共用同一个 inbox router；
6. shutdown 不丢同批消息；
7. 坏任务进入 quarantine；
8. API/tool 异常产生 failure；
9. `finally` 清理 active；
10. 使用 deadline 计算 timeout；
11. 输出结构化 summary；
12. 至少覆盖测试矩阵中的 12 项。

### 进阶要求

1. 事件唤醒加周期性 reconciliation；
2. 角色/技能匹配；
3. lease、heartbeat、fencing token；
4. retry backoff 加 jitter；
5. dead-letter queue；
6. plan execution gate；
7. 指标和 trace；
8. 任务失败向下游传播；
9. supervisor 自动重启；
10. 多进程并发测试。

### 推荐目录

```text
worker_pool/
  model.py
  task_store.py
  scheduler.py
  lease.py
  mailbox.py
  protocol.py
  worker.py
  supervisor.py
  metrics.py
  tests/
```

### 验收场景

创建：

```text
A: 准备输入
B: 处理输入，blockedBy A
C: 独立任务
D: 汇总，blockedBy B+C
```

启动三个 worker，并在运行中：

1. 杀掉认领 B 的 worker；
2. 向另一个 worker 发 plan response；
3. 写入一个坏任务文件；
4. 发普通消息和 shutdown 的同一批次；
5. 等 lease 到期后恢复 B；
6. 最终完成 D。

验收：

- 每个任务只有一个有效完成提交；
- B 能恢复；
- 坏文件不影响其他任务；
- 消息不丢；
- D 必须最后完成；
- 所有 worker 状态可解释；
- 没有永久 active；
- 任务板与 summary 一致。

## 84. 常见问题与定位

### 队友没有立即认领

先判断：

- 它是否仍在初始 WORK；
- 是否需要等下一个五秒轮询；
- task 是否 pending；
- owner 是否为空；
- blockedBy 是否全 completed。

### 队友认领了但不知道该做什么

检查 description 是否只存在任务 JSON。

原代码 auto-claim 与 teammate list 都不提供 description。

### 两个队友都说认领成功

这是无锁读—检查—写竞争。

最终 owner 只是最后一次写入者，不代表另一人没执行。

### 任务一直 pending

检查：

- owner 是否意外非空；
- status 拼写；
- 依赖文件是否存在；
- 依赖是否 completed；
- teammate 是否已经 timeout；
- `.tasks` 是否有坏 JSON 让扫描线程崩溃。

### 任务一直 in_progress

可能：

- 模型忘记 complete；
- API 异常后进入 IDLE；
- tool 异常杀死线程；
- owner 已退出；
- 没有 lease/reaper。

### Alice 明明 idle，却收不到消息

确认：

- 是否还在 60 秒窗口内；
- 是否同名 active；
- 邮箱文件路径是否正确；
- 消息是否被其他消费者读走；
- 等待最多约五秒。

### Plan 批准后行为不一致

WORK 中会标准化，IDLE 中只注入原始 JSON。

修复方向是统一 router 和真正 execution gate。

### Shutdown 后前一条消息消失

IDLE 会整批 read+unlink，再遇到 shutdown 立即 return。

这是已知的破坏性消费问题。

### Lead 看不到 teammate 结果

Lead 没有自动唤醒。

需要用户再输入一轮让主循环消费 inbox；模型通常还要再一轮才看到追加历史。

### Teammate 显示 active 但线程不工作

很可能 tool/scan 异常跳过了收尾。

检查终端 traceback，并为 run 加 `try/finally`。

### API 失败却显示 Done

API 异常被静默 break，之后 timeout summary 默认是 `Done.`。

需要结构化 failure，而不是从旧文本推断状态。

### 修改 interval 后提前 timeout

`timeout // interval` 使用整数除法。

改为 monotonic deadline。

### 队友总抢同一类任务

当前按文件名取第一项，没有角色、优先级或公平策略。

需要结构化调度，而不是靠 prompt。

### Background 或 Cron 工具不见了

S17 没有合并 S13/S14 的机制。

这不是配置问题。

### 多个队友改坏同一个文件

claim 隔离任务责任，不隔离工作目录。

S18 将引入 worktree isolation。

## 85. 设计层面的延伸思考

### 自主性来自约束内选择

候选集合、权限、预算和提交规则越清楚，自主行为越可控。

### Claim 是分布式锁的一种

它不只是写 owner 字段，而是需要原子、租约、版本和故障恢复。

### “完成”必须是状态提交

模型的自然语言不应直接成为 completed 的证据。

### Pull 调度降低中心负担，但增加竞争

从 Lead 移走的复杂度，会进入锁、公平性、惊群和任务匹配。

### Polling 是最小实现，不是最终架构

事件通知降低延迟和开销，周期 reconciliation 提供漏事件后的恢复。

### Timeout 同时是成本策略和可用性策略

太短会错过新任务，太长会占用资源。更合理的是由 supervisor 管理 worker 生命周期。

### Description 是执行契约

只把 subject 交给 worker，相当于只给函数名、不传参数和后置条件。

### 生命周期阶段不应改变协议语义

WORK 与 IDLE 应共享同一个消息路由和状态机。

### 可观察性是自治的前提

系统越少依赖 Lead 手动盯守，越需要 heartbeat、指标、结构化事件和审计。

### 恢复要求副作用幂等

如果任务不能安全重试，自动 requeue 可能比停住更危险。

### 任务所有权不等于文件所有权

两个不同任务仍可能修改同一文件；需要资源锁或工作区隔离。

### 公平性必须显式定义

字典序、随机顺序、先到先得、优先级和角色匹配会产生不同的业务结果。

## 86. 结课自测

不要看答案，先尝试口头或书面回答。

1. `scan_unclaimed_tasks()` 的三个条件是什么？
2. 为什么 pending 和 owner 两个字段都要检查？
3. 缺失依赖与未完成依赖的返回信息有何不同？
4. 为什么 owner 检查不能防止同时 claim？
5. 什么是本课中的 TOCTOU？
6. 扫描为什么大致有 `O(n×d)` 的文件读取？
7. 坏 JSON 为什么能杀死 teammate thread？
8. 内部 ID 与文件名不一致有什么风险？
9. `complete_task()` 缺少哪项授权检查？
10. 下游任务解除依赖后为什么不会立即运行？
11. teammate 的八个工具是什么？
12. 自动认领消息缺少什么关键字段？
13. teammate 为什么不能直接调用 `get_task`？
14. 第一次 IDLE 检查为什么至少要等约五秒？
15. timeout 与 interval 不整除时有什么问题？
16. 为什么 inbox 可以让任务板饥饿？
17. IDLE 怎样处理 plan response？
18. shutdown 为什么会丢掉同批普通消息？
19. 为什么用 `"Claimed" in result` 不可靠？
20. claim 首项失败后为什么会多等一轮？
21. WORK 十轮耗尽时，最后 tool result 可能发生什么？
22. 模型说“完成”为什么不等于任务 completed？
23. teammate 启动后为什么不是立即扫描任务板？
24. identity 重注入为什么不是真正 compaction 支持？
25. summary 为什么可能是旧文本？
26. API 异常怎样伪装成正常结束？
27. tool handler 异常为什么会留下 active？
28. 60 秒 timeout 会怎样错过依赖解锁？
29. Lead 为什么不会被 result 自动唤醒？
30. 重复 response 怎样反转状态？
31. WORK 与 IDLE 的 inbox 语义有哪些差异？
32. Task claim 为什么不能防止文件互相覆盖？
33. 单进程锁和跨进程锁的边界是什么？
34. atomic replace 与 lock 分别解决什么问题？
35. lease 和 fencing token 各解决什么？
36. 为什么需要结构化 failure？
37. 如何避免 polling 惊群？
38. 如何为任务加入技能匹配？
39. 为什么任务恢复要求副作用幂等？
40. S18 最需要解决 S17 的哪个问题？

如果你能用实际代码路径回答至少 34 题，并完成最小成功路径和三个修改实验，就达到了本课目标。

## 87. 完成本课后的状态

你现在拥有的不是“永不停止的全自动团队”，而是一个最小自主 worker 模型：

```text
共享任务板
  + 可执行条件
  + 自动扫描
  + 自动认领
  + WORK/IDLE 生命周期
  + 依赖解锁
  + 协议消息
  + 超时退出
```

同时你应该清楚它还缺：

```text
原子 claim
  + description 完整交付
  + 可靠消息
  + owner/lease 校验
  + 故障恢复
  + 统一协议路由
  + 事件唤醒
  + 监督与可观察性
  + 工作目录隔离
```

下一课 S18 会处理最后一项最直观的问题：

> 即使 Alice 和 Bob 认领了不同任务，只要还在同一目录写文件，它们仍可能互相覆盖；Worktree Isolation 将把“任务责任隔离”推进到“文件系统修改隔离”。
