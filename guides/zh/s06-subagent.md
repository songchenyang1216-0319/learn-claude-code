# S06 实操教学指南：用全新上下文隔离子任务

> 对应课程：[s06_subagent](../../s06_subagent/)  
> 核心代码：[code.py](../../s06_subagent/code.py)  
> 前置课程：[S05 TodoWrite](s05-todo-write.md)  
> 建议用时：90–120 分钟  
> 本课产物：一个能同步委派子任务、只回收最终摘要的主 Agent

## 1. 学完这一课，你应该能做到什么

完成 S06 后，你应该能够：

1. 解释子 Agent 怎样降低主消息历史的上下文负担；
2. 区分“全新 messages”与“全新进程”；
3. 说清父子 Agent 共享和不共享的状态；
4. 看懂 `task` 工具怎样进入原有 dispatch map；
5. 解释为什么子 Agent 没有 `task` 和 `todo_write`；
6. 预测子循环的 30 次安全上限及 fallback；
7. 验证子 Agent 的工具调用仍经过 PreToolUse Hook；
8. 区分子 Agent 中间 transcript、文件副作用和最终摘要；
9. 写出信息充分、范围清晰、可验收的委派描述；
10. 为子 Agent 增加结构化返回、异常隔离、可选 transcript 或深度控制。

本课最重要的一句话是：

> 子 Agent 隔离的是注意力和消息历史，不会自动隔离文件系统、副作用、成本或权限。

## 2. 为什么 TODO 仍不足以解决超大任务

TodoWrite 能提醒主 Agent：

```text
先调查调用链
再定位 bug
再修改
再运行测试
```

但“调查调用链”本身可能读取几十个文件并产生大量工具结果。即使它只是 TODO 的第一项，
所有中间过程仍进入主 `messages`。

Subagent 把一个独立子问题移到另一份消息历史：

```text
父 messages
  → task(description)
      → 子 messages = [description]
      → 子 Agent 多轮探索
      → 只返回最终 summary
  → 父 messages 只保存 task call + summary
```

主 Agent 不必携带子 Agent 每一次读取和每一次失败尝试。

## 3. 从 S05 到 S06 的实际变化

父 Agent 原有 6 个工具：

```text
bash
read_file
write_file
edit_file
glob
todo_write
```

S06 追加：

```text
task(description)
```

注册仍是：

```python
TOOLS.append({...})
TOOL_HANDLERS["task"] = spawn_subagent
```

所以父 Agent Loop 不增加 `if block.name == "task"`。它把 `task` 当作普通 handler 调用。

新增 handler 内部恰好又运行一个较小的 Agent Loop：

```text
父循环执行 task handler
  → spawn_subagent()
      → 子循环调用同一个模型客户端
      → 子工具执行
      → 子循环结束
  → 返回字符串给父循环
```

## 4. “Fresh context”到底隔离了什么

子 Agent 从：

```python
messages = [{"role": "user", "content": description}]
```

开始。它不会自动看到：

- 父 Agent 的原始用户消息；
- 父 Agent 之前的推理和工具结果；
- 父 TODO 列表的 tool call；
- 父 Agent 已经发现但没写进 description 的事实；
- 父对话中的指代，例如“刚才那个文件”。

它能看到：

- `description` 中明确写出的内容；
- 独立的 `SUB_SYSTEM`；
- `WORKDIR` 中真实存在的文件；
- 自己后续产生的工具结果。

因此委派描述必须自包含。主 Agent 知道但 description 没写的事实，对子 Agent 等同于不存在。

## 5. 父子 Agent 的共享矩阵

当前 S06 不是新进程，也没有创建新线程。它是同一进程里的同步函数调用。

| 对象/能力 | 是否共享 | 说明 |
|---|---:|---|
| `messages` | 否 | 子 Agent 创建新列表 |
| system prompt | 否 | 父用 `SYSTEM`，子用 `SUB_SYSTEM` |
| 可见工具 schema | 部分 | 子只看到 `SUB_TOOLS` |
| 模型客户端 `client` | 是 | 使用同一对象 |
| `MODEL` | 是 | 使用同一模型 ID |
| `WORKDIR` | 是 | 访问同一目录 |
| 文件副作用 | 是 | 子写入后父立即可见 |
| handler 函数 | 是 | SUB_HANDLERS 引用同一实现 |
| Hook 注册表 | 是 | 子工具调用同一个 `trigger_hooks` |
| `CURRENT_TODOS` 变量 | 进程内共享 | 但子没有 todo_write 工具 |
| 父对话历史 | 否 | 除非写进 description |
| API 费用和时间 | 不隔离 | 子调用仍消耗真实请求 |

