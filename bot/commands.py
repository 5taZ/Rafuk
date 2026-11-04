from __future__ import annotations

BOT_COMMANDS: tuple[tuple[str, str], ...] = (
    ("start", "Открыть Rafuk"),
)

BOT_HELP_TEXT = "\n".join(f"/{command} — {description}" for command, description in BOT_COMMANDS)
