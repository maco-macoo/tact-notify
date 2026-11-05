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


def run() -> None:
    client = open_session(config.SLACK_WEBHOOK_NOTIFY(), dry_run=True)
    total = 0.0
    try:
        sites, dt, err = _timed(lambda: client.get_json("/direct/site.json", retry=False, _limit=300))
        total += dt
        site_list = [s for s in _items(sites, "site_collection") if s.get("id")]
        print(f"site.json            {dt:6.1f}s  {len(site_list)} sites  {err or ''}")

        my, dt, err = _timed(lambda: client.get_json("/direct/assignment/my.json", retry=False))
        total += dt
        my_items = _items(my, "assignment_collection")
        by_ctx = Counter(str(first_of(a, "context", "siteId") or "") for a in my_items)
        print(
            f"assignment/my.json   {dt:6.1f}s  {len(my_items)} items in {len(by_ctx)} sites, "
            f"submissions={_has_submissions(my_items)}  {err or ''}"
        )
        if my_items:
            print(f"  my.json item keys: {sorted(my_items[0].keys())}")

        ann, dt, err = _timed(lambda: client.get_json("/direct/announcement/user.json", retry=False, n=100, d=30))
        total += dt
        print(f"announcement/user    {dt:6.1f}s  {len(_items(ann, 'announcement_collection'))} items  {err or ''}")

        print()
        print(f"{'site':10} {'type':8} {'asg/site':>9} {'n':>3} {'my':>3} {'subs':>5} {'sam_pub':>8} {'n':>3}")
        per_site_total = 0.0
        sam_total = 0.0
        shown_keys = False
        for s in site_list:
            sid = s["id"]
            data, dt, err = _timed(lambda: client.get_json(f"/direct/assignment/site/{sid}.json", retry=False))
            per_site_total += dt
            items = _items(data, "assignment_collection")
            if items and not shown_keys:
                print(f"  assignment/site item keys: {sorted(items[0].keys())}")
                shown_keys = True
            sam, sdt, serr = _timed(lambda: client.get_json(f"/direct/sam_pub/context/{sid}.json", retry=False))
            sam_total += sdt
            print(
                f"{sid[:10]:10} {str(s.get('type') or '-')[:8]:8} {dt:8.1f}s {len(items):3} "
                f"{by_ctx.get(sid, 0):3} {_has_submissions(items):>5} {sdt:7.1f}s "
                f"{len(_items(sam, 'sam_pub_collection')):3}"
                + (f"  asg: {err}" if err else "")
                + (f"  sam: {serr.split(' (')[0]}" if serr else "")
            )
        print()
        print(f"per-site assignment total {per_site_total:6.1f}s over {len(site_list)} sites "
              f"(+{config.REQUEST_INTERVAL_SEC}s pacing each)")
        print(f"per-site sam_pub total     {sam_total:6.1f}s")
        print(f"user-level calls total     {total:6.1f}s")
    finally:
        client.close()