Fresh messages 降低父上下文增长，不会消除子任务本身的模型计算成本。

## 6. 子 Agent 的工具边界

`SUB_TOOLS` 只有：

```text
bash
read_file
write_file
edit_file
glob
```

没有：

- `task`：防止通过结构化 task 工具递归创建子 Agent；
- `todo_write`：子任务保持较小，不维护父计划；
- 父 Agent 未来可能拥有的其他高级工具。

`SUB_HANDLERS` 必须和 `SUB_TOOLS` 对齐。

“没有 task”是教学级递归防护。因为子 Agent 仍有 Bash，理论上可以启动另一个课程进程。
真正的深度限制还需要在执行上下文和权限层追踪递归深度。

## 7. 同步委派的调用时序

当前 task handler 是同步函数：

```text
父 Agent 发出 task
→ 父循环暂停
→ 子 Agent 完整运行
→ 子 Agent 返回摘要
→ 父循环才继续
```

即使模型在一个父响应里发出两个 task tool call，普通 `for` 循环也会：

```text
完整执行子任务 A
→ 再完整执行子任务 B
```

它们不会并行。第二个子任务可以看到第一个已经写入文件系统的副作用。

异步后台 Agent 会在 S13 出现。

## 8. 30 次安全上限的精确含义

子循环：

```python
for _ in range(30):
    response = client.messages.create(...)
```

这里的 30 是最多 30 次模型调用，不是：

- 30 个工具；
- 30 条消息；
- 30 秒；
- 30 个文件。

一个模型响应可包含多个 tool call，它们仍只占一次循环迭代。

如果第 30 次模型调用仍返回工具请求：

1. 子 Agent 会执行该批工具；
2. 追加 tool results；
3. `for` 循环耗尽；
4. 最后一条消息是 user tool_result，不是最终文字；
5. 代码向后查找最近的 assistant 文本；
6. 如果一直没有文本，返回：

```text
Subagent stopped after 30 turns without final answer.
```

这个上限避免无限运行，但没有取消正在执行的长 Bash，也没有按 token、费用或时间预算限制。

## 9. `extract_text()` 与 summary-only

```python
def extract_text(content) -> str:
    if not isinstance(content, list):
        return str(content)
    return "\n".join(
        getattr(block, "text", "")
        for block in content
        if getattr(block, "type", None) == "text"
    )
```

它只保留最终消息中的 text blocks，不返回：

- tool_use 参数；
- tool_result；
- 子 messages；
- 子 Agent 的中间文字；
- 工具耗时；
- 完成状态。

父 Agent 收到的 task tool result 只是这个字符串。

如果最终响应有多个 text block，会用换行连接。如果没有最终文字，会进入安全上限 fallback
的向后搜索逻辑。

## 10. Hook 在父子循环中的触发差异

父 Agent 的 task 调用会经过：

```text
父 PreToolUse(permission_hook, log_hook)
→ spawn_subagent
→ 父 PostToolUse
```

子 Agent 的每个基础工具也经过：

```text
子 PreToolUse(permission_hook, log_hook)
→ SUB_HANDLERS
→ 子 PostToolUse
```

但子循环没有调用：

- `UserPromptSubmit` Hook；
- `Stop` Hook；
- Todo reminder。

所以“子工具继承 Hook 安全策略”不等于“子 Agent 完整复制父生命周期”。

## 11. 准备隔离实验目录

### 11.1 Windows PowerShell

在仓库根目录运行：

```powershell
$courseRoot = (Resolve-Path .).Path
$s06Lab = Join-Path $env:TEMP "learn-claude-code-s06"
New-Item -ItemType Directory -Force -Path $s06Lab | Out-Null
Set-Location -LiteralPath $s06Lab
$env:PYTHONUTF8 = "1"
Set-Content -Path .\math_utils.py -Encoding ascii -Value @(
    "def add(a, b):",
    "    return a + b"
)
Set-Content -Path .\text_utils.py -Encoding ascii -Value @(
    "def shout(text):",
    "    return text.upper() + '!'"
)
& "$courseRoot\.venv\Scripts\python.exe" "$courseRoot\s06_subagent\code.py"
```

