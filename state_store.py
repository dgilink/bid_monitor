from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


class SentBidState:
    def __init__(self, path: Path = Path("state") / "sent_bids.json") -> None:
        self.path = path
        self.sent_bid_ids: set[str] = set()
        self.changed = False
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return

        bid_ids = data.get("sent_bid_ids") if isinstance(data, dict) else None
        if isinstance(bid_ids, list):
            self.sent_bid_ids = {str(bid_id) for bid_id in bid_ids if str(bid_id).strip()}

    def has(self, bid_id: str) -> bool:
        return bid_id in self.sent_bid_ids

    def add(self, bid_id: str) -> None:
        if bid_id in self.sent_bid_ids:
            return
        self.sent_bid_ids.add(bid_id)
        self.changed = True

    def save(self) -> None:
        if not self.changed and self.path.exists():
            return
        self.path.parent.mkdir(exist_ok=True)
        data: dict[str, Any] = {
            "sent_bid_ids": sorted(self.sent_bid_ids),
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
