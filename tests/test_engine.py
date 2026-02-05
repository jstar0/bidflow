from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from bidflow import (
    AuctionState,
    BidFlow,
    BidFlowError,
    Event,
    IdempotencyConflictError,
    InvalidTransitionError,
    OrderState,
)

BASE = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def _live_engine(*, reserve: str = "30.00") -> BidFlow:
    engine = BidFlow()
    engine.create_auction(
        "a1",
        "seller",
        "Synthetic item",
        BASE,
        BASE + timedelta(minutes=10),
        "10.00",
        reserve,
        "1.00",
    )
    engine.publish_auction("a1", BASE)
    return engine


def test_time_window_and_bid_rules() -> None:
    engine = _live_engine()
    assert engine.get_auction("a1").state == AuctionState.LIVE
    with pytest.raises(BidFlowError, match="at least"):
        engine.place_bid("a1", "buyer", "9.99", BASE + timedelta(minutes=1))
    first = engine.place_bid("a1", "buyer", "10.00", BASE + timedelta(minutes=1))
    assert first.amount == Decimal("10.00")
    with pytest.raises(BidFlowError, match="at least"):
        engine.place_bid("a1", "other", "10.50", BASE + timedelta(minutes=2))
    with pytest.raises(BidFlowError, match="seller"):
        engine.place_bid("a1", "seller", "20.00", BASE + timedelta(minutes=2))
    with pytest.raises(BidFlowError, match="at least"):
        engine.place_bid("a1", "other", "10.99", BASE + timedelta(minutes=2))
    second = engine.place_bid("a1", "other", "11.00", BASE + timedelta(minutes=2))
    assert second.sequence > first.sequence
    with pytest.raises(BidFlowError, match="not live"):
        engine.place_bid("a1", "late", "20.00", BASE + timedelta(minutes=10))


def test_idempotency_is_atomic_and_conflicts_are_rejected() -> None:
    engine = _live_engine()
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(
            pool.map(
                lambda _: engine.place_bid("a1", "buyer", "12.00", BASE + timedelta(minutes=1), command_key="same-bid"),
                range(8),
            )
        )
    assert {bid.bid_id for bid in results} == {results[0].bid_id}
    assert len(engine.bids_for("a1")) == 1
    with pytest.raises(IdempotencyConflictError):
        engine.place_bid("a1", "buyer", "13.00", BASE + timedelta(minutes=1), command_key="same-bid")


def test_settlement_creates_order_only_when_reserve_is_met() -> None:
    engine = _live_engine(reserve="15.00")
    engine.place_bid("a1", "buyer", "16.00", BASE + timedelta(minutes=1))
    order = engine.close_auction("a1", BASE + timedelta(minutes=10))
    assert order is not None
    assert order.total == Decimal("16.00")
    assert engine.get_auction("a1").state == AuctionState.CLOSED
    pending = engine.transition_order(order.order_id, OrderState.PAYMENT_PENDING, BASE + timedelta(minutes=11))
    assert pending.state == OrderState.PAYMENT_PENDING
    paid = engine.transition_order(order.order_id, OrderState.PAID, BASE + timedelta(minutes=12))
    assert paid.state == OrderState.PAID
    with pytest.raises(InvalidTransitionError):
        engine.transition_order(order.order_id, OrderState.COMPLETED, BASE + timedelta(minutes=13))
    assert engine.replay_matches()

    no_sale = _live_engine(reserve="50.00")
    no_sale.place_bid("a1", "buyer", "10.00", BASE + timedelta(minutes=1))
    assert no_sale.close_auction("a1", BASE + timedelta(minutes=10)) is None
    assert no_sale.snapshot()["orders"] == []


def test_replay_reconstructs_the_same_projection() -> None:
    engine = _live_engine()
    engine.place_bid("a1", "buyer-a", "10.00", BASE + timedelta(minutes=1), command_key="bid-a")
    engine.place_bid("a1", "buyer-b", "11.00", BASE + timedelta(minutes=2), command_key="bid-b")
    replayed = BidFlow.replay(engine.events)
    assert replayed.snapshot() == engine.snapshot()
    assert [event.sequence for event in replayed.events] == list(range(1, len(engine.events) + 1))
    repeated = replayed.place_bid("a1", "buyer-b", "11.00", BASE + timedelta(minutes=2), command_key="bid-b")
    assert repeated == replayed.bids_for("a1")[-1]
    new_bid = replayed.place_bid("a1", "buyer-a", "12.00", BASE + timedelta(minutes=3), command_key="new-bid")
    assert new_bid.amount == Decimal("12.00")


def test_replay_rejects_duplicate_event_ids() -> None:
    events = list(_live_engine().events)
    events[1] = replace(events[1], event_id=events[0].event_id)

    with pytest.raises(ValueError, match="event ids must be unique"):
        BidFlow.replay(events)


def test_replay_rejects_aggregate_mismatch_and_orphan_bid() -> None:
    created = _live_engine().events[0]
    mismatched = replace(created, aggregate_id="different-auction")
    orphan_bid = Event(
        sequence=1,
        event_id="evt-orphan-bid",
        kind="bid.accepted",
        aggregate_id="missing-auction",
        occurred_at=BASE,
        payload={"auction_id": "missing-auction", "bidder_id": "buyer", "amount": "10.00"},
    )

    with pytest.raises(ValueError, match="aggregate"):
        BidFlow.replay([mismatched])
    with pytest.raises(ValueError, match="auction not found"):
        BidFlow.replay([orphan_bid])


def test_replay_rejects_forged_state_and_settlement_transitions() -> None:
    created = _live_engine().events[0]
    forged_close = Event(
        sequence=2,
        event_id="evt-forged-close",
        kind="auction.state_changed",
        aggregate_id="a1",
        occurred_at=BASE,
        payload={"from": "draft", "to": "closed", "reason": "published"},
    )
    forged_settlement = Event(
        sequence=2,
        event_id="evt-forged-settlement",
        kind="auction.settled",
        aggregate_id="a1",
        occurred_at=BASE,
        payload={
            "winner_bid_id": "evt-imaginary",
            "winner_id": "buyer",
            "winning_amount": "99.00",
            "reserve_met": True,
        },
    )

    with pytest.raises(ValueError, match="invalid auction state transition"):
        BidFlow.replay([created, forged_close])
    with pytest.raises(ValueError, match="closed auction"):
        BidFlow.replay([created, forged_settlement])
