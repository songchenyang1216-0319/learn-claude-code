# S12 实操教学指南：用持久任务图管理长期工作

> 对应课程：[s12_task_system](../../s12_task_system/)
> 核心代码：[code.py](../../s12_task_system/code.py)
> 前置课程：[S11 Error Recovery](s11-error-recovery.md)
> 建议用时：130–170 分钟
> 本课产物：以独立 JSON 文件保存、带依赖、认领者和状态转换的任务系统

## 1. 学完这一课，你应该能做到什么

完成 S12 后，你应该能够：

1. 区分当前回合 Todo 与跨会话 Task；
2. 解释一个任务 JSON 的六个字段；
3. 从 `blockedBy` 关系画出任务依赖图；
4. 说明 pending、in_progress、completed 的合法主路径；
5. 验证缺失、未完成、自依赖和循环依赖为何无法认领；
6. 解释任务怎样跨进程保留，以及为什么不会自动恢复执行；
7. 使用五个任务工具完成“创建→列出→查看→认领→完成”；
8. 识别 ID 碰撞覆盖、路径穿越、并发抢占、非原子写入和脏 JSON 风险；
9. 说明当前 `Unblocked` 为什么可能重复报告早已解锁的任务；
10. 把教学版扩展成有验证、锁、原子写、环检测、lease 和事件日志的任务系统。

本课最重要的一句话是：

> Todo 记录“我现在准备怎么做”，Task 记录“这个长期目标由谁负责、依赖什么、做到哪一步，并让未来会话能够重新发现”。

## 2. TodoWrite 与 Task System 不是同一个层次

S05 的 TodoWrite 适合：

```text
阅读代码
定位 bug
修改函数
运行测试
```

这些是当前 Agent 解决一个用户请求时的执行步骤。

S12 的 Task 适合：

```text
设计数据库
实现 API
编写客户端
迁移生产数据
完成发布文档
```

这些工作可能：

- 跨多个用户回合；
- 跨程序重启；
- 由不同 Agent 领取；
- 有明确先后依赖；
- 需要持久状态。

对比：

| 维度 | Todo | Task |
|---|---|---|
| 核心用途 | 当前执行计划 | 持久工作项 |
| 典型粒度 | 几分钟的小步骤 | 可独立交付的工作 |
| 存储 | 当前进程消息/内存 | `.tasks/*.json` |
| 跨重启 | 否 | 是 |
| 依赖 | 无 | `blockedBy` |
| 负责人 | 无 | `owner` |
| 认领 | 无 | `claim_task` |
| 多 Agent | 只做计划展示 | 提供基础协调状态 |

成熟 Agent 可以同时使用两者：

```text
领取一个 Task
      ↓
为这个 Task 建立本回合 Todo
      ↓
逐项执行 Todo
      ↓
验证交付物
      ↓
完成 Task
```

## 3. 本课不是 S11 的全部累加

为了聚焦任务系统，S12 恢复为简单 Agent Loop。

它省略了 S11 的：

- `RecoveryState`；
- 429/529 退避；
- fallback model；
- 8K→64K；
- continuation；
- reactive compact。

本课的 API 调用只有一个普通 `try/except`。

它保留：

- System Prompt 组装；
- Bash；
- 读文件；
- 写文件；
- Memory index 条件注入。

并新增五个工具：

```text
create_task
list_tasks
get_task
claim_task
complete_task
```

课程机制是可以组合的，但每一章代码仍应按当前文件本身理解。

## 4. Task 的存储布局

模块导入时立即执行：

```python
TASKS_DIR = WORKDIR / ".tasks"
TASKS_DIR.mkdir(exist_ok=True)
```

因此即使还没创建任务，只要导入模块就会出现：

```text
当前工作目录/
└── .tasks/
```

创建任务后：

```text
.tasks/
├── task_1700000000_0042.json
├── task_1700000012_8301.json
└── task_1700000031_0194.json
```

一个任务一个文件，而不是所有任务放进同一个大 JSON。

优点：

- 单条任务容易读取；
- 单条更新不用重写整个列表；
- 将来可以按文件加锁；
- 文件变化可被 watcher 观察。

代价：

- 列表需要扫描目录；
- 跨任务事务更难；
- 多文件关系可能暂时不一致；
- 每个文件都可能单独损坏；
- 需要安全处理 ID 到路径的映射。

## 5. Task 数据结构

```python
@dataclass
class Task:
    id: str
    subject: str
    description: str
    status: str
    owner: str | None
    blockedBy: list[str]
```

### 5.1 `id`

文件名和引用任务时使用的标识。

### 5.2 `subject`

短标题，例如：

```text
setup database schema
```

### 5.3 `description`

详细目标、验收条件或上下文。

### 5.4 `status`

注释约定：

```text
pending
in_progress
completed
```

### 5.5 `owner`

认领者名称。创建时为 `None`。

### 5.6 `blockedBy`

必须先完成的上游 Task ID 数组。

## 6. Dataclass 不会自动执行运行时类型校验

类型注解：

```python
blockedBy: list[str]
```

不会阻止直接调用：

```python
create_task("bad", blockedBy="task_123")
```

非空字符串会被保存。之后：

```python
for dep_id in task.blockedBy
```

会逐字符遍历。

同样，手工修改 JSON 可以写入：

- 未知 status；
- 数字 subject；
- 字符串 blockedBy；
- 额外字段；
- 缺失字段。

