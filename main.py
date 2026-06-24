from __future__ import annotations

import argparse
import hashlib
import json
import logging
from datetime import datetime, timedelta
from typing import Any

from config import INCLUDE_KEYWORDS, LOG_DIR, load_settings
from document_parser import extract_attachment_urls, extract_text_from_files, focus_snippets
from filter import analyze_bid, find_keywords, parse_amount
from nara_api import DiagnoseResult, MOCK_BIDS, NaraApiClient, build_g2b_detail_url
from state_store import SentBidState
from storage import BidStorage
from telegram_sender import (
    format_bid_message,
    format_money,
    format_run_summary,
    format_summary,
    send_message,
    send_test_message,
)


def configure_logging() -> None:
    LOG_DIR.mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[
            logging.FileHandler(LOG_DIR / "bid_monitor.log", encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )


def main() -> int:
    configure_logging()
    parser = argparse.ArgumentParser(description="나라장터 앱개발 입찰 모니터링")
    parser.add_argument("--test-telegram", action="store_true", help="텔레그램 테스트 메시지만 전송")
    parser.add_argument("--test-api", action="store_true", help="나라장터 API 목록 호출 결과 일부 출력")
    parser.add_argument("--test-nara", action="store_true", help="나라장터 API 상태/결과코드/건수만 점검")
    parser.add_argument("--diagnose-nara", action="store_true", help="나라장터 API 500 원인 분리를 위한 조합 진단")
    parser.add_argument("--list-matched", action="store_true", help="최근 수집된 매칭 공고 50건 출력")
    parser.add_argument("--force-notify-test", action="store_true", help="최근 실제 공고 1건을 텔레그램 포맷으로 테스트 발송")
    parser.add_argument("--mock", action="store_true", help="실제 API 대신 MOCK 데이터 사용")
    args = parser.parse_args()

    settings = load_settings()
    if args.mock:
        settings = settings.__class__(**{**settings.__dict__, "mock_mode": True})

    if args.test_telegram:
        ok = send_test_message(settings)
        print(f"telegram_test={'ok' if ok else 'failed'}")
        return 0 if ok else 1

    storage: BidStorage | None = None
    if args.list_matched or args.force_notify_test:
        storage = BidStorage(settings.db_path)
        try:
            if args.list_matched:
                print_recent_matched(storage.list_recent_matched(50))
                return 0
            rows = storage.list_recent_matched(50)
            row = pick_force_notify_row(rows)
            if not row:
                print("force_notify_test=failed: 최근 매칭 공고가 없습니다. python main.py를 먼저 실행하세요.")
                return 1
            ok = send_message(settings, format_bid_message(row))
            print(f"force_notify_test={'ok' if ok else 'failed'}")
            print(f"bid_id={row.get('bid_id')}")
            print(f"title={row.get('title')}")
            return 0 if ok else 1
        finally:
            storage.close()

    client = NaraApiClient(settings)
    if args.test_nara:
        summary = client.test_nara()
        print(f"status={summary.status}")
        print(f"resultCode={summary.result_code or '없음'}")
        print(f"resultMsg={summary.result_msg or '없음'}")
        print(f"item_count={summary.item_count}")
        return 0

    if args.diagnose_nara:
        results = client.diagnose_nara()
        print_diagnosis(results)
        return 0

    storage = BidStorage(settings.db_path)
    run_id = storage.begin_run()
    sent_state = SentBidState()
    try:
        begin = datetime.now() - timedelta(days=settings.check_days)
        end = datetime.now()
        bids = MOCK_BIDS if settings.mock_mode else client.get_all_service_bids(begin, end)
        if args.test_api:
            print(json.dumps(bids[:3], ensure_ascii=False, indent=2))
            storage.finish_run(run_id, fetched_count=len(bids), matched_count=0, notified_count=0, c_count=0)
            return 0

        detail_client = None if settings.mock_mode else client
        matched_count = 0
        notified_count = 0
        grade_counts = {"A": 0, "B": 0, "C": 0, "D": 0}

        for bid in bids:
            try:
                title = str(bid.get("bidNtceNm") or "")
                if not find_keywords(title, INCLUDE_KEYWORDS):
                    continue
                matched_count += 1
                bid_no = str(bid.get("bidNtceNo") or "").strip()
                bid_ord = str(bid.get("bidNtceOrd") or "00").strip() or "00"
                if not bid_no:
                    continue

                detail: dict[str, Any] = {}
                basic: dict[str, Any] = {}
                license_limits: list[dict[str, Any]] = []
                regions: list[dict[str, Any]] = []
                if detail_client:
                    detail = detail_client.get_detail(bid_no, bid_ord)
                    basic = detail_client.get_basic_amount(bid_no, bid_ord)
                    license_limits = detail_client.get_license_limit(bid_no, bid_ord)
                    regions = detail_client.get_possible_regions(bid_no, bid_ord)

                merged = {**bid, **detail, **basic}
                urls = extract_attachment_urls(merged)
                doc_text = str(bid.get("mock_document_text") or "")
                failed_docs: list[str] = []
                if urls and not settings.mock_mode:
                    doc_text, failed_docs = extract_text_from_files(urls, settings)
                snippets = focus_snippets(doc_text)
                analysis = analyze_bid(bid, detail=merged, doc_text=snippets or doc_text, license_limits=license_limits, regions=regions)

                if failed_docs:
                    analysis.comment += "; 첨부문서 수동확인 필요"
                grade_counts[analysis.grade] = grade_counts.get(analysis.grade, 0) + 1
                row = build_row(bid_no, bid_ord, merged, urls, license_limits, regions, analysis)
                is_new, changed = storage.upsert_bid(row)
                already_sent = sent_state.has(row["bid_id"])
                should_notify = row["grade"] in {"A", "B"} and (is_new or changed) and not already_sent
                if should_notify:
                    if send_message(settings, format_bid_message(row, changed=changed)):
                        storage.mark_notified(row["bid_id"])
                        sent_state.add(row["bid_id"])
                        notified_count += 1
            except Exception as exc:
                logging.exception("Bid processing failed: %s", exc)

        if settings.send_empty_summary:
            send_message(settings, format_run_summary(len(bids), matched_count, grade_counts, notified_count))
        elif grade_counts.get("C", 0):
            send_message(settings, format_summary(grade_counts.get("C", 0)))

        storage.finish_run(
            run_id,
            fetched_count=len(bids),
            matched_count=matched_count,
            notified_count=notified_count,
            c_count=grade_counts.get("C", 0),
        )
        logging.info(
            "finished fetched=%s matched=%s a=%s b=%s c=%s d=%s notified=%s",
            len(bids),
            matched_count,
            grade_counts.get("A", 0),
            grade_counts.get("B", 0),
            grade_counts.get("C", 0),
            grade_counts.get("D", 0),
            notified_count,
        )
        return 0
    except Exception as exc:
        logging.exception("Run failed")
        storage.finish_run(run_id, error=str(exc))
        return 1
    finally:
        sent_state.save()
        storage.close()


def print_recent_matched(rows: list[dict[str, Any]]) -> None:
    if not rows:
        print("최근 매칭 공고가 없습니다.")
        return
    headers = ["등급", "점수", "공고명", "기관", "마감일", "예산", "위험키워드", "링크"]
    widths = [4, 4, 36, 18, 16, 14, 24, 36]
    print(" | ".join(_fit(header, width) for header, width in zip(headers, widths)))
    print("-+-".join("-" * width for width in widths))
    for row in rows:
        risk = ", ".join(row.get("risk_keywords") or []) or "없음"
        values = [
            row.get("grade") or "-",
            str(row.get("solo_score") or "-"),
            row.get("title") or "-",
            row.get("notice_org") or row.get("demand_org") or "-",
            row.get("close_date") or "-",
            format_money(row.get("budget_amount") or row.get("estimated_price")),
            risk,
            row.get("detail_url") or "-",
        ]
        print(" | ".join(_fit(str(value), width) for value, width in zip(values, widths)))


def _fit(value: str, width: int) -> str:
    value = " ".join(value.split())
    if len(value) <= width:
        return value.ljust(width)
    return value[: max(0, width - 1)] + "…"


def pick_force_notify_row(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    real_rows = [row for row in rows if "example.com" not in str(row.get("detail_url") or "")]
    rows = real_rows or rows
    for grade in ("A", "B", "C", "D"):
        for row in rows:
            if row.get("grade") == grade:
                return row
    return rows[0] if rows else None


def print_diagnosis(results: list[DiagnoseResult]) -> None:
    successes = [result for result in results if result.succeeded]
    for result in results:
        print(f"[{result.index}] endpoint={result.endpoint_name} / type={result.response_type} / range={result.range_name}")
        print(f"status={result.status}")
        print(f"resultCode={result.result_code or '없음'}")
        print(f"resultMsg={result.result_msg or '없음'}")
        print(f"item_count={result.item_count}")
        print(f"body_preview={result.body_preview or '없음'}")
        print()

    if successes:
        print("[diagnosis_summary]")
        print("success=true")
        for result in successes:
            print(
                "working_combination="
                f"endpoint={result.endpoint_name}, type={result.response_type}, range={result.range_name}, items={result.item_count}"
            )
        first = successes[0]
        print("recommended_default=" f"endpoint={first.endpoint_name}, type={first.response_type}")
    else:
        print("[diagnosis_summary]")
        print("success=false")
        print("all_combinations_failed=true")
        print("possible_causes:")
        print("- 공공데이터포털 서비스키 승인/활성화 지연")
        print("- 사용 중인 API 엔드포인트 변경")
        print("- 나라장터 API 서버 일시 오류")
        print("- 날짜 범위 제한 문제")
        print("- 공공데이터포털 Swagger에서 직접 호출 확인 필요")


def build_row(bid_no: str, bid_ord: str, merged: dict[str, Any], urls: list[str], license_limits: list[dict[str, Any]], regions: list[dict[str, Any]], analysis: Any) -> dict[str, Any]:
    title = merged.get("bidNtceNm")
    detail_url = merged.get("bidNtceDtlUrl") or merged.get("bidNtceUrl") or build_g2b_detail_url(bid_no, bid_ord)
    budget = parse_amount(merged.get("asignBdgtAmt") or merged.get("bdgtAmt"))
    estimated = parse_amount(merged.get("presmptPrce") or merged.get("presmptPrceAmt"))
    base = parse_amount(merged.get("bssamt") or merged.get("bssAmt") or merged.get("baseAmt"))
    content = json.dumps(merged, ensure_ascii=False, sort_keys=True) + json.dumps(analysis.__dict__, ensure_ascii=False, sort_keys=True)
    return {
        "bid_id": f"{bid_no}-{bid_ord}",
        "bid_no": bid_no,
        "bid_ord": bid_ord,
        "title": title,
        "notice_org": merged.get("ntceInsttNm"),
        "demand_org": merged.get("dminsttNm"),
        "business_type": merged.get("bsnsDivNm"),
        "contract_method": merged.get("cntrctCnclsMthdNm"),
        "bid_method": merged.get("bidMethdNm"),
        "notice_date": merged.get("bidNtceDt"),
        "close_date": merged.get("bidClseDt"),
        "open_date": merged.get("opengDt"),
        "estimated_price": estimated,
        "budget_amount": budget,
        "base_amount": base,
        "duration_text": merged.get("prdctnPrd") or merged.get("dlvrTmlmtDt"),
        "estimated_dev_days": analysis.estimated_dev_days,
        "region_limit": regions,
        "license_limit": license_limits,
        "qualification_summary": analysis.qualification_summary,
        "attachment_urls": urls,
        "detail_url": detail_url,
        "matched_keywords": analysis.matched_keywords,
        "risk_keywords": analysis.risk_keywords,
        "solo_score": analysis.score,
        "grade": analysis.grade,
        "comment": analysis.comment,
        "raw_json": merged,
        "content_hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
    }


if __name__ == "__main__":
    raise SystemExit(main())
