# S07 实操教学指南：让知识在需要时才进入上下文

> 对应课程：[s07_skill_loading](../../s07_skill_loading/)  
> 核心代码：[code.py](../../s07_skill_loading/code.py)  
> 示例技能目录：[skills](../../skills/)  
> 前置课程：[S06 Subagent](s06-subagent.md)  
> 建议用时：90–120 分钟  
> 本课产物：一个启动时发现技能目录、运行时按名称加载完整说明的 Agent

## 1. 学完这一课，你应该能做到什么

完成 S07 后，你应该能够：

1. 解释为什么不能把所有专业说明永久塞进 system prompt；
2. 区分技能目录 catalog 与完整 SKILL.md 内容；
3. 看懂 YAML frontmatter 的最小解析过程；
4. 说明 `SKILL_REGISTRY` 怎样防止按任意路径读取文件；
5. 预测技能在启动、加载、重复加载和进程重启时的行为；
6. 解释 skill 是知识说明，而不是自动新增的可执行工具；
7. 验证运行中新增技能为什么不会立刻出现在 SYSTEM；
8. 识别重复名称、损坏 YAML、超大技能和不可信指令的风险；
9. 为扫描器加入编码、校验、冲突检测、刷新和资源路径解析；
10. 创建一个自定义技能，并让 Agent 加载后完成可验收任务。

本课最重要的一句话是：

> 目录告诉模型“有哪些知识”，load_skill 才把当前真正需要的知识放进消息历史。

## 2. 为什么按需加载比全量 system prompt 更合理

假设项目有：

```text
React 规范 2000 行
SQL 规范 1500 行
API 规范 3000 行
PDF 操作手册 1000 行
```

如果全部拼进 system prompt：

- 每次模型调用都重复携带；
- 无关规范和当前任务争夺注意力；
- system prompt 变得难维护；
- 新增一个技能会影响所有任务；
- token 成本和延迟持续增加。

S07 使用两级加载：

```text
第一级：catalog
  名称 + 一句话描述
  启动时进入 SYSTEM
  每轮都可见，成本较小

第二级：full content
  完整 SKILL.md
  模型调用 load_skill 时成为 tool_result
  只在需要的会话中承担成本
```

## 3. 从 S06 到 S07 的实际变化

新增全局对象：

```python
SKILLS_DIR = WORKDIR / "skills"
SKILL_REGISTRY: dict[str, dict] = {}
```

新增启动流程：

```text
扫描 WORKDIR/skills/*
→ 找每个子目录的 SKILL.md
→ 解析 name/description
→ 填充 SKILL_REGISTRY
→ build_system() 注入 catalog
```

新增工具：

```text
load_skill(name)
```

父 Agent 工具数变为 8。子 Agent 的 `SUB_TOOLS` 仍只有 5 个基础工具，没有：

- task；
- todo_write；
- load_skill。

Agent Loop 没有为 skill 增加专用分支，仍通过 dispatch map 执行。

## 4. 最容易踩坑的路径规则

代码定义：

```python
WORKDIR = Path.cwd()
SKILLS_DIR = WORKDIR / "skills"
```

技能目录取决于“从哪里启动程序”，不是 `code.py` 所在目录。

| 启动目录 | 实际扫描位置 |
|---|---|
| 仓库根目录 | `仓库/skills` |
| 系统临时目录 `/tmp/lab` | `/tmp/lab/skills` |
| Windows `%TEMP%\lab` | `%TEMP%\lab\skills` |

为了既看到仓库示例技能，又不让 Agent 直接操作真实仓库，本指南会把 `skills/` 复制到临时目录。

如果忘记复制，程序仍能启动，但 SYSTEM catalog 是：

```text
(no skills found)
```

## 5. 当前仓库的四个技能

| 技能名 | 主要用途 | 是否包含附加资源 |
|---|---|---|
| `agent-builder` | 设计和构建 Agent | 有 `references/` 与 `scripts/` |
| `code-review` | 安全、正确性、性能和可维护性审查 | 主要是 SKILL.md |
| `mcp-builder` | 构建 MCP Server | 主要是 SKILL.md |
| `pdf` | 读取、创建、合并和拆分 PDF | 主要是 SKILL.md |

