# S20 实操教学指南：把十九种机制归到同一个 Agent 循环

> 对应课程：[s20_comprehensive](../../s20_comprehensive/)
> 核心代码：[code.py](../../s20_comprehensive/code.py)
> 前置课程：[S19 MCP Plugin](s19-mcp-plugin.md)
> 建议用时：220–300 分钟
> 本课产物：同时包含权限、Hooks、Todo、子 Agent、技能、压缩、恢复、任务图、后台、Cron、团队、Worktree 和 MCP 的综合 Harness

## 1. 学完这一课，你应该能做到什么

完成 S20 后，你应该能够：

1. 从用户输入开始，完整追踪一次模型—工具—结果循环；
2. 指出每个机制在循环的进入点、执行点和恢复点；
3. 区分 Lead、subagent、teammate、background worker、cron worker 的执行边界；
4. 验证 27 个 builtin tool 与 26 个普通 handler 的特殊关系；
5. 解释 cron/background/todo reminder 的注入顺序；
6. 解释四层 compaction 和三类错误恢复怎样协作；
7. 识别权限只覆盖部分执行表面的系统性绕过；
8. 复现 plan reject 打开 gate、同批 tool_use 缺结果等协议一致性问题；
9. 复现 compact 与其他 tool_use 同批时“副作用已发生、结果却丢失”；
10. 复现后台异常永久 running、长结果只留 200 字符、同秒 transcript 覆盖；
11. 设计跨机制 invariant、统一 dispatcher、supervisor 和事件日志；
12. 为完整 Agent 建立可执行的端到端验收矩阵。

本课最重要的一句话是：

> 综合系统的正确性不是“每个组件单独能跑”的总和，而是所有执行路径都必须遵守同一组权限、状态、配对、恢复和审计不变量。

## 2. S20 没有发明新的“大脑”

核心仍是：

```python
while True:
    response = LLM(messages, tools)
    if not has_tool_use(response.content):
        return
    results = execute_tools(response.content)
    messages.append(tool_results)
```

S20 增加的是循环周围的 Harness：

```text
输入
  → 事件注入
  → 上下文预算
  → Prompt/Tool 组装
  → LLM 与恢复
  → 权限/Hooks
  → 同步或后台分发
  → Tool Result
  → 状态更新
  → 下一轮
```

模型负责选择。

Harness 负责：

- 能看见什么；
- 能调用什么；
- 是否允许；
- 在哪里执行；
- 失败怎么办；
- 如何恢复；
- 何时停止。

## 3. 一次 Lead Cycle 的准确顺序

```text
consume cron_queue
  → append [Scheduled]
collect completed background
  → append task_notification
todo轮数 >= 3?
  → append reminder
prepare_context
  → tool-result budget
  → snip
  → micro compact
  → summary compact if still large
update_context
  → memory/MCP/team
assemble_tool_pool
call_llm with retry
  → prompt-too-long recovery
  → max_tokens recovery
append assistant response
has actual tool_use?
  ├─ no → Stop hooks → return
  └─ yes → dispatch each block
              → compact special?
              → PreToolUse
              → background?
              → handler
              → PostToolUse
              → todo counter
              → collect tool_result
           append results + fast background notifications
           → next cycle
```

顺序会改变语义，不能随意交换。

## 4. 综合系统最重要的五个不变量

### Tool Pairing

每个留在 conversation 中的：

```text
assistant tool_use(id=X)
```

必须有：

```text
user tool_result(tool_use_id=X)
```

### Authorization

每个产生副作用的执行路径都必须经过同一权限策略。

### Ownership

只有合法 owner/lease 才能修改 task 或 worktree 状态。

### Event Durability

后台/Cron/Message 事件不能因为读出后崩溃而静默丢失。

### State–Report Consistency

自然语言 summary、Task、branch、background status 和 protocol state 必须可对账。

S20 的许多边界都可以归结为其中一个不变量被绕过。

## 5. 六种执行表面

| 表面 | 入口 | 工具池 | 权限/Hooks |
|---|---|---|---|
| Lead | `agent_loop` | 27 builtin + MCP | 是 |
| 一次性 subagent | `spawn_subagent` | 5 个固定工具 | 是 |
| 持久 teammate | thread `run` | 8 个固定工具 | 否 |
| Background | `start_background_task` | Lead handler snapshot | Pre 在主线程；Post 在 worker |
| Cron autorun | `cron_autorun_loop→agent_loop` | Lead 全工具 | 是，但可能在后台线程询问 |
| MCP | Lead dispatcher 中的 handler | 动态 | 名称启发式权限 |

“同一个代码文件”不等于“同一个安全管线”。

## 6. 27 个 Builtin Tool

```text
基础文件:
  bash, read_file, write_file, edit_file, glob

会话计划:
  todo_write

委派/知识/上下文:
  task, load_skill, compact

持久任务图:
  create_task, list_tasks, get_task,
  claim_task, complete_task

定时:
  schedule_cron, list_crons, cancel_cron

团队:
  spawn_teammate, send_message, check_inbox,
  request_shutdown, request_plan, review_plan

隔离:
  create_worktree, remove_worktree, keep_worktree

插件:
  connect_mcp
```

离线：

```python
len(BUILTIN_TOOLS) == 27
```

## 7. 为什么 Handler 只有 26 个

```python
len(BUILTIN_HANDLERS) == 26
```

缺少：

```text
compact
```

它在 dispatcher 中特殊处理：

```python
if block.name == "compact":
    messages[:] = compact_history(messages)
    ...
    break
```

所以：

```text
27 tool definitions
26 normal handlers
1 control-flow tool
```

这不是漏注册，但会让统一 registry 检查误报。

## 8. 模块 Import 就有副作用

导入 `code.py` 会：

- 读取 `.env`；
- 创建 `.tasks/`；
- 创建 `.worktrees/`；
- 扫描 `skills/`；
- 读取 `.scheduled_tasks.json`；
- 启动 cron scheduler daemon thread；
- 创建全局 client；
- 固化 `WORKDIR=Path.cwd()`。

只有在 `__main__` 中才会：

- 设置 `CLI_ACTIVE=True`；
- 启动 `cron_autorun_loop`；
- 进入用户 input loop。

因此“导入测试”也会启动 scheduler，但不会自动执行 queued Cron prompt。

## 9. `WORKDIR` 取启动目录

```python
WORKDIR = Path.cwd()
```

不是：

```text
code.py 所在目录
```

这决定：

- 文件工具根；
- skills；
- memory；
- tasks；
- mailboxes；
- worktrees；
- transcripts；
- durable cron。

从错误目录启动会把状态散落到意外位置。

## 10. Main CLI 的输入路径

每条用户输入：

```text
input
  → 空/q/exit?
  → UserPromptSubmit hooks
  → append history
  → acquire agent_lock
  → agent_loop
  → update context
  → print assistant text
  → release lock
  → consume Lead inbox
  → append [Inbox]
```

Lead inbox 仍在 agent loop 结束后才消费。

模型要真正看到 appended inbox，通常需要下一次调用。

## 11. UserPromptSubmit Hook 的返回值被忽略

主程序：

```python
trigger_hooks("UserPromptSubmit", query)
history.append(...)
```

没有：

```python
blocked = ...
if blocked: ...
```

所以未来 hook 即使返回：

```text
Permission denied
修改后的 prompt
注入内容
```

主程序也会忽略，原 query 仍进入 history。

当前 hook 只打印 WORKDIR，因此暂时没有可见错误。

