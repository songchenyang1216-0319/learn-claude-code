# S09 实操教学指南：把长期知识移出易失上下文

> 对应课程：[s09_memory](../../s09_memory/)  
> 核心代码：[code.py](../../s09_memory/code.py)  
> 前置课程：[S08 Context Compact](s08-context-compact.md)  
> 建议用时：120–150 分钟  
> 本课产物：文件存储、索引、相关性选择、回合后提取和定期整理组成的记忆系统

## 1. 学完这一课，你应该能做到什么

完成 S09 后，你应该能够：

1. 区分消息历史、压缩摘要、长期 Memory 和 session memory；
2. 说明 `.memory/MEMORY.md` 与单条记忆文件各自作用；
3. 看懂 user、feedback、project、reference 四类记忆；
4. 解释相关记忆怎样通过 side-query 选择并临时注入；
5. 计算一个普通回合可能额外产生多少次模型调用；
6. 说明记忆为何在回合结束后提取、下一回合才生效；
7. 验证记忆能跨压缩、跨进程但不能自动跨工作目录；
8. 识别 slug、frontmatter、浅拷贝、索引注入和整理删除的风险；
9. 为记忆写入增加严格校验、原子替换、备份、锁和可观察错误；
10. 完成“记录偏好→退出→重启→自动应用”的跨会话实验。

本课最重要的一句话是：

> 上下文负责当前思考，Memory 只保存未来仍值得重新取回的信息。

## 2. 为什么压缩摘要不能替代长期记忆

S08 summary 的目标是让当前工作继续。它可能把：

```text
用户要求以后所有 Python 字符串优先单引号；
测试命令固定为 python -m unittest；
不要 mock 数据库。
```

压成：

```text
用户有代码风格和测试偏好。
```

而且新进程没有旧 messages 和 summary。

Memory 把精选信息写到独立文件：

```text
messages 可以压缩或消失
        │
        └─ 值得长期保留的事实
             → .memory/*.md
             → 下次相关任务再加载
```

长期记忆不应保存所有对话。否则只是把上下文膨胀转移到磁盘。

## 3. 四类记忆

```python
MEMORY_TYPES = ["user", "feedback", "project", "reference"]
```

| 类型 | 核心问题 | 适合内容 |
|---|---|---|
| `user` | 用户是谁、偏好什么 | 语言、格式、稳定习惯 |
| `feedback` | 应怎样做事 | 多次纠正、质量要求、禁忌 |
| `project` | 项目长期事实 | 架构背景、固定命令、迁移原因 |
| `reference` | 信息在哪里 | Issue、文档、常用入口、排查线索 |

不适合长期记忆：

- 一次性验证码；
- 密码和 API Key；
- 临时错误输出；
- 已完成任务的所有中间步骤；
- 很快过期却没有有效期的信息；
- 未确认的猜测。

当前代码虽然定义了 `MEMORY_TYPES`，写入时并没有强制校验 `mem_type` 必须属于它。

## 4. 存储布局

程序导入时立即创建：

```text
WORKDIR/.memory/
```

目录内：

```text
.memory/
├── MEMORY.md
├── user-preference-tabs.md
├── project-test-command.md
└── reference-auth-issue.md
```

单条文件：

```markdown
---
name: user-preference-tabs
description: User prefers tabs for indentation
type: user
---

Use tabs rather than spaces when editing project files.
```

索引：

```markdown
- [user-preference-tabs](user-preference-tabs.md) — User prefers tabs for indentation
```

索引提供低成本发现；完整文件提供细节。

## 5. Memory 的生命周期总览

每个新用户任务：

```text
1. 读取记忆文件目录
2. 根据最近用户对话选择最多 5 条相关记忆
3. 读取完整记忆内容
4. 读取 MEMORY.md 构建本回合 SYSTEM
5. 把相关记忆临时拼到当前 user turn
6. 运行 Agent Loop 与压缩管线
7. 模型给最终回答
8. 从对话提取新记忆
9. 达到阈值时整理记忆
10. 返回外层 REPL
```

新提取记忆发生在第 8 步，因此不会回头改变已经完成的本轮主回答。它从下一次用户任务开始
出现在 index 和选择流程中。

## 6. 一个回合可能有多少次模型调用

已有记忆时，一个不使用工具的简单回合通常可能产生：

```text
1 次：select_relevant_memories side-query
1 次：主 Agent 回答
1 次：extract_memories
```

