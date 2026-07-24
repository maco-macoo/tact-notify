"""Daily 07:00 JST job: list unsubmitted assignments whose deadline has not passed."""

from __future__ import annotations

from datetime import datetime

from . import config, notion, state
from .notify import days_left_label, fmt_dt, post
from .notion_sync import sync_done
from .sakai import (
    fetch_assignments,
    fetch_quizzes,
    fetch_site_titles,
    mark_submitted_quizzes,
)
from .session import open_session


def run(dry_run: bool = False) -> None:
    webhook = config.SLACK_WEBHOOK_DIGEST()
    # login failures alert to the notify channel (the one the user watches for events)
    client = open_session(config.SLACK_WEBHOOK_NOTIFY(), dry_run)
    try:
        titles = fetch_site_titles(client)
        assignments = fetch_assignments(client, titles)
        quizzes = fetch_quizzes(client, titles)
        now = datetime.now(config.JST)
        mark_submitted_quizzes(client, quizzes, now)
    finally:
        client.close()

    open_quizzes = [
        q for q in quizzes if q.open_time is None or q.open_time <= now
    ]
    pending = sorted(
        (
            a
            for a in assignments + open_quizzes
            if a.due_time is not None and a.due_time > now and a.submitted is not True
        ),
        key=lambda a: a.due_time,  # 締切が早い順
    )

    post(webhook, format_digest(pending, now), dry_run=dry_run)
    print(f"pending: {len(pending)}")

    # nightly safety net: flip Notion cards to 完了 for anything the scoped
    # 10-min check sweep may have missed
    if notion.enabled():
        try:
            st = state.load()
            nc = notion.open_client(dry_run)
            try:
                marked = sync_done(nc, assignments + open_quizzes, st)
            finally:
                nc.close()
            if not dry_run:
                state.save(st)
            print(f"notion: marked done {marked}")
        except Exception as e:  # Notion must never break the digest
            print(f"notion: sync skipped due to error: {e}")


def format_digest(pending: list, now: datetime) -> str:
    """未提出・締切前の一覧テキスト(締切が早い順で渡すこと)。"""
    if not pending:
        return "未提出の課題はありません 🎉"
    lines = []
    for a in pending:
        left = days_left_label(a.due_time, now)
        prefix = "⚠️ " if left == "今日締切" else ""
        kind = "📝" if a.kind == "quiz" else ""
        lines.append(f"• {prefix}{fmt_dt(a.due_time)}({left}) — {kind}[{a.site_title}] {a.title}")
    header = f"📅 未提出の課題({now.month}/{now.day}時点・{len(pending)}件)"
    return header + "\n" + "\n".join(lines)
