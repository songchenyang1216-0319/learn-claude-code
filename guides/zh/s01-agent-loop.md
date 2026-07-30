# S01 实操教学指南：一个循环如何让模型持续行动

> 对应课程：[s01_agent_loop](../../s01_agent_loop/)  
> 核心代码：[code.py](../../s01_agent_loop/code.py)  
> 建议用时：60–90 分钟  
> 本课产物：一个能调用 Bash、读取结果并继续行动的最小 Agent

## 1. 学完这一课，你应该能做到什么

完成 S01 后，你不只是要“把程序跑起来”，还应该能够：

1. 用自己的话解释模型、Harness 和 Bash 各自负责什么；
2. 画出 `用户消息 → 模型 → 工具 → 工具结果 → 模型` 的循环；
3. 说清楚 `messages` 为什么是整个循环的状态；
4. 根据 `stop_reason` 判断循环应该继续还是结束；
5. 看懂一次工具调用在消息历史中增加了哪些内容；
6. 在临时目录中让 Agent 创建文件、检查文件并给出最终答复；
7. 修改系统提示词或循环日志，并预测行为会怎样变化；
8. 指出这份教学实现离生产级 Agent 还缺少哪些保护。

本课最重要的一句话是：

> 模型负责决定下一步做什么；Harness 负责执行动作、保存过程，并把结果交还给模型。

## 2. 先建立正确的心智模型

普通聊天通常只有一次模型调用：

```text
用户问题 → 模型回答 → 结束
```

但模型自己不能执行它写出来的命令。S01 在模型和真实世界之间加入一个循环：

```text
用户任务
  ↓
调用模型，并同时告诉它可使用 bash
  ↓
模型是否请求 bash？
  ├─ 是：执行命令 → 把结果追加到 messages → 再次调用模型
  └─ 否：把最终文字答复展示给用户 → 本轮结束
```

这里有三个角色：

| 角色 | 在 S01 中的实现 | 职责 |
|---|---|---|
| 决策者 | 大模型 | 根据当前消息历史决定回答还是调用工具 |
| 执行环境 | `bash` 工具 | 在当前工作目录中执行命令并返回输出 |
| Harness | `agent_loop()` | 保存消息、调模型、执行工具、回填结果、决定是否继续 |

不要把 `while True` 理解成“让模型无限运行”。它真正表达的是：

> 只要模型仍然发出工具请求，Harness 就继续；模型给出最终回答时，Harness 就退出。

## 3. 开始前的安全边界

S01 只有一个很薄的字符串拦截器，它会挡住包含 `rm -rf /`、`sudo`、
`shutdown`、`reboot` 或 `> /dev/` 的命令。这不是完整的权限系统，也不是沙箱。

因此本课必须遵守下面的练习规则：

- 在新建的临时目录中运行 Agent；
- 临时目录里不要放 API Key、私人资料或重要项目；
- 不要求 Agent 安装软件、操作系统目录或访问生产服务；
- 先阅读屏幕上以 `$` 开头的命令，再判断结果；
- 真正的权限检查会在 S03 学习，本课不要误以为字符串拦截已经足够安全。

## 4. 环境准备

### 4.1 Windows

仓库提供了一键初始化脚本。请在仓库根目录的 PowerShell 中运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\setup-windows.ps1
```

它会：

- 检查 Python 3.12；
- 创建 `.venv`；
- 安装 `requirements.txt`；
- 在缺少 `.env` 时从 `.env.example` 复制一份；
- 检查 Git Bash；
- 运行仓库测试。

如果你已经完成安装，只想复查基础环境：

```powershell
.\.venv\Scripts\python.exe --version
.\.venv\Scripts\python.exe -c "from shell_runner import find_bash; print(find_bash())"
.\.venv\Scripts\python.exe -c "import anthropic, dotenv, httpx, yaml; print('dependencies: OK')"
```

预期结果：

- Python 显示 `3.12.x`；
- 第二条命令打印一个真实存在的 `bash.exe` 路径；
- 第三条命令打印 `dependencies: OK`。

如果没有找到 Bash，请安装 Git for Windows，或在 `.env` 中设置：

```dotenv
BASH_EXECUTABLE=C:\Program Files\Git\bin\bash.exe
```

### 4.2 macOS / Linux

在仓库根目录运行：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
test -f .env || cp .env.example .env
python -c "from shell_runner import find_bash; print(find_bash())"
```

