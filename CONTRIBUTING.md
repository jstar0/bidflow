# Contributing

- Keep fixtures synthetic and timezone-aware.
- Add a test for every new state transition, idempotency rule, or event shape.
- Preserve deterministic event ordering and decimal serialization.
- Run `uv run pytest`, `uv run --with ruff ruff check src tests`, and `uv lock --check` before submitting a change.
- Never add customer identities, payment credentials, marketplace exports, or provider secrets.
