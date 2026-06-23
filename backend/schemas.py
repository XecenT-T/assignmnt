"""
Pydantic models for request / response validation.

Every field is annotated with constraints and clear descriptions so
FastAPI auto-generates accurate OpenAPI docs.
"""

from pydantic import BaseModel, Field, field_validator
import re


# ──────────────────────────────────────────────────────────────────────
# POST /transaction  –  Request body
# ──────────────────────────────────────────────────────────────────────
class TransactionCreate(BaseModel):
    """
    Payload to create a new transaction.

    Fields
    ------
    user_id : str
        Alphanumeric identifier for the user (3-50 chars, underscores
        and hyphens allowed).
    amount : float
        Monetary value – must be positive and capped at 1 000 000.
    type : str
        Either 'credit' (money in) or 'debit' (money out).
    description : str
        Optional memo (max 200 chars).
    idempotency_key : str
        Client-generated unique key to prevent duplicate processing.
        Must be between 8 and 128 characters.
    """

    user_id: str = Field(
        ...,
        min_length=3,
        max_length=50,
        description="User identifier (alphanumeric, underscores, hyphens)",
        examples=["user_alice"],
    )
    amount: float = Field(
        ...,
        gt=0,
        le=1_000_000,
        description="Transaction amount (positive, max 1 000 000)",
        examples=[250.00],
    )
    type: str = Field(
        ...,
        description="Transaction type: 'credit' or 'debit'",
        examples=["credit"],
    )
    description: str = Field(
        default="",
        max_length=200,
        description="Optional description / memo",
        examples=["Salary deposit"],
    )
    idempotency_key: str = Field(
        ...,
        min_length=8,
        max_length=128,
        description="Unique key to prevent duplicate requests (8-128 chars)",
        examples=["a1b2c3d4e5f6"],
    )

    # ── Custom validators ────────────────────────────────────────────

    @field_validator("user_id")
    @classmethod
    def validate_user_id(cls, v: str) -> str:
        if not re.match(r"^[a-zA-Z0-9_-]+$", v):
            raise ValueError(
                "user_id must contain only letters, digits, underscores, or hyphens"
            )
        return v.strip()

    @field_validator("type")
    @classmethod
    def validate_type(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in ("credit", "debit"):
            raise ValueError("type must be either 'credit' or 'debit'")
        return v

    @field_validator("amount")
    @classmethod
    def validate_amount_precision(cls, v: float) -> float:
        """Round to 2 decimal places to avoid floating-point drift."""
        return round(v, 2)

    @field_validator("description")
    @classmethod
    def sanitise_description(cls, v: str) -> str:
        """Strip leading/trailing whitespace and collapse inner spaces."""
        return " ".join(v.split())

    @field_validator("idempotency_key")
    @classmethod
    def validate_idempotency_key(cls, v: str) -> str:
        if not re.match(r"^[a-zA-Z0-9_-]+$", v):
            raise ValueError(
                "idempotency_key must contain only letters, digits, underscores, or hyphens"
            )
        return v.strip()


# ──────────────────────────────────────────────────────────────────────
# POST /transaction  –  Response body
# ──────────────────────────────────────────────────────────────────────
class TransactionResponse(BaseModel):
    """Returned after successfully creating a transaction."""

    id: str
    user_id: str
    amount: float
    type: str
    description: str
    idempotency_key: str
    created_at: str
    message: str = "Transaction created successfully"


# ──────────────────────────────────────────────────────────────────────
# GET /summary/:userId  –  Response body
# ──────────────────────────────────────────────────────────────────────
class UserSummary(BaseModel):
    """Aggregated financial summary for a single user."""

    user_id: str
    total_credits: float
    total_debits: float
    net_balance: float
    transaction_count: int
    transactions: list[dict]


# ──────────────────────────────────────────────────────────────────────
# GET /ranking  –  Single entry in the response list
# ──────────────────────────────────────────────────────────────────────
class RankingEntry(BaseModel):
    """
    One user's position in the leaderboard.

    The composite score is calculated from three weighted factors:
      score = w_volume × norm(tx_count)
            + w_net    × norm(net_balance)
            + w_variety× norm(distinct_types / 2)

    See config.py for the default weights.
    """

    rank: int
    user_id: str
    score: float = Field(description="Composite ranking score (0–1 scale)")
    transaction_count: int
    net_balance: float
    total_credits: float
    total_debits: float


# ──────────────────────────────────────────────────────────────────────
# Generic error response
# ──────────────────────────────────────────────────────────────────────
class ErrorResponse(BaseModel):
    """Standard error envelope."""

    detail: str