## 12. `agent_lock` 没覆盖全部 History 修改

Cron autorun 在 lock 内修改 history。

主 CLI：

- 在获得 lock 之前 append 用户 query；
- 释放 lock 后 consume inbox 并 append。

所以 cron thread 可能正持锁运行 Agent 时，主线程仍：

- append query；
- append inbox。

`agent_loop()` 读取的共享 list 可能在运行中被另一线程改变。

统一规则应是：

> 所有 history 读写都在同一个 owner/event loop 或 lock 内。

## 13. `terminal_print()` 解决的是显示，不是输入并发

后台 thread 打印时尝试：

- 清当前行；
- 打印事件；
- 重新显示 prompt 和 readline buffer。

它改善 CLI 外观。

它不保证：

- 两个线程不会同时 `input()`；
- prompt buffer 一定恢复；
- Windows readline 可用；
- 输出原子；
- history 不竞争。

尤其 Cron 线程触发 permission prompt 时仍有输入竞争。

## 14. System Prompt 每轮重建

内容：

- identity；
- tool 总览；
- workspace；
- 当前本地时间；
- skill catalog；
- memory 前 2000 字符；
- connected MCP server。

每轮时间变化：

```text
Current time: ...
```

意味着 system prompt 文本每秒不同，不利于稳定 prompt cache。

好处是 reminder/Cron 计算能看到当前时间。

## 15. Context 只真正使用 Memory

`update_context()` 返回：

```python
{
    "memories": ...,
    "connected_mcp": ...,
    "active_teammates": ...,
}
```

但 `assemble_system_prompt()`：

- memory 从 context 读取；
- MCP 直接读全局 `mcp_clients`；
- active teammates 没有写进 prompt。

所以 `connected_mcp` 与 `active_teammates` 字段主要是观测快照，并非都参与 prompt。

## 16. Memory 的实际边界

只读取：

```text
.memory/MEMORY.md 前 2000 字符
```

S20 没有 memory write/search 工具。

Memory：

- 每轮刷新；
- 超过 2000 字符被截断；
- 没有 relevance retrieval；
- 没有 schema；
- 文件内容作为 prompt；
- 可能包含不可信指令。

长期记忆机制仍是教学简化。

## 17. Skill 只在 Import 时扫描

```python
scan_skills()
```

在模块加载阶段调用一次。

运行中创建：

```text
skills/demo/SKILL.md
```

不会自动出现在 catalog，除非代码再次调用 `scan_skills()`。

离线验证：

```text
创建前          → no skills
运行中写入后    → still no skills
手工 rescan 后  → demo visible
```

## 18. Skill Catalog 与按需加载

system 只放：

```text
- name: description
```

模型调用：

```text
load_skill(name)
```

才拿到完整 `SKILL.md`。

这是 progressive disclosure。

边界：

- name collision 后写覆盖；
- YAML meta 类型未严格验证；
- description 直接进入 system；
- content 无长度上限；
- 运行时不刷新；
- Skill 本身不获得额外权限。

## 19. Todo 与 Task Graph 是两层

Todo：

- `CURRENT_TODOS` 内存 list；
- 当前进程/会话；
- 无依赖、owner、worktree；
- 帮助模型保持步骤。

Task：

- `.tasks/*.json`；
- 可依赖；
- 可认领；
- 可绑定 worktree；
- 支持团队。

不要用 Todo 替代持久任务状态，也不要为两步小操作滥建 Task graph。

## 20. Todo Input 的兼容处理

`_normalize_todos()` 接受：

- list；
- JSON array string；
- Python literal string。

校验：

- 每项是 dict；
- 有 content/status；
- status 在 pending/in_progress/completed。

不校验：

- content 是 string；
- 只允许一个 in_progress；
- 重复项；
- extra fields；
- todo ID。

`CURRENT_TODOS` 不写磁盘。

## 21. Todo Reminder 怎样触发

全局：

```python
rounds_since_todo
```

同步工具成功分发后：

- `todo_write` → 置 0；
- 其他同步工具 → 加 1。

每个 LLM cycle 开头：

```text
>=3 → append <reminder>Update your todos.</reminder>
     → reset 0
```

计数单位其实是同步工具调用数，不是严格的“轮数”。

被拒绝和后台工具不会增加计数。

## 22. 一次性 Subagent

`task` 工具调用 `spawn_subagent()`：

- 独立 messages；
- 固定 system；
- 5 个工具；
- 最多 30 次模型调用；
- 同步阻塞 Lead；
- 最终只返回最近 text summary。

工具：

```text
bash
read_file
write_file
edit_file
glob
```

它不能：

- spawn；
- task graph；
- MCP；
- Cron；
- Todo；
- teammate。

## 23. Subagent 经过 Hooks/Permission

每个 subagent tool_use：

```text
PreToolUse
  → handler
  → PostToolUse
```

因此它继承主 permission hook。

但：

- 使用主 WORKDIR；
- 无独立 worktree；
- API 调用无 retry；
- tool exception 仅 `TypeError` 被统一 handler 捕获；
- 没有 Stop hook；
- 没有 compaction。

## 24. 持久 Teammate 不经过 Hooks/Permission

teammate handler 直接：

```python
output = call_tool_handler(...)
```

没有：

```text
trigger_hooks("PreToolUse")
trigger_hooks("PostToolUse")
```

离线 tracker 结果：

```text
teammate 执行 write_file
PreToolUse seen = []
```

所以 Lead 被禁止的命令，teammate 可能仍可执行。

这是综合安全模型中最严重的绕过之一。

## 25. 基础文件工具不再自带 Workspace 边界

S18 `safe_path()` 会拒绝逃出 base。

S20 的 read/write/edit：

```python
fp = (base / path).resolve()
```

没有 `is_relative_to(base)`。

边界完全交给 permission hook。

若用户对 `../outside.txt` 回答 yes：

```text
permission → allow
run_write  → 真的写到 workspace 外
```

这是允许 override 的设计，但 teammate 绕过 hook 后也能直接越界。

## 26. Permission Hook：Bash Deny

DENY_LIST：

```text
rm -rf /
sudo
shutdown
reboot
mkfs
dd if=
```

只做：

```python
if pattern in command
```

结果：

- `echo sudo is a word` 也拒绝；
- 大小写/空格/转义可绕过；
- shell 拼接和脚本内容难解析；
- `rm -rf /tmp/x` 包含 `rm -rf /`，可能误拒绝。

字符串启发式适合教学，不是 Shell 安全解析器。

## 27. Permission Hook：Bash Confirm

DESTRUCTIVE：

```text
"rm "
> /etc/
chmod 777
```

命中后在当前执行线程：

```text
Allow? [y/N]
```

非 y/yes：

```text
Permission denied by user
```

问题：

- background Cron thread 也可能调用 input；
- 无 approval ID；
- 无参数摘要；
- 无超时；
- 无 once/session policy；
- 无审计持久化。

## 28. Permission Hook：Path Confirm

适用：

```text
read_file
write_file
edit_file
```

用主 WORKDIR 判断路径。

不适用：

- glob；
- bash；
- Task/Worktree 内部文件；
- teammate；
- server 侧 MCP 文件访问。

对于 worktree teammate，本来应以 worktree 为授权边界；当前 hook 甚至没有 execution context 参数。

## 29. Permission Hook：MCP 名称启发式

规则：

```python
block.name.startswith("mcp__")
and "deploy" in block.name
```

