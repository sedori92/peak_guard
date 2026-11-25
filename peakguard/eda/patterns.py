"""[분석] 사용패턴·피크 분석 그래프 (EDA, Exploratory Data Analysis 탐색적 데이터 분석)

연결 ─ 화면: — (보고용 그래프 PNG, python run.py eda)
       API : —
       DB  : measurement·weather_15m·peak_threshold (읽기)

    python run.py eda
    → reports/eda/ 에 그래프 5개 + summary.txt

  1_hourly_profile.png     평일 vs 주말·공휴일 시간대별 평균 수요전력
  2_daily_peak.png         일 피크 추이와 피크 기준(직전 30일 P95)
  3_peak_hour.png          일 피크가 발생한 시각 분포
  4_floor_share.png        층별 호실·공용 사용 비중
  5_temp_vs_peak.png       일 최고기온과 일 피크 (기상 데이터가 있을 때)
"""
from __future__ import annotations

import holidays
import matplotlib
import numpy as np
import pandas as pd
from sqlalchemy import text

from ..config import COMMON_METERS, FLOORS, REPORT_DIR
from ..database.connection import get_engine

import logging

logging.getLogger("matplotlib.font_manager").setLevel(logging.ERROR)   # 없는 글꼴 경고 숨김
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

plt.rcParams["font.family"] = ["Malgun Gothic", "AppleGothic", "NanumGothic", "Noto Sans CJK KR",
                               "Noto Sans CJK JP", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False
OUT = REPORT_DIR / "eda"


def load(engine) -> tuple[pd.DataFrame, pd.Series, pd.DataFrame]:
    with engine.connect() as con:
        m = pd.read_sql(text("""SELECT c.meter_id, m.measured_at, m.demand_kw FROM measurement m
                                JOIN channel c ON c.channel_id=m.channel_id"""), con)
        n_ch = con.execute(text("SELECT COUNT(*) FROM channel")).scalar()
        w = pd.read_sql(text("SELECT measured_at, temperature_c FROM weather_15m"), con) \
            if engine.dialect.has_table(con, "weather_15m") else pd.DataFrame(columns=["measured_at", "temperature_c"])
    m["measured_at"] = pd.to_datetime(m.measured_at)
    if getattr(m.measured_at.dt, "tz", None) is not None:
        m["measured_at"] = m.measured_at.dt.tz_convert("Asia/Seoul").dt.tz_localize(None)
    g = m.groupby("measured_at").demand_kw.agg(["sum", "size"])
    total = g.loc[g["size"] >= n_ch, "sum"]
    w["measured_at"] = pd.to_datetime(w.measured_at)
    return m, total, w


def run() -> None:
    engine = get_engine()
    OUT.mkdir(parents=True, exist_ok=True)
    m, total, w = load(engine)
    df = total.to_frame("kw")
    df["date"] = df.index.normalize()
    kr = holidays.KR(years=sorted(set(df.index.year)))
    df["off"] = (df.index.dayofweek >= 5) | pd.Series(df.index.date, index=df.index).map(lambda d: d in kr)
    df["hour"] = df.index.hour + df.index.minute / 60
    full = df.groupby("date").size()
    days = full[full == 96].index                     # 96칸이 다 있는 날만 일 단위 분석
    daily = df[df.date.isin(days)].groupby("date").agg(peak=("kw", "max"), mean=("kw", "mean"), off=("off", "first"))
    daily["peak_hour"] = df[df.date.isin(days)].groupby("date").kw.idxmax().dt.hour

    # 1) 시간대별 프로필
    fig, ax = plt.subplots(figsize=(9, 4))
    for off, lab, c in ((False, "평일", "#ff7a1a"), (True, "주말·공휴일", "#5c6bc0")):
        p = df[df.off == off].groupby("hour").kw
        ax.plot(p.mean().index, p.mean(), color=c, lw=2, label=f"{lab} 평균")
        ax.fill_between(p.mean().index, p.quantile(.1), p.quantile(.9), color=c, alpha=.15)
    ax.set_xticks(range(0, 25, 3)); ax.set_xlabel("시각"); ax.set_ylabel("수요전력 (kW)")
    ax.set_title("시간대별 건물 합산 수요전력 (음영: 10~90% 범위)"); ax.legend(); ax.grid(alpha=.3)
    fig.tight_layout(); fig.savefig(OUT / "1_hourly_profile.png", dpi=130); plt.close(fig)

    # 2) 일 피크 추이
    with engine.connect() as con:
        thr = pd.read_sql(text("SELECT date, threshold_kw FROM peak_threshold"), con) \
            if engine.dialect.has_table(con, "peak_threshold") else pd.DataFrame(columns=["date", "threshold_kw"])
    fig, ax = plt.subplots(figsize=(12, 4))
    ax.scatter(daily.index[~daily.off], daily.peak[~daily.off], s=14, color="#ff7a1a", label="평일 일 피크")
    ax.scatter(daily.index[daily.off], daily.peak[daily.off], s=14, color="#5c6bc0", label="주말·공휴일 일 피크")
    if len(thr):
        ax.plot(pd.to_datetime(thr.date), thr.threshold_kw, "r--", lw=1, label="피크 기준 (직전 30일 P95)")
    ax.set_ylabel("kW"); ax.set_title("일 피크 추이"); ax.legend(); ax.grid(alpha=.3)
    fig.tight_layout(); fig.savefig(OUT / "2_daily_peak.png", dpi=130); plt.close(fig)

    # 3) 피크 시각 분포
    fig, ax = plt.subplots(figsize=(8, 3.6))
    wk = daily[~daily.off].peak_hour.value_counts().reindex(range(24), fill_value=0)
    ax.bar(wk.index, wk.values, color="#ff7a1a"); ax.set_xticks(range(0, 24, 2))
    ax.set_xlabel("일 피크 발생 시각 (평일)"); ax.set_ylabel("일수"); ax.set_title("평일 일 피크는 언제 생기나"); ax.grid(axis="y", alpha=.3)
    fig.tight_layout(); fig.savefig(OUT / "3_peak_hour.png", dpi=130); plt.close(fig)

    # 4) 층별 비중
    share = m.groupby("meter_id").demand_kw.sum()
    rows = [(f"{f}층", share.get(rm, 0), share.get(cm, 0)) for rm, f in FLOORS.items()
            for cm, f2 in COMMON_METERS.items() if f == f2]
    lab, room, com = zip(*rows)
    tot = share.sum()
    fig, ax = plt.subplots(figsize=(7, 3.6))
    ax.bar(lab, np.array(room) / tot * 100, color="#ff7a1a", label="호실")
    ax.bar(lab, np.array(com) / tot * 100, bottom=np.array(room) / tot * 100, color="#bdbdbd", label="공용")
    ax.set_ylabel("전체 사용량 중 비중 (%)"); ax.set_title("층별 사용 비중"); ax.legend(); ax.grid(axis="y", alpha=.3)
    fig.tight_layout(); fig.savefig(OUT / "4_floor_share.png", dpi=130); plt.close(fig)

    # 5) 기온 vs 일 피크
    corr = None
    if len(w) and w.temperature_c.notna().any():
        tmax = w.assign(date=w.measured_at.dt.normalize()).groupby("date").temperature_c.max()
        j = daily.join(tmax.rename("tmax"), how="inner").dropna()
        fig, ax = plt.subplots(figsize=(6.5, 4))
        for off, lab, c in ((False, "평일", "#ff7a1a"), (True, "주말·공휴일", "#5c6bc0")):
            s = j[j.off == off]
            ax.scatter(s.tmax, s.peak, color=c, s=16, label=lab)
        ax.set_xlabel("일 최고기온 (℃)"); ax.set_ylabel("일 피크 (kW)"); ax.legend(); ax.grid(alpha=.3)
        corr = float(j[~j.off][["tmax", "peak"]].corr().iloc[0, 1])
        ax.set_title(f"기온과 일 피크 (평일 상관계수 {corr:.2f})")
        fig.tight_layout(); fig.savefig(OUT / "5_temp_vs_peak.png", dpi=130); plt.close(fig)

    wd, we = daily[~daily.off], daily[daily.off]
    s = [f"분석 대상: {daily.index.min():%Y-%m-%d} ~ {daily.index.max():%Y-%m-%d} 중 96칸이 온전한 {len(daily)}일",
         f"평일 평균 일 피크 {wd.peak.mean():.2f} kW (최대 {wd.peak.max():.2f} kW, {wd.peak.idxmax():%m-%d})",
         f"주말·공휴일 평균 일 피크 {we.peak.mean():.2f} kW",
         f"평일 일 피크 최다 발생 시각: {int(wk.idxmax())}시대 ({int(wk.max())}일)",
         f"호실 계량기 사용 비중 {sum(room) / tot * 100:.1f}%, 공용 {sum(com) / tot * 100:.1f}%"]
    if corr is not None:
        s.append(f"평일 일 최고기온과 일 피크 상관계수 {corr:.2f}")
    (OUT / "summary.txt").write_text("\n".join(s), encoding="utf-8")
    print("\n".join(s))
    print(f"저장: {OUT}")


def main() -> None:
    run()


if __name__ == "__main__":
    main()