最后一条命令应该打印 Bash 的路径，例如 `/bin/bash`。

### 4.3 配置模型

打开仓库根目录的 `.env`。使用 Anthropic API 时，最少需要：

```dotenv
ANTHROPIC_API_KEY=你的真实密钥
MODEL_ID=供应商实际支持的模型ID
```

仓库也支持 OpenAI-compatible 的 `chat/completions` 接口：

```dotenv
LLM_API_STYLE=openai
ANTHROPIC_BASE_URL=供应商给出的基础地址
ANTHROPIC_API_KEY=你的真实密钥
MODEL_ID=供应商返回的精确模型ID
```

注意：

- 不要把真实密钥写进课程指南、提示词、截图或 Git 提交；
- 模型 ID 以你所用供应商当前实际开放的值为准；
- 已经存在 `.env` 时不要再次用 `.env.example` 覆盖它；
- `.env.example` 中有仓库已适配的更多供应商示例。

## 5. 第一次阅读代码：只抓住七个位置

先打开 `s01_agent_loop/code.py`，按下面顺序阅读。第一次不需要逐行研究导入细节。

### 位置 A：加载配置

```python
load_dotenv(override=True)
client = create_client(Anthropic)
MODEL = os.environ["MODEL_ID"]
```

这里完成两件事：

1. 从 `.env` 加载配置；
2. 创建一个具有 `client.messages.create(...)` 接口的模型客户端。

`create_client()` 会根据 `LLM_API_STYLE` 选择 Anthropic SDK 或仓库提供的
OpenAI-compatible 适配层。S01 的循环不需要知道背后是哪种协议。

### 位置 B：系统提示词

```python
SYSTEM = f"You are a coding agent at {os.getcwd()}. Use bash to solve tasks. Act, don't explain."
```

它告诉模型：

- 你的身份是 coding agent；
- 当前工作目录是什么；
- 你可以使用 Bash；
- 应优先行动，而不是只解释做法。

`os.getcwd()` 非常重要：Agent 的 Bash 命令也在这个目录中执行。因此从哪个目录
启动程序，决定了本课实验会影响哪个目录。

### 位置 C：工具定义

```python
TOOLS = [{
    "name": "bash",
    "description": "Run a shell command.",
    "input_schema": {
        "type": "object",
        "properties": {"command": {"type": "string"}},
        "required": ["command"],
    },
}]
```

这不是在执行 Bash，而是在向模型描述：

- 有一个名叫 `bash` 的工具；
- 调用它时必须提供 `command`；
- `command` 必须是字符串。

模型返回的工具调用必须符合这个 schema，Harness 才知道怎样取出命令。

### 位置 D：工具实现

```python
def run_bash(command: str) -> str:
    ...
    return run_bash_command(command, cwd=os.getcwd())
```

工具定义是“给模型看的接口”；`run_bash()` 才是本地真正执行命令的实现。

`run_bash_command()` 还提供了几项跨平台行为：

- Windows 自动使用 Git Bash，而不是 `cmd.exe`；
- 命令默认 120 秒超时；
- 工具返回内容最多保留 50,000 个字符；
- 没有输出时返回 `(no output)`；
- 超时和找不到 Bash 时返回可读的错误文本。

### 位置 E：调用模型并追加回答

```python
response = client.messages.create(...)
messages.append({"role": "assistant", "content": response.content})
```

模型的回答必须先进入 `messages`。否则下一轮模型不知道自己刚刚请求过什么工具。

### 位置 F：停止判断与工具结果回填

```python
if response.stop_reason != "tool_use":
    return
```

如果模型请求了工具，Harness 会执行所有 `tool_use` 块，再追加一条用户角色消息：

```python
messages.append({"role": "user", "content": results})
```

工具结果使用 `role: "user"` 是 API 消息协议的要求，不代表真人又输入了一句话。

### 位置 G：外层交互循环

```python
history = []
while True:
    query = input(...)
    history.append({"role": "user", "content": query})
    agent_loop(history)
```

代码里其实有两个循环：

