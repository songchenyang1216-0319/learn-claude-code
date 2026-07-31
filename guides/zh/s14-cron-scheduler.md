# S14 实操教学指南：让 Agent 按时间自动开始工作

> 对应课程：[s14_cron_scheduler](../../s14_cron_scheduler/)
> 核心代码：[code.py](../../s14_cron_scheduler/code.py)
> 前置课程：[S13 Background Tasks](s13-background-tasks.md)
> 建议用时：140–180 分钟
> 本课产物：五段式 cron 解析、独立调度线程、待执行队列、空闲交付线程和持久 Job 定义

## 1. 学完这一课，你应该能做到什么

完成 S14 后，你应该能够：

1. 区分手动工具调用、后台执行和定时触发三个层次；
2. 解释 Scheduler、Queue、Queue Processor、Consumer 四层职责；
3. 手工判断五段式 cron 表达式是否匹配给定本地时间；
4. 说明 day-of-month 与 day-of-week 同时受限时的 OR 语义；
5. 验证 `*`、`*/N`、整数、范围和列表的实际支持范围；
6. 说明 `durable=True` 只持久化定义，不保证应用关闭时仍执行；
7. 解释一分钟去重、重启重复、漏过时间和夏令时的边界；
8. 识别 Job ID 碰撞、非原子持久化、取消后队列仍交付和一次性任务丢失风险；
9. 说明 `agent_lock` 怎样避免两个 Agent Turn 同时修改共享历史；
10. 把教学版扩展成有时区、原子存储、leader lock、交付确认、补偿执行和安全策略的调度器。

本课最重要的一句话是：

> 调度器只负责在时间到达时“生产一份待办工作”，真正执行应通过队列交给受控的 Agent 消费者。

## 2. 三个时间层次

### 2.1 手动调用

```text
用户现在说“运行测试”
```

触发者是用户。

### 2.2 后台执行

```text
用户现在触发，但命令不阻塞主循环
```

触发时间仍是现在，只是执行方式异步。

### 2.3 定时调度

```text
现在注册“每天 9 点运行测试”
未来到点自动产生工作
```

触发者是时钟。

它们可以组合：

```text
cron 到点
  → Agent 收到 Scheduled 消息
  → Agent 决定调用 Bash
  → Bash 选择后台运行
```

Cron Job 不等于后台进程；它只是未来工作的定义。

## 3. 本课的实际能力边界

S14 继承：

- S12 简化 Task System；
- S13 简化 Background Task；
- System Prompt 和 Memory index；
- Bash、读写文件；
- 共 8 个已有工具。

新增：

```text
schedule_cron
list_crons
cancel_cron
```

总工具数为 11。

仍未包含 S11 的完整错误恢复，且继承 S12/S13 已知的存储、并发、后台结果边界。

本课调度器只有进程运行时才工作。它不是操作系统服务。

## 4. 四层架构

```text
┌──────────────────────────────┐
│ Scheduler                    │
│ 每秒检查 cron，生产到期 Job  │
└──────────────┬───────────────┘
               ▼
┌──────────────────────────────┐
│ Queue                        │
│ cron_queue 暂存到期 Job      │
└──────────────┬───────────────┘
               ▼
┌──────────────────────────────┐
│ Queue Processor              │
│ 每 0.2 秒检查，Agent 空闲则交付│
└──────────────┬───────────────┘
               ▼
┌──────────────────────────────┐
│ Consumer / agent_loop        │
│ 注入 [Scheduled] prompt      │
└──────────────────────────────┘
```

分层的价值：

- 调度线程不调用模型；
- 调度线程不执行 Bash；
- Agent 忙时 Queue 可以积压；
- Consumer 不负责计算时间；
- 将来可以替换任意一层。

## 5. `CronJob` 数据结构

```python
@dataclass
class CronJob:
    id: str
    cron: str
    prompt: str
    recurring: bool
    durable: bool
```

| 字段 | 作用 |
|---|---|
| `id` | 注册、取消和去重标识 |
| `cron` | 五段式时间表达式 |
| `prompt` | 触发时作为 user 消息注入 |
| `recurring` | 是否每个匹配分钟都继续触发 |
| `durable` | 是否写入磁盘 |

当前没有：

- created time；
- timezone；
- enabled；
- expires；
- last run；
- next run；
- retry；
- owner；
- max runs；
- misfire policy；
-安全权限。

## 6. Cron 的五个字段

```text
minute hour day-of-month month day-of-week
```

范围：

