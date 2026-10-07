from __future__ import annotations

import json
import logging
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import requests

from config import Settings


LOGGER = logging.getLogger(__name__)

CORE_MAX_ATTEMPTS = 3
CORE_RETRY_BACKOFF_SECONDS = (2.0, 5.0)


@dataclass
class ApiSummary:
    status: int | None
    result_code: str | None
    result_msg: str | None
    item_count: int


@dataclass
class DiagnoseResult:
    index: int
    endpoint_name: str
    response_type: str
    range_name: str
    status: int | None
    result_code: str | None
    result_msg: str | None
    item_count: int
    body_preview: str
    url: str

    @property
    def succeeded(self) -> bool:
        return self.status == 200 and str(self.result_code or "").strip() in {"00", "0"}


class CoreNaraApiError(RuntimeError):
    """Raised only after the required list API cannot be trusted."""

    def __init__(self, operation: str, result_code: str, result_msg: str) -> None:
        self.operation = operation
        self.result_code = result_code
        self.result_msg = result_msg
        super().__init__(
            "Core Nara API failed "
            f"operation={operation} resultCode={result_code} resultMsg={result_msg}"
        )


class NaraApiClient:
    """Client for 조달청_나라장터 입찰공고정보서비스."""

    BASE_URL = "https://apis.data.go.kr/1230000/ad/BidPublicInfoService"
    CORE_LIST_OPERATION = "getBidPblancListInfoServc"
    DIAGNOSE_ENDPOINTS = [
        ("ad/BidPublicInfoService", "https://apis.data.go.kr/1230000/ad/BidPublicInfoService/getBidPblancListInfoServc"),
        ("ad/BidPublicInfoService02", "https://apis.data.go.kr/1230000/ad/BidPublicInfoService02/getBidPblancListInfoServc"),
        ("BidPublicInfoService", "https://apis.data.go.kr/1230000/BidPublicInfoService/getBidPblancListInfoServc"),
        ("BidPublicInfoService02", "https://apis.data.go.kr/1230000/BidPublicInfoService02/getBidPblancListInfoServc"),
    ]

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.session = requests.Session()
        self.unsupported_operations: set[str] = set()

    def _service_key(self) -> str:
        # .env should contain the decoding key. If an encoded key is provided,
        # decode once and let requests encode params exactly once.
        return unquote(self.settings.service_key)

    def _request(self, operation: str, params: dict[str, Any]) -> dict[str, Any]:
        if not self.settings.service_key:
            LOGGER.error("DATA_GO_KR_SERVICE_KEY is not configured")
            return _empty_response("NO_SERVICE_KEY", "DATA_GO_KR_SERVICE_KEY is not configured")
        if operation in self.unsupported_operations:
            return _empty_response("UNSUPPORTED_OPERATION", "operation skipped after previous 404")

        is_core_list = operation == self.CORE_LIST_OPERATION
        url = f"{self.BASE_URL}/{operation}"
        final_params = {
            "serviceKey": self._service_key(),
            "type": "json",
            **params,
        }

        max_attempts = CORE_MAX_ATTEMPTS if is_core_list else 1
        for attempt in range(1, max_attempts + 1):
            try:
                time.sleep(self.settings.request_sleep_seconds)
                response = self.session.get(url, params=final_params, timeout=self.settings.request_timeout)
                self._save_raw(operation, response.text)

                data = parse_response_body(response.text)
                result_code, result_msg = extract_result(data)
                is_transient_status = response.status_code == 429 or response.status_code >= 500

                if is_core_list and is_transient_status and attempt < max_attempts:
                    LOGGER.warning(
                        "Nara API retry attempt=%s max_attempts=%s operation=%s status=%s",
                        attempt,
                        max_attempts,
                        operation,
                        response.status_code,
                    )
                    self._sleep_before_retry(attempt)
                    continue

                if is_transient_status:
                    log = LOGGER.warning if is_core_list else LOGGER.info
                    log(
                        "%s Nara API operation failed, skipped=%s operation=%s status=%s resultCode=%s",
                        "core" if is_core_list else "optional",
                        not is_core_list,
                        operation,
                        response.status_code,
                        result_code,
                    )
                    return _empty_response(
                        f"HTTP_{response.status_code}",
                        mask_sensitive(result_msg or "Nara API transient error", self.settings),
                    )

                if response.status_code >= 400:
                    if response.status_code == 404 and not is_core_list:
                        self.unsupported_operations.add(operation)
                        LOGGER.info("optional Nara API operation unavailable, skipped: %s", operation)
                    elif is_core_list:
                        LOGGER.warning(
                            "Nara API permanent request failure operation=%s status=%s resultCode=%s",
                            operation,
                            response.status_code,
                            result_code,
                        )
                    else:
                        LOGGER.info(
                            "optional Nara API operation failed, skipped: %s status=%s resultCode=%s",
                            operation,
                            response.status_code,
                            result_code,
                        )
                    return _empty_response(
                        f"HTTP_{response.status_code}",
                        mask_sensitive(result_msg or "Nara API request failed", self.settings),
                    )

                if not isinstance(data, dict):
                    if is_core_list:
                        LOGGER.warning("Nara API parse failed operation=%s status=%s", operation, response.status_code)
                    else:
                        LOGGER.info("optional Nara API operation parse failed, skipped: %s status=%s", operation, response.status_code)
                    return _empty_response("PARSE_ERROR", "JSON/XML parse failed")

                if not self._is_success(data):
                    if is_core_list:
                        LOGGER.warning("Nara API returned operation=%s resultCode=%s", operation, result_code)
                    else:
                        LOGGER.info(
                            "optional Nara API operation returned non-success, skipped: %s resultCode=%s",
                            operation,
                            result_code,
                        )
                return data
            except requests.Timeout:
                if is_core_list and attempt < max_attempts:
                    LOGGER.warning(
                        "Nara API retry attempt=%s max_attempts=%s operation=%s status=TIMEOUT",
                        attempt,
                        max_attempts,
                        operation,
                    )
                    self._sleep_before_retry(attempt)
                    continue
                if is_core_list:
                    LOGGER.warning("Nara API request exhausted operation=%s status=TIMEOUT", operation)
                else:
                    LOGGER.info("optional Nara API operation timed out, skipped: %s", operation)
                return _empty_response("TIMEOUT", "request timed out")
            except requests.RequestException as exc:
                if is_core_list and attempt < max_attempts:
                    LOGGER.warning(
                        "Nara API retry attempt=%s max_attempts=%s operation=%s status=%s",
                        attempt,
                        max_attempts,
                        operation,
                        exc.__class__.__name__,
                    )
                    self._sleep_before_retry(attempt)
                    continue
                if is_core_list:
                    LOGGER.warning(
                        "Nara API request exhausted operation=%s status=%s",
                        operation,
                        exc.__class__.__name__,
                    )
                else:
                    LOGGER.info(
                        "optional Nara API operation exception, skipped: %s status=%s",
                        operation,
                        exc.__class__.__name__,
                    )
                return _empty_response("REQUEST_ERROR", "request failed")

        return _empty_response("REQUEST_ERROR", "request failed")

    @staticmethod
    def _sleep_before_retry(attempt: int) -> None:
        delay_index = min(attempt - 1, len(CORE_RETRY_BACKOFF_SECONDS) - 1)
        time.sleep(CORE_RETRY_BACKOFF_SECONDS[delay_index])

    @staticmethod
    def _is_success(data: dict[str, Any]) -> bool:
        code, _ = extract_result(data)
        return str(code or "").strip() in {"00", "0", ""}

    def _save_raw(self, operation: str, text: str) -> None:
        log_path = Path("logs") / f"api_raw_{operation}_{datetime.now():%Y%m%d}.log"
        with log_path.open("a", encoding="utf-8") as f:
            f.write(mask_sensitive(text[:4000], self.settings) + "\n---\n")

    @staticmethod
    def _items(data: dict[str, Any]) -> list[dict[str, Any]]:
        body = data.get("response", {}).get("body", {}) if isinstance(data, dict) else {}
        items = body.get("items", [])
        if isinstance(items, dict):
            item = items.get("item", items)
            return item if isinstance(item, list) else [item]
        return items if isinstance(items, list) else []

    def get_service_bids(self, begin: datetime, end: datetime, page_no: int = 1, rows: int = 100) -> list[dict[str, Any]]:
        data = self._request(
            self.CORE_LIST_OPERATION,
            {
                "inqryDiv": "1",
                "inqryBgnDt": begin.strftime("%Y%m%d%H%M"),
                "inqryEndDt": end.strftime("%Y%m%d%H%M"),
                "pageNo": page_no,
                "numOfRows": rows,
            },
        )
        result_code, result_msg = extract_result(data)
        normalized_code = str(result_code or "").strip()
        if normalized_code not in {"", "0", "00"}:
            raise CoreNaraApiError(
                self.CORE_LIST_OPERATION,
                normalized_code,
                mask_sensitive(result_msg or "unknown", self.settings),
            )
        return self._items(data)

    def get_all_service_bids(self, begin: datetime, end: datetime) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        page_no = 1
        while page_no <= 20:
            rows = self.get_service_bids(begin, end, page_no=page_no, rows=100)
            results.extend(rows)
            if len(rows) < 100:
                break
            page_no += 1
        return results

    def get_detail(self, bid_no: str, bid_ord: str = "00") -> dict[str, Any]:
        return self._first_available(
            ["getBidPblancDetailInfoServc", "getBidPblancListInfoServcPPSSrch"],
            {"bidNtceNo": bid_no, "bidNtceOrd": bid_ord},
        )

    def get_basic_amount(self, bid_no: str, bid_ord: str = "00") -> dict[str, Any]:
        return self._first_available(["getBidPblancListInfoServcBsisAmount"], {"bidNtceNo": bid_no, "bidNtceOrd": bid_ord})

    def get_license_limit(self, bid_no: str, bid_ord: str = "00") -> list[dict[str, Any]]:
        data = self._request("getBidPblancListInfoServcLicenseLimit", {"bidNtceNo": bid_no, "bidNtceOrd": bid_ord})
        return self._items(data)

    def get_possible_regions(self, bid_no: str, bid_ord: str = "00") -> list[dict[str, Any]]:
        data = self._request("getBidPblancListInfoServcPrtcptPsblRgn", {"bidNtceNo": bid_no, "bidNtceOrd": bid_ord})
        return self._items(data)

    def test_nara(self) -> ApiSummary:
        end = datetime.now()
        begin = end - timedelta(days=1)
        result = self._call_summary(
            self.DIAGNOSE_ENDPOINTS[0][1],
            begin,
            end,
            response_type="json",
            rows=10,
        )
        if result.status and result.status >= 400:
            LOGGER.warning(
                "Nara API test failed status=%s resultCode=%s resultMsg=%s body=%s",
                result.status,
                result.result_code,
                result.result_msg,
                result.body_preview[:500],
            )
        return ApiSummary(result.status, result.result_code, result.result_msg, result.item_count)

    def diagnose_nara(self) -> list[DiagnoseResult]:
        now = datetime.now()
        ranges = [
            ("recent_1d", now - timedelta(days=1), now),
            ("recent_3d", now - timedelta(days=3), now),
            ("2025-01-01", datetime(2025, 1, 1, 0, 0), datetime(2025, 1, 2, 0, 0)),
            ("2024-12-01", datetime(2024, 12, 1, 0, 0), datetime(2024, 12, 2, 0, 0)),
        ]
        response_types: list[str | None] = ["json", "xml", None]
        results: list[DiagnoseResult] = []
        index = 1
        for endpoint_name, url in self.DIAGNOSE_ENDPOINTS:
            for response_type in response_types:
                for range_name, begin, end in ranges:
                    result = self._call_summary(url, begin, end, response_type=response_type, rows=10)
                    result.index = index
                    result.endpoint_name = endpoint_name
                    result.response_type = response_type or "none"
                    result.range_name = range_name
                    results.append(result)
                    index += 1
                    time.sleep(self.settings.request_sleep_seconds)
        return results

    def _call_summary(self, url: str, begin: datetime, end: datetime, response_type: str | None, rows: int) -> DiagnoseResult:
        params: dict[str, Any] = {
            "serviceKey": self._service_key(),
            "inqryDiv": "1",
            "inqryBgnDt": begin.strftime("%Y%m%d%H%M"),
            "inqryEndDt": end.strftime("%Y%m%d%H%M"),
            "pageNo": 1,
            "numOfRows": rows,
        }
        if response_type:
            params["type"] = response_type

        try:
            response = self.session.get(url, params=params, timeout=self.settings.request_timeout)
            body = response.text
            self._save_raw("diagnose_nara", body)
            data = parse_response_body(body)
            code, msg = extract_result(data if isinstance(data, dict) else {})
            if msg is None:
                msg = extract_plain_error_message(body)
            return DiagnoseResult(
                index=0,
                endpoint_name="",
                response_type="",
                range_name="",
                status=response.status_code,
                result_code=code,
                result_msg=msg,
                item_count=len(self._items(data if isinstance(data, dict) else {})),
                body_preview=mask_sensitive(body, self.settings)[:300],
                url=mask_sensitive(response.url, self.settings),
            )
        except requests.RequestException as exc:
            return DiagnoseResult(
                index=0,
                endpoint_name="",
                response_type="",
                range_name="",
                status=None,
                result_code="REQUEST_ERROR",
                result_msg=exc.__class__.__name__,
                item_count=0,
                body_preview=mask_sensitive(str(exc), self.settings)[:300],
                url=mask_sensitive(url, self.settings),
            )

    def _first_available(self, operations: list[str], params: dict[str, Any]) -> dict[str, Any]:
        for operation in operations:
            items = self._items(self._request(operation, params))
            if items:
                return items[0]
        return {}


