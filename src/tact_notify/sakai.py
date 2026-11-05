"""httpx client for Sakai /direct JSON endpoints, with defensive parsing."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

import httpx

from .config import (
    BASE_URL,
    CONNECT_TIMEOUT_SEC,
    JST,
    READ_TIMEOUT_SEC,
    REQUEST_INTERVAL_SEC,
    RETRY_ATTEMPTS,
    RETRY_BACKOFF_SEC,
    USER_AGENT,
)


def transient_kind(e: BaseException) -> str | None:
    """Failure kind ("timeout" / "unknown", matching session.TRANSIENT_KINDS)
    for errors a retry or a later run can plausibly fix — timeouts, dropped
    connections, 5xx — or None for deterministic ones (4xx, malformed JSON)
    that would only repeat."""
    if isinstance(e, httpx.TimeoutException):
        return "timeout"
    if isinstance(e, httpx.TransportError):
        return "unknown"
    if isinstance(e, httpx.HTTPStatusError) and e.response.status_code >= 500:
        return "unknown"
    return None


def describe_error(e: BaseException) -> str:
    """'ReadTimeout: The read operation timed out (GET /direct/...)' — the
    request path matters for the Slack alert, httpx's own message omits it."""
    first_line = (str(e).splitlines() or [""])[0]  # HTTPStatusError adds an MDN link line
    text = f"{type(e).__name__}: {first_line}"
    try:
        request = e.request  # type: ignore[attr-defined]
    except (AttributeError, RuntimeError):  # not an httpx error / .request never set
        return text
    return f"{text} ({request.method} {request.url.path})"


class SakaiClient:
    def __init__(self, cookies: dict[str, str]):
        self._http = httpx.Client(
            base_url=BASE_URL,
            cookies=cookies,
            timeout=httpx.Timeout(CONNECT_TIMEOUT_SEC, read=READ_TIMEOUT_SEC),
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
        )
        self._last_request_at = 0.0

    def close(self) -> None:
        self._http.close()

    def _get(self, path: str, params: dict[str, Any], retry: bool) -> httpx.Response:
        """Paced GET, retried on transient failures (see transient_kind). GETs
        are idempotent, so a retry can never duplicate a notification."""
        attempts = RETRY_ATTEMPTS if retry else 1
        for attempt in range(1, attempts + 1):
            wait = REQUEST_INTERVAL_SEC - (time.monotonic() - self._last_request_at)
            if wait > 0:
                time.sleep(wait)
            try:
                resp = self._http.get(path, params=params or None)
                resp.raise_for_status()
                return resp
            except Exception as e:
                if attempt >= attempts or transient_kind(e) is None:
                    raise
                backoff = RETRY_BACKOFF_SEC[min(attempt, len(RETRY_BACKOFF_SEC)) - 1]
                print(
                    f"::warning::{describe_error(e)}; "
                    f"retrying ({attempt}/{attempts - 1}) in {backoff}s"
                )
                time.sleep(backoff)
            finally:
                self._last_request_at = time.monotonic()
        raise AssertionError("unreachable")  # the loop always returns or raises

    def get_json(self, path: str, retry: bool = True, **params: Any) -> Any:
        """retry=False for probes where a failure is an expected answer (a
        site without the tool) rather than something worth waiting on."""
        return self._get(path, params, retry).json()

    def get_text(self, path: str, retry: bool = True, **params: Any) -> str:
        return self._get(path, params, retry).text

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
    like 安全教育/研究科/学生支援. All callers now pass course_only=False —
    unwanted items are hidden per-item via Slack commands instead (manage.py)."""
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
            data = client.get_json(f"/direct/sam_pub/context/{sid}.json", retry=False)
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


def mark_submitted_quizzes(
    client: SakaiClient,
    quizzes: list,
    now: datetime,
    only_sites: set[str] | None = None,
) -> None:
    """Flip submitted=True for quizzes listed under 提出済みテスト on the
    Samigo tool page. Matched by publishedId when the row exposes one, by
    title within the site otherwise (published titles are unique per site).
    Only sites with a quiz that is unsubmitted and still open are scraped;
    only_sites narrows further (check passes the sites whose Notion card is
    still open, so the steady-state 10-min run scrapes nothing)."""
    sites = {
        q.site_id
        for q in quizzes
        if q.due_time is not None and q.due_time > now and q.submitted is not True
    }
    if only_sites is not None:
        sites &= only_sites
    for sid in sites:
        ids, titles = fetch_submitted_quizzes(client, sid)
        submitted_ids = {f"quiz-{i}" for i in ids}
        for q in quizzes:
            if q.site_id != sid:
                continue
            if q.id in submitted_ids or q.title.strip() in titles:
                q.submitted = True


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
