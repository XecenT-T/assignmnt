"""
Configuration module for the Transaction Ranking Service.

Centralises all application settings, PostgreSQL connection string,
rate-limit thresholds, and ranking weights so they can be tuned in one
place via environment variables (or a .env file).
"""

import os
from pathlib import Path

# ── Paths ────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent

# ── PostgreSQL ───────────────────────────────────────────────────────
# Async connection string used by SQLAlchemy + asyncpg.
# Default points to the Docker-Compose Postgres service.
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://txnuser:txnpass@localhost:5432/txn_ranker",
)

# ── Rate Limiting ────────────────────────────────────────────────────
# Maximum transactions a single user can submit per minute.
RATE_LIMIT_PER_MINUTE = int(os.getenv("RATE_LIMIT_PER_MINUTE", "10"))

# ── Ranking Weights ──────────────────────────────────────────────────
# The composite ranking score is a weighted sum of three normalised
# factors.  Adjust these to change ranking behaviour.
#   • VOLUME  – total number of transactions (rewards activity)
#   • NET     – net balance (credits − debits)  (rewards wealth)
#   • VARIETY – ratio of distinct transaction types used (rewards diversity)
RANKING_WEIGHT_VOLUME = float(os.getenv("RANKING_WEIGHT_VOLUME", "0.35"))
RANKING_WEIGHT_NET = float(os.getenv("RANKING_WEIGHT_NET", "0.45"))
RANKING_WEIGHT_VARIETY = float(os.getenv("RANKING_WEIGHT_VARIETY", "0.20"))

# ── Idempotency ──────────────────────────────────────────────────────
# How long (in seconds) to keep idempotency keys before expiring them.
IDEMPOTENCY_TTL_SECONDS = int(os.getenv("IDEMPOTENCY_TTL_SECONDS", "3600"))

# ── CORS ─────────────────────────────────────────────────────────────
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "*").split(",")