因此：

```text
mcp__deploy__status   → 也询问
mcp__docs__deployment_guide → 可能误询问
mcp__release__trigger → 不询问
```

离线已验证：

```text
deploy status 被拒绝
release trigger 无 prompt
```

权限必须基于结构化 annotation/policy，不是名称子串。

## 30. Hook Pipeline 的短路

注册顺序：

```text
PreToolUse:
  1. permission_hook
  2. log_hook
```

`trigger_hooks()` 在第一个非 None 结果处 return。

所以被 permission 拒绝的调用：

- 不会继续到 `log_hook`；
- 只有 permission 文本；
- 审计 hook 可能漏记。

更合理：

```text
所有 audit observer 都运行
policy decider 汇总
mutation hook 有明确阶段
```

## 31. Hook Exception 没有隔离

callback 抛异常会穿透：

- 主 tool dispatch；
- subagent；
- background worker Post hook；
- Stop；
- UserPromptSubmit。

一个可选日志 hook 就可能让核心执行中断。

需要：

- 每 hook error policy；
- critical/noncritical；
- timeout；
- isolation；
- 顺序和结果合并规则。

## 32. `has_tool_use()` 比 Stop Reason 更可靠

```python
return any(
    block.type == "tool_use"
    for block in content
)
```

主 loop 和 subagent 都以实际 block 判断。

好处：

- 不完全依赖 provider 的 stop_reason；
- 混合 text+tool_use 仍继续；
- 测试可用 SDK object block。

但有 block 就必须维护 tool pairing。

## 33. Dispatcher 的准确分支顺序

对每个 tool_use：

1. `compact` 特殊处理；
2. PreToolUse；
3. permission blocked？
4. should background？
5. normal handler；
6. PostToolUse；
7. todo counter；
8. tool_result。

`compact` 在 PreToolUse 之前。

后台 tool 的 Post hook 在 worker thread 中。

## 34. Compact Tool 绕过 PreToolUse/Log

因为先判断：

```python
if block.name == "compact":
```

然后直接执行并 break。

所以：

- 不经过 permission；
- 不经过 log_hook；
- 不产生普通 tool_result；
- 不更新 todo counter。

它是控制流动作，应有独立 audit event。

## 35. Compact 与其他 Tool 同批会丢结果

响应：

```text
1. write_file(id=w1)
2. compact(id=c1)
```

实际：

1. write_file 已执行，副作用发生；
2. result 暂存在局部 `results`；
3. 遇到 compact，立即 summary/替换 messages；
4. break；
5. `compacted_now` → continue；
6. 局部 results 从未 append。

离线结果：

```text
side.txt 存在
conversation 中 w1 tool_result 不存在
Stop hook 统计 0
```

这是 Tool Pairing 与状态—报告一致性同时破坏。

## 36. Compact 应当是 Barrier

安全规则：

```text
一个 response 中若有 compact：
  只能有 compact
```

或：

```text
先为所有已执行 tool_use 持久化结果
再在完整配对边界 compact
未执行的 block 返回 deferred/cancelled result
```

不能执行副作用后丢弃结果。

## 37. Plan Submit 也存在同批 Pairing 问题

teammate response：

```text
1. submit_plan(id=p1)
2. write_file(id=w1)
```

代码：

- 执行 submit_plan；
- 设置 waiting_plan；
- break，忽略后续 block。

追加的 result 只有：

```text
p1
```

但 assistant message 仍含 `w1`。

下一模型调用收到 orphan tool_use。

真实 API 可能拒绝 conversation；fake client 已验证 `w1` 缃果缺失。

## 38. Plan Gate 在 Reject 后也会打开

匹配 request ID 后：

```python
protocol_ctx["waiting_plan"] = None
```

不区分 approve true/false。

Reject 只追加：

```text
[Plan rejected] feedback
```

然后模型可再次调用任意工具。

离线 fake client：

```text
reject response
  → write_file after_reject.txt
  → 文件成功创建
```

所以它是“等待 response gate”，不是强制“approved execution gate”。

## 39. Plan Gate 的改善与剩余缺口

相比 S16/S17，S20 改善：

- submit 后暂停模型调用；
- 只在 matching request ID 时清 waiting；
- 同批后续工具不立即执行；
- IDLE 之前保持等待。

仍缺：

- reject 后禁止执行；
- missing tool_result；
- sender 验证；
- plan/task/hash 绑定；
- 持久化 waiting state；
- timeout/cancel；
- resubmit 状态机；
- tool layer authorization。

## 40. `run_review_plan()` 仍不验证 Type/Status

它只检查 state 存在。

不检查：

- `type == plan_approval`；
- status 仍 pending；
- reviewer 权限；
- plan payload；
- target；
- 重复 review。

可以反复改变 state，并对 shutdown request ID 发 plan response。

综合代码改善了 teammate local gate，没有修复 Lead 协议入口。

## 41. Context Compaction 的四层

LLM 前：

```text
tool_result_budget
  → snip_compact
  → micro_compact
  → estimate_size > 50000?
       → compact_history
```

目的不同：

| 层 | 解决什么 |
|---|---|
| result budget | 当前一批结果过大 |
| snip | 消息数量超过 50 |
| micro | 旧 tool result 太多 |
| summary | 总序列仍大 |

顺序先做便宜的确定性压缩，再调用模型摘要。

## 42. Size 只是 JSON 字符数

```python
len(json.dumps(messages, default=str))
```

它不是 tokenizer。

误差来自：

- Unicode；
- JSON escaping；
- 模型 tokenizer；
- system prompt；
- tools schema；
- 图片/资源；
- provider overhead。

`CONTEXT_LIMIT=50000` 是教学阈值，不代表模型真实 context window。

## 43. Large Result Budget 只检查最后一条 Message

```python
last = messages[-1]
```

只有 last 是 user list 且含 tool_result 才处理。

若历史早期有 250,000 字符结果，最后一条是普通 user 文本：

```text
旧大结果保持 250,000
```

它可能随后被 micro compact，但 budget 本身不是全局预算器。

## 44. Persisted Output

当单项输出超过 30,000 且当前批次总量超过 200,000：

```text
.task_outputs/tool-results/<tool_use_id>.txt
```

message 中替换为：

```text
Full output: <path>
Preview: 前2000字符
```

风险：

- tool_use_id 未作为文件名严格验证；
- 同 ID 不覆盖，可能引用旧内容；
- 文件无清理策略；
- secret 原样落盘；
- micro compact 后 path marker 可能也被替换；
- 模型没有专门读取持久结果工具，只能 read_file。

## 45. Snip 保持边界 Pair

超过 50 条时保留：

- 头部约三条；
- 尾部；
- 中间一个 `[snipped N messages]`。

代码会调整边界，避免：

- 只保留 assistant tool_use；
- 或只保留 user tool_result。

仓库测试专门验证 head/tail tool pair。

它只保护边界配对，不保证被删部分的业务信息已摘要。

## 46. Micro Compact

收集所有 tool_result，保留最近三项完整。

更早且内容长度 >120：

```text
[Earlier tool result compacted. Re-run if needed.]
```

优点：

- 简单；
- 不额外调用模型；
- 保持 tool_result block 和 ID。

代价：

- 丢具体证据；
- “重跑”可能有副作用；
- persisted path 也可能丢；
- 最近三项按全历史 block 顺序，不按重要性。

## 47. Summary Compact

步骤：

