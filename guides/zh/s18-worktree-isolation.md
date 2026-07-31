# S18 实操教学指南：用 Git Worktree 隔离并行 Agent 的文件修改

> 对应课程：[s18_worktree_isolation](../../s18_worktree_isolation/)
> 核心代码：[code.py](../../s18_worktree_isolation/code.py)
> 前置课程：[S17 Autonomous Agents](s17-autonomous-agents.md)
> 建议用时：170–220 分钟
> 本课产物：任务—worktree 绑定、独立分支、按任务切换的工具 cwd、保留/删除策略和生命周期事件日志

## 1. 学完这一课，你应该能做到什么

完成 S18 后，你应该能够：

1. 解释 Git worktree 如何让同一仓库同时拥有多个工作目录和分支；
2. 区分任务所有权、工作目录隔离和最终代码合并；
3. 逐步追踪 create→bind→claim→work→complete→keep/remove；
4. 验证队友的 Bash、read 和 write 实际在哪个 cwd 运行；
5. 说明主工作区未提交改动为什么不会自动进入新 worktree；
6. 复现 S18 自动认领后的 dataclass 访问错误，并完成修复；
7. 复现“已提交改动被当成 0 个未推送提交”的删除保护缺陷；
8. 识别 orphan worktree、陈旧 task binding、同 worktree 多任务等一致性风险；
9. 说明 worktree 为什么不能阻止 Bash 主动访问其他路径；
10. 把教学版扩展成有事务式创建、可靠变更基线、合并流程、资源锁和恢复索引的隔离系统。

本课最重要的一句话是：

> Worktree 隔离的是默认工作目录和 Git 分支，不是权限边界；它减少意外覆盖，但不会自动解决认领竞争、恶意越界、提交质量或合并冲突。

## 2. S17 还缺哪一种隔离

S17 能让 Alice 和 Bob 认领不同任务：

```text
Task A owner=alice
Task B owner=bob
```

但两个线程仍使用：

```text
同一个 WORKDIR
同一套文件
同一个当前分支的工作树
```

若两项任务都修改 `config.py`：

```text
Alice write config.py
Bob   write config.py
```

后写者可能覆盖前写者。

任务系统只回答：

```text
谁负责哪个目标
```

Worktree 系统回答：

```text
这个目标应该在哪个目录和分支实现
```

## 3. Git Worktree 的心智模型

一个 Git 仓库可以有多个工作目录：

```text
main repo/
  .git/                         ← 共享 Git 数据库
  app.py                        ← 主工作树，分支 main
  .worktrees/
    auth/
      app.py                    ← 工作树，分支 wt/auth
    ui/
      app.py                    ← 工作树，分支 wt/ui
```

它们共享：

- object database；
- commit 对象；
- refs；
- repository history。

它们各自拥有：

- checkout 出来的文件；
- index；
- HEAD；
- 当前分支；
- 未提交修改。

因此 Alice 在 `wt/auth` 修改 `app.py`，不会直接改写 Bob 的 `wt/ui/app.py`。

## 4. Worktree 不是再次 Clone

完整 clone 通常复制或重新获取仓库对象。

worktree：

- 复用主仓库 `.git` 中的对象；
- 创建速度通常更快；
- 占用空间更少；
- Git 知道多个工作树之间的关系；
- 同一分支不能同时 checkout 到两个 worktree；
- 删除需要通过 `git worktree remove` 维护元数据。

课程使用：

```text
git worktree add <path> -b wt/<name> HEAD
```

含义：

1. 从当前 `HEAD` 创建新分支；
2. 分支名为 `wt/<name>`；
3. 把新分支 checkout 到 `.worktrees/<name>`。

## 5. 隔离系统的三个对象

S18 同时维护：

| 对象 | 存储位置 | 作用 |
|---|---|---|
| Task | `.tasks/task_*.json` | 目标、状态、owner、依赖、worktree 名 |
| Git worktree | `.worktrees/<name>/` | 独立文件目录和分支 |
| Event | `.worktrees/events.jsonl` | create、keep、remove 记录 |

绑定关系：

```text
Task.worktree = "auth"
       │
       ▼
.worktrees/auth/
       │
       ▼
branch wt/auth
```

代码没有单独的 worktree index；关联主要依赖 Task 字段和 Git 自己的 worktree 元数据。

## 6. 本课相对 S17 的增量

新增：

- Task 的 `worktree` 字段；
- `validate_worktree_name()`；
- `run_git()`；
- `create_worktree()`；
- `bind_task_to_worktree()`；
- `_count_worktree_changes()`；
- `remove_worktree()`；
- `keep_worktree()`；
- `events.jsonl`；
- teammate 的 `wt_ctx`；
- Lead 三个 worktree 工具。

工具数量：

```text
Lead: 17
Teammate: 8
```

Lead 新增：

1. `create_worktree`
2. `remove_worktree`
3. `keep_worktree`

teammate 数量不变，只是 Bash/read/write 的 cwd 变成动态。

## 7. 本课继续继承的旧边界

S18 沿用 S17 的：

- 无锁 JSON 任务板；
- 破坏性 mailbox 消费；
- 随机 request ID；
- 无 sender 认证；
- WORK/IDLE 消息路由不一致；
- 60 秒 idle timeout；
- API 异常静默；
- tool 异常缺少 finally；
- Lead 阻塞式 input；
- 自动 claim 的字符串返回值判断；
- description 未自动注入。

Worktree 不会自动修复这些问题。

它还没有合并：

- S11 Error Recovery；
- S13 Background；
- S14 Cron。

## 8. Task 新增 `worktree`

```python
@dataclass
class Task:
    id: str
    subject: str
    description: str
    status: str
    owner: str | None
    blockedBy: list[str]
    worktree: str | None = None
```

新任务默认：

```json
"worktree": null
```

绑定后：

```json
"worktree": "auth"
```

这个字段只是名称，不是：

- 绝对路径；
- branch name；
- Git worktree ID；
- lease；
- owner；
- existence proof。

代码按照约定推导：

```text
path   = WORKTREES_DIR / task.worktree
branch = "wt/" + task.worktree
```

## 9. 协调状态为什么留在主目录

模块导入时：

```python
WORKDIR = Path.cwd()
TASKS_DIR = WORKDIR / ".tasks"
WORKTREES_DIR = WORKDIR / ".worktrees"
MAILBOX_DIR = WORKDIR / ".mailboxes"
```

任务和邮箱始终从主 `WORKDIR` 访问。

即使 teammate 的业务工具切到 worktree：

- `list_tasks()` 仍读主 `.tasks`；
- `claim_task()` 仍写主 `.tasks`；
- `complete_task()` 仍写主 `.tasks`；
- `BUS` 仍用主 `.mailboxes`。

这是合理的：

> 业务代码目录可以隔离，团队协调状态必须共享。

否则每个 worktree 会拥有不同任务板副本，无法共同认领。

## 10. Worktree 名称校验

```python
VALID_WT_NAME = re.compile(r'^[A-Za-z0-9._-]{1,64}$')
```

另外显式拒绝：

```text
空字符串
.
..
```

允许：

```text
auth
ui-login
task_123
release.v2
```

拒绝：

```text
../escape
a/b
a\b
name with spaces
超过64字符
```

主要价值是：

