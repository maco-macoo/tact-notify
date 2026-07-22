"""Notion sync shared by check and daily.

State (st["notion"]) is only a cache: whenever an id is missing from it we
query Notion by TACT ID before creating, so a lost Actions cache rebuilds
the mapping instead of duplicating pages. Failures are logged per item and
never propagate — Notion being down must not affect Slack or state saving.
"""

from __future__ import annotations

from datetime import datetime

from .models import Assignment
from .notion import NotionClient, NotionError

MAX_NOTION_CREATES_PER_RUN = 20  # runaway guard, like check.MAX_ITEMS_PER_RUN


def pending_of(assignments: list, quizzes: list, now: datetime) -> list[Assignment]:
    """Unsubmitted, already-open, due-in-the-future tasks (the daily-digest
    filter, shared so first-run seeding matches what the digest shows)."""
    open_quizzes = [q for q in quizzes if q.open_time is None or q.open_time <= now]
    return sorted(
        (
            a
            for a in assignments + open_quizzes
            if a.due_time is not None and a.due_time > now and a.submitted is not True
        ),
        key=lambda a: a.due_time,
    )


def sync_new(nc: NotionClient, tasks: list[Assignment], st: dict) -> int:
    """Create Notion pages for tasks not yet tracked. Query-first whenever the
    id is unknown to state (first run, cache loss, or partial loss) — creation
    happens only after Notion confirms the page is absent. Returns #created."""
    created = 0
    for a in tasks:
        if a.id in st["notion"]:
            continue
        if created >= MAX_NOTION_CREATES_PER_RUN:
            print(f"notion: create cap ({MAX_NOTION_CREATES_PER_RUN}) reached, rest deferred to next run")
            break
        try:
            existing = nc.find_page_by_tact_id(a.id)
            if existing is not None:
                st["notion"][a.id] = existing
                continue
            page_id = nc.create_task_page(a)
            if page_id is not None:  # None = dry-run
                st["notion"][a.id] = {"page_id": page_id, "done": False}
            created += 1
        except NotionError as e:
            print(f"notion: failed to sync new item {a.id}: {e}")
    return created


def sync_done(nc: NotionClient, tasks: list[Assignment], st: dict) -> int:
    """Flip ステータス=完了 for submitted tasks whose page is not done yet.
    Never writes anything but 完了, and only once per page — a user-set
    manual status is left alone until actual submission. Returns #marked."""
    marked = 0
    for a in tasks:
        if a.submitted is not True:
            continue
        entry = st["notion"].get(a.id)
        if entry is None:
            # page may exist without state knowing it (state loss, or seeded
            # from another environment): resolve from Notion once. A missing
            # page is recorded as done so we never re-query it.
            try:
                entry = nc.find_page_by_tact_id(a.id) or {"page_id": None, "done": True}
            except NotionError as e:
                print(f"notion: failed to look up {a.id}: {e}")
                continue
            st["notion"][a.id] = entry
        if entry.get("done") or not entry.get("page_id"):
            continue
        try:
            nc.mark_done(entry["page_id"])
            entry["done"] = True
            marked += 1
        except NotionError as e:
            print(f"notion: failed to mark done {a.id}: {e}")
    return marked
