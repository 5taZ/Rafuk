#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
JS_DIR="$ROOT/frontend/js"
OUT="$JS_DIR/app_bundle.js"

modules=(
  dom_helpers.js
  app_core_dom.js
  app_core.js
  render_core.js
  render_card_builders.js
  virtual_list.js
  render_cards.js
  render_views.js
  render_charts.js
  # OPUS-13 wave 70 — tracking-view modules ship as stubs.
  _lazy_trackers_stub.js
  # OPUS-13 wave 71 — deals-view modules api_leads + api_watchlist
  # plus render_modals ship as stubs and load on first use.
  _lazy_deals_stub.js
  app_renderers.js
  api_core.js
  api_listings.js
  api_events.js
  app_actions.js
  app.js
)

RAW="$(mktemp)"
trap 'rm -f "$RAW"' EXIT

{
  printf '(function (window) {\n'
  printf '"use strict";\n'
  printf 'window.App = window.App || {};\n'
  printf '\n'
} > "$RAW"
for module in "${modules[@]}"; do
  path="$JS_DIR/$module"
  if [[ ! -f "$path" ]]; then
    echo "missing frontend module: $path" >&2
    exit 1
  fi
  printf '\n;\n' >> "$RAW"
  cat "$path" >> "$RAW"
  printf '\n' >> "$RAW"
done

cat >> "$RAW" <<'EOF'

window.App = Object.assign(window.App || {}, {
  analyticsApp,
  createAppCore,
  domEl,
  domFragment,
  bindRovingTablist,
  _prefersReducedMotion,
  openModalAnimated,
  closeModalAnimated,
});
})(window);
EOF

# OPUS-13: minify the concatenated source. rjsmin only strips
# whitespace + comments (no identifier renaming) so the script
# stays byte-byte equivalent to the source modules' semantics —
# ``node --check`` runs against the minified output to catch any
# regression where rjsmin would otherwise eat a meaningful token.
RAW_SIZE=$(wc -c < "$RAW")
uv run python scripts/minify_js.py < "$RAW" > "$OUT"
node --check "$OUT" >/dev/null
MINI_SIZE=$(wc -c < "$OUT")
printf 'built %s from %d modules: %d → %d bytes (%d%% of source)\n' \
  "$OUT" "${#modules[@]}" "$RAW_SIZE" "$MINI_SIZE" "$((100 * MINI_SIZE / RAW_SIZE))"
