"""Environment/config loading shared by all commands."""

from __future__ import annotations

import os
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

BASE_URL = "https://tact.ac.thers.ac.jp"
PORTAL_URL = f"{BASE_URL}/portal"
JST = ZoneInfo("Asia/Tokyo")

REPO_ROOT = Path(__file__).resolve().parents[2]
STATE_PATH = REPO_ROOT / "state" / "seen.json"
# Consecutive-login-failure tracking + undelivered alerts (see session.py).
# Lives under state/ so the encrypted Actions cache persists it across runs.
LOGIN_HEALTH_PATH = REPO_ROOT / "state" / "login_health.json"
ARTIFACTS_DIR = REPO_ROOT / "artifacts"
# Cached Sakai session cookies (gitignored). Reused across runs to skip the slow
# Playwright SSO login while the session is still valid — also cuts how often we
# hit Microsoft, lowering risk-detection from frequent datacenter-IP logins.
COOKIE_PATH = REPO_ROOT / ".runtime" / "cookies.json"

# Politeness: identify ourselves and pace requests (university asks to avoid load).
USER_AGENT = "tact-notify/0.1 (student personal notification tool)"
REQUEST_INTERVAL_SEC = 0.5
# /direct/assignment/my.json walks every site the user belongs to and has been
# taking 30s+ on TACT's side since 2026-11-02 (no client change; every check
# run timed out at the old 30s limit). Reads therefore get a generous budget,
# and transient failures (timeouts, dropped connections, 5xx) are retried a few
# times with a short backoff before a run gives up.
CONNECT_TIMEOUT_SEC = 10
READ_TIMEOUT_SEC = 90
RETRY_ATTEMPTS = 3  # total attempts per request
RETRY_BACKOFF_SEC = (3, 6)  # pause before the 2nd / 3rd attempt

load_dotenv(REPO_ROOT / ".env")


def env(name: str, required: bool = True) -> str:
    value = os.environ.get(name, "").strip()
    if required and not value:
        raise SystemExit(f"environment variable {name} is not set (check .env or GitHub Secrets)")
    return value


MS_EMAIL = lambda: env("MS_EMAIL")  # noqa: E731
MS_TOTP_SECRET = lambda: env("MS_TOTP_SECRET", required=False)  # noqa: E731
MS_PASSWORD = lambda: env("MS_PASSWORD")  # noqa: E731
SLACK_WEBHOOK_NOTIFY = lambda: env("SLACK_WEBHOOK_NOTIFY")  # noqa: E731
SLACK_WEBHOOK_DIGEST = lambda: env("SLACK_WEBHOOK_DIGEST")  # noqa: E731
# Notion連携(任意): 未設定ならNotion同期はスキップされ、従来のSlack通知のみ動く
NOTION_TOKEN = lambda: env("NOTION_TOKEN", required=False)  # noqa: E731
NOTION_DS_ID = lambda: env("NOTION_DS_ID", required=False)  # noqa: E731
# Slackコマンド管理(任意): digestチャンネルの hide/show コマンドで非表示を管理。
# 未設定なら従来どおり(コマンド読み取りも返信もしない)。
SLACK_BOT_TOKEN = lambda: env("SLACK_BOT_TOKEN", required=False)  # noqa: E731
SLACK_CHANNEL_DIGEST = lambda: env("SLACK_CHANNEL_DIGEST", required=False)  # noqa: E731
