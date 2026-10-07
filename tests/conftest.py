from __future__ import annotations

import pytest

import telegram_sender


@pytest.fixture(autouse=True)
def forbid_real_telegram(monkeypatch):
    def fail_if_called(*args, **kwargs):
        raise AssertionError("A test attempted a real Telegram HTTP request")

    monkeypatch.setattr(telegram_sender.requests, "post", fail_if_called)
