# S08 实操教学指南：用分层压缩延长 Agent 会话

> 对应课程：[s08_context_compact](../../s08_context_compact/)  
> 核心代码：[code.py](../../s08_context_compact/code.py)  
> 前置课程：[S07 Skill Loading](s07-skill-loading.md)  
> 建议用时：120–150 分钟  
> 本课产物：budget、snip、micro、LLM summary 与 reactive fallback 组成的压缩管线

## 1. 学完这一课，你应该能做到什么

完成 S08 后，你应该能够：

1. 解释上下文为什么会被消息和工具结果逐步填满；
2. 区分字符估算、消息数量、工具结果预算和真实模型 token；
3. 说明 budget、snip、micro、auto summary 的职责与执行顺序；
4. 预测每层会保留、替换、落盘或永久丢弃什么；
5. 解释为什么不能拆开 assistant tool_use 与 user tool_result；
6. 验证大结果旁路存储和最近三个工具结果保留规则；
7. 说明主动 compact 与 reactive compact 的不同触发条件；
8. 识别 transcript 覆盖、摘要截断、孤立 tool_result 等当前实现边界；
9. 使用假客户端离线测试压缩，而不消耗模型 API；
10. 为压缩加入精确状态、唯一文件名、配对修复、预算指标和恢复能力。

本课最重要的原则是：

> 先做便宜、确定性的压缩；只有仍超预算时，才用模型做昂贵、有损的语义摘要。

## 2. 上下文里究竟堆积了什么

每个工具轮通常增加：

```text
assistant:
  tool_use(name, input, id)

user:
  tool_result(tool_use_id, content)
```

如果 Agent：

- 读取 20 个源文件；
- 运行 10 次测试；
- 加载两个完整技能；
- 委派多个子任务；
- 多次更新 TODO；

这些内容都会进入父 `messages`。模型每轮都要重新处理当前有效上下文，直到超过供应商窗口。

上下文管理不是简单“删除最旧消息”。至少要考虑：

- 初始用户目标；
- 最新工作状态；
- 工具请求和结果配对；
- 大输出是否可恢复；
- 用户约束；
- 已修改文件；
- 尚未完成的计划；
- 压缩本身失败时怎样恢复。

## 3. S08 的实际管线

代码每次调用主模型前执行：

```python
messages[:] = tool_result_budget(messages)
messages[:] = snip_compact(messages)
messages[:] = micro_compact(messages)

if estimate_size(messages) > CONTEXT_LIMIT:
    messages[:] = compact_history(messages)
```

实际顺序是：

```text
L3 budget
→ L1 snip
→ L2 micro
→ 字符估算
→ L4 auto summary（需要额外模型调用）
→ 正常模型调用
→ 若 API 仍报 prompt too long：reactive compact
```

L1/L2/L3 是课程概念编号，不代表实际执行编号顺序。

## 4. 当前常量与单位

```python
CONTEXT_LIMIT = 50000
KEEP_RECENT = 3
PERSIST_THRESHOLD = 30000
```

另有：

```python
tool_result_budget(..., max_bytes=200_000)
MAX_REACTIVE_RETRIES = 1
```

名称中的 token 和 bytes 需要谨慎理解：

- `estimate_size()` 使用 `len(str(messages))`，单位近似字符；
- `tool_result_budget()` 使用 `len(str(content))`，也是字符；
- `PERSIST_THRESHOLD` 是字符阈值；
- 文件实际 UTF-8 字节可能更多；
- 50000 字符不等于 50000 token；
- Python 对 SDK block 的 `str()` 表示也不等于 API 序列化后的精确大小。

教学实现用字符估算展示机制，不提供精确 tokenizer。

## 5. L3：大工具结果预算和旁路存储

尽管课程概念编号叫 L3，它在实际管线中最先运行。

### 5.1 只检查最后一条 user 消息

```python
last = messages[-1]
```

并要求：

- role 是 user；
- content 是 list；
- list 中有字典形式的 tool_result。

更早消息中的大结果不由这一轮 budget 直接处理，后面的 micro 可能处理它们。

### 5.2 先计算同一批结果总字符数

```python
total = sum(len(str(block["content"])) ...)
```

只有 `total > 200000` 才启动落盘。

单个 100000 字符结果虽然超过 `PERSIST_THRESHOLD`，但如果该批总量不超过 200000，也不会落盘。

