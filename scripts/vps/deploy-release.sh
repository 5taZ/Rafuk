#!/usr/bin/env bash
set -euo pipefail
umask 077
release_id=${1:?Release ID required}
archive_hash=${2:?Archive SHA-256 required}
[[ "$release_id" =~ ^[0-9]{8}-[0-9]{6}-[a-f0-9]{7}-[a-f0-9]{8}$ ]]
[[ "$archive_hash" =~ ^[a-f0-9]{64}$ ]]
exec 9>/opt/rafuk/deploy.lock
flock -n 9
release=/opt/rafuk/releases/$release_id
archive=/opt/rafuk/incoming/$release_id.tar.gz
previous=$(readlink -f /opt/rafuk/current)
[[ "$previous" == /opt/rafuk/releases/* && -d "$previous" ]]
test ! -e "$release"
printf '%s  %s\n' "$archive_hash" "$archive" | sha256sum --check --status
install -d -m 0755 "$release"
tar -xzf "$archive" -C "$release" --no-same-owner
find "$release" -type d -exec chmod 0755 {} +
cd "$release"
python3 - <<'PY'
import hashlib, json
from pathlib import Path
root = Path.cwd()
manifest = json.loads((root / 'DEPLOYMENT.json').read_text())
for name, expected in manifest['files'].items():
    path = root / name
    assert path.resolve().is_relative_to(root) and path.is_file(), name
    assert hashlib.sha256(path.read_bytes()).hexdigest() == expected, name
print('Uploaded source hashes verified.', flush=True)
PY
compose=(docker compose --env-file /opt/rafuk/shared/.env -p rafuk-vps
    -f docker-compose.yml -f scripts/vps/compose.vps.yml --profile local-db)
RAFUK_RELEASE=$release_id RAFUK_FRONTEND_RELEASE=$release_id "${compose[@]}" config --quiet
COMPOSE_PARALLEL_LIMIT=1 RAFUK_RELEASE=$release_id RAFUK_FRONTEND_RELEASE=$release_id \
    "${compose[@]}" build api frontend

state=/opt/rafuk/shared/deployments/$release_id
install -d -m 0700 "$state"
cp /opt/rafuk/shared/compose.vps.yml "$state/compose.previous.yml"
python3 - "$state" <<'PY'
import json, sys
from pathlib import Path
keys = {'RAFUK_RELEASE', 'RAFUK_FRONTEND_RELEASE'}
values = {}
for line in Path('/opt/rafuk/shared/.env').read_text().splitlines():
    key, sep, value = line.partition('=')
    if sep and key in keys:
        values[key] = value
Path(sys.argv[1], 'tags.previous.json').write_text(json.dumps(values))
PY
set_tags() {
    python3 - "$state/tags.previous.json" "$1" <<'PY'
import json, sys
from pathlib import Path
path = Path('/opt/rafuk/shared/.env')
keys = {'RAFUK_RELEASE', 'RAFUK_FRONTEND_RELEASE'}
values = (json.loads(Path(sys.argv[1]).read_text()) if sys.argv[2] == 'rollback'
          else dict.fromkeys(keys, sys.argv[2]))
lines = [line for line in path.read_text().splitlines() if line.partition('=')[0] not in keys]
path.write_text('\n'.join(lines + [key + '=' + value for key, value in values.items()]) + '\n')
PY
}
activate_link() {
    local next=/opt/rafuk/current.$release_id.next
    if [[ -L "$next" ]]; then unlink "$next"; fi
    test ! -e "$next"
    ln -s "$1" "$next"
    mv -Tf "$next" /opt/rafuk/current
}
rollback() {
    trap - ERR
    set +e
    local restored=0
    if set_tags rollback && cp "$state/compose.previous.yml" /opt/rafuk/shared/compose.vps.yml \
        && activate_link "$previous" && cd "$previous" \
        && docker compose --env-file /opt/rafuk/shared/.env -p rafuk-vps \
        -f docker-compose.yml -f /opt/rafuk/shared/compose.vps.yml --profile local-db \
        up -d --no-build --wait --wait-timeout 180 api bot scheduler frontend; then restored=1; fi
    if [[ "$restored" == 1 ]]; then
        printf 'Deployment failed. Previous release restored: %s\n' "$previous" >&2
    else
        printf 'Deployment and rollback failed. Inspect /opt/rafuk/current and container health.\n' >&2
    fi
    exit 1
}
trap rollback ERR
set_tags "$release_id"
cp scripts/vps/compose.vps.yml /opt/rafuk/shared/compose.vps.yml
activate_link "$release"
docker compose --env-file /opt/rafuk/shared/.env -p rafuk-vps \
    -f docker-compose.yml -f /opt/rafuk/shared/compose.vps.yml --profile local-db \
    up -d --no-build --wait --wait-timeout 180 api bot scheduler frontend

docker exec -i rafuk-vps-api-1 /app/.venv/bin/python -c '
import hashlib, json, pathlib, sys
manifest = json.load(sys.stdin)
count = 0
for name, expected in manifest["files"].items():
    if name.startswith(("api/", "bot/", "scheduler/", "migrations/")):
        assert hashlib.sha256(pathlib.Path("/app", name).read_bytes()).hexdigest() == expected, name
        count += 1
print(f"Running Python image verified: {count} source files", flush=True)
' < DEPLOYMENT.json
python3 - <<'PY'
import concurrent.futures, hashlib, json, urllib.request
from pathlib import Path
manifest = json.loads(Path('DEPLOYMENT.json').read_text())
origin = 'https://2.27.206.15'
with urllib.request.urlopen(origin + '/api/v1/health/ready', timeout=20) as response:
    assert response.status == 200
assets = {name: digest for name, digest in manifest['files'].items()
          if name.startswith(('frontend/', 'assets/'))}
def verify(item):
    name, expected = item
    public = name.removeprefix('frontend/') if name.startswith('frontend/') else name
    if public == 'index.html':
        public = ''
    with urllib.request.urlopen(origin + '/' + public + '?release=' + manifest['release'],
                                timeout=30) as response:
        assert hashlib.sha256(response.read()).hexdigest() == expected, name
with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
    list(pool.map(verify, assets.items()))
print(f'Public HTTPS assets verified: {len(assets)} files', flush=True)
PY
trap - ERR
printf 'Deployment verified: %s\n' "$release_id"
/usr/local/sbin/rafuk-vps status
