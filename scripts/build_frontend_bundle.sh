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
  render_modals.js
  render_charts.js
  render_trackers.js
  app_renderers.js
  api_core.js
  api_listings.js
  api_trackers.js
  api_leads.js
  api_watchlist.js
  api_events.js
  app_actions.js
  app.js
)

{
  printf '(function (window) {\n'
  printf '"use strict";\n'
  printf 'window.App = window.App || {};\n'
  printf '\n'
} > "$OUT"
for module in "${modules[@]}"; do
  path="$JS_DIR/$module"
  if [[ ! -f "$path" ]]; then
    echo "missing frontend module: $path" >&2
    exit 1
  fi
  printf '\n;\n' >> "$OUT"
  cat "$path" >> "$OUT"
  printf '\n' >> "$OUT"
done

cat >> "$OUT" <<'EOF'

window.App = Object.assign(window.App || {}, {
  analyticsApp,
  createAppCore,
  domEl,
  openModalAnimated,
  closeModalAnimated,
});
})(window);
EOF

node --check "$OUT" >/dev/null
printf 'built %s from %d modules\n' "$OUT" "${#modules[@]}"