### 11.2 macOS / Linux

在仓库根目录运行：

```bash
course_root="$(pwd)"
s06_lab="$(mktemp -d)"
cd "$s06_lab"
printf 'def add(a, b):\n    return a + b\n' > math_utils.py
printf "def shout(text):\n    return text.upper() + '!'\n" > text_utils.py
"$course_root/.venv/bin/python" "$course_root/s06_subagent/code.py"
```

启动后应看到：

```text
s06: Subagent — spawn sub-agents with fresh context, summary only
Type a question, press Enter. Type q to quit.

s06 >>
```

## 12. 第一次阅读代码：按八个位置理解

### 位置 A：父系统提示

```python
SYSTEM = (
    f"You are a coding agent at {WORKDIR}. "
    "For complex sub-problems, use the task tool to spawn a subagent."
)
```

它鼓励委派，但不强制。模型仍决定何时直接处理、何时调用 task。

### 位置 B：子系统提示

```python
SUB_SYSTEM = (
    f"You are a coding agent at {WORKDIR}. "
    "Complete the task you were given, then return a concise summary. "
    "Do not delegate further."
)
```

它要求直接完成并返回摘要。真正的结构化递归限制还来自 `SUB_TOOLS` 没有 task。

### 位置 C：两套工具集合

父 `TOOLS` 包含 task 和 TodoWrite；子 `SUB_TOOLS` 只包含基础工具。

不要只从 handler 字典判断模型能否调用某工具。模型是否知道工具存在由传给 API 的 tools
列表决定。

### 位置 D：Fresh messages

```python
messages = [{"role": "user", "content": description}]
```

这是上下文隔离的核心代码。没有复制父 history。

### 位置 E：子循环

结构仍然是：

```text
调用模型
→ 追加 assistant
→ 有 tool_use：执行并回填
→ 无 tool_use：break
```

说明 Subagent 不是另一种 Agent 原理，而是同一个 Agent Loop 的嵌套实例。

### 位置 F：子工具日志

通过 Hook 后打印：

```python
print(f"[sub] {block.name}: {str(output)[:100]}")
```

所以允许的子工具通常会显示两条可观测信息：

```text
[HOOK] read_file
[sub] read_file: ...
```

被 permission Hook 阻止时不会打印 `[sub]`，因为代码直接 `continue`。

### 位置 G：只回传摘要

函数结束后：

```python
return result
```

局部 `messages` 随函数返回失去引用，父 Agent 只获得字符串。文件修改则已经发生，不会回滚。

### 位置 H：父 dispatch 不变

父循环执行：

```python
handler = TOOL_HANDLERS.get(block.name)
output = handler(**block.input)
```

当名称是 task 时，handler 恰好是 `spawn_subagent`。父消息协议完全沿用普通工具结果。

## 13. 怎样写好委派描述

一个可靠 description 至少包含：

```text
Goal        要解决的具体问题
Scope       可以查看或修改哪些路径
Context     父 Agent 已确认且子 Agent 无法自行推断的事实
Constraints 禁止事项、兼容要求、工具限制
Deliverable 返回什么或创建什么
Validation  怎样证明完成
```

差的委派：

```text
Check that file and fix it.
```

好的委派：

```text
Inspect math_utils.py in the current workspace. Determine its public functions,
identify missing type hints, and return a concise recommendation. Do not modify
files. Include function names and proposed signatures.
```

子 Agent 没有父对话中的“that file”，所以路径必须明确。

## 14. 最小成功路径

输入：

```text
Use the task tool to delegate this exact subtask:
Inspect math_utils.py and text_utils.py without modifying them. Summarize each
function, its input/output behavior, and one quality improvement. Return a
concise report to the parent.
Then, as the parent, combine the returned summary into your final answer.
```

典型结构：

```text
[HOOK] task
[Subagent spawned]
[HOOK] read_file
[sub] read_file: def add...
[HOOK] read_file
[sub] read_file: def shout...
[Subagent done]
...父 Agent 最终摘要...
```

验收标准：

- 父 Agent 使用 `task`；
- 出现 spawned 与 done；
- 子 Agent 使用基础工具读取文件；
- 子 Agent 没有调用 TodoWrite 或 task；
- 父 Agent 收到的是最终摘要，不是完整子 transcript；
- 文件没有被修改；
- 最终回答覆盖两个函数和改进建议。

## 15. 八个观察实验

