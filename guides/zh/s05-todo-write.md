# S05 实操教学指南：让复杂任务拥有可见计划

> 对应课程：[s05_todo_write](../../s05_todo_write/)  
> 核心代码：[code.py](../../s05_todo_write/code.py)  
> 练习源码：[example/hello.py](../../s05_todo_write/example/hello.py)  
> 前置课程：[S04 Hooks](s04-hooks.md)  
> 建议用时：90–120 分钟  
> 本课产物：一个可创建、展示和更新内存 TODO 列表的 Agent

## 1. 学完这一课，你应该能做到什么

完成 S05 后，你应该能够：

1. 解释计划工具为什么不增加执行能力，却能提高复杂任务完成率；
2. 看懂 `CURRENT_TODOS`、`run_todo_write()` 和工具 schema；
3. 区分 `pending`、`in_progress`、`completed` 三种状态；
4. 理解每次 `todo_write` 都是完整替换，而不是局部 patch；
5. 预测提醒计数器什么时候增加、重置和注入 reminder；
6. 区分一个模型工具轮、一个 tool call 和一个用户任务；
7. 验证 JSON 字符串、Python 字面量字符串和非法输入的规范化结果；
8. 识别“显示已完成”和“实际已验证完成”的差别；
9. 为 TODO 增加更严格的不变量、持久化或事件 Hook；
10. 使用 TODO 驱动一个真实的多步骤代码重构并逐项验收。

本课最重要的一句话是：

> TodoWrite 不替 Agent 做事，它让 Agent 的意图、进度和遗漏变得可见。

## 2. 为什么复杂任务容易偏离

简单任务可能只需一次工具调用：

```text
读取文件 → 回答
```

复杂任务通常包含多个目标：

```text
理解现状
→ 设计修改
→ 编辑多个文件
→ 运行测试
→ 修复失败
→ 再次验证
→ 汇总结果
```

工具结果不断进入上下文后，模型很容易把最近的局部错误当成全部任务。显式 TODO 列表提供：

- 当前完整目标；
- 正在进行的步骤；
- 尚未开始的步骤；
- 已完成步骤；
- 可供用户观察和纠正的计划。

它不是传统工作流引擎。Harness 没有根据 TODO 自动调用工具，仍然由模型决定下一步。

## 3. 从 S04 到 S05 的实际变化

S05 保留工具分发和最小 Hook 注册表，并新增：

```text
todo_write schema
  + run_todo_write()
  + CURRENT_TODOS
  + SYSTEM 中的规划指导
  + rounds_since_todo
  + 三轮未更新时的 reminder
```

工具数量从 5 增加到 6：

```python
TOOL_HANDLERS = {
    ...
    "todo_write": run_todo_write,
}
```

Agent Loop 不需要按名称写一个新的执行分支。`todo_write` 仍通过 S02 建立的 dispatch map。

S05 说“保留 S04 Hook 结构”，但不是完整复制所有行为：

- 仍有四类 Hook 注册槽；
- 注册了输入日志、PreToolUse deny list、工具日志和 Stop 汇总；
- 没有注册 S04 的大输出 PostToolUse Hook；
- 权限 Hook 只保留 Bash 硬拒绝，不再询问删除或工作区外访问；
- 文件工具重新使用 `safe_path()`，工作区外路径直接报错。

逐课学习时要区分“架构模式保留”和“所有上一课细节完全不变”。

## 4. TODO 的数据模型

一个 TODO 是：

```json
{
  "content": "Run the test suite",
  "status": "pending"
}
```

当前状态枚举只有：

| 状态 | 终端图标 | 含义 |
|---|---|---|
| `pending` | `[ ]` | 尚未开始 |
| `in_progress` | `[▸]` | 当前正在做 |
| `completed` | `[✓]` | 模型声明已经完成 |

完整工具输入是一个数组：

```json
{
  "todos": [
    {"content": "Inspect the file", "status": "completed"},
    {"content": "Refactor greet", "status": "in_progress"},
    {"content": "Run the program", "status": "pending"}
  ]
}
```

