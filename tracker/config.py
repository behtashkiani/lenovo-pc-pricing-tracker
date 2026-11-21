"""Settings, read once from the environment (and from `.env` in the project root)."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

try:  # python-dotenv is a dependency, but the pure-logic modules must import without it
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except ImportError:  # pragma: no cover
    pass


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    return value.strip() if value and value.strip() else default


# Brand whose Best Buy laptops define the baseline spec groups.
ANCHOR_BRAND: str = _env("ANCHOR_BRAND", "Lenovo")

# Primary collector.
BESTBUY_GRAPHQL_URL: str = _env("BESTBUY_GRAPHQL_URL", "https://www.bestbuy.com/gateway/graphql")
REQUEST_DELAY_SECONDS: float = float(_env("REQUEST_DELAY_SECONDS", "1.0"))

# Local store: offline mode, and the journal that is replayed into Postgres.
DATA_DIR: Path = Path(_env("TRACKER_DATA_DIR", str(ROOT / "data")))

# Postgres (Supabase). Unset, or still holding the dashboard's placeholder, means
# "not configured": every script then works against the local store only.
SUPABASE_DB_URL: str | None = _env("SUPABASE_DB_URL")

# Optional paid backfill source.
SHOPSAVVY_API_KEY: str | None = _env("SHOPSAVVY_API_KEY")


def db_configured() -> bool:
    url = SUPABASE_DB_URL
    return bool(url) and "[YOUR-" not in url and "<" not in url
