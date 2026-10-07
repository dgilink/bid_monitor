from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml


WORKFLOW = Path(".github/workflows/bid-monitor.yml")


def test_workflow_yaml_and_required_runtime_contracts():
    text = WORKFLOW.read_text(encoding="utf-8")
    data = yaml.load(text, Loader=yaml.BaseLoader)
    triggers = data["on"]

    assert "workflow_dispatch" in triggers
    crons = [entry["cron"] for entry in triggers["schedule"]]
    assert crons == ["17 0 * * *", "17 3 * * *", "17 6 * * *", "17 9 * * *"]
    assert "timezone:" not in text
    assert data["permissions"]["contents"] == "write"
    assert data["concurrency"]["group"] == "bid-monitor"
    assert data["concurrency"]["cancel-in-progress"] == "false"
    assert "always()" in text
    assert "state/sent_bids.json" in text
    assert "state/health.json" in text
    assert "state/notification_health.json" in text


def test_utc_crons_map_to_requested_kst_hours():
    kst = ZoneInfo("Asia/Seoul")
    utc_hours = [0, 3, 6, 9]

    converted = [
        datetime(2026, 10, 7, hour, 17, tzinfo=timezone.utc).astimezone(kst).strftime("%H:%M")
        for hour in utc_hours
    ]

    assert converted == ["09:17", "12:17", "15:17", "18:17"]
