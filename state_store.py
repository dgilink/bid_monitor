from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


KST = ZoneInfo("Asia/Seoul")


def kst_date(now: datetime | None = None) -> str:
    current = now or datetime.now(tz=KST)
    if current.tzinfo is None:
        current = current.replace(tzinfo=KST)
    return current.astimezone(KST).date().isoformat()


class SentBidState:
    # Persistent Telegram delivery state with legacy migration.
    def __init__(self, path: Path = Path("state") / "sent_bids.json") -> None:
        self.path = path
        self.bids: dict[str, dict[str, Any]] = {}
        self.changed = False
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(data, dict):
            return

        current = data.get("bids")
        if isinstance(current, dict):
            for bid_id, value in current.items():
                if not str(bid_id).strip():
                    continue
                if isinstance(value, dict):
                    self.bids[str(bid_id)] = {
                        "last_sent_hash": value.get("last_sent_hash"),
                        "sent_at": value.get("sent_at"),
                    }
            return

        legacy = data.get("sent_bid_ids")
        if isinstance(legacy, list):
            for bid_id in legacy:
                bid_id = str(bid_id).strip()
                if bid_id:
                    self.bids[bid_id] = {
                        "last_sent_hash": None,
                        "sent_at": None,
                    }

    def notification_kind(self, bid_id: str, content_hash: str) -> str:
        item = self.bids.get(bid_id)
        if item is None:
            return "new"

        previous_hash = item.get("last_sent_hash")
        if not previous_hash:
            return "legacy"
        if str(previous_hash) == content_hash:
            return "same"
        return "changed"

    def set_baseline(self, bid_id: str, content_hash: str) -> None:
        item = self.bids.setdefault(bid_id, {})
        if item.get("last_sent_hash") == content_hash:
            return
        item["last_sent_hash"] = content_hash
        item.setdefault("sent_at", None)
        self.changed = True

    def mark_sent(self, bid_id: str, content_hash: str) -> None:
        self.bids[bid_id] = {
            "last_sent_hash": content_hash,
            "sent_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        }
        self.changed = True

    def save(self) -> None:
        if not self.changed and self.path.exists():
            return

        self.path.parent.mkdir(exist_ok=True)
        data: dict[str, Any] = {
            "bids": dict(sorted(self.bids.items())),
            "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        }

        temp = self.path.with_suffix(self.path.suffix + ".tmp")
        temp.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temp.replace(self.path)


class OperationalNotificationState:
    """Persistent daily alert gates and core API incident state."""

    def __init__(self, path: Path = Path("state") / "notification_health.json") -> None:
        self.path = path
        self.last_failure_alert_date: str | None = None
        self.last_heartbeat_date: str | None = None
        self.last_recovery_date: str | None = None
        self.incident_active = False
        self.last_core_error: str | None = None
        self.last_failure_at: str | None = None
        self.last_recovered_at: str | None = None
        self.changed = False
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(data, dict):
            return
        self.last_failure_alert_date = _optional_string(data.get("last_failure_alert_date"))
        self.last_heartbeat_date = _optional_string(data.get("last_heartbeat_date"))
        self.last_recovery_date = _optional_string(data.get("last_recovery_date"))
        self.incident_active = data.get("incident_active") is True
        self.last_core_error = _optional_string(data.get("last_core_error"))
        self.last_failure_at = _optional_string(data.get("last_failure_at"))
        self.last_recovered_at = _optional_string(data.get("last_recovered_at"))

    def failure_alert_due(self, now: datetime | None = None) -> bool:
        return self.last_failure_alert_date != kst_date(now)

    def heartbeat_due(self, now: datetime | None = None) -> bool:
        return self.last_heartbeat_date != kst_date(now)

    def recovery_alert_due(self, now: datetime | None = None) -> bool:
        return self.incident_active and self.last_recovery_date != kst_date(now)

    def record_core_failure(self, error_summary: str, now: datetime | None = None) -> None:
        current = _as_kst(now)
        self.incident_active = True
        self.last_core_error = error_summary
        self.last_failure_at = current.isoformat(timespec="seconds")
        self.changed = True

    def mark_failure_alert_sent(self, now: datetime | None = None) -> None:
        self.last_failure_alert_date = kst_date(now)
        self.changed = True

    def mark_heartbeat_sent(self, now: datetime | None = None) -> None:
        self.last_heartbeat_date = kst_date(now)
        self.changed = True

    def mark_recovered(self, now: datetime | None = None, *, alert_sent: bool) -> None:
        current = _as_kst(now)
        self.incident_active = False
        self.last_recovered_at = current.isoformat(timespec="seconds")
        if alert_sent:
            self.last_recovery_date = current.date().isoformat()
        self.changed = True

    def save(self) -> None:
        if not self.changed and self.path.exists():
            return
        self.path.parent.mkdir(exist_ok=True)
        payload: dict[str, Any] = {
            "last_failure_alert_date": self.last_failure_alert_date,
            "last_heartbeat_date": self.last_heartbeat_date,
            "last_recovery_date": self.last_recovery_date,
            "incident_active": self.incident_active,
            "last_core_error": self.last_core_error,
            "last_failure_at": self.last_failure_at,
            "last_recovered_at": self.last_recovered_at,
            "updated_at": datetime.now(tz=KST).isoformat(timespec="seconds"),
        }
        temp = self.path.with_suffix(self.path.suffix + ".tmp")
        temp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temp.replace(self.path)


def _as_kst(now: datetime | None) -> datetime:
    current = now or datetime.now(tz=KST)
    if current.tzinfo is None:
        current = current.replace(tzinfo=KST)
    return current.astimezone(KST)


def _optional_string(value: Any) -> str | None:
    return str(value) if value not in (None, "") else None
