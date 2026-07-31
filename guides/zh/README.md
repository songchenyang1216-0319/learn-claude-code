# Learn Claude Code：S01–S20 逐课实操指南

这里是新版 20 章课程的中文实操指南。它不会替代每章原有的
`README.md`，而是把课程说明进一步拆成可以照着执行、观察和验收的步骤。

每一课都会尽量回答六个问题：

1. 这一课解决什么问题？
2. 开始前需要知道什么？
3. 应该按什么顺序阅读和运行代码？
4. 每一步会看到什么现象？
5. 哪些结果必须一致，哪些结果会因为模型不同而变化？
6. 可以在哪里改代码做实验，改完后应该看到什么？

## 使用方式

- 建议按 S01 → S20 的顺序学习。
- 每课先完成“最小成功路径”，再做“动手实验”。
- 模型生成的具体命令和措辞不一定逐字相同；请使用指南中的“验收标准”
  判断是否成功。
- 前几课的代码会执行模型生成的 Shell 命令。请始终在临时练习目录中运行，
  不要直接拿包含重要文件的工作目录做实验。
- `docs/zh/` 是旧版 12 章材料；本目录对应根目录下的新版 `s01_*` 到
  `s20_*`。

## 指南进度

| 课程 | 主题 | 指南 | 状态 |
|---|---|---|---|
| S01 | Agent Loop | [一个循环如何让模型持续行动](s01-agent-loop.md) | 已完成 |
| S02 | Tool Use | [从一个 Bash 到可扩展工具系统](s02-tool-use.md) | 已完成 |
| S03 | Permission | [在工具执行前建立权限闸门](s03-permission.md) | 已完成 |
| S04 | Hooks | [用 Hooks 扩展 Agent](s04-hooks.md) | 已完成 |
| S05 | TodoWrite | [让复杂任务拥有可见计划](s05-todo-write.md) | 已完成 |
| S06 | Subagent | [用全新上下文隔离子任务](s06-subagent.md) | 已完成 |
| S07 | Skill Loading | [让知识在需要时才进入上下文](s07-skill-loading.md) | 已完成 |
| S08 | Context Compact | [用分层压缩延长 Agent 会话](s08-context-compact.md) | 已完成 |
| S09 | Memory | [把长期知识移出易失上下文](s09-memory.md) | 已完成 |
| S10 | System Prompt | [把 System Prompt 变成运行时配置](s10-system-prompt.md) | 已完成 |
| S11 | Error Recovery | [让 Agent 从失败中恢复](s11-error-recovery.md) | 已完成 |
| S12 | Task System | [用持久任务图管理长期工作](s12-task-system.md) | 已完成 |
| S13 | Background Tasks | [让慢命令在后台运行](s13-background-tasks.md) | 已完成 |
| S14 | Cron Scheduler | [让 Agent 按时间自动开始工作](s14-cron-scheduler.md) | 已完成 |
| S15 | Agent Teams | [从一次性子 Agent 走向可通信团队](s15-agent-teams.md) | 已完成 |
| S16 | Team Protocols | [用请求—响应协议协调 Agent 团队](s16-team-protocols.md) | 已完成 |
| S17 | Autonomous Agents | [让队友自己发现、认领并完成任务](s17-autonomous-agents.md) | 已完成 |
| S18 | Worktree Isolation | [用 Git Worktree 隔离并行 Agent 的文件修改](s18-worktree-isolation.md) | 已完成 |
| S19 | MCP Plugin | [把外部工具动态接入 Agent](s19-mcp-plugin.md) | 已完成 |
| S20 | Comprehensive Agent | [把十九种机制归到同一个 Agent 循环](s20-comprehensive.md) | 已完成 |

## 推荐学习记录

每做完一个实验，记录下面四项即可：

```text
实验：
我的提示词：
Agent 实际调用的工具/命令：
结果与我的解释：
```

如果结果和指南不同，先不要急着把它当成错误。优先判断：

- 任务是否真的完成；
- Agent 是否读取了工具结果后继续决策；
- 最终停止时是否不再请求工具；
- 差异来自模型的策略选择，还是来自代码、环境或权限问题。
