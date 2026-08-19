from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


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
