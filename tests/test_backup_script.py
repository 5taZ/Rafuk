from __future__ import annotations

import os
import subprocess
from pathlib import Path


def test_backup_script_decodes_postgres_url_credentials(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    backup_dir = tmp_path / "backups"
    capture = tmp_path / "pgdump.env"
    bin_dir.mkdir()
    fake_pg_dump = bin_dir / "pg_dump"
    fake_pg_dump.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
{
  printf 'PGPASSWORD=%s\\n' "${PGPASSWORD:-}"
  printf 'ARGS=%s\\n' "$*"
} > "$PGDUMP_CAPTURE"
while (($#)); do
  if [[ "$1" == "-f" ]]; then
    shift
    printf 'fake dump\\n' > "$1"
    exit 0
  fi
  shift
done
exit 2
""",
    )
    fake_pg_dump.chmod(0o755)

    env = {
        **os.environ,
        "DATABASE_URL": "postgresql+asyncpg://user%2Bname:p%40ss%3Aword@db-host:6543/kufar%20db?sslmode=require",
        "BACKUP_DIR": str(backup_dir),
        "RETENTION_DAYS": "30",
        "PGDUMP_CAPTURE": str(capture),
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
    }
    result = subprocess.run(
        ["bash", "scripts/backup.sh"],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )

    assert "Backup created:" in result.stdout
    assert list(backup_dir.glob("kufar_*.dump"))
    captured = capture.read_text()
    assert "PGPASSWORD=p@ss:word\n" in captured
    assert "-U user+name" in captured
    assert "-h db-host" in captured
    assert "-p 6543" in captured
    assert "-d kufar db" in captured