- 避免 `/` 和 `\` 创建嵌套路径；
- 避免 `..` 路径穿越；
- 限制路径长度和字符集；
- 让 branch 推导更可预测。

## 11. 正则通过不等于 Git Branch 合法

下面名称能通过课程校验：

```text
.foo
foo.lock
name.
```

但对应分支：

```text
wt/.foo
wt/foo.lock
wt/name.
```

可能被 Git `check-ref-format` 拒绝。

实际离线结果：

```text
validate_worktree_name(".foo") → None
git worktree add ... -b wt/.foo → fatal: not a valid branch name
```

因此名称要同时满足：

```text
路径 slug 规则
∩ Git ref 规则
∩ 平台文件名规则
```

工程化版本应调用：

```text
git check-ref-format --branch wt/<name>
```

## 12. Windows 还需考虑保留名称

正则可能允许：

```text
CON
PRN
AUX
NUL
COM1
LPT1
```

Windows 文件系统对这些名称有特殊限制。

还要考虑：

- 名称末尾点号或空格；
- 大小写折叠；
- 最大路径长度；
- antivirus 对新目录的短暂占用；
- 文件删除锁。

跨平台 name validator 不能只靠一个正则。

## 13. `run_git()` 的行为

```python
def run_git(args):
    r = run_process(
        ["git"] + args,
        cwd=WORKDIR,
        timeout=30,
    )
    out = (r.stdout + r.stderr).strip()
    out = out[:5000] if out else "(no output)"
    return r.returncode == 0, out
```

特点：

- 不使用 shell 字符串拼接；
- 参数列表降低注入风险；
- 固定从主 WORKDIR 运行；
- 合并 stdout 和 stderr；
- 输出最多 5000 字符；
- 30 秒 timeout；
- timeout 转成 `(False, "Error: git timeout")`。

没有捕获：

- `git` 不存在的 `FileNotFoundError`；
- `run_process` 的其他异常；
- OS 资源错误。

这些异常可能直接穿透 tool handler。

## 14. 创建 Worktree 的完整顺序

```text
validate name
  → 计算 .worktrees/<name>
  → path.exists 检查
  → git worktree add -b wt/<name> HEAD
  → 可选 bind task
  → log create
  → 返回成功
```

对应代码：

```python
ok, result = run_git([
    "worktree", "add", str(path),
    "-b", f"wt/{name}", "HEAD",
])
```

只有 Git 命令成功后才会 bind。

但 bind 和 event log 不在同一个事务中，后面会专门验证。

## 15. 创建前必须满足的 Git 条件

练习目录需要：

1. 是 Git repository；
2. `HEAD` 指向有效 commit；
3. `wt/<name>` 分支不存在；
4. `.worktrees/<name>` 路径不存在；
5. 仓库没有把目标分支 checkout 在其他 worktree；
6. 当前用户能写 `.git/worktrees` 和目标目录。

空仓库没有 commit 时：

```text
fatal: invalid reference: HEAD
```

不在 Git 仓库时：

```text
fatal: not a git repository
```

所以临时实验仓库要先：

```text
git init
git commit --allow-empty -m init
```

## 16. 新 Worktree 基于 Commit，不包含主目录脏改动

创建使用：

```text
HEAD
```

它代表当前 commit。

若主工作树有未提交修改：

```text
main/app.py = 已修改但未 commit
HEAD/app.py = 旧版本
```

新 worktree 得到：

```text
HEAD/app.py = 旧版本
```

不会自动复制主工作区的未提交内容。

这有两个影响：

- 隔离更干净；
- teammate 可能看不到用户刚改但未提交的必要代码。

创建前应明确基线 commit。

## 17. Branch 名称和 Path 都必须唯一

代码先检查：

```python
if path.exists():
    return "already exists"
```

即使 path 不存在，Git 仍可能因为：

```text
branch wt/<name> already exists
```

而拒绝创建。

反过来，Git worktree registry 可能仍记录一个已被手工删除的 path。

这时需要：

```text
git worktree list
git worktree prune
```

课程没有自动 prune。

## 18. 绑定任务不会改变 Task 状态

```python
def bind_task_to_worktree(task_id, worktree_name):
    task = load_task(task_id)
    task.worktree = worktree_name
    save_task(task)
```

绑定前：

```json
{
  "status": "pending",
  "owner": null,
  "worktree": null
}
```

绑定后：

```json
{
  "status": "pending",
  "owner": null,
  "worktree": "auth"
}
```

只有 claim 才推进：

```text
pending → in_progress
```

这是正确的职责分离：

- bind 选择执行目录；
- claim 选择执行者。

## 19. `bind_task_to_worktree()` 没有验证 Worktree

它不检查：

- name 是否通过 validator；
- `.worktrees/<name>` 是否存在；
- Git 是否登记该 worktree；
- branch 是否匹配；
- worktree 是否已绑定其他任务；
- task 是否已 in_progress/completed。

直接调用可以写入：

```text
../escape
does-not-exist
另一个任务正在使用的 worktree
```

Lead 工具没有单独暴露 bind，但 `create_worktree(..., task_id)` 会调用它；代码或后续扩展仍可直接调用。

## 20. Create 与 Bind 不是事务

顺序是：

```text
Git worktree 创建成功
  → load task
  → 保存 binding
  → 写 create event
```

如果 task ID 不存在：

```text
Git worktree 已创建
load_task 抛 FileNotFoundError
create event 尚未写
函数没有返回正常结果
```

实际状态：

```text
.worktrees/orphan/ 存在
branch wt/orphan 存在
task binding 不存在
events.jsonl 没有 create
```

这就是 orphan worktree。

可靠实现需要：

- 创建前验证 task；
- 失败时 rollback；
- 或记录 `create_started/create_failed`；
- 启动时 reconciliation。

## 21. 一个 Worktree 可以被多个任务绑定

Task 中只有正向字段：

```text
task.worktree = name
```

没有反向唯一索引：

```text
worktree → active task
```

所以：

```text
Task A worktree=shared
Task B worktree=shared
```

都合法。

若 Alice 与 Bob 分别认领：

- 两人的 `wt_ctx` 指向同一个目录；
- 文件修改再次互相干扰；
- worktree isolation 失效。

一任务一 worktree 目前是约定，不是强制约束。

## 22. `wt_ctx` 是队友私有的 cwd 指针

每个 teammate thread 内：

```python
wt_ctx = {"path": None}
```

使用 dict 是为了让嵌套 handler 修改共享值。

```python
def _wt_cwd():
    p = wt_ctx["path"]
    return Path(p) if p else None
```

状态含义：

```text
None
  → 工具使用主 WORKDIR

absolute worktree path
  → 工具使用该 worktree
```

它没有写入磁盘，也没有出现在 Task owner 之外的 agent state 中。

线程重启后不会恢复。

## 23. 三个业务工具怎样使用 cwd

```python
def _run_bash(command):
    return run_bash(command, cwd=_wt_cwd())

def _run_read(path):
    return run_read(path, cwd=_wt_cwd())

def _run_write(path, content):
    return run_write(path, content, cwd=_wt_cwd())
```

如果 Alice 绑定 `auth`：

```text
read_file("app.py")
```

实际读取：

```text
<main>/.worktrees/auth/app.py
```

Bob 绑定 `ui` 时读取：

```text
<main>/.worktrees/ui/app.py
```

同一个相对路径对应不同文件。

## 24. Lead 工具仍在主 WORKDIR

Lead 的 handler：

```python
"bash": run_bash
"read_file": run_read
"write_file": run_write
```

没有 teammate 的 `wt_ctx` wrapper。

因此：

- Lead `read_file("app.py")` 读主工作树；
- Alice 同样调用可能读 auth worktree；
- Bob 同样调用可能读 ui worktree。

调试时一定要记录：

```text
agent
cwd
branch
task
```

否则相同路径的输出很容易被误认。

## 25. `safe_path()` 只保护 Read/Write

```python
base = cwd or WORKDIR
path = (base / p).resolve()
if not path.is_relative_to(base):
    raise ValueError(...)
