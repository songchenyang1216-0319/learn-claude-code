# S11 实操教学指南：让 Agent 从失败中恢复

> 对应课程：[s11_error_recovery](../../s11_error_recovery/)
> 核心代码：[code.py](../../s11_error_recovery/code.py)
> 前置课程：[S10 System Prompt](s10-system-prompt.md)
> 建议用时：130–170 分钟
> 本课产物：输出扩容与续写、上下文紧急裁剪、瞬态错误退避和备用模型切换

## 1. 学完这一课，你应该能做到什么

完成 S11 后，你应该能够：

1. 区分正常停止、输出 token 用完、输入上下文超限、限流、过载和永久错误；
2. 说明为什么不同错误不能共用一种重试策略；
3. 逐步推导 8K→64K→最多三次续写的消息变化；
4. 解释为什么第一次截断输出不会追加到历史；
5. 说明 reactive compact 的触发条件、破坏性和实际保留范围；
6. 计算指数退避基础值和 25% 抖动范围；
7. 解释备用模型在第几次 529 后切换、何时恢复主模型；
8. 用假客户端在不调用真实 API 的情况下覆盖每条恢复路径；
9. 识别错误字符串分类、无效 `Retry-After`、最终多睡一次、工具错误未恢复等边界；
10. 将教学版改造成可取消、可测试、可观测且保持消息协议合法的恢复控制器。

本课最重要的一句话是：

> 恢复不是“遇到任何错误就再试一次”，而是先判断失败类型，再执行有上限、可证明有用的状态转换。

## 2. 为什么裸重试不够

下面几种失败看起来都像“调用没成功”，恢复动作却完全不同。

| 失败 | 原因 | 正确方向 |
|---|---|---|
| `stop_reason=max_tokens` | 输出空间不够 | 增大输出预算或续写 |
| prompt too long | 输入历史太大 | 减少输入上下文 |
| 429 | 请求频率或配额受限 | 等待后重试 |
| 529 / overloaded | 服务暂时过载 | 退避、必要时换模型 |
| 认证失败 | 凭证错误 | 停止并提示配置 |
| 参数错误 | 请求本身无效 | 修正请求，不盲目重试 |
| 工具错误 | 本地执行失败 | 让模型修正参数或由工具层恢复 |

如果把所有错误都立即重试：

- 无效请求会重复失败；
- 限流会被更高频率重试放大；
- 过长上下文不会自己变短；
- 被截断的回答会不断从头生成；
- 用户不知道 Agent 是在工作还是卡死；
- 调用费用和延迟变得不可控。

因此恢复逻辑需要：

```text
分类 → 改变状态 → 延迟或压缩 → 重试 → 设上限 → 给出终止结果
```

## 3. 本课的三层控制结构

主循环把错误分成三层处理：

```text
client.messages.create()
        │
        ├─ 抛出 429 / 529
        │      └─ with_retry 内部退避
        │
        ├─ 抛出其他异常
        │      └─ 外层 try/except
        │             ├─ prompt too long → compact 一次
        │             └─ 其他 → 记录错误并退出
        │
        └─ 正常返回 response
               ├─ stop_reason=max_tokens → 扩容/续写
               ├─ stop_reason=tool_use → 执行工具
               └─ 其他 → 正常退出
```

这三个恢复位置分别处理：

- 响应对象中的停止原因；
- 可重试 API 异常；
- 不可重试或需要改变上下文的 API 异常。

## 4. 先认识本课的实际能力边界

S11 仍只有：

```text
bash
read_file
write_file
```

它没有重新加入 S08 的四层压缩管线。`reactive_compact()` 只是保留尾部消息的教学替代。

它也没有处理：

- 网络连接错误；
- DNS 错误；
- 请求超时；
- 5xx 中除字符串匹配 529 之外的状态；
- 流式响应中断；
- 图片尺寸错误；
- 工具执行异常；
- 用户主动取消；
- 进程重启后的恢复状态；
- 幂等键和重复计费识别。

本课重点是恢复状态机的骨架，而不是生产级完整错误目录。

## 5. `RecoveryState` 保存什么

每次进入 `agent_loop()` 都创建：

```python
state = RecoveryState()
```

字段：

| 字段 | 初始值 | 作用 |
|---|---:|---|
| `has_escalated` | `False` | 是否已把输出预算升到 64K |
| `recovery_count` | `0` | 已注入多少次续写提示 |
| `consecutive_529` | `0` | 当前累计的 529 次数 |
| `has_attempted_reactive_compact` | `False` | 是否已经紧急裁剪过 |
| `current_model` | `PRIMARY_MODEL` | 下一次调用使用的模型 |

这些状态的生命周期是：

```text
一次用户问题对应的一次 agent_loop
```

下一次用户输入会重新创建 state：

- 输出预算回到 8000；
- 主模型恢复为 `MODEL_ID`；
- 续写计数归零；
- compact 又有一次机会。

## 6. 路径一总览：输出被截断

输出截断不是异常。SDK 正常返回 response，只是：

```python
response.stop_reason == "max_tokens"
```

处理分两阶段：

```text
第一次 max_tokens
   └─ 丢弃本次截断输出
   └─ max_tokens 8000 → 64000
   └─ 原请求重试

64K 后仍 max_tokens
   └─ 保存截断输出
   └─ 注入续写提示
   └─ 最多 3 次

续写机会用完仍截断
   └─ 保存最后一段截断输出
   └─ 返回
```