| 循环 | 作用 | 何时停止 |
|---|---|---|
| 外层 REPL 循环 | 允许你连续输入多个任务 | 输入 `q`、`exit`、空行或中断 |
| 内层 Agent Loop | 完成当前任务需要的多轮模型/工具交互 | 模型不再请求工具 |

外层的 `history` 不会在每个任务后清空，所以同一次程序运行中的后续任务可以看到
前面的对话。程序退出后，这些历史不会持久化。

## 6. 手工走一遍消息变化

假设用户要求创建 `hello.py`，一种可能的消息变化如下。具体命令会随模型变化，
但消息结构不应变化。

### 第 0 轮：只有用户任务

```python
[
    {"role": "user", "content": "创建 hello.py，并让它打印 Hello, World!"}
]
```

### 第 1 轮：模型请求工具

Harness 追加模型返回的 `tool_use` 块。概念上相当于：

```python
[
    {"role": "user", "content": "创建 hello.py，并让它打印 Hello, World!"},
    {
        "role": "assistant",
        "content": [
            {"type": "tool_use", "name": "bash", "input": {"command": "..."}}
        ],
    },
]
```

因为 `stop_reason == "tool_use"`，循环继续。

### 第 1 轮工具执行后

Harness 执行命令，并用匹配的 `tool_use_id` 回填结果：

```python
[
    ...,
    {
        "role": "user",
        "content": [
            {
                "type": "tool_result",
                "tool_use_id": "与刚才工具调用相同的ID",
                "content": "(no output)",
            }
        ],
    },
]
```

`tool_use_id` 是关联请求和结果的关键。如果漏掉或填错，模型 API 无法可靠判断
这个结果属于哪一次工具调用。

### 第 2 轮：模型验证或结束

模型可能再调用一次 Bash 检查文件，也可能直接给最终答复。只要它最终返回普通文字，
并且 `stop_reason != "tool_use"`，`agent_loop()` 就结束。

## 7. 最小成功路径：在临时目录运行

### 7.1 Windows PowerShell

先在仓库根目录保存仓库位置，再切到临时练习目录：

```powershell
$courseRoot = (Resolve-Path .).Path
$s01Lab = Join-Path $env:TEMP "learn-claude-code-s01"
New-Item -ItemType Directory -Force -Path $s01Lab | Out-Null
Set-Location -LiteralPath $s01Lab
$env:PYTHONUTF8 = "1"
& "$courseRoot\.venv\Scripts\python.exe" "$courseRoot\s01_agent_loop\code.py"
```

`setup-windows.ps1` 会为用户环境永久设置 `PYTHONUTF8=1`，但启动安装脚本的旧
PowerShell 进程不会自动继承子进程修改后的环境值。上面显式设置一次，可以保证当前终端
立即使用 UTF-8；新开的终端通常会直接继承永久设置。

### 7.2 macOS / Linux

```bash
course_root="$(pwd)"
s01_lab="$(mktemp -d)"
cd "$s01_lab"
"$course_root/.venv/bin/python" "$course_root/s01_agent_loop/code.py"
```

程序启动后应看到：

```text
s01: Agent Loop
输入问题，回车发送。输入 q 退出。

s01 >>
```

输入：

```text
Create a file called hello.py that prints "Hello, World!", then run it to verify the output.
```

一种可能的过程是：

```text
$ printf 'print("Hello, World!")\n' > hello.py
(no output)
$ python hello.py
Hello, World!
Done. ...
```

模型也可能使用 `cat`、here-document 或不同的 Python 命令。不要比较命令是否逐字一致，
而要检查下面四条验收标准：

- 屏幕至少出现一次黄色的 `$ ...` 工具命令；
- 临时目录中出现 `hello.py`；
- 执行 `hello.py` 时输出 `Hello, World!`；
- 最后出现模型的普通文字答复，随后重新显示 `s01 >>`。

在同一个交互界面再输入：

```text
Read hello.py and tell me exactly what it does. Do not change the file.
```

预期现象：

- Agent 通常会用 Bash 读取刚才的文件；
- 它知道前一轮创建了 `hello.py`，因为 `history` 仍然存在；
- 它不应修改文件；
- 回答结束后仍回到同一个 `s01 >>`。

输入 `q` 退出。