1. 写 transcript；
2. 序列化 messages；
3. 取前 80,000 字符；
4. 调模型摘要；
5. 整个 history 替换成一条 user summary。

问题：

- 取前部可能遗漏最近目标/剩余工作；
- transcript 未脱敏；
- summary call 无 retry；
- 同秒文件名碰撞覆盖；
- 只保留模型生成的单一解释；
- tool pairing 通过“全部删掉”解决，但事实可能丢失。

## 48. Transcript 同秒覆盖

文件名：

```text
transcript_<int(time.time())>.jsonl
```

同一秒两次 compact：

```text
path相同
第二次 open("w")
覆盖第一次
```

离线已验证最终只含 second。

应使用：

- UUID；
- monotonic sequence；
- exclusive create；
- session/turn ID。

## 49. Reactive Compact

模型报 prompt too long：

- 保存 transcript；
- 保留最后五条；
- 若切在 tool_result 上，向前带上 assistant tool_use；
- 摘要更早部分；
- 组成 summary + tail；
- 整个 agent_loop 最多尝试一次。

这是失败后的恢复，不是日常压缩。

若 summary 也失败，使用固定 fallback 文本。

## 50. 429/529 Retry

`with_retry()` 最多三次。

Delay 大约：

```text
0.5s, 1s, 2s
+ 0–25% jitter
```

识别靠：

- exception class name；
- message 子串。

三次都失败后抛：

```text
RuntimeError: Max retries (3) exceeded
```

原始 cause 没有链入最终文本。

## 51. Fallback Model

连续两次 529 且设置 `FALLBACK_MODEL_ID`：

```text
state.current_model = fallback
```

离线验证：

```text
前两次 529
第三次成功
current_model 仍是 fallback
```

成功会清 `consecutive_529`，不会切回 primary。

fallback 只在当前 `agent_loop` 的 RecoveryState 生命周期内持续。

## 52. Max Tokens Recovery

第一次 `stop_reason=max_tokens`：

- 不追加 partial response；
- max_tokens 从 8000 提高到 16000；
- 用原 history 重试。

第二次仍 max：

- 追加 partial assistant；
- 添加 continuation prompt；
- 最多两次。

风险：

- partial 中若含 tool_use，不执行；
- continuation user message 不提供对应 tool_result；
- 可能违反 Tool Pairing；
- recovery_count 成功后不重置。

恢复策略必须感知 content block 类型。

## 53. Stop Hook 只在正常无 Tool 时触发

触发条件：

```text
response 中没有 tool_use
```

不保证在：

- API 最终错误；
- max_tokens recovery 耗尽；
- compact exception；
- hook exception；
- tool handler 非 TypeError exception；

时执行。

它不是 `finally`。

## 54. Background 判定

只对 bash。

显式：

```text
run_in_background=true
```

或命令包含：

```text
install, build, test, deploy, compile,
docker build, pip install, npm install,
cargo build, pytest, make
```

字符串匹配会误判：

```text
echo latest test results
```

离线结果为 slow=True。

## 55. Background 分发顺序

```text
PreToolUse
  → start daemon thread
  → 立即返回 placeholder tool_result
  → worker 调 handler
  → PostToolUse
  → status=completed + 保存 result
```

主模型先看到：

```text
[Background task bg_0001 started]
```

真实结果稍后变成 task_notification。

## 56. Background Exception 会永久 Running

worker 没有 `try/finally`：

```python
result = call_tool_handler(...)
trigger_hooks(...)
status = completed
```

`call_tool_handler` 只捕获 TypeError。

RuntimeError 或 Post hook error：

- thread 退出；
- status 仍 running；
- 无 error；
- 永远不会 collect；
- 无 supervisor。

离线已验证 `bg_0002` 保持 running。

## 57. Background Result 只留下 200 字符

collect：

```python
summary = output[:200]
```

生成 notification 后：

- 从 `background_tasks` 删除；
- 从 `background_results` 删除；
- 没有持久完整结果；
- 没有 get-background-result 工具。

500 字符输出最终只剩 200。

这不是压缩后可恢复，而是永久丢失。

## 58. Background 不会单独唤醒 Agent

完成后只更新内存 dict。

注入发生：

- 当前工具批次结束得够快时；
- 下一次 agent cycle；
- 下一次用户输入；
- 下一次 Cron autorun。

若 Lead 已返回并无人再触发：

```text
background completed
模型不知道
```

需要统一 event wakeup。

## 59. Cron 的两个 Thread

Import 时：

```text
cron_scheduler_loop
```

每秒匹配并入 queue。

CLI `__main__` 时：

```text
cron_autorun_loop
```

每秒消费 queue，持 agent_lock 调 `agent_loop`。

此外正在运行的 `agent_loop` 每 cycle 也消费 queue。

所以 queue 有两个消费者，但 agent_lock 限制两个 agent loop 同时运行。

## 60. Cron Prompt 的两种注入标签

如果当前 agent loop 消费：

```text
[Scheduled] prompt
[cron inject]
```

如果 autorun loop 先消费：

```text
[Scheduled] prompt
[cron auto]
```

最终都是 user role prompt。

Cron prompt 不经过 `UserPromptSubmit` hook。

Durable file 中的恶意 prompt 可以绕过用户输入审计入口。

## 61. Cron Permission 会在后台线程询问

autorun 调用完整 `agent_loop`。

若模型选择 destructive bash 或 deploy MCP：

```python
permission_hook → input("Allow?")
```

发生在 cron daemon thread。

与此同时主线程可能正停在：

```text
s20 >>
```

两个线程可能争用 stdin。

自动任务中的 human approval 应进入持久 approval queue，而不是后台 thread 直接 input。

## 62. Cron 继承 S14 的可靠性边界

仍有：

- local time/DST；
- DOM/DOW OR；
- `*/999` 可通过；
- 随机 ID collision；
- 非原子 durable save；
- load error 静默；
- one-shot 入队时先删除；
- cancel 不清已入队；
- 无 ack/retry/misfire；
- 多进程重复触发。

S20 增加 autorun，没有补可靠队列。

## 63. Cron 与 Main Context 的引用分叉

cron thread 启动时拿到初始 context dict。

主 loop 后：

```python
context = update_context(...)
```

会重新绑定主变量。

cron thread 仍持旧 dict，并用：

```python
context.update(...)
```

`agent_loop` 每 cycle 又会创建本地 live context，所以实际 prompt 多数仍新鲜，但共享 context 模型不一致。

最好把 context 放到单一 store/owner。

## 64. Task Graph 仍是无锁 JSON

综合后依然：

- 秒级+随机 ID；
- save 非原子；
- claim TOCTOU；
- complete 不校验 owner；
- 坏 JSON 击穿 scan；
- path ID 未验证；
- 无 lease/recovery。

“加入更多机制”没有提升 Task Store 的并发语义。

## 65. S20 对 Worktree 的两处改善

第一，create 前验证 task 是否存在：

```text
missing task → Error
不会先创建 orphan
```

第二，idle auto-claim 接收：

```python
worktree_context=wt_ctx
```

成功时直接设置：

```python
wt_ctx["path"] = worktree path
```

修复了 S19 自动 claim 不更新 cwd 的回退，也避开 S18 dataclass `.get` bug。

## 66. Worktree 仍有旧缺陷

仍包括：

- bind 不验证存在/唯一；
- 两 task 可共用；
- missing path；
- Bash 不是 sandbox；
- complete 无条件清 cwd；
- `@{push}` commit 计数失败；
- default remove 可能删新 commit；
- branch delete结果忽略；
- remove 不清 task binding；
- keep 不验证存在；
- 无 merge pipeline。

