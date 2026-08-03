"""Every-30-min job: notify newly published assignments/quizzes/announcements."""

from __future__ import annotations

from datetime import datetime

from . import config, manage, notion, state
from .notify import fmt_dt, post
from .notion_sync import pending_of, sync_done, sync_new
from .sakai import (
    fetch_announcements,
    fetch_assignments,
    fetch_quizzes,
    fetch_site_titles,
    mark_submitted_quizzes,
)
from .session import open_session

MAX_ITEMS_PER_RUN = 15  # spam guard if state is ever lost

_EMOJI = {"assignment": "✏️", "quiz": "📝"}
_LABEL = {"assignment": "新しい課題", "quiz": "新しい小テスト"}


def _task_block(a) -> dict:
    return {
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": (
                f"{_EMOJI[a.kind]} *{_LABEL[a.kind]}*\n*[{a.site_title}] {a.title}*\n"
                f"公開: {fmt_dt(a.open_time)}　締切: {fmt_dt(a.due_time)}"
            ),
        },
    }


def _announce_block(n) -> dict:
    return {
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": f"📢 *新しいお知らせ*\n*[{n.site_title}] {n.title}*",
        },
    }


def _notion_sync(tasks_to_create, all_tasks, st, dry_run: bool) -> None:
    """Create pages for tasks_to_create, mark submitted ones done. Never lets
    a Notion failure escape into the Slack/state path."""
    if not notion.enabled():
        print("notion: disabled (NOTION_TOKEN/NOTION_DS_ID not set)")
        return
    try:
        nc = notion.open_client(dry_run)
        try:
            created = sync_new(nc, tasks_to_create, st)
            marked = sync_done(nc, all_tasks, st)
        finally:
            nc.close()
        print(f"notion: created {created}, marked done {marked}")
    except Exception as e:  # defensive: Notion must never break notifications
        print(f"notion: sync skipped due to error: {e}")


def run(dry_run: bool = False) -> None:
    webhook = config.SLACK_WEBHOOK_NOTIFY()
    st = state.load()  # before the session: the quiz sweep below needs both

    # digest-channel commands (hide/show/...) — read before the session so the
    # quiz sweep below knows whether an accurate full refresh is needed. Wrapped
    # like _notion_sync: command handling must never break notifications.
    cmds, cmds_newest_ts = [], None
    if manage.enabled():
        try:
            cmds, cmds_newest_ts = manage.read_commands(st)
        except Exception as e:
            print(f"manage: reading commands skipped due to error: {e}")

    # check runs every 10 min, so a one-off network blip self-heals on the next
    # run — tolerate the first transient login failure instead of going red
    client = open_session(webhook, dry_run, tolerate_transient=True)
    try:
        titles = fetch_site_titles(client, course_only=False)
        assignments = fetch_assignments(client, titles)
        quizzes = fetch_quizzes(client, titles)
        announcements = fetch_announcements(client, titles)
        now = datetime.now(config.JST)
        if cmds:
            # commands post a refreshed pending list — needs accurate submitted
            # flags everywhere (commands are rare, the full sweep is fine)
            mark_submitted_quizzes(client, quizzes, now)
        elif notion.enabled():
            # scrape only sites that still have an open (not done) quiz card,
            # so the steady-state 10-min run adds no Samigo requests
            open_quiz_sites = {
                q.site_id
                for q in quizzes
                if st["notion"].get(q.id, {}).get("done") is not True
            }
            mark_submitted_quizzes(client, quizzes, now, only_sites=open_quiz_sites)
    finally:
        client.close()

    first_run = not st["assignments"] and not st["quizzes"] and not st["announcements"]
    # one-time migration: the fetch scope used to be course sites only. The
    # first widened run sees every project-site item as "new" — record them
    # without notifying (like first_run seeding) instead of flooding the channel.
    migrate = not first_run and st.get("scope") != "all"
    st["scope"] = "all"
    new_assignments = [a for a in assignments if a.id not in st["assignments"]]
    new_quizzes = [q for q in quizzes if q.id not in st["quizzes"]]
    new_announcements = [n for n in announcements if n.id not in st["announcements"]]

    stamp = state.now_iso()
    for a in assignments:
        st["assignments"].setdefault(a.id, stamp)
    for q in quizzes:
        st["quizzes"].setdefault(q.id, stamp)
    for n in announcements:
        st["announcements"].setdefault(n.id, stamp)

    if first_run:
        st["seeded_at"] = stamp
        # seed Notion with what the daily digest would show (pending tasks),
        # so the dashboard is populated from day one / after state loss
        seed = [a for a in pending_of(assignments, quizzes, now) if a.id not in st["hidden"]]
        _notion_sync(seed, assignments + quizzes, st, dry_run)
        if not dry_run:
            state.save(st)
        post(
            webhook,
            f"🔧 初期化完了: 課題{len(assignments)}件・小テスト{len(quizzes)}件・"
            f"お知らせ{len(announcements)}件を記録しました。以後は新着のみ通知します。",
            dry_run=dry_run,
        )
        print(f"seeded: {len(assignments)} assignments, {len(quizzes)} quizzes, "
              f"{len(announcements)} announcements")
        return

    new_tasks = new_assignments + new_quizzes
    total_new = len(new_tasks) + len(new_announcements)
    if migrate:
        post(
            webhook,
            f"🔧 対象を全サイトに拡大: 課題{len(new_assignments)}件・"
            f"小テスト{len(new_quizzes)}件・お知らせ{len(new_announcements)}件を"
            "通知なしで記録しました。",
            dry_run=dry_run,
        )
    elif total_new:
        blocks: list[dict] = []
        for a in new_tasks[:MAX_ITEMS_PER_RUN]:
            blocks.append(_task_block(a))
        remaining = MAX_ITEMS_PER_RUN - len(blocks)
        for n in new_announcements[: max(remaining, 0)]:
            blocks.append(_announce_block(n))
        shown = len(blocks)
        if total_new > shown:
            blocks.append(
                {
                    "type": "context",
                    "elements": [{"type": "mrkdwn", "text": f"ほか {total_new - shown} 件"}],
                }
            )
        fallback = (
            f"新着: 課題{len(new_assignments)}件・小テスト{len(new_quizzes)}件・"
            f"お知らせ{len(new_announcements)}件"
        )
        post(webhook, fallback, blocks=blocks, dry_run=dry_run)

    # Also retry pending tasks that never got a Notion page (an earlier failed
    # sync must not become a permanent gap). Steady state: every synced id is in
    # st["notion"], so this list is empty and adds no Notion calls. Items hidden
    # via Slack commands are excluded from creation (existing pages untouched).
    pending = [a for a in pending_of(assignments, quizzes, now) if a.id not in st["hidden"]]
    if migrate:
        # new_tasks is full of old project-site items here — creating pages for
        # all of them would flood Notion. Seed only what the digest shows
        # (sync_new skips ids already tracked).
        to_create = pending
    else:
        unsynced = [
            a for a in pending
            if a.id not in st["notion"] and all(a.id != t.id for t in new_tasks)
        ]
        to_create = [t for t in new_tasks if t.id not in st["hidden"]] + unsynced
    _notion_sync(to_create, assignments + quizzes, st, dry_run)

    if manage.enabled():
        try:
            manage.apply_commands(cmds, cmds_newest_ts, st, assignments, quizzes, now, dry_run)
        except Exception as e:  # defensive: commands must never break notifications
            print(f"manage: command processing skipped due to error: {e}")

    if not dry_run:
        state.save(st)
    print(f"new: {len(new_assignments)} assignments, {len(new_quizzes)} quizzes, "
          f"{len(new_announcements)} announcements")
