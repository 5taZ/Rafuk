"""Upload and activate a verified working-tree snapshot on the configured VPS."""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import subprocess
import tarfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PRIVATE_NAMES = {".env", "DEEP_DIVE_REVIEW_COMPREHENSIVE.md", "notmyfault.md"}
RUNTIME_DIRS = ("api/", "bot/", "scheduler/", "migrations/", "frontend/", "assets/", "nginx/")


def _git_names(root: Path, *args: str) -> list[str]:
    output = subprocess.check_output(["git", *args, "-z"], cwd=root).decode()
    return [name for name in output.split("\0") if name]


def untracked_runtime_files(root: Path) -> list[str]:
    """PRJ-REVIEW-2: the release used to pack tracked files only, so a new
    module nobody ran ``git add`` on was silently left out and the release
    crashed on import (or rolled back) on the server. They are packed now.
    Refusing to deploy instead blocked agents whose sandbox cannot write
    ``.git`` (Codex), which then committed from ad-hoc repository copies."""
    return [name for name in _git_names(root, "ls-files", "--others", "--exclude-standard")
            if name.startswith(RUNTIME_DIRS)]


def uncommitted_files(root: Path) -> list[str]:
    """Tracked files whose working copy differs from HEAD (what the release
    ships beyond its ``git_revision``)."""
    return sorted(set(_git_names(root, "diff", "--name-only", "HEAD")))


def release_files(root: Path) -> list[Path]:
    tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=root).decode().split("\0")
    extras = ["Manage-RafukVps.ps1", "host.cmd", "stop-host.cmd", "docs/VPS.md"]
    extensions = {".py", ".sh", ".yml", ".conf", ".service", ".timer"}
    extras += [path.relative_to(root).as_posix() for path in (root / "scripts/vps").iterdir()
               if path.is_file() and path.suffix in extensions]
    extras += ["scripts/vps/rafuk-vps"]
    files = []
    for name in sorted(set(filter(None, tracked + extras + untracked_runtime_files(root)))):
        path = root / name
        if not path.exists():
            continue
        private = path.name in PRIVATE_NAMES or (
            path.name.startswith(".env.") and path.name != ".env.example"
        ) or path.suffix in {".pem", ".key", ".db", ".sqlite", ".sqlite3", ".dump"}
        if private or path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError(f"Refusing to package private or linked path: {name}")
        if path.is_file():
            files.append(path)
    return files


def build_release(root: Path) -> dict:
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root).decode().strip()
    untracked = untracked_runtime_files(root)
    uncommitted = sorted(set(uncommitted_files(root) + untracked))
    contents = {}
    for path in release_files(root):
        data = path.read_bytes()
        if path.suffix == ".sh" or path.name == "rafuk-vps":
            data = data.replace(b"\r\n", b"\n")
        contents[path.relative_to(root).as_posix()] = data
    hashes = {name: hashlib.sha256(data).hexdigest() for name, data in contents.items()}
    digest = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()
    now = datetime.now(UTC)
    release = f"{now:%Y%m%d-%H%M%S}-{revision[:7]}-{digest[:8]}"
    manifest = {"release": release, "git_revision": revision,
                "git_dirty": bool(uncommitted), "uncommitted_files": uncommitted,
                "untracked_files": untracked,
                "created_at": now.isoformat(), "files": hashes}
    contents["DEPLOYMENT.json"] = json.dumps(manifest, indent=2).encode()
    contents["REVISION"] = (release + "\n").encode()
    destination = root / ".run/vps-deploy"
    destination.mkdir(parents=True, exist_ok=True)
    archive = destination / f"{release}.tar.gz"
    with tarfile.open(archive, "w:gz") as output:
        for name, data in contents.items():
            entry = tarfile.TarInfo(name)
            entry.size = len(data)
            executable = name.endswith(".sh") or name == "scripts/vps/rafuk-vps"
            entry.mode = 0o755 if executable else 0o644
            output.addfile(entry, io.BytesIO(data))
    return {"release": release, "archive": str(archive), "file_count": len(hashes),
            "uncommitted_files": uncommitted,
            "sha256": hashlib.sha256(archive.read_bytes()).hexdigest()}


def main() -> None:
    config = json.loads((ROOT / ".run/rafuk-vps.json").read_text(encoding="utf-8-sig"))
    host = config["host"]
    if not re.fullmatch(r"[a-zA-Z0-9.-]+", host):
        raise ValueError("Invalid configured VPS host")
    key = Path(os.path.expandvars(config["key"]))
    known_hosts = ROOT / ".run/vps-known_hosts"
    if not key.is_file() or not known_hosts.is_file():
        raise FileNotFoundError("Configured SSH key or pinned VPS host key is missing")
    options = ["-i", str(key), "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
               "-o", "StrictHostKeyChecking=yes", "-o", f"UserKnownHostsFile={known_hosts}",
               "-o", "ConnectTimeout=15", "-o", "ServerAliveInterval=15",
               "-o", "ServerAliveCountMax=3"]
    target = f"root@{host}"
    result = build_release(ROOT)
    if result["uncommitted_files"]:
        print(f"WARNING: {len(result['uncommitted_files'])} file(s) differ from the committed "
              "revision; the release is not reproducible from git until they are committed.",
              flush=True)
    print(f"Uploading {result['release']} ({result['file_count']} files)", flush=True)
    subprocess.run(["ssh.exe", *options, target, "install -d -m 0700 /opt/rafuk/incoming"],
                   check=True)
    subprocess.run(["scp.exe", *options, result["archive"],
                    f"{target}:/opt/rafuk/incoming/{result['release']}.tar.gz"], check=True)
    subprocess.run(["scp.exe", *options, str(ROOT / "scripts/vps/deploy-release.sh"),
                    f"{target}:/opt/rafuk/incoming/deploy-release.sh"], check=True)
    command = f"bash /opt/rafuk/incoming/deploy-release.sh {result['release']} {result['sha256']}"
    subprocess.run(["ssh.exe", *options, target, command], check=True)
    result["verified_at"] = datetime.now(UTC).isoformat()
    latest = ROOT / ".run/vps-deploy/latest.json"
    latest.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"Deployed and verified: {result['release']} at https://{host}", flush=True)


if __name__ == "__main__":
    main()
