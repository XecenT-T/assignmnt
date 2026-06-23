"""
Application entry point for the Transaction Ranking Service.

Wires together:
  • FastAPI app with CORS middleware
  • SlowAPI rate-limit error handler
  • Database lifecycle (connect on startup, disconnect on shutdown)
  • API router from routes.py
  • Static file serving for the frontend
"""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from backend.config import ALLOWED_ORIGINS
from backend.database import init_db, close_db
from backend.routes import router, limiter


# ── Lifecycle ────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start DB on boot, tear down on shutdown."""
    await init_db()
    yield
    await close_db()


# ── App Factory ──────────────────────────────────────────────────────

app = FastAPI(
    title="Transaction Ranking Service",
    description=(
        "A backend service demonstrating API design, data consistency, "
        "validation, and fair multi-factor ranking logic.  "
        "Built with FastAPI + PostgreSQL."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

# ── Middleware ───────────────────────────────────────────────────────

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Rate Limit Handler ──────────────────────────────────────────────

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# ── Global Error Handler ────────────────────────────────────────────

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Catch-all so unhandled errors return a clean JSON envelope."""
    return JSONResponse(
        status_code=500,
        content={"detail": "An unexpected error occurred. Please try again later."},
    )

# ── API Routes ───────────────────────────────────────────────────────

app.include_router(router, prefix="/api", tags=["Transactions & Ranking"])

# ── Health Check ─────────────────────────────────────────────────────

@app.get("/health", tags=["Health"])
async def health_check():
    """Simple liveness probe."""
    return {"status": "healthy", "service": "Transaction Ranking Service"}

# ── Frontend Static Files ───────────────────────────────────────────

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

if FRONTEND_DIR.exists():
    @app.get("/", include_in_schema=False)
    async def serve_frontend():
        return FileResponse(FRONTEND_DIR / "index.html")

    app.mount(
        "/static",
        StaticFiles(directory=str(FRONTEND_DIR)),
        name="frontend",
    )
