"""Diagnostic: time every TACT endpoint the jobs depend on, per site, and
compare the aggregate /direct/assignment/my.json against the per-site
/direct/assignment/site/{id}.json it is built from (count + whether the
student's own `submissions` are included) — the data needed to decide
whether the heavy aggregate call can be replaced by targeted per-site calls.

Prints latencies, counts and key names only — never titles or payloads — so
the output is safe in public Actions logs. Touches no state."""

from __future__ import annotations

import time
from collections import Counter
from typing import Any, Callable

from . import config
from .sakai import SakaiClient, describe_error, first_of
from .session import open_session


def _timed(fn: Callable[[], Any]) -> tuple[Any, float, str | None]:
    t = time.perf_counter()
    try:
        return fn(), time.perf_counter() - t, None
    except Exception as e:  # noqa: BLE001 — a benchmark records failures, never stops on them
        return None, time.perf_counter() - t, describe_error(e)


def _items(data: Any, key: str) -> list[dict]:
    return data.get(key, []) if isinstance(data, dict) else []


def _has_submissions(items: list[dict]) -> str:
    """'own' when entries carry a submissions list (my.json style), 'none'
    when the key is absent/empty everywhere, else the count that do."""
    n = sum(1 for a in items if isinstance(a.get("submissions"), list) and a["submissions"])
    if not items:
        return "-"
    return "none" if n == 0 else ("all" if n == len(items) else f"{n}/{len(items)}")


def _mask(site_id: str) -> str:
    """n_2026_256_0123456 -> n_2026_256_####### : keeps the id format/term part
    (what a scheduler could key on) without naming the course."""
    parts = site_id.split("_")
    head = "_".join(parts[:2])
    tail = "_".join(parts[2:])
    return f"{head}_{tail[:1]}{'#' * max(len(tail) - 1, 0)}"


def _features(items: list[dict]) -> str:
    """Per-site shape hints for why a call might be slow (never content)."""
    status = Counter(str(a.get("status") or "?") for a in items)
    n_group = sum(1 for a in items if str(a.get("access") or "").upper() == "GROUP")
    n_peer = sum(1 for a in items if a.get("allowPeerAssessment"))
    n_att = sum(len(a.get("attachments") or []) for a in items)
    n_subs = sum(len(a.get("submissions") or []) for a in items)
    sub_att = sum(
        len(s.get("submittedAttachments") or []) + len(s.get("feedbackAttachments") or [])
        for a in items for s in (a.get("submissions") or [])
    )
    max_text = max((len(str(a.get("instructions") or "")) for a in items), default=0)
    return (
        f"status={dict(status)} group={n_group} peer={n_peer} attach={n_att} "
        f"subs={n_subs} subAttach={sub_att} maxInstr={max_text}"
    )


def _ownership(items: list[dict], user_id: str) -> str:
    """How many submissions name the current user as a submitter — tells
    whether a site returns only the student's own submissions or (grader
    view) everyone's, and whether submitters[].id matches session userId."""
    subs = [s for a in items for s in (a.get("submissions") or [])]
    if not subs:
        return "own=-"
    def ids(s: dict) -> set[str]:
        out = set()
        for x in s.get("submitters") or []:
            out.add(str(x.get("id") if isinstance(x, dict) else x))
        return out
    own = sum(1 for s in subs if user_id and user_id in ids(s))
    no_submitters = sum(1 for s in subs if not s.get("submitters"))
    return f"own={own}/{len(subs)} noSubmitters={no_submitters}"


def run() -> None:
    client = open_session(config.SLACK_WEBHOOK_NOTIFY(), dry_run=True)
    try:
        sess, dt, err = _timed(lambda: client.get_json("/direct/session/current.json", retry=False))
        user_id = str(sess.get("userId") or "") if isinstance(sess, dict) else ""
        print(f"session/current      {dt:6.1f}s  keys={sorted(sess.keys()) if isinstance(sess, dict) else err}")
        print(f"  userId set={bool(user_id)} len={len(user_id)}")

        sites, dt, err = _timed(lambda: client.get_json("/direct/site.json", retry=False, _limit=300))
        site_list = [s for s in _items(sites, "site_collection") if s.get("id")]
        print(f"site.json            {dt:6.1f}s  {len(site_list)} sites  {err or ''}")
        if site_list:
            print(f"  site keys: {sorted(site_list[0].keys())}")
            props = site_list[0].get("props") or {}
            print(f"  site props keys: {sorted(props.keys()) if isinstance(props, dict) else type(props).__name__}")

        my, dt, err = _timed(lambda: client.get_json("/direct/assignment/my.json", retry=False))
        my_items = _items(my, "assignment_collection")
        by_ctx = Counter(str(first_of(a, "context", "siteId") or "") for a in my_items)
        print(f"assignment/my.json   {dt:6.1f}s  {len(my_items)} items in {len(by_ctx)} sites  {err or ''}")
        print(f"  my.json {_features(my_items)}")
        if my_items:
            s0 = next((s for a in my_items for s in (a.get("submissions") or [])), None)
            if s0:
                print(f"  submission keys: {sorted(s0.keys())}")
                sub0 = (s0.get("submitters") or [None])[0]
                print(f"  submitter entry type={type(sub0).__name__} keys={sorted(sub0.keys()) if isinstance(sub0, dict) else '-'}")
            print(f"  my.json ownership: {_ownership(my_items, user_id)}")

        print()
        print("per-site /direct/assignment/site/{id}.json")
        timings: list[tuple[float, str]] = []
        for s in site_list:
            sid = s["id"]
            props = s.get("props") if isinstance(s.get("props"), dict) else {}
            term = props.get("term_eid") or props.get("term") or "-"
            data, dt, err = _timed(lambda: client.get_json(f"/direct/assignment/site/{sid}.json", retry=False))
            items = _items(data, "assignment_collection")
            timings.append((dt, sid))
            print(
                f"{_mask(sid):14} {str(s.get('type') or '-')[:7]:7} term={term!s:6} "
                f"created={str(s.get('createdDate') or s.get('createdTime') or '-')[:10]} "
                f"{dt:6.1f}s n={len(items):3} my={by_ctx.get(sid, 0):2}  {_features(items)}  {_ownership(items, user_id)}"
                + (f"  ERR {err}" if err else "")
            )
        print()
        for dt, sid in sorted(timings, reverse=True)[:2]:
            _, dt2, err = _timed(lambda: client.get_json(f"/direct/assignment/site/{sid}.json", retry=False))
            print(f"re-fetch slowest {_mask(sid)}: first {dt:.1f}s -> again {dt2:.1f}s {err or ''}")
    finally:
        client.close()
