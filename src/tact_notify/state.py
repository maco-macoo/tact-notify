"""state/seen.json: ids we have already notified about.

Persisted across CI runs via the GitHub Actions cache (see notify.yml),
never committed to the repo. The cache is best-effort — the "notion" map
is only an optimization and is rebuilt from Notion itself after a loss
(query-first dedupe in notion_sync)."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .config import JST, STATE_PATH

_EMPTY = {
    "version": 1,
    "seeded_at": None,
    "assignments": {},
    "announcements": {},
    "quizzes": {},
    # tact_id -> {"page_id": str | None, "done": bool} (Notion sync cache)
    "notion": {},
}


def load() -> dict:
    if not STATE_PATH.exists():
        return json.loads(json.dumps(_EMPTY))
    data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    for key, default in _EMPTY.items():
        data.setdefault(key, json.loads(json.dumps(default)))
    return data


def write_json_atomic(path: Path, data: dict) -> None:
    """Write to a temp file then rename so a crash mid-write can't leave a
    truncated file behind (the Actions cache would then persist it)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    tmp.replace(path)


def save(state: dict) -> None:
    write_json_atomic(STATE_PATH, state)


def now_iso() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")