达到 10 个文件阈值时还可能：

```text
+1 次：consolidate_memories
```

如果 auto compact：

```text
+1 次：summary
```

如果主 Agent 使用工具或子 Agent，还会更多。

所以持久 Memory 的质量与成本需要单独衡量，不能只看主 Agent 的调用次数。

## 7. 写入记忆的精确行为

```python
slug = (
    name.lower()
    .replace(" ", "-")
    .replace("/", "-")
)
filename = f"{slug}.md"
```

然后完整覆盖同名文件并重建索引。

当前边界：

- 同一 slug 会覆盖旧记忆；
- 没有版本历史；
- 没有锁；
- Windows 反斜杠没有替换，可能被解释成路径分隔符；
- `..`、冒号、控制字符等没有统一清理；
- name/description 直接进入 YAML，换行可能破坏 frontmatter；
- body 可包含任意 Markdown；
- 没有显式 UTF-8；
- 每写一条都扫描并重建整个 index。

记忆 name 来自模型输出，必须当作不可信输入。

## 8. 索引重建

`_rebuild_index()`：

1. 扫描 `.memory/*.md`；
2. 跳过 `MEMORY.md`；
3. 解析简单 frontmatter；
4. 生成 Markdown 链接；
5. 覆盖 MEMORY.md。

代码没有实际实施 README 所说的 200 行或 25KB 上限。记忆很多时，整个索引都会进入 SYSTEM。

如果某个文件损坏：

- meta 缺 name 时用文件 stem；
- description 缺失时用 body 第一行前 80 字符；
- 没有报告 warning；
- 文件仍可能进入 index。

## 9. 相关记忆选择

### 9.1 最近对话

只收集最近 3 条 role=user 且可提取为字符串的内容，拼接后取前 2000 字符。

字典形式的 tool_result 位于 user 消息，但代码只尝试读取对象的 `.text`，因此通常不会把工具
结果加入 recent。

### 9.2 Catalog

每个记忆只发送：

```text
index: name — description
```

body 不进入选择 side-query。

### 9.3 LLM 选择

要求只返回整数 JSON 数组，例如：

```json
[0, 3]
```

有效索引按返回顺序加入，最多 5 个。

当前没有去重。同一个索引重复出现时，同一记忆可能被重复加载。

### 9.4 Keyword fallback

API、正则或 JSON 解析失败时：

- recent 按空格切词；
- 只保留长度大于 3 的词；
- 在 name + description 中做小写子字符串匹配；
- 最多 5 个。

这种 fallback 对英文较友好，对无空格中文、同义词和标点不稳。

异常被完全吞掉，默认没有日志告诉用户发生了降级。

## 10. 相关记忆怎样注入

`load_memories()` 生成：

```xml
<relevant_memories>

完整 memory A

完整 memory B

</relevant_memories>
```

Agent Loop 不把它永久写回 history，而是浅复制 messages，再替换目标 user message：

```python
request_messages = messages.copy()
request_messages[memory_turn] = {
    **messages[memory_turn],
    "content": memories_content + "\n\n" + original_content,
}
```

优点：

- 记忆内容只进入实际 API 请求；
- 原 history 保持原用户文字；
- 同一长期记忆不会自动成为永久 transcript。

当前问题：

- `memory_turn` 在压缩前按最后一条消息下标记录；
- snip/auto/reactive 可能改变列表长度与位置；
- 下标超范围时本轮完全不注入；
- 下标仍有效但指向另一条消息时可能错位；
- 每个完整记忆没有单项大小限制。

## 11. SYSTEM 索引的刷新时机

每次调用 `agent_loop()` 开头：

```python
system = build_system()
```

所以：

- 同一用户任务的所有模型轮使用同一个 system 字符串；
- 本轮结束后提取的新记忆不会修改本轮 system；
- 下一次用户输入时才重新读 MEMORY.md；
- 与 S07 在进程启动时固定 SYSTEM 不同，S09 每个用户 turn 重建一次。

## 12. 回合后提取

只有主模型返回非 tool_use，准备结束时调用：

```python
extract_memories(pre_compress)
consolidate_memories()
```

提取最近 10 条消息的可见文字，prompt 要求 JSON 数组：

```json
[
  {
    "name": "user-preference-single-quotes",
    "type": "user",
    "description": "User prefers single quotes in Python",
    "body": "Use single quotes..."
  }
]
```

已有 name + description 一起提供给模型用于去重。

当前实现：

