"""
Database layer – PostgreSQL via SQLAlchemy 2.0 async + asyncpg.

Schema
------
transactions
    id              UUID PRIMARY KEY   – server-generated UUID v4
    user_id         VARCHAR(50)        – identifies the user
    amount          NUMERIC(12,2)      – positive monetary value
    type            VARCHAR(6)         – 'credit' or 'debit'
    description     VARCHAR(200)       – optional memo
    idempotency_key VARCHAR(128) UNIQUE – prevents duplicate processing
    created_at      TIMESTAMPTZ        – when the record was created

Indexes
-------
• UNIQUE on idempotency_key → rejects duplicate writes at the DB level
• B-tree on user_id         → fast per-user lookups & aggregations

Data Flow
---------
1. On startup, `init_db()` runs CREATE TABLE IF NOT EXISTS and seeds
   demo data when the table is empty.
2. Writes go through `insert_transaction()` which relies on the UNIQUE
   constraint on `idempotency_key` to reject duplicates.  The caller
   catches `IntegrityError` and returns 409 Conflict.
3. Reads use standard SQL with GROUP BY for efficient per-user summaries
   and cross-user ranking aggregates.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    String,
    Numeric,
    DateTime,
    Integer,
    func,
    case,
    text,
    select,
    distinct,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

from backend.config import DATABASE_URL

# ── Engine & Session Factory ─────────────────────────────────────────
engine = create_async_engine(DATABASE_URL, echo=False, pool_size=10, max_overflow=20)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


# ── ORM Model ────────────────────────────────────────────────────────
class Base(DeclarativeBase):
    pass


class Transaction(Base):
    """
    Represents a single financial transaction.

    The `idempotency_key` column has a UNIQUE constraint so that
    concurrent duplicate requests are safely rejected by PostgreSQL
    even under heavy load.
    """

    __tablename__ = "transactions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(String(50), nullable=False, index=True)
    amount = Column(Numeric(12, 2), nullable=False)
    type = Column(String(6), nullable=False)  # 'credit' | 'debit'
    description = Column(String(200), default="")
    idempotency_key = Column(String(128), unique=True, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))


# ── Lifecycle ────────────────────────────────────────────────────────

async def init_db() -> None:
    """
    Create tables (if they don't exist) and seed demo data when the
    transactions table is empty.
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Seed if empty
    async with async_session() as session:
        result = await session.execute(
            select(func.count()).select_from(Transaction)
        )
        count = result.scalar()
        if count == 0:
            await _seed_demo_data(session)


async def close_db() -> None:
    """Dispose of the connection pool."""
    await engine.dispose()


# ── CRUD Helpers ─────────────────────────────────────────────────────

async def insert_transaction(
    *,
    tx_id: str,
    user_id: str,
    amount: float,
    tx_type: str,
    description: str,
    idempotency_key: str,
    created_at: str,
) -> dict:
    """
    Insert a transaction row and return the created record as a dict.

    Raises `sqlalchemy.exc.IntegrityError` on duplicate idempotency_key.
    """
    txn = Transaction(
        id=uuid.UUID(tx_id),
        user_id=user_id,
        amount=amount,
        type=tx_type,
        description=description,
        idempotency_key=idempotency_key,
        created_at=datetime.fromisoformat(created_at),
    )
    async with async_session() as session:
        session.add(txn)
        await session.commit()
        await session.refresh(txn)
        return _row_to_dict(txn)


async def fetch_user_transactions(user_id: str) -> list[dict]:
    """Return all transactions for a given user, newest first."""
    async with async_session() as session:
        result = await session.execute(
            select(Transaction)
            .where(Transaction.user_id == user_id)
            .order_by(Transaction.created_at.desc())
        )
        rows = result.scalars().all()
        return [_row_to_dict(r) for r in rows]


async def fetch_all_user_aggregates() -> list[dict]:
    """
    Return per-user aggregates needed for ranking.

    Columns: user_id, total_credits, total_debits, net_balance,
             tx_count, distinct_types
    """
    stmt = (
        select(
            Transaction.user_id,
            func.sum(
                case((Transaction.type == "credit", Transaction.amount), else_=0)
            ).label("total_credits"),
            func.sum(
                case((Transaction.type == "debit", Transaction.amount), else_=0)
            ).label("total_debits"),
            func.sum(
                case(
                    (Transaction.type == "credit", Transaction.amount),
                    else_=-Transaction.amount,
                )
            ).label("net_balance"),
            func.count().label("tx_count"),
            func.count(distinct(Transaction.type)).label("distinct_types"),
        )
        .group_by(Transaction.user_id)
    )
    async with async_session() as session:
        result = await session.execute(stmt)
        return [
            {
                "user_id": row.user_id,
                "total_credits": float(row.total_credits),
                "total_debits": float(row.total_debits),
                "net_balance": float(row.net_balance),
                "tx_count": row.tx_count,
                "distinct_types": row.distinct_types,
            }
            for row in result.all()
        ]


async def fetch_user_aggregate(user_id: str) -> dict | None:
    """Return aggregate stats for a single user, or None."""
    stmt = (
        select(
            Transaction.user_id,
            func.sum(
                case((Transaction.type == "credit", Transaction.amount), else_=0)
            ).label("total_credits"),
            func.sum(
                case((Transaction.type == "debit", Transaction.amount), else_=0)
            ).label("total_debits"),
            func.sum(
                case(
                    (Transaction.type == "credit", Transaction.amount),
                    else_=-Transaction.amount,
                )
            ).label("net_balance"),
            func.count().label("tx_count"),
            func.count(distinct(Transaction.type)).label("distinct_types"),
        )
        .where(Transaction.user_id == user_id)
        .group_by(Transaction.user_id)
    )
    async with async_session() as session:
        result = await session.execute(stmt)
        row = result.first()
        if not row:
            return None
        return {
            "user_id": row.user_id,
            "total_credits": float(row.total_credits),
            "total_debits": float(row.total_debits),
            "net_balance": float(row.net_balance),
            "tx_count": row.tx_count,
            "distinct_types": row.distinct_types,
        }


async def check_idempotency_key_exists(key: str) -> dict | None:
    """
    Check if a transaction with the given idempotency_key already exists.
    Returns the existing record as a dict if found, None otherwise.
    """
    async with async_session() as session:
        result = await session.execute(
            select(Transaction).where(Transaction.idempotency_key == key)
        )
        txn = result.scalar_one_or_none()
        return _row_to_dict(txn) if txn else None


# ── Serialisation ────────────────────────────────────────────────────

def _row_to_dict(txn: Transaction) -> dict:
    """Convert an ORM Transaction to a plain dict for API responses."""
    return {
        "id": str(txn.id),
        "user_id": txn.user_id,
        "amount": float(txn.amount),
        "type": txn.type,
        "description": txn.description,
        "idempotency_key": txn.idempotency_key,
        "created_at": txn.created_at.isoformat(),
    }


# ── Demo Seed Data ───────────────────────────────────────────────────

async def _seed_demo_data(session: AsyncSession) -> None:
    """
    Insert a small set of realistic demo transactions so the app is
    usable immediately after first run.

    Assumptions / Mock Data Notes:
    • User IDs follow the pattern 'user_<name>'.
    • Amounts represent a mix of income, expenses, and rewards to
      showcase all features of the summary and ranking endpoints.
    • Idempotency keys for seed data are random UUIDs (one-time use).
    """
    demo = [
        # (user_id, amount, type, description)
        ("user_alice",   5000.00, "credit", "Salary deposit"),
        ("user_alice",   1200.50, "debit",  "Rent payment"),
        ("user_alice",    250.00, "credit", "Freelance gig"),
        ("user_alice",     75.00, "debit",  "Groceries"),
        ("user_bob",     3200.00, "credit", "Salary deposit"),
        ("user_bob",      800.00, "debit",  "Utilities"),
        ("user_bob",      150.00, "credit", "Cashback reward"),
        ("user_charlie", 8000.00, "credit", "Consulting fee"),
        ("user_charlie", 2500.00, "debit",  "Equipment purchase"),
        ("user_charlie",  600.00, "debit",  "Software subscription"),
        ("user_charlie", 1000.00, "credit", "Bonus"),
        ("user_diana",   1500.00, "credit", "Part-time income"),
        ("user_diana",    400.00, "debit",  "Transport"),
        ("user_eve",     4500.00, "credit", "Salary deposit"),
        ("user_eve",      900.00, "debit",  "Insurance premium"),
        ("user_eve",      300.00, "credit", "Dividend"),
        ("user_eve",      200.00, "debit",  "Subscriptions"),
    ]

    now = datetime.now(timezone.utc)
    for user_id, amount, tx_type, desc in demo:
        txn = Transaction(
            id=uuid.uuid4(),
            user_id=user_id,
            amount=amount,
            type=tx_type,
            description=desc,
            idempotency_key=str(uuid.uuid4()),
            created_at=now,
        )
        session.add(txn)

    await session.commit()