### 5.3 从最大的结果开始

候选按长度降序。每次只处理自身超过 30000 字符的结果，直到替换后的总量不再超过 200000。

### 5.4 落盘内容

完整输出写入：

```text
.task_outputs/tool-results/<tool_use_id>.txt
```

上下文替换为：

```text
<persisted-output>
Full output: 完整路径
Preview:
前 2000 字符
</persisted-output>
```

模型仍能看到预览和文件位置，需要时可用 read_file 再读。

### 5.5 当前边界

- 11 个各 20000 字符的结果总计 220000，但每个都低于 30000，代码不会落盘任何一个；
- 文件名直接使用 `tool_use_id`，没有额外清理；
- 相同 ID 的文件已存在时不会覆盖，极端情况下指针可能指向旧内容；
- 写文件没有显式 UTF-8；
- 完整结果会落到工作目录，可能包含敏感数据；
- 没有保留期限或容量清理。

## 6. L1：裁剪消息中段

当消息数超过 50：

```text
保留头部 3 条
保留尾部 47 条
中间插入一条 [snipped N messages]
```

返回数量通常是：

```text
3 + 1 placeholder + 47 = 51
```

所以 `max_messages=50` 是保留头尾预算，不是严格保证最终列表最多 50。

### 配对保护

如果头部第 3 条是 assistant tool_use，代码会把紧随其后的 user tool_result 也保留到头部。

如果尾部第一条是 user tool_result，且前一条是 assistant tool_use，尾部起点向前移动一条。

这样避免压缩后出现：

```text
user tool_result
```

却找不到紧邻的：

```text
assistant tool_use
```

当前检查只基于相邻消息类型，没有验证 tool_use_id 是否一致。仓库测试覆盖头尾边界不产生
明显孤立 tool_result。

### Snip 的信息损失

中间消息完全删除，只留下数量：

```text
[snipped 23 messages]
```

没有语义摘要，也没有单独保存被删中段。模型无法知道具体内容。

## 7. L2：旧工具结果占位

`collect_tool_results()` 按消息顺序收集所有字典形式 tool_result block。

`micro_compact()`：

```text
保留最近 3 个 tool_result block 的完整内容
更早结果：
  长度 > 120 → 替换成占位符
  长度 <= 120 → 继续保留
```

占位符：

```text
[Earlier tool result compacted. Re-run if needed.]
```

注意：

- “最近 3 个”按 block，不按消息；
- 同一 user 消息里有 5 个 tool results 时，前 2 个也可能被压缩；
- 函数直接修改原 block，属于原地变更；
- 对应 assistant tool_use 通常仍在，模型能知道原工具和输入；
- 需要内容时只能重新运行工具；
- 如果旧结果是 persisted-output 指针，micro 也可能把指针替换掉。

## 8. L4：模型语义摘要

当：

```python
len(str(messages)) > 50000
```

自动执行：

```text
write_transcript(messages)
→ summarize_history(messages)
→ 用一条 [Compacted] user 消息替换全部 history
```

摘要 prompt 要求保留：

1. 当前目标；
2. 关键发现与决策；
3. 已读或已改文件；
4. 剩余工作；
5. 用户约束。

摘要最多输出 2000 tokens。

### 摘要输入还有 80000 字符截断

```python
conversation = json.dumps(messages, default=str)[:80000]
```

只保留序列化后的前 80000 字符。超长历史的后部可能被排除，而最新目标往往恰好位于后部。
这与“保留当前工作”目标存在冲突，是值得修正的教学边界。

### 摘要是有损的

压缩后 active messages 只有：

```text
user: [Compacted]

<summary>
```

原始 tool_use、tool_result、技能全文、TODO tool calls 都不再直接可见。

## 9. Transcript 的真实保留范围

`write_transcript()` 写入：

```text
.transcripts/transcript_<Unix秒>.jsonl
```

每行一个 JSON 消息。

必须注意当前调用顺序：

```text
budget 先修改/落盘
→ snip 删除中段
→ micro 替换旧结果
→ 才判断并执行 compact_history
→ write_transcript
```

所以自动 L4 保存的是“经过前三层处理后的当前 messages”，不是未经压缩的完整原始会话。

另外：

