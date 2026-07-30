# S03 实操教学指南：在工具执行前建立权限闸门

> 对应课程：[s03_permission](../../s03_permission/)  
> 核心代码：[code.py](../../s03_permission/code.py)  
> 前置课程：[S02 Tool Use](s02-tool-use.md)  
> 建议用时：90–120 分钟  
> 本课产物：一个具有 deny、ask、allow 三类决策的工具执行管线

## 1. 学完这一课，你应该能做到什么

完成 S03 后，你应该能够：

1. 解释为什么不能把安全责任交给模型自己；
2. 区分硬拒绝、规则命中、用户审批和默认放行；
3. 说明权限检查为什么必须位于 tool call 和 handler 之间；
4. 根据工具名与参数预测 `check_permission()` 的结果；
5. 理解路径安全校验和用户授权不是同一件事；
6. 分别验证直接放行、询问后拒绝、询问后允许、硬拒绝四条路径；
7. 解释为什么被拒绝的调用仍必须返回 `tool_result`；
8. 识别简单字符串 deny list 的误报与漏报；
9. 为权限决策增加原因、审计记录和可测试的审批接口；
10. 指出生产权限系统还需要哪些策略来源与生命周期控制。

本课最重要的一句话是：

> 模型可以提出动作，但是否允许动作接触真实世界，必须由 Harness 决定。

## 2. 从 S02 到 S03：只在执行前插一道门

S02 的通用分发是：

```text
模型发出 tool_use
  → 根据工具名找到 handler
  → 立即执行
  → 回填 tool_result
```

S03 变成：

```text
模型发出 tool_use
  → check_permission(block)
      ├─ 硬拒绝：不执行
      ├─ 规则命中：询问用户
      │   ├─ 用户允许：执行
      │   └─ 用户拒绝：不执行
      └─ 无规则命中：直接执行
  → 无论执行与否，都回填 tool_result
```

Agent Loop 的模型调用、停止判断和消息结构仍然没有改变。新增机制位于工具调用与
`TOOL_HANDLERS` 分发之间：

```python
if not check_permission(block):
    results.append({
        "type": "tool_result",
        "tool_use_id": block.id,
        "content": "Permission denied.",
    })
    continue
```

这是一个重要的架构边界：

```text
模型层：我想做什么
权限层：这次是否可以做
工具层：具体怎样做
```

## 3. 不要混淆三类安全机制

### 3.1 Schema 校验

回答“参数长什么样”：

```text
command 是否是字符串？
path 是否存在？
limit 是否是整数？
```

Schema 本身不能判断 `command="rm file"` 是否应获准。

### 3.2 路径或输入校验

回答“这个输入在工具语义上是否有效”：

```text
路径是否位于工作区？
文件是否过大？
old_text 是否唯一匹配？
```

S02 的 `safe_path()` 属于这一层。

### 3.3 权限决策

回答“即使动作有效，这次是否授权执行”：

```text
工作区外读取是否需要用户同意？
删除文件是否需要确认？
某条命令是否永远禁止？
```

S03 的重点是这一层。生产系统通常会同时拥有三层，而不是三选一。

## 4. S03 的三道闸门

| 顺序 | 闸门 | 当前实现 | 命中后的行为 |
|---|---|---|---|
| 1 | 硬拒绝 | Bash 命令包含 deny list 字符串 | 立即拒绝，不能通过审批覆盖 |
| 2 | 规则匹配 | 工作区外文件访问，或部分危险 Bash 片段 | 进入用户审批 |
| 3 | 用户审批 | 从标准输入读取 `y/yes` | 允许执行；其他输入默认拒绝 |

如果前两道都没有命中，调用直接通过。

顺序很重要。假设命令同时匹配硬拒绝和询问规则：

```text
rm -rf /
```

它既包含硬拒绝项 `rm -rf /`，也包含询问规则的 `rm `。因为硬拒绝先执行，用户不会得到
通过输入 `y` 覆盖它的机会。

## 5. 本课的真实安全边界

S03 是教学权限模型，不是生产沙箱。当前代码有这些边界：

