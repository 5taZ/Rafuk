"""Input-validation constants used by API routers.

Historically this module also exported `validate_*` helpers, but they
were never wired into the routers (FastAPI's pydantic + Query() handles
validation). Only the constants below are imported elsewhere; the
helpers were removed to avoid dead code drift.

If you ever want stricter validation than pydantic's defaults, create
a `Annotated[str, AfterValidator(...)]` type alias next to the model
so the contract lives with the schema.
"""

from __future__ import annotations

# ── Query constraints ──────────────────────────────────────────────────────
MAX_QUERY_LENGTH = 255