```

对 `read_file` 和 `write_file`：

- `../` 逃出当前 base 会被拒绝；
- 绝对路径在 base 外会被拒绝；
- symlink resolve 后通常也受检查。

当 base 是 worktree 时，默认边界收紧到该 worktree。

这是路径工具的隔离，不是整个 Agent 的隔离。

## 26. Bash 可以主动逃出 Worktree

`run_bash()` 只是把 worktree 设为进程启动 cwd：

```python
run_bash_command(command, cwd=worktree)
```

模型仍可运行：

```text
cd ..
git -C <main> status
读取绝对路径
写入主仓库
访问网络或其他系统路径
```

取决于 shell runner 和操作系统权限。

所以 worktree 提供：

```text
默认位置隔离
```

不提供：

```text
安全沙箱
```

真正权限边界需要：

- 容器；
- 文件系统 sandbox；
- 进程权限；
- 命令 allowlist；
- 审批策略。

## 27. 手动 Claim 的 cwd 切换路径

teammate 主动调用 `claim_task`：

```python
result = claim_task(task_id, owner=name)
if "Claimed" in result:
    task = load_task(task_id)
    if task.worktree:
        wt_ctx["path"] = str(WORKTREES_DIR / task.worktree)
    else:
        wt_ctx["path"] = None
```

这条路径正确使用 dataclass 属性：

```python
task.worktree
```

因此可以作为原始代码的临时绕行：

- 在 spawn prompt 中给出 task ID；
- 要求 teammate 在初始 WORK 立刻调用 `claim_task`；
- 不等它进入 IDLE 自动认领。

但这绕开了 S17 的核心 pull 演示。

## 28. 自动 Claim 返回两项

S18 的 `idle_poll()` 相比 S17 改成：

```python
return "work", task_id
```

或：

```python
return "work", None
return "shutdown", None
return "timeout", None
```

第二项用于告诉 teammate 外层循环：

```text
刚才是不是自动认领了一项任务
```

若是，就应该根据 Task.worktree 设置 `wt_ctx`。

## 29. 自动 Claim 主路径存在 Dataclass Bug

外层代码：

```python
task = load_task(claimed_task_id)
if task.get("worktree"):
    wt_ctx["path"] = str(
        WORKTREES_DIR / task["worktree"]
    )
```

但 `load_task()` 返回：

```python
Task
```

不是 dict。

`Task` 没有：

```text
.get()
[]
```

实际错误：

```text
AttributeError: 'Task' object has no attribute 'get'
```

发生时任务已经成功 claim：

```text
status=in_progress
owner=alice
```

然后 thread 崩溃：

- wt_ctx 没设置；
- result 没发送；
- active 没清理；
- task 留在 in_progress。

这是学习本课前必须知道的真实代码行为。

## 30. 修复自动 Claim

把：

```python
task = load_task(claimed_task_id)
if task.get("worktree"):
    wt_ctx["path"] = str(
        WORKTREES_DIR / task["worktree"]
    )
else:
    wt_ctx["path"] = None
```

改成：

```python
task = load_task(claimed_task_id)
if task.worktree:
    wt_ctx["path"] = str(
        WORKTREES_DIR / task.worktree
    )
else:
    wt_ctx["path"] = None
```

修改后预期：

- auto claim 不再抛 `AttributeError`；
- 带绑定的任务把 cwd 指向 worktree；
- 无绑定任务把 cwd 清为主 WORKDIR；
- thread 继续进入下一轮 WORK。

这一修复只解决类型访问，不验证 worktree 是否真实存在。

## 31. 缺失 Worktree 会怎样

Task 可以绑定：

```text
worktree="missing"
```

自动或手动 claim 后：

```text
wt_ctx = <main>/.worktrees/missing
```

代码不检查：

- path 是否存在；
- path 是否是 Git worktree；
- branch 是否正确。

后续：

- Bash 可能因 cwd 不存在而失败；
- `read_file` 返回错误；
- `write_file` 会创建父目录，可能把它变成普通目录；
- Agent 可能在一个非 Git 目录中继续工作。

claim 前应验证 binding health。

## 32. `complete_task` 会无条件清空 cwd

```python
def _run_complete_task(task_id):
    result = complete_task(task_id)
    wt_ctx["path"] = None
    return result
```

即使 complete 失败：

```text
Task ... is pending, cannot complete
```

也会清空。

即使成功后模型还想：

- 运行最终测试；
- 查看 git diff；
- commit；
- 写总结文件；

下一条工具也会落到主 WORKDIR。

因此操作顺序应该是：

```text
修改
  → 测试
  → 检查 diff
  → commit 或保留
  → complete_task
```

不要先 complete 再继续操作 worktree。

## 33. 同一 Teammate 连续任务时 cwd 会切换

任务 A 完成：

```text
wt_ctx → None
```

IDLE 自动认领 B：

```text
wt_ctx → B.worktree
```

理想序列：

```text
A worktree
  → complete
  → main
  → claim B
  → B worktree
```

如果 A 的完成调用失败却清空，或自动 claim 遇到 dataclass bug，序列就断裂。

cwd 是生命周期状态，应该和 task/lease 一起原子更新，而不是散落在 handler 中。

## 34. Event Log 记录什么

```python
event = {
    "type": event_type,
    "worktree": worktree_name,
    "task_id": task_id,
    "ts": time.time(),
}
```

写入：

```text
.worktrees/events.jsonl
```

可能内容：

```json
{"type":"create","worktree":"auth","task_id":"task_...","ts":...}
{"type":"keep","worktree":"auth","task_id":"","ts":...}
{"type":"remove","worktree":"auth","task_id":"","ts":...}
```

JSONL 每行一个事件，适合：

- 追加；
- 人工查看；
- 简单脚本处理；
- 保留发生顺序。

## 35. Event Log 不是事实数据库

当前日志：

- 没有 event ID；
- 没有 actor；
- 没有 branch；
- 没有 path；
- 没有 commit/base SHA；
- 没有 success/failure；
- 没有 reason；
- 没有文件锁；
- 没有 flush/fsync；
- remove/keep 没有 task ID。

而且：

- `keep_worktree()` 对不存在名称也会写 keep；
- create 在 bind 失败时不会写 event；
- 用户可直接运行 Git 命令绕过日志；
- event 文件可以手工修改。

恢复时必须对账：

```text
events
vs
git worktree list
vs
branch refs
vs
task bindings
vs
filesystem paths
```

## 36. Keep 实际只写一条日志

```python
def keep_worktree(name):
    validate...
    log_event("keep", name)
    return "kept for review"
```

它不检查：

- path 是否存在；
- Git 是否登记；
- branch 是否存在；
- task 是否完成；
- 是否有改动；
- 是否已 keep。

因此：

```text
keep_worktree("does-not-exist")
```

仍返回：

```text
Worktree 'does-not-exist' kept for review
```

并写入误导性事件。

Keep 是“意图记录”，不是受验证的状态转换。

## 37. Remove 前的两项计数

```python
files, commits = _count_worktree_changes(path)
```

files：

```text
git status --porcelain
```

统计输出非空行。

它包括：

- staged；
- unstaged；
- untracked；
- rename/delete 等。

commits：

```text
git log @{push}..HEAD --oneline
```

意图是统计“未推送 commit”。

## 38. `@{push}` 在新 Branch 上通常不存在

`create_worktree()` 创建：

```text
wt/<name>
```

没有设置 upstream 或 push remote。

因此：

```text
git log @{push}..HEAD
```

通常报：

```text
fatal: no upstream configured for branch
```

但 `_count_worktree_changes()`：

- 不检查 return code；
- 只读取 stdout；
- stderr 被忽略；
- stdout 为空；
- commits 被算成 0。

所以 clean worktree 中的新 commit 可能显示：

```text
files=0
commits=0
```

这是删除保护的严重缺陷。

## 39. 已提交改动可能被默认删除

复现顺序：

```text
创建 worktree
  → 新增文件
  → git add
  → git commit
  → 工作树变 clean
  → remove_worktree(discard_changes=False)