当前数据模型没有：

- ID；
- 创建或更新时间；
- 负责人；
- 依赖关系；
- 失败或取消状态；
- 完成证据；
- 优先级；
- 跨进程持久化。

这些限制会在后续 Task System 中逐步解决。

## 5. `todo_write` 是整表替换

核心赋值是：

```python
CURRENT_TODOS = todos
```

因此每次调用必须提交你希望保留的完整列表。

第一次：

```text
A pending
B pending
C pending
```

更新 A 时应发送：

```text
A completed
B in_progress
C pending
```

如果只发送：

```text
B in_progress
```

`CURRENT_TODOS` 就只剩 B，A 和 C 会消失。当前工具没有“只修改某一项”的增量语义。

## 6. 输入规范化与安全解析

正常工具调用应传 Python 列表，但 `_normalize_todos()` 还兼容字符串。

### 6.1 已经是列表

直接验证每一项。

### 6.2 JSON 数组字符串

先尝试：

```python
json.loads(todos)
```

例如：

```text
[{"content": "Inspect", "status": "pending"}]
```

### 6.3 Python 字面量字符串

JSON 解析失败后尝试：

```python
ast.literal_eval(todos)
```

例如：

```text
[{'content': 'Inspect', 'status': 'pending'}]
```

`ast.literal_eval()` 只解析字面量结构，不执行函数调用。下面的字符串不会执行：

```text
__import__('pathlib').Path(...).write_text('bad')
```

它会返回 todo 格式错误。

### 6.4 当前验证范围

代码验证：

- 顶层必须是列表；
- 每一项必须是字典；
- 每项必须含 `content` 和 `status`；
- status 必须是三个枚举值之一。

代码没有验证：

- `content` 是否真的是非空字符串；
- TODO 内容是否重复；
- 是否最多只有一个 `in_progress`；
- 是否至少存在一个任务；
- completed 是否拥有验证证据。

工具 schema 会提示模型正确类型，但 Harness 侧的规范化仍有这些空缺。

## 7. Reminder 计数器的精确语义

每次用户任务进入 `agent_loop()` 时：

```python
rounds_since_todo = 0
```

它不是整个进程共享的全局计数。

每当模型返回 `stop_reason == "tool_use"`：

```python
rounds_since_todo += 1
```

无论该响应中有 1 个还是 5 个 tool call，都只增加 1。这里的“轮”是一次包含工具请求的
模型响应。

如果同一响应中包含 `todo_write`：

```python
rounds_since_todo = 0
```

下一次调用模型前，先检查：

```python
if rounds_since_todo >= 3 and messages:
    messages.append({
        "role": "user",
        "content": "<reminder>Update your todos.</reminder>",
    })
    rounds_since_todo = 0
```

时间线：

```text
第 1 个无 todo 工具轮结束 → counter=1
第 2 个无 todo 工具轮结束 → counter=2
第 3 个无 todo 工具轮结束 → counter=3
下一次调用模型前 → 注入 reminder，counter=0
```

如果模型在第三个工具轮后直接给最终文字，下一次循环顶部不会到来，提醒也不会出现。

还有一个当前实现细节：只要 tool block 名称是 `todo_write` 就重置计数，即使 handler 返回
`Error:`。更严格的实现应只在 TODO 成功更新时重置。

## 8. 准备隔离实验目录

### 8.1 Windows PowerShell

在仓库根目录运行：

```powershell
$courseRoot = (Resolve-Path .).Path
$s05Lab = Join-Path $env:TEMP "learn-claude-code-s05"
New-Item -ItemType Directory -Force -Path $s05Lab | Out-Null
Copy-Item "$courseRoot\s05_todo_write\example\hello.py" "$s05Lab\hello.py" -Force
Set-Location -LiteralPath $s05Lab
$env:PYTHONUTF8 = "1"
& "$courseRoot\.venv\Scripts\python.exe" "$courseRoot\s05_todo_write\code.py"
```

