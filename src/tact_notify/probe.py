"""Dev-only: log in, hit candidate endpoints, dump raw JSON to probe_out/.

Never dumps full payloads to stdout in CI (GITHUB_ACTIONS) — local use only.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from . import config
from .auth import login
from .sakai import SakaiClient

OUT_DIR = config.REPO_ROOT / "probe_out"


def _dump(name: str, data) -> None:
    OUT_DIR.mkdir(exist_ok=True)
    (OUT_DIR / f"{name}.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _summary(name: str, data) -> str:
    if isinstance(data, dict):
        coll_key = next((k for k in data if k.endswith("_collection")), None)
        if coll_key:
            items = data[coll_key]
            keys = sorted(items[0].keys()) if items else []
            return f"{name}: {len(items)} items; first-item keys: {keys}"
        return f"{name}: dict keys {sorted(data.keys())}"
    return f"{name}: {type(data).__name__}"


def run() -> None:
    print("== login ==", flush=True)
    cookies = login(config.MS_EMAIL(), config.MS_PASSWORD(), config.MS_TOTP_SECRET())
    client = SakaiClient(cookies)
    try:
        eid = client.current_user_eid()
        if not eid:
            raise SystemExit("PROBE FAIL: session is anonymous — login did not stick")
        print(f"login OK (userEid={eid})")

        targets: list[tuple[str, str, dict]] = [
            ("session_current", "/direct/session/current.json", {}),
            ("assignment_my", "/direct/assignment/my.json", {}),
            ("announcement_user", "/direct/announcement/user.json", {"n": 50, "d": 30}),
            ("site", "/direct/site.json", {"_limit": 300}),
        ]
        results: dict[str, object] = {}
        for name, path, params in targets:
            try:
                data = client.get_json(path, **params)
                results[name] = data
                _dump(name, data)
                print(_summary(name, data))
            except Exception as e:
                print(f"{name}: FAILED ({e})")

        # Per-site candidates (quizzes, resources, calendar) using first few sites
        site_ids: list[str] = []
        site_data = results.get("site")
        if isinstance(site_data, dict):
            site_ids = [
                s.get("id")
                for s in site_data.get("site_collection", [])
                if s.get("id") and s.get("type") in ("course", "project", None)
            ][:3]
        for sid in site_ids:
            for name, path in [
                (f"sam_pub_{sid[:8]}", f"/direct/sam_pub/context/{sid}.json"),
                (f"content_{sid[:8]}", f"/direct/content/site/{sid}.json"),
                (f"calendar_{sid[:8]}", f"/direct/calendar/site/{sid}.json"),
            ]:
                try:
                    data = client.get_json(path)
                    _dump(name, data)
                    print(_summary(name, data))
                except Exception as e:
                    print(f"{name}: FAILED ({e})")

        if os.environ.get("GITHUB_ACTIONS") != "true":
            print(f"\nraw dumps in {OUT_DIR}")
    finally:
        client.close()
