from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any


def as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    return value.astimezone(UTC)


def parse_money(value: Decimal | int | str) -> Decimal:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"invalid monetary amount: {value!r}") from exc
    if not amount.is_finite() or amount < 0:
        raise ValueError("monetary amount must be finite and non-negative")
    quantized = amount.quantize(Decimal("0.01"))
    if amount != quantized:
        raise ValueError("monetary amount must use at most two decimal places")
    return quantized


class AuctionState(StrEnum):
    DRAFT = "draft"
    SCHEDULED = "scheduled"
    LIVE = "live"
    CLOSED = "closed"
    CANCELLED = "cancelled"


class OrderState(StrEnum):
    CREATED = "created"
    PAYMENT_PENDING = "payment_pending"
    PAID = "paid"
    FULFILLING = "fulfilling"
    SHIPPED = "shipped"
    COMPLETED = "completed"
    REFUNDED = "refunded"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class Auction:
    auction_id: str
    seller_id: str
    title: str
    starts_at: datetime
    ends_at: datetime
    starting_price: Decimal
    reserve_price: Decimal
    min_increment: Decimal
    state: AuctionState = AuctionState.DRAFT

    def __post_init__(self) -> None:
        if not self.auction_id.strip() or not self.seller_id.strip() or not self.title.strip():
            raise ValueError("auction identifiers and title must be non-empty")
        start = as_utc(self.starts_at)
        end = as_utc(self.ends_at)
        if end <= start:
            raise ValueError("ends_at must be after starts_at")
        starting_price = parse_money(self.starting_price)
        reserve_price = parse_money(self.reserve_price)
        min_increment = parse_money(self.min_increment)
        if reserve_price < starting_price or min_increment <= 0:
            raise ValueError("auction prices are inconsistent")
        object.__setattr__(self, "starts_at", start)
        object.__setattr__(self, "ends_at", end)
        object.__setattr__(self, "starting_price", starting_price)
        object.__setattr__(self, "reserve_price", reserve_price)
        object.__setattr__(self, "min_increment", min_increment)

    def as_dict(self) -> dict[str, Any]:
        return {
            "auction_id": self.auction_id,
            "seller_id": self.seller_id,
            "title": self.title,
            "starts_at": self.starts_at.isoformat(),
            "ends_at": self.ends_at.isoformat(),
            "starting_price": str(self.starting_price),
            "reserve_price": str(self.reserve_price),
            "min_increment": str(self.min_increment),
            "state": self.state.value,
        }


@dataclass(frozen=True, slots=True)
class Bid:
    bid_id: str
    auction_id: str
    bidder_id: str
    amount: Decimal
    accepted_at: datetime
    sequence: int

    def __post_init__(self) -> None:
        if not self.bid_id.strip() or not self.auction_id.strip() or not self.bidder_id.strip():
            raise ValueError("bid identifiers must be non-empty")
        object.__setattr__(self, "amount", parse_money(self.amount))
        object.__setattr__(self, "accepted_at", as_utc(self.accepted_at))
        if self.sequence < 1:
            raise ValueError("bid sequence must be positive")

    def as_dict(self) -> dict[str, Any]:
        return {
            "bid_id": self.bid_id,
            "auction_id": self.auction_id,
            "bidder_id": self.bidder_id,
            "amount": str(self.amount),
            "accepted_at": self.accepted_at.isoformat(),
            "sequence": self.sequence,
        }


@dataclass(frozen=True, slots=True)
class Order:
    order_id: str
    auction_id: str
    buyer_id: str
    seller_id: str
    total: Decimal
    state: OrderState = OrderState.CREATED

    def __post_init__(self) -> None:
        if not self.order_id.strip() or not self.auction_id.strip() or not self.buyer_id.strip():
            raise ValueError("order identifiers must be non-empty")
        object.__setattr__(self, "total", parse_money(self.total))

    def as_dict(self) -> dict[str, Any]:
        return {
            "order_id": self.order_id,
            "auction_id": self.auction_id,
            "buyer_id": self.buyer_id,
            "seller_id": self.seller_id,
            "total": str(self.total),
            "state": self.state.value,
        }


@dataclass(frozen=True, slots=True)
class Event:
    sequence: int
    event_id: str
    kind: str
    aggregate_id: str
    occurred_at: datetime
    payload: dict[str, Any]
    idempotency_key: str | None = None

    def __post_init__(self) -> None:
        if self.sequence < 1 or not self.event_id.strip() or not self.kind.strip() or not self.aggregate_id.strip():
            raise ValueError("event identity is invalid")
        object.__setattr__(self, "occurred_at", as_utc(self.occurred_at))
        object.__setattr__(self, "payload", dict(self.payload))

    def as_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "event_id": self.event_id,
            "kind": self.kind,
            "aggregate_id": self.aggregate_id,
            "occurred_at": self.occurred_at.isoformat(),
            "payload": self.payload,
            "idempotency_key": self.idempotency_key,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> Event:
        return cls(
            sequence=int(payload["sequence"]),
            event_id=str(payload["event_id"]),
            kind=str(payload["kind"]),
            aggregate_id=str(payload["aggregate_id"]),
            occurred_at=datetime.fromisoformat(str(payload["occurred_at"])),
            payload=dict(payload.get("payload", {})),
            idempotency_key=payload.get("idempotency_key"),
        )
