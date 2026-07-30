# Summary

## Project Overview

**Learn Claude Code** — a 0-to-1 harness engineering tutorial that teaches how to build the operational environment (harness) around an AI agent model.

## Core Philosophy

> *Agency comes from the model. The harness gives agency a place to land.*

An agent product = **Model + Harness**. The model (LLM) supplies intelligence through training. The harness (tools, knowledge, permissions, context) supplies the environment in which the model operates.

## Requirements (dependencies)

| Package         | Version Constraint | Purpose                |
|-----------------|--------------------|------------------------|
| anthropic       | >=0.25.0           | Anthropic API client   |
| python-dotenv   | >=1.0.0            | Environment variables  |
| pyyaml          | >=6.0              | YAML configuration     |
| httpx           | >=0.27.0           | HTTP client            |

## Structure

### Two Tracks
- **Current**: 20 progressive lessons (`s01_agent_loop` → `s20_comprehensive`)
- **Legacy**: 12-lesson track (kept temporarily in `agents/`, `docs/`, `web/`)

### 6 Learning Stages

1. **Core Capabilities** — Agent Loop, Tool Use, Permissions, Hooks
2. **Complex Work** — TodoWrite (planning), Subagents, Context Compaction
3. **Memory & Recovery** — Memory, System Prompts, Error Recovery
4. **Long-Running Tasks** — Task System, Background Tasks, Cron Scheduler
5. **Multi-Agent Collaboration** — Teams, Protocols, Autonomous Agents, Worktree Isolation
6. **Extension & Integration** — Skill Loading, MCP Plugin, Comprehensive Agent

### Project Layout

```
learn-claude-code/
├── s01_agent_loop/ ... s20_comprehensive/   # 20 lesson chapters
├── agents/                                   # Legacy scripts
├── skills/                                   # Skill files (s07)
├── docs/                                     # Legacy docs
├── web/                                      # Web app (legacy track)
├── tests/
├── requirements.txt
└── README.md
```

## How to Run

```sh
git clone https://github.com/shareAI-lab/learn-claude-code
cd learn-claude-code
pip install -r requirements.txt
cp .env.example .env   # set ANTHROPIC_API_KEY
python s01_agent_loop/code.py
```

## License

MIT