"""httpx client for Sakai /direct JSON endpoints, with defensive parsing."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

import httpx

from .config import BASE_URL, JST, REQUEST_INTERVAL_SEC, USER_AGENT


class SakaiClient:
    def __init__(self, cookies: dict[str, str]):
        self._http = httpx.Client(
            base_url=BASE_URL,
            cookies=cookies,
            timeout=30,
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
        )
        self._last_request_at = 0.0

    def close(self) -> None:
        self._http.close()

    def get_json(self, path: str, **params: Any) -> Any:
        wait = REQUEST_INTERVAL_SEC - (time.monotonic() - self._last_request_at)
        if wait > 0:
            time.sleep(wait)
        resp = self._http.get(path, params=params or None)
        self._last_request_at = time.monotonic()
        resp.raise_for_status()
        return resp.json()

    def get_text(self, path: str, **params: Any) -> str:
        wait = REQUEST_INTERVAL_SEC - (time.monotonic() - self._last_request_at)
        if wait > 0:
            time.sleep(wait)
        resp = self._http.get(path, params=params or None)
        self._last_request_at = time.monotonic()
        resp.raise_for_status()
        return resp.text

    def current_user_eid(self) -> str:
        """Empty string means an anonymous session (login actually failed)."""
        data = self.get_json("/direct/session/current.json")
        return (data.get("userEid") or "").strip()


def parse_time(value: Any) -> datetime | None:
    """Accept Sakai time in epoch millis/seconds, {'time'|'epochSecond': ...} or ISO."""
    if value is None:
        return None
    if isinstance(value, dict):
        for key in ("epochSecond", "time"):
            if key in value:
                return parse_time(value[key])
        return None
    if isinstance(value, (int, float)):
        if value <= 0:
            return None
        seconds = value / 1000 if value > 1_000_000_000_000 else value
        return datetime.fromtimestamp(seconds, tz=timezone.utc).astimezone(JST)
    if isinstance(value, str):
        v = value.strip()
        if not v:
            return None
        if v.isdigit():
            return parse_time(int(v))
        try:
            return datetime.fromisoformat(v.replace("Z", "+00:00")).astimezone(JST)
        except ValueError:
            return None
    return None


def first_of(d: dict, *keys: str) -> Any:
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return None


def fetch_site_titles(client: SakaiClient, course_only: bool = True) -> dict[str, str]:
    """Site id -> title map. course_only keeps only regular lecture sites
    (type=="course"; the portal's「(不明な学期)」group), excluding project sites
    like 安全教育/研究科/学生支援 per the user's preference."""
    data = client.get_json("/direct/site.json", _limit=300)
    return {
        s["id"]: (s.get("title") or s["id"])
        for s in data.get("site_collection", [])
        if s.get("id") and (not course_only or s.get("type") == "course")
    }


def _submitted_of(a: dict) -> bool | None:
    """TACT's my.json contains only the current student's own submissions:
    an entry with submitted=True + a dateSubmitted means actually turned in
    (submitted=False would be an unsaved draft)."""
    subs = a.get("submissions")
    if not isinstance(subs, list):
        return None
    return any(
        s.get("submitted") is True
        and (s.get("dateSubmittedEpochSeconds") or s.get("dateSubmitted"))
        for s in subs
    )


def fetch_assignments(client: SakaiClient, site_titles: dict[str, str]):
    from .models import Assignment

    data = client.get_json("/direct/assignment/my.json")
    out: list[Assignment] = []
    for a in data.get("assignment_collection", []):
        aid = a.get("id")
        if not aid:
            continue
        sid = str(first_of(a, "context", "siteId") or "")
        if sid not in site_titles:  # not a target (lecture) site
            continue
        out.append(
            Assignment(
                id=str(aid),
                site_id=sid,
                site_title=site_titles.get(sid, sid),
                title=str(first_of(a, "title") or "(無題)"),
                open_time=parse_time(first_of(a, "openTime", "openDate")),
                due_time=parse_time(first_of(a, "dueTime", "dueDate", "closeTime")),
                submitted=_submitted_of(a),
            )
        )
    return out


def fetch_announcements(client: SakaiClient, site_titles: dict[str, str]):
    from .models import Announcement

    data = client.get_json("/direct/announcement/user.json", n=100, d=30)
    out: list[Announcement] = []
    for m in data.get("announcement_collection", []):
        mid = m.get("id")
        if not mid:
            continue
        sid = str(first_of(m, "siteId", "context") or "")
        if sid not in site_titles:  # not a target (lecture) site
            continue
        # the API's own siteTitle is often just "Home" — prefer the site map
        out.append(
            Announcement(
                id=str(mid),
                site_id=sid,
                site_title=site_titles.get(sid) or str(first_of(m, "siteTitle") or sid),
                title=str(first_of(m, "title") or "(無題)"),
                created=parse_time(first_of(m, "createdOn", "createdDate", "date")),
            )
        )
    return out


def fetch_quizzes(client: SakaiClient, site_titles: dict[str, str]):
    """Samigo published assessments, per site. Quiz deadlines are separate from
    assignments. sam_pub carries no usable per-student submission info (its
    submittedCount stays 0 even after the student submits), so submitted is
    left None here; daily.py resolves it from the tool page's 提出済みテスト
    list via fetch_submitted_quiz_titles()."""
    from .models import Assignment

    out: list[Assignment] = []
    for sid, stitle in site_titles.items():
        try:
            data = client.get_json(f"/direct/sam_pub/context/{sid}.json")
        except Exception:
            continue  # site without the quiz tool
        for q in data.get("sam_pub_collection", []):
            qid = q.get("publishedAssessmentId")
            if qid is None:
                continue
            out.append(
                Assignment(
                    id=f"quiz-{qid}",
                    site_id=sid,
                    site_title=stitle,
                    title=str(first_of(q, "title") or "(無題)"),
                    open_time=parse_time(first_of(q, "startDate")),
                    due_time=parse_time(first_of(q, "dueDate", "retractDate")),
                    submitted=None,
                    kind="quiz",
                )
            )
    return out


def _samigo_placement_id(client: SakaiClient, site_id: str) -> str | None:
    pages = client.get_json(f"/direct/site/{site_id}/pages.json")
    for page in pages if isinstance(pages, list) else []:
        for tool in page.get("tools", []):
            if tool.get("toolId") == "sakai.samigo" and tool.get("id"):
                return str(tool["id"])
    return None


def fetch_submitted_quizzes(client: SakaiClient, site_id: str) -> tuple[set[str], set[str]]:
    """(publishedAssessmentIds, titles) listed under 提出済みテスト on the
    site's Samigo tool page.

    A row's presence is the only reliable submitted signal: sam_pub has none,
    quizzes allowing resubmission stay in the テストを受験 list after taking,
    and a score may never appear (not every quiz is auto-graded) — so scores
    are deliberately ignored. A row carries a publishedId only while a
    feedback/review link is rendered; otherwise the title is plain text
    (verified on TACT), hence both keys are returned and the caller matches
    by id first, title as fallback. Returns empty sets when in doubt, which
    keeps the quiz in the digest (the safe direction)."""
    import re

    from bs4 import BeautifulSoup

    try:
        placement = _samigo_placement_id(client, site_id)
        if not placement:
            return set(), set()
        html = client.get_text(f"/portal/site/{site_id}/tool/{placement}")
    except Exception:
        return set(), set()
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", id=lambda i: i and "reviewTable" in i)
    if table is None:
        return set(), set()  # nothing submitted in this site yet
    ids: set[str] = set()
    titles: set[str] = set()
    for row in table.find_all("tr"):
        cell = row.find("td")
        if cell is None:  # header row
            continue
        ids.update(re.findall(r"'publishedId':'(\d+)'", str(row)))
        title = cell.get_text(strip=True)
        if title:
            titles.add(title)
    return ids, titles