def parse_response_body(text: str) -> dict[str, Any]:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return parse_xml_response(text)


def parse_xml_response(text: str) -> dict[str, Any]:
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return {}

    def find_text(name: str) -> str | None:
        node = root.find(f".//{name}")
        return node.text.strip() if node is not None and node.text else None

    items: list[dict[str, str]] = []
    for item in root.findall(".//item"):
        row = {child.tag: (child.text or "") for child in list(item)}
        if row:
            items.append(row)

    return {
        "response": {
            "header": {"resultCode": find_text("resultCode"), "resultMsg": find_text("resultMsg")},
            "body": {"items": items},
        }
    }


def extract_result(data: dict[str, Any]) -> tuple[str | None, str | None]:
    header = data.get("response", {}).get("header", {}) if isinstance(data, dict) else {}
    return header.get("resultCode"), header.get("resultMsg")


def extract_plain_error_message(text: str) -> str | None:
    compact = re.sub(r"\s+", " ", text or "").strip()
    return compact[:120] if compact else None


def _empty_response(result_code: str | None, result_msg: str | None) -> dict[str, Any]:
    return {
        "response": {
            "header": {"resultCode": result_code, "resultMsg": result_msg},
            "body": {"items": []},
        }
    }


def mask_sensitive(text: str, settings: Settings | None = None) -> str:
    if not text:
        return text
    masked = re.sub(r"([?&](?:serviceKey|ServiceKey)=)[^&\s]+", r"\1***", text)
    masked = re.sub(r"(https://api\.telegram\.org/bot)[^/\s]+", r"\1***", masked)
    masked = re.sub(r"(TELEGRAM_BOT_TOKEN=)[^\s]+", r"\1***", masked)
    if settings:
        secrets = {
            settings.service_key,
            unquote(settings.service_key),
            settings.telegram_bot_token,
            settings.telegram_chat_id,
        }
        for secret in secrets:
            if secret:
                masked = masked.replace(secret, "***")
    return masked


