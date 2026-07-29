"""Cross-platform Bash execution for the course examples.

The course exposes a tool named ``bash``. On POSIX systems ``shell=True``
usually happens to select a compatible shell, but on Windows it selects
``cmd.exe``. That breaks commands such as ``cat``, ``ls`` and ``rm`` and can
also crash while decoding localized CMD error messages.

All runnable lessons delegate their Bash tool to this module so the behavior
matches the tool name on every supported platform.
"""

from __future__ import annotations

import locale
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Sequence


DEFAULT_TIMEOUT = 120
DEFAULT_MAX_OUTPUT = 50_000


def _existing_file(path: str | os.PathLike[str] | None) -> str | None:
    if not path:
        return None
    resolved = Path(path).expanduser()
    return str(resolved) if resolved.is_file() else None


def find_bash() -> str | None:
    """Return a Bash executable, honoring an explicit user override first."""
    for variable in ("BASH_EXECUTABLE", "GIT_BASH_PATH"):
        configured = _existing_file(os.getenv(variable))
        if configured:
            return configured

    discovered = shutil.which("bash")
    if discovered:
        if os.name == "nt":
            # Git for Windows' bin/bash.exe is a launcher. Some distributions
            # keep captured pipes open for ~30 seconds when a command exits
            # non-zero. Prefer the direct usr/bin executable when available.
            direct = Path(discovered).parent.parent / "usr" / "bin" / "bash.exe"
            if direct.is_file():
                return str(direct)
        return discovered

    if os.name == "nt":
        candidates = [
            Path(os.getenv("ProgramFiles", "")) / "Git" / "usr" / "bin" / "bash.exe",
            Path(os.getenv("ProgramFiles", "")) / "Git" / "bin" / "bash.exe",
            Path(os.getenv("LOCALAPPDATA", "")) / "Programs" / "Git" / "usr" / "bin" / "bash.exe",
            Path(os.getenv("LOCALAPPDATA", "")) / "Programs" / "Git" / "bin" / "bash.exe",
        ]
        for candidate in candidates:
            if candidate.is_file():
                return str(candidate)
    return None


def bash_argv(command: str) -> Sequence[str]:
    """Build an argv that always invokes Bash rather than the platform shell."""
    bash = find_bash()
    if not bash:
        if os.name == "nt":
            raise FileNotFoundError(
                "Bash was not found. Install Git for Windows, or set "
                "BASH_EXECUTABLE in .env to the full path of bash.exe."
            )
        raise FileNotFoundError("Bash was not found on PATH.")

    # Avoid user startup scripts: agent commands should be deterministic, and
    # some Windows Git Bash profiles are surprisingly slow or change encoding.
    return [bash, "--noprofile", "--norc", "-c", command]


def decode_output(data: bytes | None) -> str:
    """Decode subprocess bytes without crashing on Windows UTF-8/GBK output."""
    if not data:
        return ""

    candidates = ["utf-8"]
    preferred = locale.getpreferredencoding(False)
    if preferred:
        candidates.append(preferred)
    if os.name == "nt":
        candidates.extend(["mbcs", "gb18030"])

    for encoding in dict.fromkeys(candidates):
        try:
            return data.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace")


def run_process(
    args: Sequence[str],
    *,
    cwd: str | os.PathLike[str] | None = None,
    timeout: int | float | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run an argv command and decode its output with the same safe policy."""
    if os.name == "nt":
        # File-backed capture avoids subprocess's decoding reader threads. A
        # localized Windows error can therefore never turn stdout into None
        # midway through collection, which caused the original double failure.
        with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
            raw = subprocess.run(
                args,
                cwd=cwd or os.getcwd(),
                stdout=stdout_file,
                stderr=stderr_file,
                timeout=timeout,
                shell=False,
            )
            stdout_file.seek(0)
            stderr_file.seek(0)
            stdout = stdout_file.read()
            stderr = stderr_file.read()
    else:
        raw = subprocess.run(
            args,
            cwd=cwd or os.getcwd(),
            capture_output=True,
            timeout=timeout,
            shell=False,
        )
        stdout = raw.stdout
        stderr = raw.stderr

    return subprocess.CompletedProcess(
        args=raw.args,
        returncode=raw.returncode,
        stdout=decode_output(stdout),
        stderr=decode_output(stderr),
    )


def run_bash_command(
    command: str,
    *,
    cwd: str | os.PathLike[str] | None = None,
    timeout: int | float = DEFAULT_TIMEOUT,
    max_output: int = DEFAULT_MAX_OUTPUT,
) -> str:
    """Execute one Bash command and return combined stdout/stderr text."""
    try:
        completed = run_process(
            bash_argv(command),
            cwd=cwd or os.getcwd(),
            timeout=timeout,
        )
        output = (completed.stdout + completed.stderr).strip()
        return output[:max_output] if output else "(no output)"
    except subprocess.TimeoutExpired:
        return f"Error: Timeout ({timeout:g}s)"
    except (FileNotFoundError, OSError) as error:
        return f"Error: {error}"
