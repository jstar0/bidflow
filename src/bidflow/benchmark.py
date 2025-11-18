from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from .engine import BidFlow
from .models import OrderState

BASE = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def _scenario(auction_id: str, reserve: str, winning_bid: str) -> dict[str, Any]:
    engine = BidFlow()
    engine.create_auction(
        auction_id,
        "seller-1",
        "Synthetic observability kit",
        BASE,
        BASE + timedelta(minutes=10),
        "20.00",
        reserve,
        "1.00",
        command_key=f"create-{auction_id}",
    )
    engine.publish_auction(auction_id, BASE, command_key=f"publish-{auction_id}")
    first = engine.place_bid(
        auction_id, "buyer-a", "22.00", BASE + timedelta(minutes=1), command_key=f"bid-{auction_id}-a"
    )
    repeated = engine.place_bid(
        auction_id, "buyer-a", "22.00", BASE + timedelta(minutes=1), command_key=f"bid-{auction_id}-a"
    )
    engine.place_bid(
        auction_id, "buyer-b", winning_bid, BASE + timedelta(minutes=2), command_key=f"bid-{auction_id}-b"
    )
    order = engine.close_auction(auction_id, BASE + timedelta(minutes=10), command_key=f"close-{auction_id}")
    if order is not None:
        engine.transition_order(
            order.order_id,
            OrderState.PAYMENT_PENDING,
            BASE + timedelta(minutes=11),
            command_key=f"pending-{auction_id}",
        )
        engine.transition_order(
            order.order_id, OrderState.PAID, BASE + timedelta(minutes=12), command_key=f"paid-{auction_id}"
        )
    snapshot = engine.snapshot()
    return {
        "scenario": auction_id,
        "reserve_price": reserve,
        "winning_bid": winning_bid,
        "accepted_bid_count": len(engine.bids_for(auction_id)),
        "idempotent_bid_reused": first.bid_id == repeated.bid_id,
        "reserve_met": bool(snapshot["settlements"][0]["reserve_met"]),
        "order_count": len(snapshot["orders"]),
        "order_state": snapshot["orders"][0]["state"] if snapshot["orders"] else None,
        "event_count": snapshot["event_count"],
        "replay_matches": engine.replay_matches(),
    }


def run_benchmark() -> dict[str, Any]:
    scenarios = [
        _scenario("reserve-cleared", "30.00", "32.00"),
        _scenario("reserve-missed", "40.00", "32.00"),
    ]
    return {
        "benchmark_version": 1,
        "scenario_count": len(scenarios),
        "scenarios": scenarios,
        "all_replays_match": all(row["replay_matches"] for row in scenarios),
        "idempotency_reuse_count": sum(row["idempotent_bid_reused"] for row in scenarios),
        "reserve_clearing_count": sum(row["reserve_met"] for row in scenarios),
        "total_events": sum(row["event_count"] for row in scenarios),
    }
