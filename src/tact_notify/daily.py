"""Daily 07:00 JST job: list unsubmitted assignments whose deadline has not passed."""

from __future__ import annotations

from datetime import datetime

from . import config, manage, notion, state
from .notify import post
from .notion_sync import pending_of, sync_done
from .sakai import (
    fetch_assignments,
    fetch_quizzes,
    fetch_site_titles,
    mark_submitted_quizzes,
)
from .session import open_session


def run(dry_run: bool = False) -> None:
    webhook = config.SLACK_WEBHOOK_DIGEST()
    st = state.load()
    # login failures alert to the notify channel (the one the user watches for events)
    client = open_session(config.SLACK_WEBHOOK_NOTIFY(), dry_run)
    try:
        titles = fetch_site_titles(client, course_only=False)
        assignments = fetch_assignments(client, titles)
        quizzes = fetch_quizzes(client, titles)
        now = datetime.now(config.JST)
        mark_submitted_quizzes(client, quizzes, now)
    finally:
        client.close()

    pending = pending_of(assignments, quizzes, now)
    manage.purge_hidden(st, assignments + quizzes, now)
    visible = [a for a in pending if a.id not in st["hidden"]]

    text, num_map = manage.format_pending_list(
        visible, now, hidden_count=len(pending) - len(visible)
    )
    post(webhook, text, dry_run=dry_run)
    if manage.enabled():
        manage.record_map(st, "pending_map", num_map)
    print(f"pending: {len(visible)} shown, {len(pending) - len(visible)} hidden")

    # nightly safety net: flip Notion cards to 完了 for anything the scoped
    # 10-min check sweep may have missed
    if notion.enabled():
        try:
            nc = notion.open_client(dry_run)
            try:
                marked = sync_done(nc, assignments + quizzes, st)
            finally:
                nc.close()
            print(f"notion: marked done {marked}")
        except Exception as e:  # Notion must never break the digest
            print(f"notion: sync skipped due to error: {e}")

    if not dry_run:
        state.save(st)