### 8.2 macOS / Linux

在仓库根目录运行：

```bash
course_root="$(pwd)"
s05_lab="$(mktemp -d)"
cp "$course_root/s05_todo_write/example/hello.py" "$s05_lab/hello.py"
cd "$s05_lab"
"$course_root/.venv/bin/python" "$course_root/s05_todo_write/code.py"
```

启动后应看到：

```text
s05: TodoWrite — plan before execute, nag if you forget
Type a question, press Enter. Type q to quit.

s05 >>
```

## 9. 第一次阅读代码：按七个区域理解

### 区域 A：全局内存状态

```python
CURRENT_TODOS: list[dict] = []
```

它在当前 Python 进程中共享：

- 同一 REPL 的后续用户任务仍能访问该变量；
- 退出程序后清空；
- 没有写入磁盘；
- 没有并发锁；
- `run_todo_write()` 直接保存传入列表引用，没有深拷贝。

### 区域 B：规划系统提示词

```python
SYSTEM = (
    ...
    "Before starting any multi-step task, use todo_write to plan your steps. "
    "Update status as you go."
)
```

这是行为引导，不是强制约束。模型仍可能：

- 直接开始执行；
- 只创建一次 TODO 后不更新；
- 把简单任务也过度规划；
- 未验证就标 completed。

Reminder 是第二层提示，但仍不保证模型遵守。

### 区域 C：规范化

`_normalize_todos()` 返回二元组：

```text
(规范化后的列表, None)
或
(None, 错误字符串)
```

这让 `run_todo_write()` 可以统一处理列表和字符串输入。

### 区域 D：状态展示

```python
icon = {
    "pending": " ",
    "in_progress": "▸",
    "completed": "✓",
}[t["status"]]
```

Windows 需要 UTF-8 控制台，否则 `▸` 和 `✓` 可能触发 GBK 编码错误。指南启动命令显式设置
了 `PYTHONUTF8=1`。

### 区域 E：工具 schema

schema 定义数组、对象、必填字段和 status 枚举。模型通过 schema 了解如何调用。

当前 items 没有 `additionalProperties: False`，也没有 `minItems` 或字符串长度限制。

### 区域 F：Hook 行为

`todo_write` 也会经过 PreToolUse：

```text
permission_hook → log_hook → run_todo_write
```

当前 permission Hook 只检查 Bash deny list，所以 TodoWrite 正常直接通过。

Stop 汇总把 TodoWrite 也计为一次 tool call，因为它确实是模型发出的工具请求。

### 区域 G：Reminder 插入位置

Reminder 在调用模型前追加为普通用户消息：

```python
{"role": "user", "content": "<reminder>Update your todos.</reminder>"}
```

它不是系统提示、Hook 消息或 tool result。它会留在 `history`，后续模型轮次都能看到。

## 10. 最小成功路径

输入：

```text
Refactor hello.py in three explicit steps:
1. add type hints,
2. add a concise docstring,
3. add a main guard and run the file.
Use todo_write before editing, keep exactly one task in_progress, update the
full list after each step, and verify before marking everything completed.
```

典型第一次 TODO：

```text
## Current Tasks
  [▸] Inspect hello.py and plan the exact changes
  [ ] Add type hints and a docstring
  [ ] Add a main guard and run the file
```

后续可能更新为：

```text
## Current Tasks
  [✓] Inspect hello.py and plan the exact changes
  [▸] Add type hints and a docstring
  [ ] Add a main guard and run the file
```

最终：

```text
## Current Tasks
  [✓] Inspect hello.py and plan the exact changes
  [✓] Add type hints and a docstring
  [✓] Add a main guard and run the file
```

代码的可接受结果之一：

```python
def greet(name: str) -> None:
    """Print a greeting for the supplied name."""
    message = "Hello, " + name
    print(message)


if __name__ == "__main__":
    greet("Claude")
```

具体 docstring 可不同。验收标准：