- 同一秒两次保存使用相同文件名并以 `w` 打开，会覆盖；
- SDK 内容对象用 `default=str`，不一定能完整结构化恢复；
- 默认 `json.dumps` 会转义非 ASCII；
- 教学代码没有 transcript 查询工具；
- transcript 可能含用户数据，需要访问控制。

大工具输出在 budget 阶段可能已保存到 `.task_outputs`，但被 snip/micro 删除的其他内容无法仅靠
当前 transcript 恢复。

## 10. Reactive Compact

触发条件不是字符估算，而是正常模型 API 抛异常，错误字符串包含：

```text
prompt_too_long
或
too many tokens
```

流程：

```text
保存当前 transcript
→ 默认保留最近约 5 条原始消息
→ 对更早历史生成摘要
→ [Reactive compact summary] + recent tail
→ 重试主模型
```

尾部边界同样避免从 tool_result 开始。

当前最多 reactive 一次。第二次仍然 prompt too long 就重新抛出异常。

每次主模型 API 成功后：

```python
reactive_retries = 0
```

所以限制针对连续失败，不是整个会话总次数。

### 小历史边界

消息不超过 5 条时 `tail_start=0`，代码仍会让 summary 模型总结空列表，再把全部原消息附回。
这增加调用，却几乎不减少上下文。

## 11. 模型主动调用 `compact`

父工具列表新增：

```text
compact(focus?)
```

它不在 `TOOL_HANDLERS`，而是在 Agent Loop 中特殊处理：

```python
if block.name == "compact":
    messages[:] = compact_history(messages)
    ...
    break
```

`focus` 字段当前没有传给 `compact_history()`，所以模型即使提供焦点也不会影响 summary prompt。

### 当前配对问题

代码先把包含 compact tool_use 的 assistant 消息纳入摘要并从 active messages 删除，然后追加：

```text
user tool_result(compact id)
```

压缩后的 active history 可能变成：

```text
user [Compacted summary]
user [tool_result for compact]
```

对应 assistant tool_use 已不存在，产生孤立 tool_result。严格工具消息协议的供应商可能在下一次
请求时拒绝。

如果同一响应中 compact 前还有其他工具调用，已执行结果也会和被删除的 tool_use 一起混入
这个问题。

主动 compact 应被视为需要修正的教学路径，后文给出方案。

## 12. S08 继承机制的实际范围

S08 聚焦压缩，部分旧机制被简化：

- 仍有基础工具、TodoWrite、task、load_skill；
- TodoWrite 的三轮 nag reminder 不在 S08 主循环中；
- Hook 注册表只含 PreToolUse/PostToolUse；
- 没有 UserPromptSubmit 和 Stop Hook；
- deny list 缩减为 `rm -rf /`、`sudo`、`shutdown`；
- 技能 frontmatter 不再使用 PyYAML，只做逐行 `key: value` 解析；
- 多行 YAML description（例如 `description: |`）会在 catalog 中显示为 `|`；
- 子 Agent 没有 compact 和 load_skill。

学习时应聚焦本课增量，同时以实际代码判断继承了哪些行为。

## 13. 准备隔离实验目录

### 13.1 Windows PowerShell

在仓库根目录运行：

```powershell
$courseRoot = (Resolve-Path .).Path
$s08Lab = Join-Path $env:TEMP "learn-claude-code-s08"
New-Item -ItemType Directory -Force -Path $s08Lab | Out-Null
Copy-Item "$courseRoot\skills" -Destination $s08Lab -Recurse -Force
Set-Location -LiteralPath $s08Lab
$env:PYTHONUTF8 = "1"

$utf8NoBom = [Text.UTF8Encoding]::new($false)
1..5 | ForEach-Object {
    $path = Join-Path $s08Lab ("medium-{0}.txt" -f $_)
    [IO.File]::WriteAllText($path, ("m" * 500), $utf8NoBom)
}
[IO.File]::WriteAllText(
    (Join-Path $s08Lab "huge.txt"),
    ("H" * 250001),
    $utf8NoBom
)

& "$courseRoot\.venv\Scripts\python.exe" "$courseRoot\s08_context_compact\code.py"
```

### 13.2 macOS / Linux

在仓库根目录运行：

