from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any


SCHEMA = """
CREATE TABLE IF NOT EXISTS bids (
    bid_id TEXT PRIMARY KEY,
    bid_no TEXT NOT NULL,
    bid_ord TEXT NOT NULL,
    title TEXT,
    notice_org TEXT,
    demand_org TEXT,
    business_type TEXT,
    contract_method TEXT,
    bid_method TEXT,
    notice_date TEXT,
    close_date TEXT,
    open_date TEXT,
    estimated_price INTEGER,
    budget_amount INTEGER,
    base_amount INTEGER,
    duration_text TEXT,
    estimated_dev_days INTEGER,
    region_limit TEXT,
    license_limit TEXT,
    qualification_summary TEXT,
    attachment_urls TEXT,
    detail_url TEXT,
    matched_keywords TEXT,
    risk_keywords TEXT,
    solo_score INTEGER,
    grade TEXT,
    comment TEXT,
    raw_json TEXT,
    content_hash TEXT,
    notified_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    fetched_count INTEGER DEFAULT 0,
    matched_count INTEGER DEFAULT 0,
    notified_count INTEGER DEFAULT 0,
    c_count INTEGER DEFAULT 0,
    error TEXT
);
"""


class BidStorage:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def begin_run(self) -> int:
        cur = self.conn.execute("INSERT INTO runs(started_at) VALUES (?)", (datetime.now().isoformat(timespec="seconds"),))
        self.conn.commit()
        return int(cur.lastrowid)

    def finish_run(self, run_id: int, **values: Any) -> None:
        columns = ["finished_at = ?"]
        params: list[Any] = [datetime.now().isoformat(timespec="seconds")]
        for key, value in values.items():
            columns.append(f"{key} = ?")
            params.append(value)
        params.append(run_id)
        self.conn.execute(f"UPDATE runs SET {', '.join(columns)} WHERE id = ?", params)
        self.conn.commit()

    def get(self, bid_id: str) -> sqlite3.Row | None:
        cur = self.conn.execute("SELECT * FROM bids WHERE bid_id = ?", (bid_id,))
        return cur.fetchone()

    def upsert_bid(self, row: dict[str, Any]) -> tuple[bool, bool]:
        existing = self.get(row["bid_id"])
        now = datetime.now().isoformat(timespec="seconds")
        row.setdefault("created_at", now)
        row["updated_at"] = now
        row = {k: _serialize(v) for k, v in row.items()}

        if existing:
            changed = existing["content_hash"] != row.get("content_hash")
            original_created = existing["created_at"]
            row["created_at"] = original_created
            assignments = ", ".join(f"{k}=:{k}" for k in row.keys() if k != "bid_id")
            self.conn.execute(f"UPDATE bids SET {assignments} WHERE bid_id=:bid_id", row)
            self.conn.commit()
            return False, changed

        columns = ", ".join(row.keys())
        placeholders = ", ".join(f":{k}" for k in row.keys())
        self.conn.execute(f"INSERT INTO bids ({columns}) VALUES ({placeholders})", row)
        self.conn.commit()
        return True, False

    def mark_notified(self, bid_id: str) -> None:
        self.conn.execute("UPDATE bids SET notified_at = ? WHERE bid_id = ?", (datetime.now().isoformat(timespec="seconds"), bid_id))
        self.conn.commit()

    def list_recent_matched(self, limit: int = 50) -> list[dict[str, Any]]:
        cur = self.conn.execute(
            """
            SELECT *
            FROM bids
            WHERE matched_keywords IS NOT NULL
              AND matched_keywords NOT IN ('[]', '')
            ORDER BY updated_at DESC, close_date ASC
            LIMIT ?
            """,
            (limit,),
        )
        return [_deserialize_row(row) for row in cur.fetchall()]

    def close(self) -> None:
        self.conn.close()


def _serialize(value: Any) -> Any:
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return value


def _deserialize_row(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    for key in ("matched_keywords", "risk_keywords", "attachment_urls", "region_limit", "license_limit", "raw_json"):
        value = result.get(key)
        if isinstance(value, str) and value:
            try:
                result[key] = json.loads(value)
            except json.JSONDecodeError:
                pass
    return result