| 字段 | 范围 |
|---|---|
| minute | 0–59 |
| hour | 0–23 |
| day-of-month | 1–31 |
| month | 1–12 |
| day-of-week | 0–6，0=星期日 |

示例：

```text
* * * * *       每分钟
0 9 * * *       每天 09:00
*/5 * * * *     分钟值能被 5 整除
0 9 * * 1-5     周一到周五 09:00
0 0 1 * *       每月 1 日 00:00
```

它没有秒字段。

## 7. 单字段实际支持的语法

### 7.1 通配符

```text
*
```

任意值匹配。

### 7.2 从零起的步长

```text
*/5
```

实现是：

```python
value % 5 == 0
```

### 7.3 列表

```text
1,3,5
```

每个子项递归使用同一 matcher。

### 7.4 闭区间

```text
1-5
```

包括两端。

### 7.5 单整数

```text
17
```

值相等才匹配。

## 8. 当前不支持的 Cron 语法

不支持：

- `1-10/2` 范围步长；
- `5/10` 起点步长；
- `JAN`、`MON` 名称；
- `L` 最后一天；
- `W` 最近工作日；
- `?` 未指定；
- `#` 第几个星期；
- 环绕范围 `22-2`；
- day-of-week 的 `7=Sunday`；
- 秒字段；
- 年字段。

例如：

```text
1-5/2 * * * *
```

会返回：

```text
minute: Invalid range
```

## 9. Step 校验没有字段上限

校验只要求：

```python
step > 0
```

所以：

```text
*/999 * * * *
```

是合法表达式。

在分钟字段，0 是唯一能被 999 整除的合法值，因此实际相当于每小时第 0 分钟。

在 day-of-month 中取值 1–31，没有 0，`*/999` 永远不匹配。

合法语法不代表一定存在触发时间。成熟实现应能计算 next run，发现“永不触发”或极端表达式。

## 10. 星期转换

Python：

```text
Monday=0 ... Sunday=6
```

Cron：

```text
Sunday=0, Monday=1 ... Saturday=6
```

转换：

```python
(dt.weekday() + 1) % 7
```

本课拒绝有些 cron 实现接受的 Sunday=7。

## 11. DOM/DOW 的 OR 语义

分钟、小时、月份必须全部匹配。

day-of-month 和 day-of-week：

- 两者都是 `*` → 通过；
- DOM=`*` → 只看 DOW；
- DOW=`*` → 只看 DOM；
- 两者都受限 → 任一匹配即可。

表达式：

```text
0 9 1 * 1
```

含义是：

```text
每月 1 日，或者每个周一，09:00
```

不是：

```text
恰好落在周一的每月 1 日
```

这是常见误解。

## 12. “未受限”只认精确的 `*`

代码判断：

```python
dom == "*"
dow == "*"
```

`*/1` 虽然数学上匹配所有值，语法上却被视为“受限”。

例如：

```text
0 9 */1 * 1
```

DOM 每天都匹配，DOM/DOW 进入 OR，结果是每天 9 点，而不是只在周一。

这体现了 cron 语义中“表达式形式”与“匹配集合”等价性不总是同一回事。

## 13. `validate_cron()` 与 `cron_matches()` 的分工

注册前：

```python
validate_cron()
```

负责：

- 五字段数量；
- 数字格式；
- 范围边界；
- step 非零；
- range 起点不大于终点。

`cron_matches()` 假设表达式大体有效。

直接把：

```text
*/x * * * *
```

传给 matcher，`int("x")` 会抛异常。

调度线程对每个 Job 有 try/except，因此单 Job 不会杀死线程；正常工具注册则提前拒绝。

## 14. 调度线程何时启动

模块顶层执行：

```python
load_durable_jobs()
Thread(cron_scheduler_loop, daemon=True).start()
```

因此：

- 仅仅导入模块就启动线程；
- 离线测试也会出现 scheduler 日志；
- `queue_processor_loop` 只在脚本主程序中启动；
- 作为库导入时，Job 可以入队却不会自动交付；
- daemon thread 不阻止进程退出。

模块导入有明显运行时副作用。

## 15. 每秒轮询与分钟级触发

Scheduler：

```text
sleep 1 秒
读取 datetime.now()
遍历 Job
```

Cron 只精确到分钟。

同一分钟内线程会检查约 60 次，因此需要 `_last_fired` 去重。

真正触发时间可能在该分钟的任意秒，例如：

```text
09:00:00.8
```

而不是严格 09:00:00。

## 16. Minute Marker

```python
now.strftime("%Y-%m-%d %H:%M")
```

记录：

```python
_last_fired[job.id] = marker
```

同一 Job 同一分钟只入队一次。