这里的“丢弃”只表示不放进 `messages`，响应已经产生的 API 成本不会消失。

## 7. 第一次截断为什么不追加

初始调用：

```python
max_tokens = 8000
```

若截断：

```python
if not state.has_escalated:
    max_tokens = 64000
    state.has_escalated = True
    continue
```

`messages` 完全不变。

这样下一次请求仍是原始任务，只是输出预算变大。优点：

- 模型能从头生成一份连贯答案；
- 不需要猜截断发生在哪个语法位置；
- 避免第一段与续写段重复；
- 工具协议不会因为半截内容被直接拼接而复杂化。

代价：

- 第一次 8K 输出全部浪费；
- 第二次生成可能与第一次不同；
- 长回答从头再算一遍；
- 若模型每次都选择不同策略，结果不可预测；
- provider 不支持 64K 输出时可能转成参数错误。

## 8. 64K 后怎样续写

第二次及以后截断时，程序先保存：

```python
messages.append({
    "role": "assistant",
    "content": response.content,
})
```

如果续写次数还没到 3：

```python
messages.append({
    "role": "user",
    "content": CONTINUATION_PROMPT,
})
```

提示内容：

```text
Output token limit hit. Resume directly — no apology, no recap.
Pick up mid-thought.
```

然后继续用 64K 调用。

这让后续请求能看到已经产生的内容，并从断点继续。

## 9. 一直截断时会调用多少次

若每次 response 都是 `max_tokens`：

| 调用 | 输出预算 | 调用后动作 |
|---:|---:|---|
| 1 | 8000 | 丢弃输出，升级 |
| 2 | 64000 | 保存输出，续写 1/3 |
| 3 | 64000 | 保存输出，续写 2/3 |
| 4 | 64000 | 保存输出，续写 3/3 |
| 5 | 64000 | 保存输出，达到上限并返回 |

最终历史在原用户消息后新增：

```text
assistant part 1
user continuation
assistant part 2
user continuation
assistant part 3
user continuation
assistant part 4
```

第 1 次 8K 的内容不在历史里。

“最多三次续写”不等于“最多三次模型调用”；极端情况下是五次调用。

## 10. 截断计数不会在工具成功后重置

`state` 持续整个 `agent_loop()`。

假设：

1. 发生一次截断并升到 64K；
2. 后来正常返回 tool use；
3. 工具执行成功；
4. 再次发生截断。

此时：

- `has_escalated` 仍为 `True`；
- `max_tokens` 仍为 64000；
- 已用过的 `recovery_count` 不会归零。

所以计数表示“本用户任务累计恢复次数”，不是“连续截断次数”。

这为单个任务提供总预算上限，但变量名和日志没有明确表达这一点。

## 11. 截断响应里的 Tool Use 不会执行

代码先判断：

```python
if response.stop_reason == "max_tokens":
```

之后才走正常 tool use 分支。

如果截断响应 content 中已经含有工具块：

- 第一次截断：整个 response 不进历史，工具不执行；
- 后续截断：response 进历史，但工具仍不执行，随后注入普通 user 续写提示。

这可能留下：

- assistant tool_use 没有配套 tool_result；
- 下一条却是普通 user 文本；
- API 协议校验失败；
- 模型误以为工具已经执行。

真实系统需要在截断、tool use 和消息配对之间做专门协调。本课没有覆盖这一组合。

## 12. 路径二总览：输入上下文过长

API 抛出异常后，外层先调用：

```python
is_prompt_too_long_error(e)
```

第一次识别为过长：

```python
messages[:] = reactive_compact(messages)
state.has_attempted_reactive_compact = True
continue
```

第二次仍过长：

1. 打印不可恢复日志；
2. 在历史中追加一条错误 assistant 消息；
3. 返回。

它只给一次紧急裁剪机会。

## 13. 错误分类使用的是字符串启发式

匹配条件：

```python
("prompt" in msg and "long" in msg)
or "prompt_is_too_long" in msg
or "context_length_exceeded" in msg
or "max_context_window" in msg
```

优点：

- 不绑定某一个 SDK 异常类；
- 可兼容部分代理服务的错误文案；
- 教学代码短。

风险：

- 普通错误只要同时含 prompt 和 long 就可能误判；
- `"context length exceeded"` 使用空格时不一定匹配；
- 没检查 HTTP 状态或结构化错误 code；
- provider 改文案后可能漏判；
- 本地异常文本碰巧匹配也会触发破坏性裁剪。

生产实现应优先读取：

```text
异常类型 → HTTP status → provider error code → 最后才是文案 fallback
```

## 14. `reactive_compact()` 实际做了什么

代码：

```python
tail = messages[-5:]
return [{
    "role": "user",
    "content": (
        "[Reactive compact] Earlier conversation trimmed. "
        "Continue from where you left off."
    ),
}, *tail]
```

它不调用模型，不生成事实摘要。

结果固定是：

```text
一条占位 user 消息
+ 原历史最后最多 5 条
```

它不是 S08 的语义压缩，更像紧急尾部截断。

## 15. Reactive Compact 的五个边界

### 15.1 消息少时反而变长

原来只有 2 条消息：

```text
2 → 占位 1 + 原 2 = 3
```

字符数也会增加。

### 15.2 会破坏 Tool Use 配对

如果最后 5 条从一个 `tool_result` 开始，对应的 assistant tool_use 已被切掉，API 可能拒绝。

也可能保留 assistant tool_use，却切掉紧随其后的 tool_result。

### 15.3 会产生连续同角色