S07 只自动读取每个目录的 `SKILL.md`。`references/`、`scripts/` 或其他文件不会随
`load_skill` 自动展开；技能内容可以指导 Agent 后续使用 `read_file` 或 Bash 按需访问。

## 6. SKILL.md 的最小结构

示例：

```markdown
---
name: code-review
description: Perform thorough code reviews with security, performance, and maintainability analysis.
---

# Code Review Skill

## Review Checklist

...
```

当前教学扫描器只实际使用：

- `name`；
- `description`。

其他 frontmatter 字段即使存在，也不会自动获得运行时语义。例如写入
`allowed-tools` 并不会改变权限或工具列表。

注册表保存的是原始全文：

```python
{
    "name": name,
    "description": desc,
    "content": raw,
}
```

因此 load_skill 返回的内容包含 frontmatter 和正文。

## 7. Frontmatter 解析的精确行为

```python
def _parse_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    try:
        meta = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        meta = {}
    return meta, parts[2].strip()
```

### 正常情况

开头和结束分隔符存在，YAML 返回字典：

```text
meta = {"name": ..., "description": ...}
body = 去掉 frontmatter 后的正文
```

### 没有 frontmatter

返回空 meta 和完整原文。扫描器使用目录名作为 name，并尝试用第一行标题作为 description。

### 缺少结束分隔符

返回空 meta 和原文。第一行是 `---` 时，fallback description 可能也变成 `---`。

### YAML 语法错误

捕获 `yaml.YAMLError`，meta 为空。

### YAML 合法但不是字典

例如 frontmatter 是一个列表，`yaml.safe_load()` 会返回 list。当前代码之后调用
`meta.get(...)`，会在启动扫描时抛异常。扫描器还需要类型校验。

`body` 当前解析出来却没有使用；注册表保存 `raw`。

## 8. 注册表查找为什么能防路径遍历

`load_skill()`：

```python
skill = SKILL_REGISTRY.get(name)
if not skill:
    return f"Skill not found: {name}"
return skill["content"]
```

它没有执行：

```python
(SKILLS_DIR / name / "SKILL.md").read_text()
```

所以输入：

```text
../../.env
```

只会被当成一个注册表 key。除非扫描时真的存在同名 key，否则返回 not found，不会沿路径读取。

注册表模式还提供：

- 启动时统一验证；
- catalog 和 full load 使用同一来源；
- 可检测名称冲突；
- 可附加版本、来源和资源根目录。

## 9. 扫描与刷新生命周期

模块导入时只执行一次：

```python
_scan_skills()
SYSTEM = build_system()
```

之后：

- 新建技能不会自动扫描；
- 修改 SKILL.md 不会更新注册表内容；
- 删除技能不会从注册表消失；
- 手动调用 `_scan_skills()` 可以加入/覆盖项目，但它不会先清空旧项；
- 即使注册表变化，已经构建的 `SYSTEM` 也不会自动重建；
- 重启程序会重新扫描并重新构建。

这是“启动时 catalog”的直接结果。

## 10. 准备隔离实验目录

### 10.1 Windows PowerShell

在仓库根目录运行：

```powershell
$courseRoot = (Resolve-Path .).Path
$s07Lab = Join-Path $env:TEMP "learn-claude-code-s07"
New-Item -ItemType Directory -Force -Path $s07Lab | Out-Null
Copy-Item "$courseRoot\skills" -Destination $s07Lab -Recurse -Force
Set-Location -LiteralPath $s07Lab
$env:PYTHONUTF8 = "1"
Set-Content -Path .\vulnerable.py -Encoding ascii -Value @(
    "def find_user(db, username):",
    "    query = f`"SELECT * FROM users WHERE name = '{username}'`"",
    "    return db.execute(query)"
)
& "$courseRoot\.venv\Scripts\python.exe" "$courseRoot\s07_skill_loading\code.py"
```

### 10.2 macOS / Linux

在仓库根目录运行：

```bash
course_root="$(pwd)"
s07_lab="$(mktemp -d)"
cp -R "$course_root/skills" "$s07_lab/"
cd "$s07_lab"
printf '%s\n' \
  'def find_user(db, username):' \
  '    query = f"SELECT * FROM users WHERE name = '\''{username}'\''"' \
  '    return db.execute(query)' \
  > vulnerable.py
