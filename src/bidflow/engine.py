from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from threading import RLock
from typing import Any

from .models import Auction, AuctionState, Bid, Event, Order, OrderState, as_utc, parse_money


class BidFlowError(ValueError):
    """Base class for expected command failures."""


class NotFoundError(BidFlowError):
    pass


class InvalidTransitionError(BidFlowError):
    pass


class IdempotencyConflictError(BidFlowError):
    pass


class BidFlow:
    """Thread-safe event-sourced auction engine for deterministic local demos."""

    def __init__(self, events: tuple[Event, ...] = ()) -> None:
        self._lock = RLock()
        self._events: list[Event] = []
        self._auctions: dict[str, Auction] = {}
        self._bids: dict[str, list[Bid]] = {}
        self._orders: dict[str, Order] = {}
        self._settlements: dict[str, dict[str, Any]] = {}
        self._commands: dict[str, tuple[str, Any]] = {}
        seen_event_ids: set[str] = set()
        for event in events:
            if event.sequence != len(self._events) + 1:
                raise ValueError("event sequences must be contiguous and start at one")
            if event.event_id in seen_event_ids:
                raise ValueError("event ids must be unique")
            seen_event_ids.add(event.event_id)
            self._events.append(event)
            self._apply(event)
        self._restore_idempotency()

    @classmethod
    def replay(cls, events: tuple[Event, ...] | list[Event]) -> BidFlow:
        return cls(tuple(events))

    @property
    def events(self) -> tuple[Event, ...]:
        with self._lock:
            return tuple(self._events)

    @staticmethod
    def _fingerprint(command: str, payload: dict[str, Any]) -> str:
        canonical = json.dumps({"command": command, "payload": payload}, sort_keys=True, default=str)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _cached(self, key: str | None, command: str, payload: dict[str, Any]) -> Any | None:
        if key is None:
            return None
        fingerprint = self._fingerprint(command, payload)
        existing = self._commands.get(key)
        if existing is None:
            return None
        if existing[0] != fingerprint:
            raise IdempotencyConflictError(f"idempotency key already used for a different {command}")
        return existing[1]

    def _remember(self, key: str | None, command: str, payload: dict[str, Any], result: Any) -> None:
        if key is not None:
            self._commands[key] = (self._fingerprint(command, payload), result)

    def _restore_idempotency(self) -> None:
        """Rebuild command results when an event stream is loaded from disk."""

        for event in self._events:
            key = event.idempotency_key
            if key is None:
                continue
            command: str | None = None
            payload: dict[str, Any] | None = None
            result: Any = None
            if event.kind == "auction.created":
                command, payload, result = "create_auction", event.payload, self._auctions.get(event.aggregate_id)
            elif event.kind == "bid.accepted":
                command, payload, result = "place_bid", event.payload, next(
                    (bid for bid in self._bids.get(event.aggregate_id, ()) if bid.bid_id == event.event_id), None
                )
            elif event.kind == "auction.state_changed" and event.payload.get("reason") == "published":
                command, payload = "publish_auction", {"auction_id": event.aggregate_id}
                result = self._auctions.get(event.aggregate_id)
            elif event.kind == "auction.state_changed" and event.payload.get("reason") == "cancelled":
                command, payload = "cancel_auction", {"auction_id": event.aggregate_id}
                result = self._auctions.get(event.aggregate_id)
            elif event.kind == "order.transitioned":
                command, payload, result = "transition_order", {
                    "order_id": event.aggregate_id,
                    "to": event.payload.get("to"),
                }, self._orders.get(event.aggregate_id)
            elif event.kind == "auction.settled":
                command, payload = "close_auction", {"auction_id": event.aggregate_id}
                result = next(
                    (order for order in self._orders.values() if order.auction_id == event.aggregate_id),
                    None,
                )
            if command is not None and payload is not None:
                self._commands[key] = (self._fingerprint(command, payload), result)

    def _append(
        self,
        kind: str,
        aggregate_id: str,
        payload: dict[str, Any],
        occurred_at: datetime,
        idempotency_key: str | None = None,
    ) -> Event:
        event = Event(
            sequence=len(self._events) + 1,
            event_id=f"evt-{len(self._events) + 1:06d}",
            kind=kind,
            aggregate_id=aggregate_id,
            occurred_at=as_utc(occurred_at),
            payload=dict(payload),
            idempotency_key=idempotency_key,
        )
        self._events.append(event)
        self._apply(event)
        return event

    def _require_auction(self, auction_id: str) -> Auction:
        try:
            return self._auctions[auction_id]
        except KeyError as exc:
            raise NotFoundError(f"auction not found: {auction_id}") from exc

    def _require_order(self, order_id: str) -> Order:
        try:
            return self._orders[order_id]
        except KeyError as exc:
            raise NotFoundError(f"order not found: {order_id}") from exc

    def create_auction(
        self,
        auction_id: str,
        seller_id: str,
        title: str,
        starts_at: datetime,
        ends_at: datetime,
        starting_price: Decimal | int | str,
        reserve_price: Decimal | int | str,
        min_increment: Decimal | int | str,
        *,
        command_key: str | None = None,
    ) -> Auction:
        payload = {
            "auction_id": auction_id,
            "seller_id": seller_id,
            "title": title,
            "starts_at": as_utc(starts_at).isoformat(),
            "ends_at": as_utc(ends_at).isoformat(),
            "starting_price": str(parse_money(starting_price)),
            "reserve_price": str(parse_money(reserve_price)),
            "min_increment": str(parse_money(min_increment)),
        }
        with self._lock:
            cached = self._cached(command_key, "create_auction", payload)
            if cached is not None:
                return cached
            if auction_id in self._auctions:
                raise BidFlowError(f"auction already exists: {auction_id}")
            now = as_utc(starts_at)
            self._append("auction.created", auction_id, payload, now, command_key)
            result = self._auctions[auction_id]
            self._remember(command_key, "create_auction", payload, result)
            return result

    def publish_auction(
        self, auction_id: str, now: datetime, *, command_key: str | None = None
    ) -> Auction:
        timestamp = as_utc(now)
        payload = {"auction_id": auction_id}
        with self._lock:
            cached = self._cached(command_key, "publish_auction", payload)
            if cached is not None:
                return cached
            auction = self._require_auction(auction_id)
            if auction.state != AuctionState.DRAFT:
                raise InvalidTransitionError(f"cannot publish auction in state {auction.state}")
            if timestamp >= auction.ends_at:
                raise BidFlowError("cannot publish an auction whose end has passed")
            state = AuctionState.LIVE if timestamp >= auction.starts_at else AuctionState.SCHEDULED
            self._append(
                "auction.state_changed",
                auction_id,
                {"from": auction.state.value, "to": state.value, "reason": "published"},
                timestamp,
                command_key,
            )
            result = self._auctions[auction_id]
            self._remember(command_key, "publish_auction", payload, result)
            return result

    def advance(self, now: datetime) -> tuple[Auction, ...]:
        """Move scheduled/live auctions across time boundaries; never creates orders."""

        timestamp = as_utc(now)
        with self._lock:
            for auction_id in sorted(self._auctions):
                auction = self._auctions[auction_id]
                target: AuctionState | None = None
                if auction.state == AuctionState.SCHEDULED and timestamp >= auction.ends_at:
                    target = AuctionState.CLOSED
                elif auction.state == AuctionState.SCHEDULED and timestamp >= auction.starts_at:
                    target = AuctionState.LIVE
                elif auction.state == AuctionState.LIVE and timestamp >= auction.ends_at:
                    target = AuctionState.CLOSED
                if target is not None:
                    self._append(
                        "auction.state_changed",
                        auction_id,
                        {"from": auction.state.value, "to": target.value, "reason": "clock"},
                        timestamp,
                    )
            return tuple(self._auctions.values())

    def place_bid(
        self,
        auction_id: str,
        bidder_id: str,
        amount: Decimal | int | str,
        now: datetime,
        *,
        command_key: str | None = None,
    ) -> Bid:
        normalized = parse_money(amount)
        payload = {"auction_id": auction_id, "bidder_id": bidder_id, "amount": str(normalized)}
        timestamp = as_utc(now)
        with self._lock:
            cached = self._cached(command_key, "place_bid", payload)
            if cached is not None:
                return cached
            self.advance(timestamp)
            auction = self._require_auction(auction_id)
            if auction.state != AuctionState.LIVE:
                raise BidFlowError(f"auction is not live: {auction.state}")
            if bidder_id == auction.seller_id:
                raise BidFlowError("seller cannot bid on its own auction")
            bids = self._bids.setdefault(auction_id, [])
            minimum = auction.starting_price if not bids else bids[-1].amount + auction.min_increment
            if normalized < minimum:
                raise BidFlowError(f"bid must be at least {minimum:.2f}")
            self._append("bid.accepted", auction_id, payload, timestamp, command_key)
            result = self._bids[auction_id][-1]
            self._remember(command_key, "place_bid", payload, result)
            return result

    def close_auction(
        self, auction_id: str, now: datetime, *, command_key: str | None = None
    ) -> Order | None:
        timestamp = as_utc(now)
        payload = {"auction_id": auction_id}
        with self._lock:
            cached = self._cached(command_key, "close_auction", payload)
            if cached is not None:
                return cached
            self.advance(timestamp)
            auction = self._require_auction(auction_id)
            if auction.state != AuctionState.CLOSED:
                if timestamp < auction.ends_at:
                    raise BidFlowError("auction cannot close before ends_at")
                raise InvalidTransitionError(f"cannot close auction in state {auction.state}")
            existing = next((order for order in self._orders.values() if order.auction_id == auction_id), None)
            if existing is not None:
                self._remember(command_key, "close_auction", payload, existing)
                return existing
            if auction_id in self._settlements:
                self._remember(command_key, "close_auction", payload, None)
                return None
            bids = self._bids.get(auction_id, [])
            winner = max(bids, key=lambda bid: (bid.amount, -bid.sequence), default=None)
            meets_reserve = winner is not None and winner.amount >= auction.reserve_price
            settlement = {
                "winner_bid_id": winner.bid_id if meets_reserve and winner else None,
                "winner_id": winner.bidder_id if meets_reserve and winner else None,
                "winning_amount": str(winner.amount) if meets_reserve and winner else None,
                "reserve_met": meets_reserve,
            }
            self._append("auction.settled", auction_id, settlement, timestamp, command_key)
            result: Order | None = None
            if meets_reserve and winner is not None:
                order_id = f"order-{auction_id}"
                self._append(
                    "order.created",
                    order_id,
                    {
                        "order_id": order_id,
                        "auction_id": auction_id,
                        "buyer_id": winner.bidder_id,
                        "seller_id": auction.seller_id,
                        "total": str(winner.amount),
                    },
                    timestamp,
                    command_key,
                )
                result = self._orders[order_id]
            self._remember(command_key, "close_auction", payload, result)
            return result

    def cancel_auction(
        self, auction_id: str, now: datetime, *, command_key: str | None = None
    ) -> Auction:
        payload = {"auction_id": auction_id}
        timestamp = as_utc(now)
        with self._lock:
            cached = self._cached(command_key, "cancel_auction", payload)
            if cached is not None:
                return cached
            auction = self._require_auction(auction_id)
            if auction.state in {AuctionState.CLOSED, AuctionState.CANCELLED}:
                raise InvalidTransitionError(f"cannot cancel auction in state {auction.state}")
            if self._bids.get(auction_id):
                raise BidFlowError("an auction with accepted bids cannot be cancelled")
            self._append(
                "auction.state_changed",
                auction_id,
                {"from": auction.state.value, "to": AuctionState.CANCELLED.value, "reason": "cancelled"},
                timestamp,
                command_key,
            )
            result = self._auctions[auction_id]
            self._remember(command_key, "cancel_auction", payload, result)
            return result

    def transition_order(
        self,
        order_id: str,
        target: OrderState,
        now: datetime,
        *,
        command_key: str | None = None,
    ) -> Order:
        target = OrderState(target)
        payload = {"order_id": order_id, "to": target.value}
        timestamp = as_utc(now)
        allowed = {
            OrderState.CREATED: {OrderState.PAYMENT_PENDING, OrderState.CANCELLED},
            OrderState.PAYMENT_PENDING: {OrderState.PAID, OrderState.CANCELLED},
            OrderState.PAID: {OrderState.FULFILLING, OrderState.REFUNDED},
            OrderState.FULFILLING: {OrderState.SHIPPED, OrderState.REFUNDED},
            OrderState.SHIPPED: {OrderState.COMPLETED, OrderState.REFUNDED},
            OrderState.COMPLETED: {OrderState.REFUNDED},
            OrderState.REFUNDED: set(),
            OrderState.CANCELLED: set(),
        }
        with self._lock:
            cached = self._cached(command_key, "transition_order", payload)
            if cached is not None:
                return cached
            order = self._require_order(order_id)
            if target not in allowed[order.state]:
                raise InvalidTransitionError(f"cannot move order {order_id} from {order.state} to {target}")
            self._append(
                "order.transitioned",
                order_id,
                {"from": order.state.value, "to": target.value},
                timestamp,
                command_key,
            )
            result = self._orders[order_id]
            self._remember(command_key, "transition_order", payload, result)
            return result

    def get_auction(self, auction_id: str) -> Auction:
        with self._lock:
            return self._require_auction(auction_id)

    def bids_for(self, auction_id: str) -> tuple[Bid, ...]:
        with self._lock:
            self._require_auction(auction_id)
            return tuple(self._bids.get(auction_id, ()))

    def get_order(self, order_id: str) -> Order:
        with self._lock:
            return self._require_order(order_id)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "schema_version": 1,
                "auctions": [self._auctions[key].as_dict() for key in sorted(self._auctions)],
                "bids": [bid.as_dict() for key in sorted(self._bids) for bid in self._bids[key]],
                "orders": [self._orders[key].as_dict() for key in sorted(self._orders)],
                "settlements": [self._settlements[key] | {"auction_id": key} for key in sorted(self._settlements)],
                "event_count": len(self._events),
            }

    def replay_matches(self) -> bool:
        return BidFlow.replay(self.events).snapshot() == self.snapshot()

    def _apply(self, event: Event) -> None:
        payload = event.payload
        if event.kind == "auction.created":
            if payload.get("auction_id") != event.aggregate_id:
                raise ValueError("auction.created aggregate must match payload auction_id")
            if event.aggregate_id in self._auctions:
                raise ValueError(f"auction already exists: {event.aggregate_id}")
            auction = Auction(
                auction_id=str(payload["auction_id"]),
                seller_id=str(payload["seller_id"]),
                title=str(payload["title"]),
                starts_at=datetime.fromisoformat(str(payload["starts_at"])),
                ends_at=datetime.fromisoformat(str(payload["ends_at"])),
                starting_price=Decimal(str(payload["starting_price"])),
                reserve_price=Decimal(str(payload["reserve_price"])),
                min_increment=Decimal(str(payload["min_increment"])),
            )
            self._auctions[auction.auction_id] = auction
            self._bids.setdefault(auction.auction_id, [])
        elif event.kind == "auction.state_changed":
            auction = self._require_auction(event.aggregate_id)
            try:
                target = AuctionState(str(payload["to"]))
            except KeyError as exc:
                raise ValueError("auction state event requires a target state") from exc
            if payload.get("from") != auction.state.value or target not in {
                AuctionState.DRAFT: {AuctionState.SCHEDULED, AuctionState.LIVE, AuctionState.CANCELLED},
                AuctionState.SCHEDULED: {AuctionState.LIVE, AuctionState.CLOSED, AuctionState.CANCELLED},
                AuctionState.LIVE: {AuctionState.CLOSED, AuctionState.CANCELLED},
                AuctionState.CLOSED: set(),
                AuctionState.CANCELLED: set(),
            }[auction.state]:
                raise ValueError("invalid auction state transition in event log")
            reason = payload.get("reason")
            if reason == "published":
                valid_reason = auction.state == AuctionState.DRAFT and target in {
                    AuctionState.SCHEDULED,
                    AuctionState.LIVE,
                }
            elif reason == "clock":
                valid_reason = target in {AuctionState.LIVE, AuctionState.CLOSED}
            elif reason == "cancelled":
                valid_reason = target == AuctionState.CANCELLED and not self._bids.get(event.aggregate_id)
            else:
                valid_reason = False
            if not valid_reason:
                raise ValueError("invalid auction state transition reason in event log")
            self._auctions[event.aggregate_id] = replace(auction, state=target)
        elif event.kind == "bid.accepted":
            if payload.get("auction_id") != event.aggregate_id:
                raise ValueError("bid.accepted aggregate must match payload auction_id")
            auction = self._require_auction(event.aggregate_id)
            if auction.state != AuctionState.LIVE:
                raise ValueError("accepted bid requires a live auction")
            if not (auction.starts_at <= event.occurred_at < auction.ends_at):
                raise ValueError("accepted bid must fall within the auction window")
            bidder_id = str(payload["bidder_id"])
            if bidder_id == auction.seller_id:
                raise ValueError("seller cannot bid in a replayed event")
            amount = parse_money(Decimal(str(payload["amount"])))
            bids = self._bids[event.aggregate_id]
            minimum = auction.starting_price if not bids else bids[-1].amount + auction.min_increment
            if amount < minimum:
                raise ValueError(f"accepted bid must be at least {minimum:.2f}")
            bid = Bid(
                bid_id=event.event_id,
                auction_id=event.aggregate_id,
                bidder_id=bidder_id,
                amount=amount,
                accepted_at=event.occurred_at,
                sequence=event.sequence,
            )
            bids.append(bid)
        elif event.kind == "auction.settled":
            auction = self._require_auction(event.aggregate_id)
            if auction.state != AuctionState.CLOSED:
                raise ValueError("settlement requires a closed auction")
            if event.aggregate_id in self._settlements:
                raise ValueError("auction cannot be settled twice")
            winner = max(self._bids[event.aggregate_id], key=lambda bid: (bid.amount, -bid.sequence), default=None)
            reserve_met = winner is not None and winner.amount >= auction.reserve_price
            expected = {
                "winner_bid_id": winner.bid_id if reserve_met and winner else None,
                "winner_id": winner.bidder_id if reserve_met and winner else None,
                "winning_amount": str(winner.amount) if reserve_met and winner else None,
                "reserve_met": reserve_met,
            }
            if payload != expected:
                raise ValueError("settlement payload does not match replayed bids")
            self._settlements[event.aggregate_id] = expected
        elif event.kind == "order.created":
            if payload.get("order_id") != event.aggregate_id:
                raise ValueError("order.created aggregate must match payload order_id")
            if event.aggregate_id in self._orders:
                raise ValueError(f"order already exists: {event.aggregate_id}")
            auction_id = str(payload["auction_id"])
            auction = self._require_auction(auction_id)
            settlement = self._settlements.get(auction_id)
            if settlement is None or not settlement["reserve_met"]:
                raise ValueError("order requires a reserve-clearing settlement")
            expected = {
                "order_id": f"order-{auction_id}",
                "auction_id": auction_id,
                "buyer_id": settlement["winner_id"],
                "seller_id": auction.seller_id,
                "total": settlement["winning_amount"],
            }
            if payload != expected:
                raise ValueError("order payload does not match its settlement")
            order = Order(
                order_id=str(payload["order_id"]),
                auction_id=str(payload["auction_id"]),
                buyer_id=str(payload["buyer_id"]),
                seller_id=str(payload["seller_id"]),
                total=Decimal(str(payload["total"])),
            )
            self._orders[order.order_id] = order
        elif event.kind == "order.transitioned":
            order = self._require_order(event.aggregate_id)
            try:
                target = OrderState(str(payload["to"]))
            except KeyError as exc:
                raise ValueError("order transition event requires a target state") from exc
            allowed = {
                OrderState.CREATED: {OrderState.PAYMENT_PENDING, OrderState.CANCELLED},
                OrderState.PAYMENT_PENDING: {OrderState.PAID, OrderState.CANCELLED},
                OrderState.PAID: {OrderState.FULFILLING, OrderState.REFUNDED},
                OrderState.FULFILLING: {OrderState.SHIPPED, OrderState.REFUNDED},
                OrderState.SHIPPED: {OrderState.COMPLETED, OrderState.REFUNDED},
                OrderState.COMPLETED: {OrderState.REFUNDED},
                OrderState.REFUNDED: set(),
                OrderState.CANCELLED: set(),
            }
            if payload.get("from") != order.state.value or target not in allowed[order.state]:
                raise ValueError("invalid order transition in event log")
            self._orders[event.aggregate_id] = replace(order, state=target)
        else:
            raise ValueError(f"unknown event kind: {event.kind}")
