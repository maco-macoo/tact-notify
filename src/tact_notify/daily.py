"""Daily 07:00 JST job: list unsubmitted assignments whose deadline has not passed."""

from __future__ import annotations

from datetime import datetime

from . import config
from .notify import days_left_label, fmt_dt, post
from .sakai import (
    fetch_assignments,
    fetch_quizzes,
    fetch_site_titles,
    fetch_submitted_quiz_titles,
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
        _mark_submitted_quizzes(client, quizzes, now)
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


def _mark_submitted_quizzes(client, quizzes: list, now: datetime) -> None:
    """Flip submitted=True for quizzes listed under 提出済みテスト on the
    Samigo tool page (matched by title within the site). Only sites with a
    quiz that could reach the digest are scraped, to keep requests down."""
    sites = {
        q.site_id
        for q in quizzes
        if q.due_time is not None and q.due_time > now and q.submitted is not True
    }
    for sid in sites:
        submitted_titles = fetch_submitted_quiz_titles(client, sid)
        if not submitted_titles:
            continue
        for q in quizzes:
            if q.site_id == sid and q.title.strip() in submitted_titles:
                q.submitted = True


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