"$course_root/.venv/bin/python" "$course_root/s07_skill_loading/code.py"
```

启动后应看到：

```text
s07: Skill Loading — catalog in SYSTEM, content on demand
Type a question, press Enter. Type q to quit.

s07 >>
```

## 11. 第一次阅读代码：按八个区域理解

### 区域 A：扫描根目录

```python
for directory in sorted(SKILLS_DIR.iterdir()):
    if not directory.is_dir():
        continue
    manifest = directory / "SKILL.md"
```

只有一层直接子目录，且必须存在大小写完全匹配的 `SKILL.md`。

### 区域 B：名称与描述 fallback

```python
name = meta.get("name", directory.name)
desc = meta.get(
    "description",
    raw.split("\n")[0].lstrip("#").strip(),
)
```

缺 name 使用目录名。缺 description 使用原文第一行，而不是解析后的 body 第一行。

### 区域 C：重复名称覆盖

```python
SKILL_REGISTRY[name] = ...
```

如果两个目录 frontmatter 使用同一个 name，按目录排序后扫描到的后一个会静默覆盖前一个。

### 区域 D：Catalog 生成

```python
return "\n".join(
    f"- **{skill['name']}**: {skill['description']}"
    for skill in SKILL_REGISTRY.values()
)
```

Catalog 没有字符或 token 上限。技能很多、description 很长时，第一级也可能膨胀。

### 区域 E：SYSTEM 构建

SYSTEM 包含工作目录、完整 catalog 和使用 load_skill 的提示。它在每次模型调用中重复发送。

### 区域 F：load_skill

精确、大小写敏感的 key 查找。没有模糊匹配、别名或推荐逻辑。

### 区域 G：父工具与子工具

父 `TOOLS` 包含 load_skill。子 `SUB_TOOLS` 不包含它，`SUB_SYSTEM` 也没有 catalog。

若希望子 Agent 使用某技能，父可以：

- 把必要规则写进 task description；
- 让子工具集中也加入 load_skill；
- 使用未来的 forked skill 上下文。

### 区域 H：加载后的生命周期

完整技能作为普通 tool result 进入 parent messages。后续模型调用继续携带，直到：

- 会话结束；
- 历史被手动截断；
- S08 上下文压缩重写消息。

load_skill 本身没有“卸载”操作。

## 12. 最小成功路径

先输入：

```text
What skills are available? Do not call any tool; answer from your system catalog.
```

预期列出四个技能，且不出现 `[HOOK] load_skill`。

再输入：

```text
Load the code-review skill, then review vulnerable.py according to that skill.
Focus on concrete security, correctness, and testing findings. Do not modify
the file.
```

典型过程：

```text
[HOOK] load_skill
--- 
name: code-review
...
[HOOK] read_file
def find_user...
```

最终应至少指出：

- SQL 通过 f-string 拼接，存在注入风险；
- 应使用参数化查询；
- 缺少输入边界和数据库错误测试；
- 不应修改文件，因为任务要求只审查。

验收标准：

- Catalog 问题不加载完整 skill；
- 审查任务先调用 load_skill；
- load_skill 返回完整 SKILL.md；
- 模型再读取目标文件；
- 结论遵循 skill 的审查结构或检查维度；
- 文件保持不变。

## 13. 八个观察实验

### 实验 1：精确名称与大小写

输入：

```text
Call load_skill with the exact name `Code-Review` and report the tool result.
```

预期：

```text
Skill not found: Code-Review
```

再使用 `code-review` 应成功。注册表 key 大小写敏感。

### 实验 2：路径遍历名称不会读取文件

输入：

```text
Call load_skill with name `../../.env`. Report only whether a skill was found;
do not use read_file or bash.
```

预期：

```text
Skill not found: ../../.env
```

`load_skill` 不把 name 拼成路径。

### 实验 3：重复加载会重复进入上下文

连续两次要求：

```text
Load the code-review skill again and report its first heading.
```

当前没有 `LOADED_SKILLS` 集合。每次调用都返回完整 raw content，并增加一条新的大 tool result。

结论：按需加载避免“所有技能预加载”，但重复加载仍可能浪费上下文。

### 实验 4：运行中新增技能不会自动发现

在 Agent 运行期间要求：

```text
Use write_file to create skills/mini-style/SKILL.md with YAML frontmatter:
name mini-style, description Require descriptive variable names.
The body should instruct using descriptive Python names.
Then call load_skill with name mini-style.
```

文件会创建，但当前注册表仍是启动快照，所以预期：

```text
Skill not found: mini-style
```

退出并重新启动 S07，再询问 catalog，应看到 mini-style，加载也应成功。

### 实验 5：修改现有技能同样需要重启

运行中编辑 `skills/mini-style/SKILL.md` 的正文，再立即 load。返回的是扫描时保存的旧 raw 字符串。

重启后才读到新内容。

### 实验 6：子 Agent 看不到 load_skill

输入：

```text
Delegate a task asking the child to load the code-review skill and state
whether a load_skill tool is available. It must not read SKILL.md directly.
```

预期子 Agent 没有 load_skill，只能说明不可用。父 Agent 的 catalog 不会自动进入 fresh child
messages。

### 实验 7：技能不会自动执行脚本

加载 `agent-builder`。它的 SKILL.md 引用了 `references/` 和 `scripts/`。

预期：

- load_skill 只返回 SKILL.md；
- 引用文件不会自动读取；
- 脚本不会自动运行；
- 模型必须后续显式调用 read_file 或 Bash；
- 这些调用仍经过普通权限与 Hook。

### 实验 8：技能是指令，不是更高权限

创建一个纯练习 skill，正文要求模型运行包含 deny list 文本的无害 Bash 命令。加载后要求遵循。

预期 PreToolUse deny list 仍能阻止。技能内容不会提升权限，也不应覆盖更高优先级安全策略。

## 14. 离线验证扫描器

无需调用模型 API，可以在实验副本中打印：

```python
print(SKILLS_DIR)
print(list(SKILL_REGISTRY))
print(list_skills())
print(load_skill("code-review")[:100])
print(load_skill("../../.env"))
```

在正确复制 skills 的临时目录启动时，预期：

```text
SKILL_REGISTRY keys:
agent-builder
code-review
mcp-builder
pdf
```

并且：

- `load_skill("code-review")` 以 frontmatter 开头；
- 遍历名称返回 not found；
- registry 中没有任意文件路径。

## 15. 修改实验：让扫描器对坏文件更健壮

先复制：

Windows：

```powershell
Copy-Item "$courseRoot\s07_skill_loading\code.py" "$courseRoot\s07_skill_loading\code_experiment.py"
```

macOS / Linux：

```bash
cp "$course_root/s07_skill_loading/code.py" "$course_root/s07_skill_loading/code_experiment.py"
```

### 改动 A：显式 UTF-8 与 meta 类型检查

读取：

```python
raw = manifest.read_text(encoding="utf-8")
```

解析后：

```python
meta, body = _parse_frontmatter(raw)
if not isinstance(meta, dict):
    print(f"[skill warning] frontmatter must be a mapping: {manifest}")
    continue
