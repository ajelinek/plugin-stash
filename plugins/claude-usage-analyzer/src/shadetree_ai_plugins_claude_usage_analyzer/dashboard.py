"""Render the reorganization plan as a self-contained, re-renderable HTML
dashboard, and keep a local history log so re-running analysis later shows
progress over time instead of just the latest snapshot.

This module does no analysis of its own -- clustering chats, naming
Projects, deciding what's stale -- that reasoning is the calling skill's
job (it needs to actually understand conversation content, which this
module never sees). This is presentation only: it takes the structured
`plan` the skill already produced and turns it into HTML.

Colors/typography follow this workspace's dataviz skill: status colors
(good/warning/critical) always ship with an icon + label, never as a bare
color; text stays in ink roles, never the accent color; light/dark are
both selected via `prefers-color-scheme` and a `data-theme` override so a
host page's theme toggle wins in both directions.
"""

from __future__ import annotations

import json
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any

_STATUS_ICON = {
    "good": "\U0001f7e2",
    "warning": "\U0001f7e1",
    "critical": "\U0001f534",
    "info": "\U0001f535",
}

_STYLE = """
:root { color-scheme: light; }
@media (prefers-color-scheme: dark) {
  :root:where(:not([data-theme="light"])) { color-scheme: dark; }
}
:root[data-theme="dark"] { color-scheme: dark; }

body {
  --surface-1: #fcfcfb; --page: #f9f9f7; --ink-1: #0b0b0b; --ink-2: #52514e;
  --ink-muted: #898781; --grid: #e1e0d9; --border: rgba(11,11,11,0.10);
  --accent: #2a78d6; --good: #0ca30c; --warning: #fab219; --critical: #d03b3b;
  background: var(--page); color: var(--ink-1);
  font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
  margin: 0; padding: 2rem 1.25rem 4rem; line-height: 1.5;
}
@media (prefers-color-scheme: dark) {
  body:where(:not([data-theme="light"] body)) {
    --surface-1: #1a1a19; --page: #0d0d0d; --ink-1: #ffffff; --ink-2: #c3c2b7;
    --ink-muted: #898781; --grid: #2c2c2a; --border: rgba(255,255,255,0.10);
    --accent: #3987e5; --good: #0ca30c; --warning: #fab219; --critical: #e66767;
  }
}
[data-theme="dark"] body {
  --surface-1: #1a1a19; --page: #0d0d0d; --ink-1: #ffffff; --ink-2: #c3c2b7;
  --ink-muted: #898781; --grid: #2c2c2a; --border: rgba(255,255,255,0.10);
  --accent: #3987e5; --good: #0ca30c; --warning: #fab219; --critical: #e66767;
}
.wrap { max-width: 920px; margin: 0 auto; }
h1 { font-size: 1.5rem; margin: 0 0 0.25rem; }
.subtitle { color: var(--ink-2); margin: 0 0 0.5rem; }
.sources { color: var(--ink-muted); font-size: 0.85rem; margin: 0 0 0.4rem; }
.window {
  display: inline-block; margin: 0 0 2rem; padding: 0.35rem 0.7rem;
  border-radius: 6px; font-size: 0.85rem;
  background: var(--accent-soft, rgba(120,140,255,0.12)); color: var(--ink-2);
}
.window-full { background: transparent; padding-left: 0; color: var(--ink-muted); }
.stat-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 0.75rem; margin-bottom: 2.5rem; }
.stat-tile { background: var(--surface-1); border: 1px solid var(--border); border-radius: 10px;
  padding: 0.9rem 1rem; }
.stat-tile .label { color: var(--ink-2); font-size: 0.8rem; margin-bottom: 0.25rem; }
.stat-tile .value { font-size: 1.6rem; font-weight: 600; }
.stat-tile .status { font-size: 0.8rem; margin-top: 0.25rem; color: var(--ink-2); }
h2 { font-size: 1.15rem; border-bottom: 1px solid var(--grid);
  padding-bottom: 0.4rem; margin-top: 2.5rem; }
.card { background: var(--surface-1); border: 1px solid var(--border); border-radius: 10px;
  padding: 1rem 1.25rem; margin-bottom: 1rem; }
.card h3 { margin: 0 0 0.35rem; font-size: 1.05rem; }
.card .tag { display: inline-block; font-size: 0.72rem; color: var(--ink-2);
  border: 1px solid var(--border); border-radius: 999px;
  padding: 0.05rem 0.5rem; margin-left: 0.5rem; }
.card .field-label { color: var(--ink-muted); font-size: 0.78rem; text-transform: uppercase;
  letter-spacing: 0.02em; margin: 0.6rem 0 0.15rem; }
.card ul { margin: 0.15rem 0 0; padding-left: 1.1rem; }
.card .instructions { white-space: pre-wrap; background: var(--page); border-radius: 6px;
  padding: 0.5rem 0.7rem; font-size: 0.9rem; }
.notes { color: var(--ink-2); font-size: 0.9rem; }
.notes li { margin-bottom: 0.35rem; }
.start-here { background: var(--surface-1); border: 1px solid var(--border);
  border-left: 3px solid var(--accent); border-radius: 10px;
  padding: 0.25rem 1.25rem 1rem; margin-bottom: 2.5rem; }
.start-here h2 { border-bottom: none; margin-top: 1rem; margin-bottom: 0.25rem; }
.fix-list { list-style: none; margin: 0; padding: 0; }
.fix { display: flex; gap: 0.85rem; align-items: baseline;
  padding: 0.7rem 0; border-top: 1px solid var(--grid); }
.fix:first-child { border-top: none; }
.fix-rank { flex: 0 0 1.5rem; height: 1.5rem; line-height: 1.5rem; text-align: center;
  border-radius: 999px; background: var(--accent); color: #fff;
  font-size: 0.8rem; font-weight: 600; font-variant-numeric: tabular-nums; }
.fix-body { flex: 1 1 auto; min-width: 0; }
.fix-title { margin: 0; font-weight: 600; }
.fix-action { margin: 0.15rem 0 0; color: var(--ink-2); font-size: 0.92rem; }
.fix-meta { margin: 0.3rem 0 0; color: var(--ink-muted); font-size: 0.78rem; }
.fix-overflow { margin: 0.75rem 0 0; color: var(--ink-muted); font-size: 0.8rem; }
table.history { border-collapse: collapse; width: 100%; font-size: 0.85rem; }
table.history th, table.history td { text-align: left; padding: 0.35rem 0.6rem;
  border-bottom: 1px solid var(--grid); font-variant-numeric: tabular-nums; }
table.history th { color: var(--ink-muted); font-weight: 600; }
.bars { display: flex; align-items: flex-end; gap: 3px; height: 32px; margin-top: 0.4rem; }
.bars .bar { width: 8px; background: var(--accent); border-radius: 2px 2px 0 0; }
.model-usage { background: var(--surface-1); border: 1px solid var(--border); border-radius: 10px;
  padding: 0.85rem 1.1rem; margin-bottom: 1rem; }
.model-row { display: grid; grid-template-columns: minmax(120px, 1fr) 3fr auto;
  align-items: center; gap: 0.6rem; padding: 0.3rem 0; }
.model-name { font-size: 0.85rem; color: var(--ink-2); overflow-wrap: anywhere; }
.model-bar-track { background: var(--grid); border-radius: 999px; height: 10px; overflow: hidden; }
.model-bar { background: var(--accent); height: 100%; border-radius: 999px; }
.model-count { font-size: 0.85rem; color: var(--ink-2); font-variant-numeric: tabular-nums;
  text-align: right; }
footer { color: var(--ink-muted); font-size: 0.8rem; margin-top: 3rem; }
"""


