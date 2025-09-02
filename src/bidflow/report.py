from __future__ import annotations

import html
import json
from pathlib import Path

from .engine import BidFlow


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def write_report(engine: BidFlow, output_dir: Path) -> dict[str, str]:
    """Write an event log snapshot and a self-contained audit-friendly HTML view."""

    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "snapshot.json"
    payload = {"snapshot": engine.snapshot(), "events": [event.as_dict() for event in engine.events]}
    _atomic_write(json_path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    auctions = engine.snapshot()["auctions"]
    orders = engine.snapshot()["orders"]
    auction_rows = "".join(
        f"<tr><td><code>{html.escape(str(row['auction_id']))}</code></td>"
        f"<td>{html.escape(str(row['title']))}</td><td>{html.escape(str(row['state']))}</td>"
        f"<td>{html.escape(str(row['starting_price']))}</td><td>{html.escape(str(row['reserve_price']))}</td></tr>"
        for row in auctions
    )
    order_rows = "".join(
        f"<tr><td><code>{html.escape(str(row['order_id']))}</code></td>"
        f"<td>{html.escape(str(row['buyer_id']))}</td><td>{html.escape(str(row['total']))}</td>"
        f"<td>{html.escape(str(row['state']))}</td></tr>"
        for row in orders
    ) or '<tr><td colspan="4">No reserve-clearing order was created.</td></tr>'
    event_rows = "".join(
        f"<tr><td>{event.sequence}</td><td><code>{html.escape(event.kind)}</code></td>"
        f"<td>{html.escape(event.aggregate_id)}</td><td>{html.escape(event.occurred_at.isoformat())}</td></tr>"
        for event in engine.events
    )
    replay_status = "PASS" if engine.replay_matches() else "FAIL"
    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>BidFlow audit report</title><style>body{{margin:0;background:#0b1020;color:#e6edf7;font:15px/1.5 system-ui,sans-serif}}main{{max-width:1100px;margin:0 auto;padding:32px 20px}}.panel{{background:#121c31;border:1px solid #263b5d;border-radius:16px;padding:18px;margin:16px 0}}.metrics{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}}.metric{{background:#192845;padding:14px;border-radius:12px}}.metric b{{display:block;font-size:25px;color:#7dd3fc}}.muted{{color:#9fb2d0}}table{{width:100%;border-collapse:collapse}}th,td{{padding:8px;text-align:left;border-bottom:1px solid #263b5d;vertical-align:top}}th{{color:#9fb2d0}}code{{color:#a5b4fc}}@media(max-width:700px){{.metrics{{grid-template-columns:1fr}}table{{font-size:12px}}}}</style></head>
<body><main><h1>BidFlow event-sourced audit</h1><p class="muted">Synthetic auction fixture · replay verification <strong>{replay_status}</strong></p>
<section class="metrics"><div class="metric"><b>{len(auctions)}</b>auctions</div><div class="metric"><b>{len(orders)}</b>orders</div><div class="metric"><b>{len(engine.events)}</b>events</div></section>
<section class="panel"><h2>Auctions</h2><table><thead><tr><th>ID</th><th>Title</th><th>State</th><th>Start</th><th>Reserve</th></tr></thead><tbody>{auction_rows}</tbody></table></section>
<section class="panel"><h2>Orders</h2><table><thead><tr><th>ID</th><th>Buyer</th><th>Total</th><th>State</th></tr></thead><tbody>{order_rows}</tbody></table></section>
<section class="panel"><h2>Append-only event timeline</h2><table><thead><tr><th>#</th><th>Kind</th><th>Aggregate</th><th>Occurred at</th></tr></thead><tbody>{event_rows}</tbody></table></section>
<section class="panel"><p class="muted">The report stores event payloads and synthetic identifiers only. Review data rights and redact adapter-supplied records before sharing.</p></section>
</main></body></html>"""
    html_path = output_dir / "report.html"
    _atomic_write(html_path, page)
    return {"json": str(json_path), "html": str(html_path)}
