# Architecture

## Command and event boundaries

`BidFlow` is the command boundary. Every mutating command executes under an `RLock`, validates the current aggregate state, appends one or more immutable `Event` records, and applies those events to in-memory projections. A command key is fingerprinted with its normalized payload; a repeated key returns the original result, while a changed payload raises `IdempotencyConflictError`.

The event stream is the source of truth for the demo. `BidFlow.replay()` applies the same event types without executing commands, and `replay_matches()` compares the reconstructed snapshot to the live projection.

## Invariants

- All timestamps are timezone-aware and normalized to UTC.
- Money is quantized to cents before validation and stored as decimal strings in events.
- A bid is accepted only while the auction is live, from a non-seller, and at least the starting price or previous bid plus the configured increment.
- A winning bid must meet the reserve before an order is emitted.
- Event sequences are contiguous and event IDs are monotonic (`evt-000001`, ...).
- Order transitions are an explicit allow-list; terminal states cannot be advanced.

## Concurrency and replay

The lock serializes the check-then-append section, so two simultaneous bids cannot both pass the same minimum-price check. Event sequence is the deterministic tie-break when amounts match. The tests exercise concurrent submissions and then replay the resulting stream.

This is an in-memory reference implementation, not a production database. A durable adapter would need transactional append, optimistic version checks, persistence, and an audited authorization layer.