包含日期很重要。如果只存 `"09:00"`，每天 9 点的 recurring Job 第二天仍可能被误认为已执行过。

## 17. 重启会丢失 `_last_fired`

`_last_fired` 只在内存。

若 durable Job 在 09:00:10 已触发，程序在 09:00:20 重启：

1. Job 从磁盘恢复；
2. `_last_fired` 为空；
3. 当前仍是 09:00；
4. Job 再次触发。

因此持久化定义并没有持久化交付去重状态。

## 18. 调度器不会补跑漏过的时间

如果应用：

```text
08:50 关闭
09:00 应触发
09:20 重新启动
```

当前时间不匹配 `0 9 * * *`，所以不会补跑。

Durable 的含义只是：

```text
Job 定义还在
```

不是：

```text
错过的运行记录会补偿
```

需要明确 misfire policy：

- skip；
- fire once now；
- replay all；
- replay up to limit。

## 19. 本地时区

调度线程使用：

```python
datetime.now()
```

因此 cron 按运行机器本地时区解释。

Job 本身不存 timezone。

部署到另一时区后，同一表达式会在不同绝对时间触发。

夏令时还会造成：

- 春季跳过某个本地时间 → Job 不触发；
- 秋季同一时分出现两次 → marker 相同，第二次被去重。

成熟系统应保存 IANA timezone，并明确定义 DST 策略。

## 20. 调度线程为什么不直接调用 Agent

若 Scheduler 到点就同步调用模型：

- 时钟检查被长 Agent Turn 阻塞；
- 多个 Job 无法及时调度；
- Scheduler 需要理解消息、工具和错误；
- 共享 history 容易并发修改；
- API 故障可能杀死调度器。

本课只做：

```python
cron_queue.append(job)
```

调度与执行解耦。

## 21. Queue 的并发保护

共享：

```python
cron_queue: list[CronJob]
cron_lock = threading.Lock()
```

Scheduler 写，Consumer 清空，Queue Processor 查询。

所有这三个操作都在 `cron_lock` 内：

- append；
- copy+clear；
- bool。

这避免普通列表的并发读写竞态。

## 22. Queue Processor 的工作

每 0.2 秒：

1. 检查 queue 是否非空；
2. 非阻塞获取 `agent_lock`；
3. 获取失败说明 Agent 正忙，稍后再试；
4. 获取成功后再次检查 queue；
5. 运行一个无显式用户 query 的 Agent Turn；
6. finally 释放 lock。

二次检查防止第一次观察后，另一个消费者已清空队列。

## 23. `agent_lock` 保护什么

主线程处理用户 query 时：

```python
with agent_lock:
    run_agent_turn_locked(query)
```

Queue Processor 也必须持有同一锁。

因此不会同时：

- 修改 `session_history`；
- 更新 `session_context`；
- 调用同一个 Agent Loop；
- 打印两个模型结果。

Job 在 Agent 忙时不会丢失，只是在 queue 中等待。

## 24. 等待用户输入时不持有 `agent_lock`

主线程调用：

```python
input("s14 >> ")
```

时没有锁。

因此 Queue Processor 可以在用户正在打字时：

- 输出日志；
- 调用模型；
- 打印回答；
- 再打印换行。

共享 session 数据仍受锁保护，但终端 UI 可能被打乱。

事件驱动 UI 应把输入和异步输出交给统一渲染器。

## 25. Consumer 怎样注入 Job

每次 Agent Loop while 顶部：

```python
fired = consume_cron_queue()
```

每个 Job追加：

```text
role=user
content=[Scheduled] {job.prompt}
```

多个 Job 会成为多个连续 user 消息，然后一次发送给模型。

Job prompt 是普通 user 层输入，不是 System Prompt。

它仍可能请求高风险操作；工具权限不能因为它来自 scheduler 就绕过。

## 26. Agent 正在执行时到期的 Job

Scheduler 仍能入队，因为它只需要 `cron_lock`，不需要 `agent_lock`。

当前 Agent Loop 每次工具轮之后回到 while 顶部，也会 consume queue。

所以到期 Job可能：

- 等当前模型调用结束；
- 在同一个长 Agent Turn 的下一次 LLM 调用前注入；
- 或等当前 Agent Turn 完全结束，再由 Queue Processor启动新 Turn。

这取决于当前循环是否还在进行工具轮。

## 27. 一次性 Job 的删除时机

Job 匹配后：

1. 先 append 到内存 queue；
2. 立即从 `scheduled_jobs` 删除；
3. durable 时立即重写磁盘。

如果进程在：