```

计数：

```text
uncommitted files = 0
unpushed commits = 0  ← 错误
```

随后：

```text
git worktree remove --force
git branch -D wt/<name>
```

branch 和提交引用都被删除。

如果没有其他 ref 指向该 commit，只能依靠 reflog/对象恢复，且不应把这当成正常恢复方案。

本课实验只允许在临时仓库中验证，绝不要拿重要分支试。

## 40. Remove 的完整决策

```text
validate name
  → path exists?
  → discard_changes?
      ├─ false: count files/commits
      │    ├─ 无法验证 → 拒绝
      │    ├─ 有任何改动 → 拒绝
      │    └─ 计数为0 → 继续
      └─ true: 跳过计数
  → git worktree remove --force
  → git branch -D
  → log remove
```

即使 `discard_changes=False`，实际 Git 删除命令也带：

```text
--force
```

安全性完全依赖前面的计数准确。

## 41. `discard_changes=True` 的真实含义

它不是“只丢未提交文件”。

它会：

1. 跳过所有变更/commit 检查；
2. force remove 工作树；
3. force delete `wt/<name>` 分支。

可能丢弃：

- staged 修改；
- unstaged 修改；
- untracked 文件；
- worktree branch 上的 commit。

所以参数更准确的名字接近：

```text
destroy_worktree_and_branch
```

调用它必须是显式、高风险决策。

## 42. Branch 删除失败被忽略

```python
run_git(["branch", "-D", f"wt/{name}"])
log_event("remove", name)
return "removed"
```

没有检查第二个 `run_git()` 的 `ok`。

例如：

- branch 已重命名；
- ref 被锁；
- 权限错误；
- Git 返回其他失败。

函数仍：

- 写 remove event；
- 返回 removed。

结果可能是：

```text
worktree path 已删
branch 仍存在
日志声称 removed
```

remove 应分别记录 directory removal 和 branch cleanup。

## 43. Remove 不清理 Task Binding

任务可能仍保存：

```json
"worktree": "auth"
```

删除 worktree 后不会改成 null。

如果任务仍 pending 或之后被重置：

- teammate 会认领它；
- wt_ctx 指向不存在目录；
- write 甚至可能创建一个普通 `.worktrees/auth` 目录；
- 这个目录没有 Git worktree 元数据。

如果任务 completed，历史绑定仍可用于审计，但需要明确：

```text
worktree_state=removed
```

只保留 name 无法区分历史引用和可用绑定。

## 44. Remove 不会 Complete Task

这是课程刻意的正确设计。

删除目录不等于任务完成：

- 可能是取消；
- 可能是放弃；
- 可能是清理失败实验；
- 可能是迁移；
- 可能是已合并后的清理。

任务状态应由：

```text
任务验收结果
```

决定，而不是：

```text
目录是否存在
```

反过来，complete 也不会自动 keep/remove。

## 45. 课程没有 Merge 流程

`keep_worktree()` 只保留 branch 和目录。

没有工具：

- 查看 worktree diff；
- commit；
- rebase；
- merge；
- cherry-pick；
- 冲突解决；
- 测试 gate；
- code review；
- 删除合并后 worktree。

完整交付流程应该是：

```text
worktree change
  → test
  → commit
  → review
  → update/rebase
  → merge/cherry-pick
  → verify main
  → remove worktree
```

Worktree 只把并行改动分开，不负责把它们安全合起来。

## 46. 隔离不消除 Merge Conflict

Alice 在 `wt/auth` 改第 10 行。

Bob 在 `wt/ui` 也改第 10 行。

工作期间互不覆盖，但合并时 Git 仍可能报告冲突。

这比共享目录中的静默覆盖更好，因为：

- 两份改动都保留；
- commit 来源清楚；
- 冲突在集成时显式出现；
- 可以审查和选择。

隔离把问题从：

```text
执行期互相踩写
```

转化为：

```text
集成期显式协调
```

## 47. Worktree 自己也可能包含未跟踪协调目录

模块在主仓库创建：

```text
.tasks/
.mailboxes/
.worktrees/events.jsonl
```

若 `.gitignore` 没有排除这些目录，主工作树 `git status` 会看到它们。

创建 worktree 时，`.worktrees/<name>` 本身位于主工作树目录下。

实际项目建议忽略：

```gitignore
/.tasks/
/.mailboxes/
/.memory/
/.worktrees/
```

是否忽略任务历史取决于你的持久化策略；不要未经考虑就 commit 临时 mailbox 或 worktree 管理文件。

## 48. 运行前：不要直接使用当前脏仓库

本课涉及：

- 创建分支；
- 创建 worktree；
- force remove；
- force delete branch。

最安全的学习环境是独立临时 Git 仓库。

不要在：

- 有重要未提交改动的仓库；
- 正在协作的共享仓库；
- 不熟悉分支恢复的项目；
- 生产部署目录；

中直接练习删除路径。

## 49. Windows PowerShell：创建隔离练习仓库

```powershell
$lab = Join-Path $env:TEMP ("s18-lab-" + [guid]::NewGuid())
New-Item -ItemType Directory -Path $lab | Out-Null
Set-Location $lab

git init
git config user.name "S18 Student"
git config user.email "s18@example.invalid"
git commit --allow-empty -m "initial"

$env:PYTHONUTF8 = "1"
$env:MODEL_ID = "<你的模型ID>"
$env:ANTHROPIC_API_KEY = "<你的Key>"

& D:\Projects\learn-claude-code\.venv\Scripts\python.exe `
  D:\Projects\learn-claude-code\s18_worktree_isolation\code.py
```

期望：

```text
s18: worktree isolation
Enter a question, press Enter to send. Type q to quit.

s18 >>
```

如果仓库根目录 `.env` 已能被加载，可以不重复设置 secret；不要把 key 保存到临时仓库。

## 50. macOS / Linux：创建隔离练习仓库

```bash
lab="$(mktemp -d)"
cd "$lab"

git init
git config user.name "S18 Student"
git config user.email "s18@example.invalid"
git commit --allow-empty -m "initial"

export PYTHONUTF8=1
export MODEL_ID="<你的模型ID>"
export ANTHROPIC_API_KEY="<你的Key>"

/path/to/learn-claude-code/.venv/bin/python \
  /path/to/learn-claude-code/s18_worktree_isolation/code.py
```

退出后保留 `$lab`，先完成所有检查，再决定是否删除。

## 51. 第一个基线实验：复现自动 Claim 崩溃

先不要修代码。

输入：

```text
Create one task named "write isolated.txt". Create worktree "alpha" and
bind it to that task. Spawn alice as a developer and ask her to pull work
from the task board. Do not tell her to claim the task during her initial
work phase; let idle auto-claim it.
```

预期先看到：

```text
[bind] write isolated.txt → worktree:alpha
[worktree] created: alpha ...
[teammate] alice spawned ...
[claim] write isolated.txt → in_progress
[idle] alice auto-claimed ...
```

随后 thread traceback：

```text
AttributeError: 'Task' object has no attribute 'get'
```

磁盘任务：

```json
{
  "status": "in_progress",
  "owner": "alice",
  "worktree": "alpha"
}
```

但：

- `isolated.txt` 通常未创建；
- Alice 没有 result；
- Alice 仍可能留在 `active_teammates`。

这次实验的“成功标准”就是稳定复现缺陷。

## 52. 应用一处类型修复

在练习副本或课程代码中找到自动 claim 后的外层逻辑。

把 dict 风格：

```python
if task.get("worktree"):
    wt_ctx["path"] = str(
        WORKTREES_DIR / task["worktree"]
    )
```

改成 dataclass 风格：

