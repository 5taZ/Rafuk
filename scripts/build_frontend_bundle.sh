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
  virtual_list.js
  render_views.js
  render_profile.js
  render_admin.js
  # OPUS-13 wave 70 — tracking-view modules ship as stubs.
  _lazy_trackers_stub.js
  # OPUS-13 wave 71 — deals-view modules api_leads + api_watchlist
  # plus render_modals ship as stubs and load on first use.
  _lazy_deals_stub.js
  # OPUS-13 wave 72 — render_charts.js ships as a stub. Chart.js
  # itself was already lazy; this also defers the renderer.
  _lazy_charts_stub.js
  # OPUS-13 wave 73 — render_card_builders + render_cards ship as
  # stubs; virtual_list stays in the bundle because trackers + cards
  # both use createVirtualList through context.
  _lazy_cards_stub.js
  app_renderers.js
  api_core.js
  api_profile.js
  api_admin.js
  api_image_proxy.js
  api_listings.js
  api_events.js
  app_actions.js
  app.js
)

RAW="$(mktemp)"
trap 'rm -f "$RAW"' EXIT

# FE-NEW-1: compute sha384 SRI hashes for every lazy-loadable module so
# _loadScript can assert integrity at runtime. The map is emitted inside
# the IIFE right after "use strict" so it shares scope with _loadScript.
LAZY_MODULES=(
  render_trackers.js   # FE-NEW-1
  api_trackers.js      # FE-NEW-1
  api_leads.js         # FE-NEW-1
  api_watchlist.js     # FE-NEW-1
  render_modals.js     # FE-NEW-1
  render_charts.js     # FE-NEW-1
  render_card_builders.js  # FE-NEW-1
  render_cards.js      # FE-NEW-1
  api_ai_modal.js      # FE-NEW-1
  api_ai_render.js     # FE-NEW-1
  api_ai.js            # FE-NEW-1
  api_listing_assistant.js  # FE-NEW-1
  render_admin.js      # FE-NEW-1
)

_build_integrity_map() {
  printf 'const __LAZY_INTEGRITY = {\n'
  for lm in "${LAZY_MODULES[@]}"; do
    lm_path="$JS_DIR/$lm"
    if [[ -f "$lm_path" ]]; then
      hash=$(openssl dgst -sha384 -binary "$lm_path" | openssl base64 -A)
      printf '  "%s": "sha384-%s",\n' "$lm" "$hash"
    fi
  done
  printf '};\n'
}

{
  printf '(function (window) {\n'
  printf '"use strict";\n'
  printf 'window.App = window.App || {};\n'
  _build_integrity_map
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
  logClientError,
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