- deny list 只检查 Bash；
- deny list 使用大小写敏感的简单子字符串匹配；
- Bash 的询问规则也使用简单子字符串；
- `glob` 仍把结果限制在工作区；
- `read_file`、`write_file`、`edit_file` 在 S03 中不再自行阻止越界；
- 文件工具访问工作区外会触发询问，用户允许后真的执行；
- 没有“本次允许”和“以后都允许”的区分；
- 没有持久化审计日志；
- 拒绝结果只告诉模型 `Permission denied.`，没有包含具体原因。

尤其要注意 S02 与 S03 的差异：

| 行为 | S02 | S03 |
|---|---|---|
| 文件路径越界 | `safe_path()` 直接报错 | 规则命中并询问用户 |
| 用户能否批准越界 | 不能 | 能 |
| Bash 工作区边界 | 没有 | 仍没有 |

这种变化是为了演示审批语义。真实系统更常见的做法是：先做不可绕过的输入安全校验，
再对校验通过但有风险的动作做权限决策。

## 6. 准备隔离实验目录

### 6.1 Windows PowerShell

在仓库根目录运行：

```powershell
$courseRoot = (Resolve-Path .).Path
$s03Lab = Join-Path $env:TEMP "learn-claude-code-s03"
New-Item -ItemType Directory -Force -Path $s03Lab | Out-Null
Set-Location -LiteralPath $s03Lab
$env:PYTHONUTF8 = "1"
Set-Content -Path .\keep.txt -Encoding ascii -Value "keep"
Set-Content -Path .\remove-me.txt -Encoding ascii -Value "temporary"
Set-Content -Path (Join-Path $env:TEMP "s03-outside.txt") -Encoding ascii -Value "outside"
& "$courseRoot\.venv\Scripts\python.exe" "$courseRoot\s03_permission\code.py"
```

### 6.2 macOS / Linux

在仓库根目录运行：

```bash
course_root="$(pwd)"
s03_lab="$(mktemp -d)"
cd "$s03_lab"
printf 'keep\n' > keep.txt
printf 'temporary\n' > remove-me.txt
printf 'outside\n' > "$(dirname "$s03_lab")/s03-outside.txt"
"$course_root/.venv/bin/python" "$course_root/s03_permission/code.py"
```

启动后应看到：

```text
s03: Permission
输入问题，回车发送。输入 q 退出。

s03 >>
```

所有实验都只操作这些无价值的练习文件。不要把真实删除命令放到重要目录测试。

## 7. 第一次阅读代码：按七个位置理解

### 位置 A：系统提示词只负责提醒模型

```python
SYSTEM = (
    f"You are a coding agent at {WORKDIR}. "
    "All destructive operations require user approval."
)
```

系统提示词告诉模型破坏性操作需要批准，但它不是权限边界。模型可能判断错误、忽略提示，
或生成超出预期的调用。真正的边界是后面的 Python 检查。

### 位置 B：S03 文件 handler 不再调用 `safe_path()`

例如：

```python
file_path = (WORKDIR / path).resolve()
file_path.write_text(content)
```

这里只解析路径，没有 `is_relative_to(WORKDIR)` 拒绝逻辑。是否允许越界由
`PERMISSION_RULES` 和用户输入决定。

如果你只读课程说明而不读实际 handler，很容易错误地以为文件工具仍然拥有 S02 的硬边界。

### 位置 C：硬拒绝表

```python
DENY_LIST = [
    "rm -rf /",
    "sudo",
    "shutdown",
    "reboot",
    "mkfs",
    "dd if=",
    "> /dev/sda",
]
```

检查函数返回具体原因或 `None`：

```python
def check_deny_list(command: str) -> str | None:
    for pattern in DENY_LIST:
        if pattern in command:
            return f"Blocked: '{pattern}' is on the deny list"
    return None
```

它不解析 Shell 语法，也不判断字符串出现在命令、参数、注释还是输出文本中。

### 位置 D：询问规则

第一条规则覆盖三个文件工具：

```python
{
    "tools": ["read_file", "write_file", "edit_file"],
    "check": lambda args: not (
        WORKDIR / args.get("path", "")
    ).resolve().is_relative_to(WORKDIR),
    "message": "Writing outside workspace",
}
```

