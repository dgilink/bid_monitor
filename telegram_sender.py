from __future__ import annotations

import logging
import time
from typing import Any

import requests

from config import Settings


LOGGER = logging.getLogger(__name__)


def format_bid_message(row: dict[str, Any], changed: bool = False) -> str:
    grade_label = {"A": "A등급 / 바로 검토", "B": "B등급 / 확인 필요", "C": "C등급 / 어려움", "D": "D등급 / 제외"}.get(row["grade"], row["grade"])
    risk = ", ".join(row.get("risk_keywords") or []) or "없음"
    prefix = "[변경공고]\n" if changed else ""
    return f"""{prefix}[나라장터 앱개발 입찰 알림]

{grade_label}
공고명: {row.get("title") or "-"}
기관: {row.get("notice_org") or row.get("demand_org") or "-"}
마감: {row.get("close_date") or "-"}
예산: {format_money(row.get("budget_amount") or row.get("estimated_price"))}
기간: {row.get("duration_text") or (str(row.get("estimated_dev_days")) + "일" if row.get("estimated_dev_days") else "수동확인 필요")}
자격판단: {qualification_label(row)}
위험키워드: {risk}
1인개발 가능성: {row.get("solo_score")}점
핵심사유:
- {row.get("comment") or "자동 분석 근거 부족"}
- 개인사업자 가능 여부는 공고문 최종 확인 필요

공고링크:
{row.get("detail_url") or "-"}

주의:
이 알림은 자동 분석 결과이며 최종 입찰 가능 여부는 공고문/제안요청서 원문 확인 필요."""


def format_summary(c_count: int) -> str:
    return f"[나라장터 앱개발 입찰 요약]\nC등급 참고용 공고 {c_count}건 저장됨\n원문 확인이 필요한 자동 분석 결과입니다."


def format_run_summary(fetched: int, matched: int, grade_counts: dict[str, int], notified: int) -> str:
    status = "A/B 신규 공고 없음" if notified == 0 else f"신규 알림 {notified}건 발송"
    return f"""[나라장터 입찰 모니터링 요약]
조회공고: {fetched:,}건
앱개발 관련 매칭: {matched:,}건
A등급: {grade_counts.get("A", 0):,}건
B등급: {grade_counts.get("B", 0):,}건
C등급: {grade_counts.get("C", 0):,}건
신규 알림 발송: {notified:,}건
상태: {status}"""


def format_money(value: Any) -> str:
    try:
        if value in (None, ""):
            return "수동확인 필요"
        return f"{int(value):,}원"
    except Exception:
        return "수동확인 필요"


def qualification_label(row: dict[str, Any]) -> str:
    risks = row.get("risk_keywords") or []
    if row.get("grade") == "A" and not risks:
        return "제한 낮음"
    if row.get("grade") in {"A", "B"}:
        return "원문 확인 필요"
    return "자격위험 높음"


def send_message(settings: Settings, text: str) -> bool:
    if not settings.send_telegram:
        LOGGER.info("Telegram disabled. Message:\n%s", text)
        return True
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        LOGGER.warning("Telegram token/chat id missing. Skipping send.")
        return False

    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    for attempt in range(1, 4):
        try:
            response = requests.post(
                url,
                json={"chat_id": settings.telegram_chat_id, "text": text[:3900], "disable_web_page_preview": True},
                timeout=settings.request_timeout,
            )
            if response.ok:
                return True
            LOGGER.warning(
                "Telegram send failed attempt %s: %s %s",
                attempt,
                response.status_code,
                _telegram_error_description(response),
            )
        except requests.Timeout:
            LOGGER.warning("Telegram send failed attempt %s: request timed out", attempt)
        except requests.RequestException as exc:
            LOGGER.warning("Telegram send failed attempt %s: %s", attempt, _safe_request_error(exc))
        time.sleep(attempt * 2)
    return False


def _telegram_error_description(response: requests.Response) -> str:
    try:
        data = response.json()
        description = data.get("description")
        if description:
            return str(description)
    except ValueError:
        pass
    return response.reason or "request failed"


def _safe_request_error(exc: requests.RequestException) -> str:
    response = getattr(exc, "response", None)
    if response is not None:
        return f"{response.status_code} {_telegram_error_description(response)}"
    return exc.__class__.__name__


def send_test_message(settings: Settings) -> bool:
    return send_message(settings, "[나라장터 앱개발 입찰 알림]\n텔레그램 테스트 전송입니다.")
