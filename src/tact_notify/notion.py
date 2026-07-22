"""Notion REST API client (httpx, no SDK) for the「TACT課題」database.

API version is pinned to 2025-09-03: pages are created against a
data_source_id parent and queried via /v1/data_sources/{id}/query.
Do not mix in the older database_id-style calls — the two shapes are
mutually exclusive per Notion-Version and fail with opaque validation
errors when crossed.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Any

import httpx

from . import config
from .models import Assignment

NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2025-09-03"
# Notion allows ~3 req/s; stay under it the same way SakaiClient paces itself.
REQUEST_INTERVAL_SEC = 0.4

# Property names must match the「TACT課題」database character-for-character
# (including the space in "TACT ID"). Renaming them in the Notion UI breaks
# the integration.
PROP_TITLE = "名前"
PROP_COURSE = "講義"
PROP_DUE = "締切"
PROP_KIND = "種類"
PROP_STATUS = "ステータス"
PROP_TACT_ID = "TACT ID"
PROP_URL = "URL"

STATUS_TODO = "未着手"
STATUS_DONE = "完了"
KIND_LABEL = {"assignment": "課題", "quiz": "クイズ"}


class NotionError(RuntimeError):
    pass


def enabled() -> bool:
    return bool(config.NOTION_TOKEN() and config.NOTION_DS_ID())


class NotionClient:
    def __init__(self, token: str, data_source_id: str, dry_run: bool = False):
        self._ds_id = data_source_id
        self._dry_run = dry_run
        self._http = httpx.Client(
            base_url=NOTION_API,
            timeout=30,
            headers={
                "Authorization": f"Bearer {token}",
                "Notion-Version": NOTION_VERSION,
                "Content-Type": "application/json",
            },
        )
        self._last_request_at = 0.0

    def close(self) -> None:
        self._http.close()

    def _request(self, method: str, path: str, json: dict | None = None) -> dict:
        wait = REQUEST_INTERVAL_SEC - (time.monotonic() - self._last_request_at)
        if wait > 0:
            time.sleep(wait)
        try:
            resp = self._http.request(method, path, json=json)
        except httpx.HTTPError as e:
            raise NotionError(f"{method} {path}: {e}") from e
        finally:
            self._last_request_at = time.monotonic()
        if resp.status_code >= 400:
            raise NotionError(f"{method} {path}: HTTP {resp.status_code}: {resp.text[:300]}")
        return resp.json()

    def whoami(self) -> str:
        """Bot user name, to verify the token works."""
        data = self._request("GET", "/users/me")
        return str(data.get("name") or data.get("id") or "?")

    def find_page_by_tact_id(self, tact_id: str) -> dict | None:
        """{"page_id", "done"} for the page whose TACT ID equals tact_id,
        or None when absent. Raises NotionError on failure (caller decides —
        creation must be skipped that run, never done blind)."""
        data = self._request(
            "POST",
            f"/data_sources/{self._ds_id}/query",
            json={
                "filter": {"property": PROP_TACT_ID, "rich_text": {"equals": tact_id}},
                "page_size": 1,
            },
        )
        results = data.get("results") or []
        if not results:
            return None
        page = results[0]
        status = (
            ((page.get("properties") or {}).get(PROP_STATUS) or {}).get("select") or {}
        ).get("name")
        return {"page_id": page["id"], "done": status == STATUS_DONE}

    def create_task_page(self, a: Assignment) -> str | None:
        """Create a task page; returns page_id (None in dry_run)."""
        props: dict[str, Any] = {
            PROP_TITLE: {"title": [{"text": {"content": a.title[:200]}}]},
            # select option names cannot contain commas
            PROP_COURSE: {"select": {"name": a.site_title.replace(",", " ")[:100]}},
            PROP_KIND: {"select": {"name": KIND_LABEL.get(a.kind, a.kind)}},
            PROP_STATUS: {"select": {"name": STATUS_TODO}},
            PROP_TACT_ID: {"rich_text": [{"text": {"content": a.id}}]},
            PROP_URL: {"url": f"{config.BASE_URL}/portal/site/{a.site_id}"},
        }
        if a.due_time is not None:
            props[PROP_DUE] = {"date": {"start": a.due_time.isoformat()}}
        payload = {
            "parent": {"type": "data_source_id", "data_source_id": self._ds_id},
            "properties": props,
        }
        if self._dry_run:
            print(f"[dry-run] notion create: {payload}")
            return None
        data = self._request("POST", "/pages", json=payload)
        return str(data["id"])

    def mark_done(self, page_id: str) -> None:
        """Set ステータス=完了. A 404 (page deleted by the user) counts as done —
        we must not recreate or retry a page the user chose to remove."""
        if self._dry_run:
            print(f"[dry-run] notion mark done: {page_id}")
            return
        try:
            self._request(
                "PATCH",
                f"/pages/{page_id}",
                json={"properties": {PROP_STATUS: {"select": {"name": STATUS_DONE}}}},
            )
        except NotionError as e:
            if "HTTP 404" in str(e):
                return
            raise


def open_client(dry_run: bool = False) -> NotionClient:
    return NotionClient(config.NOTION_TOKEN(), config.NOTION_DS_ID(), dry_run=dry_run)


def run_test(dry_run: bool = False) -> None:
    """`python -m tact_notify notion-test`: verify token, data source, page
    create and mark-done end to end. TACT is not touched at all."""
    if not enabled():
        raise SystemExit("notion: NOTION_TOKEN / NOTION_DS_ID が未設定です (.env / GitHub Secrets を確認)")
    nc = open_client(dry_run)
    try:
        print(f"1/4 トークンOK (bot: {nc.whoami()})")
        existing = nc.find_page_by_tact_id("notion-test")
        print(f"2/4 データソースOK (既存テストページ: {existing['page_id'] if existing else 'なし'})")
        a = Assignment(
            id="notion-test",
            site_id="notion-test",
            site_title="接続確認",
            title="【テスト】tact-notify 接続確認",
            open_time=None,
            due_time=datetime.now(config.JST) + timedelta(days=1),
            submitted=None,
        )
        if existing:
            page_id: str | None = existing["page_id"]
            print("3/4 作成スキップ (テストページが既に存在)")
        else:
            page_id = nc.create_task_page(a)
            print(f"3/4 ページ作成OK ({page_id if page_id else 'dry-run'})")
        if page_id:
            nc.mark_done(page_id)
            print("4/4 完了化OK")
            print(f"ページ: https://www.notion.so/{page_id.replace('-', '')}")
            print("確認できたら、このテストページはNotion上で削除してかまいません。")
    finally:
        nc.close()