代码信息写的是 `Writing outside workspace`，但规则也会匹配 `read_file` 和
`edit_file`。这是提示文本不够精确，不影响规则本身执行。

第二条覆盖 Bash：

```python
{
    "tools": ["bash"],
    "check": lambda args: any(
        keyword in args.get("command", "")
        for keyword in ["rm ", "> /etc/", "chmod 777"]
    ),
    "message": "Potentially destructive command",
}
```

`check_rules()` 按列表顺序查找，并在第一个匹配处返回：

```python
for rule in PERMISSION_RULES:
    if tool_name in rule["tools"] and rule["check"](args):
        return rule["message"]
```

未来增加重叠规则时，顺序会影响用户看到的原因。

### 位置 E：用户审批默认拒绝

```python
choice = input("   Allow? [y/N] ").strip().lower()
return "allow" if choice in ("y", "yes") else "deny"
```

下列输入允许：

```text
y
Y
yes
YES
```

下列输入都拒绝：

```text
直接回车
n
no
任意其他文字
```

`[y/N]` 中的大写 `N` 表示默认值是拒绝。这是安全交互的常见设计。

### 位置 F：管线顺序

```python
def check_permission(block) -> bool:
    if block.name == "bash":
        reason = check_deny_list(...)
        if reason:
            return False

    reason = check_rules(block.name, block.input)
    if reason:
        decision = ask_user(...)
        if decision == "deny":
            return False

    return True
```

返回值只有布尔值：

- `True`：执行 handler；
- `False`：不执行。

具体原因只打印在本地终端，没有被这个函数返回给 Agent Loop。

### 位置 G：拒绝也要完成消息协议

```python
if not check_permission(block):
    results.append({
        "type": "tool_result",
        "tool_use_id": block.id,
        "content": "Permission denied.",
    })
    continue
```

模型已经发出一个带 ID 的 `tool_use`。即使 Harness 不执行，也必须用相同 ID 回填结果。
否则下一轮消息中会出现“只有工具请求，没有工具结果”的不完整协议状态。

`continue` 只跳过当前工具块。若同一模型响应中还有其他工具调用，循环会继续逐个检查。

## 8. 权限决策速查表

假设 `WORKDIR` 是临时练习目录：

| 工具调用 | 硬拒绝 | 规则命中 | 用户输入 | 最终结果 |
|---|---:|---:|---|---|
| `read_file("keep.txt")` | 否 | 否 | 不询问 | 执行 |
| `write_file("new.txt", ...)` | 否 | 否 | 不询问 | 执行 |
| `glob("*.txt")` | 否 | 否 | 不询问 | 执行 |
| `bash("pwd")` | 否 | 否 | 不询问 | 执行 |
| `bash("rm remove-me.txt")` | 否 | 是 | 回车或 `n` | 拒绝 |
| `bash("rm remove-me.txt")` | 否 | 是 | `y` | 执行 |
| `read_file("../s03-outside.txt")` | 否 | 是 | `n` | 拒绝 |
| `read_file("../s03-outside.txt")` | 否 | 是 | `y` | 执行 |
| Bash 参数中包含 `sudo` | 是 | 不再检查 | 不询问 | 拒绝 |
| `bash("echo hello")` | 否 | 否 | 不询问 | 执行 |

## 9. 手工走一遍拒绝后的消息

用户要求删除 `remove-me.txt`。模型返回：

```text
tool_use:
  id: tool_123
  name: bash
  input:
    command: rm remove-me.txt
```

权限管线发现 `rm `，询问用户。用户输入 `n`。

Harness 不调用 `run_bash()`，但追加：

```text
tool_result:
  tool_use_id: tool_123
  content: Permission denied.
```

下一轮模型可能：

- 告诉用户删除被拒绝；
- 询问是否采用其他方法；
- 放弃删除并继续完成任务中的安全部分；
- 再次提出一个不同工具调用。

权限拒绝的是这一次具体调用，不是自动终止整个 Agent Loop，也不等于模型不能继续思考。

## 10. 最小成功路径：完整验证四类结果

### 10.1 默认放行

输入：

```text
Use write_file to create allowed.txt containing `allowed`, then read it.
```

预期：

