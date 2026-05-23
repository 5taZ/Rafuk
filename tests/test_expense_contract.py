from __future__ import annotations

from pathlib import Path

from sqlalchemy import CheckConstraint

from api.models import DealExpense
from api.schemas import ExpenseTypeEnum

EXPENSE_TYPES = {"delivery", "repair", "customs", "packaging", "transport", "other"}


def test_expense_type_enum_matches_db_constraint() -> None:
    enum_values = {item.value for item in ExpenseTypeEnum}
    constraints = [
        constraint
        for constraint in DealExpense.__table__.constraints
        if isinstance(constraint, CheckConstraint)
        and constraint.name == "chk_deal_expenses_expense_type"
    ]
    assert enum_values == EXPENSE_TYPES
    assert constraints
    constraint_sql = str(constraints[0].sqltext)
    for expense_type in EXPENSE_TYPES:
        assert f"'{expense_type}'" in constraint_sql


def test_expense_type_ui_exposes_full_contract() -> None:
    index_text = Path("frontend/index.html").read_text(encoding="utf-8")
    for expense_type in EXPENSE_TYPES:
        assert f'value="{expense_type}"' in index_text
