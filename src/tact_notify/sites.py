"""Per-site assignment fetching with a latency-aware schedule.

Why not /direct/assignment/my.json: it is the server-side sum of every
per-site call, so one expensive site (a grader-view site whose 8 assignments
carry ~1,400 other people's submissions: 25s per call on TACT) makes the
whole aggregate slow and a single timeout loses everything. Fetching
/direct/assignment/site/{id}.json per site gives the same fields (including
the student's own submissions) and lets each site be scheduled, cached and
fail on its own.

Schedule (check runs every 10 min; daily/manage pass full=True):
- cheap sites (EWMA latency <= SLOW_SITE_SEC) are fetched every run;
- expensive sites are refreshed once per SLOW_SITE_INTERVAL_SEC, otherwise
  their last snapshot is reused — a not-yet-open assignment is already in the
  snapshot with its openTime, so its "new" notification still fires on time;
- a site whose fetch fails transiently keeps its snapshot and is not retried
  before SLOW_SITE_INTERVAL_SEC has passed (its latency estimate is left
  alone, so one blip does not demote a cheap site for hours); a 4xx (no
  Assignments tool / lost access) yields an empty list. Only when every
  attempted site fails does the run itself fail (fetch_guard treats that as
  an outage).

Quizzes (sam_pub per site) are cheap but there is one call per site; a site
without the Tests & Quizzes tool is detected once (pages.json) and skipped
until the next full run, which re-checks every site.

Snapshots live in state["sites"] next to the notified-id store, so a cache
loss just means one full fetch.
"""

from __future__ import annotations

import time
from datetime import datetime

import httpx

from .config import LATENCY_EWMA, SLOW_SITE_INTERVAL_SEC, SLOW_SITE_SEC
from .models import Assignment
from .sakai import (
    SakaiClient,
    describe_error,
    fetch_site_assignments,
    fetch_site_quizzes,
    has_samigo_tool,
    transient_kind,
)


def _due(entry: dict | None, now: datetime) -> bool:
    if not entry:
        return True
    if entry.get("retry_after") and now < datetime.fromisoformat(entry["retry_after"]):
        return False
    if not entry.get("fetched_at") or float(entry.get("latency") or 0) <= SLOW_SITE_SEC:
        return True
    fetched_at = datetime.fromisoformat(entry["fetched_at"])
    return (now - fetched_at).total_seconds() >= SLOW_SITE_INTERVAL_SEC


def _snapshot(entry: dict | None, sid: str, title: str) -> list[Assignment]:
    items = []
    for d in (entry or {}).get("assignments") or []:
        a = Assignment.from_dict(d)
        a.site_id, a.site_title = sid, title  # titles may have been renamed
        items.append(a)
    return items


def fetch_assignments(
    client: SakaiClient,
    site_titles: dict[str, str],
    st: dict,
    now: datetime,
    full: bool = False,
) -> list[Assignment]:
    """Assignments of every site in site_titles: freshly fetched where the
    schedule says so (or full=True), from the state snapshot otherwise."""
    store: dict = st.setdefault("sites", {})
    for sid in list(store):  # dropped from site.json: no longer a member
        if sid not in site_titles:
            del store[sid]

    out: list[Assignment] = []
    attempted = failed = 0
    spent = spent_slow = 0.0
    last_error: Exception | None = None
    for sid, title in site_titles.items():
        entry = store.get(sid)
        if not (full or _due(entry, now)):
            out.extend(_snapshot(entry, sid, title))
            continue
        attempted += 1
        started = time.monotonic()
        try:
            items = fetch_site_assignments(client, sid, title)
        except httpx.HTTPStatusError as e:
            if transient_kind(e) is not None:
                failed, last_error = failed + 1, e
                out.extend(_defer(store, sid, title, e, now))
                continue
            print(f"::warning::assignments of site {sid}: {describe_error(e)}; treating as none")
            items = []
        except Exception as e:
            if transient_kind(e) is None:
                raise  # not a network condition: a bug, surface it
            failed, last_error = failed + 1, e
            out.extend(_defer(store, sid, title, e, now))
            continue
        latency = time.monotonic() - started
        spent += latency
        if latency > SLOW_SITE_SEC:
            spent_slow += latency
        prev = float((entry or {}).get("latency") or latency)
        store[sid] = {
            "fetched_at": now.isoformat(timespec="seconds"),
            "latency": round(LATENCY_EWMA * latency + (1 - LATENCY_EWMA) * prev, 2),
            "retry_after": None,
            "assignments": [a.to_dict() for a in items],
        }
        out.extend(items)

    if attempted and failed == attempted and last_error is not None:
        raise last_error  # every site failed: an outage, not a slow site
    slow = sorted(s for s, e in store.items() if float(e.get("latency") or 0) > SLOW_SITE_SEC)
    print(
        f"sites: {attempted - failed} fetched in {spent:.1f}s (of which expensive sites "
        f"{spent_slow:.1f}s), {failed} failed (snapshot kept), "
        f"{len(site_titles) - attempted} from snapshot; expensive (hourly): {len(slow)}"
    )
    return out


def _defer(store: dict, sid: str, title: str, e: Exception, now: datetime) -> list[Assignment]:
    """A transient failure: keep the snapshot and leave the site alone for
    one interval, so the next runs do not wait on it again."""
    print(f"::warning::assignments of site {sid}: {describe_error(e)}; using snapshot")
    entry = store.setdefault(sid, {"fetched_at": None, "latency": None, "assignments": []})
    retry_after = now.timestamp() + SLOW_SITE_INTERVAL_SEC
    entry["retry_after"] = datetime.fromtimestamp(retry_after, tz=now.tzinfo).isoformat(timespec="seconds")
    return _snapshot(entry, sid, title)


def fetch_quizzes(
    client: SakaiClient,
    site_titles: dict[str, str],
    st: dict,
    now: datetime,
    full: bool = False,
) -> list[Assignment]:
    """Quizzes of every site, skipping sites known to lack the quiz tool
    (learned once via pages.json; full=True re-checks all of them)."""
    store: dict = st.setdefault("sites", {})
    out: list[Assignment] = []
    skipped = checked = 0
    for sid, title in site_titles.items():
        entry = store.setdefault(sid, {"fetched_at": None, "latency": None, "assignments": []})
        if not full and entry.get("samigo") is False:
            skipped += 1
            continue
        items = fetch_site_quizzes(client, sid, title)
        if items:
            entry["samigo"] = True
        elif full or entry.get("samigo") is None:
            entry["samigo"] = has_samigo_tool(client, sid)  # None: unknown, re-check
            checked += 1
        out.extend(items)
    print(f"quizzes: {len(site_titles) - skipped} sites polled, {skipped} skipped (no quiz tool), {checked} tool checks")
    return out