占位消息固定是 user。若尾部第一条也是 user，就形成：

```text
user
user
```

不同 provider 对连续角色的处理不同。

### 15.4 早期事实完全丢失

没有 summary，所以：

- 原始任务目标；
- 约束；
- 已完成步骤；
- 重要路径；
- 用户决定

都可能消失。

### 15.5 它会原地覆盖共享历史

```python
messages[:]
```

改变外层 `history` 同一个列表对象。被删除的消息无法恢复，除非另有 transcript。

## 16. `turn_start` 会因 Compact 变得过期

外层 REPL 在调用前记录：

```python
turn_start = len(history)
```

Reactive compact 可能把长 history 缩到 6 条。

返回后打印：

```python
for msg in history[turn_start:]:
```

如果旧 `turn_start` 是 20，而新列表只有 7 条：

```python
history[20:] == []
```

最终 assistant 回答不会被外层打印。

这是“修改共享列表长度”与“保存旧索引”组合产生的实际 bug。

修复方向：

- `agent_loop()` 直接返回最终可显示文本；
- 用 turn ID 而不是列表下标；
- compact 时保留当前 turn 的边界元数据；
- 展示层订阅事件，不回头切片共享 history。

## 17. Compact 后 System Prompt 没有立即重算

触发裁剪后直接：

```python
continue
```

局部变量 `system` 沿用裁剪前的值。

当前 prompt context 只依赖：

- tools；
- workspace；
- memory index。

所以裁剪 messages 不会改变它，当前实现结果通常没问题。

若未来 prompt section 依赖：

- 消息数量；
- 当前任务；
- 压缩摘要；
- token budget；
- 对话阶段，

compact 后继续用旧 system 就会过期。

## 18. 路径三总览：429 与 529

`with_retry()` 最多循环：

```python
range(MAX_RETRIES)
```

默认 `MAX_RETRIES = 10`。

每次：

1. 执行 `fn()`；
2. 成功则把 `consecutive_529` 归零并返回；
3. 429 则等待后继续；
4. 529 则累计、可能切模型、等待后继续；
5. 其他异常立即重新抛出。

循环结束：

```python
raise RuntimeError("Max retries (10) exceeded")
```

外层把它当作不可恢复错误。

## 19. “10 次重试”实际是 10 次总调用

调用发生在 for 循环内部：

```python
for attempt in range(10):
    fn()
```

因此极端情况下：

```text
总调用次数 = 10
失败后的额外调用次数 = 9
```

不是：

```text
初始 1 次 + 重试 10 次 = 11 次
```

常量和日志把它叫 `MAX_RETRIES`，语义更准确的名称是：

```python
MAX_ATTEMPTS
```

## 20. 指数退避公式

基础延迟：

```python
base = min(500 * (2 ** attempt), 32000) / 1000
```

单位转换后：

| `attempt` | 基础延迟 |
|---:|---:|
| 0 | 0.5 秒 |
| 1 | 1 秒 |
| 2 | 2 秒 |
| 3 | 4 秒 |
| 4 | 8 秒 |
| 5 | 16 秒 |
| 6 | 32 秒 |
| 7+ | 32 秒 |

抖动：

```python
random.uniform(0, base * 0.25)
```

所以总延迟范围：

| 基础 | 最终范围 |
|---:|---:|
| 0.5s | 0.5–0.625s |
| 1s | 1–1.25s |
| 8s | 8–10s |
| 32s | 32–40s |

抖动减少大量客户端同时重试形成的“惊群”。

## 21. 最后一次失败后仍会睡眠

在第 10 次调用失败后，代码仍执行：

```python
time.sleep(delay)
continue
```

然后 for 循环结束并抛错。

这次睡眠后没有下一次请求，所以纯粹增加最终错误延迟。

若 attempt 从 0 到 9，且没有 `Retry-After`：

- 第 10 次基础等待已经达到 32 秒；
- 加抖动最多 40 秒；
- 用户会在注定失败前多等这段时间。

正确分支应在睡前判断：

```python
if attempt == MAX_ATTEMPTS - 1:
    raise
```

## 22. `Retry-After` 只写在函数签名里，没有接入异常

`retry_delay()` 支持：

```python
retry_delay(attempt, retry_after=...)
```

且真值非零时优先返回。

但 `with_retry()` 实际调用始终是：

```python
retry_delay(attempt)
```

它没有从异常 response headers 解析 `Retry-After`。

所以 README 所描述的“服务器值优先”，在当前 Agent 路径中没有真正发生。

另外：

- `retry_after=0` 因 `if retry_after` 被当作未提供；
- 字符串值若原样返回，`time.sleep()` 会报类型错误；
- HTTP date 形式需要专门解析；
- 秒数应设置合理上限。

## 23. 429 的识别规则

任一条件成立：

```python
"ratelimit" in type(e).__name__.lower()
or "429" in str(e).lower()
```

例如：

- `RateLimitError`；
- `MyRateLimitException`；
- 消息 `"HTTP 429"`。

可能误判：

```text
Validation failed: field 429 is invalid
```

因为只搜字符串 `"429"`。

429 分支不会修改 `consecutive_529`。

## 24. 529 的识别规则

任一条件成立：

```python
"overloaded" in exception class name
or "529" in message
or "overloaded" in message
```

但 429 分支写在它前面。

如果同一个异常字符串同时含 429 和 overloaded，会按 429 处理，不累计 529。

## 25. “连续 529”在当前实现中并不严格连续

