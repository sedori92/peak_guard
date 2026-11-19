"""[10] 보고서 — 주간·월간 사용량·피크·예측 정확도 보고서를 텍스트로 출력

    python run.py 10 --period week  --date 2026-10-28    # 그 날이 속한 주(월~일)
    python run.py 10 --period month --date 2026-10-15    # 그 날이 속한 달
    GET /api/report?period=week&date=2026-10-28           # 같은 내용을 API 로

결과는 화면에 출력하고 reports/report_<week|month>_<시작일>.txt 로도 저장합니다.
"""
from __future__ import annotations

import argparse

import numpy as np
import pandas as pd
from sqlalchemy import text

from ..config import FLOORS, REPORT_DIR
from ..database.connection import get_engine

WEEK_KO = "월화수목금토일"


def period_range(period: str, date: str) -> tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp, pd.Timestamp]:
    d = pd.Timestamp(date).normalize()
    if period == "week":
        s = d - pd.Timedelta(days=d.dayofweek)
        e = s + pd.Timedelta(days=7)
        ps = s - pd.Timedelta(days=7)
    elif period == "month":
        s = d.replace(day=1)
        e = s + pd.offsets.MonthBegin(1)
        ps = s - pd.offsets.MonthBegin(1)
    else:
        raise ValueError("period 는 week 또는 month")
    return s, e, ps, s


def _read(engine, a: pd.Timestamp, b: pd.Timestamp) -> pd.DataFrame:
    with engine.connect() as con:
        m = pd.read_sql(text("""SELECT m.channel_id, c.meter_id, m.measured_at, m.demand_kw
                                FROM measurement m JOIN channel c ON c.channel_id=m.channel_id
                                WHERE m.measured_at >= :a AND m.measured_at < :b"""), con,
                        params=dict(a=a.strftime("%Y-%m-%d %H:%M:%S"), b=b.strftime("%Y-%m-%d %H:%M:%S")))
    m["measured_at"] = pd.to_datetime(m.measured_at)
    if getattr(m.measured_at.dt, "tz", None) is not None:
        m["measured_at"] = m.measured_at.dt.tz_convert("Asia/Seoul").dt.tz_localize(None)
    return m


def _total(m: pd.DataFrame, n_ch: int) -> pd.Series:
    g = m.groupby("measured_at").demand_kw.agg(["sum", "size"])
    return g.loc[g["size"] >= n_ch, "sum"]          # 96개 채널이 모두 있는 칸만 합산


def label(cid: str) -> str:
    meter = cid.split("-")[0]
    return f"{FLOORS[meter]}층 {cid[-2:]}번" if meter in FLOORS else f"공용 {cid}"