- 不出现审批提示；
- 出现 `> write_file` 和 `> read_file`；
- `allowed.txt` 创建成功；
- 最终回答确认内容为 `allowed`。

### 10.2 询问后拒绝

输入：

```text
Use bash to delete remove-me.txt.
```

出现：

```text
⚠  Potentially destructive command
   Tool: bash({'command': 'rm remove-me.txt'})
   Allow? [y/N]
```

直接回车或输入 `n`。

验收标准：

- Bash handler 没有执行；
- `remove-me.txt` 仍然存在；
- 模型收到 `Permission denied.`；
- Agent 最终说明操作没有完成，或提出下一步。

### 10.3 询问后允许

如果上一步后模型已经结束，再次输入相同任务：

```text
Use bash to delete remove-me.txt.
```

审批时输入 `y`。

验收标准：

- 命令执行；
- 工具结果通常为 `(no output)`；
- `remove-me.txt` 不再存在；
- 模型最终确认删除完成。

### 10.4 硬拒绝

使用一条无害但包含 deny list 文本的命令：

```text
Use bash to run exactly `echo sudo`. Do not use another command.
```

预期：

```text
⛔ Blocked: 'sudo' is on the deny list
```

并且：

- 不出现 `Allow?`；
- 命令没有执行，所以不会打印 Bash 的 `sudo`；
- 模型收到 `Permission denied.`。

这同时证明硬拒绝优先级高于用户审批，也暴露了字符串匹配会误伤无害命令。

## 11. 七个深入实验

### 实验 1：默认拒绝是否生效

再次请求删除一个练习文件，在 `Allow? [y/N]` 处只按回车。

预期和输入 `n` 完全相同。默认拒绝可以避免用户误触回车导致危险动作执行。

### 实验 2：工作区外读取先拒绝、再允许

输入：

```text
Use read_file to read ../s03-outside.txt and report its content.
```

第一次输入 `n`：

- 工具不执行；
- 模型看不到外部文件内容；
- 本地终端显示原因 `Writing outside workspace`。

再次请求并输入 `y`：

- `run_read()` 真的读取工作区外文件；
- 工具结果是 `outside`；
- 模型可以报告内容。

结论：S03 的这条规则是“需授权”，不是“不可越过的安全边界”。

### 实验 3：Bash 的普通只读命令直接通过

输入：

```text
Use bash to run `pwd` and `ls`, then report the results.
```

预期不询问。当前规则不做完整的只读分析，只是没有在这些字符串中命中特定风险片段。
“没有命中”不等于已经证明命令安全。

### 实验 4：字符串匹配的误报

输入：

```text
Use bash to run exactly `echo "rm file"`. Do not run rm itself.
```

虽然命令只输出文字，因为字符串里含有 `rm `，仍会出现审批。

输入 `n`，验证没有 Bash 输出。这说明规则匹配的是字符，不是 Shell 的真实语义。

### 实验 5：大小写造成不同结果

依次尝试两条无害命令：

```text
Use bash to run exactly `echo sudo`.
```

```text
Use bash to run exactly `echo SUDO`.
```

当前检查大小写敏感：

- 第一条硬拒绝；
- 第二条通常直接执行并输出 `SUDO`。

不要把这个实验理解成绕过真实安全系统的方法。它只是证明简单 deny list 无法承担生产安全。

### 实验 6：同一批工具逐个决策

输入：

```text
In one response if possible, request read_file for keep.txt and bash to remove
allowed.txt. The read is independent from the delete.
```

可能看到：

1. `read_file` 直接执行；
2. `bash rm ...` 暂停询问；
3. 选择拒绝后，读取结果和拒绝结果一起回填给模型。

如果模型拆成多个响应也属于正常模型行为。关键是每个 `tool_use` 独立经过权限管线。

### 实验 7：拒绝动作不等于终止任务

输入：

```text
Delete keep.txt. If permission is denied, do not try another deletion method;
instead read the file and report its content.
```

删除审批时输入 `n`。

预期：

- 删除被拒绝；
- 模型读取 `Permission denied.` 后可继续请求 `read_file`；
- `keep.txt` 保留；
- 最终报告内容 `keep`。

