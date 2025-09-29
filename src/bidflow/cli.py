from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .engine import BidFlow, BidFlowError
from .models import Event, OrderState
from .report import write_report


def _demo() -> BidFlow:
    base = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    engine = BidFlow()
    engine.create_auction(
        "auction-demo",
        "seller-1",
        "Synthetic observability kit",
        base,
        base + timedelta(minutes=10),
        "20.00",
        "30.00",
        "1.00",
        command_key="create-demo",
    )
    engine.publish_auction("auction-demo", base, command_key="publish-demo")
    engine.place_bid("auction-demo", "buyer-a", "22.00", base + timedelta(minutes=1), command_key="bid-a")
    engine.place_bid("auction-demo", "buyer-b", "30.00", base + timedelta(minutes=2), command_key="bid-b")
    engine.place_bid("auction-demo", "buyer-a", "32.00", base + timedelta(minutes=3), command_key="bid-a2")
    order = engine.close_auction("auction-demo", base + timedelta(minutes=10), command_key="close-demo")
    if order is not None:
        engine.transition_order(
            order.order_id,
            OrderState.PAYMENT_PENDING,
            base + timedelta(minutes=11),
            command_key="pay-pending",
        )
        engine.transition_order(order.order_id, OrderState.PAID, base + timedelta(minutes=12), command_key="paid")
    return engine


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bidflow",
        description="Run an event-sourced auction/order state-machine demo.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    demo = subparsers.add_parser("demo", help="run the synthetic demo")
    demo.add_argument("--output-dir", type=Path, default=Path("outputs/demo"))
    replay = subparsers.add_parser("replay", help="replay an exported event list")
    replay.add_argument("events", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "demo":
        engine = _demo()
        artifacts = write_report(engine, args.output_dir)
        print(
            json.dumps(
                {
                    "status": "ok",
                    "events": len(engine.events),
                    "replay_matches": engine.replay_matches(),
                    "artifacts": artifacts,
                },
                indent=2,
            )
        )
        return 0
    try:
        payload = json.loads(args.events.read_text(encoding="utf-8"))
        raw_events = payload.get("events", payload)
        events = tuple(Event.from_dict(row) for row in raw_events)
        engine = BidFlow.replay(events)
    except (OSError, json.JSONDecodeError, TypeError, ValueError, BidFlowError) as exc:
        print(json.dumps({"status": "error", "message": str(exc)}))
        return 2
    print(
        json.dumps(
            {"status": "ok", "events": len(engine.events), "replay_matches": engine.replay_matches()},
            indent=2,
        )
    )
    return 0