def build(period: str, date: str) -> tuple[str, str]:
    engine = get_engine()
    s, e, ps, pe = period_range(period, date)
    with engine.connect() as con:
        n_ch = con.execute(text("SELECT COUNT(*) FROM channel")).scalar()
    m = _read(engine, s, e)
    title = "주간" if period == "week" else "월간"
    span = f"{s:%Y-%m-%d}({WEEK_KO[s.dayofweek]}) ~ {(e - pd.Timedelta(days=1)):%Y-%m-%d}({WEEK_KO[(e - pd.Timedelta(days=1)).dayofweek]})"
    L = [f"[피크가드 {title} 보고서] {span}",
         f"작성 {pd.Timestamp.now():%Y-%m-%d %H:%M} · 대상: 생활지원로봇센터 96개 계측채널 합산 · 단위 kW/kWh", ""]
    if m.empty:
        L.append("해당 기간 측정 데이터가 없습니다.")
        return "\n".join(L), f"report_{period}_{s:%Y-%m-%d}.txt"

    tot = _total(m, n_ch)
    expected = int((e - s) / pd.Timedelta("15min"))
    kwh = tot.sum() * 0.25
    prev = _total(_read(engine, ps, pe), n_ch)
    prev_kwh = prev.sum() * 0.25
    prev_cov = len(prev) / int((pe - ps) / pd.Timedelta("15min")) * 100
    pk_t = tot.idxmax()
    day = pd.DataFrame({"kw": tot})
    day["date"] = day.index.normalize()
    daily = day.groupby("date").kw.agg(peak="max", t=lambda x: x.idxmax(), kwh=lambda x: x.sum() * 0.25, n="size")
    import holidays
    kr = holidays.KR(years=sorted({d.year for d in daily.index}))
    daily["off"] = [(d.dayofweek >= 5) or (d.date() in kr) for d in daily.index]

    L += ["1. 사용량 요약",
          f"  - 총 사용량: {kwh:,.1f} kWh" + (f" (직전 {'주' if period == 'week' else '달'} {prev_kwh:,.1f} kWh 대비 {((kwh / prev_kwh) - 1) * 100:+.1f}%"
                                              + (f", 단 수신률 이번 {len(tot) / expected * 100:.0f}%·직전 {prev_cov:.0f}%라 비교 주의)" if min(prev_cov, len(tot) / expected * 100) < 95 else ")") if prev_kwh > 0 else ""),
          f"  - 평균 수요전력 {tot.mean():.2f} kW / 최대(피크) {tot.max():.2f} kW ({pk_t:%m-%d %H:%M})",
          f"  - 평일 평균 일 피크 {daily.loc[~daily.off, 'peak'].mean():.2f} kW · 주말·공휴일 평균 일 피크 {daily.loc[daily.off, 'peak'].mean():.2f} kW"
          if daily.off.any() and (~daily.off).any() else f"  - 평균 일 피크 {daily.peak.mean():.2f} kW",
          f"  - 데이터 수신률: {len(tot) / expected * 100:.1f}% (전체 합산 가능 15분 칸 {len(tot):,} / {expected:,})", ""]

    with engine.connect() as con:
        th = pd.read_sql(text("SELECT date, threshold_kw FROM peak_threshold WHERE date >= :a AND date < :b"), con,
                         params=dict(a=f"{s:%Y-%m-%d}", b=f"{e:%Y-%m-%d}"))
    L.append("2. 피크 기준 초과 (기준 = 직전 30일 합산 수요전력 P95)")
    if th.empty:
        L += ["  - 이 기간 피크 기준이 계산되어 있지 않습니다 (run.py 3 실행 후 생성).", ""]
    else:
        thr = th.set_index("date").threshold_kw
        day["thr"] = [thr.get(f"{d:%Y-%m-%d}", np.nan) for d in day.index]
        over = day[day.kw >= day.thr]
        L.append(f"  - 기준값 범위 {thr.min():.2f} ~ {thr.max():.2f} kW")
        L.append(f"  - 기준 이상 15분 칸 {len(over)}개 ({over.date.nunique()}일)"
                 + (": " + ", ".join(f"{d:%m-%d} {n}칸" for d, n in over.groupby('date').size().items()) if len(over) else ""))
        L.append("")

    L.append("3. 일별 피크")
    L.append("  날짜        요일   최대(kW)  시각    사용량(kWh)  비고")
    for d, r in daily.iterrows():
        note = "휴일" if r.off else ""
        if r.n < 96:
            note += f" 수신 {int(r.n)}/96칸"
        L.append(f"  {d:%Y-%m-%d}  {WEEK_KO[d.dayofweek]}    {r.peak:7.2f}   {r.t:%H:%M}   {r.kwh:9.1f}   {note.strip()}")
    L.append("")

    rooms = m[m.meter_id.isin(FLOORS)]
    use = rooms.groupby("channel_id").demand_kw.sum() * 0.25
    at_peak = rooms[rooms.measured_at == pk_t].set_index("channel_id").demand_kw
    L.append("4. 호실 채널 사용량 상위 5 (호실 34개 채널 기준)")
    for cid, v in use.sort_values(ascending=False).head(5).items():
        share = at_peak.get(cid, np.nan) / tot.max() * 100
        L.append(f"  - {label(cid):9s} ({cid})  {v:8.1f} kWh  · 호실 합계의 {v / use.sum() * 100:4.1f}%"
                 f"  · 기간 피크 시각 기여 {share:4.1f}%")
    L.append("")

    L.append("5. 예측 정확도 (다음 1시간, 15분×4, 건물 합산)")
    with engine.connect() as con:
        fc = pd.read_sql(text("""SELECT model_name, issued_at, horizon_min, predicted_kw FROM st_forecast
                                 WHERE series_id='TOTAL' AND issued_at >= :a AND issued_at < :b"""), con,
                         params=dict(a=f"{s:%Y-%m-%d %H:%M:%S}", b=f"{e:%Y-%m-%d %H:%M:%S}")) \
            if engine.dialect.has_table(con, "st_forecast") else pd.DataFrame()
    if fc.empty:
        L.append("  - 이 기간은 예측 평가 대상(평가기간)이 아니어서 정확도를 계산하지 않았습니다.")
    else:
        fc["target"] = pd.to_datetime(fc.issued_at) + pd.to_timedelta(fc.horizon_min, unit="min")
        fc["actual"] = fc.target.map(tot)
        fc = fc.dropna(subset=["actual"])
        res = fc.assign(err=(fc.actual - fc.predicted_kw).abs()).groupby("model_name").err.agg(["mean", "size"])
        for name, r in res.sort_values("mean").iterrows():
            L.append(f"  - {name:12s} 평균 오차 {r['mean']:.3f} kW (평가 {int(r['size']):,}칸)")
    L += ["", "※ 예측은 사전 계획용 참고값이며, 정산 근거는 실측입니다. 합산은 96개 채널이 모두 수신된 칸만 사용했습니다."]
    return "\n".join(L), f"report_{period}_{s:%Y-%m-%d}.txt"


def main() -> None:
    ap = argparse.ArgumentParser(description="주간·월간 보고서")
    ap.add_argument("--period", choices=["week", "month"], default="week")
    ap.add_argument("--date", default="2026-10-28")
    a = ap.parse_args()
    txt, name = build(a.period, a.date)
    REPORT_DIR.mkdir(exist_ok=True)
    (REPORT_DIR / name).write_text(txt, encoding="utf-8")
    print(txt)
    print(f"\n저장: {REPORT_DIR / name}")


if __name__ == "__main__":
    main()