S20 只是做了局部修补。

## 67. Teammate Auto-Claim 仍缺 Description

注入：

```text
Task ID: subject
Work directory: path
```

不含 description。

teammate 工具仍无 `get_task`。

所以 worktree cwd 正确后，模型仍可能不知道完整验收要求。

目录正确不等于任务理解正确。

## 68. MCP 在综合 Dispatcher 中

每 cycle：

```text
assemble builtin + connected MCP
```

所以 connect 后下一 cycle 自动可见，无需 S19 单独检测 connect block。

同一 response 中：

```text
connect + guessed MCP call
```

仍使用旧 handlers，第二项 Unknown。

MCP collision、schema、结果、真实 transport 等 S19 边界全部继承。

## 69. MCP Permission 只覆盖 Lead/Cron

teammate 没有 MCP 工具。

Lead MCP 经过 PreToolUse。

Cron 触发 Lead MCP 也经过，但可能后台 input。

Subagent 没有 MCP。

权限仍按 `"deploy" in name` 判断，而非 annotation。

## 70. 综合并不等于每个 Agent 都拥有全能力

能力表：

| 能力 | Lead | Subagent | Teammate |
|---|---:|---:|---:|
| 文件/Bash | ✓ | ✓ | ✓ |
| Permission/Hooks | ✓ | ✓ | ✗ |
| Todo | ✓ | ✗ | ✗ |
| Skill | ✓ | ✗ | ✗ |
| Task graph | ✓ | ✗ | ✓ |
| Background | ✓ | ✗ | ✗ |
| Cron | ✓ | ✗ | ✗ |
| Protocol | ✓ | ✗ | 部分 |
| Worktree cwd | Lead只管理 | ✗ | ✓ |
| MCP | ✓ | ✗ | ✗ |
| Retry/Compact | ✓ | ✗ | ✗ |

需要根据任务选择正确 delegation surface。

## 71. 运行前准备：使用 Disposable Lab

S20 可以：

- 执行 Shell；
- 写 workspace 外路径（经允许）；
- 创建线程；
- 创建任务/邮箱；
- 创建 Git branch/worktree；
- 保存 transcript；
- 持久化 Cron；
- 模拟 deploy MCP。

请不要直接在重要脏仓库做综合实验。

### Windows PowerShell

```powershell
$lab = Join-Path $env:TEMP ("s20-lab-" + [guid]::NewGuid())
New-Item -ItemType Directory -Path $lab | Out-Null
Set-Location $lab

git init
git config user.name "S20 Student"
git config user.email "s20@example.invalid"
git commit --allow-empty -m "initial"

$env:PYTHONUTF8 = "1"
$env:MODEL_ID = "<你的模型ID>"
$env:ANTHROPIC_API_KEY = "<你的Key>"

& D:\Projects\learn-claude-code\.venv\Scripts\python.exe `
  D:\Projects\learn-claude-code\s20_comprehensive\code.py
```

### macOS / Linux

```bash
lab="$(mktemp -d)"
cd "$lab"

git init
git config user.name "S20 Student"
git config user.email "s20@example.invalid"
git commit --allow-empty -m "initial"

export PYTHONUTF8=1
export MODEL_ID="<你的模型ID>"
export ANTHROPIC_API_KEY="<你的Key>"

/path/to/learn-claude-code/.venv/bin/python \
  /path/to/learn-claude-code/s20_comprehensive/code.py
```

## 72. 启动后的初始状态

期望：

```text
s20: comprehensive agent
Enter a question, press Enter to send. Type q to quit.

s20 >>
```

目录可能立即出现：

```text
.tasks/
.worktrees/
```

若有 durable file，会加载 jobs。

检查：

```text
skills/ 是否在启动前就存在
.memory/MEMORY.md 是否存在
.scheduled_tasks.json 是否来自旧实验
```

每个实验最好使用新 lab，避免旧状态影响结果。

## 73. 第一阶段：只验证基础循环

输入：

```text
Use glob to list Python files. Do not use bash and do not create or edit files.
Then summarize how many files were found.
```

期望路径：

```text
UserPromptSubmit hook
  → LLM
  → glob tool_use
  → PreToolUse permission(None)
  → log_hook
  → run_glob
  → PostToolUse
  → tool_result
  → LLM final text
  → Stop hook
```

终端应出现：

```text
[HOOK] UserPromptSubmit
> glob
[HOOK] glob
[HOOK] Stop
```

模型结果因 lab 文件不同而变化。

## 74. 第二阶段：Todo + File Tools

输入：

```text
Create a todo list with:
1. inspect the workspace (in_progress)
2. create hello.txt (pending)
3. verify hello.txt (pending)
Then execute the plan, updating todo status as you progress.
hello.txt must contain exactly HELLO-S20.
```

观察：

- 第一次 `todo_write` 置 counter=0；
- 文件工具经过 hook；
- 模型是否保持一个 in_progress；
- 最终 todo 是否全部 completed；
- `hello.txt` 是否精确。

验收：

```text
文件内容正确
CURRENT_TODOS 有三项
最终状态 completed
```

Todo 的具体更新次数由模型策略决定。

## 75. Todo Reminder 实验

输入：

```text
Do four small read/glob operations without calling todo_write.
```

每个同步非 todo 工具让 counter+1。

下一个 cycle 应注入：

```text
<reminder>Update your todos.</reminder>
```

它不一定在终端直接打印，但可用 fake client 捕获 messages。

多个 tool_use 在同一 response 中也分别计数。

## 76. 第三阶段：Skill + Memory

必须在启动 S20 前准备：

```text
skills/demo/SKILL.md
.memory/MEMORY.md
```

示例 Skill：

```markdown
---
name: demo
description: Create files with a DEMO header.
---

When creating a demo text file, begin with `DEMO:`.
```

Memory：

```text
For this lab, use the filename remembered.txt.
```

重启 S20 后输入：

```text
Use the relevant skill and memory to create the requested demo file.
```

理想：

```text
system catalog 看到 demo
load_skill("demo")
write remembered.txt
内容以 DEMO: 开头
```

若运行中才创建 skill，需要重启或手工 rescan。

## 77. 第四阶段：一次性 Subagent

输入：

```text
Use the task tool to inspect the lab and report which generated state
directories exist. The subagent must not edit anything.
```

观察：

- Lead 调 `task`；
- subagent 有独立 messages；
- subagent 可 glob/read；
- hook 日志显示其工具；
- Lead 只收到 final summary；
- subagent 中间上下文不进入主 history。

验收时自己检查 summary 是否与磁盘一致。

## 78. Subagent Permission 实验

只在 disposable lab，要求 subagent 尝试一个会被字符串规则拒绝、但不会执行的命令：

```text
Ask a subagent to run the harmless command that prints the literal word
"sudo" using bash. Do not run any privileged command.
```

由于 command 字符串含 `sudo`：

```text
Permission denied
```

证明 subagent 经过 permission，也证明 substring 会误报。

## 79. 第五阶段：Background

使用一个在当前环境确定安全的慢命令。

示例：

```text
Run the repository's harmless test command in the background, then
immediately read README.md and continue. Do not install dependencies.
```

期望：

```text
[background] bg_....
[Background task ... started]
Lead 继续其他工具
```

若后台在同一轮后半完成，可能立即注入 notification。

否则下一次输入：

```text
Check whether any background notification has arrived.
```

注意 notification 只有前 200 字符。

## 80. Background 故障注入

离线注册一个抛 `RuntimeError` 的 fake handler，调用：

```python
bg = start_background_task(block, handlers)
```

预期原代码：

```text
thread traceback
background_tasks[bg]["status"] == "running"
```

“永久 running”就是实验成功复现。

不要用真实长进程模拟；fake handler 更快、更确定。

## 81. 第六阶段：Cron

先只做 list/schedule/cancel，不等待真实分钟：

```text
Schedule a non-durable recurring cron for every minute with prompt
"say CRON-DEMO only". Then list the cron jobs.
```

期望表达式：

```text
* * * * *
```

一分钟边界时：

```text
[cron auto] say CRON-DEMO only
```

Agent 自动运行。

完成后立即 cancel，避免 lab 持续输出。

## 82. Cron 离线匹配实验

```python
from datetime import datetime

