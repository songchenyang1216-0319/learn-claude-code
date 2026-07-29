import os
from pathlib import Path
import tempfile
import unittest

from shell_runner import (
    bash_argv,
    decode_output,
    find_bash,
    run_bash_command,
    run_process,
)


ROOT = Path(__file__).resolve().parents[1]


class ShellRunnerTests(unittest.TestCase):
    def test_bash_is_available_and_invoked_without_platform_shell(self):
        bash = find_bash()
        self.assertIsNotNone(bash)
        argv = bash_argv("echo ok")
        self.assertEqual(argv[0], bash)
        self.assertEqual(argv[-1], "echo ok")
        self.assertIn("--noprofile", argv)

    def test_utf8_output(self):
        self.assertEqual(run_bash_command("printf '你好'"), "你好")

    @unittest.skipUnless(os.name == "nt", "GBK is a Windows compatibility case")
    def test_gbk_output_falls_back_without_crashing(self):
        self.assertEqual(decode_output("中文错误".encode("gb18030")), "中文错误")

    def test_create_read_and_remove_file(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temp_dir:
            output = run_bash_command(
                "printf 'hello agent' > demo.txt && cat demo.txt && rm demo.txt",
                cwd=temp_dir,
            )
            self.assertEqual(output, "hello agent")
            self.assertFalse((Path(temp_dir) / "demo.txt").exists())

    def test_command_output_is_returned_as_text(self):
        output = run_bash_command("printf 'simulated command output'")
        self.assertIn("simulated command output", output)

    def test_generic_process_returns_decoded_completed_process(self):
        result = run_process(bash_argv("printf process-ok"))
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "process-ok")
        self.assertEqual(result.stderr, "")


if __name__ == "__main__":
    unittest.main()