```python
if task.worktree:
    wt_ctx["path"] = str(
        WORKTREES_DIR / task.worktree
    )
```

开始新临时仓库或清理上次状态后重跑。

验收：

- 不再出现 AttributeError；
- Alice 在下一轮 WORK 调用工具；
- task 能从 in_progress 到 completed；
- worktree 中出现产物；
- 主工作树没有同名产物。

若不想修改课程文件，可以使用第 27 节的手动 claim 绕行，但那不能验证自动认领链路。

## 53. 最小成功路径：两个隔离任务

修复后输入：

```text
Create two tasks:
1. "auth artifact" — create module.txt containing AUTH.
2. "ui artifact" — create module.txt containing UI.
Create worktree "auth" bound to task 1 and worktree "ui" bound to task 2.
Spawn alice and bob as developers. Let them auto-claim from the board.
They must inspect pwd and git branch before writing, run git status after,
then complete their task.
```

理想流程：

```text
Task A ↔ auth ↔ wt/auth
Task B ↔ ui   ↔ wt/ui

Alice claim one
Bob claim the other
各自在自己的 cwd 写 module.txt
各自 complete
```

不要求 Alice 必须拿 auth。

验收：

```text
.worktrees/auth/module.txt → AUTH
.worktrees/ui/module.txt   → UI
主目录/module.txt          → 不存在
```

两个任务：

```text
status=completed
owner分别对应实际执行者
worktree字段保持各自名称
```

## 54. 验证 CWD 和 Branch

退出交互程序后，在主练习目录运行。

Windows：

```powershell
git worktree list --porcelain
git -C .worktrees\auth branch --show-current
git -C .worktrees\ui branch --show-current
Get-Content .worktrees\auth\module.txt
Get-Content .worktrees\ui\module.txt
Test-Path .\module.txt
```

macOS/Linux：

```bash
git worktree list --porcelain
git -C .worktrees/auth branch --show-current
git -C .worktrees/ui branch --show-current
cat .worktrees/auth/module.txt
cat .worktrees/ui/module.txt
test ! -e module.txt
```

期望分支：

```text
wt/auth
wt/ui
```

`git worktree list --porcelain` 应同时列出主工作树和两个 linked worktree。

## 55. 验证任务绑定不推进状态

单独创建任务和 worktree，但暂时不 spawn。

输入：

```text
Create one pending task and create worktree "binding-demo" bound to it.
Do not claim the task and do not spawn anyone. Show me the task details.
```

期望 Task：

```json
{
  "status": "pending",
  "owner": null,
  "worktree": "binding-demo"
}
```

这证明：

```text
create/bind ≠ claim
```

## 56. 验证未提交修改保护

在临时仓库创建 worktree `dirty-demo`。

写入但不 commit：

```text
.worktrees/dirty-demo/change.txt
```

调用：

```text
remove_worktree(name="dirty-demo", discard_changes=false)
```

预期拒绝：

```text
has 1 uncommitted file(s)
Use discard_changes=true ...
or keep_worktree ...
```

验证：

- worktree 路径仍存在；
- branch 仍存在；
- change.txt 仍存在；
- events 中没有 remove。

## 57. 验证已提交改动保护缺陷

只在临时仓库进行。

创建 `commit-demo`，写文件并：

```text
git -C .worktrees/commit-demo add .
git -C .worktrees/commit-demo commit -m "valuable work"
```

确认 clean：

```text
git -C .worktrees/commit-demo status --porcelain
```

然后调用默认 remove：

```text
remove_worktree(name="commit-demo", discard_changes=false)
```

原代码很可能返回：

```text
Worktree 'commit-demo' removed
```

原因：

```text
@{push} 不存在
stderr 未检查
commits 被误算 0
```

验证：

```text
.worktrees/commit-demo 不存在
wt/commit-demo 分支不存在
```

这个实验展示的是缺陷，绝不是推荐操作。

## 58. 验证 Keep 的语义

对有改动的 worktree：

```text
keep_worktree("auth")
```

预期：

```text
Worktree 'auth' kept for review (branch: wt/auth)
```

验证：

- 目录没有变化；
- branch 没有变化；
- task 状态没有变化；
- events 新增 keep。

再对不存在名称调用。

原代码仍返回成功并记录事件。

这证明 keep 当前不是验证过的生命周期操作。

## 59. 查看 Event Log

Windows：

```powershell
Get-Content .worktrees\events.jsonl
```

macOS/Linux：

```bash
cat .worktrees/events.jsonl
```

检查：

- create 是否带 task_id；
- keep/remove 的 task_id 是否为空；
- bind 失败的 orphan 是否缺 create；
- 不存在 worktree 的 keep 是否仍出现；
- ts 是否按发生顺序增加。

不要只根据日志判断真实 Git 状态。

## 60. 十二个观察实验

### 实验 1：非法路径名称

尝试：

```text
../escape
a/b
a\b
name with spaces
```

预期：

- validator 拒绝；
- 不运行 Git；
- 不创建 path；
- 不写 event。

### 实验 2：正则允许但 Git 拒绝

尝试：

```text
.foo
foo.lock
```

预期：

- validator 返回合法；
- Git ref 校验失败；
- create 返回 `Git error`；
- 不绑定 task。

### 实验 3：非 Git 目录

在普通临时目录运行课程，再调用 create。

预期：

```text
Git error: fatal: not a git repository
```

`.tasks`、`.mailboxes`、`.worktrees` 目录仍可能已由 import 创建。

### 实验 4：没有 Commit 的空仓库

只 `git init`，不 commit。

预期：

```text
invalid reference: HEAD
```

### 实验 5：主工作树有脏改动

修改一个 tracked 文件但不 commit，再创建 worktree。

预期：

- 主目录看到修改版；
- 新 worktree 看到 HEAD 版；
- 两者内容不同。

### 实验 6：不存在 Task ID

调用：

```text
create_worktree("orphan", "task_missing")
```

预期：

- Git worktree 和 branch 创建成功；
- bind 抛 FileNotFoundError；
- 没有 create event；
- 需要人工清理 orphan。

### 实验 7：重复名称

创建 `alpha` 两次。

预期：

- 第二次因 path.exists 返回 already exists；
- 不新增 event；
- 原 worktree 不受影响。

### 实验 8：Branch 存在、Path 不存在

先创建：

```text
git branch wt/collision
```

再调用 `create_worktree("collision")`。

预期：

- path 检查通过；
- Git 因 branch exists 拒绝；
- 没有新 worktree。

### 实验 9：两任务绑定同一 Worktree

直接用 `bind_task_to_worktree()` 把 A、B 都绑定 `shared`。

预期：

- 两个 JSON 都保存成功；
- 没有唯一性错误；
- 若分别被认领，两个 Agent cwd 相同。

### 实验 10：陈旧绑定

绑定任务后 remove worktree。

预期：

- task.worktree 仍为旧名称；
- path 不存在；
- 新 claim 不做健康检查。

### 实验 11：错误 Complete 清空 CWD

让 Alice 正在 worktree A，然后调用另一个 pending task 的 `complete_task`。

预期：

- complete 返回 cannot complete；
- `wt_ctx` 仍被清成 None；
- 下一次 write 落到主目录。

### 实验 12：Bash 越界

在 disposable repo 中让 teammate 执行：

```text
pwd
git rev-parse --show-toplevel
git -C .. status
```

预期：

- 初始 cwd 是 worktree；
- Bash 可以主动引用父路径；
- worktree 不是安全 sandbox。

## 61. 离线验证名称和工具数量

在有环境变量的临时 Git 仓库中：

```python
import s18_worktree_isolation.code as c

print(len(c.TOOLS))
for name in [
    "", ".", "..", "../x", "a/b",
    "a b", ".foo", "foo.lock", "ok_name",
]:
    print(repr(name), c.validate_worktree_name(name))
```

