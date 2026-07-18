"""`test` command: send sample messages to both channels so you can see the
exact notification format, without touching state or hitting TACT."""

from __future__ import annotations

from datetime import datetime, timedelta

from . import config
from .check import _announce_block, _task_block
from .daily import format_digest
from .models import Announcement, Assignment
from .notify import post


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
    text = "🧪 これはテスト送信です\n" + format_digest(pending, now)
    post(config.SLACK_WEBHOOK_DIGEST(), text, dry_run=dry_run)

    print("sent test messages to both channels")