计数只在两种情况下清零：

1. 调用成功；
2. 达到 3 次并执行“切换或无 fallback”的分支。

429 不会清零。

序列：

```text
529 → 429 → 529 → 529
```

仍会在最后一次触发 3 次 529 阈值。

所以当前字段实际表达：

> 自最近一次成功或阈值重置以来累计的 529 数量。

若要严格连续，在处理任何非 529 结果时都应归零。

## 26. 备用模型何时真正生效

第三次累计 529 时：

```python
state.current_model = FALLBACK_MODEL
```

当前失败的请求已经结束。

下一轮 `fn()` 再执行 lambda 时读取：

```python
model=state.current_model
```

因此备用模型从下一次调用生效。

如果没有配置 `FALLBACK_MODEL_ID`：

- 计数仍会清零；
- 打印没有备用模型；
- 继续使用主模型重试。

如果配置了：

- 同一 `agent_loop()` 后续一直使用备用模型；
- 成功不会切回主模型；
- 下一次新的用户问题创建新 state，重新使用主模型。

## 27. 备用模型切换没有同步能力约束

主模型与备用模型可能不同：

- 最大输出 token；
- 上下文窗口；
- 工具调用格式；
- 可用地区；
- 价格；
- 延迟；
- 推理能力。

当前切换只改：

```python
model
```

没有重新计算：

- `max_tokens`；
- System Prompt；
- 工具集；
- provider client；
-模型能力标记；
- 缓存边界。

如果备用模型不支持 64000 输出，之前的截断扩容可能让它立即失败。

## 28. 错误恢复只包住模型调用

被 try/except 包住的是：

```python
with_retry(lambda: client.messages.create(...))
```

工具执行发生在后面：

```python
output = handler(**block.input)
```

若 handler 抛出异常，主循环不会分类恢复。

当前 `run_read` 和 `run_write` 自己捕获大多数异常并返回字符串；`run_bash` 的实际行为依赖
`shell_runner`。

其他未被恢复层包住的操作还包括：

- `update_context()` 读取 Memory index；
- `get_system_prompt()` 组装；
- response content 遍历；
- 工具 handler 参数不匹配；
- REPL 最终输出遍历。

“Agent 有错误恢复”不等于整个进程的每个阶段都有恢复。

## 29. 不可恢复错误怎样进入历史

普通不可恢复异常：

```python
messages.append({
    "role": "assistant",
    "content": [{
        "type": "text",
        "text": f"[Error] {name}: ...",
    }],
})
```

这里 content block 是普通 `dict`。

但外层显示时使用：

```python
getattr(block, "type", None)
```

普通字典没有 `.type` 属性，因此错误历史不会被最终打印循环渲染。

用户仍会看到前面 `print()` 的红色日志，但不会看到保存到 history 的 `[Error] ...` 文本。

这是 SDK `TextBlock` 对象与字典 block 表示混用造成的边界。

## 30. S10 Prompt 机制在 S11 中发生了一个回退

S10 的 tools 与 workspace 从 context 动态生成。

S11 改成：

```python
PROMPT_SECTIONS = {
    "tools": "Available tools: bash, read_file, write_file.",
    "workspace": f"Working directory: {WORKDIR}",
}
```

`assemble_system_prompt()` 固定读取这两个字符串，只从 context 动态读取 memories。

因此：

- 修改 `context["enabled_tools"]` 不会改变 prompt 工具段；
- 修改 `context["workspace"]` 不会改变工作区段；
- 但这两个字段仍在 JSON 缓存键里；
- 它们变化会造成一次无意义 cache miss；
- `PROMPT_SECTIONS["memory"]` 也定义了，但实际组装没有使用这个模板。

README 说从 S10 “synced”，机制意图相同，但实现并非逐行保持。

这是阅读递进式课程时很有价值的练习：始终以当前章代码为准。

## 31. 运行前准备隔离目录

本课仍有 Bash，没有权限确认。请用临时目录。

### 31.1 Windows PowerShell

```powershell
cd D:\Projects\learn-claude-code
$lab = Join-Path $env:TEMP "learn-claude-s11"
New-Item -ItemType Directory -Force $lab | Out-Null
Set-Location $lab
$env:PYTHONUTF8 = "1"
$env:MODEL_ID = "你的主模型 ID"
$env:FALLBACK_MODEL_ID = "可选备用模型 ID"
$env:ANTHROPIC_API_KEY = "你的 API Key"
& "D:\Projects\learn-claude-code\.venv\Scripts\python.exe" `
  "D:\Projects\learn-claude-code\s11_error_recovery\code.py"
```

### 31.2 macOS / Linux

```bash
LAB_DIR="$(mktemp -d)"
cd "$LAB_DIR"
export MODEL_ID="你的主模型 ID"
export FALLBACK_MODEL_ID="可选备用模型 ID"
export ANTHROPIC_API_KEY="你的 API Key"
/path/to/learn-claude-code/.venv/bin/python \
  /path/to/learn-claude-code/s11_error_recovery/code.py