- 第一次实际修改前调用了 `todo_write`；
- TODO 覆盖三个目标，没有漏掉运行验证；
- 每次更新提交完整列表；
- 执行过程中能看到 in_progress 向后移动；
- `hello.py` 有参数和返回类型；
- 函数有 docstring；
- 顶层调用位于 main guard；
- `python hello.py` 输出 `Hello, Claude`；
- 运行成功后才把验证项标 completed。

## 11. 八个观察实验

### 实验 1：简单任务不一定需要计划

输入：

```text
Use read_file to tell me the first line of hello.py. This is a one-step task.
```

模型可能直接读取，不调用 TodoWrite。这不一定是失败。系统提示词要求的是 multi-step task。

结论：规划工具应该服务复杂度，而不是让所有操作都增加流程成本。

### 实验 2：TodoWrite 不执行任务

输入：

```text
Use todo_write to create one pending task: create planned.txt.
Stop immediately after updating the todo list; do not create the file.
```

预期：

- 终端显示一条 pending；
- 工具结果为 `Updated 1 tasks`；
- `planned.txt` 不存在。

结论：TodoWrite 只改变 Harness 的计划状态，没有写文件能力。

### 实验 3：整表替换会删除遗漏项

依次要求：

```text
Use todo_write to set three pending tasks named A, B, and C. Do nothing else.
```

```text
Now call todo_write with only task B marked in_progress. Do not include A or C.
```

第二次终端应只显示 B。

这不是 UI 隐藏，而是 `CURRENT_TODOS` 已被新列表完整替换。

### 实验 4：一个工具响应只增加一次计数

在实验副本中加入计数日志：

```python
print(f"[todo trace] before LLM: {rounds_since_todo=}")
```

让模型在一个响应中同时读取三个已知文件，但不调用 TodoWrite。

如果确实一次返回三个 `read_file`，该响应处理结束后 counter 只从 0 变 1，而不是变 3。

### 实验 5：观察三轮提醒

在实验副本中暂时把 SYSTEM 改为：

```python
SYSTEM = f"You are a coding agent at {WORKDIR}. Follow the user's requested tool sequence."
```

输入：

```text
Do not call todo_write initially. Use separate tool rounds:
first glob *.py and wait for its result;
then read hello.py and wait;
then run `python hello.py` and wait;
then continue the task.
```

配合 counter 日志观察。理想时间线：

```text
tool round 1 → 1
tool round 2 → 2
tool round 3 → 3
下一次模型请求前插入 <reminder>Update your todos.</reminder>
```

模型行为有概率性。如果它把工具合并到同一响应，counter 增长会更少；如果提前结束，也不会
进入提醒分支。

### 实验 6：TodoWrite 更新重置计数

在 counter 为 2 时，让模型调用 `todo_write`。处理该工具块后 counter 应回到 0，接下来
需要重新累计三个无 TodoWrite 工具轮才会提醒。

同一响应中即使 TodoWrite 前还有别的工具，最终仍会重置为 0。

### 实验 7：计划可以谎报完成

输入：

```text
Use todo_write to create one task called `run tests`, immediately mark it
completed, but do not run any command.
```

当前 Harness 会显示绿色完成，因为它不验证证据。

结论：

- 状态是模型声明；
- UI 的绿色勾不等于真实世界验证；
- 高可靠系统需要把 completed 与工具证据或验收条件关联。

### 实验 8：重启后计划消失

创建 TODO 后输入 `q`，重新启动 S05，再要求：

```text
Tell me the exact current todo list without creating a new one.
```

`CURRENT_TODOS` 已恢复为空。模型可能根据你的自然语言猜测，但没有持久化的 Todo 数据。

## 12. 离线验证 `_normalize_todos()`

这组实验不调用模型 API。先回到仓库根目录，确保 `.env` 已配置，然后运行课程模块的纯函数。

Windows PowerShell：