```bash
course_root="$(pwd)"
s08_lab="$(mktemp -d)"
cp -R "$course_root/skills" "$s08_lab/"
cd "$s08_lab"
for number in 1 2 3 4 5; do
  head -c 500 /dev/zero | tr '\0' 'm' > "medium-$number.txt"
done
head -c 250001 /dev/zero | tr '\0' 'H' > huge.txt
"$course_root/.venv/bin/python" "$course_root/s08_context_compact/code.py"
```

启动后应看到：

```text
s08: Context Compact — four-layer compaction pipeline
输入问题，回车发送。输入 q 退出。

s08 >>
```

## 14. 最小成功路径：触发大结果落盘

输入：

```text
Use read_file to read huge.txt and then report only its exact character count.
```

过程：

1. `read_file` 返回 250001 字符；
2. 控制台只预览前 200 字符；
3. 结果作为 user tool_result 追加；
4. 下一轮顶部 `tool_result_budget()` 看到总量超过 200000；
5. 完整输出写入 `.task_outputs/tool-results/`；
6. tool result 被替换成 persisted marker + 2000 字符预览；
7. 模型根据 marker 和内容报告长度。

验收：

- `.task_outputs/tool-results/` 出现以 tool ID 命名的文件；
- 文件大小约 250001 字节；
- active tool result 不再含完整 250001 字符；
- marker 提供完整路径；
- 模型最终仍能完成任务。

如果模型改用 Bash `wc -c`，输出很小，不会触发。提示词必须明确 `read_file`。

## 15. 八个观察实验

### 实验 1：Micro 保留最近三个结果

输入：

```text
Read medium-1.txt through medium-5.txt with separate read_file calls. After all
five results, explain which files share the same content.
```

在下一轮预处理后：

- 前两个长度 500 的 tool result 变为占位符；
- 最近三个保留完整；
- 对应 tool_use 仍在；
- 模型若需要前两个的细节，可以重新读取。

原代码没有打印 micro 日志。使用后面的 trace 改动才能直接观察。

### 实验 2：小结果即使很旧也保留

准备多个只有几十字符的文件并读取超过 3 个。旧结果长度不超过 120 时不会被替换。

所以 KEEP_RECENT=3 不是“只允许三个完整结果”，而是“大于 120 字符的旧结果只保留最近三个”。

### 实验 3：Snip 保留头尾

在实验副本把 `max_messages` 默认值临时改成 8，连续进行足够多的无工具和工具对话。

预期：

- 前 3 条保留；
- 尾部约 5 条保留；
- 中间出现 `[snipped N messages]`；
- tool_use/tool_result 边界可能让保留数量略多；
- 中间内容无法恢复。

### 实验 4：Budget 不是单结果阈值

创建 100000 字符文件并只读它一次。

它超过 PERSIST_THRESHOLD=30000，但该批 total 不超过 200000，所以不会落盘。

再让同一模型响应读取三个 100000 字符文件；若模型确实把结果放在同一 user message，总量超过
200000，budget 会从最大结果开始落盘。

### 实验 5：许多中等结果可能无法降到预算

离线构造 11 个各 20000 字符的 tool_result：

```text
total = 220000
每个 <= 30000
```

budget 进入处理，但没有任何 block 满足持久化阈值，最终仍为 220000。

这说明“总预算”和“单项落盘阈值”组合需要兜底策略。

### 实验 6：Auto summary 是额外模型调用

在实验副本把：

```python
CONTEXT_LIMIT = 3000
```

连续读取几个 medium 文件并对话。

看到：

```text
[auto compact]
[transcript saved: ...]
```

一次 auto compact 会先调用 summary 模型，再调用正常 Agent 模型，因此该轮至少多一个 API 请求。

### 实验 7：主动 compact 的协议诊断

输入：

```text
Call the compact tool now, by itself, with focus `preserve the current goal`.
```

可能看到 transcript 和 summary，但下一次主模型请求可能因孤立 tool_result 报消息协议错误。

检查 active history 或加入配对断言：

```python
assert_no_orphan_tool_results(messages)
```

本实验的目标是定位教学实现边界，不应把报错误认为模型供应商不支持工具。

### 实验 8：Reactive 只在错误字符串匹配时触发

用假客户端第一次抛：

```text
RuntimeError("prompt_too_long")
```

会进入 reactive。

若错误是：

```text
RuntimeError("request rejected")
```

即使真实原因可能也是上下文，字符串不匹配就直接抛出。生产实现应依据结构化错误码。

