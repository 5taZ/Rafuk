from __future__ import annotations

BOT_COMMANDS: tuple[tuple[str, str], ...] = (
    ("app", "Открыть мини-апп"),
    ("start", "Приветствие"),
    ("help", "Список команд"),
    ("deals", "Активные сделки"),
    ("profit", "Прибыль за месяц"),
    ("stats", "Статистика по запросу"),
)

BOT_HELP_TEXT = "\n".join(f"/{command} — {description}" for command, description in BOT_COMMANDS)