这展示权限反馈如何影响模型的下一步策略。

## 12. 修改实验：提高可解释性与可测试性

先复制实验文件。

Windows：

```powershell
Copy-Item "$courseRoot\s03_permission\code.py" "$courseRoot\s03_permission\code_experiment.py"
```

macOS / Linux：

```bash
cp "$course_root/s03_permission/code.py" "$course_root/s03_permission/code_experiment.py"
```

### 改动 A：返回结构化权限决策

当前 `bool` 丢失了原因。加入：

```python
from dataclasses import dataclass
from typing import Literal


@dataclass
class PermissionDecision:
    behavior: Literal["allow", "deny"]
    reason: str
```

把 `check_permission()` 的返回值改成：

```python
def check_permission(block) -> PermissionDecision:
    if block.name == "bash":
        reason = check_deny_list(block.input.get("command", ""))
        if reason:
            return PermissionDecision("deny", reason)

    reason = check_rules(block.name, block.input)
    if reason:
        decision = ask_user(block.name, block.input, reason)
        if decision == "deny":
            return PermissionDecision("deny", reason)
        return PermissionDecision("allow", f"User approved: {reason}")

    return PermissionDecision("allow", "No permission rule matched")
```

循环中使用：

```python
decision = check_permission(block)
if decision.behavior == "deny":
    results.append({
        "type": "tool_result",
        "tool_use_id": block.id,
        "content": f"Permission denied: {decision.reason}",
        "is_error": True,
    })
    continue
```

预期：

- 模型知道是硬拒绝、工作区外访问还是用户拒绝；
- `is_error: True` 明确标记工具未成功；
- 后续恢复策略可以基于原因，而不是只看到统一字符串。

### 改动 B：把审批函数作为依赖传入

直接调用 `input()` 很难自动测试。改为：

```python
def check_permission(block, approve=ask_user) -> PermissionDecision:
    ...
    decision = approve(block.name, block.input, reason)
    ...
```

测试时可以注入：

```python
def always_allow(tool_name, args, reason):
    return "allow"


def always_deny(tool_name, args, reason):
    return "deny"
```

预期：

- 不需要真实键盘输入就能覆盖 allow 与 deny；
- 权限规则和 UI 解耦；
- 未来可替换成图形对话框、远程审批或父 Agent 权限冒泡。

### 改动 C：记录 JSONL 审计事件

定义：

```python
import json
from datetime import datetime, timezone


AUDIT_FILE = WORKDIR / "permission-audit.jsonl"


def audit_permission(block, decision: PermissionDecision) -> None:
    event = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "tool_use_id": block.id,
        "tool": block.name,
        "behavior": decision.behavior,
        "reason": decision.reason,
    }
    with AUDIT_FILE.open("a", encoding="utf-8") as file:
        file.write(json.dumps(event, ensure_ascii=False) + "\n")
```

在每次决策后调用。完成几个实验后检查文件。

验收标准：

- 每次工具请求都有一行事件；
- allow 与 deny 都被记录；
- 记录包含调用 ID，能和消息历史关联；
- 不直接记录完整 `content` 或 API Key。

生产审计还需要防篡改、轮转、访问控制和保留期限；本实验只验证 append-only 思路。

### 改动 D：审批界面只显示必要摘要

当前代码直接打印整个 `args`。`write_file` 的 `content` 可能很长或敏感。可以增加：

```python
def summarize_args(tool_name: str, args: dict) -> dict:
    summary = dict(args)
    if tool_name == "write_file" and "content" in summary:
        content = str(summary["content"])
        summary["content"] = f"<{len(content)} chars>"
    return summary
```

在 `ask_user()` 中显示：

```python
print(f"   Tool: {tool_name}({summarize_args(tool_name, args)})")
```

预期：

- 用户仍能看到目标路径和内容长度；
- 终端不再完整显示写入内容；
- 审批信息足够判断风险，但减少数据泄露。

### 改动 E：修正路径规则提示

把：

```python
"message": "Writing outside workspace"
```

改成：

```python
"message": "File access outside workspace"
```

然后分别触发 `read_file` 与 `write_file` 越界。