- dialogue 最多 4000 字符；
- 正则贪婪匹配第一个 `[` 到最后一个 `]`；
- 不验证顶层一定是 list；
- 不验证每项一定是 dict；
- 不验证 type；
- 不验证 name/description/body 类型和长度；
- 写入过程可部分成功后遇错；
- 所有异常静默吞掉。

## 13. `pre_compress` 并不是真正深快照

代码：

```python
pre_compress = [
    message if isinstance(message, dict) else ...
    for message in messages
]
```

绝大多数消息本来就是 dict，因此新列表仍引用相同 dict 和嵌套 content。

后续：

- budget 修改 tool_result；
- micro 原地替换 content；
- `pre_compress` 会看到这些嵌套修改；
- snip 替换 messages 列表时，被裁掉的旧 dict 仍留在 snapshot；
- 最终 assistant response 在 snapshot 创建后才追加，因此提取时不包含本轮最终回答。

所以注释中的“full fidelity”只部分成立。真正快照需要 `copy.deepcopy()`，而本轮完整 transcript
还需要主动收集之后新增的 response/result。

## 14. Consolidation 的当前危险顺序

当记忆文件数达到 10：

```text
把所有记忆前 16000 字符发给模型
→ 解析返回 JSON
→ 删除所有旧 .md（保留 MEMORY.md）
→ 写回新 items
```

删除发生在完整验证新 items 之前。

风险：

- 模型返回 `[]`：全部旧记忆被删，写回 0 条；
- items 中前几项合法、后面一项不是 dict：旧文件已删，写回可能部分完成后异常；
- 写磁盘中途失败：旧数据已不可恢复；
- 两个进程同时整理：互相删除/覆盖；
- prompt 只含 catalog 前 16000 字符，后面的记忆可能没被模型看到却仍被删除；
- 异常被吞掉，用户可能不知道记忆已损坏。

这是本课最值得修复的不变量：

> 新集合未完全验证并安全落盘前，绝不能删除旧集合。

## 15. S09 继承机制的实际范围

本课为了聚焦 Memory，工具缩减为：

```text
bash
read_file
write_file
edit_file
glob
task
```

没有：

- todo_write；
- load_skill；
- compact 工具。

仍有自动压缩函数，但没有 Hook 注册表和 deny list。主循环直接执行 handler，Bash 不经过前几课
的 permission Hook。必须继续在隔离目录练习。

子 Agent 更简化，只拥有 bash、read_file、write_file。

## 16. 准备稳定的隔离目录

为了验证跨进程记忆，必须多次使用同一个实验目录。

### 16.1 Windows PowerShell

在仓库根目录运行：

```powershell
$courseRoot = (Resolve-Path .).Path
$s09Lab = Join-Path $env:TEMP "learn-claude-code-s09"
New-Item -ItemType Directory -Force -Path $s09Lab | Out-Null
Set-Location -LiteralPath $s09Lab
$env:PYTHONUTF8 = "1"
& "$courseRoot\.venv\Scripts\python.exe" "$courseRoot\s09_memory\code.py"
```

### 16.2 macOS / Linux

```bash
course_root="$(pwd)"
s09_lab="${TMPDIR:-/tmp}/learn-claude-code-s09"
mkdir -p "$s09_lab"
cd "$s09_lab"
"$course_root/.venv/bin/python" "$course_root/s09_memory/code.py"
```

不要每次用新的 `mktemp -d`，否则每个目录都有独立 `.memory`，无法证明跨会话。

启动后会立即创建空 `.memory/`。

## 17. 最小成功路径：记录并跨进程使用偏好

第一次启动时输入：

```text
Remember this stable preference: when writing Python, use single quotes for
ordinary strings and include complete function type hints.
```

主回答结束前后可能出现：

```text
[Memory: extracted 1 new memories]
```

退出后检查：

```text
.memory/MEMORY.md
.memory/<generated-name>.md
```

如果提取模型判断为两个独立偏好，也可能生成两条。

输入 `q`，从同一个 `$s09Lab` 或 `$s09_lab` 重新启动。再输入：

```text
Create greeting.py with a greet(name) function that returns Hello plus the
name. Follow my remembered preferences, then read the file to verify it.
```

验收：

- 重启后索引仍存在；
- 本轮选择到了相关偏好；
- `greeting.py` 使用单引号；
- 函数参数和返回值有类型；
- Agent 不需要用户重新说明偏好；
- 最终读取验证；
- 文件位于临时目录。