## 8. 六个循序渐进的观察实验

每个实验都建议在同一个临时练习目录中进行。

### 实验 1：不使用工具时，循环是否立即结束

提示词：

```text
Do not call bash. In one sentence, explain what an agent loop is.
```

应该观察到：

- 屏幕上没有 `$ ...`；
- 模型直接给出文字；
- 内层 `agent_loop()` 只调用模型一次就返回。

结论：循环是否继续不是由用户任务长度决定，而是由模型有没有请求工具决定。

### 实验 2：一个任务可以产生多次工具调用

提示词：

```text
Create numbers.txt containing the numbers 1 through 5, display the file,
then tell me their sum.
```

模型可能一次完成，也可能依次写入、读取、计算。验收标准：

- `numbers.txt` 存在并包含 1 到 5；
- 最终答案包含 `15`；
- 如果有多次 `$ ...`，每次结果都会被送回模型后再做下一次决策。

### 实验 3：让工具返回错误，观察模型是否恢复

提示词：

```text
Read missing.txt. If it does not exist, create it with the text recovered,
then read it again.
```

应该观察到：

1. 某次 Bash 调用报告文件不存在，或者 Agent 先检查存在性；
2. Agent 没有因为工具失败就自动崩溃；
3. 模型读取错误文本后决定创建文件；
4. 最终 `missing.txt` 的内容是 `recovered`。

结论：在 S01 中，工具错误只是普通的工具结果。是否以及怎样恢复，由模型决定。
S11 才会加入 Harness 层面的系统化恢复策略。

### 实验 4：验证同一进程内有会话历史

依次输入：

```text
Create note.txt containing alpha.
```

```text
Append beta to the file you created in the previous task, then show the full file.
```

验收标准：

- 第二个任务能从上下文理解“the file”是 `note.txt`；
- 文件最后包含 `alpha` 和 `beta`；
- 退出程序再重新启动后，只问 “Which file did you create?”，模型不应可靠记得上一次进程的历史。

结论：`history` 是进程内短期上下文，不是跨会话记忆。跨会话记忆会在 S09 学习。

### 实验 5：区分“终端显示截断”和“模型收到的结果”

提示词：

```text
Use bash to run exactly `seq 1 300`, then report the final number.
```

`code.py` 只打印 `output[:200]`，所以终端中的工具输出预览大约只有前 200 个字符；
但 `tool_result` 使用的是 `output`，模型最多可以收到 `shell_runner.py` 保留的
50,000 个字符。

验收标准：

- 终端预览可能看不到 `300`；
- 模型仍应能报告最后一个数字是 `300`。

结论：日志中展示多少内容，和送入模型上下文多少内容，是两个不同的设计决策。

### 实验 6：认识简单字符串拦截的局限

提示词：

```text
Use bash to run exactly `echo sudo`.
```

因为 `run_bash()` 只做子字符串匹配，这条本来无害的命令也会返回：

```text
Error: Dangerous command blocked
```

结论：

- 这种拦截可能误伤安全命令；
- 它也不能覆盖所有危险写法；
- 真正的权限系统需要解析动作、匹配规则、请求审批并记录决策，而不是只搜几个字符串。

## 9. 动手改代码：每次只改变一个变量

建议先复制一份实验文件，不直接改课程原文件。

Windows：

```powershell
Copy-Item "$courseRoot\s01_agent_loop\code.py" "$courseRoot\s01_agent_loop\code_experiment.py"
```

macOS / Linux：

```bash
cp "$course_root/s01_agent_loop/code.py" "$course_root/s01_agent_loop/code_experiment.py"
```

后续运行 `code_experiment.py`。完成实验后，可以直接删除这份个人实验副本。

### 改动 A：显示 Agent Loop 的轮次

在 `agent_loop()` 的 `while True` 前加入计数器，并在循环顶部打印：

```python
def agent_loop(messages: list):
    round_no = 0
    while True:
        round_no += 1
        print(f"[trace] round={round_no}, messages={len(messages)}")
```

预期变化：

- 不调用工具的任务只出现一个 round；
- 每多完成一轮“模型请求工具 → Harness 回填结果”，round 会增加；
- `messages` 通常会在一次工具轮后增加两条：assistant 工具请求和 user 工具结果。

