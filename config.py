from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
LOG_DIR = BASE_DIR / "logs"
DOWNLOAD_DIR = BASE_DIR / "downloads"


@dataclass(frozen=True)
class Settings:
    service_key: str
    telegram_bot_token: str
    telegram_chat_id: str
    check_days: int = 3
    mock_mode: bool = False
    send_telegram: bool = True
    send_empty_summary: bool = False
    db_path: Path = DATA_DIR / "bids.sqlite"
    request_timeout: int = 30
    document_timeout: int = 8
    request_sleep_seconds: float = 0.25
    max_document_downloads: int = 8


def load_settings() -> Settings:
    load_dotenv(BASE_DIR / ".env")
    DATA_DIR.mkdir(exist_ok=True)
    LOG_DIR.mkdir(exist_ok=True)
    DOWNLOAD_DIR.mkdir(exist_ok=True)

    return Settings(
        service_key=unquote(os.getenv("DATA_GO_KR_SERVICE_KEY", "").strip()),
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", "").strip(),
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", "").strip(),
        check_days=int(os.getenv("CHECK_DAYS", "3") or "3"),
        mock_mode=os.getenv("MOCK_MODE", "false").lower() in {"1", "true", "yes", "y"},
        send_telegram=os.getenv("SEND_TELEGRAM", "true").lower() in {"1", "true", "yes", "y"},
        send_empty_summary=os.getenv("SEND_EMPTY_SUMMARY", "false").lower() in {"1", "true", "yes", "y"},
        db_path=Path(os.getenv("SQLITE_PATH", DATA_DIR / "bids.sqlite")),
        request_timeout=int(os.getenv("REQUEST_TIMEOUT", "30") or "30"),
        document_timeout=int(os.getenv("DOCUMENT_TIMEOUT", "8") or "8"),
        max_document_downloads=int(os.getenv("MAX_DOCUMENT_DOWNLOADS", "8") or "8"),
    )


INCLUDE_KEYWORDS = [
    "앱개발", "앱 개발", "모바일앱", "모바일 앱", "어플리케이션", "어플", "앱 기능개선", "앱 고도화",
    "앱 구축", "모바일 서비스", "플랫폼 개발", "플랫폼 구축", "시스템 개발", "정보시스템", "웹앱",
    "PWA", "Android", "iOS", "Flutter", "React Native", "AI 앱", "AI 서비스", "동작분석 앱",
    "통합 운영 솔루션", "홈페이지 및 앱", "예약 앱", "교육 앱", "알림 앱",
]

RISK_KEYWORDS = [
    "최근 3년 실적", "단일 실적", "유사사업 실적", "수행실적", "실적증명", "소프트웨어사업자",
    "컴퓨터관련서비스사업", "직접생산확인증명서", "중소기업확인서", "소기업", "소상공인", "벤처기업",
    "여성기업", "장애인기업", "창업기업 제한", "지역제한", "본점 소재지", "정보통신공사업",
    "산업디자인전문회사", "공동수급 필수", "컨소시엄 필수", "제안서 발표 필수", "상주 인력",
    "PM 상주", "보안인증", "ISMS", "GS인증", "CC인증", "조달청 경쟁입찰참가자격등록 특정 업종",
]

POSITIVE_KEYWORDS = [
    "참가자격 제한 없음", "업종제한 없음", "지역제한 없음", "면허제한 없음", "실적제한 없음",
    "소액수의", "견적제출", "전자견적", "수의계약 안내", "제한없음", "누구나",
    "법인 또는 개인사업자", "개인사업자 가능",
]

DOC_FOCUS_KEYWORDS = [
    "입찰참가자격", "참가자격", "과업기간", "계약기간", "개발기간", "착수일", "완료일", "납품기한",
    "제안요청서", "과업지시서", "유지보수 범위", "앱스토어", "플레이스토어", "Android", "iOS",
    "관리자 페이지", "서버", "DB", "API", "알림", "로그인", "본인인증", "위치기반", "AI", "OCR", "QR",
]