```

真实 429、529 和 prompt too long 都不容易稳定制造。核心恢复逻辑应优先用后面的假客户端离线验证。

## 32. 最小成功路径：正常请求不触发恢复

输入：

```text
Create hello.txt containing hello, then read it back.
```

正常路径应看到：

1. assembled；
2. `write_file`；
3. cache hit；
4. `read_file`；
5. cache hit；
6. 最终回答。

不应看到：

```text
[max_tokens]
[429 rate limit]
[529 overloaded]
[reactive compact]
[unrecoverable]
```

验收：

- 恢复机制没有改变正常工具循环；
- response 正常追加；
- tool use 与 tool result 成对；
- 文件实际存在于临时目录。

## 33. 为什么不要靠真实模型强行制造截断

课程 README 建议请求很长代码，但结果不稳定：

- 模型可能提前简化；
- provider 可能限制 `max_tokens=64000`；
- 任务可能在 8K 内完成；
- 调用成本很高；
- 输出大量无意义文本；
- 真实 API 失败难以重复。

更好的测试方式：

```text
Fake client → 预设 response 序列 → 观察调用参数和 messages
```

这样可以精确覆盖所有分支，无网络、无费用、可重复。

## 34. 离线验证退避公式

设置占位环境变量后：

```python
import s11_error_recovery.code as c

c.random.uniform = lambda low, high: 0

print(c.retry_delay(0))  # 0.5
print(c.retry_delay(1))  # 1.0
print(c.retry_delay(2))  # 2.0
print(c.retry_delay(6))  # 32.0
print(c.retry_delay(9))  # 32.0
print(c.retry_delay(4, retry_after=7))  # 7
```

验收：

- attempt 从 0 开始；
- 第 7 个基础值到达 32 秒上限；
- 关闭随机抖动后数值精确；
- 显式非零 `retry_after` 覆盖公式。

测试后要恢复或重新导入模块，避免 monkey patch 污染其他实验。

## 35. 离线验证 529 切换模型

构造函数依次：

```text
529 → 529 → 529 → success
```

并让：

```python
c.time.sleep = lambda seconds: None
c.FALLBACK_MODEL = "fallback-test"
```

每次调用记录 `state.current_model`。

预期：

```text
调用 1：primary
调用 2：primary
调用 3：primary
第三次失败后切换
调用 4：fallback-test
```

最终：

```python
state.current_model == "fallback-test"
state.consecutive_529 == 0
```

成功分支会再次把 529 计数归零。

## 36. 离线验证 429 不清空 529

预设：

```text
529 → 429 → 529 → 529 → success
```

预期仍触发备用模型。

这证明当前计数不是严格“连续”。

若你修改为严格连续，预期应变为：

- 429 时清零；
- 上述序列不切换；
- 必须真正连续三次 529 才切。

## 37. 离线验证耗尽后多睡一次

临时改：

```python
c.MAX_RETRIES = 2
```

让函数永远抛出 `"429"`，并记录：

```python
c.time.sleep = lambda seconds: sleeps.append(seconds)
```

当前预期：

```text
fn 调用次数：2
sleep 调用次数：2
最终：RuntimeError("Max retries (2) exceeded")
```

理想实现应是：

```text
fn 调用次数：2
sleep 调用次数：1
```

因为第二次失败后已经没有下一次调用。

## 38. 离线验证一直截断

Fake response：

```python
from types import SimpleNamespace as S

responses = [
    S(
        stop_reason="max_tokens",
        content=[S(type="text", text=f"part {i}")],
    )
    for i in range(5)
]
```

Fake client 每次弹出一个 response，并记录：

```python
(max_tokens, len(messages))
```

预期记录：

```text
(8000, 1)
(64000, 1)
(64000, 3)
(64000, 5)
(64000, 7)
```

它精确证明：

- 第一次历史长度没有增加；
- 后三次续写每次增加 assistant + user 两条；
- 最后一次只增加 assistant 后返回。

## 39. 离线验证 Reactive Compact

准备 8 条有编号的消息：

```python
messages = [
    {"role": "user", "content": f"m{i}"}
    for i in range(8)
]

result = c.reactive_compact(messages)
```

预期：

```text
len(result) == 6
result[0] 是 reactive compact 占位
result[1:] 对应 m3...m7
```

再只给 2 条：

```text
len(result) == 3
```

证明“compact”不保证消息数变少。

## 40. 八个观察实验

### 实验 1：成功会清空 529

序列：

```text
529 → success
```

预期 `consecutive_529 == 0`。

### 实验 2：普通错误不重试

让 fn 抛：

```python
ValueError("bad request")
```

预期调用一次就重新抛出。

### 实验 3：错误文本含 429 会误入退避

抛：

```python
ValueError("field 429 is invalid")
```

预期当前代码按 rate limit 处理。

### 实验 4：`retry_after=0` 不优先

```python
retry_delay(0, 0)
```

预期返回 0.5 秒加抖动，而不是 0。

### 实验 5：备用模型只在当前任务保留

一个 `agent_loop()` 切换后结束，再开始新用户任务。

预期新 `RecoveryState` 又使用主模型。

### 实验 6：Context 字段变化不改变静态工具段

直接组装两个 context：

```python
{"enabled_tools": ["x"], "workspace": "one"}
{"enabled_tools": [], "workspace": "two"}
```

预期 prompt 相同，只要 memories 相同。

### 实验 7：第二次 prompt too long 终止

Fake client 连续抛两次：

```text
context_length_exceeded
```

预期第一次 compact，第二次追加错误并返回。

### 实验 8：Compact 会让旧 `turn_start` 失效

用长度 20 的 history 记录 `turn_start=20`，compact 后追加一条 assistant。

预期 `history[20:]` 为空，展示层漏掉回答。

## 41. 修改实验：结构化错误分类

定义统一类别：

```python
from enum import Enum