你应该尝试解释：为什么一轮工具调用通常增加两条消息，而不是一条？

### 改动 B：打印停止原因

在模型响应返回后加入：

```python
print(f"[trace] stop_reason={response.stop_reason}")
```

预期变化：

- 工具调用轮打印 `tool_use`；
- 最终文字轮通常打印 `end_turn`；
- 如果输出达到模型限制，可能出现其他停止原因，此时当前教学代码也会直接退出。

### 改动 C：改变系统提示词

把 `SYSTEM` 临时改成：

```python
SYSTEM = (
    f"You are a cautious coding agent at {os.getcwd()}. "
    "Before writing any file, inspect the current directory with bash. "
    "After writing, verify the result. Act, don't explain."
)
```

再次执行创建文件任务。预期变化：

- 写文件前更可能先出现 `pwd` 或 `ls`；
- 写文件后更可能出现 `cat`、`ls` 或运行验证；
- 工具轮数通常会增加；
- 不保证每个模型都采用完全相同的命令，但整体策略应更偏向“先检查、后验证”。

结论：系统提示词改变模型的决策策略，循环本身没有变化。

### 改动 D：加入最大轮次保护

生产系统不能假设模型一定会主动停止。可以把内层循环改为：

```python
def agent_loop(messages: list, max_rounds: int = 10):
    for round_no in range(1, max_rounds + 1):
        response = client.messages.create(
            model=MODEL, system=SYSTEM, messages=messages,
            tools=TOOLS, max_tokens=8000,
        )
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason != "tool_use":
            return

        results = []
        for block in response.content:
            if block.type == "tool_use":
                print(f"\033[33m$ {block.input['command']}\033[0m")
                output = run_bash(block.input["command"])
                print(output[:200])
                results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": output,
                })
        messages.append({"role": "user", "content": results})

    print(f"[guard] stopped after {max_rounds} rounds")
```

可以先把 `max_rounds` 默认值改成 `1`，再运行一个明确需要“创建并验证”的任务。

预期变化：

- Agent 可能只来得及执行第一次工具调用；
- Harness 会打印保护提示并结束；
- 任务可能处于“做了一半”的状态；
- 这说明“限制循环”容易，“达到限制后怎样安全收尾”是另一个设计问题。

## 10. 本课综合挑战：让 Agent 生成目录盘点报告

在临时练习目录中准备下面的数据：

```text
notes/
├── a.txt    内容为 alpha、beta 两行
└── b.txt    内容为 one、空行、three
```

Windows PowerShell 可以这样准备：

```powershell
New-Item -ItemType Directory -Force -Path .\notes | Out-Null
Set-Content -Path .\notes\a.txt -Encoding ascii -Value @("alpha", "beta")
Set-Content -Path .\notes\b.txt -Encoding ascii -Value @("one", "", "three")
```

macOS / Linux 可以这样准备：

```bash
mkdir -p notes
printf 'alpha\nbeta\n' > notes/a.txt
printf 'one\n\nthree\n' > notes/b.txt
```

启动 S01，输入：

```text
Inspect every .txt file under notes. Create REPORT.md containing a Markdown
table with each path and its number of non-empty lines, plus a total.
Verify the report before you finish.
```

不要要求模型生成某一条固定 Shell 命令。最终产物满足下面条件就算通过：

- `REPORT.md` 已创建；
- 报告同时列出 `notes/a.txt` 和 `notes/b.txt`；
- 两个文件的非空行数都是 `2`；
- 总非空行数是 `4`；
- Agent 在最终回答前读取或显示过报告，完成了验证；
- 过程中至少经历一次工具请求、工具结果回填和后续模型决策。

如果 Agent 统计错误，先查看它实际执行的命令和 `REPORT.md`，再追问：

```text
Re-check the definition: count non-empty lines only. Fix and verify REPORT.md.
```

这个追问能帮助你观察：Harness 没有内置“统计规则”，模型需要从用户反馈和工具结果中
修正自己的行动。

## 11. 常见问题与定位顺序

### 启动时报 `KeyError: 'MODEL_ID'`

原因：没有加载到 `MODEL_ID`。检查：

1. 仓库根目录是否有 `.env`；
2. `.env` 中是否存在未被注释的 `MODEL_ID=...`；
3. 是否误把配置写进了 `.env.example` 而不是 `.env`。