记忆选择是模型行为，可能没有选中。加入选择日志后可区分“没有保存”和“保存了但没选”。

## 18. 八个观察实验

### 实验 1：记忆下一回合才生效

第一个回合表达新偏好时，主回答先生成，之后才提取。该回合的 SYSTEM 和 relevant memories 中
都还没有新文件。

下一次用户输入才重建 index 并选择。

### 实验 2：换工作目录就换记忆库

从另一个临时目录启动 S09。

预期创建新的 `.memory`，看不到原实验目录记忆。Memory 跟随 WORKDIR，不是全局用户配置。

### 实验 3：索引和全文不同

打开 MEMORY.md，应只有 name、链接和 description。

完整 body 只在：

- read_memory_file；
- load_memories 选中；
- consolidation catalog；

等路径读取。

### 实验 4：最多加载五条

准备 6 条描述都明显与 “Python style” 相关的记忆，让选择 side-query 返回 6 个索引。

循环只追加前 5 个有效索引。当前重复索引也占名额。

### 实验 5：Side-query 失败后关键词降级

在实验副本让选择 API 抛异常，或用假 client。输入包含某记忆 description 的长英文关键词。

预期 fallback 根据 name + description 选中。加入：

```python
print("[memory selection] keyword fallback")
```

否则原代码静默降级。

### 实验 6：工具结果不会成为 fallback 查询重点

让 Bash 输出一个只出现在某 memory description 的词，但用户文本不含该词。

recent 收集 user tool_result 时无法从字典 block 提取 `.text`，通常不会把输出加入选择文本。

### 实验 7：同名记忆覆盖

连续两轮要求记住同一个 name 但不同 body，或直接离线调用 `write_memory_file()`。

相同 slug 的文件被覆盖，索引仍只有一条，没有冲突提示或历史版本。

### 实验 8：Consolidation 空数组会清空

只在一次性复制的 `.memory` 练习目录做，不要使用重要记忆：

1. 准备 10 条 memory；
2. 假客户端让 consolidate 返回 `[]`；
3. 调用 consolidate；
4. 观察所有非 index `.md` 被删除。

这个实验用于证明事务顺序问题。完成后实施安全修复，不要在真实记忆库复现。

## 19. 离线验证存储与选择

无需主模型，可直接测试：

```python
write_memory_file(
    "python-style",
    "user",
    "User prefers typed Python functions",
    "Add argument and return type hints.",
)
```

验证：

```text
文件存在
frontmatter 正确
MEMORY.md 有一行链接
list_memory_files() 返回 metadata + body
read_memory_file() 返回全文
```

把 `client.messages.create` 替换为抛异常的假函数，调用：

```python
select_relevant_memories([
    {"role": "user", "content": "Use typed Python functions"}
])
```

预期关键词 fallback 选中 `python-style.md`。

## 20. 修改实验：安全 slug 和元数据校验

先复制：

Windows：

```powershell
Copy-Item "$courseRoot\s09_memory\code.py" "$courseRoot\s09_memory\code_experiment.py"
```

macOS / Linux：

```bash
cp "$course_root/s09_memory/code.py" "$course_root/s09_memory/code_experiment.py"
```

### 安全 slug

```python
import hashlib
import re


def safe_memory_slug(name: str) -> str:
    if not isinstance(name, str) or not name.strip():
        raise ValueError("memory name must be a non-empty string")
    normalized = re.sub(
        r"[^a-z0-9]+",
        "-",
        name.lower(),
    ).strip("-")
    if not normalized:
        normalized = "memory"
    digest = hashlib.sha256(
        name.encode("utf-8")
    ).hexdigest()[:8]
    return f"{normalized[:60]}-{digest}"
```

### 记录校验

```python
def validate_memory(memory: dict) -> dict:
    if not isinstance(memory, dict):
        raise ValueError("memory must be an object")
    name = memory.get("name")
    mem_type = memory.get("type")
    description = memory.get("description")
    body = memory.get("body")

    if mem_type not in MEMORY_TYPES:
        raise ValueError(f"invalid memory type: {mem_type!r}")
    for field, value, limit in (
        ("name", name, 120),
        ("description", description, 500),
        ("body", body, 10000),
    ):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} must be non-empty text")
        if len(value) > limit:
            raise ValueError(f"{field} exceeds {limit} characters")
    return memory
```

