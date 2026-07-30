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
| S09 | Memory | 待编写 | 待开始 |
| S10 | System Prompt | 待编写 | 待开始 |
| S11 | Error Recovery | 待编写 | 待开始 |
| S12 | Task System | 待编写 | 待开始 |
| S13 | Background Tasks | 待编写 | 待开始 |
| S14 | Cron Scheduler | 待编写 | 待开始 |
| S15 | Agent Teams | 待编写 | 待开始 |
| S16 | Team Protocols | 待编写 | 待开始 |
| S17 | Autonomous Agents | 待编写 | 待开始 |
| S18 | Worktree Isolation | 待编写 | 待开始 |
| S19 | MCP Plugin | 待编写 | 待开始 |
| S20 | Comprehensive Agent | 待编写 | 待开始 |

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