```

再校验：

```python
name = meta.get("name", directory.name)
description = meta.get("description")
if not isinstance(name, str) or not name.strip():
    print(f"[skill warning] invalid name: {manifest}")
    continue
if not isinstance(description, str) or not description.strip():
    first_heading = next(
        (
            line.lstrip("#").strip()
            for line in body.splitlines()
            if line.startswith("#")
        ),
        name,
    )
    description = first_heading
```

预期一个坏 skill 只产生 warning，不阻止所有其他技能启动。

### 改动 B：检测重复名称

写入前：

```python
if name in SKILL_REGISTRY:
    previous = SKILL_REGISTRY[name]["manifest"]
    raise ValueError(
        f"Duplicate skill name {name!r}: "
        f"{previous} and {manifest}"
    )
```

注册表增加：

```python
"manifest": str(manifest)
```

预期：

- 两个目录声明同名时明确失败；
- 错误包含两个来源；
- 不再由扫描排序静默决定生效版本。

如果产品支持优先级覆盖，也应显式记录 source 和 precedence。

### 改动 C：安全刷新

重写扫描函数：

```python
def scan_skills() -> dict[str, dict]:
    new_registry = {}
    ...
    return new_registry
```

刷新时原子替换：

```python
SKILL_REGISTRY = scan_skills()
SYSTEM = build_system()
```

再添加一个 `reload_skills` 工具。

验收：

- 新建技能后调用 reload，无需重启即可加载；
- 删除技能后 reload，catalog 和 registry 都移除；
- 扫描失败时保留旧 registry，而不是半更新；
- 下一次模型调用使用重建后的 SYSTEM。

### 改动 D：避免重复加载

建立：

```python
LOADED_SKILLS = set()
```

加载时：

```python
if name in LOADED_SKILLS:
    return f"Skill already loaded in this session: {name}"
