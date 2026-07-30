# S02 实操教学指南：从一个 Bash 到可扩展工具系统

> 对应课程：[s02_tool_use](../../s02_tool_use/)  
> 核心代码：[code.py](../../s02_tool_use/code.py)  
> 前置课程：[S01 Agent Loop](s01-agent-loop.md)  
> 建议用时：90–120 分钟  
> 本课产物：一个拥有 5 个工具、能按名称分发调用的 Agent

## 1. 学完这一课，你应该能做到什么

完成 S02 后，你应该能够：

1. 解释为什么“Bash 什么都能做”仍不代表“只需要 Bash 就够了”；
2. 区分工具定义、工具实现和工具注册三个概念；
3. 看懂 JSON Schema 怎样向模型描述工具参数；
4. 解释 `TOOL_HANDLERS` 怎样把工具名映射到 Python 函数；
5. 独立增加一个新工具，而不修改 Agent Loop 的控制结构；
6. 说清 `safe_path()` 能保护什么、不能保护什么；
7. 分辨“一次返回多个工具调用”和“多个工具并发执行”；
8. 预测 `read_file`、`write_file`、`edit_file`、`glob` 的成功与失败结果；
9. 通过日志观察模型为什么选择某个工具；
10. 指出当前工具系统在校验、异常隔离和并发方面的生产化缺口。

本课最重要的设计公式是：

```text
一个可调用工具
= 给模型看的 schema
+ Harness 中的处理函数
+ 工具名到处理函数的注册关系
```

## 2. 从 S01 到 S02，究竟改变了什么

S01 只有一个工具：

```python
TOOLS = [{"name": "bash", ...}]
```

循环执行工具时，代码可以直接写死：

```python
output = run_bash(block.input["command"])
```

S02 有 5 个工具：

| 工具名 | 主要用途 | 是否直接修改文件 |
|---|---|---|
| `bash` | 执行通用 Shell 命令 | 取决于命令 |
| `read_file` | 读取文件，可限制行数 | 否 |
| `write_file` | 创建或完整覆盖文件 | 是 |
| `edit_file` | 精确替换第一次出现的文本 | 是 |
| `glob` | 按模式查找文件 | 否 |

此时再把执行函数写死就会变成一长串分支：

```python
if block.name == "bash":
    ...
elif block.name == "read_file":
    ...
elif block.name == "write_file":
    ...
```

S02 改用分发映射：

```python
TOOL_HANDLERS = {
    "bash": run_bash,
    "read_file": run_read,
    "write_file": run_write,
    "edit_file": run_edit,
    "glob": run_glob,
}
```

Agent Loop 中只需：

```python
handler = TOOL_HANDLERS.get(block.name)
output = handler(**block.input) if handler else f"Unknown: {block.name}"
```

因此 S02 并没有重新设计 Agent Loop。模型调用、消息追加、停止判断和结果回填都沿用
S01。变化集中在“模型可以调用哪些工具，以及 Harness 怎样找到对应实现”。

## 3. 为什么专用工具仍然有价值

Bash 的能力范围很大，但要求模型自己处理命令语法、引号、转义、平台差异和退出状态。
例如写入一段同时包含单引号、双引号和换行的 Python 代码，用 Bash 可能需要 here-document
或复杂转义；使用 `write_file` 时只需结构化参数：

```json
{
  "path": "hello.py",
  "content": "print(\"Hello\")\n"
}
```

专用工具带来四个直接好处：

- **意图明确**：看到 `read_file` 就知道模型想读取，不必解析 `cat`、`sed` 或 `head`；
- **参数可描述**：schema 能声明必填字段和字段类型；
- **权限可细分**：后续可以允许读、询问写、拒绝危险命令；
- **实现可替换**：模型接口不变，底层可以加入编码、日志、限流或远程存储。

代价是工具数量增加后，模型需要在更多选项中选择。工具名称和描述设计得不好时，
模型可能仍然用 Bash 读写文件，或者选错专用工具。

## 4. 本课安全边界

S02 比 S01 多了 `safe_path()`，但仍然不是完整沙箱：

- `read_file`、`write_file`、`edit_file` 和 `glob` 会把路径限制在 `WORKDIR`；
- `safe_path()` 会解析 `..` 和符号链接后再检查；
- `bash` 仍能使用 `../`、绝对路径或其他 Shell 能力；
- Bash 仍只有 S01 的简单危险子字符串拦截；
- 工具执行前没有用户审批；
- `write_file` 会直接覆盖已有文件。

因此仍要在临时目录中学习。S02 的安全性提升应理解为：