class ErrorKind(Enum):
    RATE_LIMIT = "rate_limit"
    OVERLOADED = "overloaded"
    PROMPT_TOO_LONG = "prompt_too_long"
    AUTH = "auth"
    INVALID_REQUEST = "invalid_request"
    NETWORK = "network"
    UNKNOWN = "unknown"
```

分类优先级：

```python
def classify_error(exc) -> ErrorKind:
    status = getattr(exc, "status_code", None)
    body = getattr(exc, "body", None) or {}
    code = body.get("error", {}).get("type")

    if status == 429:
        return ErrorKind.RATE_LIMIT
    if status == 529:
        return ErrorKind.OVERLOADED
    if code in {
        "prompt_is_too_long",
        "context_length_exceeded",
    }:
        return ErrorKind.PROMPT_TOO_LONG
    # 再处理认证、网络，最后才用文案 fallback
    return ErrorKind.UNKNOWN
```

验收：

- 字段值 `429` 不再误判；
- SDK 类型变化仍可通过 status/code 判断；
- 每个类别有独立单元测试；
- UNKNOWN 不盲目重试。

## 42. 修改实验：正确解析 `Retry-After`

先提取 header：

```python
def get_retry_after(exc) -> float | None:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", {}) or {}
    raw = headers.get("retry-after")
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return max(0.0, min(value, 120.0))
```

调用：

```python
delay = retry_delay(
    attempt,
    retry_after=get_retry_after(e),
)
```

并把判断改为：

```python
if retry_after is not None:
```

验收：

- `"3.5"` 解析为 3.5；
- `0` 有效；
- 负数归零；
- 异常字符串 fallback；
- 超大值受上限控制；
- 若 provider 使用 HTTP date，再实现日期解析。

## 43. 修改实验：最后一次失败不再等待

把尝试和重试语义写清楚：

```python
MAX_ATTEMPTS = 10

for attempt in range(MAX_ATTEMPTS):
    try:
        return fn()
    except Exception as exc:
        if not is_transient(exc):
            raise
        if attempt == MAX_ATTEMPTS - 1:
            raise MaxAttemptsExceeded() from exc
        delay = ...
        sleep(delay)
```

验收：

- 永远失败时调用 10 次；
- 只 sleep 9 次；
- 最终异常保留原始异常为 `__cause__`；
- 日志使用 `attempt 1/10` 而不是含混的 retry；
- 成功后立即返回。

## 44. 修改实验：严格连续的 529

原则：

```python
if kind == OVERLOADED:
    state.consecutive_529 += 1
else:
    state.consecutive_529 = 0
```

成功也清零。

测试序列：

| 序列 | 是否切换 |
|---|---|
| 529, 529, 529 | 是 |
| 529, 429, 529, 529 | 否 |
| 529, success, 529, 529 | 否 |
| 529, 529, 429, 529 | 否 |

如果产品定义本来就是“窗口内累计”，则不要叫 consecutive，应改名并定义时间窗口。

## 45. 修改实验：可取消的等待

直接 `time.sleep(32)` 会阻塞当前线程，期间 Agent 无法正常响应取消状态。

一种同步实现：

```python
def interruptible_sleep(seconds, cancelled):
    deadline = time.monotonic() + seconds
    while True:
        if cancelled():
            raise UserCancelled()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(0.25, remaining))
```

异步程序应使用：

```python
await asyncio.sleep(delay)
```

并正确传播 cancellation。

验收：

- 10 秒退避能在 250ms 左右响应取消；
- 取消不会被归类为 UNKNOWN 并写成失败；
- 不再发下一次请求；
- 展示明确的 cancelled 状态。

## 46. 修改实验：保持 Tool Use 配对的 Compact

不要直接切最后 5 条。先识别消息组：

```text
普通 user + assistant
或
assistant(tool_use) + user(tool_result)
```

从尾部选择完整组，绝不从 pair 中间切。

保留：

1. 当前用户任务；
2. 最近完整工具轮；
3. 一条结构化 summary；
4. 当前未完成目标。

最低验收：

- 每个 tool_result 都能找到此前 tool_use_id；
- 每个保留的 tool_use 都有结果，除非它确实待执行；
- 第一条历史不会是孤立 tool_result；
- compact 后调用 provider 不报消息协议错误。

可复用 S08 指南中的 pairing 验证思路。

## 47. 修改实验：只有真的变小时才提交 Compact

先计算近似字符数：

```python
def message_chars(messages):
    return len(json.dumps(
        messages,
        ensure_ascii=False,
        default=str,
    ))
```

然后：

```python
candidate = reactive_compact(messages)
if message_chars(candidate) >= message_chars(messages):
    raise CompactMadeNoProgress()
messages[:] = candidate
```

验收：

- 2 条短消息不会被“压缩”成 3 条；
- 长消息显著缩小；
- 无进展时不重复同一恢复；
- 日志记录 before/after 字符数；
- 更严谨的系统使用 provider token 估算而不是字符数。

## 48. 修改实验：保存可恢复的摘要

紧急摘要至少包含：

```markdown
## User goal
## Constraints
## Files changed
## Commands and results
## Pending work
## Last error
```

生成后先验证：

- 非空；
- 包含当前目标；
- 未超过预算；
- 不含明显密钥；
- 工具配对合法；
- summary 生成失败时保留原历史。

然后使用事务式替换：

```python
old = list(messages)
candidate = build_compacted_messages(old)
validate(candidate)
messages[:] = candidate
```

不要边生成边破坏原历史。

## 49. 修改实验：恢复状态机显式化

当前多个 `continue` 分散在主循环。

可以返回 transition：

```python
class Transition(Enum):
    RETRY_SAME = "retry_same"
    RETRY_COMPACTED = "retry_compacted"
    CONTINUE_OUTPUT = "continue_output"
    RUN_TOOLS = "run_tools"
    COMPLETE = "complete"
    FAIL = "fail"