```text
删除持久定义之后、Consumer 交付之前
```

崩溃：

- queue 丢失；
- Job 定义也已删除；
- 一次性工作永久消失。

这是 at-most-once 倾向，不是可靠交付。

## 28. 取消 Job 不会撤回已排队实例

`cancel_job()` 只从：

```python
scheduled_jobs
```

删除。

它不扫描：

```python
cron_queue
```

所以：

1. Job 已 fire 入队；
2. 用户调用 cancel；
3. list 不再显示；
4. queue 中实例仍会被 Agent 执行一次。

产品需要定义 cancel 是否只取消未来，还是也撤回未交付实例。

## 29. `_last_fired` 不会清理

取消或一次性删除 Job 后，对应 marker 仍留在 `_last_fired`。

长期创建大量短命 Job 会让字典增长。

更严重的是 Job ID 碰撞：

- 新 Job复用旧 ID；
- 在同一分钟注册；
- marker 仍相同；
- 新 Job可能被错误抑制。

删除 Job 时应清理 marker，或使用稳定唯一 run key。

## 30. Job ID 碰撞

```python
cron_{random.randint(0, 999999):06d}
```

只有 100 万空间，无存在性检查。

同 ID 新 Job：

```python
scheduled_jobs[job.id] = job
```

静默覆盖旧 Job。

特殊情况：

- 新 session-only Job覆盖旧 durable Job；
- 因 session-only 不调用 save，磁盘仍保留旧 durable；
- 当前进程内运行新 Job；
- 重启后旧 Job“复活”。

应使用 UUID/ULID 或独占分配并检测冲突。

## 31. Durable 文件格式

路径：

```text
WORKDIR/.scheduled_tasks.json
```

内容是数组：

```json
[
  {
    "id": "cron_000042",
    "cron": "0 9 * * *",
    "prompt": "run tests",
    "recurring": true,
    "durable": true
  }
]
```

只保存 `durable=True` 的 Job。

没有 schema version、last fired、created time或 revision。

## 32. 持久化写入的边界

```python
DURABLE_PATH.write_text(
    json.dumps(durable, indent=2)
)
```

当前：

- 非原子覆盖；
- 无显式 UTF-8；
- 无文件锁；
- 无 fsync；
- 无备份；
- 无多进程协调；
- 无错误处理；
- 写失败可能从工具 handler 抛出。

进程中断可能留下半截 JSON。

## 33. `save_durable_jobs()` 的锁范围不一致

`schedule_job()`：

1. 锁内修改 dict；
2. 释放锁；
3. 调用 save。

`cancel_job()` 同样。

Scheduler 处理 one-shot 时：

1. 持有 cron_lock；
2. 修改 dict；
3. 在锁内调用 save。

而 `save_durable_jobs()` 自己不获取 lock。

这意味着外部调用 save 时，Scheduler 可能同时修改 `scheduled_jobs`：

- 快照不一致；
- 字典迭代时变化；
- 后一次写覆盖前一次；
- 内存与磁盘不同步。

应让一个函数统一负责“锁内快照，锁外原子写”，并用 revision 处理并发。

## 34. Durable 加载会吞掉所有异常

```python
except Exception:
    pass
```

以下情况都没有用户提示：

- JSON 损坏；
- 不是数组；
- 字段缺失；
- 多余字段；
- 类型错误；
- 权限错误；
- 解码错误。

用户只会发现 Job 没加载。

坏 cron 会单独打印 skipping，但它仍留在磁盘文件里，加载函数没有重写清理后的集合。

生产系统应保留坏文件、报告错误，不能静默假装“没有任务”。

## 35. 多进程会重复触发

若两个 S14 进程在同一 WORKDIR：

- 各自加载同一 durable 文件；
- 各自启动 Scheduler；
- 各自维护 `_last_fired`；
- 到点各自入队；
- 同一个 Job执行两次；
- 同时写同一 durable 文件。

`cron_lock` 只是进程内 threading lock，不能跨进程。

需要：

- 文件 leader lock；
- 数据库 lease；
- 单独 scheduler service；
- 分布式锁。

## 36. 应用关闭时 Durable Job 不会运行

Daemon thread属于当前 Python 进程。

关闭后：

- 没有每秒检查；
- 没有 Queue Processor；
- 没有 Agent Consumer。

若必须离线也执行，应使用：

- Windows Task Scheduler；
- cron/crond；
- systemd timer；
- Kubernetes CronJob；
- 云调度服务。

应用内 Scheduler 适合“应用在线期间的自动工作”。

## 37. 定时 Agent 的安全边界

Job prompt 可写：