### 实验 1：证明父对话不会自动传给子 Agent

输入：

```text
Remember the code word PINEAPPLE in this parent conversation.
Call task with description exactly:
`What code word appeared earlier in the parent conversation? Do not inspect files.`
Do not include the code word inside the description.
```

如果父模型遵守 exact description，子 Agent 应说明它不知道，因为 fresh messages 里只有该问题。

模型可能把 code word 自行写入 description，导致实验失效。打开工具参数日志或在实验副本中
打印 `description`，确认父 Agent 实际传了什么。

### 实验 2：显式传入上下文后子 Agent能使用

输入：

```text
Call task with a description that explicitly says:
`The parent code word is PINEAPPLE. Return it and explain that it came from
the delegated description.`
```

预期 task 结果包含 PINEAPPLE。

结论：上下文隔离不阻止主动传递，只阻止隐式继承。

### 实验 3：子 Agent 文件副作用对父可见

输入：

```text
Delegate a task to create child-output.txt containing exactly `made by child`
with write_file and verify it. After the task returns, the parent must use
read_file to independently verify child-output.txt.
```

验收：

- 子 Agent 写文件并验证；
- task 只返回摘要；
- 父 Agent 之后读到相同文件；
- fresh messages 没有提供文件隔离。

### 实验 4：子 Agent 没有递归 task

输入：

```text
Delegate this subtask: inspect math_utils.py. Try to use a task tool if one is
available; otherwise perform the inspection directly and state whether task
was available.
```

预期：

- 子模型看到的 tools 中没有 task；
- 它直接使用 read_file，或说明无法再委派；
- 不出现第二层 `[Subagent spawned]`。

### 实验 5：权限 Hook 同样保护子工具

输入：

```text
Delegate a subtask that uses bash to run exactly `echo sudo`, then report the
tool result. Do not use an alternative command.
```

预期：

- 父 task 本身通过；
- 子 Bash 进入共享 PreToolUse；
- deny list 阻止；
- 不出现 `[sub] bash: sudo`；
- 子 Agent最终摘要应说明权限被拒绝。

### 实验 6：父 Stop Hook 不统计子内部工具

完成一个子 Agent 使用多个读取工具的任务。

父 `summary_hook()` 遍历父 messages。父 history 中只有 task 的一个 tool result，不含子内部
tool results。因此 Stop 统计通常把整个子任务计为 1 次父工具调用，而不是子内部调用总数。

如果父在 task 前后还用了其他工具，数量相应增加。

### 实验 7：两个 task 默认串行

输入：

```text
In one response if possible, launch one task to inspect math_utils.py and a
second independent task to inspect text_utils.py. Return both summaries.
```

如果父模型一次返回两个 task，会看到：

```text
spawn A → 子 A 完成 → done A
spawn B → 子 B 完成 → done B
```

不会交错。父 `for` 循环逐个调用同步 handler。

### 实验 8：摘要减少父上下文，不减少总工作

让子 Agent 读取多个文件并进行多轮调查。在实验副本中打印：

```python
print(f"[sub trace] child messages={len(messages)}")
print(f"[sub trace] summary chars={len(result)}")
```

预期子 messages 明显多于父收到的一条 task result。模型 API 请求、时间和费用已经实际发生，
只是父上下文只保留摘要。

## 16. 离线验证 fresh messages 与 summary

可以用假客户端返回固定响应，不发网络请求：

```python
from copy import deepcopy
from types import SimpleNamespace


class FakeMessages:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.seen = []

    def create(self, **kwargs):
        self.seen.append(deepcopy(kwargs["messages"]))
        return next(self.responses)


tool_call = SimpleNamespace(
    type="tool_use",
    id="tool-1",
    name="read_file",
    input={"path": "math_utils.py"},
)
final_text = SimpleNamespace(
    type="text",
    text="math_utils.py defines add(a, b).",
)
```

构造两个 response：

```python
responses = [
    SimpleNamespace(
        content=[tool_call],
        stop_reason="tool_use",
    ),
    SimpleNamespace(
        content=[final_text],
        stop_reason="end_turn",
    ),
]
```

替换模块 `client.messages` 后调用：

```python
result = spawn_subagent("Inspect math_utils.py")
```

验收：

- `seen[0]` 只有一条 user description；
- 第二次模型调用才包含子工具请求和结果；
- `result` 只等于最终 text；
- 父 history 没被传入。

## 17. 修改实验：返回结构化子任务结果

