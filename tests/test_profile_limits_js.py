from __future__ import annotations

import subprocess
from pathlib import Path


def test_profile_renders_role_quota_windows_and_tracker_capacity() -> None:
    harness = Path("frontend/js/render_profile.js").read_text(encoding="utf-8") + r"""
const assert = require("assert");
function element(tag, options = {}, ...children) {
    return {
        tag, options, children: children.flat(Infinity).filter(Boolean),
        appendChild(child) { this.children.push(child); },
    };
}
const root = element("div");
const state = { profile: { consents: {}, data: {
    status: { code: "bare_search", display_name: "Free" },
    permissions: { can_use_ai: false, can_use_assistant: false },
    limits: { trackers: { used: 2, limit: 3 } },
} } };
const renderer = createRenderProfile({
    state, elements: { profileContent: root }, formatDate: () => "",
    safeRender: (_name, fn) => fn(), domEl: element,
    domClear: (node) => { node.children = []; },
});
function text(node) {
    return [node.options.text || "", ...node.children.map(text)].join(" ");
}
renderer.renderProfile();
assert.match(text(root), /Лимиты аккаунта/);
assert.match(text(root), /Автотрекеры.*2 \/ 3/);
assert.doesNotMatch(text(root), /за 5 часов|за 7 дней/);
state.profile.data = {
    status: { code: "scout", display_name: "Pro" },
    permissions: { can_use_ai: true, can_use_assistant: true },
    limits: {
        ai_5h: { used: 7, limit: 20, remaining: 13, resets_at: "2026-11-08T15:00:00Z" },
        ai_weekly: { used: 7, limit: 100, remaining: 93, resets_at: "2026-11-15T10:00:00Z" },
        trackers: { used: 5, limit: 10 },
    },
};
renderer.renderProfile();
assert.match(text(root), /AI · за 5 часов.*7 \/ 20/);
assert.match(text(root), /AI · за 7 дней.*7 \/ 100/);
assert.match(text(root), /Автотрекеры.*5 \/ 10/);
assert.match(text(root), /Восстановление: 15/);
assert.doesNotMatch(text(root), /Лимиты сегодня|сброс в/);
state.profile.data.limits.ai_5h.available = false;
renderer.renderProfile();
assert.match(text(root), /— \/ 20/);
assert.match(text(root), /Расход временно недоступен/);
"""
    result = subprocess.run(["node"], input=harness, capture_output=True, encoding="utf-8")
    assert result.returncode == 0, result.stderr