```

每次 response/error 转为：

```python
decision = recovery.decide(event, state)
```

好处：

- 状态变化集中；
- 可以表驱动测试；
- 日志和指标统一；
- 避免遗漏某个 `continue` 前的必要更新；
- 更容易证明存在终止上限。

## 50. 修改实验：恢复预算而不是分散常量

定义：

```python
@dataclass
class RecoveryBudget:
    max_attempts: int = 10
    max_total_wait_seconds: float = 90
    max_continuations: int = 3
    max_compactions: int = 1
    max_model_switches: int = 1
```

每次动作消耗预算。

即使每类分别没超限，总预算也能防止组合爆炸：

```text
多次 529
+ 一次 compact
+ 一次扩容
+ 三次续写
```

验收：

- 超过总等待时间立即停止；
- 日志显示剩余预算；
- 用户取消优先于预算；
- 单次用户任务有确定的最坏调用次数。

## 51. 修改实验：修复错误消息显示

展示函数同时支持对象和字典：

```python
def block_type(block):
    if isinstance(block, dict):
        return block.get("type")
    return getattr(block, "type", None)

def block_text(block):
    if isinstance(block, dict):
        return block.get("text", "")
    return getattr(block, "text", "")
```

或者统一内部内容表示，进入历史前全部转成字典，展示和工具解析都按字典处理。

验收：

- 正常 SDK TextBlock 能显示；
- 本地错误 dict 能显示；
- Reactive compact 最终错误能显示；
- 未知 block 不崩溃；
- 不依赖 `turn_start` 的旧下标。

## 52. 修改实验：模型切换时重新校验能力

建立 model profile：

```python
MODEL_PROFILES = {
    "primary": {
        "max_output_tokens": 64000,
        "supports_tools": True,
    },
    "fallback": {
        "max_output_tokens": 16000,
        "supports_tools": True,
    },
}
```

切换时：

```python
max_tokens = min(
    max_tokens,
    profile["max_output_tokens"],
)
```

还应验证：

- 上下文窗口；
- 工具能力；
- 图片能力；
- System Prompt cache 配置；
- provider/client 是否相同；
- 数据驻留与权限策略。

验收：备用模型不会收到超出它限制的请求。

## 53. 扩展实验：区分请求安全与业务幂等

重试模型请求通常不会直接再次修改文件，因为工具调用只有在响应成功返回后才执行。

但仍可能：

- provider 已生成并计费，客户端只丢了响应；
- 流式响应已经展示一部分，再重试造成重复 UI；
- 某些服务端工具在模型调用内部有副作用；
- 用户看到两份相似文本；
- tracing 记录多个逻辑重复请求。

应为每个逻辑模型调用记录：

```text
turn_id
logical_request_id
attempt
model
reason
```

如果 provider 支持幂等键，再按其协议使用。

## 54. 扩展实验：恢复指标

至少记录：

- 正常请求数；
- 429、529 次数；
- 每类尝试次数；
- 累计等待时长；
- compact before/after token；
- 输出扩容次数；
- 续写次数；
- 模型切换次数；
- 最终恢复成功率；
- 最终失败类别；
- 取消次数。

不要只统计“最后成功”。一次任务先重试 9 次再成功，用户体验和成本都可能已经不可接受。

## 55. 扩展实验：可重复的随机抖动测试

生产使用真实随机数，测试注入：

```python
def retry_delay(attempt, retry_after=None, rand=None):
    rand = rand or random.uniform
    ...
    return base + rand(0, base * 0.25)