def build_g2b_detail_url(bid_no: str, bid_ord: str = "00") -> str:
    return f"https://www.g2b.go.kr/pt/menu/selectSubFrame.do?bidNtceNo={bid_no}&bidNtceOrd={bid_ord}"


MOCK_BIDS = [
    {
        "bidNtceNo": "20260600001",
        "bidNtceOrd": "00",
        "bidNtceNm": "2026년 모바일앱 기능개선 및 관리자 페이지 개발 용역",
        "ntceInsttNm": "테스트시",
        "dminsttNm": "테스트시 정보화담당관",
        "bsnsDivNm": "용역",
        "cntrctCnclsMthdNm": "수의(총액)소액",
        "bidMethdNm": "전자견적",
        "bidNtceDt": "2026-06-22 09:00:00",
        "bidClseDt": "2026-07-03 10:00:00",
        "opengDt": "2026-07-03 11:00:00",
        "asignBdgtAmt": "32000000",
        "presmptPrce": "29090909",
        "prdctClsfcNoNm": "정보시스템개발서비스",
        "bidNtceDtlUrl": "https://example.com/bid/mock-a",
        "ntceSpecDocUrl1": "",
    },
    {
        "bidNtceNo": "20260600002",
        "bidNtceOrd": "00",
        "bidNtceNm": "차세대 통합 플랫폼 구축 및 내부시스템 연계 사업",
        "ntceInsttNm": "테스트공단",
        "dminsttNm": "테스트공단",
        "bsnsDivNm": "용역",
        "cntrctCnclsMthdNm": "제한경쟁",
        "bidMethdNm": "전자입찰",
        "bidNtceDt": "2026-06-21 09:00:00",
        "bidClseDt": "2026-06-30 10:00:00",
        "opengDt": "2026-06-30 11:00:00",
        "asignBdgtAmt": "180000000",
        "presmptPrce": "163636363",
        "prdctClsfcNoNm": "소프트웨어개발",
        "bidNtceDtlUrl": "https://example.com/bid/mock-b",
        "ntceSpecDocUrl1": "",
        "mock_document_text": "입찰참가자격: 최근 3년 실적증명, 직접생산확인증명서, 소프트웨어사업자 필요. PM 상주.",
    },
]
