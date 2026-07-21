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


def _dump_text(name: str, text: str) -> None:
    OUT_DIR.mkdir(exist_ok=True)
    (OUT_DIR / name).write_text(text, encoding="utf-8")


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

        # Samigo tool page per course site: the "提出済みテスト" (submitted
        # assessments) list is only visible in the tool HTML, not via /direct.
        course_ids: list[str] = []
        if isinstance(site_data, dict):
            course_ids = [
                s.get("id")
                for s in site_data.get("site_collection", [])
                if s.get("id") and s.get("type") == "course"
            ]
        for sid in course_ids:
            short = sid  # full site id — short prefixes collide across sites
            try:
                data = client.get_json(f"/direct/sam_pub/context/{sid}.json")
                _dump(f"sam_pub_{short}", data)
            except Exception as e:
                print(f"sam_pub_{short}: FAILED ({e})")
            try:
                pages = client.get_json(f"/direct/site/{sid}/pages.json")
                _dump(f"pages_{short}", pages)
            except Exception as e:
                print(f"pages_{short}: FAILED ({e})")
                continue
            placement = None
            for page in pages if isinstance(pages, list) else []:
                for tool in page.get("tools", []):
                    if tool.get("toolId") == "sakai.samigo":
                        placement = tool.get("id")
            if not placement:
                print(f"samigo_{short}: no samigo tool")
                continue
            try:
                html = client.get_text(f"/portal/site/{sid}/tool/{placement}")
                _dump_text(f"samigo_{short}.html", html)
                print(f"samigo_{short}: {len(html)} chars"
                      f"{' (has 提出済みテスト)' if '提出済みテスト' in html else ''}")
            except Exception as e:
                print(f"samigo_{short}: FAILED ({e})")

        if os.environ.get("GITHUB_ACTIONS") != "true":
            print(f"\nraw dumps in {OUT_DIR}")
    finally:
        client.close()
