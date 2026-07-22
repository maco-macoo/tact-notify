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
ARTIFACTS_DIR = REPO_ROOT / "artifacts"
# Cached Sakai session cookies (gitignored). Reused across runs to skip the slow
# Playwright SSO login while the session is still valid — also cuts how often we
# hit Microsoft, lowering risk-detection from frequent datacenter-IP logins.
COOKIE_PATH = REPO_ROOT / ".runtime" / "cookies.json"

# Politeness: identify ourselves and pace requests (university asks to avoid load).
USER_AGENT = "tact-notify/0.1 (student personal notification tool)"
REQUEST_INTERVAL_SEC = 0.5

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