```powershell
Push-Location -LiteralPath $courseRoot
try {
    $env:PYTHONUTF8 = "1"
    & .\.venv\Scripts\python.exe -c "from s05_todo_write.code import run_todo_write; print(run_todo_write([{'content':'inspect','status':'pending'}]))"
    & .\.venv\Scripts\python.exe -c "from s05_todo_write.code import run_todo_write; print(run_todo_write([{'content':'bad','status':'unknown'}]))"
} finally {
    Pop-Location
}
```

macOS / Linux：

```bash
cd "$course_root"
.venv/bin/python -c "from s05_todo_write.code import run_todo_write; print(run_todo_write([{'content':'inspect','status':'pending'}]))"
.venv/bin/python -c "from s05_todo_write.code import run_todo_write; print(run_todo_write([{'content':'bad','status':'unknown'}]))"
```

预期：

```text
Updated 1 tasks
Error: todos[0] has invalid status 'unknown'
```

仓库测试还覆盖：

- JSON 数组字符串可以解析；
- Python 列表字面量字符串可以解析；
- 带函数调用的恶意字符串不会被执行。

## 13. 修改实验：加强 TODO 不变量

先复制：

Windows：

```powershell
Copy-Item "$courseRoot\s05_todo_write\code.py" "$courseRoot\s05_todo_write\code_experiment.py"
```

macOS / Linux：

```bash
cp "$course_root/s05_todo_write/code.py" "$course_root/s05_todo_write/code_experiment.py"
```

### 改动 A：要求非空且唯一的内容

在 `_normalize_todos()` 中增加：

```python
seen_contents = set()
for i, todo in enumerate(todos):
    content = todo.get("content")
    if not isinstance(content, str) or not content.strip():
        return None, f"Error: todos[{i}].content must be a non-empty string"
    normalized = content.strip()
    if normalized in seen_contents:
        return None, f"Error: duplicate todo content '{normalized}'"
    seen_contents.add(normalized)
```

验收：

- 空字符串被拒绝；
- 只有空格被拒绝；
- 完全重复项被拒绝；
- 合法列表正常显示。

### 改动 B：最多一个 `in_progress`

加入：

```python
in_progress_count = sum(
    1 for todo in todos
    if todo["status"] == "in_progress"
)
if in_progress_count > 1:
    return None, "Error: at most one todo may be in_progress"
```

预期：

- 两个正在进行的任务不再被接受；
- 计划 UI 更明确地表达当前焦点；
- 空列表或全 pending/completed 仍可接受。

这是一种产品策略，不是所有任务系统都必须遵守。并行 Agent 场景可能允许多个进行中任务。

### 改动 C：只在成功更新后重置 reminder

当前代码：

```python
if block.name == "todo_write":
    rounds_since_todo = 0
```

改为：

```python
if (
    block.name == "todo_write"
    and isinstance(output, str)
    and output.startswith("Updated ")
):
    rounds_since_todo = 0
```

让模型或离线探针提交非法 status。

预期：

- handler 返回错误；
- counter 不重置；
- 无效更新不能假装满足“计划已更新”。

### 改动 D：只在存在未完成计划时提醒

当前代码即使从未创建 TODO，也会在三个工具轮后提醒。可以改成：

```python
has_open_todos = any(
    todo["status"] != "completed"
    for todo in CURRENT_TODOS
)
if rounds_since_todo >= 3 and has_open_todos and messages:
    ...
```

权衡：

- 减少简单任务中的无意义提醒；
- 如果模型从未创建 TODO，就不会被 reminder 推动去创建；
- 可进一步区分“该任务是否足够复杂”。

### 改动 E：保留状态迁移规则

建立旧状态映射：

```python
previous = {
    todo["content"]: todo["status"]
    for todo in CURRENT_TODOS
}
```

拒绝 completed 直接回到 pending：

```python
for todo in todos:
    old_status = previous.get(todo["content"])
    if old_status == "completed" and todo["status"] == "pending":
        return None, (
            f"Error: completed todo cannot return to pending: "
            f"{todo['content']}"
        )
```

如果确实需要重新打开，应该增加明确的 `reopened` 事件或理由，而不是静默倒退。