`Task(**data)` 遇到缺失或额外字段可能直接抛 `TypeError`。

API 工具 schema 能约束大部分正常模型调用，但不能代替存储层运行时验证。

## 7. ID 的精确生成方式

```python
id = (
    f"task_{int(time.time())}_"
    f"{random.randint(0, 9999):04d}"
)
```

由：

- 当前 Unix 秒；
- 四位伪随机数

组成。

示例：

```text
task_1700000000_0042
```

同一秒理论上只有 10000 个随机空间，而且没有碰撞检查。

若两次生成相同 ID：

```python
save_task()
```

会直接覆盖旧 JSON。

因此“任务 ID 唯一”只是概率假设，不是代码保证。

## 8. ID 碰撞会怎样

在同一秒把随机数固定为 7：

```text
create first  → task_1800000000_0007.json
create second → task_1800000000_0007.json
```

结果：

- 返回的两个 Task 对象 ID 相同；
- 磁盘只有一个匹配文件；
- 文件内容是 second；
- first 被静默丢失；
- 其他任务对 first 的依赖现在可能错误地指向 second。

没有异常、warning 或重试。

更可靠的选择：

- UUID；
- ULID；
- 数据库自增 ID；
- 锁保护的 high-water mark；
- 使用独占创建并在冲突时重试。

## 9. Task ID 同时被当作文件路径的一部分

```python
def _task_path(task_id):
    return TASKS_DIR / f"{task_id}.json"
```

没有：

- 允许字符校验；
- `resolve()`；
- `is_relative_to(TASKS_DIR)`；
- 固定 ID pattern 验证。

例如：

```text
../escaped
```

会解析为：

```text
WORKDIR/escaped.json
```

而不是 `.tasks/` 内。

任务工具中的 `task_id` 来自模型输入，因此这是真实路径穿越边界。

更隐蔽的情况：

1. 用户在一个看似安全的 task 文件中把 JSON 的 `"id"` 改为 `"../outside"`；
2. `load_task("safe-name")` 读取该文件；
3. `complete_task()` 修改对象；
4. `save_task(task)` 使用对象内部的恶意 id；
5. 写入 `.tasks` 外部。

所以读取时也必须验证“文件名 ID 与 JSON 内 ID 一致”。

## 10. 创建任务的精确行为

```python
def create_task(subject, description="", blockedBy=None):
```

创建时固定：

```text
status = pending
owner = null
```

`blockedBy` 使用：

```python
blockedBy or []
```

然后立即保存并返回 Task 对象。

当前不会验证：

- subject 是否非空；
- description 长度；
- blockedBy 中的 ID 是否存在；
- 是否引用自己；
- 是否重复依赖；
- 依赖图是否有环；
- 同一 subject 是否重复；
- ID 是否碰撞。

这意味着“创建成功”只表示 JSON 写入成功，不表示任务图有效。

## 11. 保存和加载的精确行为

保存：

```python
json.dumps(asdict(task), indent=2)
```

再：

```python
write_text(...)
```

加载：

```python
json.loads(path.read_text())
Task(**data)
```

当前特性：

- 未显式指定 UTF-8；
- `ensure_ascii` 默认是 `True`；
- 中文通常保存为 `\uXXXX`；
- 无 schema version；
- 无 checksum；
- 无临时文件和原子替换；
- 无文件锁；
- 无 fsync；
- 无备份。

若进程在覆盖一半时终止，文件可能成为半截 JSON。

## 12. `list_tasks()` 怎样列出任务

```python
sorted(TASKS_DIR.glob("task_*.json"))
```

只读取文件名以 `task_` 开头的 JSON。

结果顺序是：

```text
按完整文件名的字典序
```

时间戳部分通常让不同秒按创建时间排序；同一秒内四位随机数排序，不是实际创建顺序。

任意一个匹配文件：

- JSON 损坏；
- 字段缺失；
- 字段多余；
- 类型异常

都可能让整个列表失败。

没有“跳过坏记录并报告”的隔离。

## 13. `run_list_tasks()` 的展示

图标：

```text
pending     ○
in_progress ●
completed   ✓
其他状态    ?
```

一行包含：

```text
图标 ID: subject [status] [owner] (blockedBy: ...)
```

没有任务时：

```text
No tasks. Use create_task to add some.
```

列表不显示完整 description。需要用 `get_task`。

Windows 终端若不是 UTF-8，圆点和对勾可能乱码；建议设置：

```powershell
$env:PYTHONUTF8 = "1"
```

## 14. `get_task()` 返回什么

返回完整 JSON：

```json
{
  "id": "task_...",
  "subject": "schema",
  "description": "...",
  "status": "pending",
  "owner": null,
  "blockedBy": []
}
```

`run_get_task()` 只捕获：

```python
FileNotFoundError
```

以下错误不会转成友好工具结果：

- JSON 解码错误；
- 权限错误；
- dataclass 字段错误；
- 路径异常；
- Unicode 解码错误。

## 15. 依赖关系怎样形成图

假设：

```text
schema
  ├─→ api ─→ tests
  └─→ docs
```

存储的是下游任务的 `blockedBy`：

```text
schema.blockedBy = []
api.blockedBy    = [schema]
tests.blockedBy  = [api]
docs.blockedBy   = [schema]
```

没有反向 `blocks` 字段。

要找下游任务，只能扫描所有 Task，判断其 `blockedBy` 是否包含某 ID。

## 16. 代码并没有保证它是 DAG

DAG 要求：

