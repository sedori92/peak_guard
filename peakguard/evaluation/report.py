"""[4] 예측모델별 평가 결과 — 표(CSV·텍스트)와 그래프(PNG)

    python run.py 4
    → reports/eval/ 에 아래 파일 생성
       metrics_total.csv / metrics_channel.csv / peak_detection.csv   (표)
       1_mae_by_horizon.png      모델 × 선행시간(15·30·45·60분) 평균 오차
       2_total_week.png          평가기간 1주일: 실측 vs 모델별 1시간 뒤 예측 (전체 피크 선)
       3_peak_detection.png      피크 사전 감지 정밀도·재현율
       4_importance.png          영향요인 순위 (LightGBM SHAP 평균 |기여도|)
       5_channel_mae.png         채널(호실)별 평균 오차 상위
run.py 3 (예측모델 학습)을 먼저 실행해야 합니다.
"""
from __future__ import annotations

import json

import matplotlib
import numpy as np
import pandas as pd
from sqlalchemy import text

from ..config import DISPLAY, FLOORS, REPORT_DIR
from ..database.connection import get_engine

import logging

logging.getLogger("matplotlib.font_manager").setLevel(logging.ERROR)   # 없는 글꼴 경고 숨김
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

plt.rcParams["font.family"] = ["Malgun Gothic", "AppleGothic", "NanumGothic", "Noto Sans CJK KR",
                               "Noto Sans CJK JP", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False
COLORS = {"전일 동일시각": "#9e9e9e", "최근값 유지": "#607d8b", "LightGBM": "#ff7a1a",
          "RandomForest": "#2e7d32", "BiLSTM": "#5c6bc0"}
OUT = REPORT_DIR / "eval"


def _q(engine, sql, **p) -> pd.DataFrame:
    with engine.connect() as con:
        return pd.read_sql(text(sql), con, params=p)


def tables(engine) -> dict[str, pd.DataFrame]:
    ev = _q(engine, "SELECT * FROM st_eval")
    out = {}
    for scope in ("TOTAL", "CHANNEL"):
        e = ev[ev.scope == scope]
        t = e[e.horizon_min == 0].set_index("model_name")[["mae", "rmse", "mape", "n"]]
        for h in (15, 30, 45, 60):
            t[f"MAE_{h}분"] = e[e.horizon_min == h].set_index("model_name").mae
        base = t.loc["전일 동일시각", "mae"] if "전일 동일시각" in t.index else np.nan
        t["전일기준대비개선%"] = (base - t.mae) / base * 100
        t.index = [DISPLAY.get(i, i) for i in t.index]
        out[scope] = t.round(3)
    pk = _q(engine, "SELECT * FROM st_peak_eval").set_index("model_name")
    pk.index = [DISPLAY.get(i, i) for i in pk.index]
    out["PEAK"] = pk.round(3)
    return out


def run() -> None:
    engine = get_engine()
    OUT.mkdir(parents=True, exist_ok=True)
    info = json.loads(_q(engine, "SELECT value FROM st_meta WHERE key='info'").value[0])
    t = tables(engine)
    t["TOTAL"].to_csv(OUT / "metrics_total.csv", encoding="utf-8-sig")
    t["CHANNEL"].to_csv(OUT / "metrics_channel.csv", encoding="utf-8-sig")
    t["PEAK"].to_csv(OUT / "peak_detection.csv", encoding="utf-8-sig")
    print(f"평가기간 {info['test_start']} ~ {info['test_end']}  (모든 모델이 예측한 칸만 비교)\n")
    print("■ 건물 전체 합산 (kW)\n" + t["TOTAL"].to_string() + "\n")
    print("■ 채널 61개 (kW)\n" + t["CHANNEL"].to_string() + "\n")
    print("■ 피크 사전 감지 (다음 1시간 중 기준 초과)\n" + t["PEAK"][["precision", "recall", "f1", "tp", "fp", "fn"]].to_string() + "\n")

    ev = _q(engine, "SELECT * FROM st_eval WHERE scope='TOTAL' AND horizon_min>0")
    models = info["models"]
    # 1) 선행시간별 MAE
    fig, ax = plt.subplots(figsize=(9, 4.5))
    w = 0.8 / len(models)
    for k, m in enumerate(models):
        e = ev[ev.model_name == m].sort_values("horizon_min")
        ax.bar(np.arange(4) + k * w - 0.4 + w / 2, e.mae, w, label=DISPLAY.get(m, m), color=COLORS.get(m))
    ax.set_xticks(range(4), ["15분 뒤", "30분 뒤", "45분 뒤", "60분 뒤"])
    ax.set_ylabel("평균 오차 MAE (kW)"); ax.set_title("건물 전체 합산 — 선행시간별 예측 오차 (낮을수록 좋음)")
    ax.legend(ncol=3, fontsize=9); ax.grid(axis="y", alpha=.3)
    fig.tight_layout(); fig.savefig(OUT / "1_mae_by_horizon.png", dpi=130); plt.close(fig)

    # 2) 평가기간 중 1주일: 실측 vs 1시간 뒤 예측
    d0 = pd.Timestamp("2026-10-26"); d1 = d0 + pd.Timedelta(days=7)
    fc = _q(engine, """SELECT model_name, issued_at, predicted_kw FROM st_forecast
                       WHERE series_id='TOTAL' AND horizon_min=60 AND issued_at >= :a AND issued_at < :b""",
            a=str(d0 - pd.Timedelta(hours=1)), b=str(d1))
    fc["t"] = pd.to_datetime(fc.issued_at) + pd.Timedelta(hours=1)
    act = _q(engine, """SELECT measured_at, SUM(demand_kw) AS y, COUNT(*) AS n FROM measurement
                        WHERE measured_at >= :a AND measured_at < :b GROUP BY measured_at""", a=str(d0), b=str(d1))
    act["t"] = pd.to_datetime(act.measured_at)
    act = act[act.n == act.n.max()]
    thr = _q(engine, "SELECT date, threshold_kw FROM peak_threshold WHERE date >= :a AND date < :b",
             a=f"{d0:%Y-%m-%d}", b=f"{d1:%Y-%m-%d}")
    fig, ax = plt.subplots(figsize=(13, 4.8))
    ax.plot(act.t, act.y, color="black", lw=1.6, label="실측")
    for m in ("최근값 유지", "LightGBM", "RandomForest", "BiLSTM"):
        e = fc[fc.model_name == m].sort_values("t")
        if len(e):
            ax.plot(e.t, e.predicted_kw, lw=1, color=COLORS.get(m), label=f"{DISPLAY.get(m, m)} (1시간 전 예측)")
    for r in thr.itertuples():
        a = pd.Timestamp(r.date)
        ax.hlines(r.threshold_kw, a, a + pd.Timedelta(days=1), colors="red", linestyles="--", lw=1)
    ax.plot([], [], "r--", label="피크 기준 (직전 30일 P95)")
    ax.set_title(f"전체 피크 선 — {d0:%m/%d}~{(d1 - pd.Timedelta(days=1)):%m/%d} 실측과 1시간 뒤 예측")
    ax.set_ylabel("수요전력 (kW)"); ax.legend(ncol=3, fontsize=8, loc="upper right"); ax.grid(alpha=.3)
    fig.tight_layout(); fig.savefig(OUT / "2_total_week.png", dpi=130); plt.close(fig)

    # 3) 피크 사전 감지
    pk = t["PEAK"]
    fig, ax = plt.subplots(figsize=(8, 4))
    x = np.arange(len(pk))
    ax.bar(x - 0.2, pk.precision, 0.4, label="정밀도 (경보가 맞은 비율)", color="#ff7a1a")
    ax.bar(x + 0.2, pk.recall, 0.4, label="재현율 (실제 피크를 미리 잡은 비율)", color="#5c6bc0")
    ax.set_xticks(x, pk.index); ax.set_ylim(0, 1.05); ax.legend(fontsize=9); ax.grid(axis="y", alpha=.3)
    ax.set_title(f"피크 사전 감지 성능 (실제 피크 {int(pk.tp.iloc[0] + pk.fn.iloc[0])}건)")
    fig.tight_layout(); fig.savefig(OUT / "3_peak_detection.png", dpi=130); plt.close(fig)

    # 4) 영향요인
    imp = info.get("importance") or []
    if imp:
        fig, ax = plt.subplots(figsize=(7, 4.5))
        names, vals = zip(*imp[::-1])
        ax.barh(names, vals, color="#ff7a1a"); ax.set_xlabel("평균 |SHAP| (kW)")
        ax.set_title("영향요인 순위 — LightGBM 1시간 뒤 전체 합산 예측"); ax.grid(axis="x", alpha=.3)
        fig.tight_layout(); fig.savefig(OUT / "4_importance.png", dpi=130); plt.close(fig)

    # 5) 호실 채널별 오차 (LightGBM, 15분 뒤)
    fc = _q(engine, """SELECT series_id, issued_at, predicted_kw FROM st_forecast
                       WHERE model_name='LightGBM' AND series_id<>'TOTAL' AND horizon_min=15""")
    if len(fc):
        fc["t"] = (pd.to_datetime(fc.issued_at) + pd.Timedelta(minutes=15)).dt.strftime("%Y-%m-%d %H:%M:%S")
        m = _q(engine, "SELECT channel_id AS series_id, measured_at AS t, demand_kw FROM measurement WHERE measured_at >= :a",
               a=info["test_start"])
        j = fc.merge(m, on=["series_id", "t"])
        e = j.assign(err=(j.demand_kw - j.predicted_kw).abs()).groupby("series_id").agg(mae=("err", "mean"),
                                                                                         mean=("demand_kw", "mean"))
        e = e.sort_values("mae", ascending=False).head(12)[::-1]
        lab = [f"{FLOORS.get(c.split('-')[0], '?')}층 {c[-2:]}번" for c in e.index]
        fig, ax = plt.subplots(figsize=(7, 4.5))
        ax.barh(lab, e.mae, color="#ff7a1a", label="평균 오차")
        ax.barh(lab, e["mean"], color="none", edgecolor="#555", label="평균 사용량")
        ax.set_xlabel("kW"); ax.legend(fontsize=9); ax.grid(axis="x", alpha=.3)
        ax.set_title("호실 채널별 15분 뒤 예측 오차 상위 12 (LightGBM)")
        fig.tight_layout(); fig.savefig(OUT / "5_channel_mae.png", dpi=130); plt.close(fig)
    print(f"저장: {OUT} (표 3개, 그래프 5개)")


def main() -> None:
    run()


if __name__ == "__main__":
    main()