dt = datetime(2026, 7, 30, 12, 0)
print(c.cron_matches("* * * * *", dt))
print(c.cron_matches("0 12 * * *", dt))
print(c.validate_cron("*/0 * * * *"))
print(c.validate_cron("*/999 * * * *"))
```

预期：

```text
True
True
minute: Invalid step ...
None
```

`*/999` 被接受，但在分钟 0 才匹配。

## 83. 第七阶段：Task + Worktree + Teammate

输入：

```text
Create one task "isolated artifact" whose description requires file
artifact.txt containing ISOLATED-S20. Create worktree "isolated" bound to
the task. Spawn alice and ask her to pull from the task board, inspect pwd
and branch, create and verify the file, then complete the task.
Do not remove the worktree.
```

S20 应自动：

- Alice IDLE scan；
- claim；
- `wt_ctx` 切到 `.worktrees/isolated`；
- 相对 file tools落在 worktree；
- complete 后 cwd 清空。

验收：

```text
.worktrees/isolated/artifact.txt 存在
主目录/artifact.txt 不存在
Task completed/owner=alice/worktree=isolated
```

模型未拿到 description 是潜在变量；可在 spawn prompt 重复完整要求。

## 84. 第八阶段：Plan Approval

输入：

```text
Spawn bob and explicitly require him to submit a plan before any file or
bash tool. When his plan arrives, show it to me and wait for my decision.
```

观察：

```text
plan_approval_request
waiting_plan
模型暂停
```

批准：

```text
review_plan(request_id, approve=true)
```

拒绝实验：

```text
approve=false
```

然后观察 Bob 是否仍继续。

原代码只靠 `[Plan rejected]` 提醒，gate 已打开；模型可能继续。

## 85. 第九阶段：MCP

输入：

```text
Connect to docs, then on the next model turn call the docs version tool.
Do not connect deploy.
```

期望：

```text
connect_mcp docs
下一 cycle tool pool 29 个
mcp__docs__get_version
```

结果：

```text
[docs] API v2.1.0
```

说明 27 builtins + 2 MCP。

## 86. MCP Permission 实验

连接 deploy 后只查询 status。

原 hook 仍会询问：

```text
MCP destructive-looking tool: mcp__deploy__status
```

输入 n：

```text
Permission denied by user
```

然后思考：readOnly status 为什么被拦，而名为 release 的 trigger 为什么可能不拦。

## 87. 第十阶段：显式 Compact

只在没有其他 tool call 的独立模型 response 使用：

```text
Compact the conversation now. Make compact the only tool call in this
response, then continue by stating the current goal.
```

期望：

- transcript 保存；
- summary 生成；
- messages 缩成 summary；
- 下一轮继续；
- 没有其他副作用结果被丢。

检查 `.transcripts/`。

## 88. Compact 混批故障实验

使用 fake client 返回：

```text
write_file(id=w1)
compact(id=c1)
```

然后给 summary 和 final response。

原代码离线预期：

```text
文件存在
w1 result 不在 messages
compact 不在 PreToolUse log
Stop 统计 0
```

这不是推荐提示词，而是 invariant 测试。

## 89. 第十一阶段：Retry/Fallback

离线 fake `fn`：

```python
attempts = 0

def flaky():
    global attempts
    attempts += 1
    if attempts <= 2:
        raise RuntimeError("529 overloaded")
    return "ok"
```

设置 `FALLBACK_MODEL_ID`，把 retry delay 改为 0。

调用：

```python
state = RecoveryState()
with_retry(flaky, state)
```

预期：

```text
尝试3次
结果ok
state.current_model=fallback
```

## 90. Prompt Too Long 故障实验

fake client 第一次抛：

```text
context_length_exceeded
```

让 `summarize_history` 返回固定 summary，第二次正常。

验证：

- 写 reactive transcript；
- history 变 summary+tail；
- 同一 agent_loop 不会第二次 reactive compact；
- 正常结果最终追加。

## 91. 十五个跨机制观察实验

### 实验 1：Denied Tool 与 Log Hook

拒绝后观察 log_hook 未运行。

### 实验 2：Teammate Permission Bypass

注册 tracker hook，teammate write 后 tracker 为空。

### 实验 3：Cron User Hook Bypass

Cron prompt 注入不出现 UserPromptSubmit。

### 实验 4：Cron 后台审批

让 scheduled agent 选择 deploy，观察后台 input 竞争。

### 实验 5：Fast Background

handler 立即返回，notification 可能进入同一批 user content。

### 实验 6：Slow Background

Lead 已停止后结果等待下一触发。

### 实验 7：Background Post Hook 异常

status 永远 running。

### 实验 8：Todo + Background

后台工具不增加 todo counter。

### 实验 9：Persist + Micro Compact

旧 persisted marker 可能被替换成 generic compact 文本。

### 实验 10：两个 Compact 同秒

第一次 transcript 被第二次覆盖。

### 实验 11：Max Tokens 含 Tool Use

观察 continuation 缺 tool_result 的潜在 API 错误。

### 实验 12：Reject 后继续

fake teammate 在 reject 后写文件成功。

### 实验 13：Plan 同批后续 Tool

后续 tool_use 没 result。

### 实验 14：Auto Worktree + Complete 失败

错误 complete 也清 cwd，后续写主目录。

### 实验 15：MCP Connect 同批 Call

新工具在当前 handler snapshot 中仍 Unknown。

## 92. 修改实验：统一所有 Tool Dispatcher

现在有三套：

```text
Lead dispatcher
Subagent dispatcher
Teammate dispatcher
```

改成：

```python
def dispatch_tool(call, execution_context):
    tool = registry.resolve(call.name)
    validate_args(tool.schema, call.input)
    decision = authorize(tool, call.input, execution_context)
    audit_pre(call, decision, execution_context)
    if not decision.allowed:
        return denied_result(call, decision)
    if should_background(tool, call, execution_context):
        return enqueue_background(...)
    result = invoke(tool, call.input, execution_context)
    result = normalize_result(result)
    audit_post(call, result, execution_context)
    return result
```

Lead/subagent/teammate 只传不同 capability context。

修改后 teammate tracker 应看到 Pre/Post。

## 93. 修改实验：Capability Context

```python
@dataclass
class ExecutionContext:
    principal: str
    role: str
    task_id: str | None
    workspace: Path
    worktree: Path | None
    allowed_tools: set[str]
    approval_ids: set[str]
    registry_version: int
```

权限判断不再读全局 WORKDIR 和字符串名称。

示例：

```text
Lead:
  可管理 team/cron/mcp