LOADED_SKILLS.add(name)
return skill["content"]
```

权衡：

- 避免完整文本重复进入 history；
- 技能文件更新后无法在同一会话重载；
- 压缩可能移除旧内容，但集合仍认为已加载；
- 更好方案需要把 loaded 状态与当前有效上下文关联。

## 16. 修改实验：给 Catalog 设置预算

大量技能会让第一级目录也变大。可以限制描述：

```python
MAX_CATALOG_CHARS = 8000


def list_skills() -> str:
    lines = []
    used = 0
    for skill in SKILL_REGISTRY.values():
        description = " ".join(skill["description"].split())
        line = f"- **{skill['name']}**: {description[:240]}"
        if used + len(line) + 1 > MAX_CATALOG_CHARS:
            lines.append("- ... more skills omitted")
            break
        lines.append(line)
        used += len(line) + 1
    return "\n".join(lines) or "(no skills found)"
```

验收：

- 换行 description 被压成一行；
- 单项描述最多 240 字符；
- 总 catalog 不超过预算附近；
- 被省略技能仍在 registry，但模型无法从 catalog 主动发现。

更完整方案需要搜索或分页工具，而不是静默隐藏。

## 17. 扩展实验：安全加载技能附加资源

把技能根目录保存在注册表：

```python
"root": directory.resolve(),
```

新增：

```python
def load_skill_resource(name: str, path: str) -> str:
    skill = SKILL_REGISTRY.get(name)
    if not skill:
        return f"Skill not found: {name}"

    root = skill["root"]
    resource = (root / path).resolve()
    if not resource.is_relative_to(root):
        return f"Error: resource escapes skill directory: {path}"
    if not resource.is_file():
        return f"Error: resource not found: {path}"
    return resource.read_text(encoding="utf-8")
```

用 `agent-builder` 测试：

```text
references/agent-philosophy.md
```

并测试：

```text
../../.env
```

预期前者成功，后者被技能根边界拒绝。资源工具仍应限制大小和类型。

## 18. 扩展实验：技能内容完整性

扫描时计算：

```python
import hashlib

digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
```

注册：

```python
"sha256": digest
```

load_skill 返回头部元数据：

```text
Loaded skill: code-review
SHA256: ...
Source: ...
```

作用：

- 审计当前会话加载了哪个版本；
- 文件变化后能判断旧 tool result 是否过期；
- 配合签名或可信来源策略；
- 支持缓存。

哈希只能检测变化，不能证明内容可信。信任还取决于来源、审查和签名。

## 19. 本课综合挑战：自定义 Python 安全审查技能

在启动 S07 前，在临时目录创建：

```text
skills/python-safety/SKILL.md
```

内容：

```markdown
---
name: python-safety
description: Review Python data-access code for injection, error handling, and tests.
---

# Python Safety Review

When reviewing:
1. Identify concrete injection paths.
2. Recommend parameterized APIs rather than string interpolation.
3. Separate correctness findings from missing tests.
4. Cite the relevant function name.
5. Do not modify code unless the user explicitly asks.

