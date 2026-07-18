"""Slack Incoming Webhook posting and Japanese date formatting."""

from __future__ import annotations

import json
import os
from datetime import datetime

import httpx

from .config import JST

_WEEKDAYS_JA = ["月", "火", "水", "木", "金", "土", "日"]


def fmt_dt(dt: datetime | None) -> str:
    if dt is None:
        return "未設定"
    dt = dt.astimezone(JST)
    year = f"{dt.year}/" if dt.year != datetime.now(JST).year else ""
    return f"{year}{dt.month}/{dt.day}({_WEEKDAYS_JA[dt.weekday()]}) {dt:%H:%M}"


def days_left_label(due: datetime, now: datetime) -> str:
    days = (due.astimezone(JST).date() - now.astimezone(JST).date()).days
    return "今日締切" if days <= 0 else f"あと{days}日"


def post(webhook_url: str, text: str, blocks: list | None = None, dry_run: bool = False) -> None:
    payload: dict = {"text": text}
    if blocks:
        payload["blocks"] = blocks
    if dry_run:
        print("--- DRY RUN Slack payload ---")
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    resp = httpx.post(webhook_url, json=payload, timeout=15)
    if resp.status_code != 200 or resp.text != "ok":
        raise RuntimeError(f"Slack webhook failed: {resp.status_code} {resp.text[:200]}")


def run_link() -> str:
    """Link to the current GitHub Actions run, when running in CI."""
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return ""
    return (
        f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/"
        f"{os.environ.get('GITHUB_REPOSITORY', '')}/actions/runs/"
        f"{os.environ.get('GITHUB_RUN_ID', '')}"
    )


def alert_login_failure(webhook_url: str, kind: str, detail: str, dry_run: bool = False) -> None:
    hints = {
        "credentials": "メールアドレスまたはパスワードが拒否されました。.env / GitHub Secrets を確認してください。",
        "challenge": "追加認証(MFA)を要求されました。MS_TOTP_SECRET の設定を確認してください。",
        "timeout": "ログインフローが完了しませんでした(ページ構成変更やIPブロックの可能性)。",
        "unknown": "原因不明のエラーです。",
    }
    link = run_link()
    text = (
        f"🚨 TACTログイン失敗 ({kind})\n{hints.get(kind, '')}\n{detail}"
        + (f"\n実行ログ: {link}" if link else "")
    )
    post(webhook_url, text, dry_run=dry_run)
