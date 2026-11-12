"""Shell lookup for tests that run the repository's bash scripts."""

from __future__ import annotations

import os
import shutil
from pathlib import Path


def bash_executable() -> str:
    """PRJ-REVIEW-11: on Windows the first ``bash`` on PATH is often WSL's
    launcher in System32, which runs the script inside a Linux distro without
    the test's PATH stubs and fails; prefer Git for Windows' bash."""
    if os.name != "nt":
        return shutil.which("bash") or "bash"
    program_files = os.environ.get("PROGRAMFILES", r"C:\Program Files")
    candidates = [Path(program_files, "Git", "bin", "bash.exe")]
    if git := shutil.which("git"):
        candidates.insert(0, Path(git).resolve().parents[1] / "bin" / "bash.exe")
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    found = shutil.which("bash")
    if found and Path(found).parent.name.lower() != "system32":
        return found
    return "bash"