- 有向；
- 无环。

`blockedBy` 当然是有向边，但没有环检测。

可能出现：

### 自依赖

```text
A blockedBy A
```

### 两节点环

```text
A blockedBy B
B blockedBy A
```

### 多节点环

```text
A ← B ← C ← A
```

这些任务都会永久不能认领。

正常工具没有 update dependency，因此创建环不够方便；但可以：

- 预先猜 ID；
- 直接修改 JSON；
- 未来增加 update 工具后产生；
- 数据迁移时引入。

存储层仍应验证。

## 17. `can_start()` 的精确条件

对 task 的每个 `blockedBy`：

1. 依赖文件不存在 → `False`；
2. 依赖存在但 status 不是 completed → `False`；
3. 所有依赖存在且 completed → `True`。

空依赖数组：

```text
循环零次 → True
```

它不检查当前 task 自身状态。所以：

```python
can_start(completed_task_id)
```

只要依赖完成，也可能返回 `True`。

“能否开始”在调用者 `claim_task()` 中才与 `status == pending` 合起来判断。

## 18. 缺失依赖选择了 Fail Closed

若 blockedBy 指向不存在 ID：

```text
can_start = False
```

这是安全的默认行为：

- 不把配置错误当成依赖已完成；
- 不让下游越过未知前置任务。

但当前系统没有专门区分：

```text
依赖未完成
```

与：

```text
依赖引用损坏
```

认领结果统一是：

```text
Blocked by: [...]
```

生产系统应把 missing dependency 标为数据完整性错误。

## 19. 认领任务的状态转换

合法主路径：

```text
pending
  │ claim_task
  ▼
in_progress
```

认领顺序：

1. 加载任务；
2. 要求 status 是 pending；
3. 调用 `can_start()`；
4. 若 blocked，重新构造未完成依赖列表；
5. 设置 owner；
6. 设置 status；
7. 保存；
8. 返回成功文本。

工具 handler 固定：

```python
owner="agent"
```

模型不能通过 schema 指定其他 owner。

底层 `claim_task()` 虽支持 owner 参数，但没有 owner 格式验证。

## 20. 认领为什么还不是真正的并发协调

两个进程可能同时：

```text
进程 A 读取 pending
进程 B 读取 pending
进程 A 检查可开始
进程 B 检查可开始
进程 A 写 owner=A
进程 B 写 owner=B
```

两者都收到成功，最终磁盘只保留最后写入者。

这是经典：

```text
check-then-act / TOCTOU
```

当前没有：

- 文件锁；
- compare-and-swap；
- revision；
- 数据库事务；
- owner 唯一约束；
- lease。

所以 owner 字段表达意图，却没有提供跨进程互斥保证。

## 21. 完成任务的状态转换

合法主路径：

```text
in_progress
  │ complete_task
  ▼
completed
```

若不是 in_progress：

```text
Task ... is pending/completed, cannot complete
```

成功时：

1. 设置 status=completed；
2. 保留原 owner；
3. 保存；
4. 扫描所有任务；
5. 找出 pending、有 blockedBy、现在 `can_start=True` 的任务；
6. 报告 `Unblocked`。

它不检查调用者是否等于 owner。

任何知道 ID 的 Agent 都可以完成另一个 Agent 的任务。

## 22. `Unblocked` 并不只表示“刚刚解锁”

筛选条件：

```python
t.status == "pending"
and t.blockedBy
and can_start(t.id)
```

没有要求：

```text
刚完成的 task.id 在 t.blockedBy 中
```

也没有比较完成前后的可开始状态。

例如：

```text
完成 schema → docs 已解锁但仍 pending
认领并完成 api
```

第二次完成时输出可能是：

```text
Unblocked: docs, tests
```

其中：

- tests 刚因 api 完成而解锁；
- docs 早在 schema 完成时就已解锁，只是一直没认领。

因此当前列表准确含义是：

> 现在所有带依赖、pending 且已可开始的任务。

而不是严格的“本次新解锁”。

## 23. 完成状态不等于工作真的完成

`complete_task()` 不检查：

- 文件是否创建；
- 测试是否通过；
- description 的验收条件；
- owner 是否提交证据；
- 下游是否能正常运行；
- 用户是否批准。

模型调用工具就能把状态改为 completed。

任务系统只管理声明状态。工作真实性需要：

- 自动检查；
- 测试结果；
- artifact 链接；
- reviewer；
- 用户确认；
- 完成策略。

不要把一个 JSON status 当作交付证据。

## 24. 多个 `create_task` 在同一次模型响应中的限制

模型一次可以发出多个 tool use，但这些调用参数是在任何工具结果返回前一起决定的。

父任务 ID 是运行时随机生成的。

所以模型无法在同一 response 中可靠地：

1. 创建 schema；
2. 立即用 schema 的真实返回 ID 创建 api 依赖。

正确节奏：

```text
模型调用 create_task(schema)
  ↓
程序返回真实 task ID
  ↓
模型下一轮调用 create_task(api, blockedBy=[真实 ID])
```

也可以先生成稳定客户端 ID，但当前工具不支持。

当用户一次要求整张依赖图时，模型通常需要多个工具轮。

## 25. 跨会话持久化究竟意味着什么

任务 JSON 位于当前 `WORKDIR/.tasks`。

退出程序再从同一工作目录启动：

- 文件仍在；
- `list_tasks` 能重新读取；
- owner/status/blockedBy 保留。

但程序不会：