def _fmt_status(status: str | None) -> str:
    if not status or status not in _STATUS_ICON:
        return ""
    icon = _STATUS_ICON[status]
    return f'<div class="status">{icon} {escape(status.capitalize())}</div>'


def _stat_tile(tile: dict[str, Any]) -> str:
    return (
        '<div class="stat-tile">'
        f'<div class="label">{escape(str(tile.get("label", "")))}</div>'
        f'<div class="value">{escape(str(tile.get("value", "")))}</div>'
        f"{_fmt_status(tile.get('status'))}"
        "</div>"
    )


def _list_items(items: list[str] | None) -> str:
    if not items:
        return ""
    return "<ul>" + "".join(f"<li>{escape(str(i))}</li>" for i in items) + "</ul>"


def _project_card(project: dict[str, Any]) -> str:
    name = escape(str(project.get("name", "(unnamed project)")))
    status_tag = project.get("status")
    tag_html = f'<span class="tag">{escape(str(status_tag))}</span>' if status_tag else ""
    description = escape(str(project.get("description", "")))
    instructions = project.get("instructions")
    chats = project.get("chats") or []
    files = project.get("files") or []

    parts = [f"<h3>{name}{tag_html}</h3>"]
    if description:
        parts.append(f"<p>{description}</p>")
    if instructions:
        parts.append('<div class="field-label">Custom instructions</div>')
        parts.append(f'<div class="instructions">{escape(str(instructions))}</div>')
    if chats:
        parts.append(f'<div class="field-label">Chats moving in ({len(chats)})</div>')
        parts.append(_list_items([str(c) for c in chats]))
    if files:
        parts.append('<div class="field-label">Project files/folders</div>')
        parts.append(_list_items([str(fp) for fp in files]))

    return f'<div class="card">{"".join(parts)}</div>'


