"""Login + session validation shared by check/daily, with Slack alerting on failure.

Reuses a cached Sakai session cookie when still valid, falling back to a full
Playwright SSO login otherwise.
"""

from __future__ import annotations

import json

from . import config
from .auth import LoginError, login
from .notify import alert_login_failure
from .sakai import SakaiClient


def _load_cached_cookies() -> dict[str, str] | None:
    try:
        return json.loads(config.COOKIE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None


def _save_cached_cookies(cookies: dict[str, str]) -> None:
    try:
        config.COOKIE_PATH.parent.mkdir(parents=True, exist_ok=True)
        config.COOKIE_PATH.write_text(
            json.dumps(cookies), encoding="utf-8", newline="\n"
        )
    except Exception:
        pass  # caching is best-effort; a failure just means a login next run


def _valid_client(cookies: dict[str, str] | None) -> SakaiClient | None:
    if not cookies:
        return None
    client = SakaiClient(cookies)
    try:
        if client.current_user_eid():
            return client
    except Exception:
        pass
    client.close()
    return None


def _alert_best_effort(alert_webhook: str, kind: str, detail: str, dry_run: bool) -> None:
    """The alert must never mask the login failure itself (e.g. when the same
    network outage that broke the login also makes Slack unreachable)."""
    try:
        alert_login_failure(alert_webhook, kind, detail, dry_run)
    except Exception as e:
        try:
            print(f"::warning::Slack alert delivery failed: {e}")
        except Exception:
            pass  # even a broken stdout must not swallow the SystemExit below


def open_session(alert_webhook: str, dry_run: bool = False) -> SakaiClient:
    # 1) try the cached session — fast path, no browser
    client = _valid_client(_load_cached_cookies())
    if client is not None:
        return client

    # 2) full Playwright SSO login
    try:
        cookies = login(config.MS_EMAIL(), config.MS_PASSWORD(), config.MS_TOTP_SECRET())
    except LoginError as e:
        _alert_best_effort(alert_webhook, e.kind, str(e), dry_run)
        raise SystemExit(f"login failed ({e.kind}): {e}")

    client = SakaiClient(cookies)
    if not client.current_user_eid():
        client.close()
        _alert_best_effort(alert_webhook, "unknown", "session is anonymous after login", dry_run)
        raise SystemExit("login failed: anonymous session")
    _save_cached_cookies(cookies)
    return client