## 16. 离线实验：直接测试前三层

构造消息不需要调用模型：

```python
def result_message(tool_id: str, size: int):
    return {
        "role": "user",
        "content": [{
            "type": "tool_result",
            "tool_use_id": tool_id,
            "content": "x" * size,
        }],
    }
```

### Micro

```python
messages = [
    result_message(f"t-{index}", 200)
    for index in range(5)
]
micro_compact(messages)
```

预期：

- t-0、t-1 变占位；
- t-2、t-3、t-4 保留 200 字符。

### Budget

```python
messages = [{
    "role": "user",
    "content": [
        {
            "type": "tool_result",
            "tool_use_id": "large-a",
            "content": "a" * 150000,
        },
        {
            "type": "tool_result",
            "tool_use_id": "large-b",
            "content": "b" * 150000,
        },
    ],
}]
tool_result_budget(messages)
```

预期至少最大的一个结果落盘，替换后 total 降到 200000 以下。

### Snip

构造 60 条消息，并在边界放入匹配 tool pair。调用：

```python
snip_compact(messages, max_messages=10)
```

验证任何 user tool_result 前一条仍是 assistant tool_use。

仓库 `tests/test_compaction_tool_pairs.py` 对 S08、S09、S20 都检查了这些边界。

## 17. 修改实验：加入可观测指标

先复制：

Windows：

```powershell
Copy-Item "$courseRoot\s08_context_compact\code.py" "$courseRoot\s08_context_compact\code_experiment.py"
```

macOS / Linux：

```bash
cp "$course_root/s08_context_compact/code.py" "$course_root/s08_context_compact/code_experiment.py"
```

增加：

```python
def compact_metrics(label: str, before, after) -> None:
    print(
        f"[compact] {label}: "
        f"messages {len(before)} -> {len(after)}, "
        f"chars {estimate_size(before)} -> {estimate_size(after)}"
    )
```

注意部分函数会原地修改，记录 before 时要深拷贝：

```python
from copy import deepcopy

before = deepcopy(messages)
messages[:] = tool_result_budget(messages)
compact_metrics("budget", before, messages)
```

依次记录 budget、snip、micro、auto。

验收：

- 能看见每层释放多少字符；
- 没有变化的层也有明确 0；
- 落盘文件数单独记录；
- summary 的额外 API 次数可统计。

## 18. 修改实验：修复中等结果预算漏洞

当 total 超预算但所有结果都小于 PERSIST_THRESHOLD，可以继续从大到小落盘：

```python
for _, block in ranked:
    if total <= max_bytes:
        break
    content = str(block.get("content", ""))
    tool_id = block.get("tool_use_id", "unknown")
    block["content"] = persist_output_force(tool_id, content)
    total = recalculate_total(blocks)
```

或者动态选择阈值：

```text
第一阶段：优先落盘 >30000
第二阶段：仍超预算时继续落盘较小结果
第三阶段：如果 marker 本身仍超预算，缩短 preview
```

验收 11 × 20000 的构造用例最终低于 200000。

## 19. 修改实验：可靠的持久化文件名

不要直接把工具 ID 当路径。使用哈希和 UUID：

```python
import hashlib
import uuid


def output_filename(tool_use_id: str, output: str) -> str:
    digest = hashlib.sha256(output.encode("utf-8")).hexdigest()[:16]
    unique = uuid.uuid4().hex
    return f"{unique}-{digest}.txt"
```

写入使用：

```python
path.write_text(output, encoding="utf-8")
```

marker 记录：

```text
tool_use_id
sha256
字符数
UTF-8 字节数
文件路径
```

验收：

- 相同 tool ID 的两次输出不会冲突；
- 文件名不含路径分隔符；
- 哈希可验证内容；
- Unicode 大小记录准确。

## 20. 修改实验：保存压缩前 transcript

如果目标是保留完整原始会话，应在任何破坏性预处理前保存快照。

一种策略：

```python
if should_checkpoint(messages):
    write_transcript(messages, kind="pre_compaction")

messages[:] = tool_result_budget(messages)
messages[:] = snip_compact(messages)
messages[:] = micro_compact(messages)
```

`write_transcript` 使用毫秒/UUID：

```python
path = TRANSCRIPT_DIR / (
    f"transcript_{time.time_ns()}_{uuid.uuid4().hex}.jsonl"
)
```