def _leftover_card(item: dict[str, Any]) -> str:
    name = escape(str(item.get("name", "(unnamed)")))
    note = escape(str(item.get("note", "")))
    return f'<div class="card"><h3>{name}</h3>{f"<p>{note}</p>" if note else ""}</div>'


def _finding_card(finding: dict[str, Any]) -> str:
    title = escape(str(finding.get("title", "(untitled finding)")))
    recommendation = escape(str(finding.get("recommendation", "")))
    evidence = finding.get("evidence") or []

    parts = [f"<h3>{title}</h3>", _fmt_status(finding.get("severity"))]
    if recommendation:
        parts.append(f"<p>{recommendation}</p>")
    if evidence:
        parts.append('<div class="field-label">Evidence</div>')
        parts.append(_list_items([str(e) for e in evidence]))
    return f'<div class="card">{"".join(parts)}</div>'


def _recommendation_card(rec: dict[str, Any]) -> str:
    title = escape(str(rec.get("title", "(untitled recommendation)")))
    rationale = escape(str(rec.get("rationale", "")))
    evidence = rec.get("evidence") or []
    tag_bits = [b for b in (rec.get("item_type"), rec.get("source")) if b]
    tag_html = (
        f'<span class="tag">{escape(" · ".join(str(b) for b in tag_bits))}</span>'
        if tag_bits
        else ""
    )

    parts = [f"<h3>{title}{tag_html}</h3>"]
    if rationale:
        parts.append(f"<p>{rationale}</p>")
    if evidence:
        parts.append('<div class="field-label">Evidence</div>')
        parts.append(_list_items([str(e) for e in evidence]))
    return f'<div class="card">{"".join(parts)}</div>'


def _recommendations_section(recommendations: list[dict[str, Any]]) -> str:
    if not recommendations:
        return ""
    existing = [r for r in recommendations if r.get("kind") == "existing"]
    custom = [r for r in recommendations if r.get("kind") != "existing"]

    parts = [
        f"<h2>Recommended skills, plugins &amp; connectors ({len(recommendations)})</h2>"
    ]
    if existing:
        parts.append('<div class="field-label">Already available -- install/connect these</div>')
        parts.extend(_recommendation_card(r) for r in existing)
    if custom:
        parts.append('<div class="field-label">Worth building custom</div>')
        parts.extend(_recommendation_card(r) for r in custom)
    return "".join(parts)


