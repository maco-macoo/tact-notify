"""`test` command: send sample messages to both channels so you can see the
exact notification format, without touching state or hitting TACT."""

from __future__ import annotations

import time
from datetime import datetime, timedelta

from . import config, slack_api
from .check import _announce_block, _task_block
from .manage import format_pending_list
from .models import Announcement, Assignment
from .notify import post
from .slack_api import SlackApiError


def run(dry_run: bool = False) -> None:
    now = datetime.now(config.JST)

    sample_assignment = Assignment(
        id="test-a", site_id="x", site_title="サンプル講義A",
        title="【テスト】第7回課題", open_time=now,
        due_time=now + timedelta(days=6, hours=13), submitted=False, kind="assignment",
    )
    sample_quiz = Assignment(
        id="test-q", site_id="x", site_title="サンプル講義B",
        title="【テスト】中間確認テスト", open_time=now,
        due_time=now + timedelta(days=1, hours=8), submitted=None, kind="quiz",
    )
    sample_announce = Announcement(
        id="test-n", site_id="x", site_title="サンプル講義C",
        title="【テスト】第8回の教室変更のお知らせ", created=now,
    )

    # --- 課題・おしらせ チャンネル ---
    blocks = [
        {"type": "context", "elements": [
            {"type": "mrkdwn", "text": "🧪 これはテスト送信です(実際の課題ではありません)"}]},
        _task_block(sample_assignment),
        _task_block(sample_quiz),
        _announce_block(sample_announce),
    ]
    post(config.SLACK_WEBHOOK_NOTIFY(),
         "🧪 テスト送信: 新着通知のサンプル", blocks=blocks, dry_run=dry_run)

    # --- 課題一覧 チャンネル(締切が早い順) ---
    pending = sorted([sample_quiz, sample_assignment], key=lambda a: a.due_time)
    digest_text, _ = format_pending_list(pending, now)
    post(config.SLACK_WEBHOOK_DIGEST(), "🧪 これはテスト送信です\n" + digest_text, dry_run=dry_run)

    print("sent test messages to both channels")

    # --- コマンド管理ボットの疎通(有効時のみ) ---
    if not slack_api.enabled():
        print("slack bot: disabled (SLACK_BOT_TOKEN/SLACK_CHANNEL_DIGEST not set)")
        return
    if dry_run:
        slack_api.post_message("🧪 bot疎通テストOK(コマンド管理は有効です)", dry_run=True)
        print("slack bot: dry-run, skipped live auth/history checks")
        return
    try:
        name = slack_api.auth_check()
        slack_api.fetch_messages(oldest=time.time() - 60)  # verifies history scope + membership
        slack_api.post_message("🧪 bot疎通テストOK(コマンド管理は有効です)")
    except SlackApiError as e:
        raise SystemExit(
            f"slack bot NG: {e}\n"
            "→ missing_scope: OAuth & Permissions でスコープ追加後 Reinstall to Workspace\n"
            "→ not_in_channel: digestチャンネルに /invite @<bot名>\n"
            "→ invalid_auth / channel_not_found: SLACK_BOT_TOKEN / SLACK_CHANNEL_DIGEST の値を確認"
        )
    print(f"slack bot ok: @{name} (history readable, reply posted)")
