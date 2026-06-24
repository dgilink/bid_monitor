from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from config import INCLUDE_KEYWORDS, POSITIVE_KEYWORDS, RISK_KEYWORDS


EXCLUDE_KEYWORDS = ["장비 구매", "물품 구매", "유지보수", "공사", "매각", "임대", "온비드", "라이선스 구매"]


@dataclass
class AnalysisResult:
    matched_keywords: list[str]
    risk_keywords: list[str]
    positive_keywords: list[str]
    score: int
    grade: str
    comment: str
    qualification_summary: str
    estimated_dev_days: int | None


def normalize_text(*values: Any) -> str:
    return " ".join(str(v or "") for v in values).replace("\u00a0", " ")


def find_keywords(text: str, keywords: list[str]) -> list[str]:
    lowered = text.lower()
    return [kw for kw in keywords if kw.lower() in lowered]


def parse_amount(value: Any) -> int | None:
    if value in (None, ""):
        return None
    digits = re.sub(r"[^0-9]", "", str(value))
    return int(digits) if digits else None


def parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y%m%d%H%M", "%Y/%m/%d %H:%M"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def estimate_duration_days(text: str) -> int | None:
    patterns = [
        r"착수일(?:로부터)?\s*(\d{1,3})\s*일",
        r"계약일(?:로부터)?\s*(\d{1,3})\s*일",
        r"과업기간[^0-9]{0,20}(\d{1,3})\s*일",
        r"개발기간[^0-9]{0,20}(\d{1,3})\s*일",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return int(match.group(1))
    month_match = re.search(r"(\d{1,2})\s*개월", text)
    if month_match:
        return int(month_match.group(1)) * 30
    return None


def extract_qualification_summary(text: str) -> str:
    labels = ["입찰참가자격", "참가자격", "입찰 참가자격"]
    for label in labels:
        idx = text.find(label)
        if idx >= 0:
            snippet = re.sub(r"\s+", " ", text[idx: idx + 450]).strip()
            return snippet
    return "API/문서에서 참가자격 핵심 문장을 자동 특정하지 못함"


def analyze_bid(bid: dict[str, Any], detail: dict[str, Any] | None = None, doc_text: str = "", license_limits: list[dict[str, Any]] | None = None, regions: list[dict[str, Any]] | None = None) -> AnalysisResult:
    detail = detail or {}
    license_limits = license_limits or []
    regions = regions or []
    text = normalize_text(bid, detail, doc_text, license_limits, regions)
    title = normalize_text(bid.get("bidNtceNm"), detail.get("bidNtceNm"))

    matched = find_keywords(normalize_text(title, detail.get("prdctClsfcNoNm"), doc_text), INCLUDE_KEYWORDS)
    risks = find_keywords(text, RISK_KEYWORDS)
    positives = find_keywords(text, POSITIVE_KEYWORDS)
    excludes = find_keywords(title, EXCLUDE_KEYWORDS)

    score = 35
    reasons: list[str] = []

    if matched:
        score += min(25, 8 + len(matched) * 4)
        reasons.append(f"관련 키워드: {', '.join(matched[:5])}")
    if any(k in text for k in ["앱", "웹앱", "모바일", "관리자 페이지", "Android", "iOS"]):
        score += 20
        reasons.append("앱/웹 개발 범위가 확인됨")

    duration = estimate_duration_days(text)
    if duration is not None:
        if duration >= 90:
            score += 20
        elif duration >= 60:
            score += 10
        elif duration <= 30:
            score -= 20
        reasons.append(f"개발기간 추정 {duration}일")

    amount = parse_amount(bid.get("asignBdgtAmt") or bid.get("presmptPrce") or detail.get("asignBdgtAmt"))
    if amount:
        if 10_000_000 <= amount <= 50_000_000:
            score += 15
            reasons.append("1인 개발자가 검토 가능한 예산 범위")
        elif amount >= 100_000_000:
            score -= 20
            reasons.append("예산 1억 이상 대형 SI 가능성")

    if any(k in text for k in ["기능개선", "모바일앱", "모바일 앱"]):
        score += 15
    if any(k in text for k in ["관리자 페이지", "관리자페이지"]):
        score += 10
    if any(k in text for k in ["AI", "OCR", "QR", "알림"]):
        score += 10
    if positives:
        score += 20
        reasons.append(f"제한 낮음 문구: {', '.join(positives[:4])}")

    penalties = {
        "실적": -30,
        "직접생산확인증명서": -25,
        "중소기업확인서": -20,
        "소기업": -20,
        "소상공인": -20,
        "정보통신공사업": -20,
        "지역제한": -20,
        "본점 소재지": -20,
        "상주": -25,
        "보안인증": -20,
        "ISMS": -20,
        "유지보수": -15,
    }
    for token, penalty in penalties.items():
        if token in text:
            score += penalty
    if license_limits:
        score -= 20
        risks.append("면허/업종 제한 API 항목 존재")
    if regions:
        score -= 15
        risks.append("참가가능지역 API 항목 존재")
    if excludes:
        score -= 45
        reasons.append(f"제외 성격 키워드: {', '.join(excludes)}")

    close_dt = parse_datetime(bid.get("bidClseDt") or detail.get("bidClseDt"))
    if close_dt and close_dt < datetime.now():
        score -= 50
        reasons.append("이미 마감됨")

    score = max(0, min(100, score))
    if not matched or excludes or (close_dt and close_dt < datetime.now()):
        grade = "D"
    elif score >= 75 and len(risks) <= 1:
        grade = "A"
    elif score >= 50:
        grade = "B"
    elif score >= 25:
        grade = "C"
    else:
        grade = "D"

    return AnalysisResult(
        matched_keywords=sorted(set(matched)),
        risk_keywords=sorted(set(risks)),
        positive_keywords=sorted(set(positives)),
        score=score,
        grade=grade,
        comment="; ".join(reasons[:6]) or "자동 분석 근거 부족, 원문 확인 필요",
        qualification_summary=extract_qualification_summary(text),
        estimated_dev_days=duration,
    )