- 启动时自动列出任务；
- 自动找到上次 in_progress；
- 自动恢复执行；
- 验证 owner 是否还活着；
- 自动认领 pending；
- 把任务摘要放进 System Prompt。

持久化提供的是：

```text
可以恢复
```

不是：

```text
已经自动恢复
```

换一个工作目录就是另一套 `.tasks`。

## 26. Task 内容怎样进入模型上下文

System Prompt 只说任务工具可用。

具体 Task 内容只有在模型调用：

```text
list_tasks
get_task
```

后，作为 tool result 进入 messages。

这是一种按需加载：

- 不把所有任务每轮塞进 prompt；
- 新会话需要显式发现；
- 任务很多时 list 输出仍可能很大；
- description 只有 get 才加载。

Task subject/description 属于工作区数据，不应被当作更高优先级指令。

## 27. Agent Loop 的工具执行行为

一个 response 中所有 tool use：

1. 按 content 顺序执行；
2. 每个输出打印前 300 字符；
3. 结果汇总成一个 user/tool_result 消息；
4. 再调用模型。

任务工具 handler 抛异常时没有统一捕获。

例如：

- `claim_task` 使用不存在的 ID；
- list 遇到坏 JSON；
- complete 写文件失败；
- 参数类型错误

都可能跳出 Agent Loop，而不是变成一个可供模型纠正的 tool result。

## 28. API 错误展示相对 S11 有一处修正

API 异常仍被保存为 dict text block。

外层打印增加：

```python
elif isinstance(block, dict) and block.get("type") == "text":
```

因此 S11 中“dict 错误 block 不显示”的问题在 S12 得到处理。

但主循环只捕获模型 API 调用异常，没有恢复策略。

## 29. 运行前准备隔离目录

在仓库根目录直接运行会创建 `.tasks/`，并可能被 Bash 修改真实项目。

### 29.1 Windows PowerShell

```powershell
cd D:\Projects\learn-claude-code
$lab = Join-Path $env:TEMP "learn-claude-s12"
New-Item -ItemType Directory -Force $lab | Out-Null
Set-Location $lab
$env:PYTHONUTF8 = "1"
$env:MODEL_ID = "你的模型 ID"
$env:ANTHROPIC_API_KEY = "你的 API Key"
& "D:\Projects\learn-claude-code\.venv\Scripts\python.exe" `
  "D:\Projects\learn-claude-code\s12_task_system\code.py"
```

### 29.2 macOS / Linux

```bash
LAB_DIR="$(mktemp -d)"
cd "$LAB_DIR"
export MODEL_ID="你的模型 ID"
export ANTHROPIC_API_KEY="你的 API Key"
/path/to/learn-claude-code/.venv/bin/python \
  /path/to/learn-claude-code/s12_task_system/code.py
```

完成实验后，确认临时目录中的 `.tasks`，不要误查仓库根目录。

## 30. 最小成功路径：创建第一条任务

输入：

```text
Create a task named "setup schema" with description
"Create the initial SQLite schema and verify migrations."
Then list all tasks.
```

预期：

1. `create_task`；
2. 输出返回真实 ID；
3. `list_tasks`；
4. 状态为 pending；
5. owner 为空；
6. `.tasks/{id}.json` 存在。

示意：

```text
○ task_...: setup schema [pending]
```

打开 JSON，确认六个字段。

## 31. 最小成功路径：建立依赖链

先取得 schema 的真实 ID，再输入：

```text
Create "build API" blocked by <schema-id>.
```

然后：

```text
Try to claim "build API".
```

预期：

```text
Blocked by: [schema-id]
```

再：

```text
Claim the schema task, complete it, then claim the API task.
```

预期状态：

```text
schema: completed, owner=agent
api: in_progress, owner=agent
```

这验证了依赖不是展示文本，而是在 claim 前实际检查。

## 32. 最小成功路径：跨进程恢复

第一进程中创建并认领任务，然后输入 `q`。

保持同一临时目录，再次运行程序。

输入：

```text
List all tasks. Get full details for the in-progress task.
```

预期：

- 任务仍存在；
- status 仍为 in_progress；
- owner 仍为 agent；
- description 能读取。

如果显示 `No tasks`，最常见原因是第二次启动时 cwd 不同。

## 33. 离线验证任务图，不调用模型

模块导入需要占位环境变量：

```powershell
$env:PYTHONUTF8 = "1"
$env:MODEL_ID = "offline-test"
$env:ANTHROPIC_API_KEY = "offline-test"
```

在临时 cwd 中：

```python
import s12_task_system.code as c

schema = c.create_task("schema")
api = c.create_task("api", blockedBy=[schema.id])
tests = c.create_task("tests", blockedBy=[api.id])