## 14. 扩展实验：为完成状态增加证据

给 TODO 增加可选字段：

```json
{
  "content": "Run hello.py",
  "status": "completed",
  "evidence": "python hello.py -> Hello, Claude"
}
```

在 schema 中加入：

```python
"evidence": {"type": "string"}
```

在规范化中要求：

```python
if (
    todo["status"] == "completed"
    and not str(todo.get("evidence", "")).strip()
):
    return None, (
        f"Error: completed todo requires evidence: "
        f"{todo['content']}"
    )
```

显示时：

```python
if t["status"] == "completed":
    lines.append(f"      evidence: {t['evidence']}")
```

预期：

- 模型不能只发送绿色勾；
- 完成项需要说明用什么命令或观察证明；
- 证据仍是模型提供的文本，不是密码学证明，但可供用户审阅。

进一步可以让 Harness 自动把最近的工具调用 ID 绑定到 TODO，而不是让模型自由填写。

## 15. 扩展实验：生成 TODO 生命周期事件

TodoWrite 现在只保存最终快照。可以比较旧列表与新列表，生成事件：

```python
def todo_status_map(todos):
    return {
        todo["content"]: todo["status"]
        for todo in todos
    }
```

更新前：

```python
before = todo_status_map(CURRENT_TODOS)
after = todo_status_map(todos)
```

打印：

```text
TodoCreated
TodoStarted
TodoCompleted
TodoRemoved
```

例如：

```python
for content, status in after.items():
    old = before.get(content)
    if old is None:
        print(f"[TODO EVENT] created: {content}")
    elif old != status:
        print(f"[TODO EVENT] {old} -> {status}: {content}")
```

这些事件可用于：

- UI 更新；
- 审计；
- 进度指标；
- Hook；
- 后续多 Agent 通知。

## 16. 扩展实验：最小磁盘持久化

这是对 S12 的提前预览。每次成功更新后写入：

```python
TODO_FILE = WORKDIR / ".todos.json"


def save_todos(todos):
    TODO_FILE.write_text(
        json.dumps(todos, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
```

程序启动时加载：

```python
if TODO_FILE.exists():
    loaded = json.loads(TODO_FILE.read_text(encoding="utf-8"))
    normalized, error = _normalize_todos(loaded)
    if error is None:
        CURRENT_TODOS = normalized
```

验收：

- 创建 TODO 后退出；
- 重新启动后变量从磁盘恢复；
- 损坏 JSON 时程序给出清晰错误而不是静默清空；
- `.todos.json` 位于临时练习目录，不包含敏感信息。

当前实现没有锁。两个进程同时写可能互相覆盖，这正是持久化任务系统比内存 TODO 更复杂的
原因之一。

## 17. 本课综合挑战：用计划驱动重构

使用临时目录中的 `hello.py`，输入：

```text
Refactor hello.py with a visible, evidence-based plan:
- inspect the current code;
- add complete type hints;
- add a useful function docstring;
- move execution under a main guard;
- run the program and verify its exact output.

Use todo_write before editing. Keep the full todo list on every update, keep
at most one item in_progress, and do not mark verification completed until
you have real command output.
```

要求自己观察并记录：

```text
第一次 TODO 内容：
第一次实际文件修改发生在第几个工具调用：
总共更新 TODO 几次：
是否曾有两个 in_progress：
验证命令及输出：
最终是否仍有 pending：
```

最终验收：

- 计划先于编辑；
- 计划覆盖读取、修改和验证；
- 每次更新保留所有任务；
- `hello.py` 满足类型、docstring、main guard 三项目标；
- 实际运行输出正确；
- 最后所有任务 completed；
- 完成状态与真实工具证据一致；
- Agent 没有因为处理局部错误忘掉原始目标。

## 18. 常见问题与定位

### 模型没有先调用 TodoWrite

检查：

- 任务是否真的多步骤；
- 是否运行 S05；
- SYSTEM 是否保留规划指导；
- 提示词是否明确要求先规划；
- 当前模型是否支持工具调用。