期望：

```text
17
'ok_name' None
'.foo' None
'foo.lock' None
```

最后两个通过课程 validator，但不保证通过 Git。

## 62. 离线验证 Create 与 Bind

临时仓库必须已有 HEAD commit。

```python
task = c.create_task("demo", "work in isolation")
result = c.create_worktree("demo", task.id)

print(result)
loaded = c.load_task(task.id)
print(loaded.status, loaded.owner, loaded.worktree)
print((c.WORKTREES_DIR / "demo").exists())
```

预期：

```text
Worktree 'demo' created ...
pending None demo
True
```

继续：

```python
ok, output = c.run_git(["worktree", "list", "--porcelain"])
print(ok)
print(output)
```

输出应包含新路径和：

```text
branch refs/heads/wt/demo
```

## 63. 离线验证 Read/Write 隔离

```python
auth = c.WORKTREES_DIR / "auth"
ui = c.WORKTREES_DIR / "ui"

c.create_worktree("auth")
c.create_worktree("ui")

print(c.run_write("same.txt", "AUTH", cwd=auth))
print(c.run_write("same.txt", "UI", cwd=ui))

print(c.run_read("same.txt", cwd=auth))
print(c.run_read("same.txt", cwd=ui))
print((c.WORKDIR / "same.txt").exists())
```

预期：

```text
AUTH
UI
False
```

还要检查：

```python
print(c.run_read("../outside.txt", cwd=auth))
```

预期以：

```text
Error: Path escapes workspace
```

开头。

## 64. 离线验证 Auto-Claim 崩溃

用 fake client 让初始 WORK 立即结束，再让 fake idle 返回已认领 task ID。

关键结构：

```python
from types import SimpleNamespace as S
import threading

task = c.create_task("auto")
c.client = S(messages=S(create=lambda **kwargs: S(
    stop_reason="end_turn",
    content=[S(type="text", text="initial done")],
)))

def fake_idle(*args):
    c.claim_task(task.id, "alice")
    return "work", task.id

c.idle_poll = fake_idle
threading.excepthook = lambda args: print(
    args.exc_type.__name__,
    args.exc_value,
)
c.spawn_teammate_thread("alice", "developer", "wait")
```

原代码期望：

```text
AttributeError 'Task' object has no attribute 'get'
```

然后检查：

```text
task=in_progress
owner=alice
active alice=True
lead inbox empty
```

应用第 30 节修复后，异常应消失。

## 65. 离线验证 Orphan Worktree

```python
before = c.WORKTREES_DIR.joinpath(
    "events.jsonl"
).read_text() if c.WORKTREES_DIR.joinpath(
    "events.jsonl"
).exists() else ""

try:
    c.create_worktree("orphan", "task_missing")
except Exception as exc:
    print(type(exc).__name__)

print((c.WORKTREES_DIR / "orphan").exists())
print("orphan" in before)
```

预期：

```text
FileNotFoundError
True
False
```

第三项准确检查时应重新读取 after，并确认没有 `type=create, worktree=orphan`。

清理只能在临时仓库：

```python
c.remove_worktree("orphan", discard_changes=True)
```

## 66. 离线验证 Commit 计数缺陷

在 `demo` worktree 创建并 commit 一个文件后：

```python
path = c.WORKTREES_DIR / "demo"
print(c._count_worktree_changes(path))
```

新 branch 没有 upstream 时，实际常见结果：

```text
(0, 0)
```

再直接运行：

```text
git -C <path> log @{push}..HEAD --oneline
```

会看到 fatal。

对比证明：

- Git 命令失败；
- 函数却把失败解释成零提交。

## 67. 修改实验：创建前完整预检

建议先验证：

```text
name合法
task存在
task状态允许绑定
task尚无binding
branch ref合法
branch不存在
path不存在
worktree registry无冲突
```

伪代码：

```python
def preflight_create(name, task_id):
    validate_slug(name)
    validate_git_ref(f"wt/{name}")
    task = load_task(task_id) if task_id else None
    if task and task.status != "pending":
        raise Conflict("task_not_pending")
    if task and task.worktree:
        raise Conflict("task_already_bound")
    assert_path_and_branch_available(name)
    return task
```

预检仍不能消除检查后竞争，但能避免明显 orphan。

## 68. 修改实验：事务式 Create/Bind

最小补偿事务：

```python
task = preflight_create(name, task_id)
created = False
try:
    git_create(...)
    created = True
    if task:
        bind(...)
    log_event("create_committed", ...)
except Exception as exc:
    log_event("create_failed", ..., error=str(exc))
    if created:
        git_remove_and_delete_branch(...)
    raise
```

更可靠：

- 持久 operation ID；
- `create_started`；
- 每一步保存；
- 重启后按 operation state 对账；
- rollback 本身失败时进入 needs_repair。

修改后重做 nonexistent task 实验。

预期：

- 最好在 Git 创建前就拒绝；
- 即使 bind 写失败，也无 orphan；
- 失败 event 可追踪。

## 69. 修改实验：建立 Worktree Index

新增：

```json
{
  "name": "auth",
  "path": ".../.worktrees/auth",
  "branch": "wt/auth",
  "base_sha": "abc123",
  "head_sha": "abc123",
  "task_id": "task_...",
  "owner": null,
  "state": "ready",
  "created_at": 123.45
}
```

推荐状态：

```text
creating
ready
active
kept
merging
merged
removing
removed
needs_repair
```

索引不能取代 Git 查询。

启动时：

```text
读取 index
  → git worktree list --porcelain
  → refs 检查
  → path 检查
  → task binding 检查
  → reconciliation
```

## 70. 修改实验：强制一 Worktree 一活动任务

bind 事务中检查反向索引：

```python
existing = find_active_binding(worktree_name)
if existing and existing.task_id != task_id:
    return Conflict(
        code="worktree_already_bound",
        task_id=existing.task_id,
    )
```

还应检查 task：

```text
一个 task 只能绑定一个 active worktree
```

如果要允许多个任务复用同一 worktree，必须保证：

- 同一时刻只有一个 active；
- 后续任务明确基于前一个状态；
- owner 和 branch lifecycle 一致；
- 调度器理解串行资源锁。

## 71. 修改实验：Claim 前验证 Binding Health

在 claim 事务中：

```python
if task.worktree:
    record = validate_worktree(task.worktree)
    if not record.ok:
        return ClaimResult(
            ok=False,
            code="invalid_worktree_binding",
            detail=record.error,
        )
```

至少确认：

- path exists；
- Git worktree list 包含该 path；
- 当前 branch 与记录一致；
- worktree clean/dirty 状态符合策略；
- 未绑定给另一活动任务。

修改后，陈旧绑定任务不应进入 in_progress。

## 72. 修改实验：正确计算 Worktree 改动

“未推送”依赖 remote/upstream，不适合新本地 branch。

更清晰的基线是创建时保存：

```text
base_sha = git rev-parse HEAD
```

检查：

```text
uncommitted files:
  git status --porcelain

new commits:
  git rev-list --count <base_sha>..HEAD
```

还可以检查：

```text
ahead/behind target branch
merge-base
branch contains
remote publication
```

函数必须验证每个 Git command return code。

命令失败时：

```text
unknown
```

不是：

```text
0
```

## 73. 修改实验：安全 Remove Policy

推荐默认规则：

```text
若无法验证 → 拒绝
若有未提交文件 → 拒绝
若有 base 后新 commit → 拒绝
若未合并 → 拒绝
若任务 active → 拒绝
若 worktree 被使用 → 拒绝
全部通过 → remove
```

force destroy 需要：

- 明确 actor；
- reason；
- 展示将丢失的文件和 commit；
- 二次确认或审批；
- 先记录 recovery ref/tag；
- 审计事件。

