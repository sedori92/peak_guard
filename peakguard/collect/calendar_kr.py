"""데이터 수집 — 주말·공휴일 달력 (데이터명세서 D03)

    python -m peakguard.collect.calendar_kr --start 2026-06-29 --end 2026-12-31
    → data/calendar/calendar.csv

공휴일은 holidays 패키지(대한민국, 대체공휴일 포함)를 사용합니다.
기업의 실제 영업일·휴무일과는 다를 수 있으니, 기업 휴무일을 받으면 extra_offdays 에 추가하세요.
"""
from __future__ import annotations

import argparse

import holidays as hd
import pandas as pd

from ..config import DATA_CALENDAR

WEEK_KO = "월화수목금토일"
extra_offdays: dict[str, str] = {}      # 예: {"2026-12-31": "기업 휴무"}


def calendar(start: str, end: str) -> pd.DataFrame:
    days = pd.date_range(start, end, freq="D")
    kr = hd.KR(years=sorted({d.year for d in days}))
    rows = []
    for d in days:
        name = kr.get(d.date()) or extra_offdays.get(d.date().isoformat())
        rows.append({"date_kst": d.date().isoformat(), "weekday_iso": d.isoweekday(),
                     "weekday_name_ko": WEEK_KO[d.dayofweek], "is_weekend": d.dayofweek >= 5,
                     "is_public_holiday": name is not None, "holiday_name": name or "",
                     "is_offday": (d.dayofweek >= 5) or (name is not None)})
    return pd.DataFrame(rows)


def is_offday(dates) -> pd.Series:
    """날짜 목록 → 주말·공휴일 여부 (예측 모델 특징에 사용)."""
    d = pd.to_datetime(pd.Series(dates))
    kr = hd.KR(years=sorted(set(d.dt.year)))
    return (d.dt.dayofweek >= 5) | d.dt.date.map(lambda x: x in kr or x.isoformat() in extra_offdays)


def main() -> None:
    ap = argparse.ArgumentParser(description="주말·공휴일 달력 CSV 생성")
    ap.add_argument("--start", default="2026-06-29")
    ap.add_argument("--end", default="2026-12-31")
    a = ap.parse_args()
    DATA_CALENDAR.mkdir(parents=True, exist_ok=True)
    cal = calendar(a.start, a.end)
    out = DATA_CALENDAR / "calendar.csv"
    cal.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"저장: {out} ({len(cal)}일, 공휴일 {cal.is_public_holiday.sum()}일)")
    print(cal[cal.is_public_holiday][["date_kst", "weekday_name_ko", "holiday_name"]].to_string(index=False))


if __name__ == "__main__":
    main()