Return sections: Summary, Critical Findings, Test Gaps, Verdict.
```

重启 S07，输入：

```text
Load python-safety, use it to review vulnerable.py, and create REVIEW.md with
the exact required sections. Read REVIEW.md to verify it. Do not modify
vulnerable.py.
```

验收标准：

- Catalog 含 python-safety；
- Agent 调用 load_skill，而不是凭名称猜内容；
- 审查指出 `find_user` 的 SQL 注入；
- 推荐参数化查询；
- 列出恶意用户名、正常用户名和数据库异常测试；
- `REVIEW.md` 有四个指定 section；
- Agent 读取报告完成验证；
- `vulnerable.py` 保持不变；
- 安全结论来自技能指导与实际源码，而不是只复述技能。

## 20. 常见问题与定位

### Catalog 显示 `(no skills found)`

打印或检查：

```text
WORKDIR
SKILLS_DIR
```

确认启动目录下有 `skills/<name>/SKILL.md`。临时目录实验必须先复制 skills。

### 在仓库根目录能看到，临时目录看不到

因为 `SKILLS_DIR = WORKDIR / "skills"`。这是预期路径语义。

### 新技能文件已经存在但 load_skill 仍 not found

扫描只在启动时运行。重启，或完成 reload 扩展。

### 技能名称看起来正确却找不到

检查 frontmatter 中的 `name`，不是只看目录名；查找大小写敏感。

### 一个坏 SKILL.md 让程序启动失败

YAML 可能合法但不是字典，或 name 不可作为字典 key。加入 meta 类型和字段验证。

### 加载技能后模型没有遵循

检查：

- 是否真的出现 load_skill tool call；
- tool result 是否完整；
- 用户要求是否和 skill 冲突；
- skill 是否清晰、具体、可执行；
- 更高优先级 system/权限是否阻止部分步骤；
- 上下文是否已很长。

Skill 是指令，不是确定性代码。

### 子 Agent 无法加载 skill

S07 的 SUB_TOOLS 没有 load_skill，SUB_SYSTEM 也无 catalog。把规则放进 task description，
或显式扩展子工具集。

### 重复 load 后上下文增长

当前没有去重或卸载。避免重复请求，或实现 loaded 状态并与压缩生命周期协调。

## 21. 设计层面的延伸思考

### Skill 是知识层，不是能力层

SKILL.md 可以告诉模型怎样创建 PDF，但如果系统没有相关工具或软件，知识不会凭空产生执行能力。

### Catalog description 决定技能发现

模型通常依据 name 和 description 判断是否加载。描述应明确：

- 解决什么问题；
- 什么时候使用；
- 不适合什么情况；
- 关键触发词。

过于宽泛的描述会让模型频繁误加载。

### 按需加载仍然需要上下文生命周期

加载后的全文成为历史的一部分。任务切换后它可能变成噪声。S08 会处理旧结果压缩，但技能是否
应该保留、摘要还是重新加载，需要专门策略。

### 技能内容必须视为可执行影响

即使它只是文本，也会影响模型调用工具。技能来源需要：

- 可信目录；
- 代码审查；
- 版本记录；
- 冲突规则；
- 权限不升级原则；
- 对引用脚本的独立审批。

### 多来源技能需要显式优先级

用户、项目、插件、组织策略可能提供同名技能。静默覆盖会让行为难以解释，应保留 source、
precedence 和冲突诊断。

## 22. 结课自测

不看代码回答：

1. 两级技能加载分别包含什么？
2. 为什么 catalog 仍放在 SYSTEM？
3. SKILLS_DIR 由什么决定？
4. `_scan_skills()` 在何时运行几次？
5. load_skill 为什么不会发生简单路径遍历？
6. `body` 当前是否被用于返回技能内容？
7. 两个相同 name 会发生什么？
8. 运行中新增技能为什么不可见？
9. 子 Agent 是否能使用 load_skill？
10. 重复加载会怎样影响上下文？
11. Skill 能否自动提升权限或安装能力？
12. YAML 合法但不是 mapping 时为什么会崩溃？

完成自定义技能挑战、健壮扫描器改动，并正确回答至少 10 题，就可以认为掌握了 S07。

## 23. 完成本课后的状态

你现在拥有：

```text
WORKDIR/skills
  → 启动扫描
  → SKILL_REGISTRY
      ├─ name
      ├─ description
      └─ raw SKILL.md
  → SYSTEM 只注入 catalog
  → load_skill(name) 按需回填全文
  → 后续模型根据技能继续使用普通工具
```

按需加载解决了“不相关知识不要提前进入上下文”。随着 Agent 持续工作，已经进入历史的旧工具
结果和技能全文仍会堆积。S08 Context Compact 将用多层策略回收上下文空间。