Harness 目前不强制“第一个工具必须是 todo_write”。

### TODO 只显示一项，旧任务消失

新调用只提交了一项。TodoWrite 是完整替换。要求模型每次更新完整列表。

### 状态图标乱码或测试报 GBK 错误

Windows 当前终端设置：

```powershell
$env:PYTHONUTF8 = "1"
```

然后重新启动 Python。

### 三轮后没看到 reminder

可能原因：

- 模型把多个工具合并在同一个响应，只算一轮；
- 模型在第三轮后直接结束；
- 中途调用过 TodoWrite；
- 每个新的用户任务都会重新创建 counter；
- 实际没有连续三次 `stop_reason == "tool_use"`。

加入 counter trace 后再判断。

### TODO 显示 completed，但产物不正确

当前没有完成证据校验。以文件内容、测试命令和验收条件为准，不以图标为准。

### 重启后 TODO 丢失

当前只保存在内存。完成磁盘持久化扩展，或等到 S12 Task System。

### 直接传字符串也能工作

这是兼容逻辑。先尝试 JSON，再使用安全的 `ast.literal_eval`。代码不会调用危险的 `eval()`。

## 19. 设计层面的延伸思考

### 计划工具不是规划算法

Harness 只提供结构和可见状态，任务拆分质量仍来自模型。一个糟糕计划可能：

- 粒度太粗；
- 漏掉验证；
- 顺序错误；
- 任务互相重叠；
- 把实现细节误当目标。

### Reminder 是注意力机制，不是强制执行

Reminder 通过消息影响模型行为。模型仍可以忽略、错误更新或形式化应付。真正的强制规则
需要 Harness 在状态转换处验证。

### 快照简单，但并发与历史能力弱

整表快照容易实现和展示，但：

- 无法知道谁改了哪一项；
- 并发写容易覆盖；
- 不容易增量同步；
- 删除可能是遗漏，也可能是有意；
- 难以恢复历史版本。

事件日志或独立任务记录能解决部分问题，但系统复杂度会增加。

### 计划状态和执行状态需要关联

高可靠 Agent 应能回答：

```text
这个 completed 对应哪次工具调用？
哪个测试证明它完成？
文件之后是否又被修改导致证据过期？
失败后是否需要重新打开任务？
```

### TODO 列表不等于任务依赖图

平铺列表只能暗示顺序，不能表达 B 被 A 阻塞、C 与 D 可并行。S12 会加入 `blockedBy`，
多 Agent 课程还会加入 ownership。

## 20. 结课自测

不看代码回答：

1. TodoWrite 增加了什么能力，没有增加什么能力？
2. `CURRENT_TODOS` 保存在哪里，何时消失？
3. 为什么每次更新必须发送完整列表？
4. `_normalize_todos()` 为什么先 JSON 再 literal_eval？
5. 为什么不能使用 `eval()`？
6. 当前验证了哪些字段，漏了哪些不变量？
7. counter 统计的是工具数量还是模型工具轮？
8. 三轮后 reminder 在什么时刻注入？
9. 为什么模型第三轮后直接结束时可能没有 reminder？
10. 无效 TodoWrite 为什么也可能重置 counter？
11. 绿色 completed 为什么不是完成证明？
12. TodoWrite 与 S12 Task System 的核心区别是什么？

完成综合挑战、至少两个不变量扩展，并正确回答至少 10 题，就可以认为掌握了 S05。

## 21. 完成本课后的状态

你现在拥有：

```text
稳定 Agent Loop
  + 工具分发
  + 权限与 Hooks
  + todo_write 计划工具
      ├─ pending
      ├─ in_progress
      └─ completed
  + 三个无更新工具轮后的 reminder
  = 一个能把多步骤意图外显并持续更新的 Agent
```

当任务继续扩大时，所有工作仍挤在同一个消息历史里。S06 Subagent 会让主 Agent 把独立子任务
交给拥有全新上下文的子 Agent，再把结果带回主循环。