例如先创建：

```text
refs/archive/worktree/<operation-id>
```

再删除 branch。

## 74. 修改实验：分别检查两步删除

```python
ok_remove, out_remove = run_git([
    "worktree", "remove", str(path)
])
if not ok_remove:
    return RemoveResult(stage="worktree", error=out_remove)

ok_branch, out_branch = run_git([
    "branch", "-d", branch
])
if not ok_branch:
    log_event("branch_cleanup_failed", ...)
    return RemoveResult(
        stage="branch",
        worktree_removed=True,
        error=out_branch,
    )
```

默认使用：

```text
git branch -d
```

让 Git 拒绝删除未合并 branch。

只有明确 destroy 时才 `-D`。

## 75. 修改实验：Keep 变成验证过的状态转换

Keep 前：

```text
worktree path存在
Git registry存在
branch存在
task binding一致
```

然后更新 index：

```text
active/ready → kept
```

记录：

```json
{
  "type": "keep",
  "actor": "lead",
  "reason": "awaiting review",
  "task_id": "...",
  "branch": "wt/auth",
  "head_sha": "...",
  "dirty_files": 2
}
```

重复 keep 应幂等：

```text
already kept
```

而不是无限追加误导事件。

## 76. 修改实验：不要在 Complete 失败时清 CWD

当前依据返回文本无法稳妥判断。

先让 complete 返回结构化结果：

```python
result = complete_task(task_id, owner=name)
if result.ok:
    wt_ctx["path"] = None
return result
```

更好的是拆开：

```text
complete_task
finalize_worktree
leave_worktree
```

任务 completed 后是否离开取决于流程：

- 还需 commit/review：保持 cwd；
- 已完成所有收尾：离开；
- 失败：保持现场供诊断。

## 77. 修改实验：用 ExecutionContext 代替 Dict

```python
@dataclass
class ExecutionContext:
    agent: str
    task_id: str | None = None
    worktree_name: str | None = None
    cwd: Path = WORKDIR
    branch: str | None = None
    phase: str = "idle"
```

状态转换：

```text
idle
  → claim
  → validate binding
  → enter_worktree
  → working
  → validating
  → completed
  → kept/removed
  → idle
```

每个工具调用记录 context snapshot。

这样错误日志能回答：

```text
谁、为哪个任务、在哪个目录、哪个分支、哪个阶段执行了什么
```

## 78. 修改实验：限制 Bash 越界

仅设置 cwd 不足。

可组合：

- 容器 mount 只暴露 worktree；
- deny 绝对路径；
- deny `..`、`git -C` 等只是弱防护；
- shell 命令 AST/allowlist；
- 每次命令前授权；
- 只提供结构化 build/test 工具；
- 网络和 secret 最小权限；
- OS 用户隔离。

同时将协调目录以只允许专用 Task/Message 工具访问的方式暴露。

验收：

- Bash 无法写主工作树；
- read/write 无法逃出 worktree；
- Agent 仍能通过受控 Task API 更新任务；
- secret 不因复制 worktree 泄漏。

## 79. 修改实验：显式集成流程

新增工具或状态：

```text
inspect_worktree
run_validation
commit_worktree
request_review
approve_merge
merge_worktree
verify_target
remove_worktree
```

推荐 gate：

```text
task completed
  ≠
branch ready to merge
```

分开：

```text
work_status: working/validated
review_status: pending/approved
integration_status: unmerged/merged
cleanup_status: kept/removed
```

这样不会因为任务 completed 就误删尚未集成的 branch。

## 80. 修改实验：事件日志与 Reconciliation

事件至少包括：

```text
event_id
operation_id
actor
type
task_id
worktree
path
branch
base_sha
head_sha
before_state
after_state
success
error
timestamp
```

写日志：

- 指定 UTF-8；
- 进程/线程锁；
- flush；
- 必要时 fsync；
- 不允许部分 JSON 行；
- event ID 去重。

启动恢复：

1. 列出 Git worktrees；
2. 扫描 index；
3. 扫描 Task bindings；
4. 检查 paths/branches；
5. 标记 orphan、stale、duplicate；
6. 自动修复安全项；
7. 其余进入 `needs_repair`。

## 81. 修改实验：生成碰撞安全的名称

不要直接把任意 task subject 当 path。

推荐：

```text
slug(subject)
+ task ID 短后缀
+ attempt ID
```

例如：

```text
auth-refactor-a81c-attempt-2
```

同时：

- Git ref 校验；
- Windows name 校验；
- 长度裁剪；
- case-insensitive collision 检查；
- transaction 内保留名称。

## 82. 测试矩阵

| 场景 | 初始状态 | 动作 | 预期 |
|---|---|---|---|
| 正常创建 | 有 HEAD | create | path/branch/event 均存在 |
| 非 Git | 普通目录 | create | Git error |
| 空仓库 | 无 commit | create | invalid HEAD |
| 路径非法 | `../x` | create | validator 拒绝 |
| Git ref 非法 | `.foo` | create | Git 拒绝 |
| path 重复 | 已有 path | create | already exists |
| branch 重复 | 已有 ref | create | Git 拒绝 |
| task 不存在 | Git 可创建 | create+bind | 原代码 orphan |
| binding | pending | bind | 状态仍 pending |
| 重复 binding | 两任务同 name | bind | 原代码允许 |
| 手动 claim | 有合法 binding | claim | wt_ctx 切换 |
| 自动 claim | 有合法 binding | idle claim | 原代码 AttributeError |
| missing path | task 有 stale name | claim | 原代码仍成功 |
| write isolation | 两个 cwd | 写同名文件 | 内容独立 |
| read escape | `../x` | read | 拒绝 |
| Bash escape | `cd ..` | bash | 原代码允许 |
| complete 成功 | in_progress | complete | task completed、cwd清空 |
| complete 失败 | pending | complete | 原代码仍清 cwd |
| dirty remove | 未提交文件 | remove default | 拒绝 |
| clean commit | 有新 commit | count | 原代码常返回0 |
| committed remove | clean+new commit | remove default | 原代码可能删除 |
| force remove | 任意修改 | discard=true | path/branch强制删除 |
| branch delete失败 | path已删 | cleanup ref失败 | 原代码仍返回成功 |
| stale task | remove 后 | load task | binding仍在 |
| keep exists | 有 worktree | keep | 只写事件 |
| keep missing | 无 worktree | keep | 原代码仍成功 |
| 日志并发 | 多线程 append | events | 原代码无锁 |

## 83. 本课综合挑战：实现安全的 Worktree Manager

### 必做要求

1. 修复 dataclass auto-claim；
2. 创建前验证 task、path、branch 和 Git ref；
3. create/bind 有补偿事务；
4. 一 worktree 同时只允许一个 active task；
5. Task claim 前检查 binding health；
6. WorktreeRecord 保存 base SHA；
7. 变更计数检查 Git return code；
8. remove 默认拒绝新 commit；
9. branch cleanup 失败不能报告完全成功；
10. keep 验证真实状态；
11. complete 失败不清 cwd；
12. event 有 actor、operation ID、before/after；
13. 启动时 reconciliation；
14. 覆盖测试矩阵至少 18 项。

### 进阶要求

1. sandbox Bash；
2. per-worktree resource lease；
3. commit/review/merge gate；
4. 保存 recovery ref；
5. worktree crash recovery；
6. task retry 创建新 attempt worktree；
7. 监控 worktree 磁盘占用；
8. 自动 prune 但需安全审批；
9. 多进程并发创建/删除测试；
10. Windows、macOS、Linux 兼容测试。

### 推荐目录

```text
worktree_manager/
  model.py
  git_backend.py
  name_policy.py
  index.py
  binding.py
  lifecycle.py
  integration.py
  recovery.py
  audit.py
  tests/
```