def _model_usage_section(model_usage: list[dict[str, Any]]) -> str:
    if not model_usage:
        return ""
    max_count = max((float(m.get("count") or 0) for m in model_usage), default=0) or 1
    rows = []
    for m in model_usage:
        model = escape(str(m.get("model", "(unknown)")))
        count = m.get("count", 0)
        pct = max(4, round(float(count or 0) / max_count * 100))
        rows.append(
            '<div class="model-row">'
            f'<div class="model-name">{model}</div>'
            f'<div class="model-bar-track"><div class="model-bar" '
            f'style="width:{pct}%"></div></div>'
            f'<div class="model-count">{escape(str(count))}</div>'
            "</div>"
        )
    return '<h2>Model usage</h2><div class="model-usage">' + "".join(rows) + "</div>"


def _mini_bars(values: list[float]) -> str:
    if len(values) < 2:
        return ""
    max_v = max(values) or 1
    bars = "".join(
        f'<div class="bar" style="height:{max(4, round(v / max_v * 32))}px" title="{v}"></div>'
        for v in values
    )
    return f'<div class="bars">{bars}</div>'


def _history_section(history: list[dict[str, Any]]) -> str:
    if not history:
        return ""
    all_labels: list[str] = []
    for snap in history:
        for tile in snap.get("stat_tiles", []):
            label = tile.get("label")
            if label and label not in all_labels:
                all_labels.append(label)

    header_cells = "".join(f"<th>{escape(label)}</th>" for label in all_labels)
    rows = []
    for snap in history:
        by_label = {t.get("label"): t.get("value") for t in snap.get("stat_tiles", [])}
        cells = "".join(
            f"<td>{escape(str(by_label.get(label, '')))}</td>" for label in all_labels
        )
        rows.append(f"<tr><td>{escape(str(snap.get('generated_at', '')))}</td>{cells}</tr>")

    trend_html = ""
    if all_labels:
        first_label = all_labels[0]
        numeric_values: list[float] = []
        for snap in history:
            by_label = {t.get("label"): t.get("value") for t in snap.get("stat_tiles", [])}
            raw = by_label.get(first_label)
            try:
                numeric_values.append(float(str(raw).replace(",", "")))
            except (TypeError, ValueError):
                numeric_values = []
                break
        trend_html = _mini_bars(numeric_values)
        if trend_html:
            trend_html = f'<p class="notes">Trend for "{escape(first_label)}":</p>{trend_html}'

    return (
        "<h2>Progress over time</h2>"
        f"<table class=\"history\"><thead><tr><th>Run</th>{header_cells}</tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>{trend_html}"
    )


_START_HERE_SOFT_LIMIT = 5


def _start_here_section(fixes: list[dict[str, Any]]) -> str:
    """The ranked "do these first" list that opens the dashboard.

    The rest of the page is deliberately complete -- every lens reports
    everything it found. That completeness is only useful if the reader
    isn't drowned in it, so this section is the one place that ranks. It is
    a *pointer* to findings detailed below, not a separate set of them.
    """
    if not fixes:
        return ""
    rows = []
    for rank, fix in enumerate(fixes, start=1):
        title = escape(str(fix.get("title", "")))
        action = escape(str(fix.get("action", "")))
        where = fix.get("where")
        impact = fix.get("impact")
        meta = []
        if where:
            meta.append(f'<span class="fix-where">{escape(str(where))}</span>')
        if impact:
            meta.append(f'<span class="fix-impact">{escape(str(impact))}</span>')
        rows.append(
            f'<li class="fix"><span class="fix-rank">{rank}</span>'
            f'<div class="fix-body"><p class="fix-title">{title}</p>'
            f'<p class="fix-action">{action}</p>'
            + (f'<p class="fix-meta">{" &middot; ".join(meta)}</p>' if meta else "")
            + "</div></li>"
        )
    overflow = (
        f'<p class="fix-overflow">{len(fixes)} fixes listed -- '
        f"everything else this run found is detailed below.</p>"
        if len(fixes) > _START_HERE_SOFT_LIMIT
        else ""
    )
    return (
        '<section class="start-here"><h2>Start here</h2>'
        f'<ol class="fix-list">{"".join(rows)}</ol>{overflow}</section>'
    )