当前 task 只返回字符串，无法区分正常完成和触发上限。定义：

```python
from dataclasses import dataclass, asdict


@dataclass
class SubagentResult:
    status: str
    summary: str
    model_turns: int
    tool_calls: int
```

在循环中计数：

```python
model_turns = 0
tool_calls = 0
finished = False

for _ in range(30):
    model_turns += 1
    ...
    if response.stop_reason != "tool_use":
        finished = True
        break
    ...
    if block.type == "tool_use":
        tool_calls += 1
```

返回 JSON 字符串：

```python
return json.dumps(asdict(SubagentResult(
    status="completed" if finished else "max_turns",
    summary=result,
    model_turns=model_turns,
    tool_calls=tool_calls,
)), ensure_ascii=False)
```

预期父 Agent 能明确知道子任务是否正常完成，而不是把 fallback 文本当普通摘要。

## 18. 修改实验：保存调试 transcript，但不塞回父上下文

调试 summary 错误时，需要查看子过程。可以可选落盘：

```python
from datetime import datetime, timezone


def save_subagent_transcript(messages):
    transcript_dir = WORKDIR / ".subagent-transcripts"
    transcript_dir.mkdir(exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = transcript_dir / f"{timestamp}.json"
    path.write_text(
        json.dumps(messages, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    return path
```

父 task result 仍只返回：

```text
summary + transcript path
```

而不是整个 transcript。

注意：

- transcript 可能含源码和敏感工具结果；
- 需要访问控制、清理策略和脱敏；
- 文件名还应加入唯一 ID，避免同秒冲突；
- 默认关闭比默认保存更安全。

## 19. 修改实验：隔离子 Agent 异常

当前模型 API 或 handler 抛异常会穿过 `spawn_subagent()`，进而让父 Agent Loop 崩溃。

加入：

```python
def spawn_subagent(description: str) -> str:
    try:
        return run_subagent(description)
    except Exception as error:
        return json.dumps({
            "status": "failed",
            "summary": "",
            "error": f"{type(error).__name__}: {error}",
        })
```

其中原循环移到 `run_subagent()`。

预期：

- 子任务失败成为 task tool result；
- 父 Agent 可以重试、缩小任务或直接处理；
- 主进程不因一次子任务失败退出。

生产错误不应把 API Key、完整栈或敏感路径直接暴露给模型或用户。

## 20. 修改实验：显式深度而非删除工具

如果希望允许有限递归，可以传递执行上下文：

```python
MAX_SUBAGENT_DEPTH = 2


def spawn_subagent(description: str, depth: int = 1) -> str:
    if depth > MAX_SUBAGENT_DEPTH:
        return "Error: maximum subagent depth exceeded"
    ...
```

但只加参数还不够。子 task handler 必须绑定：

```python
lambda description: spawn_subagent(
    description,
    depth=depth + 1,
)
```

并且工具 schema、日志、预算、取消和权限都需要传播深度信息。

本课原始实现选择完全不给 task，概念更简单，也更容易保证不会结构化递归爆炸。

## 21. 修改实验：为子任务选择轻量模型

可以增加：

```dotenv
SUBAGENT_MODEL_ID=某个可用模型ID
```

代码：

```python
SUBAGENT_MODEL = os.getenv("SUBAGENT_MODEL_ID", MODEL)
```

子循环使用：

```python
model=SUBAGENT_MODEL
```

比较同一只读调查任务：

```text
耗时
工具调用数
摘要准确性
遗漏
成本
```

轻量模型可能更快，但复杂代码调查的摘要质量可能下降。不要只根据响应速度选择。

## 22. 本课综合挑战：双子任务审查与父级汇总

要求主 Agent：

```text
Use two separate task calls:

Task A: inspect math_utils.py without modifying it. Create reports/math.md
with its API, risks, and a recommended typed signature. Verify the report.

Task B: inspect text_utils.py without modifying it. Create reports/text.md
with its API, risks, and a recommended typed signature. Verify the report.

After both tasks finish, the parent must read both report files, create
OVERVIEW.md comparing them, and verify OVERVIEW.md. Do not let either child
edit the source files.
```

验收标准：