> 部分专用文件工具具备工作区边界，不代表 Agent 整体已经被限制在工作区。

## 5. 环境与临时练习目录

如果已经完成 S01，可以直接复用 `.venv` 和 `.env`。

### 5.1 Windows PowerShell

在仓库根目录运行：

```powershell
$courseRoot = (Resolve-Path .).Path
$s02Lab = Join-Path $env:TEMP "learn-claude-code-s02"
New-Item -ItemType Directory -Force -Path $s02Lab | Out-Null
Set-Location -LiteralPath $s02Lab
$env:PYTHONUTF8 = "1"
```

准备三个练习文件：

```powershell
New-Item -ItemType Directory -Force -Path .\docs | Out-Null
Set-Content -Path .\docs\alpha.txt -Encoding ascii -Value @("shared: orange", "owner: Alice")
Set-Content -Path .\docs\beta.txt -Encoding ascii -Value @("shared: orange", "owner: Bob")
Set-Content -Path .\docs\gamma.txt -Encoding ascii -Value @("shared: orange", "owner: Carol")
```

启动 S02：

```powershell
& "$courseRoot\.venv\Scripts\python.exe" "$courseRoot\s02_tool_use\code.py"
```

### 5.2 macOS / Linux

在仓库根目录运行：

```bash
course_root="$(pwd)"
s02_lab="$(mktemp -d)"
cd "$s02_lab"
mkdir -p docs
printf 'shared: orange\nowner: Alice\n' > docs/alpha.txt
printf 'shared: orange\nowner: Bob\n' > docs/beta.txt
printf 'shared: orange\nowner: Carol\n' > docs/gamma.txt
"$course_root/.venv/bin/python" "$course_root/s02_tool_use/code.py"
```

程序启动后应看到：

```text
s02: Tool Use — 在 s01 基础上加了 4 个工具
输入问题，回车发送。输入 q 退出。

s02 >>
```