使用 PyYAML 的 `safe_dump()` 写 frontmatter，或至少拒绝 name/description 中的换行。

验收：

- `..\outside` 不会生成目录逃逸；
- 非法 type 被拒绝；
- 空 body 不写文件；
- 同一可见 name 的文件名稳定且安全；
- 全部使用 UTF-8。

## 21. 修改实验：深拷贝并记录当前回合

不要从整个已压缩 history 猜当前 turn。建立独立列表：

```python
from copy import deepcopy

turn_transcript = [deepcopy(messages[-1])]
```

每次主 response：

```python
assistant_message = {
    "role": "assistant",
    "content": response.content,
}
messages.append(assistant_message)
turn_transcript.append(deepcopy(assistant_message))
```

每次结果：

```python
result_message = {
    "role": "user",
    "content": results,
}
messages.append(result_message)
turn_transcript.append(deepcopy(result_message))
```

最终 response 也已经被加入。停止时：

```python
extract_memories(turn_transcript)
```

预期：

- Micro 不再修改提取 transcript；
- Snip 不影响当前回合提取；
- 最终 assistant 文本可供提取器判断；
- 不把多个旧用户任务重复发送给提取器。

## 22. 修改实验：稳定的记忆注入位置

不要保存易失下标。压缩和 memory 选择完成后，构造请求时找最后一条普通 user string：

```python
request_messages = messages.copy()
target = next(
    (
        index
        for index in range(len(request_messages) - 1, -1, -1)
        if request_messages[index].get("role") == "user"
        and isinstance(request_messages[index].get("content"), str)
        and not request_messages[index]["content"].startswith("[Compacted]")
    ),
    None,
)
```

如果找不到，作为独立上下文消息追加：

```python
if memories_content:
    memory_message = {
        "role": "user",
        "content": memories_content,
    }
```

更稳妥的 API 可使用专门 attachments/context 通道。无论采用哪种方式，都要测试 snip、auto 和
reactive 后仍能注入到正确回合。

## 23. 修改实验：事务化 Consolidation

安全顺序：

```text
1. 解析模型响应
2. 验证顶层 list
3. 验证每一条 memory
4. 要求结果非空，除非明确允许清空
5. 写入临时目录
6. 在临时目录重建并验证 index
7. 创建旧目录备份
8. 原子交换目录
9. 成功后再清理备份
```

伪代码：

```python
validated = [
    validate_memory(item)
    for item in items
]
if not validated:
    raise ValueError("consolidation returned no memories")

staging = MEMORY_DIR.parent / ".memory-staging-<uuid>"
backup = MEMORY_DIR.parent / ".memory-backup-<uuid>"
```

Windows 上目录替换语义需要专门验证，不能在一个 shell 中拼接未经检查的递归删除命令。

至少做到：

- 校验完成前不 unlink；
- 失败时保留旧文件；
- 错误可见；
- 只有成功集合才替换；
- 并发时持有锁。

## 24. 修改实验：让提取错误可观察

当前所有异常 `except Exception: pass`。改成结构化结果：

```python
@dataclass
class MemoryExtractionResult:
    status: str
    created: list[str]
    error: str | None = None
```

日志：

```text
[memory] select=llm selected=2 duration=...
[memory] select=fallback reason=timeout selected=1
[memory] extract created=1 skipped=0
[memory] consolidate status=skipped count=7
```

不要记录完整记忆 body 或用户敏感内容。

## 25. 扩展实验：给记忆增加更新时间和来源

Frontmatter：

```yaml
name: project-test-command
description: Preferred project test command
type: project
created_at: 2026-07-30T...
updated_at: 2026-07-30T...
source: user-explicit
confidence: confirmed
```

可用于：

- 过期判断；
- 冲突合并；
- 优先保留用户明确要求；
- 区分模型推断和用户确认；
- 审计来源。

对容易变化的事实增加 `expires_at` 或重新验证策略。

## 26. 扩展实验：用户可控的忘记操作

当前用户没有工具删除单条记忆。新增：

```text
memory_list
memory_forget(filename)
```

`memory_forget`：

- 必须从扫描注册表按精确文件名查找；
- 路径必须保持在 MEMORY_DIR；
- 删除前显示 description；
- 需要用户确认；
- 删除后重建索引；
- 写入审计；
- 支持从备份恢复。

“忘记我的偏好”不应只让模型忽略文件，应该真正删除持久存储。

## 27. 本课综合挑战：跨会话风格偏好

第一会话输入：