```

测试：

```python
zero = lambda low, high: low
max_ = lambda low, high: high
```

分别验证区间下界和上界。

不要在测试里真的 sleep。把 sleeper 也作为依赖注入：

```python
with_retry(fn, state, sleep=fake_sleep)
```

## 56. 本课综合挑战：实现可验证的恢复控制器

最低要求：

1. 使用结构化 error kind；
2. `MAX_ATTEMPTS` 语义准确；
3. 最后一次失败后不 sleep；
4. 正确解析并限制 `Retry-After`；
5. 529 严格连续计数；
6. 备用模型切换时校验输出上限；
7. compact 保持完整 tool pair；
8. compact 只有在 token/字符确实减少时提交；
9. 截断 tool use 不产生孤立消息；
10. 展示层能显示对象和字典 block；
11. 等待可取消；
12. 每个任务有总恢复预算；
13. 所有测试使用 fake client 和 fake sleep；
14. 记录 transition、attempt、wait 和最终结果。

建议测试场景：

```text
正常完成
一次 429 后成功
一直 429
三次连续 529 后 fallback 成功
529/429 交错
普通 400
第一次 max_tokens 后正常
一直 max_tokens
第一次 prompt too long 后成功
第二次仍过长
compact 无进展
compact 切在 tool pair 边界
等待中取消
```

最终验收：

- 每条路径都有确定终止条件；
- 不依赖真实网络；
- 不会无效等待；
- 不会破坏历史协议；
- 状态变化有日志；
- 正常路径不受影响。

## 57. 常见问题与定位

### 一运行就报 `MODEL_ID`

模块顶层读取 `os.environ["MODEL_ID"]`。配置主模型；离线导入也需占位值。

### 配了备用模型但没切换

需要在同一个 `agent_loop()` 的 `with_retry()` 中累计三次 529。

下一次用户任务会重置为主模型。

### 429 后等待很久

指数退避最高基础 32 秒，加抖动最多 40 秒。当前最后一次失败后还会多等一次。

### 服务端返回 `Retry-After` 但日志不是该值

当前 `with_retry()` 没有提取 header，只使用本地公式。

### 上下文只有几条却 compact 后仍太长

Reactive compact 保留最后 5 条的完整内容；单条消息可能本身非常大，而且还新增占位消息。

### Compact 后最终回答没打印

检查外层旧 `turn_start` 是否大于压缩后的 history 长度。

### 第二次 Context Too Long 直接退出

这是设计上限：每个 `agent_loop()` 只允许一次 reactive compact。

### 64K 参数被 provider 拒绝

当前代码没有按模型能力限制 `ESCALATED_MAX_TOKENS`。将其改为模型 profile 的上限。

### 错误被保存但下一轮看不到

本地错误 content 是 dict block，展示代码只读取对象属性 `.type`。

### 工具异常导致程序崩溃

本课恢复层只包住 API 调用，没有包住 handler。

### 明明中间有 429，仍触发三次“连续”529

当前 429 不清空 `consecutive_529`，所以它是累计而非严格连续。

### Prompt 的工具列表没随 context 改变

S11 把 tools/workspace section 静态化了，只有 memories 仍从 context 动态读取。

## 58. 设计层面的延伸思考

### 恢复必须有进展证明

每次重试前都应回答：

> 哪个状态改变了，使下一次有理由成功？

示例：

- 429：时间过去了；
- 529：时间过去或模型切换；
- too long：输入变小；
- max tokens：输出预算变大或已提供断点。

如果什么都没变，重复请求通常只是浪费。

### 上限应覆盖组合路径

每条路径单独有限，不代表组合后成本有限得合理。需要总调用、总等待、总 token 和总时间预算。

### 恢复状态是用户体验的一部分

长退避期间应让用户知道：

- 发生了什么；
- 第几次尝试；
- 下次等待多久；
- 是否切换模型；
- 怎样取消。

但不要把内部异常、密钥或完整 prompt 泄露到日志。

### 压缩是有损状态迁移

它不是普通数组切片。需要：

- 保留目标；
- 保留协议结构；
- 校验结果；
- 有回滚；
- 记录丢失范围。

### Fallback 不是免费等价替换

备用模型的能力、成本、地区和行为可能不同。切换是产品决策，不只是改字符串。

### 错误分类应随 Provider 适配层实现

主循环不应理解每家 provider 的所有文案。适配层应把错误规范化成稳定类别。

### 可恢复不代表应该自动恢复

有些操作成本高、敏感或接近外部副作用时，即使技术上可重试，也可能需要用户确认。

## 59. 结课自测

不看代码，回答：

1. 为什么 max tokens 是 stop reason，不是异常？
2. 第一次 8K 截断为什么不进 messages？
3. 一直截断最多会调用几次模型？
4. 最终会保存几段 64K 截断输出？
5. `recovery_count` 是连续计数还是整次任务累计？
6. 截断 response 含 tool use 时当前代码会执行吗？
7. Reactive compact 最多保留多少条旧消息？
8. 为什么 2 条消息 compact 后会变成 3 条？
9. 如何判断 compact 是否切坏 tool pair？
10. 为什么旧 `turn_start` 会漏掉最终回答？
11. Prompt too long 最多 compact 几次？
12. 当前字符串分类可能怎样误判？
13. attempt=0、1、6 的基础延迟分别是多少？
14. 抖动最大占基础延迟多少？
15. `MAX_RETRIES=10` 表示总调用 10 还是 11？
16. 为什么最后一次失败后等待没有意义？
17. 当前 Agent 实际使用服务器 `Retry-After` 了吗？
18. 529、429、529、529 是否触发 fallback？为什么？
19. fallback 在第三次失败的当前请求还是下一请求生效？
20. 新用户任务是否继续使用 fallback？
21. 工具 handler 异常是否被 `with_retry()` 捕获？
22. 本地错误 block 为什么不被展示代码打印？
23. S11 的 tools/workspace 是否仍完全从 context 动态生成？
24. 怎样让退避测试既快又确定？
25. 什么叫“每次恢复必须有进展证明”？

如果你能回答至少 21 题，并完成综合挑战，就真正掌握了本课。

## 60. 完成本课后的状态

你现在拥有：

```text
LLM 请求
  ├─ 429 / 529 → 指数退避
  │                  └─ 529 达阈值 → 备用模型
  ├─ prompt too long → 尾部裁剪一次
  ├─ max_tokens → 8K 升 64K
  │                  └─ 最多三次续写
  ├─ tool_use → 正常执行工具
  └─ 其他 → 错误记录并结束
```

也应该清楚教学实现还缺少：

- 结构化异常类型；
- 真正接入的 `Retry-After`；
- 最终无效等待修复；
- 严格连续 529；
- 可取消 sleep；
- 工具层恢复；
- 合法的 compact 消息配对；
- 语义摘要；
- 多路径总预算；
- fallback 能力检查；
- 可靠错误展示；
- 完整可观察性。

下一课 S12 会从“一次用户任务的恢复”转向“跨回合持久存在的任务图”：任务状态、依赖关系、
领取、更新和磁盘持久化。
