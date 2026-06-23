# Transaction Ranking Service

A backend service and live frontend demonstrating API design, data consistency, request validation, and fair multi-factor ranking logic. Built with **FastAPI**, **PostgreSQL**, and **Docker**.

---

## Table of Contents

- [Quick Start](#quick-start)
- [Architecture](#architecture)
- [API Reference](#api-reference)
  - [POST /api/transaction](#post-apitransaction)
  - [GET /api/summary/{user_id}](#get-apisummaryuser_id)
  - [GET /api/ranking](#get-apiranking)
- [How Ranking Is Calculated](#how-ranking-is-calculated)
- [How Duplicate Requests Are Prevented](#how-duplicate-requests-are-prevented)
- [Concurrency & Fairness](#concurrency--fairness)
- [Abuse Prevention](#abuse-prevention)
- [Database Schema & Data Flow](#database-schema--data-flow)
- [Mock / Demo Data](#mock--demo-data)
- [Project Structure](#project-structure)
- [Trade-offs & Limitations](#trade-offs--limitations)

---

## Quick Start

### Prerequisites

- Docker & Docker Compose installed

### Run with Docker (recommended)

```bash
# Clone the repo
git clone <repo-url> && cd assignmnt

# Start PostgreSQL + Backend in one command
docker compose up --build

# The app is now live at:
#   Frontend:  http://localhost:8000
#   API Docs:  http://localhost:8000/docs
#   Health:    http://localhost:8000/health
```

### Run without Docker (local development)

```bash
# 1. Start a PostgreSQL instance (e.g. via Docker)
docker run -d --name pg_txn -e POSTGRES_USER=txnuser -e POSTGRES_PASSWORD=txnpass -e POSTGRES_DB=txn_ranker -p 5432:5432 postgres:16-alpine

# 2. Install Python dependencies
cd backend
pip install -r requirements.txt

# 3. Set env vars (or create a .env file from .env.example)
export DATABASE_URL="postgresql+asyncpg://txnuser:txnpass@localhost:5432/txn_ranker"

# 4. Run the server
cd ..
uvicorn backend.main:app --reload --port 8000
```

---

## Architecture

```
┌──────────────┐       ┌──────────────────┐       ┌──────────────┐
│   Frontend   │──────▶│  FastAPI Backend  │──────▶│  PostgreSQL  │
│  (HTML/JS)   │  HTTP │  (Python 3.12)   │ async │  (Docker)    │
└──────────────┘       └──────────────────┘       └──────────────┘
                            │
                       Rate Limiter (SlowAPI)
                       Pydantic Validation
                       Idempotency Guard
```

---

## API Reference

### POST /api/transaction

> **Create a new financial transaction (credit or debit).**

**Request Body (JSON):**

| Field             | Type   | Required | Constraints                        |
|-------------------|--------|----------|------------------------------------|
| `user_id`         | string | ✅       | 3–50 chars, alphanumeric / `_` / `-` |
| `amount`          | float  | ✅       | `> 0`, `≤ 1,000,000`              |
| `type`            | string | ✅       | `"credit"` or `"debit"`            |
| `description`     | string | ❌       | Max 200 chars                      |
| `idempotency_key` | string | ✅       | 8–128 chars, alphanumeric / `_` / `-` |

**Example Request:**

```bash
curl -X POST http://localhost:8000/api/transaction \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "user_alice",
    "amount": 500.00,
    "type": "credit",
    "description": "Freelance payment",
    "idempotency_key": "abc123def456"
  }'
```

**Success Response (201):**

```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "user_id": "user_alice",
  "amount": 500.0,
  "type": "credit",
  "description": "Freelance payment",
  "idempotency_key": "abc123def456",
  "created_at": "2026-06-23T04:30:00+00:00",
  "message": "Transaction created successfully"
}
```

**Error Responses:**

| Code | Condition                 |
|------|---------------------------|
| 409  | Duplicate idempotency key |
| 422  | Validation error          |
| 429  | Rate limit exceeded       |

---

### GET /api/summary/{user_id}

> **Retrieve a user's aggregated financial summary and full transaction history.**

**Path Parameter:** `user_id` – the user whose summary is requested.

**Example Request:**

```bash
curl http://localhost:8000/api/summary/user_alice
```

**Success Response (200):**

```json
{
  "user_id": "user_alice",
  "total_credits": 5250.0,
  "total_debits": 1275.5,
  "net_balance": 3974.5,
  "transaction_count": 4,
  "transactions": [
    {
      "id": "...",
      "user_id": "user_alice",
      "amount": 75.0,
      "type": "debit",
      "description": "Groceries",
      "idempotency_key": "...",
      "created_at": "2026-06-23T04:20:00+00:00"
    }
  ]
}
```

**Error Responses:**

| Code | Condition                        |
|------|----------------------------------|
| 404  | No transactions found for user   |

---

### GET /api/ranking

> **Fetch the global leaderboard sorted by composite score (descending).**

**Example Request:**

```bash
curl http://localhost:8000/api/ranking
```

**Success Response (200):**

```json
[
  {
    "rank": 1,
    "user_id": "user_charlie",
    "score": 0.9125,
    "transaction_count": 4,
    "net_balance": 5900.0,
    "total_credits": 9000.0,
    "total_debits": 3100.0
  }
]
```

---

## How Ranking Is Calculated

The ranking uses a **weighted composite score** based on three normalised factors:

```
score = 0.35 × norm(transaction_count)
      + 0.45 × norm(net_balance)
      + 0.20 × norm(type_variety_ratio)
```

| Factor               | Weight | What It Rewards                             |
|----------------------|--------|---------------------------------------------|
| `transaction_count`  | 0.35   | Activity – users with more transactions     |
| `net_balance`        | 0.45   | Financial health – credits minus debits     |
| `type_variety_ratio` | 0.20   | Diversity – using both credit AND debit     |

**Normalisation:** Each factor is **min-max normalised** across all users to the `[0, 1]` range. This ensures no single dimension dominates unfairly. If all users have the same value for a factor, they each receive `0.5`.

**Why three factors?** A single-factor ranking (e.g. just net balance) would be trivially gameable. The multi-factor approach means:
- Spamming only credits inflates net balance but gets penalised on variety.
- Creating many tiny transactions boosts volume but not net balance.
- The best score requires genuine, diverse financial activity.

---

## How Duplicate Requests Are Prevented

Duplicate prevention uses a **two-layer guard**:

1. **Application layer** – Before INSERT, we query the database for the `idempotency_key`. If it already exists, we immediately return **409 Conflict** with the original transaction details.

2. **Database layer** – The `idempotency_key` column has a **UNIQUE constraint** in PostgreSQL. Even if two identical requests bypass the application check simultaneously, only one INSERT will succeed; the other will raise `IntegrityError`, which is caught and returned as **409**.

This ensures **exactly-once processing** regardless of network retries or concurrent submissions.

---

## Concurrency & Fairness

| Concern              | Solution                                                                 |
|----------------------|--------------------------------------------------------------------------|
| Simultaneous writes  | PostgreSQL MVCC + UNIQUE constraint — only one writer wins               |
| Read consistency     | Aggregates computed via SQL `GROUP BY` — always reflect latest state      |
| Race on idempotency  | DB-level UNIQUE index is the final arbiter, not in-memory state          |
| Fair ranking         | Server-computed scores — users cannot submit or manipulate scores         |
| Equal normalisation  | Min-max scaling across all users ensures relative fairness               |

---

## Abuse Prevention

| Threat                        | Mitigation                                                       |
|-------------------------------|------------------------------------------------------------------|
| Transaction spam              | Rate limiting: max 10 requests/minute per IP (configurable)      |
| Inflated amounts              | Amount capped at 1,000,000 per transaction                      |
| Score manipulation            | Scores are server-computed from raw data; no client input        |
| One-dimensional gaming        | Variety factor penalises using only credits or only debits       |
| Injection / XSS               | Pydantic validation + regex constraints on all string fields     |
| Replay attacks                | Idempotency key required; duplicates rejected with 409           |

---

## Database Schema & Data Flow

### Schema

```sql
CREATE TABLE transactions (
    id              UUID PRIMARY KEY,
    user_id         VARCHAR(50)    NOT NULL,
    amount          NUMERIC(12,2)  NOT NULL CHECK (amount > 0),
    type            VARCHAR(6)     NOT NULL CHECK (type IN ('credit', 'debit')),
    description     VARCHAR(200)   DEFAULT '',
    idempotency_key VARCHAR(128)   UNIQUE NOT NULL,
    created_at      TIMESTAMPTZ    NOT NULL
);

-- Indexes
CREATE UNIQUE INDEX ON transactions (idempotency_key);
CREATE INDEX ON transactions (user_id);
```

### Data Flow

```
Client Request
      │
      ▼
┌─────────────┐    Pydantic    ┌──────────────┐    Rate Limit    ┌──────────┐
│  HTTP POST  │──▶ Validation ──▶ Idempotency ──▶   Check      ──▶│ INSERT  │
│  /api/txn   │                │   Check      │                   │ (PG)    │
└─────────────┘                └──────────────┘                   └──────────┘
                                     │ exists?                        │
                                     ▼                                ▼
                               409 Conflict                    201 Created
```

---

## Mock / Demo Data

On first startup (when the `transactions` table is empty), the system seeds **17 demo transactions** across 5 users:

| User           | Credits           | Debits                    |
|----------------|-------------------|---------------------------|
| `user_alice`   | Salary, Freelance | Rent, Groceries           |
| `user_bob`     | Salary, Cashback  | Utilities                 |
| `user_charlie` | Consulting, Bonus | Equipment, Software       |
| `user_diana`   | Part-time income  | Transport                 |
| `user_eve`     | Salary, Dividend  | Insurance, Subscriptions  |

**Assumptions:**
- User IDs follow the pattern `user_<name>` (alphanumeric with underscores).
- Amounts are in USD and represent realistic everyday transactions.
- Seed idempotency keys are random UUIDs (one-time use during seeding).

---

## Project Structure

```
assignmnt/
├── backend/
│   ├── __init__.py          # Package marker
│   ├── config.py            # Centralised configuration (env vars)
│   ├── database.py          # PostgreSQL ORM, schema, CRUD, seed data
│   ├── schemas.py           # Pydantic request/response models
│   ├── ranking.py           # Multi-factor ranking algorithm
│   ├── routes.py            # API endpoint definitions
│   ├── main.py              # FastAPI app factory & middleware
│   └── requirements.txt     # Python dependencies
├── frontend/
│   ├── index.html           # Dashboard UI
│   ├── style.css            # Premium dark-theme styles
│   └── app.js               # Frontend API integration logic
├── docker-compose.yml       # PostgreSQL + Backend services
├── Dockerfile               # Python 3.12 slim image
├── .env.example             # Example environment variables
├── .gitignore
└── README.md
```

---

## Trade-offs & Limitations

| Area                    | Trade-off / Limitation                                                                                              |
|-------------------------|---------------------------------------------------------------------------------------------------------------------|
| **Database**            | Single PostgreSQL instance — no replication or sharding. Sufficient for demo; production would use read replicas.     |
| **Rate limiting**       | Per-IP only — a determined attacker could rotate IPs. Production would add per-user-ID limits + CAPTCHA.             |
| **Idempotency TTL**     | Keys are stored permanently. A production system would expire them (e.g. after 24h) to reclaim storage.              |
| **Ranking freshness**   | Computed on every GET /ranking call. At scale, this would be cached (e.g. Redis) with periodic recomputation.        |
| **Authentication**      | No auth — any client can submit transactions for any user_id. Production would add JWT / OAuth.                      |
| **Amount validation**   | Debits are allowed even if they exceed the user's balance (no balance check). This is a design simplification.       |
| **Horizontal scaling**  | Single backend instance. To scale, add a load balancer; the DB-level idempotency guard remains correct across nodes. |