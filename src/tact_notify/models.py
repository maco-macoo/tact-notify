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

    def is_open(self, now: datetime) -> bool:
        """Visible to students (no openTime, or already past it)."""
        return self.open_time is None or self.open_time <= now

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "site_id": self.site_id,
            "site_title": self.site_title,
            "title": self.title,
            "open": self.open_time.isoformat() if self.open_time else None,
            "due": self.due_time.isoformat() if self.due_time else None,
            "submitted": self.submitted,
            "kind": self.kind,
        }

    @classmethod
    def from_dict(cls, d: dict) -> Assignment:
        return cls(
            id=str(d["id"]),
            site_id=str(d.get("site_id") or ""),
            site_title=str(d.get("site_title") or ""),
            title=str(d.get("title") or "(無題)"),
            open_time=datetime.fromisoformat(d["open"]) if d.get("open") else None,
            due_time=datetime.fromisoformat(d["due"]) if d.get("due") else None,
            submitted=d.get("submitted"),
            kind=str(d.get("kind") or "assignment"),
        )


@dataclass
class Announcement:
    id: str
    site_id: str
    site_title: str
    title: str
    created: datetime | None
