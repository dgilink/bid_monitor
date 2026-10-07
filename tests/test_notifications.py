from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import main
from main import send_core_failure_alert, send_success_status, should_notify_bid
from state_store import OperationalNotificationState, SentBidState
from telegram_sender import format_bid_message


KST = ZoneInfo("Asia/Seoul")


def at(day: int, hour: int = 9) -> datetime:
    return datetime(2026, 10, day, hour, 17, tzinfo=KST)


def test_new_ab_notifies_once_and_same_hash_does_not(tmp_path):
    state = SentBidState(tmp_path / "sent.json")

    kind = state.notification_kind("bid-1", "hash-1")
    assert kind == "new"
    assert should_notify_bid("A", kind)
    state.mark_sent("bid-1", "hash-1")

    assert state.notification_kind("bid-1", "hash-1") == "same"
    assert not should_notify_bid("A", state.notification_kind("bid-1", "hash-1"))


def test_changed_hash_is_changed_notification(tmp_path):
    state = SentBidState(tmp_path / "sent.json")
    state.mark_sent("bid-1", "hash-1")

    kind = state.notification_kind("bid-1", "hash-2")
    message = format_bid_message(
        {
            "grade": "B",
            "risk_keywords": [],
            "title": "changed",
            "solo_score": 70,
        },
        changed=True,
    )

    assert kind == "changed"
    assert should_notify_bid("B", kind)
    assert message.startswith("[변경공고]")


def test_c_and_d_are_never_individual_notifications():
    for grade in ("C", "D"):
        assert not should_notify_bid(grade, "new")
        assert not should_notify_bid(grade, "changed")


def test_legacy_entry_is_baselined_without_notification(tmp_path):
    path = tmp_path / "sent.json"
    path.write_text(json.dumps({"sent_bid_ids": ["bid-1"]}), encoding="utf-8")
    state = SentBidState(path)

    assert state.notification_kind("bid-1", "hash-1") == "legacy"
    state.set_baseline("bid-1", "hash-1")
    assert state.notification_kind("bid-1", "hash-1") == "same"


def test_core_failure_alert_is_limited_to_once_per_kst_day(tmp_path, monkeypatch):
    sent: list[str] = []
    monkeypatch.setattr(main, "send_message", lambda settings, text: sent.append(text) or True)
    state = OperationalNotificationState(tmp_path / "notification.json")
    settings = SimpleNamespace()

    assert send_core_failure_alert(settings, state, "TIMEOUT", at(7, 9))
    assert send_core_failure_alert(settings, state, "TIMEOUT", at(7, 12))

    assert len(sent) == 1
    assert "핵심 API 조회 실패" in sent[0]
    assert state.incident_active


def test_failed_failure_alert_does_not_lose_incident_state(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "send_message", lambda settings, text: False)
    state = OperationalNotificationState(tmp_path / "notification.json")

    assert not send_core_failure_alert(SimpleNamespace(), state, "HTTP 5xx", at(7))
    assert state.incident_active
    assert state.last_core_error == "HTTP 5xx"
    assert state.last_failure_alert_date is None


def test_heartbeat_once_same_day_and_again_next_day(tmp_path, monkeypatch):
    sent: list[str] = []
    monkeypatch.setattr(main, "send_message", lambda settings, text: sent.append(text) or True)
    state = OperationalNotificationState(tmp_path / "notification.json")
    settings = SimpleNamespace()

    assert send_success_status(settings, state, fetched_count=10, matched_count=2, notified_count=0, now=at(7, 9))
    assert send_success_status(settings, state, fetched_count=11, matched_count=3, notified_count=0, now=at(7, 12))
    assert len(sent) == 1
    assert sent[0].startswith("[나라장터 입찰 모니터 정상]")

    assert send_success_status(settings, state, fetched_count=12, matched_count=4, notified_count=0, now=at(8, 9))
    assert len(sent) == 2


def test_failure_then_success_sends_one_combined_recovery_and_heartbeat(tmp_path, monkeypatch):
    sent: list[str] = []
    monkeypatch.setattr(main, "send_message", lambda settings, text: sent.append(text) or True)
    state = OperationalNotificationState(tmp_path / "notification.json")
    state.record_core_failure("TIMEOUT", at(7, 9))

    assert send_success_status(
        SimpleNamespace(),
        state,
        fetched_count=20,
        matched_count=5,
        notified_count=1,
        now=at(7, 12),
    )

    assert len(sent) == 1
    assert sent[0].startswith("[나라장터 입찰 모니터 복구]")
    assert "조회공고: 20건" in sent[0]
    assert not state.incident_active
    assert state.last_recovery_date == "2026-10-07"
    assert state.last_heartbeat_date == "2026-10-07"


def test_operational_state_save_is_atomic_and_reloadable(tmp_path):
    path = tmp_path / "notification.json"
    state = OperationalNotificationState(path)
    state.record_core_failure("TIMEOUT", at(7))
    state.mark_failure_alert_sent(at(7))
    state.save()

    loaded = OperationalNotificationState(path)
    assert loaded.incident_active
    assert loaded.last_failure_alert_date == "2026-10-07"
    assert not path.with_suffix(".json.tmp").exists()