### 综合验收场景

1. 从同一 base 创建 auth 和 ui；
2. Alice/Bob 各自写同名文件；
3. Bob tool 异常，保留现场；
4. Alice commit 并请求 review；
5. 创建一个 orphan 模拟中断；
6. 重启 manager 做 reconciliation；
7. 恢复 Bob 或创建 attempt-2；
8. 合并 Alice；
9. 故意制造 Bob 与主分支冲突；
10. 解决冲突并合并；
11. 安全 remove 两个 worktree；
12. 验证任务、index、Git refs、events 全部一致。

## 84. 常见问题与定位

### `not a git repository`

运行时当前目录不是练习 Git 仓库。

`WORKDIR` 取进程启动 cwd，不是 `code.py` 所在目录。

### `invalid reference: HEAD`

仓库还没有 commit。

先创建一个初始 commit。

### `branch ... already exists`

同名 branch 尚在。

检查：

```text
git branch --list
git worktree list
```

不要未经检查就 `branch -D`。

### `already exists at ...`

目标 path 仍存在，可能是：

- 正常 worktree；
- 普通目录；
- 上次失败残留；
- task stale 写操作创建的假目录。

用 `git worktree list --porcelain` 判断。

### Auto-claim 后 thread 崩溃

检查是否：

```text
Task object has no attribute get
```

按第 30 节改用 `.worktree`。

### Task 显示 in_progress 但没有文件

可能 claim 后 dataclass bug 终止线程。

需要清理 active、恢复 task，并确认 worktree 状态。

### Teammate 写到了主目录

检查：

- task 是否绑定；
- path 是否真实存在；
- claim 是否走手动/自动正确路径；
- complete 是否提前清空 wt_ctx；
- 错误 complete 是否清空 wt_ctx；
- 工具是否为 Bash 主动切目录。

### Worktree 中看不到主目录刚改的代码

新 worktree 基于 HEAD，不复制未提交改动。

先决定 commit/stash/patch 策略。

### 默认 remove 居然删了 commit

`@{push}` 在新 branch 上不存在，函数忽略失败并算零。

从 reflog 尝试恢复，然后修复计数策略；不要继续依赖原删除保护。

### Keep 不存在目录也成功

原函数只校验 name 并写 event，不查 Git/文件系统。

### Remove 后 Task 仍指向旧名称

原函数不更新 binding。

需要状态化索引或明确历史 binding 语义。

### 两个 Agent 仍互相覆盖

检查两个 Task 是否绑定了同一个 worktree。

当前代码不强制唯一性。

### Read 拒绝但 Bash 可以访问外部

`safe_path` 只包 read/write。

Bash cwd 不是 sandbox。

### `Cannot verify worktree status`

`_count_worktree_changes()` 捕获了异常并返回 `(-1,-1)`。

不要为了绕过诊断直接 force；先手工检查 Git 状态。

### Events 与真实状态不一致

日志不是事务事实源。

对账 Git registry、branch、path 和 Task。

### Worktree 删除失败

Windows 上可能是：

- 文件被编辑器占用；
- antivirus 扫描；
- 当前 shell cwd 在目标内；
- 进程仍运行；
- 权限问题。

先让所有进程离开目标目录，再重试非破坏性检查。

### Background/Cron 不可用

S18 没有合并这些前章工具。

### 任务完成但代码没进主分支

complete task 与 merge branch 是两套状态。

本课没有自动 merge。

## 85. 设计层面的延伸思考

### 任务隔离与目录隔离是正交能力

owner 防止责任重复，worktree 防止默认文件路径相撞；两者都需要。

### 隔离把冲突延后到集成

这不是消灭冲突，而是保留两份输入并让冲突显式发生。

### CWD 是安全相关状态

每个工具调用都应能证明当前 task、worktree 和 branch。

### Git Error 不能解释成零

未知、失败和零是三个不同状态。

### 删除安全取决于正确基线

“未推送”不是新本地 branch 的可靠定义；应保存 base SHA 和集成状态。

### Keep/Remove 都是状态转换

不能只返回一句文本；要验证前置条件并持久化结果。

### Task Completed 不等于 Integrated

执行完成、review 完成、merge 完成、cleanup 完成要分别建模。

### Worktree 不是权限沙箱

它优化协作正确性，不抵御恶意命令。

### 绑定需要双向一致性

Task→worktree 与 worktree→task 都应可查询并满足约束。

### 创建需要 Saga

Git、Task JSON、event log 无法天然单事务，必须有补偿和恢复。

### 临时分支也是有价值的数据

删除前要证明已合并或明确批准丢弃。

### Reconciliation 比“日志看起来完整”更重要

进程会在任意两步之间崩溃，启动时必须从外部事实重建一致状态。

## 86. 结课自测

1. Git worktree 与 clone 有什么区别？
2. 多个 worktree 共享什么、隔离什么？
3. Task owner 与 Task.worktree 分别回答什么问题？
4. 为什么协调状态仍放在主 WORKDIR？
5. validator 拒绝哪些路径形式？
6. 为什么 `.foo` 能过正则却被 Git 拒绝？
7. Windows 还要考虑哪些名称？
8. `run_git()` 从哪个 cwd 执行？
9. 空仓库为什么不能创建 worktree？
10. 主目录脏改动为什么不出现在新 worktree？
11. bind 为什么不应推进 task status？
12. bind 缺少哪些真实性检查？
13. nonexistent task 如何产生 orphan？
14. 为什么两个 task 能绑定同一 worktree？
15. `wt_ctx=None` 表示什么？
16. Lead 和 teammate 对同一相对路径可能读到什么？
17. `safe_path` 能保护哪些工具？
18. Bash 为什么能逃出 worktree？
19. 手动 claim 怎样设置 cwd？
20. 自动 claim 为什么抛 AttributeError？
21. 这一错误发生时 Task 已是什么状态？
22. 正确修复是 `.get` 改成什么？
23. stale binding 会产生什么后果？
24. complete 失败为什么也会丢 cwd？
25. events.jsonl 记录哪些事件？
26. keep 为什么可能记录不存在的 worktree？
27. uncommitted files 怎样统计？
28. commit 为什么常被算成 0？
29. 默认 remove 为什么可能删除有价值 commit？
30. `discard_changes=true` 实际会删除什么？
31. branch delete 失败为什么可能被隐藏？
32. remove 后 task binding 怎样变化？
33. complete 和 remove 为什么不应互相隐式触发？
34. worktree 为什么不消除 merge conflict？
35. 正确变更基线应该保存什么 SHA？
36. `-d` 与 `-D` 在删除策略上有什么差异？
37. 如何保证一 worktree 一 active task？
38. 如何恢复 orphan/stale 状态？
39. 什么是 create/bind 的补偿事务？
40. S19 将解决哪一种扩展问题？

能用实际 Git 命令和代码路径回答至少 34 题，并完成类型修复、双 worktree 成功路径、dirty remove 和 commit 计数实验，就达到了本课目标。

## 87. 完成本课后的状态

你现在具备：

```text
共享任务板
  + 自主认领
  + Task→Worktree 绑定
  + 独立 Git branch
  + teammate 动态 cwd
  + 基础路径校验
  + keep/remove 意图
  + 生命周期事件
```

也应该明确剩余缺口：

```text
自动 claim 类型修复
  + 事务式创建
  + binding 唯一性
  + worktree health
  + 正确 commit 基线
  + 安全删除
  + merge/review gate
  + Bash sandbox
  + reconciliation
```

下一课 S19 将从“在哪执行”转向“能调用什么”：

> MCP Plugin 会让外部工具通过统一协议进入 Agent 工具集合，使能力扩展不再要求把每个 API 都硬编码进主循环。