```text
deploy production
delete old backups
email the report
```

触发时无人盯着。

因此需要比交互任务更严格：

- 注册权限；
- Job owner；
- 最大频率；
- 工具 allowlist；
- 高风险动作人工批准；
- dry run；
- 工作区隔离；
- 成本预算；
- 审计；
- 自动过期；
- 紧急 disable。

Cron 只负责时间，不应扩大工具权限。

## 38. 运行前准备隔离目录

S14 会自动创建：

- `.tasks/`；
- 可能的 `.scheduled_tasks.json`；
- scheduler thread。

### 38.1 Windows PowerShell

```powershell
cd D:\Projects\learn-claude-code
$lab = Join-Path $env:TEMP "learn-claude-s14"
New-Item -ItemType Directory -Force $lab | Out-Null
Set-Location $lab
$env:PYTHONUTF8 = "1"
$env:MODEL_ID = "你的模型 ID"
$env:ANTHROPIC_API_KEY = "你的 API Key"
& "D:\Projects\learn-claude-code\.venv\Scripts\python.exe" `
  "D:\Projects\learn-claude-code\s14_cron_scheduler\code.py"
```

### 38.2 macOS / Linux

```bash
LAB_DIR="$(mktemp -d)"
cd "$LAB_DIR"
export MODEL_ID="你的模型 ID"
export ANTHROPIC_API_KEY="你的 API Key"
/path/to/learn-claude-code/.venv/bin/python \
  /path/to/learn-claude-code/s14_cron_scheduler/code.py
```

第一次实验只安排输出日期或读取临时文件，不要定时部署、安装或删除。

## 39. 最小成功路径：每分钟触发一次

输入：

```text
Schedule a recurring session-only cron job with expression
* * * * * and prompt:
Use bash to print the current date and time.
Then list all cron jobs.
```

预期：

1. `schedule_cron` 返回 Job ID；
2. list 显示 recurring、session；
3. Scheduler 在当前或下一分钟 fire；
4. Queue Processor日志出现；
5. 注入 `[Scheduled] ...`；
6. Agent 调用 Bash；
7. 输出一次；
8. 同一分钟不重复。

由于 `* * * * *` 当前分钟立即匹配，通常无需等整整一分钟，最多等一次 1 秒轮询。

## 40. 最小成功路径：一次性任务

输入：

```text
Schedule a one-shot session cron with * * * * *
whose prompt is: read note.txt and report its contents.
```

预期：

- 最多约一秒后 fire；
- Job从 list 中消失；
- queued 工作仍被交付；
- 不会在下一分钟再触发。

如果 Job消失但没有执行，可能发生一次性删除与交付之间的异常。

## 41. 最小成功路径：Durable

注册：

```text
0 9 * * *，durable=true
```

确认当前目录出现：

```text
.scheduled_tasks.json
```

退出并从同一 cwd 重启。

预期启动日志：

```text
[cron] loaded 1 durable job(s)
```

list 能看到它。

这只验证定义恢复，不必等到 9 点验证执行。

## 42. 离线验证表达式

使用固定 datetime：

```python
from datetime import datetime
import s14_cron_scheduler.code as c

