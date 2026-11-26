"""데이터 수집 — 고객 텍스트(공개 앱 리뷰) 수집과 전처리 (데이터명세서 D04, DCX 분석용)

연결 ─ 화면: — (화면 설계 근거용 고객 리뷰 분석, 예측과 무관)
       API : —
       DB  : — (data/reviews/ CSV)

    pip install google-play-scraper
    python -m peakguard.collect.reviews --count 200
    → data/reviews/reviews_raw.csv, reviews_clean.csv

※ 예측 모델의 입력이 아니라 DCX(고객 경험) 분석·화면 설계 근거용입니다.
※ 작성자 이름·프로필 등 개인정보는 저장하지 않습니다(리뷰 ID는 해시로만 보관).
※ 이 모듈은 Google Play 접속이 필요합니다. 개발 환경(인터넷 제한)에서는 실행 확인을 하지 못했습니다.
"""
from __future__ import annotations

import argparse
import hashlib
import re

import pandas as pd

from ..config import DATA_REVIEWS

APPS = {  # 앱 이름 → Google Play 패키지 ID (수집 전 스토어에서 ID를 꼭 확인하세요)
    "파워플래너": "kr.co.kepco.pp",
    "아파트아이": "com.aptner.aptapp",
}


def fetch(app_id: str, count: int) -> list[dict]:
    from google_play_scraper import Sort, reviews
    result, _ = reviews(app_id, lang="ko", country="kr", sort=Sort.NEWEST, count=count)
    return result


def clean_text(s: str) -> str:
    s = re.sub(r"http\S+", " ", str(s))
    s = re.sub(r"[^0-9A-Za-z가-힣\s.,!?]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def main() -> None:
    ap = argparse.ArgumentParser(description="공개 앱 리뷰 수집")
    ap.add_argument("--count", type=int, default=200, help="앱별 최대 리뷰 수")
    a = ap.parse_args()
    rows = []
    for name, app_id in APPS.items():
        try:
            got = fetch(app_id, a.count)
        except Exception as e:  # 네트워크·패키지 ID 오류 등
            print(f"[실패] {name}({app_id}): {e}")
            continue
        for r in got:
            rows.append({"app_name": name, "app_id": app_id,
                         "review_hash": hashlib.sha256(str(r.get("reviewId")).encode()).hexdigest()[:16],
                         "time": r.get("at"), "score": r.get("score"), "review": r.get("content", "")})
        print(f"  {name}: {len(got)}개")
    if not rows:
        print("수집된 리뷰가 없습니다.")
        return
    DATA_REVIEWS.mkdir(parents=True, exist_ok=True)
    raw = pd.DataFrame(rows)
    raw.to_csv(DATA_REVIEWS / "reviews_raw.csv", index=False, encoding="utf-8-sig")
    cl = raw.assign(review_clean=raw.review.map(clean_text))
    cl = cl[cl.review_clean.str.len() >= 10].drop_duplicates("review_clean")
    cl.to_csv(DATA_REVIEWS / "reviews_clean.csv", index=False, encoding="utf-8-sig")
    print(f"저장: 원본 {len(raw)}개, 정제 {len(cl)}개 → {DATA_REVIEWS}")


if __name__ == "__main__":
    main()
