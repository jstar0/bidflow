# BidFlow

BidFlow is a compact event-sourced auction and order state machine. It is designed to make the hard backend edges visible:

- timezone-aware auction windows with explicit `draft → scheduled → live → closed` transitions;
- precise money arithmetic with `Decimal` and reserve-price settlement;
- thread-safe, monotonic bid acceptance with deterministic tie-breaking;
- idempotency keys that reject accidental reuse for a different command;
- an append-only event log that can be replayed to reconstruct the same snapshot;
- explicit order transitions, invalid-state diagnostics, and a self-contained audit report.

There is no web server, payment provider, customer account, or real marketplace data in this repository. The CLI uses a synthetic fixture so a clean checkout can demonstrate the complete flow offline.

## Quick start

```bash
uv sync --extra dev
uv run bidflow demo --output-dir outputs/demo
open outputs/demo/report.html
```

The generated `snapshot.json` contains the event timeline and current state. Replay it with:

```bash
uv run bidflow replay outputs/demo/snapshot.json
```

Replay verifies more than event order: it rejects duplicate event IDs,
aggregate/payload mismatches, orphan bids, invalid lifecycle transitions, and
settlement or order payloads that do not match the replayed bids. This is an
internal-consistency check only; the local JSON format is not signed or an
authenticated event store.

## Verification

```bash
uv run pytest
uv run --with ruff ruff check src tests
uv lock --check
uv build
```

The state machine is an independent implementation of a general event-driven commerce problem. It does not copy client code, schemas, credentials, or customer records. MIT licensed; see [LICENSE](LICENSE).