权衡：

- 更可恢复；
- 磁盘占用更高；
- 原始大输出可能重复存储；
- 敏感信息保留范围扩大。

应配合去重、加密、清理和用户可见策略。

## 21. 修改实验：让摘要兼顾头部与尾部

当前 summary 输入只截前 80000 字符。可按预算组合：

```text
系统要求与首个用户目标：头部
最近工作和未完成项：尾部
中间：由 snip/micro 处理或分段摘要
```

最小实现：

```python
serialized = json.dumps(messages, default=str, ensure_ascii=False)
if len(serialized) > 80000:
    conversation = (
        serialized[:30000]
        + "\n...[middle omitted]...\n"
        + serialized[-50000:]
    )
else:
    conversation = serialized
```

验收：

- 摘要 prompt 同时包含初始目标和最新状态；
- 中间截断明确标记；
- 不在 JSON 字符串任意位置截断造成难读结构，是下一步改进目标。

更好方案应按完整消息组预算，而不是切原始序列化字符串。

## 22. 修改实验：修复主动 compact 的工具配对

最简单的约束是要求 compact 必须是该响应唯一工具：

```python
tool_blocks = [
    block for block in response.content
    if block.type == "tool_use"
]
```

如果混合：

```python
if any(block.name == "compact" for block in tool_blocks) and len(tool_blocks) != 1:
    # 返回错误，不执行任何同批工具
```

单独 compact 时，压缩后重新建立一个合法 pair：

```python
compact_block = tool_blocks[0]
summary_messages = compact_history(messages)
messages[:] = summary_messages
messages.append({
    "role": "assistant",
    "content": [compact_block],
})
messages.append({
    "role": "user",
    "content": [{
        "type": "tool_result",
        "tool_use_id": compact_block.id,
        "content": (
            "[Compacted. Conversation history has been summarized.]"
        ),
    }],
})
continue
```

虽然 compact 请求在摘要前已经出现过，active history 中重新附加一份是为了满足接下来 API 的
工具协议。

另一种更干净的设计是把 manual compact 变成用户命令或 Harness 控制事件，而不是模型工具。

## 23. 修改实验：使用 `focus`

当前 schema 有可选 `focus`，实现忽略。修改：

```python
def summarize_history(messages, focus: str | None = None):
    focus_instruction = (
        f"\nPrioritize this focus: {focus}"
        if focus
        else ""
    )
    ...
```

并把 focus 沿：

```text
compact block.input
→ compact_history
→ summarize_history
```

传递。

还需要：

- 限制 focus 长度；
- 把它当用户/模型输入而非高优先级系统指令；
- 不允许 focus 要求遗漏安全约束；
- summary 固定保留不可丢字段。

## 24. 修改实验：Reactive 使用结构化错误

当前：

```python
"prompt_too_long" in str(error).lower()
```

应优先检查供应商异常类型、HTTP 状态或结构化 error code，再用文本作为兼容 fallback。

伪代码：

```python
def is_prompt_too_long(error: Exception) -> bool:
    code = getattr(error, "code", None)
    status = getattr(error, "status_code", None)
    if code in {"prompt_too_long", "context_length_exceeded"}:
        return True
    if status == 413:
        return True
    text = str(error).lower()
    return any(
        marker in text
        for marker in ("prompt_too_long", "too many tokens")
    )
```

再加入总重试与连续重试指标。

## 25. 本课综合挑战：构建可审计压缩演示

在 `code_experiment.py`：

1. 设置 `CONTEXT_LIMIT = 5000`；
2. 加入每层 before/after 指标；
3. 使用唯一 transcript 文件名；
4. 修复主动 compact 配对；
5. 保留最近尾部的 summary 输入。

运行任务：

```text
Create a todo plan. Read medium-1.txt through medium-5.txt separately. Read
huge.txt with read_file. Record the character count of every file in REPORT.md.
Use compact by itself after the report is written, preserving the report path
and verification state. After compaction, read REPORT.md and verify all counts.
```

验收：

- Todo 计划出现；
- Micro 压缩旧 medium 结果；
- huge 结果落到 `.task_outputs`；
- auto 或 manual compact 保存唯一 transcript；
- compact 后没有孤立 tool_result；
- summary 保留 REPORT.md 路径和待验证状态；
- Agent 能在压缩后重新读取报告；
- 最终报告中 medium 文件均为 500，huge 为 250001；
- 完整流程没有无限重试；
- 指标能解释每层释放了多少空间。

