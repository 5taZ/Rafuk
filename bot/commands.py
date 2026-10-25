from __future__ import annotations

BOT_COMMANDS: tuple[tuple[str, str], ...] = (
    ("app", "Открыть мини-апп"),
    ("start", "Приветствие"),
    ("help", "Список команд"),
    ("deals", "Активные сделки"),
    # TG-18: the figures cover all time, not a month.
    ("profit", "Прибыль и ROI"),
    ("stats", "Статистика по запросу"),
)

BOT_HELP_TEXT = "\n".join(f"/{command} — {description}" for command, description in BOT_COMMANDS)