monday = datetime(2026, 8, 3, 9, 0)
print(c.cron_matches("0 9 * * 1-5", monday))
print(c.cron_matches("*/5 * * * *", monday))
print(c.validate_cron("0 24 * * *"))
print(c.validate_cron("1-5/2 * * * *"))
```

预期：

```text
True
True
hour 越界错误
range step 不支持错误
```

导入模块会启动 daemon scheduler；在短命测试进程和临时 cwd 中执行。

## 43. 离线验证 DOM/DOW OR

表达式：

```text
0 9 1 * 1
```

选择：

- 不是 1 日的周一 → 匹配；
- 不是周一的 1 日 → 匹配；
- 既不是 1 日也不是周一 → 不匹配。

用固定 datetime 断言三种情况。

这比等待真实日历更稳定。

## 44. 离线验证 ID 碰撞

测试中固定：

```python
c.random.randint = lambda a, b: 42
```

连续注册两个 session Job。

当前预期：

```text
两个返回 ID 相同
scheduled_jobs 只剩一条
内容是第二条
```

只在临时进程中 monkey patch。

## 45. 离线验证取消后仍排队

```python
job = c.schedule_job(..., durable=False)
c.cron_queue.append(job)
c.cancel_job(job.id)
```

当前预期：

```text
job.id 不在 scheduled_jobs
cron_queue 仍含 job
consume 后仍会交付
```

这帮助你决定 cancel 的产品语义。

## 46. 十个观察实验

### 实验 1：DOW=7

```text
0 9 * * 7
```

预期校验拒绝。

### 实验 2：巨大 Step

```text
*/999 * * * *
```

预期校验通过，但只在分钟 0 匹配。

### 实验 3：空列表项

```text
1, * * * *
```

字段切分会变化或空子项无效，验证应拒绝。

### 实验 4：同一分钟重启

让 durable 每分钟 Job触发后立刻重启。

预期可能在同一分钟再次触发。

### 实验 5：错过时间

注册一个只匹配特定过去分钟的 session Job。

预期不补跑。

### 实验 6：取消已排队 Job

预期未来调度取消，但内存排队实例仍交付。

### 实验 7：多个同时到期

注册三个 `* * * * *`。

预期 Queue Processor一次 drain，Agent收到三个 Scheduled user 消息。

### 实验 8：Agent 忙时到期

用 fake client 阻塞一个 Turn，让 Scheduler 入队。

预期 Job等待 lock，或在同一 Agent Loop下一个工具轮顶部消费。

### 实验 9：坏 Durable JSON

在临时目录写半截文件再导入。

预期无明确错误、Job静默丢失。

### 实验 10：两个进程

仅用无副作用 prompt 测试同一 durable Job。

预期两个进程各自触发，证明 threading lock 不跨进程。

## 47. 修改实验：安全唯一 ID

使用：

```python
from uuid import uuid4

id = f"cron_{uuid4().hex}"
```

或独占创建并检查冲突。

验收：

- 高并发创建不覆盖；
- cancel 后新 Job不复用旧 ID；
- `_last_fired` 不抑制新 Job；
- ID 可作为稳定事件关联键。

## 48. 修改实验：原子持久化

流程：

1. 在 `cron_lock` 内复制 durable 快照和 revision；
2. 释放锁；
3. UTF-8 写同目录临时文件；
4. flush/fsync；
5. `os.replace()`；
6. 若 revision 已变化，再安排一次保存。

不要长时间持有 `cron_lock` 做磁盘 I/O。

文件增加：

```json
{
  "schemaVersion": 1,
  "revision": 12,
  "jobs": []
}
```

坏文件应报告并保留备份。

## 49. 修改实验：持久化 Run State

为每个 Job保存：

```text
lastScheduledAt
lastDeliveredAt
lastCompletedAt
lastRunId
nextRunAt
```

使用稳定 run ID：

```text
{job_id}:{scheduled_minute_utc}
```

重启后可判断：

- 已调度未交付；
- 已交付未完成；
- 当前分钟是否重复；
- 是否需要补跑。

只保存 `_last_fired` 仍不足以完成可靠交付。

## 50. 修改实验：Misfire Policy

Job增加：

```text
misfire = skip | fire_once | catch_up
maxCatchUp = 3
```

重启时计算上次检查到现在的匹配时间。

策略：

- `skip`：只等未来；
- `fire_once`：若有任何遗漏，立即一次；
- `catch_up`：按时间顺序补，但受数量限制。

避免应用离线一周后瞬间发出上万次任务。

## 51. 修改实验：显式时区

Job保存：

```text
timezone = Asia/Shanghai
```

使用 `zoneinfo.ZoneInfo`。

触发内部转换为 UTC run key，展示保留本地时间。

需要测试：

- 普通日期；
- 春季跳时；
- 秋季重复时；
- 时区数据库更新；
- 更改系统时区不影响已有 Job。

## 52. 修改实验：Next Run 预览

注册前计算未来若干次：

```text
nextRuns:
2026-08-03T09:00+08:00
2026-08-04T09:00+08:00
2026-08-05T09:00+08:00
```

用户能提前发现：

- DOM/DOW OR 误解；
- timezone 错误；
- 永不匹配；
- 频率太高；
- DST 异常。

list 工具也应显示 next run。

## 53. 修改实验：可靠 Queue

不要在 fire 后立刻删除 one-shot。

Run 实例持久化：

```text
scheduled → delivered → acknowledged → completed/failed
```

one-shot Job在 Run 至少持久化为 scheduled 后，才从 active definitions 移除。

进程崩溃后：

- scheduled 可重新交付；
- delivered 未 ack 可重投；
- event ID 去重；
- completed 不重复执行。

这比内存 list 更接近作业队列。

## 54. 修改实验：取消未来与撤回排队分开

接口：

```text
cancel_job(id, cancel_pending_runs=False)
```

默认只取消未来，返回：

```text
Definition cancelled; 1 run already queued.
```

显式撤回时：

- queued 可取消；
- 已 delivered 需要 cooperative cancel；
- 已执行无法假装撤销；
- 全部写审计。

## 55. 修改实验：清理 `_last_fired`

删除 Job时：

```python
_last_fired.pop(job_id, None)
```

更好的是不使用 Job ID+本地 marker 的临时字典作为唯一去重，而使用持久 run key。

定期清理已不存在 Job的 marker，避免内存增长。

## 56. 修改实验：跨进程 Leader

同一工作区只有 Leader 调度 durable Job：

```text
竞争 lease
  → Leader 心跳
  → 只有 Leader 生产 Run
  → lease 过期后其他实例接管