## 26. 常见问题与定位

### 读取巨大文件却没有落盘

检查同一批 user tool results 的总量是否严格大于 200000。PERSIST_THRESHOLD 不是单独触发条件。

### `.task_outputs` 没出现

可能：

- 模型用 Bash 统计而非 read_file；
- 结果总量不够；
- 工作目录不是预期临时目录；
- 工具结果已被其他方式截断。

### 旧结果没有被 micro

必须：

- 总 tool_result block 超过 3；
- 目标旧结果长度大于 120；
- 预处理已经进入下一轮循环。

### 看不到 micro/snip 日志

原始函数不打印。增加 metrics/trace，或离线检查变更后的 messages。

### Auto compact 频繁触发

字符估算阈值过低，summary 后仍然较长，或新结果增长很快。记录压缩前后大小与额外 API 次数。

### Transcript 里没有原始旧结果

自动 transcript 在前三层之后保存。若需要原始快照，提前 checkpoint。

### 主动 compact 后 API 报 tool_result 配对错误

当前 special-case 删除了 assistant tool_use。完成配对修复，或暂时不要使用模型 compact 工具。

### Reactive 一次后仍失败

MAX_REACTIVE_RETRIES=1。第二次连续 prompt-too-long 会抛出。检查保留尾部是否本身过大、summary
是否太长、auto 是否重新增长。

### Catalog 中 agent-builder 描述只有 `|`

S08 使用简单逐行 frontmatter parser，不理解 YAML block scalar。不是技能文件损坏。

## 27. 设计层面的延伸思考

### 压缩是信息策略，不只是字符串操作

每层隐含价值判断：

- 大输出可以落盘；
- 旧工具结果可重新运行；
- 中间对话价值低于头尾；
- 摘要可以代表原历史。

这些假设可能因任务不同而失效。

### 可恢复性需要保存“位置”

只保存原内容不够，模型还要知道：

- 文件在哪里；
- 属于哪个工具调用；
- 内容是否仍有效；
- 怎样重新读取；
- 是否含敏感数据。

Micro 把 persisted pointer 替换后，虽然磁盘文件还在，活跃上下文却可能失去位置。

### 工具消息配对是硬不变量

压缩可以改内容，却不能随意保留 tool_result、删除对应 tool_use。任何裁剪边界、摘要替换和
manual compact 都要运行配对验证。

### Summary 不能成为唯一真相

模型摘要会遗漏或误述。关键状态最好有结构化旁路：

- TODO/Task 存储；
- 修改文件列表；
- 用户约束；
- 权限决策；
- 测试结果；
- transcript。

### 压缩本身也需要预算

把过长历史再次发给 summary 模型可能同样超限。需要分段摘要、旧摘要合并、输入预算和失败熔断。

## 28. 结课自测

不看代码回答：

1. 四层的概念编号和实际执行顺序分别是什么？
2. estimate_size 的单位是什么？
3. Budget 为什么只检查最后一条 user 消息？
4. 总量超过 200000 时，哪些 block 会优先落盘？
5. 11 个 20000 字符结果为什么可能无法降预算？
6. Snip 为什么通常返回 51 条而不是 50？
7. Micro 保留的是最近三条消息还是三个结果 block？
8. Auto transcript 为什么不一定是完整原始会话？
9. summarize_history 为什么可能丢掉最新尾部？
10. Reactive 保留多少尾部，怎样保护工具对？
11. 主动 compact 当前为什么可能产生孤立 result？
12. focus 字段当前是否真正生效？

完成综合挑战、离线配对测试和主动 compact 修复，并正确回答至少 10 题，就可以认为掌握了
S08。

## 29. 完成本课后的状态

你现在拥有：

```text
messages
  → 大结果预算与旁路存储
  → 中段裁剪
  → 旧结果占位
  → 字符预算判断
      ├─ 未超：正常模型调用
      └─ 超出：LLM 摘要替换
  → API 仍拒绝：reactive summary + recent tail
```

压缩让长会话继续，但它会有损地删除或概括信息。用户偏好、长期约束和项目事实不能只依赖
messages 偶然保留。S09 Memory 将把值得长期保存的信息提取到独立存储。