如果启动失败，先回到
[S01 的环境准备和故障排查](s01-agent-loop.md#4-环境准备)。S02 沿用同一个模型与
Shell 适配层。

## 6. 第一次阅读代码：按八个位置理解

### 位置 A：固定工作目录

```python
WORKDIR = Path.cwd()
```

程序启动时记录当前目录。后续所有专用文件工具都以这个目录作为根。

这和每次调用时重新执行 `Path.cwd()` 有细微区别：即使未来某段 Python 代码改变了当前
进程目录，`WORKDIR` 仍保留启动时的值。

### 位置 B：路径边界

```python
def safe_path(p: str) -> Path:
    path = (WORKDIR / p).resolve()
    if not path.is_relative_to(WORKDIR):
        raise ValueError(f"Path escapes workspace: {p}")
    return path
```

它执行三个步骤：

1. 把模型给出的路径拼到 `WORKDIR` 下；
2. 用 `resolve()` 处理 `.`、`..` 和符号链接；
3. 检查最终路径是否仍位于 `WORKDIR` 内。

示例：

| 输入 | 假设工作目录为 `/tmp/lab` | 结果 |
|---|---|---|
| `docs/a.txt` | `/tmp/lab/docs/a.txt` | 允许 |
| `./docs/../docs/a.txt` | `/tmp/lab/docs/a.txt` | 允许 |
| `../secret.txt` | `/tmp/secret.txt` | 拒绝 |
| `/etc/hosts` | `/etc/hosts` | 拒绝 |

`safe_path()` 抛出的异常会被各文件处理函数捕获，并转成 `Error: ...` 工具结果。

### 位置 C：读取工具

```python
def run_read(path: str, limit: int | None = None) -> str:
    lines = safe_path(path).read_text().splitlines()
    if limit and limit < len(lines):
        lines = lines[:limit] + [f"... ({len(lines) - limit} more lines)"]
    return "\n".join(lines)
```

行为：

- 读取整个文本文件；
- `limit` 存在且小于总行数时，只返回前几行；
- 末尾追加还有多少行未显示；
- 文件不存在、无法解码或路径越界时返回错误文本。

它返回的是字符串，不返回文件对象，也不直接把内容打印给用户。Harness 会先把结果
交给模型。

### 位置 D：写入工具

```python
def run_write(path: str, content: str) -> str:
    file_path = safe_path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content)
    return f"Wrote {len(content)} bytes to {path}"
```

行为：

- 自动创建缺失的父目录；
- 文件不存在时创建；
- 文件存在时完整覆盖；
- 不会自动保留旧内容；
- 成功后返回写入长度。

注意：代码使用 `len(content)`，它实际统计 Python 字符数，提示文本却写成了 bytes。
ASCII 内容下两者通常相同，中文等 UTF-8 内容下会不同。这是后面的扩展实验之一。

### 位置 E：编辑工具

```python
def run_edit(path: str, old_text: str, new_text: str) -> str:
    text = file_path.read_text()
    if old_text not in text:
        return f"Error: text not found in {path}"
    file_path.write_text(text.replace(old_text, new_text, 1))
```

它不是正则替换，也不是补丁系统：

- `old_text` 必须精确匹配；
- 只替换第一次出现的位置；
- 找不到时不写文件；
- 大小写、空格和换行都属于精确匹配的一部分。

“找不到就不写”非常重要。它避免模型定位错误时悄悄产生一个看似成功的文件。

### 位置 F：Glob 工具

```python
for match in g.glob(pattern, root_dir=WORKDIR):
    if (WORKDIR / match).resolve().is_relative_to(WORKDIR):
        results.append(match)
```

它返回相对 `WORKDIR` 的匹配结果，并再次过滤解析后越界的路径。

当前实现没有传入 `recursive=True`，因此不要把 `**` 当成完整的递归搜索保证。
基础实验优先使用 `docs/*.txt`、`src/*.py` 这类明确模式。后面会把递归和排序作为改动实验。

### 位置 G：Schema 与 Handler

以 `edit_file` 为例：

```python
{
    "name": "edit_file",
    "description": "Replace exact text in a file once.",
    "input_schema": {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "old_text": {"type": "string"},
            "new_text": {"type": "string"},
        },
        "required": ["path", "old_text", "new_text"],
    },
}
```

它和处理函数参数必须对得上：

```python
def run_edit(path: str, old_text: str, new_text: str) -> str:
```

分发时的 `handler(**block.input)` 会把 JSON 对象展开成关键字参数：

```python
handler(path="a.py", old_text="x", new_text="y")
```

如果 schema 使用 `old_string`，函数却要求 `old_text`，执行时就会出现参数不匹配。
因此“schema 和函数签名同步”是添加工具时的第一条约束。

### 位置 H：通用分发循环

```python
for block in response.content:
    if block.type == "tool_use":
        print(f"\033[33m> {block.name}\033[0m")
        handler = TOOL_HANDLERS.get(block.name)
        output = handler(**block.input) if handler else f"Unknown: {block.name}"
        results.append({
            "type": "tool_result",
            "tool_use_id": block.id,
            "content": output,
        })
```

这段逻辑不关心 `read_file` 怎样读、`edit_file` 怎样改。它只负责：

1. 取得工具名；
2. 查找处理函数；
3. 把结构化参数传进去；
4. 收集带原始 `tool_use_id` 的结果。

找不到工具名时会返回 `Unknown: 工具名`，不会直接索引字典导致 `KeyError`。

## 7. 五个工具的完整接口速查

| 工具 | 必填参数 | 可选参数 | 成功结果 | 常见错误 |
|---|---|---|---|---|
| `bash` | `command: str` | 无 | 命令输出或 `(no output)` | 超时、找不到 Bash、危险字符串被挡 |
| `read_file` | `path: str` | `limit: int` | 文件文本 | 不存在、越界、解码失败 |
| `write_file` | `path: str`, `content: str` | 无 | `Wrote N bytes to ...` | 越界、无权限、编码问题 |
| `edit_file` | `path: str`, `old_text: str`, `new_text: str` | 无 | `Edited ...` | 文件不存在、原文找不到、越界 |
| `glob` | `pattern: str` | 无 | 每行一个路径或 `(no matches)` | 无效模式、匹配结果越界 |

从副作用角度，可以先做一个教学级分类：

```text
候选只读：read_file、glob
明确写入：write_file、edit_file
取决于输入：bash
```

这只是并发判断的起点，不是完整规则。比如两个读取操作通常可以并发；两个编辑操作
即使修改不同文件也需要明确的冲突策略；Bash 是否只读则必须分析具体命令。

## 8. 手工走一遍多工具分发

用户提出：

```text
读取 docs/alpha.txt 和 docs/beta.txt，告诉我它们共同的 shared 值。
```

模型可能在一个响应里返回两个工具调用：

```text
tool_use(read_file, {"path": "docs/alpha.txt"})
tool_use(read_file, {"path": "docs/beta.txt"})
```

Harness 的处理顺序是：

```text
response.content 第 1 个 read_file
  → TOOL_HANDLERS["read_file"]
  → run_read(path="docs/alpha.txt")
  → 保存 tool_result A

response.content 第 2 个 read_file
  → TOOL_HANDLERS["read_file"]
  → run_read(path="docs/beta.txt")
  → 保存 tool_result B

把 [tool_result A, tool_result B] 一起追加到 messages
  → 再调用模型
```

必须注意：

> “同一个模型响应中有两个 tool_use”不等于“两个处理函数同时运行”。

当前代码使用普通 `for` 循环，所以 A 完成后才开始 B。它保持原始顺序，但没有并发。

还有一个因果限制：模型在发出 B 时，还没看到 A 的真实结果。因此适合放在同一响应中的
通常是互相独立的调用，例如同时读取两个已知文件。不适合把“根据 A 的输出决定 B 参数”
硬塞在同一响应里。

## 9. 最小成功路径

在已经启动的 `s02 >>` 中输入：

```text
Use glob to find docs/*.txt. Read every matched file with read_file, without
using bash for file access. Tell me the shared value and list every owner.
```

一种典型日志结构是：

```text
> glob
docs/alpha.txt
docs/beta.txt
docs/gamma.txt
> read_file
shared: orange
owner: Alice
> read_file
shared: orange
owner: Bob
> read_file
shared: orange
owner: Carol
The shared value is orange. The owners are Alice, Bob, and Carol.
```

Windows 上 `glob` 的路径分隔符可能显示为 `docs\alpha.txt`，macOS/Linux 通常显示为
`docs/alpha.txt`。二者指向相同的相对路径，不应据此判断实验失败。

工具调用的分组可能不同：

- 模型可能先 `glob`，下一轮再一次返回 3 个 `read_file`；
- 也可能每读一个文件就回到模型一次；
- 它也可能在回答前重复读取或检查。

验收标准不是固定轮数，而是：

- 使用 `glob` 找到 3 个文件；
- 使用 `read_file` 获取内容，没有用 Bash 代替文件读取；
- 最终共同值为 `orange`；
- 最终列出 Alice、Bob、Carol；
- 所有工具结果都被回填后，模型才给出最终答案。

## 10. 八个循序渐进的实验

### 实验 1：专用工具和 Bash 会竞争

输入：

```text
Read docs/alpha.txt and tell me its owner.
```

模型可能选 `read_file`，也可能选 Bash。再输入一个更明确的版本：

```text
Use read_file, not bash, to read docs/alpha.txt and tell me its owner.
```

预期：

- 第二次应显示 `> read_file`；
- 工具结果包含 `owner: Alice`；
- 最终答案包含 Alice。

结论：工具可用不代表模型一定选它。工具名、描述、系统提示词和用户提示词都会影响选择。

### 实验 2：观察一次响应中的多个独立调用

输入：

```text
Read docs/alpha.txt, docs/beta.txt, and docs/gamma.txt. They are independent,
so request all three read_file calls together if possible. Compare the owners.
```

理想现象是连续出现三次：

```text
> read_file
...
> read_file
...
> read_file
...
```

模型不保证一定把三次调用放在同一个响应中。后面的“调用批次日志”改动可以确定它们
究竟来自同一个模型响应，还是来自三个模型轮次。

无论模型怎样分轮，当前处理函数始终顺序执行。

### 实验 3：验证 `read_file` 的行数限制

先创建一个五行文件：

```text
one
two
three
four
five
```

可以直接要求 Agent：

```text
Use write_file to create five.txt with five lines: one, two, three, four, five.
Then use read_file with limit=2 and report the exact tool result.
```

`read_file` 的预期工具结果是：

```text
one
two
... (3 more lines)
```

验收重点：

- `limit` 是传给 `read_file` 的结构化整数参数；
- 省略的三行不会进入本次工具结果；
- 模型不能从这个工具结果直接知道被省略行的具体内容，除非它再次读取。

### 实验 4：写入会创建目录，也会覆盖

输入：

```text
Use write_file to create nested/config/app.txt containing version=1.
Read it back, then use write_file again to replace the whole file with version=2.
Read it one final time.
```

验收标准：

- 原本不存在的 `nested/config/` 自动创建；
- 第一次读取为 `version=1`；
- 第二次读取只剩 `version=2`；
- 文件里不应同时保留两个版本。

结论：`write_file` 是“完整写入”，不是“追加”。要求小范围修改时应优先使用
`edit_file`。

### 实验 5：精确编辑只替换第一次

输入：

```text
Use write_file to create colors.txt with exactly two lines, both `color=red`.
Then use edit_file once to replace `color=red` with `color=blue`.
Read the final file.
```

预期结果：

```text
color=blue
color=red
```

再输入：

```text
Use edit_file on colors.txt with old_text=`color = red` and new_text=`color=green`.
Do not use another tool until you report the edit_file result.
```

因为多了空格，预期工具结果是：

```text
Error: text not found in colors.txt
```

并且文件保持不变。

### 实验 6：验证文件工具的路径边界

先退出 Agent，在练习目录的上一级准备一个无敏感内容的文件。

Windows PowerShell：

```powershell
Set-Content -Path (Join-Path $env:TEMP "s02-outside.txt") -Encoding ascii -Value "outside"
```

macOS / Linux：

```bash
printf 'outside\n' > "$(dirname "$s02_lab")/s02-outside.txt"
```

重新启动 S02，然后输入：

```text
Use read_file, not bash, to read ../s02-outside.txt. Report the exact tool result.
```

预期：

```text
Error: Path escapes workspace: ../s02-outside.txt
```

然后输入：

```text
Use bash to run `cat ../s02-outside.txt`.
```

当前教学实现通常能读取并返回：

```text
outside
```

结论：`safe_path()` 保护的是调用它的文件处理函数，无法自动约束 Bash。不要在真实重要
目录中做这个实验。

### 实验 7：工具错误作为普通结果返回

输入：

```text
Use edit_file to change `enabled=false` to `enabled=true` in absent.cfg.
If the tool returns an error, create the file correctly with write_file and verify it.
```

预期过程：

1. `edit_file` 返回文件不存在错误；
2. 错误字符串进入 `tool_result`；
3. 模型根据错误选择 `write_file`；
4. 再用 `read_file` 验证；
5. 最终文件包含 `enabled=true`。

和 S01 一样，这里没有 Harness 级重试器。恢复动作仍然是模型决定的。

### 实验 8：验证“字符数”不等于“UTF-8 字节数”

输入：

```text
Use write_file to create chinese.txt containing exactly `你好`.
Report the write_file result, then use bash `wc -c chinese.txt` to check its byte size.
```

当前实现可能显示：

```text
Wrote 2 bytes to chinese.txt
```

而 UTF-8 文件的 `wc -c` 通常显示：

```text
6 chinese.txt
```

原因是 `len("你好") == 2` 统计 Unicode 字符，而 UTF-8 编码后占 6 字节。
这是返回消息的措辞问题，不代表文件内容写错。

## 11. 修改实验：让工具系统更易观察

建议复制实验文件：

Windows：

```powershell
Copy-Item "$courseRoot\s02_tool_use\code.py" "$courseRoot\s02_tool_use\code_experiment.py"
```

macOS / Linux：

```bash
cp "$course_root/s02_tool_use/code.py" "$course_root/s02_tool_use/code_experiment.py"
```

后续只修改并运行 `code_experiment.py`。

### 改动 A：显示工具输入

把原来的：

```python
print(f"\033[33m> {block.name}\033[0m")
```

临时改成：

```python
print(f"\033[33m> {block.name} {block.input}\033[0m")
```

再完成读取、写入和编辑实验。你应该能直接看到：

```text
> read_file {'path': 'docs/alpha.txt'}
> edit_file {'path': 'colors.txt', 'old_text': 'color=red', 'new_text': 'color=blue'}
```

这能帮助调试 schema 和参数名，但不建议原样用于生产环境：工具参数可能包含源码、用户数据
或其他敏感内容，日志系统需要脱敏和访问控制。

### 改动 B：标记同一模型响应中的调用批次

在处理工具前加入：

```python
tool_blocks = [
    block for block in response.content
    if block.type == "tool_use"
]
print(
    f"[batch] count={len(tool_blocks)}, "
    f"tools={[block.name for block in tool_blocks]}"
)
```

并把后面的循环改为：

```python
for block in tool_blocks:
```

再次执行“三个文件一起读取”的实验。

预期：

- 如果模型一次返回三个调用，会看到
  `[batch] count=3, tools=['read_file', 'read_file', 'read_file']`；
- 如果模型逐轮读取，会看到三次 `count=1`；
- 无论 `count` 是多少，批次内部仍由普通 `for` 顺序执行。

### 改动 C：让工具描述更有选择倾向

把 Bash 和文件工具的描述改得更明确：

```python
{"name": "bash",
 "description": "Run programs and shell commands. Prefer dedicated file tools for file access.",
 ...}

{"name": "read_file",
 "description": "Read a text file safely inside the workspace. Prefer this over bash cat.",
 ...}
```

然后用同一个提示词重复 3 次：

```text
Read docs/alpha.txt and tell me its owner.
```

记录修改前后模型选择 `read_file` 和 `bash` 的次数。预期趋势是修改后更偏向
`read_file`，但不能把概率性模型行为当成绝对保证。

### 改动 D：让 Glob 真正递归并保持稳定顺序

把：

```python
for match in g.glob(pattern, root_dir=WORKDIR):
```

改为：

```python
for match in sorted(g.glob(pattern, root_dir=WORKDIR, recursive=True)):
```

在练习目录创建：

```text
tree/
├── root.txt
└── level1/
    └── level2/
        └── deep.txt
```

要求 Agent 使用 `glob` 搜索 `tree/**/*.txt`。

预期：

- 修改前不应依赖它找到任意深度的文件；
- 修改后应同时匹配 `tree/root.txt` 和深层的 `tree/level1/level2/deep.txt`；
- 多次运行返回顺序稳定。

### 改动 E：收紧 Schema

当前 schema 只规定字段类型和必填字段。可以给 `read_file.limit` 增加下限，并禁止未知字段：

```python
"input_schema": {
    "type": "object",
    "properties": {
        "path": {"type": "string", "minLength": 1},
        "limit": {"type": "integer", "minimum": 1},
    },
    "required": ["path"],
    "additionalProperties": False,
}
```

预期效果：

- schema 更明确地告诉模型 `limit` 必须大于等于 1；
- 不应生成 `filename`、`max_lines` 等未定义字段；
- 具体供应商对工具 schema 的强制校验能力可能不同，因此 Harness 仍不应完全依赖模型端校验。

这引出下一个改动。

### 改动 F：隔离分发阶段的异常

当前处理函数内部大多捕获了文件错误，但 `handler(**block.input)` 本身仍可能因为未知参数
或函数签名不一致抛出 `TypeError`。把分发改成：

```python
handler = TOOL_HANDLERS.get(block.name)
try:
    if handler is None:
        output = f"Error: unknown tool {block.name}"
    else:
        output = handler(**block.input)
except Exception as error:
    output = f"Error executing {block.name}: {error}"
```

预期：

- 单个工具参数异常会变成一个可回填给模型的错误结果；
- 整个 Agent 进程不会因为这一个 handler 崩溃；
- 模型有机会修正参数后重试。

这只是最小异常边界。生产系统还应记录异常类型、调用 ID、耗时和安全审计信息。

## 12. 扩展实验：亲手添加 `file_info` 工具

这个实验验证你是否真正掌握“实现 + schema + 注册”。

### 第 1 步：实现处理函数

在工具实现区域加入：

```python
import json


def run_file_info(path: str) -> str:
    try:
        file_path = safe_path(path)
        stat = file_path.stat()
        return json.dumps({
            "path": path,
            "kind": "directory" if file_path.is_dir() else "file",
            "size_bytes": stat.st_size,
        }, ensure_ascii=False)
    except Exception as error:
        return f"Error: {error}"
```

### 第 2 步：给模型定义 schema

在 `TOOLS` 数组加入：

```python
{
    "name": "file_info",
    "description": "Get the type and byte size of a path inside the workspace.",
    "input_schema": {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
        },
        "required": ["path"],
        "additionalProperties": False,
    },
},
```

### 第 3 步：注册 handler

在 `TOOL_HANDLERS` 加入：

```python
"file_info": run_file_info,
```

### 第 4 步：运行验证

输入：

```text
Use file_info to report the exact byte size and kind of chinese.txt.
```

预期：

- 控制台出现 `> file_info`；
- 结果中的 `kind` 为 `file`；
- 如果 `chinese.txt` 仍然只包含 UTF-8 的 `你好`，`size_bytes` 通常为 `6`；
- Agent Loop 本身不需要增加 `if block.name == "file_info"`。

故意只做其中两步，再观察失败：

| 漏掉的部分 | 可能现象 |
|---|---|
| 没有 handler 实现 | 注册时出现 `NameError` |
| 没有 schema | 模型不知道工具存在，通常不会调用 |
| 没有注册映射 | 模型会调用，但 Harness 返回 unknown tool |
| schema 参数名和函数不同 | 分发时出现参数错误 |

## 13. 高阶扩展：保守地并发独立只读工具

这一节是扩展，不是 S02 原始代码已有能力。

先建立原则：

- 只有互相独立的调用才能并发；
- `read_file` 和 `glob` 暂时视为并发安全；
- `write_file`、`edit_file` 暂时保持串行；
- Bash 不分析具体命令时，保守地保持串行；
- 返回给模型的结果顺序必须与工具调用顺序一致。

可以先抽取单次执行函数：

```python
def execute_tool(block):
    handler = TOOL_HANDLERS.get(block.name)
    try:
        output = (
            handler(**block.input)
            if handler
            else f"Error: unknown tool {block.name}"
        )
    except Exception as error:
        output = f"Error executing {block.name}: {error}"
    return {
        "type": "tool_result",
        "tool_use_id": block.id,
        "content": output,
    }
```

再对“整个批次都是只读”的情况做最小并发：

```python
from concurrent.futures import ThreadPoolExecutor

CONCURRENCY_SAFE = {"read_file", "glob"}

tool_blocks = [
    block for block in response.content
    if block.type == "tool_use"
]

if tool_blocks and all(
    block.name in CONCURRENCY_SAFE
    for block in tool_blocks
):
    worker_count = min(4, len(tool_blocks))
    with ThreadPoolExecutor(max_workers=worker_count) as pool:
        results = list(pool.map(execute_tool, tool_blocks))
else:
    results = [execute_tool(block) for block in tool_blocks]
```

`pool.map()` 会按输入顺序产生结果，因此即使完成时间不同，返回的 `results` 顺序仍和
`response.content` 一致。

为了观察耗时，可以临时在 `run_read()` 开头加入：

```python
import time
time.sleep(1)
```

再让模型在一个响应中读取三个独立文件，并用 `time.perf_counter()` 记录整个工具批次耗时。

预期趋势：

- 原始串行版三个读取约需 3 秒；
- 最小并发版约需 1 秒；
- 如果模型把三个读取分成三个模型响应，就无法在同一批次并发；
- 含 `write_file` 或 `edit_file` 的批次会保守地全部串行。

这个最小版本没有把混合调用分割为多个连续批次。例如：

```text
[read A, read B, edit C, read D]
```

更完整的调度应分为：

```text
并发批次 [read A, read B]
→ 串行批次 [edit C]
→ 并发批次 [read D]
```

并严格等待前一批结束后再开始下一批。不要简单地把所有读取放一组、所有写入放另一组，
那样会破坏原始因果顺序。

## 14. 本课综合挑战：配置升级与代码盘点

退出 Agent，在临时练习目录准备：

```text
src/
├── config.py
└── app.py
```

Windows PowerShell：

```powershell
New-Item -ItemType Directory -Force -Path .\src | Out-Null
Set-Content -Path .\src\config.py -Encoding ascii -Value 'MODE = "dev"'
Set-Content -Path .\src\app.py -Encoding ascii -Value @(
    "from config import MODE",
    "print(f'mode={MODE}')"
)
```

macOS / Linux：

```bash
mkdir -p src
printf 'MODE = "dev"\n' > src/config.py
printf 'from config import MODE\nprint(f"mode={MODE}")\n' > src/app.py
```

重新启动 S02，输入：

```text
Complete this task using specialized tools:
1. Use glob to find Python files directly under src.
2. Read every matched file with read_file.
3. Use edit_file to change MODE from "dev" to "test".
4. Use write_file to create REPORT.md listing the discovered files and the final mode.
5. Use bash only to run `python src/app.py`.
6. Read REPORT.md to verify it before finishing.
```

验收标准：

- `glob` 找到 `src/app.py` 和 `src/config.py`；
- 两个文件都经过 `read_file`；
- `edit_file` 精确修改 `MODE = "dev"`；
- `src/config.py` 最终包含 `MODE = "test"`；
- Bash 输出 `mode=test`；
- `REPORT.md` 同时列出两个 Python 文件和最终模式 `test`；
- Agent 最后用 `read_file` 验证了报告；
- 日志中至少出现 `glob`、`read_file`、`edit_file`、`write_file` 和 `bash`。

如果模型跳过某个指定工具，不代表分发代码坏了。可以追问：

```text
You skipped one of the required tool types. Inspect the tool log, identify
which one, use it for the intended verification, and then finish.
```

## 15. 常见问题与定位

### Agent 总是使用 Bash，不用专用工具

检查顺序：

1. 确认运行的是 `s02_tool_use/code.py`，不是 S01；
2. 启动信息是否写着“加了 4 个工具”；
3. 提示词明确指定一次 `read_file`；
4. 使用“改动 C”强化工具描述；
5. 确认所用模型和供应商完整支持工具调用。

### `write_file` 把旧内容清空了

这是设计行为。`write_file` 完整覆盖；小范围替换使用 `edit_file`。追加内容时，模型可以先读
再写完整内容，但必须考虑中间状态变化和并发冲突。

### `edit_file` 明明看起来一样却找不到

检查：

- 大小写；
- 空格和缩进；
- `\n` 与 `\r\n`；
- 引号类型；
- 文本是否已经被上一次实验改过。

最稳妥的流程是先 `read_file`，复制工具结果中的精确片段作为 `old_text`。

### `glob` 没找到深层文件

当前实现没有 `recursive=True`。使用明确的单层模式，或完成“改动 D”。

### 多个工具看起来还是一个个执行

这是正确现象。S02 原始实现支持多个工具调用，但使用顺序 `for` 循环。只有完成高阶扩展后，
满足条件的只读批次才会真正并发。

### 路径越界被挡住，但 Bash 又能读取

这正是本课要暴露的边界：文件工具和 Bash 的安全策略不一致。S03 会把所有工具调用放到
统一权限管线之前。

### Handler 抛出 `unexpected keyword argument`

schema 字段名和处理函数参数不一致，或者模型返回了未禁止的额外字段。同步二者，并考虑：

- `additionalProperties: False`；
- 分发异常捕获；
- Harness 侧的输入验证。

## 16. 设计层面的延伸思考

### 工具描述也是 Agent 行为的一部分

模型不是根据 Python 函数源码选择工具，它主要看到工具名、description 和 schema。
描述含糊会直接造成行为含糊。因此工具设计不仅是后端接口设计，也是提示设计。

### Schema 只描述结构，不自动保证业务安全

`path` 是字符串，并不代表它一定在工作区；`command` 是字符串，也不代表命令安全。
schema 解决“参数长什么样”，`safe_path` 和权限管线解决“这个值能不能执行”。

### Handler 返回错误还是抛异常，是一种协议选择

当前文件工具把大多数错误转成字符串，让模型有机会恢复。优点是循环不中断；缺点是调用方
必须区分成功文本和以 `Error:` 开头的失败文本。生产系统通常会使用结构化状态和错误类型。

### 多工具调用先解决正确性，再解决速度

顺序执行容易理解，也保留原始顺序。并发能降低延迟，但会引入：

- 写冲突；
- 读写竞态；
- 日志交错；
- 取消传播；
- 结果排序；
- 并发上限；
- 单个调用失败时怎样处理其他调用。

所以生产级并发不是把 `for` 换成线程池这么简单。

### 专用文件工具仍有更多可改进空间

当前实现还没有：

- 显式 UTF-8 编码；
- 文件大小限制；
- 二进制文件检测；
- 行号和范围读取；
- 原子写入；
- 编辑前版本校验；
- 多处匹配歧义检查；
- 结果持久化与大输出预览；
- 统一的结构化成功/错误返回。

这些都是可以从 S02 继续延伸的 Harness 工程问题。

## 17. 结课自测

不看代码回答：

1. 工具 schema 和 handler 分别给谁使用？
2. 为什么 `handler(**block.input)` 要求字段名和函数参数一致？
3. 添加新工具时必须改哪两个注册位置？
4. 为什么添加 `file_info` 不需要修改 Agent Loop 的分支逻辑？
5. `write_file` 和 `edit_file` 的语义差异是什么？
6. `edit_file` 找不到 `old_text` 时为什么不应该继续写入？
7. `safe_path()` 为什么要在检查前调用 `resolve()`？
8. 为什么文件工具拒绝 `../x` 不代表整个 Agent 不能访问 `../x`？
9. 一次模型响应返回三个 `tool_use` 是否代表三个 handler 并发？
10. 哪些工具可以初步视为并发安全，为什么这还不够？
11. 为什么不应把所有读取移到所有写入之前执行？
12. JSON Schema 能否代替路径安全和权限检查？

完成综合挑战、`file_info` 扩展，并正确回答至少 10 题，就可以认为掌握了 S02。

## 18. 完成本课后的状态

你现在拥有：

```text
S01 的 Agent Loop
  + 5 个结构化工具 schema
  + 5 个处理函数
  + TOOL_HANDLERS 分发映射
  + 文件工具的工作区路径保护
  + 多工具调用的顺序处理
  = 一个可扩展的工具系统
```

但所有工具执行仍缺少统一的安全决策：

```text
模型请求工具
  → 当前 S02：直接分发执行
  → 下一课 S03：先判断 allow / deny / ask，再决定是否执行
```

这就是 S03 Permission 要解决的问题。
