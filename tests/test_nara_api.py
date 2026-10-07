from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import Mock

import pytest
import requests

import nara_api
from config import Settings
from nara_api import CoreNaraApiError, NaraApiClient, mask_sensitive


class FakeResponse:
    def __init__(self, status_code: int, payload: dict) -> None:
        self.status_code = status_code
        self.text = json.dumps(payload)


def response_payload(items: list[dict] | None = None, code: str = "00", message: str = "OK") -> dict:
    return {
        "response": {
            "header": {"resultCode": code, "resultMsg": message},
            "body": {"items": items or []},
        }
    }


@pytest.fixture
def client(tmp_path, monkeypatch) -> NaraApiClient:
    settings = Settings(
        service_key="service-secret",
        telegram_bot_token="telegram-secret",
        telegram_chat_id="chat-secret",
        db_path=tmp_path / "bids.sqlite",
        request_sleep_seconds=0,
    )
    api = NaraApiClient(settings)
    monkeypatch.setattr(api, "_save_raw", lambda *args: None)
    monkeypatch.setattr(nara_api.time, "sleep", lambda *args: None)
    return api


def fetch(client: NaraApiClient) -> list[dict]:
    return client.get_service_bids(datetime(2026, 10, 1), datetime(2026, 10, 2))


def test_timeout_then_second_attempt_succeeds(client):
    client.session.get = Mock(
        side_effect=[requests.Timeout(), FakeResponse(200, response_payload([{"bidNtceNo": "1"}]))]
    )

    assert fetch(client) == [{"bidNtceNo": "1"}]
    assert client.session.get.call_count == 2


def test_two_timeouts_then_third_attempt_succeeds(client):
    client.session.get = Mock(
        side_effect=[
            requests.Timeout(),
            requests.Timeout(),
            FakeResponse(200, response_payload([{"bidNtceNo": "1"}])),
        ]
    )

    assert len(fetch(client)) == 1
    assert client.session.get.call_count == 3


@pytest.mark.parametrize("status", [500, 503])
def test_5xx_retries_then_succeeds(client, status):
    client.session.get = Mock(
        side_effect=[
            FakeResponse(status, response_payload(code="99", message="temporary")),
            FakeResponse(200, response_payload([{"bidNtceNo": "1"}])),
        ]
    )

    assert len(fetch(client)) == 1
    assert client.session.get.call_count == 2


def test_429_retries_then_succeeds(client):
    client.session.get = Mock(
        side_effect=[
            FakeResponse(429, response_payload(code="429", message="rate limited")),
            FakeResponse(200, response_payload([{"bidNtceNo": "1"}])),
        ]
    )

    assert len(fetch(client)) == 1
    assert client.session.get.call_count == 2


def test_request_exception_retries_then_succeeds_without_secret_in_logs(client, caplog):
    client.session.get = Mock(
        side_effect=[
            requests.ConnectionError("service-secret must not be logged"),
            FakeResponse(200, response_payload([{"bidNtceNo": "1"}])),
        ]
    )

    assert len(fetch(client)) == 1
    assert client.session.get.call_count == 2
    assert "service-secret" not in caplog.text


def test_permanent_4xx_is_not_retried(client):
    client.session.get = Mock(
        return_value=FakeResponse(400, response_payload(code="04", message="bad request"))
    )

    with pytest.raises(CoreNaraApiError) as error:
        fetch(client)

    assert error.value.result_code == "HTTP_400"
    assert client.session.get.call_count == 1


def test_three_timeouts_raise_core_error(client):
    client.session.get = Mock(side_effect=requests.Timeout())

    with pytest.raises(CoreNaraApiError) as error:
        fetch(client)

    assert error.value.result_code == "TIMEOUT"
    assert client.session.get.call_count == 3


def test_retry_success_continues_pagination(client):
    first_page = [{"bidNtceNo": str(index)} for index in range(100)]
    client.session.get = Mock(
        side_effect=[
            FakeResponse(200, response_payload(first_page)),
            requests.Timeout(),
            FakeResponse(200, response_payload([{"bidNtceNo": "last"}])),
        ]
    )

    rows = client.get_all_service_bids(datetime(2026, 10, 1), datetime(2026, 10, 2))

    assert len(rows) == 101
    assert client.session.get.call_count == 3


def test_optional_timeout_is_skipped_without_retry(client):
    client.session.get = Mock(side_effect=requests.Timeout())

    assert client.get_license_limit("1") == []
    assert client.session.get.call_count == 1


def test_mask_sensitive_keeps_all_configured_secrets_out(client):
    text = (
        "https://example.test?serviceKey=service-secret&x=1 "
        "https://api.telegram.org/bottelegram-secret/sendMessage "
        "chat-secret"
    )

    masked = mask_sensitive(text, client.settings)

    assert "service-secret" not in masked
    assert "telegram-secret" not in masked
    assert "chat-secret" not in masked
    assert masked.count("***") >= 3
