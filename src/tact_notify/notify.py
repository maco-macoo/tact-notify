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
        if os.environ.get("GITHUB_ACTIONS") == "true":
            # public repo: Actions logs are world-readable, so never echo course
            # or item titles there — just enough to see what would have gone out
            first = text.splitlines()[0] if text else ""
            print(f"--- DRY RUN Slack post: {first[:40]!r} ({len(text)} chars, {len(blocks or [])} blocks) ---")
        else:
            print("--- DRY RUN Slack payload ---")
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    # retries= covers connection establishment only, so a delivered-but-slow
    # request is never re-sent (no duplicate messages)
    transport = httpx.HTTPTransport(retries=2)
    with httpx.Client(transport=transport, timeout=15) as client:
        resp = client.post(webhook_url, json=payload)
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


# Where a run failed against TACT: logging in, or fetching data afterwards.
STAGE_LABEL = {"login": "TACTログイン失敗", "fetch": "TACTデータ取得失敗"}

_HINTS = {
    ("login", "credentials"): "メールアドレスまたはパスワードが拒否されました。.env / GitHub Secrets を確認してください。",
    ("login", "challenge"): "追加認証(MFA)を要求されました。MS_TOTP_SECRET の設定を確認してください。",
    ("login", "timeout"): "ログインフローが完了しませんでした(TACT/ネットワークの一時的な不調、ページ構成変更、IPブロックの可能性)。",
    ("fetch", "timeout"): "TACTの応答が間に合いませんでした(サーバの高負荷・一時的な不調の可能性)。",
    ("fetch", "unknown"): "TACTとの通信に失敗しました(ネットワークの不調・サーバエラーの可能性)。",
}


def alert_failure(
    webhook_url: str, kind: str, detail: str, dry_run: bool = False, stage: str = "login"
) -> None:
    hint = _HINTS.get((stage, kind), "原因不明のエラーです。" if kind == "unknown" else "")
    link = run_link()
    text = (
        f"🚨 {STAGE_LABEL.get(stage, 'TACTアクセス失敗')} ({kind})\n{hint}\n{detail}"
        + (f"\n実行ログ: {link}" if link else "")
    )
    post(webhook_url, text, dry_run=dry_run)
