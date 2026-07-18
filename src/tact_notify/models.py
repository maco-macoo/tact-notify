from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class Assignment:
    id: str
    site_id: str
    site_title: str
    title: str
    open_time: datetime | None
    due_time: datetime | None
    submitted: bool | None  # None = could not determine
    kind: str = "assignment"  # "assignment" | "quiz"


@dataclass
class Announcement:
    id: str
    site_id: str
    site_title: str
    title: str
    created: datetime | None