- 出现两次 spawned/done；
- 每个子 Agent 只收到自己的完整 description；
- 两个子任务默认顺序执行；
- `reports/math.md` 与 `reports/text.md` 都存在；
- 源文件保持不变；
- 父 Agent 读取两个报告；
- `OVERVIEW.md` 同时包含 add 与 shout 的建议签名；
- 父 history 中只保留两个 task 摘要，不含完整子 transcript；
- 最终由父 Agent 做跨子任务综合，而不是让某个子 Agent猜测另一份结果。

## 23. 常见问题与定位

### 模型没有使用 task

检查：

- 任务是否明确要求委派；
- 是否运行 S06；
- 父 TOOLS 是否 append 了 task；
- 工具参数名是 `description`，不是旧场景数据里的 `prompt`；
- 模型供应商是否支持工具调用。

### 子 Agent 不知道父对话里的信息

这是 fresh messages 的预期行为。把必要事实、路径、约束和产出写进 description。

### 子 Agent 修改了父工作区

父子共享 `WORKDIR` 和 handler。上下文隔离不是文件隔离。需要只读提示、权限规则、临时目录
或后续 worktree 隔离。

### 父 Agent 没看到子过程

task 设计为 summary-only。查看终端 `[sub]` 日志，或实现可选 transcript 落盘，不要默认把
完整过程塞回父 messages。

### 子任务完成后父 Agent 卡很久

task 是同步 handler。父循环等待子 Agent 完整结束。检查子任务是否仍在工具循环、Bash 是否
长时间运行、API 是否重试。

### Stop 汇总数量比终端子工具少

父 Stop Hook 只统计父 messages 中的 tool results。子内部结果被隔离并丢弃。

### 返回了 30 turns fallback

子 Agent 没在 30 次模型调用内给最终文字。缩小任务、提高 description 质量、增加结构化状态，
不要只无限提高上限。

### 子权限提示没有冒泡式 UI

教学版直接复用同一终端的同步 Hook，所以输入自然出现在父终端；它没有独立 permission mode
或正式的 bubble 协议。

## 24. 设计层面的延伸思考

### 委派质量决定隔离后的可用性

隔离越强，隐式上下文越少，description 质量越重要。好的委派不是一句“帮我看看”，而是一份
小型任务契约。

### Summary 是有损压缩

父上下文变小的代价是：

- 中间证据可能丢失；
- 子结论可能过度概括；
- 父无法直接审计推导过程；
- 错误摘要会影响后续决策。

可以通过结构化结果、证据路径和可选 transcript 平衡。

### 文件副作用使子任务不是纯函数

如果子 Agent 只研究并返回摘要，task 接近纯函数；一旦写文件，结果由“摘要 + 工作区变化”
共同组成。父 Agent 应独立验证重要副作用。

### 同步简单，但会放大延迟

同步保证父拿到结果后再继续，因果关系清晰。多个独立子任务则可能浪费并行机会。并发之后
必须处理文件冲突、预算、取消和结果通知。

### 递归能力需要全局预算

允许子 Agent 再委派时，最大深度只是一个维度，还需要：

- 总 Agent 数；
- 每层并发数；
- 总模型调用；
- token/费用预算；
- 时间截止；
- 取消传播。

## 25. 结课自测

不看代码回答：

1. 子 Agent 为什么能减少父上下文增长？
2. Fresh messages 是否代表新进程？
3. 父子 Agent 共享哪些重要对象？
4. 子 Agent 为什么看不到父 TODO？
5. task description 为什么必须自包含？
6. 子 Agent 能否通过结构化 task 工具继续递归？
7. 子工具是否经过权限 Hook？
8. 哪些父生命周期 Hook 没在子循环触发？
9. 30 次上限统计什么？
10. 第 30 次仍为 tool_use 时怎样 fallback？
11. Summary-only 节省了什么，没有节省什么？
12. 两个 task tool call 当前是并行还是串行？

完成综合挑战、结构化返回扩展，并正确回答至少 10 题，就可以认为掌握了 S06。

## 26. 完成本课后的状态

你现在拥有：

```text
父 Agent
  ├─ 自己的 messages、TODO 和 7 个工具
  └─ task(description)
       → 子 Agent
          ├─ fresh messages
          ├─ 独立 SUB_SYSTEM
          ├─ 5 个基础工具
          ├─ 共享 WORKDIR 与 Hooks
          ├─ 最多 30 次模型调用
          └─ summary-only 返回
```

下一课 S07 Skill Loading 会解决另一个上下文问题：不是把所有专业知识永久塞进 system prompt，
而是让 Agent 在真正需要时按名称加载一份技能说明。