### 报 API Key 缺失或 401

依次检查：

1. Key 是否写在 `.env`；
2. Key 是否属于当前 `ANTHROPIC_BASE_URL` 对应的供应商；
3. `LLM_API_STYLE` 是否和供应商协议一致；
4. Key 前后是否多了引号或空格；
5. 不要在终端输出完整 Key 来排查。

### 报模型不存在或 404

`MODEL_ID` 必须是供应商实际返回的精确 ID。不同供应商即使展示名称相似，也不代表
API ID 相同。

### Windows 报 Bash 找不到

先运行：

```powershell
.\.venv\Scripts\python.exe -c "from shell_runner import find_bash; print(find_bash())"
```

如果输出 `None`，安装 Git for Windows，或在 `.env` 设置 `BASH_EXECUTABLE` 的完整路径。

### 文件出现在了错误目录

原因通常是启动程序前没有切换到临时目录。系统提示词和工具执行目录都使用
`os.getcwd()`。先在终端运行 `pwd`（Bash）或 `Get-Location`（PowerShell）确认位置。

### 只得到文字说明，没有任何 `$ ...`

可能原因：

- 任务不需要工具；
- 提示词说了“不调用 Bash”；
- 模型选择解释而没有行动；
- 当前模型对工具调用协议支持不完整。

先用最明确的任务复测：

```text
Use bash to run `pwd`, then report the result.
```

### 命令失败后程序没有自动重试

S01 没有 Harness 层重试器。错误文本会交给模型，模型可能换方法，也可能直接结束。
这是课程有意保留的限制，不一定是环境故障。

## 12. 代码中的几个容易忽略的设计点

### Bash 能力很大，但接口数量很小

只给一个 Bash 工具，模型仍能读写文件、运行程序和组合命令。这证明“Agent 能行动”
并不要求先设计大量工具。但单一 Bash 也有明显缺点：

- 权限粒度粗；
- 参数难验证；
- Shell 转义容易出错；
- 很难判断两个命令能否安全并发；
- 日志难以按“读文件”“写文件”等动作分类。

S02 会用多个专用工具改善这些问题。

### 工具结果也是上下文

Agent 并不是直接“看见终端”。它只能看见 Harness 放进 `messages` 的工具结果。
如果 Harness 截断、遗漏或错误关联结果，模型就会基于不完整信息决策。

### 最终答复也必须进入历史

`messages.append({"role": "assistant", ...})` 发生在停止判断之前。因此最终文字答复也会
被保存，下一次用户输入才能延续完整对话。

### 当前实现不是完整的生产循环

至少还缺少：

- 结构化权限审批；
- 工具级别的输入验证；
- 最大轮次、费用和 token 预算；
- API 错误重试与备用模型；
- 上下文压缩；
- 流式输出与流式工具执行；
- 并发安全调度；
- 持久化会话和审计日志；
- 取消、恢复和任务生命周期。

后续课程会逐步把这些机制叠加在同一个核心循环周围。

## 13. 结课自测

不看代码，尝试回答：

1. 为什么普通聊天模型写出命令后不会自己继续？
2. `TOOLS` 和 `run_bash()` 有什么区别？
3. 为什么工具结果必须带 `tool_use_id`？
4. 为什么工具结果使用 `role: "user"`？
5. `stop_reason == "tool_use"` 时为什么不能直接结束？
6. 外层 REPL 循环和内层 Agent Loop 分别负责什么？
7. 为什么在仓库根目录直接运行有风险？
8. 工具报错后，是 Harness 还是模型决定下一步？
9. 同一进程中 Agent 为什么记得上一轮，重启后为什么忘记？
10. 最大轮次保护解决了什么，又引入了什么新问题？

如果你能完成综合挑战，并清楚回答至少 8 个问题，就已经掌握 S01 的核心。

## 14. 完成本课后的状态

此时你拥有的是：

```text
模型
  + 一个 Bash 工具
  + messages 消息历史
  + 一个根据工具请求继续运行的循环
  = 最小可运行 Agent
```

下一课 S02 会把“所有动作都塞进 Bash”拆成多个明确工具，并讨论工具分发与并发。