预期两者都显示语义正确的统一提示。这是一个很小的改动，却体现权限 UI 需要准确描述
“将要发生什么”。

## 13. 扩展实验：编写权限决策矩阵测试

有了可注入审批函数后，可以为纯权限逻辑建立表格测试：

```python
from types import SimpleNamespace


def block(name: str, **arguments):
    return SimpleNamespace(
        id="test-id",
        name=name,
        input=arguments,
    )


cases = [
    (block("bash", command="pwd"), "allow"),
    (block("bash", command="echo sudo"), "deny"),
    (block("bash", command="rm demo.txt"), "ask"),
    (block("read_file", path="keep.txt"), "allow"),
    (block("read_file", path="../outside.txt"), "ask"),
]
```

为了直接区分 `ask`，可以让规则评估先返回三态，而不是在 `check_permission()` 内立即读取
用户输入：

```text
evaluate_policy(block) → allow / deny / ask
resolve_approval(ask, approver) → allow / deny
```

这样测试分为两层：

1. 策略是否把调用正确分类；
2. 审批结果是否正确解析。

这比用一堆模拟输入测试一个混合函数更清晰，也是生产权限系统常用的拆分方式。

## 14. 扩展实验：加入“本次允许”缓存

反复读取同一个工作区外测试文件时，每次询问会影响体验。可以建立只在当前进程有效的
精确调用缓存：

```python
SESSION_ALLOW = set()


def permission_key(tool_name: str, args: dict) -> str:
    import json
    return f"{tool_name}:{json.dumps(args, sort_keys=True, ensure_ascii=False)}"
```

用户批准后加入：

```python
SESSION_ALLOW.add(permission_key(block.name, block.input))
```

规则命中后先检查：

```python
if permission_key(block.name, block.input) in SESSION_ALLOW:
    return True
```

只缓存完整工具名和完整参数，避免把：

```text
允许读取 ../s03-outside.txt
```

错误扩大成：

```text
允许所有工作区外读取
```

实验结束后退出进程，缓存应消失。不要把“允许一次”自动持久化为长期权限。

## 15. 为什么不能只改进 deny list

你可能想到：

- 全部转小写；
- 去掉多余空格；
- 增加更多危险关键词；
- 使用正则表达式；
- 解析 `&&`、管道和重定向。

这些能减少部分误报和漏报，但 Shell 还包含变量展开、命令替换、别名、脚本调用、编码、
符号链接和间接执行。无限扩展字符串规则仍很难证明命令安全。

更可靠的方向包括：

- 用专用结构化工具代替自由 Bash；
- 根据工具与结构化参数授权；
- 在受限容器或操作系统沙箱内执行；
- 对 Bash 使用保守的 allow/ask 策略；
- 将不可恢复的动作设计成两阶段操作；
- 让审批界面展示解析后的实际影响范围。

S03 的 deny list 应被理解为权限管线的最小教学入口，不是安全方案终点。

## 16. 本课综合挑战：安全清理练习目录

退出 Agent，重新准备：

```text
keep.txt
remove-me.txt
```

启动 S03 后输入：

```text
Perform a cautious cleanup:
1. Use glob and read_file to inspect all .txt files.
2. Use write_file to create INVENTORY.md describing which file should stay
   and which file is temporary.
3. Ask through the normal permission system before deleting remove-me.txt.
4. After deletion, use glob to verify the final .txt files.
5. Never delete keep.txt.
```

第一次出现删除审批时输入 `n`。

观察：

- 盘点和写报告不需要审批；
- 删除被拒绝；
- `remove-me.txt` 保留；
- Agent 应基于拒绝结果说明清理未完成。

再次输入：

```text
Retry only the previously denied deletion of remove-me.txt, then verify.
```

这次输入 `y`。

最终验收：

- `keep.txt` 始终存在；
- `remove-me.txt` 最终被删除；
- `INVENTORY.md` 存在；
- 只读和工作区内写入直接通过；
- 第一次删除产生拒绝 tool result；
- 第二次删除经过明确批准才执行；
- 最终 Glob 结果不再包含 `remove-me.txt`。

## 17. 常见问题与定位

### 程序看起来卡住了

先看终端是否停在：

