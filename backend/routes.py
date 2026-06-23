"""
API Routes for the Transaction Ranking Service.

This module defines three endpoints:

┌────────────────────────┬────────────────────────────────────────────┐
│ Endpoint               │ Purpose                                    │
├────────────────────────┼────────────────────────────────────────────┤
│ POST   /transaction    │ Create a new financial transaction         │
│ GET    /summary/{uid}  │ Retrieve a user's aggregated summary       │
│ GET    /ranking        │ Fetch the global leaderboard               │
└────────────────────────┴────────────────────────────────────────────┘

Cross-cutting concerns handled here:
  • Request validation     – via Pydantic schemas
  • Duplicate prevention   – idempotency_key checked before INSERT;
                             DB UNIQUE constraint as final guard
  • Rate limiting          – SlowAPI limits POST /transaction per IP
  • Abuse prevention       – amount caps, format validation, rate limits
  • Concurrency safety     – PostgreSQL handles concurrent INSERTs;
                             UNIQUE constraint prevents double-spending
"""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy.exc import IntegrityError

from backend.config import RATE_LIMIT_PER_MINUTE
from backend.database import (
    insert_transaction,
    fetch_user_transactions,
    fetch_user_aggregate,
    check_idempotency_key_exists,
)
from backend.ranking import compute_ranking
from backend.schemas import (
    TransactionCreate,
    TransactionResponse,
    UserSummary,
    RankingEntry,
    ErrorResponse,
)

router = APIRouter()

# ── Rate Limiter ─────────────────────────────────────────────────────
limiter = Limiter(key_func=get_remote_address)


# ══════════════════════════════════════════════════════════════════════
# POST /transaction
# ══════════════════════════════════════════════════════════════════════
#
# Creates a new financial transaction (credit or debit).
#
# Request body (JSON):
#   user_id          – str, 3-50 chars, alphanumeric / _ / -
#   amount           – float, > 0, ≤ 1 000 000
#   type             – str, 'credit' or 'debit'
#   description      – str, optional, max 200 chars
#   idempotency_key  – str, 8-128 chars, alphanumeric / _ / -
#
# Success Response (201):
#   { id, user_id, amount, type, description, idempotency_key,
#     created_at, message }
#
# Error Responses:
#   409 – Duplicate idempotency_key (returns the original transaction)
#   422 – Validation error (Pydantic)
#   429 – Rate limit exceeded
#   500 – Unexpected server error
#
# Duplicate Prevention:
#   1. Application-level check: query by idempotency_key before INSERT.
#   2. Database-level guard:   UNIQUE constraint on idempotency_key
#      catches any race between the check and the INSERT.
#   Both layers ensure exactly-once processing.
#
# Concurrency Safety:
#   PostgreSQL's MVCC + the UNIQUE constraint guarantee that even if
#   two identical requests arrive simultaneously, only one INSERT
#   succeeds; the other receives IntegrityError → 409.
# ══════════════════════════════════════════════════════════════════════
@router.post(
    "/transaction",
    response_model=TransactionResponse,
    status_code=201,
    responses={409: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
    summary="Create a new transaction",
    description="Submit a credit or debit transaction with an idempotency key.",
)
@limiter.limit(f"{RATE_LIMIT_PER_MINUTE}/minute")
async def create_transaction(request: Request, body: TransactionCreate):
    # ── Step 1: Check idempotency key (fast path) ────────────────────
    existing = await check_idempotency_key_exists(body.idempotency_key)
    if existing:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Duplicate request – transaction already processed",
                "existing_transaction": existing,
            },
        )

    # ── Step 2: Attempt INSERT (DB-level guard for races) ────────────
    tx_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    try:
        record = await insert_transaction(
            tx_id=tx_id,
            user_id=body.user_id,
            amount=body.amount,
            tx_type=body.type,
            description=body.description,
            idempotency_key=body.idempotency_key,
            created_at=now,
        )
    except IntegrityError:
        # Race condition: another concurrent request won the INSERT
        existing = await check_idempotency_key_exists(body.idempotency_key)
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Duplicate request – transaction already processed (concurrent)",
                "existing_transaction": existing,
            },
        )

    return TransactionResponse(
        id=record["id"],
        user_id=record["user_id"],
        amount=record["amount"],
        type=record["type"],
        description=record["description"],
        idempotency_key=record["idempotency_key"],
        created_at=record["created_at"],
        message="Transaction created successfully",
    )


# ══════════════════════════════════════════════════════════════════════
# GET /summary/{user_id}
# ══════════════════════════════════════════════════════════════════════
#
# Returns an aggregated financial summary for a single user.
#
# Path parameter:
#   user_id – str, the user whose summary is requested
#
# Success Response (200):
#   { user_id, total_credits, total_debits, net_balance,
#     transaction_count, transactions: [...] }
#
# Error Responses:
#   404 – User has no transactions
#
# Notes:
#   • The aggregate is computed on-the-fly via SQL GROUP BY, so it
#     always reflects the latest state of the database.
#   • The full transaction list is included for convenience.
# ══════════════════════════════════════════════════════════════════════
@router.get(
    "/summary/{user_id}",
    response_model=UserSummary,
    responses={404: {"model": ErrorResponse}},
    summary="Get user summary",
    description="Retrieve aggregated credits, debits, net balance, and full transaction list.",
)
async def get_user_summary(user_id: str):
    aggregate = await fetch_user_aggregate(user_id)
    if not aggregate:
        raise HTTPException(
            status_code=404,
            detail=f"No transactions found for user '{user_id}'",
        )

    transactions = await fetch_user_transactions(user_id)

    return UserSummary(
        user_id=aggregate["user_id"],
        total_credits=round(aggregate["total_credits"], 2),
        total_debits=round(aggregate["total_debits"], 2),
        net_balance=round(aggregate["net_balance"], 2),
        transaction_count=aggregate["tx_count"],
        transactions=transactions,
    )


# ══════════════════════════════════════════════════════════════════════
# GET /ranking
# ══════════════════════════════════════════════════════════════════════
#
# Returns the global leaderboard sorted by composite score (descending).
#
# Success Response (200):
#   [ { rank, user_id, score, transaction_count, net_balance,
#       total_credits, total_debits }, ... ]
#
# Ranking Algorithm (see ranking.py for full details):
#   score = 0.35 × norm(tx_count)
#         + 0.45 × norm(net_balance)
#         + 0.20 × norm(type_variety_ratio)
#
#   Each factor is min-max normalised across all users so no single
#   dimension dominates.
#
# Anti-manipulation:
#   • Scores are server-computed – users cannot submit scores.
#   • Rate limiting prevents spamming transactions to inflate volume.
#   • Variety factor penalises one-dimensional activity.
# ══════════════════════════════════════════════════════════════════════
@router.get(
    "/ranking",
    response_model=list[RankingEntry],
    summary="Get global ranking",
    description="Fetch the leaderboard based on multi-factor composite scoring.",
)
async def get_ranking():
    return await compute_ranking()