def render_dashboard_html(plan: dict[str, Any], history: list[dict[str, Any]]) -> str:
    title = escape(str(plan.get("title", "Claude usage analysis")))
    subtitle = plan.get("subtitle")
    generated_at = plan.get("generated_at") or datetime.now().astimezone().isoformat()
    sources = plan.get("data_sources") or []
    time_window = plan.get("time_window")
    stat_tiles = plan.get("stat_tiles") or []
    projects = plan.get("projects") or []
    leftovers = plan.get("leftovers") or []
    notes = plan.get("notes") or []
    findings = plan.get("findings") or []
    model_usage = plan.get("model_usage") or []
    recommendations = plan.get("recommendations") or []
    start_here = plan.get("start_here") or []

    sections = [
        f"<h1>{title}</h1>",
        f'<p class="subtitle">{escape(str(subtitle))}</p>' if subtitle else "",
        (
            f'<p class="sources">Generated {escape(str(generated_at))} '
            f'from: {escape(", ".join(str(s) for s in sources))}</p>'
            if sources
            else f'<p class="sources">Generated {escape(str(generated_at))}</p>'
        ),
        # Rendered as its own prominent line rather than folded into the
        # sources footnote: every number below describes this period, and a
        # reader who misses that will read a windowed count as an account
        # total. Absent means the run covered the full history.
        (
            f'<p class="window"><strong>Time window:</strong> '
            f"{escape(str(time_window))}</p>"
            if time_window
            else '<p class="window window-full">Covering all available history.</p>'
        ),
    ]

    if stat_tiles:
        sections.append(
            '<div class="stat-grid">' + "".join(_stat_tile(t) for t in stat_tiles) + "</div>"
        )

    # Directly after the headline numbers and before every detailed section:
    # the reader should meet the ranked shortlist before the full inventory.
    sections.append(_start_here_section(start_here))

    if findings:
        sections.append(f"<h2>Usage &amp; best-practices findings ({len(findings)})</h2>")
        sections.extend(_finding_card(f) for f in findings)

    if recommendations:
        sections.append(_recommendations_section(recommendations))

    if projects:
        sections.append(f"<h2>Proposed Projects ({len(projects)})</h2>")
        sections.extend(_project_card(p) for p in projects)

    if model_usage:
        sections.append(_model_usage_section(model_usage))

    if leftovers:
        sections.append(f"<h2>Leftovers ({len(leftovers)})</h2>")
        sections.extend(_leftover_card(item) for item in leftovers)

    if notes:
        sections.append("<h2>Data quality notes</h2>")
        note_items = "".join(f"<li>{escape(str(n))}</li>" for n in notes)
        sections.append(f'<ul class="notes">{note_items}</ul>')

    sections.append(_history_section(history))

    sections.append(
        "<footer>This is a proposed plan only -- nothing here has moved, renamed, or deleted "
        "any chat or Project. Review it, then decide what to actually apply.</footer>"
    )

    body = "\n".join(s for s in sections if s)
    return (
        "<!doctype html><html><head><meta charset=\"utf-8\">"
        f"<title>{title}</title><style>{_STYLE}</style></head>"
        f"<body><div class=\"wrap\">{body}</div></body></html>\n"
    )


def render_dashboard(
    plan: dict[str, Any], out_path: str, history_path: str | None = None
) -> dict[str, Any]:
    """Render `plan` (see this module's docstring / the render_dashboard tool
    for the expected shape) to `out_path`, append a snapshot to
    `history_path` (JSONL, oldest first) if given, and return the path,
    raw HTML (so a caller with an Artifact-publishing capability can use it
    directly), and how many prior runs are now in the history."""
    history: list[dict[str, Any]] = []
    history_file = Path(history_path).expanduser() if history_path else None
    if history_file and history_file.exists():
        for line in history_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                history.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    generated_at = plan.get("generated_at") or datetime.now().astimezone().isoformat()
    snapshot = {"generated_at": generated_at, "stat_tiles": plan.get("stat_tiles") or []}

    html = render_dashboard_html(plan, history)

    out_file = Path(out_path).expanduser()
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(html, encoding="utf-8")

    if history_file:
        history_file.parent.mkdir(parents=True, exist_ok=True)
        with history_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(snapshot, ensure_ascii=False) + "\n")

    return {
        "path": str(out_file),
        "html": html,
        "history_runs": len(history) + 1,
    }
