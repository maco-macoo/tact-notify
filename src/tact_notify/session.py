"""Login + session validation shared by check/daily, with Slack alerting on failure.

Reuses a cached Sakai session cookie when still valid, falling back to a full
Playwright SSO login otherwise.

Login health is tracked across runs in state/login_health.json (persisted via
the encrypted Actions cache, like seen.json):

- Transient failures (kind "timeout"/"unknown") are counted. The 10-minute
  check loop tolerates the first one as a soft-fail (warning + exit 0) because
  the next run self-heals a one-off network blip; only consecutive failures
  alert and fail the run. Deterministic failures (bad credentials, MFA
  challenge) always alert immediately.
- An alert that could not be delivered (the outage that broke the login often
  breaks Slack from the same runner too — see run #1117/#1121) is queued and
  re-sent at the start of a later run, so real outages are never silently lost.
- Once login works again, a recovery notice is posted if an outage alert had
  been delivered, and the health file is removed.
"""

from __future__ import annotations

import json
from typing import NoReturn

from . import config
from .auth import LoginError, login
from .notify import alert_login_failure, post
from .sakai import SakaiClient
from .state import now_iso, write_json_atomic

# Failure kinds a retry on a later run can plausibly fix (network/site outage),
# as opposed to deterministic ones where retrying just hammers Microsoft.
TRANSIENT_KINDS = ("timeout", "unknown")
# Consecutive transient failures before a tolerant caller alerts and goes red.
ALERT_AFTER = 2


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


def _load_health() -> dict:
    try:
        return json.loads(config.LOGIN_HEALTH_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_health(health: dict) -> bool:
    """Returns whether the health file was actually persisted. Callers that
    grant leniency based on recorded state must not do so on a failed save."""
    try:
        if health:
            write_json_atomic(config.LOGIN_HEALTH_PATH, health)
        else:
            config.LOGIN_HEALTH_PATH.unlink(missing_ok=True)
        return True
    except Exception:
        return False  # best-effort; never mask the real outcome


def _print_best_effort(msg: str) -> None:
    try:
        print(msg)
    except Exception:
        pass  # even a broken stdout must not change the control flow


def _alert_best_effort(alert_webhook: str, kind: str, detail: str, dry_run: bool) -> bool:
    """Returns whether the alert was delivered. The alert must never mask the
    login failure itself (e.g. when the same network outage that broke the
    login also makes Slack unreachable)."""
    try:
        alert_login_failure(alert_webhook, kind, detail, dry_run)
        return True
    except Exception as e:
        _print_best_effort(f"::warning::Slack alert delivery failed: {e}")
        return False


def _flush_pending_alert(alert_webhook: str, dry_run: bool, health: dict) -> None:
    """Re-send an alert an earlier run could not deliver. Keeps it queued if
    Slack is still unreachable."""
    pending = health.get("pending_alert")
    if not pending:
        return
    text = (
        f"🚨 TACTログイン失敗(遅延通知: {pending.get('at', '?')} 時点で"
        f"Slackに送信できなかったアラートです)\n"
        f"種別: {pending.get('kind', '?')}\n{pending.get('detail', '')}"
    )
    try:
        post(alert_webhook, text, dry_run=dry_run)
    except Exception as e:
        _print_best_effort(f"::warning::pending alert delivery failed again: {e}")
        return
    if dry_run:
        return  # a dry run must not consume the queued alert
    health.pop("pending_alert", None)
    health["alerted"] = True
    _save_health(health)


def _fail(
    kind: str, detail: str, alert_webhook: str, dry_run: bool, tolerate_transient: bool
) -> NoReturn:
    health = _load_health()
    if kind in TRANSIENT_KINDS:
        streak = int(health.get("streak", 0)) + 1
        health["streak"] = streak
        health["last_failure"] = {"kind": kind, "at": now_iso()}
        if tolerate_transient and streak < ALERT_AFTER:
            # Grant the grace only if the streak was actually persisted — with a
            # broken state dir every run would see streak=1 and soft-fail
            # forever, so fall through to the normal alert + failure instead.
            if dry_run or _save_health(health):
                _print_best_effort(
                    f"::warning::login failed ({kind}); transient failure "
                    f"{streak}/{ALERT_AFTER}, leaving the retry to the next run: {detail}"
                )
                raise SystemExit(0)
    delivered = _alert_best_effort(alert_webhook, kind, detail, dry_run)
    if not dry_run:  # a dry run must not record alerts it never really sent
        if delivered:
            health["alerted"] = True
        elif "pending_alert" not in health:  # keep the oldest one — it dates the outage
            health["pending_alert"] = {"kind": kind, "detail": detail, "at": now_iso()}
        _save_health(health)
    raise SystemExit(f"login failed ({kind}): {detail}")


def _note_success(alert_webhook: str, dry_run: bool) -> None:
    health = _load_health()
    if not health:
        return
    health.pop("streak", None)
    health.pop("last_failure", None)
    if health.get("alerted"):
        try:
            post(alert_webhook, "✅ TACTログイン復旧: ログインが再び成功しました。", dry_run=dry_run)
            health.pop("alerted", None)
        except Exception as e:  # keep the flag so the next run retries the notice
            _print_best_effort(f"::warning::recovery notice delivery failed: {e}")
    if not dry_run:  # a dry run must not clear real incident state
        _save_health(health)


def open_session(
    alert_webhook: str, dry_run: bool = False, tolerate_transient: bool = False
) -> SakaiClient:
    # 0) deliver any alert a previous run failed to get out
    health = _load_health()
    if health.get("pending_alert"):
        _flush_pending_alert(alert_webhook, dry_run, health)

    # 1) try the cached session — fast path, no browser
    client = _valid_client(_load_cached_cookies())
    if client is not None:
        _note_success(alert_webhook, dry_run)
        return client

    # 2) full Playwright SSO login
    try:
        cookies = login(config.MS_EMAIL(), config.MS_PASSWORD(), config.MS_TOTP_SECRET())
    except LoginError as e:
        _fail(e.kind, str(e), alert_webhook, dry_run, tolerate_transient)

    client = SakaiClient(cookies)
    if not client.current_user_eid():
        client.close()
        _fail("unknown", "session is anonymous after login", alert_webhook, dry_run, tolerate_transient)
    _save_cached_cookies(cookies)
    _note_success(alert_webhook, dry_run)
    return client
