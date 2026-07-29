from __future__ import annotations

from pathlib import Path
import py_compile
import unittest


ROOT = Path(__file__).resolve().parents[1]
AGENTS_DIR = ROOT / "agents"
AGENT_FILES = sorted(
    path for path in AGENTS_DIR.glob("*.py") if path.name != "__init__.py"
)


class AgentSmokeTests(unittest.TestCase):
    def test_agent_scripts_compile(self) -> None:
        self.assertTrue(AGENT_FILES, "expected at least one agent script")
        for agent_path in AGENT_FILES:
            with self.subTest(agent=agent_path.name):
                py_compile.compile(str(agent_path), doraise=True)


if __name__ == "__main__":
    unittest.main()