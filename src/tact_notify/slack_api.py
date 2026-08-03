"""Slack Web API (bot token) client for digest-channel commands (manage.py).

Optional feature: enabled only when SLACK_BOT_TOKEN and SLACK_CHANNEL_DIGEST
are both set. Uses httpx directly like notify.post — only conversations.history
/ chat.postMessage / auth.test are needed, not worth a slack_sdk dependency.
"""

from __future__ import annotations

import json

import httpx

from . import config

_API = "https://slack.com/api"


class SlackApiError(RuntimeError):
    """Slack returned ok:false (the message carries Slack's error string,
    e.g. missing_scope / not_in_channel / invalid_auth)."""


def enabled() -> bool:
    return bool(config.SLACK_BOT_TOKEN() and config.SLACK_CHANNEL_DIGEST())


def _call(method: str, params: dict) -> dict:
    transport = httpx.HTTPTransport(retries=2)
    headers = {"Authorization": f"Bearer {config.SLACK_BOT_TOKEN()}"}
    with httpx.Client(transport=transport, timeout=15, headers=headers) as client:
        resp = client.post(f"{_API}/{method}", data=params)
    data = resp.json()
    if not data.get("ok"):
        raise SlackApiError(f"{method}: {data.get('error', f'HTTP {resp.status_code}')}")
    return data


def fetch_messages(oldest: float, limit: int = 50) -> list[dict]:
    """Human messages in the digest channel newer than `oldest`, oldest first.
    Bot posts (incl. our own webhook digests) and join/topic events are dropped."""
    data = _call(
        "conversations.history",
        {
            "channel": config.SLACK_CHANNEL_DIGEST(),
            "oldest": f"{oldest:.6f}",
            "limit": limit,
        },
    )
    messages = [
        m for m in data.get("messages", [])
        if "bot_id" not in m and "subtype" not in m
    ]
    return list(reversed(messages))  # API returns newest-first


def post_message(text: str, dry_run: bool = False) -> None:
    if dry_run:
        print("--- DRY RUN Slack bot message ---")
        print(json.dumps({"text": text}, ensure_ascii=False, indent=2))
        return
    _call("chat.postMessage", {"channel": config.SLACK_CHANNEL_DIGEST(), "text": text})


def auth_check() -> str:
    """Validate the token; returns the bot's user name (for selftest)."""
    return _call("auth.test", {}).get("user", "?")