Subagent:
  只读或特定文件写

Teammate:
  只允许当前 worktree
  只允许 owner task

Cron:
  无交互 input
  只允许预授权工具
```

## 94. 修改实验：Tool Batch 预检与 Pairing

收到 response 后先构建 execution plan：

```text
验证所有 tool_use ID 唯一
识别 barrier tool
验证 schema
决定哪些执行/拒绝/defer
为每个 block 预留 ToolResult
```

无论结果：

```text
executed
denied
deferred
cancelled_due_to_barrier
unknown
```

每个 ID 都有 result。

`compact`、`connect_mcp`、`submit_plan` 都应是 barrier。

重跑混批实验后：

```text
w1 有结果
c1 有控制结果或被单独执行
```

## 95. 修改实验：真正 Plan State Machine

```text
draft
  → submitted
  → approved / rejected
approved
  → executing
  → completed
rejected
  → revised
  → submitted
```

Gate 绑定：

```text
request_id
task_id
plan_hash
agent
allowed tool scope
approved_by
expires_at
```

工具层检查：

```text
requires_plan && state != approved
  → denied
```

Reject 后只允许：

- read；
- submit revised plan；
- cancel；
- message。

不能继续 write/bash。

## 96. 修改实验：非交互 Approval Queue

Cron/background thread 不直接 `input()`。

产生：

```json
{
  "approval_id": "...",
  "principal": "cron:...",
  "tool": "...",
  "args_summary": "...",
  "reason": "...",
  "expires_at": ...
}
```

状态：

```text
pending
approved
rejected
expired
cancelled
```

主 UI 展示并由用户处理。

批准后通过事件恢复原 operation。

## 97. 修改实验：可靠 Background State

```text
queued
running
completed
failed
cancelled
timed_out
```

worker：

```python
try:
    result = invoke(...)
except Exception as exc:
    mark_failed(exc)
finally:
    signal_event()
```

完整结果：

- 持久化；
- notification 只给摘要+result_ref；
- 有 get result 工具；
- 有 TTL/cleanup；
- 支持 cancel；
- 进程重启后 reconcile。

异常实验应变：

```text
status=failed
error=RuntimeError
Lead 被唤醒
```

## 98. 修改实验：统一 Event Loop

事件类型：

```text
user_prompt
cron_fired
background_completed
background_failed
teammate_message
approval_decided
mcp_notification
shutdown
```

单一 coordinator：

```text
event queue
  → acquire conversation ownership
  → append normalized event
  → run agent
  → persist checkpoint
```

所有 history 修改由 coordinator 完成。

这消除：

- main/cron list race；
- background 不唤醒；
- teammate 要等用户；
- 双线程 input。

## 99. 修改实验：可靠 Cron Delivery

```text
scheduled job
  → fire occurrence
  → queued event
  → claimed
  → executing
  → acknowledged
```

one-shot 只在 ack 后完成。

持久字段：

```text
occurrence_id
scheduled_for
enqueued_at
attempt
status
last_error
```

missed fire 按 misfire policy：

- skip；
- run once；
- catch up all；
- deadline 过期。

## 100. 修改实验：Compaction 按 Turn/Pair 操作

建立结构：

```text
Turn
  assistant response
  complete set of tool results
```

只在完整 turn 边界 compact。

摘要输入：

- 当前目标；
- constraints；
- changed files；
- task/protocol state；
- important evidence；
- latest turns；
- unresolved approvals；
- background refs。

Transcript：

- UUID；
- UTF-8；
- secret redaction；
- immutable；
- retention；
- hash。

不要只取序列化前 80k。

## 101. 修改实验：恢复策略保留 Cause 与 Tool State

结构化：

```python
RecoveryDecision(
    error_class,
    retryable,
    retry_after,
    model_switch,
    compact,
    call_outcome,
)
```

max_tokens：

- text partial 可 continuation；
- tool_use partial 不进入历史，或补全/取消配对；
- 不执行不完整 tool call；
- recovery counter 在稳定成功后重置。

retry 最终：

```text
raise ... from original
```

保留 cause。

## 102. 修改实验：Store 一致性

把 Task、Protocol、Worktree、Cron、Background 从零散 dict/JSON 提升为 repository：

```text
transaction
version
lock
atomic write
schema
migration
event log
reconciliation
```

关键跨对象事务：

- claim Task + enter worktree lease；
- approve plan + capability grant；
- complete Task + result commit；
- cron fire + event enqueue；
- background finish + notification enqueue。

## 103. 修改实验：统一 Observability

每个 operation：

```text
trace_id
session_id
turn_id
event_id
agent/principal
task_id
tool_use_id
tool
cwd/worktree
permission decision
approval
start/end/duration
result/error
retry
model
context size
```

关键告警：

- orphan tool_use；
- active teammate 无 heartbeat；
- background running 超时；
- stale worktree binding；
- Task in_progress owner dead；
- Cron queued 未 ack；
- permission bypass surface；
- transcript overwrite；
- MCP collision。

## 104. 测试矩阵：主循环

| 场景 | 预期 |
|---|---|
| 无 tool_use | Stop hook 后返回 |
| text+tool_use | 执行工具并继续 |
| unknown tool | 对应 error result |
| denied tool | 对应 denied result |
| denied tool | 原代码 log_hook 不运行 |
| 三个同步工具 | 下一 cycle todo reminder |
| todo_write | counter reset |
| background tool | 立即 placeholder |
| background fast finish | 同批或下 cycle notification |
| background RuntimeError | 原代码永久 running |
| compact only | transcript+summary |
| write+compact | 原代码副作用有、result无 |
| API 429×2 | 第三次调用 |
| API 529×2 | 切 fallback |
| prompt too long | reactive compact 一次 |
| max tokens once | 16000重试 |
| max tokens含tool | 验证 pairing |
| connect MCP | 下一 cycle 工具增加 |

## 105. 测试矩阵：异步与团队

| 场景 | 预期 |
|---|---|
| Cron fire | 自动 agent turn |
| Cron destructive | 不应后台 input（原代码会） |
| Cron one-shot failure | 可靠版可重试 |
| Main+Cron history | 无并发修改 |
| Teammate write | 应经 permission（原代码不经） |
| Teammate auto claim | wt_ctx 正确 |
| Teammate description | 原代码缺失 |
| submit plan only | waiting gate |
| submit+write同批 | 原代码 write缺result |
| approve matching | gate打开 |
| reject matching | 原代码也打开 |
| forged approval | 应拒绝 sender |
| teammate tool exception | 应清active/报failure |
| background完成 | 自动唤醒 |
| Lead inbox result | 自动协调而非等用户 |

## 106. 本课综合挑战：生产化 Mini Harness

### 必做要求

1. 单一 dispatcher 覆盖 Lead/subagent/teammate；
2. 每个 tool_use 都有 result；
3. barrier tool batch preflight；
4. structured permission；
5. teammate 不可绕过；
6. plan reject 保持写 gate；
7. background failure/timeout；
8. event-driven wakeup；
9. cron durable delivery/ack；
10. history single owner；
11. compaction 只在完整 pair 边界；
12. transcript 唯一且脱敏；
13. Task claim 原子；
14. Worktree lease/health；
15. MCP collision/schema/policy；
16. 至少覆盖两个测试矩阵 24 项。

### 进阶要求

1. 崩溃恢复；
2. 多进程 worker；
3. per-user auth；
4. plan capability token；
5. non-idempotent outcome 查询；
6. event replay；
7. policy hot reload；
8. model fallback 恢复 primary；
9. metrics/tracing；
10. fault injection suite。

### 端到端场景

```text
用户目标
  → Todo
  → Task A/B 依赖
  → Worktree A/B
  → Alice/Bob submit plan
  → 一批 approve、一批 reject+revise
  → 自动 claim
  → Background tests
  → 一次 worker crash
  → 恢复
  → Docs MCP 查询
  → Review/Merge gate
  → Cron 安排后续验证
  → 最终 task/branch/event 对账
