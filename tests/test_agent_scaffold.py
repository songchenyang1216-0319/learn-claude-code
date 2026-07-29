from contextlib import redirect_stdout
import io
from pathlib import Path
import py_compile
import runpy
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCAFFOLD = ROOT / "skills" / "agent-builder" / "scripts" / "init_agent.py"


class AgentScaffoldTests(unittest.TestCase):
    def test_generated_agents_include_provider_and_shell_adapters(self):
        create_agent = runpy.run_path(str(SCAFFOLD))["create_agent"]

        with tempfile.TemporaryDirectory(dir=ROOT) as temp_dir:
            destination = Path(temp_dir)
            for level in (0, 1):
                name = f"generated_level_{level}"
                with redirect_stdout(io.StringIO()):
                    create_agent(name, level, destination)

                agent_dir = destination / name
                source = (agent_dir / f"{name}.py").read_text(encoding="utf-8")
                self.assertIn("create_client(Anthropic)", source)
                self.assertIn("run_bash_command", source)
                self.assertNotIn("shell=True", source)
                self.assertTrue((agent_dir / "provider_client.py").is_file())
                self.assertTrue((agent_dir / "shell_runner.py").is_file())
                py_compile.compile(
                    str(agent_dir / f"{name}.py"),
                    doraise=True,
                )


if __name__ == "__main__":
    unittest.main()