```text
Remember these durable preferences:
1. Python functions should have complete argument and return type hints.
2. Ordinary Python strings should use single quotes where practical.
3. Verification should run the created file.
Confirm what should be remembered, but do not create code yet.
```

等待提取完成，检查 `.memory`。退出程序。

第二会话从相同目录启动，输入：

```text
Create profile.py with a format_user(name, role) function returning
`name: role`. Apply relevant remembered preferences. Run the file with an
example and verify the output.
```

验收：

- 第一会话生成合法 memory 文件和 index；
- 第二会话无需重述偏好；
- 选择器加载相关 memory；
- `format_user` 有完整类型；
- 普通字符串优先单引号；
- 文件实际运行；
- 模型没有加载无关 memory；
- 第二回合提取器不会无意义复制同一偏好；
- 重启后结果仍成立。

## 28. 常见问题与定位

### 没有生成 `.memory`

目录在模块导入时创建。检查 WORKDIR 和启动脚本。若目录存在但没文件，提取模型可能返回空数组
或解析失败。

### 没出现 extracted 日志

可能：

- 模型判断没有长期价值；
- JSON 格式解析失败；
- API 失败被静默吞掉；
- desc/body 为空；
- 主 Agent 尚未到最终非 tool_use 分支。

加入可观察日志。

### 重启后不记得

确认从同一工作目录启动，并检查：

- MEMORY.md 是否存在；
- 记忆文件是否存在；
- description 是否足以让选择器发现；
- side-query 是否选中；
- injection 是否因压缩下标失效。

### 记忆文件存在但 Agent 不应用

保存、选择、加载、注入、遵循是五个阶段。逐段记录，不要只看最终回答猜原因。

### 每轮响应明显变慢

已有记忆时增加 selection 和 extraction side-query；达到阈值还会 consolidation。记录每阶段耗时。

### Consolidation 后记忆消失

当前实现先删旧文件再写新结果。恢复备份，并实施事务化整理。

### Memory index 越来越大

当前没有 200 行/25KB限制。增加预算、分页、搜索和 consolidation 安全策略。

### Bash 不再被 deny Hook 阻止

S09 没有继承 Hook/permission 注册表。它直接分发工具。只在临时目录运行。

## 29. 设计层面的延伸思考

### 记忆选择比记忆写入更决定体验

存了但取不回等于没记住；每次全加载又会造成上下文污染。description 是检索接口的一部分。

### 记忆必须允许纠正和遗忘

偏好会变化，项目事实会过期。系统需要：

- 新旧冲突处理；
- 用户显式覆盖；
- 有效期；
- 来源与置信度；
- 删除和导出；
- 可审计历史。

### Memory 是高敏感持久层

它跨会话保存，风险高于普通 context。必须避免秘密、访问令牌、私人数据和未经确认的推断。

### 提取模型不能拥有无限写权限

提取结果需要 schema、大小、类型、路径和重复检查。模型生成 JSON 不等于数据可信。

### 整理是数据库迁移，不是普通回答

Consolidation 会重写持久真相，必须具备事务、备份、锁、校验、回滚和审计。

## 30. 结课自测

不看代码回答：

1. Memory 与 context compact summary 的目的差别是什么？
2. MEMORY.md 保存全文还是索引？
3. 四类 memory 分别适合什么？
4. 相关性选择发送 body 吗？
5. 每回合为什么可能多两次模型调用？
6. 新记忆什么时候开始进入 SYSTEM？
7. Relevant memory 是否写回原 history？
8. memory_turn 为什么会在压缩后失效？
9. pre_compress 为什么不是真正深快照？
10. 当前 mem_type 是否经过枚举校验？
11. consolidation 为什么可能清空全部记忆？
12. 跨进程实验为什么必须复用同一个 WORKDIR？

完成跨会话挑战、事务化 consolidation 设计和离线 fallback 测试，并正确回答至少 10 题，就可以
认为掌握了 S09。

## 31. 完成本课后的状态

你现在拥有：

```text
.memory/*.md
  → MEMORY.md 小索引
  → 每个 user turn 重建 SYSTEM
  → LLM/关键词选择相关文件
  → 完整内容临时注入
  → 主 Agent 工作
  → 回合结束提取新长期信息
  → 达到阈值时整理
```

下一课 S10 System Prompt 会把目前散落在字符串中的身份、环境、工具、记忆和运行指导拆成可组合
区段，按当前运行状态组装。

