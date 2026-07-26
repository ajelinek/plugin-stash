"""Parse a claude.ai account data export (Settings > Account > Export Data)
into a handful of compact files a caller can page through, instead of
holding a potentially huge `conversations.json` in context at once.

This is a *different* format from a Claude Code CLI session log
(`~/.claude/projects/*.jsonl`, handled by local_data.py) or a single pasted
conversation transcript -- it covers claude.ai web chats only, with zero
overlap with local_data.py's Desktop/Cowork/CLI sources. See
skills/claude-usage-analyzer/references/data-sources.md section 4 for how
the two combine.

Export schemas vary by account/export vintage and aren't a documented
public API, so every reader here is defensive: an unexpected shape is
noted, never raised.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

FIRST_MESSAGE_PREVIEW_CHARS = 300

_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "of", "to", "in", "on", "for", "with",
    "is", "are", "was", "were", "be", "been", "being", "this", "that", "it",
    "as", "at", "by", "from", "i", "you", "we", "me", "my", "your", "our",
    "do", "does", "did", "can", "could", "will", "would", "should", "have",
    "has", "had", "not", "so", "if", "then", "than", "just", "about", "into",
    "up", "out", "what", "how", "when", "where", "why", "who", "which",
}


def _text_of_content_block(block: Any) -> str:
    if isinstance(block, str):
        return block
    if isinstance(block, dict):
        if isinstance(block.get("text"), str):
            return block["text"]
        if isinstance(block.get("input"), dict):
            return ""  # tool_use input params -- not conversation prose
    return ""


def _message_text(message: dict[str, Any]) -> str:
    if isinstance(message.get("text"), str) and message["text"]:
        return message["text"]
    content = message.get("content")
    if isinstance(content, list):
        return " ".join(_text_of_content_block(b) for b in content).strip()
    return ""


def _tool_names(message: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    content = message.get("content")
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name"):
                names.add(block["name"])
    return names


def _keywords(text: str, limit: int = 8) -> list[str]:
    tokens = re.findall(r"[a-zA-Z][a-zA-Z0-9_-]{2,}", text.lower())
    counts = Counter(t for t in tokens if t not in _STOPWORDS)
    return [word for word, _ in counts.most_common(limit)]


def _load_json(path: Path) -> Any | None:
    try:
        with path.open(encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


# --------------------------------------------------------------------------------
# conversations.json
# --------------------------------------------------------------------------------


def summarize_conversation(convo: dict[str, Any]) -> dict[str, Any]:
    messages = convo.get("chat_messages") or []
    human_messages = [m for m in messages if m.get("sender") == "human"]
    first_human_text = _message_text(human_messages[0]) if human_messages else ""

    tool_names: set[str] = set()
    for m in messages:
        tool_names |= _tool_names(m)

    keyword_source = f"{convo.get('name') or ''} {first_human_text}"

    return {
        "uuid": convo.get("uuid"),
        "name": convo.get("name") or "(untitled)",
        "created_at": convo.get("created_at"),
        "updated_at": convo.get("updated_at"),
        "project_uuid": convo.get("project_uuid"),
        "message_count": len(messages),
        "first_human_message": first_human_text[:FIRST_MESSAGE_PREVIEW_CHARS],
        "tool_names": sorted(tool_names),
        "keywords": _keywords(keyword_source),
    }


def load_conversations(export_dir: Path) -> tuple[list[dict[str, Any]], list[str]]:
    notes: list[str] = []
    path = export_dir / "conversations.json"
    data = _load_json(path)
    if not isinstance(data, list):
        notes.append(f"{path} missing or not a JSON array -- treating as zero conversations.")
        return [], notes

    summaries = [summarize_conversation(c) for c in data if isinstance(c, dict)]
    summaries.sort(key=lambda c: c.get("created_at") or "")
    return summaries, notes


# --------------------------------------------------------------------------------
# projects/*.json (accepts either a single projects.json array or a projects/ dir)
# --------------------------------------------------------------------------------


def load_projects(export_dir: Path) -> tuple[dict[str, dict[str, Any]], list[str]]:
    notes: list[str] = []
    projects: dict[str, dict[str, Any]] = {}

    single_file = export_dir / "projects.json"
    projects_dir = export_dir / "projects"

    raw_entries: list[Any] = []
    if single_file.is_file():
        data = _load_json(single_file)
        if isinstance(data, list):
            raw_entries.extend(data)
        else:
            notes.append(f"{single_file} present but not a JSON array -- ignored.")
    elif projects_dir.is_dir():
        for p in sorted(projects_dir.glob("*.json")):
            data = _load_json(p)
            if isinstance(data, dict):
                raw_entries.append(data)
            elif isinstance(data, list):
                raw_entries.extend(data)
    else:
        notes.append("No projects.json or projects/ directory in this export.")

    for entry in raw_entries:
        if not isinstance(entry, dict) or not entry.get("uuid"):
            continue
        projects[entry["uuid"]] = {
            "uuid": entry["uuid"],
            "name": entry.get("name"),
            "description": entry.get("description"),
            "prompt_template": entry.get("prompt_template"),
        }
    return projects, notes


# --------------------------------------------------------------------------------
# memories.json -- Claude's own synthesized narrative, verbatim
# --------------------------------------------------------------------------------

_GLOBAL_MEMORY_KEYS = ("memory", "global_memory", "account_memory", "narrative")
_PROJECT_MEMORIES_KEYS = ("project_memories", "projects", "per_project_memories")


def render_memory_context(export_dir: Path) -> tuple[str, list[str]]:
    notes: list[str] = []
    path = export_dir / "memories.json"
    data = _load_json(path)
    if data is None:
        notes.append(f"{path} not present in this export -- no memory_context available.")
        return "# Memory context\n\n(No memories.json in this export.)\n", notes

    if not isinstance(data, dict):
        notes.append(f"{path} present but not a JSON object -- dumping raw preview only.")
        return f"# Memory context\n\n```\n{json.dumps(data, indent=2)[:2000]}\n```\n", notes

    lines = ["# Memory context", ""]

    global_text = next((data[k] for k in _GLOBAL_MEMORY_KEYS if isinstance(data.get(k), str)), None)
    if global_text:
        lines += ["## Account-level memory", "", global_text.strip(), ""]

    project_entries = next(
        (data[k] for k in _PROJECT_MEMORIES_KEYS if isinstance(data.get(k), list)), None
    )
    if project_entries:
        lines += ["## Per-project memory", ""]
        for entry in project_entries:
            if not isinstance(entry, dict):
                continue
            name = (
                entry.get("project_name") or entry.get("name") or entry.get("project_uuid") or "?"
            )
            text = entry.get("memory") or entry.get("text") or ""
            if not text:
                continue
            lines += [f"### {name}", "", text.strip(), ""]
    elif not global_text:
        notes.append(f"{path} has an unrecognized shape -- dumping a raw preview only.")
        lines += ["## Raw preview", "", "```", json.dumps(data, indent=2)[:2000], "```"]

    return "\n".join(lines) + "\n", notes


# --------------------------------------------------------------------------------
# Top-level entry point
# --------------------------------------------------------------------------------


def parse_export(export_dir: str, out_dir: str) -> dict[str, Any]:
    export_path = Path(export_dir).expanduser()
    out_path = Path(out_dir).expanduser()
    out_path.mkdir(parents=True, exist_ok=True)

    if not export_path.is_dir():
        raise ValueError(f"export_dir {export_path} is not a directory.")

    conversations, convo_notes = load_conversations(export_path)
    projects, project_notes = load_projects(export_path)
    memory_md, memory_notes = render_memory_context(export_path)
    notes = [*convo_notes, *project_notes, *memory_notes]

    conversations_path = out_path / "conversations.jsonl"
    with conversations_path.open("w", encoding="utf-8") as f:
        for c in conversations:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")

    project_conversation_counts = Counter(
        c["project_uuid"] for c in conversations if c.get("project_uuid")
    )
    projects_index = {
        uuid: {**meta, "conversation_count": project_conversation_counts.get(uuid, 0)}
        for uuid, meta in projects.items()
    }
    projects_index_path = out_path / "projects_index.json"
    projects_index_path.write_text(
        json.dumps(projects_index, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    memory_context_path = out_path / "memory_context.md"
    memory_context_path.write_text(memory_md, encoding="utf-8")

    dated = [c["created_at"] for c in conversations if c.get("created_at")]
    conversations_with_project = sum(1 for c in conversations if c.get("project_uuid"))
    if conversations and conversations_with_project == 0 and projects_index:
        notes.append(
            "conversations_with_project is 0 even though projects exist in this export -- "
            "this export schema doesn't carry a per-conversation project link. Reconstruct "
            "membership from memory_context.md's per-project sections and "
            "name/keyword similarity instead of treating these chats as unaffiliated."
        )

    stats = {
        "conversation_count": len(conversations),
        "date_range": {
            "earliest": min(dated) if dated else None,
            "latest": max(dated) if dated else None,
        },
        "project_count": len(projects_index),
        "conversations_with_project": conversations_with_project,
        "notes": notes,
    }
    stats_path = out_path / "stats.json"
    stats_path.write_text(json.dumps(stats, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    if stats["conversation_count"] < 10:
        stats["notes"].append(
            "Fewer than 10 conversations in this export -- thin data means low-confidence "
            "recommendations, not no output. Say so plainly before presenting either analysis."
        )

    return {
        "stats": stats,
        "paths": {
            "conversations": str(conversations_path),
            "projects_index": str(projects_index_path),
            "memory_context": str(memory_context_path),
            "stats": str(stats_path),
        },
    }