```

验收：

- 无 orphan tool block；
- 无权限绕过；
- 无永久 running；
- 无丢失 event；
- reject 不执行写；
- task/worktree owner 一致；
- background 完整结果可取；
- transcript 可恢复；
- 最终 summary 与持久状态一致。

## 107. 常见问题与定位

### 为什么 27 Tools 只有 26 Handlers

`compact` 是 dispatcher control tool。

### Hook 明明返回 Block，用户 Prompt 仍执行

UserPromptSubmit 的返回值在 main 中被忽略。

### Denied Tool 没有 `[HOOK] tool`

permission_hook 先返回，trigger_hooks 短路，log_hook 未运行。

### Teammate 没弹权限确认

teammate 不调用 hook pipeline。

### Outside Path 回答 Yes 后真的写出去了

S20 文件 handler 不再有 safe_path boundary；permission 是唯一 Lead gate。

### Deploy Status 也要确认

MCP policy 按名称包含 deploy，不看 readOnly。

### Background 永远 Running

worker 或 Post hook 抛异常，未进入完成状态。

### Background 完整输出找不到

collect 只保留前 200 字符，然后删除内存 full result。

### Background 完成但 Agent 不知道

没有 completion wake event；再触发一轮或修复 event loop。

### Cron 到点没自动运行

确认程序是 `__main__` CLI，而不只是 import；autorun thread 只在 main 启动。

### Cron 抢了我的输入

后台 agent 的 permission hook 正在调用 input。

### Skill 新增后看不到

只在 import 时 scan；重启或 rescan。

### Memory 后半段没生效

只读前 2000 字符。

### Compact 后刚才写文件的结果不见

write 和 compact 同一 response，局部 results 被丢。

### Plan 后 API 报 Tool Result Missing

submit_plan 后的同批 tool_use 被忽略但仍留在 assistant content。

### Reject 后 Bob 仍继续

matching response 无论 approve 值都会清 waiting_plan。

### Worktree Commit 被 Remove 掉

`@{push}` 无 upstream，commit 被误算 0。

### Auto-claimed Teammate 不理解任务

消息没有 description，且 teammate 没 get_task。

### MCP Connect 同批 Search Unknown

handler snapshot 在 cycle 开头组装；下一 cycle 才看到新 tool。

### Stop Hook 没运行

异常/恢复耗尽不是正常 no-tool stop，代码无 finally。

### Transcript 被覆盖

同秒文件名相同。

## 108. 设计层面的延伸思考

### 执行表面必须共享安全内核

Lead 安全、teammate 不安全，等于系统不安全。

### 控制流 Tool 需要 Barrier 语义

compact、connect、submit_plan 会改变后续执行环境。

### 每个副作用都需要可关联结果

结果丢失会让模型重试并重复副作用。

### 异步系统需要 Event Ownership

多个 thread 直接改 history 是最难调试的竞争来源。

### Human Approval 是持久协议

后台 thread `input()` 不是可恢复审批。

### Context 是事实的视图，不是事实本身

Task、branch、approval 应保存在结构化 store，摘要只是视图。

### Compact 也属于安全边界

它决定哪些约束、证据和 approval 被保留。

### Background Placeholder 不是完成

Task state 不能因“已启动”变成 completed。

### Retry 必须理解幂等性

模型/API retry 与外部工具 retry 是不同层。

### “综合”要防功能回退

每加入一章机制，都应跑此前能力的回归测试。

### 可观察性不能靠自然语言

trace、event、state 和 summary 要能交叉验证。

### Harness 的复杂性来自协调

不是 while loop 变神秘，而是权限、并发、持久化和恢复必须一致。

## 109. 结课自测

1. 一次 Lead cycle 的注入顺序是什么？
2. 五个核心 invariant 是什么？
3. 六种执行表面有哪些？
4. 为什么 27 tools 只有 26 handlers？
5. import 会产生哪些副作用？
6. WORKDIR 从哪里取？
7. UserPromptSubmit return 为什么无效？
8. agent_lock 漏了哪些 history 写入？
9. system prompt 为什么每轮变化？
10. context 的哪些字段实际未进入 prompt？
11. Skill 为什么运行中不刷新？
12. Todo 和 Task graph 怎样分工？
13. todo reminder 的计数单位是什么？
14. subagent 有哪些工具？
15. subagent 是否经过 permission？
16. teammate 为什么能绕过 permission？
17. S20 path handler 与 S18 有何区别？
18. Bash deny substring 有哪些误判？
19. MCP deploy permission 有哪些误判？
20. hook 短路为什么漏 audit？
21. compact 为什么绕过 hooks？
22. write+compact 如何破坏 pairing？
23. submit_plan+write 如何破坏 pairing？
24. reject 为什么会打开 gate？
25. plan gate 还缺哪些绑定？
26. compaction 四层是什么？
27. result budget 为什么不是全局？
28. persisted output 有哪些风险？
29. snip 怎样保护边界 pair？
30. micro compact 保留几项？
31. summary 为什么可能漏最近目标？
32. reactive compact 最多几次？
33. 529 何时切 fallback？
34. fallback 何时切回？
35. max_tokens 含 tool_use 有什么风险？
36. background 如何判慢？
37. background RuntimeError 后状态是什么？
38. full background result 为什么丢失？
39. background 怎样唤醒模型？
40. Cron 有哪两个线程？
41. Cron prompt 是否经过 UserPromptSubmit？
42. Cron approval 为什么争 stdin？
43. S20 修复了哪两个 worktree 问题？
44. worktree 还继承哪些删除风险？
45. teammate 为什么仍缺任务要求？
46. connect 后 MCP 工具何时可见？
47. 如何统一 dispatcher？
48. barrier batch 怎样保证每个 ID 有 result？
49. reliable event loop 如何组织？
50. 最终如何证明 summary 可信？

能用代码路径和离线实验回答至少 43 题，并完成基础循环、Todo、subagent、background、Cron、team/worktree、MCP、compact 和 recovery 九阶段验收，就完成了 S20。

## 110. 完成二十课后的能力地图

你已经从最小循环走到完整 Harness：

```text
S01  Agent loop
S02  Tool dispatch
S03  Permission
S04  Hooks
S05  Todo
S06  Subagent
S07  Skills
S08  Compaction
S09  Memory
S10  System prompt
S11  Recovery
S12  Task graph
S13  Background
S14  Cron
S15  Teams
S16  Protocols
S17  Autonomy
S18  Worktrees
S19  MCP
S20  Composition
```

最终应该形成的工程判断是：

> 模型能力决定它能提出多好的下一步；Harness 质量决定这些步骤是否有权限、可执行、可恢复、可审计，并且不会在并发和故障中失去真实状态。

课程结束不代表代码已经生产就绪。恰恰相反，你现在已经有足够的结构视角，能够看出从教学原型走向可靠 Agent Runtime 还需要补哪些系统工程。
