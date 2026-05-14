#!/usr/bin/env bash
#
# INF-H8: roll the cache-busting query string on every JS/CSS asset
# referenced from frontend/index.html or lazy-loaded by frontend JS, and
# bump the service-worker cache bucket, in one shot. Use after touching
# any file under frontend/js or frontend/css.
#
# nginx serves /js/*.js and /css/*.css with `expires 365d` +
# `Cache-Control: public, immutable` so repeat visits are free; the
# trade-off is that the only way to ship a new build to existing
# users is to change the URL. Today the URL changes via a `?v=YYYYMMDD-vN`
# query string baked into <script src> / <link href> tags and lazy JS
# loaders. Keep the bump scripted so it doesn't get forgotten — old
# clients silently pinned to a security-patched-bypassing build is
# exactly the failure mode the immutable header creates.
#
# Usage:
#   scripts/bump_static_version.sh           # auto: ?v=YYYYMMDD-v<git-short-sha>
#   scripts/bump_static_version.sh 20260601-v9   # explicit tag
#
# Idempotent: running twice with the same tag is a no-op.

set -euo pipefail

cd "$(dirname "$0")/.."

ASSET_FILES=(frontend/index.html frontend/js/*.js)
INDEX="${ASSET_FILES[0]}"
SW_FILE="frontend/sw.js"
if [[ ! -f "$INDEX" ]]; then
    echo "error: $INDEX not found (run from repo root)" >&2
    exit 1
fi

if [[ $# -ge 1 ]]; then
    NEW_TAG="$1"
else
    DATE_PART="$(date -u +%Y%m%d)"
    if git rev-parse --short HEAD >/dev/null 2>&1; then
        SHA_PART="$(git rev-parse --short HEAD)"
    else
        SHA_PART="$(printf '%04x' "$RANDOM")"
    fi
    NEW_TAG="${DATE_PART}-${SHA_PART}"
fi

# Find the existing tag(s) — there should be only one used consistently
# across the file. Multiple tags mean a previous bump failed mid-way;
# we still rewrite all of them to the new tag.
EXISTING_TAGS="$(grep -hoE '\?v=[A-Za-z0-9._-]+' "${ASSET_FILES[@]}" | sort -u || true)"
if [[ -z "$EXISTING_TAGS" ]]; then
    echo "no ?v= tags found in $INDEX — nothing to do"
    exit 0
fi

for tag in $EXISTING_TAGS; do
    if [[ "$tag" == "?v=$NEW_TAG" ]]; then
        continue
    fi
    sed -i "s|${tag}|?v=${NEW_TAG}|g" "${ASSET_FILES[@]}"
done

if [[ -f "$SW_FILE" ]]; then
    sed -i -E "s|const CACHE_VERSION = \"[^\"]+\";|const CACHE_VERSION = \"rafuk-cache-${NEW_TAG}\";|" "$SW_FILE"
fi

REWROTE=$(grep -rho "?v=${NEW_TAG}" "${ASSET_FILES[@]}" | wc -l | tr -d " ")
echo "rewrote $REWROTE asset reference(s) → ?v=${NEW_TAG}"
