from __future__ import annotations

import hashlib
import json
import re
import subprocess
import tarfile
from pathlib import Path

import pytest

from scripts.vps.deploy import build_release


def _repository(root: Path) -> None:
    (root / "scripts/vps").mkdir(parents=True)
    (root / "api").mkdir()
    (root / "api/main.py").write_text("before\n", encoding="utf-8")
    subprocess.run(["git", "init", "--quiet", str(root)], check=True)
    subprocess.run(["git", "add", "api/main.py"], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                    "commit", "--quiet", "-m", "fixture"], cwd=root, check=True)


def test_release_contains_current_working_files_and_verifiable_manifest(tmp_path: Path) -> None:
    _repository(tmp_path)
    (tmp_path / "api/main.py").write_bytes(b"current working source\n")
    (tmp_path / ".env").write_text("PRIVATE=must-stay-local\n", encoding="utf-8")
    (tmp_path / "scripts/vps/run.sh").write_bytes(b"#!/bin/bash\r\ntrue\r\n")
    result = build_release(tmp_path)
    assert re.fullmatch(r"\d{8}-\d{6}-[a-f0-9]{7}-[a-f0-9]{8}", result["release"])
    assert hashlib.sha256(Path(result["archive"]).read_bytes()).hexdigest() == result["sha256"]
    with tarfile.open(result["archive"]) as archive:
        assert ".env" not in archive.getnames()
        assert not any(name.startswith((".git/", ".run/")) for name in archive.getnames())
        assert archive.extractfile("api/main.py").read() == b"current working source\n"
        assert archive.extractfile("scripts/vps/run.sh").read() == b"#!/bin/bash\ntrue\n"
        assert archive.getmember("scripts/vps/run.sh").mode == 0o755
        manifest = json.load(archive.extractfile("DEPLOYMENT.json"))
        for name, digest in manifest["files"].items():
            assert hashlib.sha256(archive.extractfile(name).read()).hexdigest() == digest
        assert archive.extractfile("REVISION").read().decode().strip() == manifest["release"]


@pytest.mark.parametrize("name", [".env", "notmyfault.md", "server.key", "local.db"])
def test_release_refuses_private_files_even_if_accidentally_tracked(
    tmp_path: Path, name: str,
) -> None:
    _repository(tmp_path)
    (tmp_path / name).write_text("private", encoding="utf-8")
    subprocess.run(["git", "add", "--", name], cwd=tmp_path, check=True)
    with pytest.raises(ValueError, match="Refusing to package"):
        build_release(tmp_path)


def test_release_ships_untracked_runtime_modules_without_git_add(tmp_path: Path) -> None:
    # PRJ-REVIEW-2: a module nobody added to git used to be left out silently
    # and crash the release on import. It ships now, without requiring a
    # write to .git (sandboxed agents cannot run git add).
    _repository(tmp_path)
    (tmp_path / "migrations/versions").mkdir(parents=True)
    (tmp_path / "migrations/versions/0037_new.py").write_bytes(b"x = 1\n")
    (tmp_path / "notes.txt").write_text("scratch\n", encoding="utf-8")
    result = build_release(tmp_path)
    assert result["uncommitted_files"] == ["migrations/versions/0037_new.py"]
    with tarfile.open(result["archive"]) as archive:
        assert archive.extractfile("migrations/versions/0037_new.py").read() == b"x = 1\n"
        assert "notes.txt" not in archive.getnames()
        manifest = json.load(archive.extractfile("DEPLOYMENT.json"))
    assert manifest["untracked_files"] == ["migrations/versions/0037_new.py"]


def test_untracked_private_runtime_files_are_still_refused(tmp_path: Path) -> None:
    _repository(tmp_path)
    (tmp_path / "api/dump.sqlite").write_text("private", encoding="utf-8")
    with pytest.raises(ValueError, match="Refusing to package"):
        build_release(tmp_path)


def test_manifest_records_files_that_differ_from_the_revision(tmp_path: Path) -> None:
    _repository(tmp_path)
    clean = build_release(tmp_path)
    assert clean["uncommitted_files"] == []
    (tmp_path / "api/main.py").write_text("edited\n", encoding="utf-8")
    dirty = build_release(tmp_path)
    assert dirty["uncommitted_files"] == ["api/main.py"]
    with tarfile.open(dirty["archive"]) as archive:
        manifest = json.load(archive.extractfile("DEPLOYMENT.json"))
    assert manifest["git_dirty"] is True
    assert manifest["uncommitted_files"] == ["api/main.py"]