```text
Allow? [y/N]
```

这不是死锁，而是在等待人工审批。输入 `y`、`n` 或直接回车。

### 我输入了 `q`，程序却没有退出

如果此时处于审批提示，`q` 会被当成审批答案，因此表示拒绝；只有回到 `s03 >>` 后输入
`q` 才会退出外层 REPL。

### 工作区外读取被允许了

这是 S03 当前设计。文件 handler 没有 S02 的 `safe_path()` 硬拒绝；规则只要求审批。
如果需要永远禁止，应在输入校验层直接拒绝，或把规则改成硬 deny。

### 拒绝后模型又提出其他方法

权限拒绝针对一次工具调用。模型仍能继续决策。提示词可以要求“若被拒绝，不要绕过”，
但真正防止绕过需要所有能力经过统一权限检查，而不能依赖模型自律。

### 没出现审批

检查模型实际工具和参数：

- 它可能用了 `write_file` 而不是 Bash；
- Bash 命令可能不含当前规则的精确小写字符串；
- 文件路径解析后可能仍在工作区；
- 模型可能只给了文字建议，没有发工具调用。

给工具日志临时加入 `block.input` 可以帮助定位。

### 输入 `y` 后工具仍报错

权限允许只代表“可以尝试执行”，不保证动作成功。文件可能不存在、路径可能无权限、
命令可能语法错误。权限结果和工具执行结果是两个阶段。

## 18. 设计层面的延伸思考

### 权限规则应该统一包住所有工具

如果每个 handler 自己决定是否询问，新工具很容易忘记检查。把权限放在统一分发入口，
可以让未来加入的工具默认经过同一管线。

### 硬拒绝应不可被低优先级授权覆盖

企业策略、操作系统边界等通常具有更高优先级。用户会话内的“允许”不应覆盖组织级 deny。
S03 用固定顺序表达了这个原则的最小版本。

### 审批必须展示足够准确的信息

只显示“危险操作”不够，用户需要知道：

- 哪个工具；
- 哪个目标；
- 将读、写、删除还是执行；
- 影响范围；
- 是允许一次、允许本会话还是永久允许。

同时又不能把敏感内容原样写入日志。这是安全与可用性的平衡。

### 拒绝也是 Agent 可以利用的观察结果

权限系统不只是挡住动作，还应帮助模型选择安全替代方案。结构化拒绝原因比统一的
`Permission denied.` 更有价值。

### 自动审批必须比人工审批更保守

未来可以用规则或分类器自动放行低风险动作，但自动系统误判时没有人类最后确认。
应有明确白名单、失败回退、频率限制和审计，而不是简单设置“全部允许”模式。

## 19. 结课自测

不看代码回答：

1. 为什么系统提示词不能替代权限检查？
2. 三道闸门的固定顺序是什么？
3. 什么情况会直接允许，不询问用户？
4. 为什么 `rm -rf /` 不能通过输入 `y` 放行？
5. `[y/N]` 为什么把默认值设为拒绝？
6. S02 和 S03 对工作区外文件访问有什么不同？
7. 为什么被拒绝的工具仍需要匹配 ID 的 `tool_result`？
8. `continue` 拒绝一个工具后，同批其他工具会怎样？
9. `Permission denied.` 为什么不利于模型恢复？
10. 字符串 deny list 会有哪些误报和漏报？
11. 权限允许为什么不代表工具一定执行成功？
12. Schema、输入校验和权限决策分别解决什么问题？

完成综合挑战、结构化决策改动，并正确回答至少 10 题，就可以认为掌握了 S03。

## 20. 完成本课后的状态

你现在拥有：

```text
S01 Agent Loop
  + S02 工具 schema 与分发
  + S03 执行前权限管线
      ├─ hard deny
      ├─ rule match
      └─ user approval
  + 被拒绝调用的完整 tool_result
  = 一个能在行动前询问“是否允许”的 Agent
```

但权限逻辑现在直接写在 Agent Loop 的执行路径里。如果继续加入日志、参数改写、自动格式化、
提交代码等前后处理，循环会越来越臃肿。

S04 Hooks 将把这些扩展逻辑从循环中抽离为可注册的前置与后置钩子。

