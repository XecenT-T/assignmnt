"""
Ranking Service – computes a composite leaderboard from transaction data.

Algorithm
---------
Each user receives a **composite score** between 0 and 1, calculated as
a weighted sum of three individually normalised (min-max) factors:

    score = W_vol  × norm(transaction_count)
          + W_net  × norm(net_balance)
          + W_var  × norm(type_variety_ratio)

Where:
    • transaction_count  – total number of transactions (rewards activity)
    • net_balance        – credits minus debits (rewards financial health)
    • type_variety_ratio – distinct types used / 2  (rewards diversity;
                           max is 1.0 when both credit and debit are used)

Min-max normalisation maps each factor to [0, 1] across all users so no
single dimension dominates unfairly.

Anti-Manipulation
-----------------
• Scores are computed **server-side** from raw transaction data – users
  cannot submit a score directly.
• Rate limiting (see routes.py) caps the number of transactions a user
  can create per minute, preventing score inflation via spam.
• The variety factor means that spamming only credits (or only debits)
  yields a lower score than genuinely diverse activity.

Default weights (configurable in config.py):
    VOLUME  = 0.35
    NET     = 0.45
    VARIETY = 0.20
"""

from backend.config import (
    RANKING_WEIGHT_VOLUME,
    RANKING_WEIGHT_NET,
    RANKING_WEIGHT_VARIETY,
)
from backend.database import fetch_all_user_aggregates


def _min_max_normalise(values: list[float]) -> list[float]:
    """
    Normalise a list of floats to [0, 1] using min-max scaling.

    If all values are identical the result is a list of 0.5 (tied).
    """
    if not values:
        return []
    lo, hi = min(values), max(values)
    if hi == lo:
        return [0.5] * len(values)
    return [(v - lo) / (hi - lo) for v in values]


async def compute_ranking() -> list[dict]:
    """
    Fetch per-user aggregates from the database, compute composite
    scores, and return a sorted leaderboard (rank 1 = best).

    Returns a list of dicts, each containing:
        rank, user_id, score, transaction_count, net_balance,
        total_credits, total_debits
    """
    aggregates = await fetch_all_user_aggregates()

    if not aggregates:
        return []

    # ── Extract raw factor vectors ───────────────────────────────────
    tx_counts = [a["tx_count"] for a in aggregates]
    net_balances = [a["net_balance"] for a in aggregates]
    # variety ratio: distinct_types / 2 (max possible types)
    variety_ratios = [a["distinct_types"] / 2.0 for a in aggregates]

    # ── Normalise each factor to [0, 1] ─────────────────────────────
    norm_vol = _min_max_normalise(tx_counts)
    norm_net = _min_max_normalise(net_balances)
    norm_var = _min_max_normalise(variety_ratios)

    # ── Compute weighted composite score ─────────────────────────────
    scored = []
    for i, agg in enumerate(aggregates):
        score = (
            RANKING_WEIGHT_VOLUME * norm_vol[i]
            + RANKING_WEIGHT_NET * norm_net[i]
            + RANKING_WEIGHT_VARIETY * norm_var[i]
        )
        scored.append(
            {
                "user_id": agg["user_id"],
                "score": round(score, 4),
                "transaction_count": agg["tx_count"],
                "net_balance": round(agg["net_balance"], 2),
                "total_credits": round(agg["total_credits"], 2),
                "total_debits": round(agg["total_debits"], 2),
            }
        )

    # ── Sort descending by score, then alphabetically for ties ───────
    scored.sort(key=lambda x: (-x["score"], x["user_id"]))

    # ── Assign ranks ─────────────────────────────────────────────────
    for rank, entry in enumerate(scored, start=1):
        entry["rank"] = rank

    return scored