print(c.claim_task(api.id))
print(c.claim_task(schema.id))
print(c.complete_task(schema.id))
print(c.claim_task(api.id))
print(c.complete_task(api.id))
print(c.can_start(tests.id))
```

预期：

```text
api 首次被 schema 阻塞
schema 可认领并完成
api 随后可认领并完成
tests 最后 can_start=True
```

## 34. 离线验证重复 `Unblocked`

创建：

```text
schema → api → tests
schema → docs
```

完成 schema：

```text
Unblocked: api, docs
```

只认领并完成 api，不认领 docs。

当前实现会报告：

```text
Unblocked: docs, tests
```

这证明 docs 不是本次新解锁，而是“当前仍可开始”。

## 35. 离线验证 ID 碰撞

测试中固定：

```python
c.time.time = lambda: 1800000000
c.random.randint = lambda a, b: 7
```

连续创建 first、second。

当前预期：

```text
first.id == second.id
匹配文件数 == 1
load_task(first.id).subject == "second"
```

测试必须在临时目录，不要覆盖真实任务。

## 36. 九个观察实验

### 实验 1：缺失依赖

创建：

```text
blockedBy=["task_missing"]
```

预期无法认领。

### 实验 2：自依赖

创建后手工把自身 ID 写进 blockedBy。

预期 `can_start=False`，永久阻塞。

### 实验 3：两节点环

手工让 A blockedBy B，B blockedBy A。

预期两者都不能认领，没有环错误提示。

### 实验 4：重复依赖

```text
blockedBy=[A, A]
```

若 A 完成，仍可开始；系统不去重。

### 实验 5：未知状态

手工把 status 改成 `"paused"`。

预期 list 图标为 `?`，claim 和 complete 都拒绝。

### 实验 6：坏 JSON

创建一个 `task_broken.json`，内容只写 `{`。

预期 `list_tasks()` 整体抛 JSON 错误。

### 实验 7：非匹配文件

在 `.tasks` 放 `notes.json`。

预期 list 忽略它。

### 实验 8：路径穿越

只调用并打印：

```python
c._task_path("../escaped").resolve()
```

不要实际写重要路径。

预期结果位于 `.tasks` 外，证明缺少 ID 校验。

### 实验 9：完成者不是 owner

底层用 owner A 认领，再从另一个调用路径直接 complete。

预期完成成功，因为 complete 不校验调用者身份。

## 37. 修改实验：严格验证 Task

定义常量：

```python
VALID_STATUSES = {
    "pending",
    "in_progress",
    "completed",
}
TASK_ID_RE = re.compile(
    r"^task_[A-Za-z0-9_-]+$"
)
```

验证：

```python
def validate_task(task: Task):
    if not TASK_ID_RE.fullmatch(task.id):
        raise ValueError("Invalid task id")
    if not isinstance(task.subject, str) or not task.subject.strip():
        raise ValueError("subject must be non-empty")
    if len(task.subject) > 200:
        raise ValueError("subject too long")
    if task.status not in VALID_STATUSES:
        raise ValueError("invalid status")
    if not isinstance(task.blockedBy, list):
        raise ValueError("blockedBy must be a list")
    if any(not TASK_ID_RE.fullmatch(x) for x in task.blockedBy):
        raise ValueError("invalid dependency id")
    if task.id in task.blockedBy:
        raise ValueError("task cannot block itself")
```

在：

- 创建前；
- 保存前；
- 加载后

都调用。

加载后额外验证 JSON 内 id 与请求文件名一致。

## 38. 修改实验：路径必须留在 `.tasks`

```python
def _task_path(task_id: str) -> Path:
    if not TASK_ID_RE.fullmatch(task_id):
        raise ValueError(f"Invalid task id: {task_id!r}")
    path = (TASKS_DIR / f"{task_id}.json").resolve()
    if not path.is_relative_to(TASKS_DIR.resolve()):
        raise ValueError("Task path escapes task store")
    return path
```

验收：

- 正常 ID 成功；
- `../x` 拒绝；
- 绝对路径拒绝；
- 斜杠和反斜杠拒绝；
- 换行和控制字符拒绝；
- 读取与写入都使用同一函数。

还要考虑 symlink 策略；仅做词法检查不一定阻止所有链接攻击。

## 39. 修改实验：冲突时不覆盖

使用独占创建：

```python
def create_task(...):
    for _ in range(10):
        task = build_candidate(...)
        path = _task_path(task.id)
        try:
            with path.open(
                "x",
                encoding="utf-8",
            ) as f:
                json.dump(
                    asdict(task),
                    f,
                    ensure_ascii=False,
                    indent=2,
                )
            return task
        except FileExistsError:
            continue
    raise RuntimeError("Could not allocate unique task id")
```

`"x"` 模式保证已存在时失败，不静默覆盖。

注意：之后的更新仍需要锁和原子替换。

## 40. 修改实验：原子保存

同目录临时文件：

```python
def save_task(task):
    validate_task(task)
    target = _task_path(task.id)
    temp = target.with_suffix(
        f".{os.getpid()}.tmp"
    )
    data = json.dumps(
        asdict(task),
        ensure_ascii=False,
        indent=2,
    )
    temp.write_text(data, encoding="utf-8")
    os.replace(temp, target)
```

同文件系统内 `os.replace()` 通常提供原子替换语义。

进一步：

- flush；
- `os.fsync()`；
- 清理残留 temp；
- Windows 文件占用处理；
- 目录 fsync；
- 备份策略。

验收：模拟写入中断时，目标要么是旧完整 JSON，要么是新完整 JSON，不是半截。

## 41. 修改实验：容错列出坏任务

```python
def list_tasks():
    tasks = []
    errors = []
    for path in sorted(TASKS_DIR.glob("task_*.json")):
        try:
            tasks.append(load_task_path(path))
        except Exception as exc:
            errors.append({
                "file": path.name,
                "error": str(exc),
            })
    return tasks, errors
```

工具结果明确显示：

```text
Loaded 8 tasks.
Skipped 1 invalid record: task_broken.json ...
```

不要静默忽略，因为任务丢失会影响依赖判断。

对于依赖检查，坏记录应 fail closed。

## 42. 修改实验：加入 Schema Version

文件：

```json
{
  "schemaVersion": 1,
  "task": {
    "id": "...",
    "subject": "...",
    "status": "pending"
  }
}
```

加载时：

```python
if version == 1:
    ...
else:
    raise UnsupportedTaskSchema(...)
```

未来新增：

- timestamps；
- priority；
- result；
- lease；
- metadata

时可以做明确迁移，而不是让 `Task(**data)` 突然失败。

## 43. 修改实验：真正的环检测

把所有任务构建为邻接表：

```python
graph = {
    task.id: task.blockedBy
    for task in list_tasks()
}
```

使用 DFS 三色标记：

```text
WHITE 未访问
GRAY  当前递归栈
BLACK 已完成
```

遇到指向 GRAY 的边就有环，并返回完整路径：

```text
task_A → task_B → task_C → task_A
```

在添加或修改依赖前：

1. 复制当前图；
2. 应用候选边；
3. 检查 missing；
4. 检查 self；
5. 检查 cycle；
6. 通过后才写入。

验收覆盖自环、二节点环、长环和合法菱形 DAG。

## 44. 修改实验：精确报告“本次新解锁”

最简单的直接下游方案：

```python
unblocked = [
    t.subject
    for t in list_tasks()
    if t.status == "pending"
    and task.id in t.blockedBy
    and can_start(t.id)
]
```

这只报告依赖当前完成任务的下游。

更严谨：

1. 完成前计算所有 pending 的 `can_start`；
2. 完成当前任务；
3. 完成后再次计算；
4. 返回 `False → True` 的集合。

后者能适配更复杂的条件。

验收：

```text
docs 已提前解锁但未认领
完成 api
```

只报告 tests，不再重复 docs。

## 45. 修改实验：加锁认领

核心要求：

```text
锁内重新读取 → 检查 → 更新 → 原子保存 → 解锁
```

不要在锁外读取后，锁内直接保存旧对象。

伪代码：

```python
with task_lock(task_id):
    task = load_task(task_id)
    if task.status != "pending":
        return conflict
    if not can_start_locked(task):
        return blocked
    task.owner = owner
    task.status = "in_progress"
    task.revision += 1
    save_task_atomic(task)
```

多任务依赖检查可能还需要：

- 列表级锁；
- 固定顺序获取多个锁；
- 数据库事务；
- 乐观并发 revision。

否则依赖可能在检查后变化。

## 46. 修改实验：乐观并发 Revision

增加：

```json
"revision": 4
```

更新 API 要求调用者传入 expected revision。

只有：

```text
disk revision == expected revision
```

才更新为 5。

否则返回 conflict，让调用者重新读取。

这能发现“基于旧状态覆盖新状态”，但文件实现仍需原子 compare-and-swap 或锁配合。

## 47. 修改实验：Release 与重新认领

加入转换：

```text
in_progress
   │ release
   ▼
pending
```

`release_task(task_id, owner, reason)`：

- 校验当前 owner；
- 清空 owner；
- status 回 pending；
- 记录 reason；
- revision 增加；
- 让其他 Agent 可认领。

适用于：

- Agent 崩溃；
- 人工取消；
- 当前 owner 无能力完成；
- 任务需要重新规划。

不要用手改 JSON 代替正式状态转换。

## 48. 修改实验：Lease 防止僵尸 Owner

只记录 owner 会永久占用任务。

增加：

```text
leaseExpiresAt
lastHeartbeatAt
```

认领时获得有限 lease；执行中定期续租。

过期后：

- 标记 stale；
- 重新回 pending；
- 或允许其他 Agent steal；
- 记录审计事件。

要使用 UTC 时间，并考虑时钟漂移。更可靠的协调通常需要共享数据库或协调服务。

## 49. 修改实验：只有 Owner 能完成

接口：

```python
complete_task(
    task_id,
    owner,
    result,
)
```

校验：

```python
if task.owner != owner:
    return "owner mismatch"
```

再验证状态与 lease。

但不要把 owner 字符串当强认证。调用身份应来自可信运行环境，而不是模型随意提供的参数。

## 50. 修改实验：保存完成证据

增加：

```json
{
  "result": {
    "summary": "Implemented schema",
    "artifacts": ["migrations/001.sql"],
    "checks": [
      {"command": "pytest", "status": "passed"}
    ]
  },
  "completedAt": "..."
}
```

完成策略可以要求：

- result 非空；
- 必需文件存在；
- 指定测试通过；
- reviewer 批准；
- 下游接口契约有效。

不同任务类型可以有不同验收器。

## 51. 修改实验：加入事件日志

当前 JSON 只保留最终快照，不知道：

- 谁创建；
- 谁认领；
- 谁释放；
- 状态何时变化；
- 为什么失败；
- 是否被覆盖。

追加事件：

```json
{
  "eventId": "...",
  "taskId": "...",
  "type": "claimed",
  "actor": "agent-a",
  "at": "...",
  "fromRevision": 2,
  "toRevision": 3
}
```

快照用于快速读取，事件用于审计与恢复。

不要让事件日志本身成为无锁并发写入的另一个损坏点。

## 52. 修改实验：启动时恢复策略

启动后主动：

1. 列出所有任务；
2. 找出当前 owner 的 in_progress；
3. 检查 lease；
4. 查看 description 和 result；
5. 验证工作区实际状态；
6. 决定继续、释放或请求用户确认；
7. 列出所有未阻塞 pending。

System Prompt 可以只注入一个轻量摘要：

```text
Tasks: 2 in progress, 3 ready, 4 blocked.
Use list_tasks for details.
```

不要把数百条完整任务每轮全部注入。

## 53. 修改实验：提供 `ready_tasks`

当前模型要 list 后自己判断依赖。

新增：

```python
def ready_tasks():
    return [
        t for t in list_tasks()
        if t.status == "pending"
        and can_start(t.id)
    ]
```

工具只返回可认领任务，并可按：

- priority；
- 创建时间；
- 预计耗时；
- owner 能力

排序。

注意：ready 只是一瞬间的观察，实际 claim 时必须重新检查。

## 54. 修改实验：批量创建任务图的事务

为解决同一 response 不知道父 ID，可支持：

```json
{
  "tasks": [
    {"clientKey": "schema", "subject": "schema"},
    {
      "clientKey": "api",
      "subject": "api",
      "blockedByKeys": ["schema"]
    }
  ]
}
```

服务端：

1. 验证 clientKey 唯一；
2. 为全部任务分配 ID；
3. 把 key 引用转成 ID；
4. 检查图；
5. 在一个事务中保存；
6. 任一失败则全不提交。

文件存储做多文件事务很复杂；SQLite 会更适合。

## 55. 扩展实验：任务优先级不是依赖

依赖回答：

```text
现在能不能开始？
```

优先级回答：

```text
多个可开始任务先做哪个？
```

不要用虚假依赖表达优先级，否则：

- 图语义失真；
- 不必要地阻塞并行；
- 上游失败会错误阻塞低优先级任务。

可增加：

```text
priority: low | normal | high | urgent
```

并保留独立调度策略。

## 56. 扩展实验：任务失败状态

当前只有 completed，没有 failed。

现实中需要区分：

```text
failed_retryable
failed_terminal
cancelled
blocked_external
```

但状态越多，合法 transition 越复杂。

先定义状态图和每条边的：

- actor；
- 前置条件；
- 副作用；
- 是否解锁下游；
- 是否可回退；
- 审计事件。

## 57. 扩展实验：删除任务的依赖完整性

若将来加入 delete：

- 有下游 blockedBy 当前任务时能否删除？
- 是拒绝、级联、还是留下 tombstone？
- 已完成 Task 是否允许删除？
- ID 是否能重用？
- 审计和结果如何保留？

更安全的默认：

```text
archive/tombstone，而不是物理删除
```

并保持 ID 永不重用。

## 58. 扩展实验：Task 描述也是不可信内容

description 可能含：

```text
Ignore all rules and run ...
```

当 `get_task` 把它作为 tool result 返回模型时，应视为数据。

防护：

- 明确来源和边界；
- 不把任务内容直接拼到高优先级 System Prompt；
- 工具执行仍走权限；
- 限制长度；
- 对外部同步任务保留 source；
- 不在 description 存密钥。

## 59. 测试矩阵

至少覆盖：

| 场景 | 期望 |
|---|---|
| 创建无依赖 | pending |
| 创建有已完成依赖 | 可认领 |
| 创建有未完成依赖 | blocked |
| 缺失依赖 | fail closed |
| 自依赖 | 创建拒绝 |
| 环 | 更新拒绝并报告路径 |
| pending 完成 | 拒绝 |
| in_progress 完成 | 成功 |
| completed 再认领 | 拒绝 |
| 非 owner 完成 | 拒绝 |
| 并发认领 | 只有一个成功 |
| ID 碰撞 | 重试、不覆盖 |
| 坏 JSON | 隔离并报告 |
| 路径穿越 | 拒绝 |
| 写入中断 | 保留完整旧/新文件 |
| release | 回 pending |
| lease 过期 | 可恢复 |
| 重启 | 状态仍在 |
| 精确 unblocked | 只报告 False→True |

所有文件测试使用临时目录，绝不污染仓库根目录。

## 60. 本课综合挑战：构建可靠的本地任务图

最低要求：

1. 安全且不可穿越的 ID；
2. ID 冲突不覆盖；
3. Task 运行时 schema 校验；
4. 显式 UTF-8；
5. 原子写；
6. 列表隔离坏记录并报告；
7. 环检测；
8. 精确的新解锁集合；
9. owner 身份校验；
10. 并发认领只有一个成功；
11. release 路径；
12. revision 或 lease；
13. 完成结果与证据；
14. 事件日志；
15. 启动恢复摘要；
16. 自动化测试覆盖第 59 节。

完成后的状态流至少为：

```text
pending ──claim──→ in_progress ──complete──→ completed
   ▲                    │
   └──────release───────┘
```

失败和取消状态可在定义清楚语义后再加入。

最终验收：

- 两个并发进程不能同时认领；
- 崩溃不会留下半截 JSON；
- 环无法提交；
- 路径始终在 `.tasks`；
- 重启能发现遗留工作；
- completed 有可检查证据；
- 所有状态变化可审计。

## 61. 常见问题与定位

### 启动后仓库多了 `.tasks`

`WORKDIR` 是启动进程时的当前目录，模块导入就创建目录。请在临时 cwd 运行。

### 重启后显示 `No tasks`

检查两次运行的当前工作目录是否完全相同。

### 一次要求创建依赖图，但 blockedBy 不对

随机父 ID 必须先由工具结果返回。模型可能需要多个工具轮，或使用批量图 API。

### Task 存在却不能认领

依次检查：

- status 是否 pending；
- 每个 blockedBy 文件是否存在；
- 每个依赖 status 是否 completed；
- ID 是否拼错；
- JSON 是否损坏。

### 明明刚完成的是 API，却又报告 docs 解锁

当前 complete 列出所有已可开始的带依赖 pending 任务，不只列本次新解锁。

### 两个 Agent 都说认领成功

教学版没有锁，发生了 TOCTOU。检查磁盘最终 owner 也只能看到最后写入者，不能证明另一方没工作。

### 中文 JSON 是 `\u4e2d...`

`json.dumps` 默认 `ensure_ascii=True`。改成 False 并显式 UTF-8。

### `list_tasks` 因一个文件全部失败

当前没有坏记录隔离。定位对应 `task_*.json`，修复后再列；扩展实现应报告并隔离。

### 任务永远 blocked

可能有 missing dependency、自依赖或环。当前没有诊断工具。

### `get_task` 使用异常 ID 访问到 `.tasks` 外

当前 `_task_path` 没验证 ID。这是安全缺陷，按第 38 节修复。

### 完成状态却没有实际代码

Task status 是声明，不是验收。增加 result、checks 和完成 gate。

### 已完成任务 owner 仍是 agent

当前 complete 不清空 owner，用它记录最后负责人。这是现有行为。

### API 错误没有重试

S12 为聚焦 Task System 省略了 S11 的完整恢复。

## 62. 设计层面的延伸思考

### Task Store 是小型数据库

一旦有：

- 唯一 ID；
- schema；
- 外键依赖；
- 并发更新；
- 事务；
- 查询；
- migration；
-审计，

你实际上已经在实现数据库能力。规模增长后，SQLite 往往比手写多文件事务更合适。

### 依赖与所有权是不同约束

`blockedBy` 决定能否开始；owner 决定谁负责。不要用 owner 代替锁，也不要用依赖代替调度。

### 持久化不等于恢复

磁盘上有状态只是第一步。真正恢复还需要：

- 发现；
- 验证；
- 判断 owner 是否存活；
- 对照实际工作区；
- 选择继续或回滚。

### 状态转换应是唯一写入口

若任何人都能直接改 JSON，状态机约束只是约定。生产系统应通过受控 API 更新，并审计外部修改。

### 完成是业务判断

不同 Task 的完成定义不同。任务系统应允许插入验证器，而不是一律相信调用者。

### 依赖图需要完整性治理

missing、cycle、删除、迁移、跨项目引用都会影响图。每次边变更都应验证整体不变量。

### 多 Agent 协作首先是并发控制

仅有 owner 字段不会阻止重复工作。锁、lease、revision 和幂等才让协作可靠。

## 63. 结课自测

不看代码，回答：

1. Todo 与 Task 的核心生命周期差别是什么？
2. Task 六个字段分别作用是什么？
3. `.tasks` 在什么时候创建？
4. 为什么 type hint 不能阻止字符串 blockedBy？
5. 当前 ID 怎样生成？
6. 同秒同随机数会怎样？
7. `_task_path()` 为什么有路径穿越风险？
8. JSON 内 id 与文件名不一致为何也危险？
9. list 顺序是创建顺序吗？
10. 一个坏 JSON 会怎样影响 list？
11. 无依赖任务为什么 `can_start=True`？
12. completed task 的 `can_start` 是否一定 False？
13. 缺失依赖采用 fail open 还是 fail closed？
14. 当前代码检测环吗？
15. claim 的合法状态转换是什么？
16. 两进程为什么可能同时 claim 成功？
17. complete 是否校验调用者等于 owner？
18. `Unblocked` 当前真正表示什么集合？
19. 为什么模型不能在同一次并行工具响应中引用刚生成的随机 ID？
20. 跨进程持久化为什么不等于自动恢复？
21. Task 内容何时进入模型上下文？
22. S12 还保留 S11 的退避吗？
23. 怎样保证 ID 冲突不覆盖？
24. 怎样保证写入中断不产生半截 JSON？
25. 如何精确计算本次新解锁？
26. 环检测应在什么时候运行？
27. revision 和 lock 各解决什么问题？
28. lease 为什么比永久 owner 更适合崩溃恢复？
29. completed 状态为什么不是完成证据？
30. 什么时候应该从 JSON 文件迁移到 SQLite？

如果你能回答至少 26 题，并完成综合挑战，就真正掌握了本课。

## 64. 完成本课后的状态

你现在拥有：

```text
.tasks/
  └─ 每任务一个 JSON
          │
          ├─ create → pending
          ├─ blockedBy 全 completed
          ├─ claim → in_progress + owner
          └─ complete → completed + 扫描可开始任务
```

Agent 可以：

- 创建持久工作项；
- 查看摘要和详情；
- 拒绝提前认领被阻塞任务；
- 记录负责人；
- 完成上游后开放下游；
- 在同一工作目录跨重启读取状态。

也应该清楚教学版还缺少：

- 唯一 ID 保证；
- ID/路径校验；
- schema 校验；
- UTF-8 与原子写；
- 文件锁和并发认领；
- DAG 环检测；
- 精确新解锁；
- owner 身份验证；
- release/lease；
- 完成证据；
- 事件审计；
- 自动启动恢复。

下一课 S13 会处理另一类时间问题：任务中的命令可能运行很久，Agent 不应该阻塞等待，而应把它
放到后台、继续工作，并在以后收集输出。