```

session-only Job仍由所属进程处理。

文件锁适合本地单机；多机需要数据库或协调服务。

必须防：

- leader crash；
- lease 过期时旧 leader 仍运行；
- fencing token 缺失导致双 leader；
- 时钟漂移。

## 57. 修改实验：Job 限额与频率保护

至少限制：

- 每工作区 Job 数；
- 每分钟最大触发数；
- 同一 owner Job 数；
- Prompt 长度；
- 单 Job最短间隔；
- 每日模型调用预算；
- 自动过期时间。

`* * * * *` 每天 1440 次。一个高成本 Agent Turn 会迅速消耗预算。

注册时显示预计频率和成本警告。

## 58. 修改实验：安全执行策略

Job注册时保存：

```text
allowedTools
workingDirectory
maxTurns
maxCost
requiresApprovalFor
createdBy
```

触发时创建受限执行上下文。

例如定时检查可以：

- 读状态；
- 运行只读命令；
- 写报告；
- 禁止部署和外部消息。

高风险操作产生待审批项，而不是无人值守执行。

## 59. 修改实验：Queue Processor 容错

当前 `run_agent_turn_locked()` 若在工具层抛出未捕获异常，Queue Processor thread 会终止。

外层加入：

```text
try → 记录 run failed → retry policy
finally → release agent_lock
```

监控线程存活，必要时 supervised restart。

不要无上限重试同一 Scheduled prompt。

## 60. 测试矩阵

至少覆盖：

| 场景 | 期望 |
|---|---|
| `*` | 任意合法值 |
| `*/5` | 0,5,10... |
| 整数 | 精确匹配 |
| range | 闭区间 |
| list | 任一子项 |
| 非五字段 | 拒绝 |
| 越界 | 拒绝 |
| DOM/DOW 同限 | OR |
| DOW Sunday | 0 |
| 同一分钟轮询 | 只生产一次 |
| 同一分钟重启 | 按持久 run key 去重 |
| one-shot | 可靠交付一次 |
| cancel future | 不再生产 |
| cancel queued | 按策略 |
| Agent 忙 | queue 保留 |
| 多 Job同到期 | 全部交付 |
| Durable 重启 | 定义恢复 |
| 坏文件 | 报告且不破坏备份 |
| 两进程 | 单 Leader 生产 |
| DST | 按策略 |
| misfire | skip/fire/catch-up |

使用 fake clock 和临时 store，不要让测试真的等分钟。

## 61. 本课综合挑战：可靠的应用内调度器

最低要求：

1. 标准化表达式并提供 next runs；
2. Job ID 唯一；
3. 显式 IANA timezone；
4. 原子、带 schema version 的持久化；
5. 持久 Run 实例；
6. 一分钟去重跨重启有效；
7. 可配置 misfire；
8. one-shot 可靠交付；
9. cancel 明确未来与排队语义；
10. 多进程 Leader lease；
11. Queue 有 ack 和重投；
12. Queue Processor 可监督恢复；
13. Job/频率/成本限额；
14. 受限工具权限；
15. 审计和紧急 disable；
16. fake clock 完成第 60 节测试。

最终验收：

- 应用在线时按预期时间触发；
- 重启不重复、不静默漏掉已承诺的 Run；
- 两进程不会双执行；
- Job取消行为可解释；
- 夏令时和时区有明确结果；
- 高风险工具不会因定时触发获得额外权限。

## 62. 常见问题与定位

### 导入模块就出现 Scheduler 日志

线程在模块顶层启动，这是当前设计。

### 表达式看起来合法却被拒绝

检查是否使用了范围步长、名称、DOW=7、秒字段等未支持语法。

### `*/999` 为什么合法

当前只检查 step>0，没有按字段限制 step。

### 每月 1 日加周一为什么每周一也跑

DOM/DOW 同时受限使用 OR。

### 重启后同一分钟重复执行

`_last_fired` 没持久化。

### 应用关闭时没执行

Durable 只保存定义，进程关闭就没有 Scheduler。

### 重启后 Job消失但没报错

加载函数吞掉所有异常。检查 `.scheduled_tasks.json` 是否损坏。

### 取消后仍执行一次

Job在取消前已进入 `cron_queue`，当前 cancel 不撤回。

### Job ID 重复后内容变了

六位随机 ID 无冲突检查，后者覆盖前者。

### 同一 Job执行两次

可能有两个进程同时加载 durable Job，当前无跨进程 leader lock。

### 用户输入时终端突然打印定时结果

等待 `input()` 时不持有 agent_lock，Queue Processor 可以运行。

### 一次性 Job突然丢失

可能在定义删除后、内存 queue 交付前崩溃。

### 定时任务成本过高

当前无频率、次数和成本上限。立即 cancel，并加入策略限制。

## 63. 设计层面的延伸思考

### 时间匹配和工作执行必须解耦

Scheduler 应保持轻量、确定，Agent执行可以慢、失败、重试。

### 定义持久化不是运行可靠性

Job Definition、Run Instance、Delivery State 是三种不同数据。

### Cron 的难点不是解析五个字段

真正困难的是：

- 时区；
- DST；
- misfire；
- 重启；
- 多实例；
- exactly-once 幻觉；
- cancel；
- 成本与权限。

### Exactly-once 通常来自幂等

分布式系统更现实的是至少一次交付，加稳定 run ID 和幂等业务操作。

### 自主触发需要更严格权限

交互式用户不在场，不能默认所有工具都可执行。

### 队列背压很重要

Agent忙一小时，数百个 Job可能积压。需要合并、丢弃过期、优先级和容量策略。

### 本地时间是用户体验，UTC 是去重基础

用户用本地时区描述；系统可用 UTC instant/run key 实现一致性。

## 64. 结课自测

不看代码，回答：

1. Scheduler 为什么不直接调用模型？
2. 四层分别负责什么？
3. 五字段范围是什么？
4. 当前支持哪些单字段语法？
5. 为什么 `1-5/2` 不支持？
6. `*/999` 为什么能通过？
7. DOW 0 表示什么？
8. DOM 与 DOW 同受限用 AND 还是 OR？
9. `*/1` 为什么不被当作未受限？
10. matcher 为什么仍可能抛异常？
11. Scheduler 在何时启动？
12. Queue Processor 在导入模块时启动吗？
13. 为什么需要 minute marker？
14. marker 为什么包含日期？
15. 重启为何可能同一分钟重复？
16. Durable 会补跑离线期间的 9 点任务吗？
17. 当前 cron 使用哪个时区？
18. 秋季重复时分如何处理？
19. Agent忙时 Job去哪？
20. `agent_lock` 保护哪些共享状态？
21. 为什么用户输入界面仍可能被异步输出打乱？
22. 一次性 Job在哪个时刻删除？
23. 这个删除时机有什么丢失窗口？
24. cancel 会移除已排队实例吗？
25. `_last_fired` 为什么可能泄漏？
26. 六位随机 ID 碰撞会怎样？
27. session Job覆盖 durable ID 后为什么重启会复活旧 Job？
28. Durable save 为什么存在竞态？
29. 坏 JSON 为什么难以定位？
30. threading lock 能防两个进程双触发吗？
31. 应用关闭仍需调度时应使用什么？
32. misfire policy 有哪些选择？
33. 为什么 one-shot 也需要持久 Run？
34. 如何限制无人值守 Agent 的风险？
35. 如何用 fake clock 测试而不真等一分钟？

如果你能回答至少 30 题，并完成综合挑战，就真正掌握了本课。

## 65. 完成本课后的状态

你现在拥有：

```text
CronJob Definition
      │
      ├─ session memory
      └─ durable JSON
             ↓
Scheduler 每秒匹配
             ↓
minute marker 去重
             ↓
cron_queue
             ↓
Queue Processor 获取 agent_lock
             ↓
Agent Loop 注入 [Scheduled]
             ↓
模型与工具执行
```

Agent 在进程在线时能够无人手工输入地按时间自动开始工作。

也应该清楚教学版还缺少：

- 完整 cron 语法；
- Job ID 唯一；
- 时区与 DST 策略；
- next run；
- misfire；
- 原子存储；
- Run 持久化；
- 交付确认；
- cancel queued；
- 跨进程 leader；
- 频率和成本限制；
- 自主工具权限；
-可靠 one-shot。

下一课 S15 会把单 Agent 共享循环扩展成团队：持久队友、每人独立线程和消息收件箱。
