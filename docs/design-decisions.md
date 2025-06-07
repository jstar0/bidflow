# BidFlow design decisions and limits

BidFlow is a compact, single-process event-sourcing exercise for a synthetic
auction fixture. This note records the guarantees the code makes, the simpler
alternatives it rejects, and the boundaries it deliberately does not cross.

## Decisions embodied in the implementation

1. **Commands append facts; projections are rebuilt from those facts.**
   `BidFlow._append()` allocates a contiguous sequence and immutable `Event`,
   then `_apply()` updates the auction, bid, settlement, and order projections.
   `BidFlow.replay()` reconstructs those projections from the event list.
   Rejected alternative: a CRUD-only in-memory model, because the demo needs a
   replayable timeline.

2. **Money is `Decimal` with two fractional digits.** `parse_money()` rejects
   non-finite, negative, and over-precise amounts before command handling or
   replay. Rejected alternative: floating-point money, because tie-breaking and
   reserve checks must be deterministic.

3. **Auction and order lifecycles are explicit state machines.** Auction
   commands produce `draft → scheduled/live → closed` or `cancelled`; orders
   use an explicit allowed-transition table. Rejected alternative: a generic
   `set_status` command, because invalid transitions would otherwise be
   indistinguishable in the audit trail.

4. **Idempotency binds a key to the command payload.** A SHA-256 fingerprint of
   canonical command plus payload is remembered under the caller key. Repeating
   the same command returns the original result; reusing its key for another
   command raises `IdempotencyConflictError`. A key-only cache was rejected
   because it could silently turn a changed bid into a stale success.

5. **Replay is fail-closed for logical integrity.** Replayed events must have
   contiguous sequences and unique IDs. `_apply()` checks aggregate/payload
   identity, current state, bid window and minimum, settlement recomputation,
   order-to-settlement linkage, and legal order transitions before changing a
   projection. Trusting JSON because it was named `snapshot.json` was rejected:
   that previously admitted forged transitions and orphan bids.

6. **Winner selection is deterministic.** Settlement selects the highest
   `Decimal` bid and breaks ties by the earliest event sequence. A wall-clock
   tie-break was rejected because it is sensitive to timestamp precision and
   input serialization.

7. **The demonstration is offline and synthetic.** The CLI constructs one
   fixed auction, and the benchmark covers exactly reserve-clearing and
   reserve-missed scenarios. Provider adapters, payment capture, account
   storage, and marketplace data are outside this repository.

## Integrity checks added after adversarial replay review

The replay path formerly verified only sequence continuity. A crafted event list
could therefore reuse an event ID, attach an `auction.created` payload to a
different aggregate, inject a bid for an unknown auction, or claim an arbitrary
state/settlement. The current checks reject each case before emitting a
projection. `tests/test_engine.py` contains direct regression tests for duplicate
IDs, aggregate mismatch/orphan bids, and forged close/settlement events.

These checks establish **internal consistency**, not source authenticity. A
well-formed event stream can still be fabricated by someone who controls the
input file.

## Known weaknesses

1. There is no durable event store, transaction log, optimistic concurrency
   token, or multi-process coordination. The lock only serializes threads in
   one Python process.
2. Event JSON is not signed, hash chained, or anchored externally. Replay can
   reject contradictions but cannot prove who wrote a consistent stream.
3. `Event.from_dict()` remains a permissive local-file parser that normalizes
   values through `str()` and `int()`; it is not a public API schema or a
   substitute for an authenticated transport boundary.
4. Event timestamps are checked for bid-window membership but are not globally
   monotonic across multiple auctions. The fixture is a deterministic local
   ledger, not a distributed ordering protocol.
5. Cancellation, refund, and fulfilment state changes model only their state
   labels. They do not call payments, inventory, shipping, notifications, or
   access-control services.
6. The benchmark has two fixed synthetic auctions. It proves replay and reserve
   branches on that fixture; it says nothing about load, market pricing,
   fraud, user behaviour, or payment reliability.
7. The HTML report is an inspection view, not an authorization surface. It
   includes synthetic identifiers and must not be used to display real customer
   data without a separate data-rights review.

## Interview-safe description

Describe BidFlow as an independent local event-sourced auction state-machine
demo with deterministic money, idempotent commands, strict replay consistency
checks, and synthetic benchmark artifacts. Do not describe it as a payment
system, an immutable audit ledger, a marketplace backend, or evidence of real
transactions.
