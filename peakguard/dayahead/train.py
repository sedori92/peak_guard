"""학습 → 평가 → 다음날 피크 예측 → DB 저장 (한 번에 실행)

사용법
    python -m peakguard.train                      # 4개 모델 전부 (Naive, LightGBM, RandomForest, BiLSTM)
    python -m peakguard.train --models LightGBM RandomForest
    python -m peakguard.train --test-days 21 --no-refit

결과
    - DB: model_run(성능), peak_forecast(일 피크 예측), forecast_slot(15분 예측)
    - reports/metrics.csv, reports/total_test_forecast.png
    - models/ 에 학습된 모델 저장 (predict.py 가 사용)
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sqlalchemy import select, text
from sqlalchemy.engine import Engine

from . import features
from .dataset import SLOTS, TOTAL, DayData, read_wide, set_scale, to_days
from ..database.connection import forecast_slot, get_engine, model_run, peak_forecast
from .models import BiLSTMModel, NaiveModel, TreeModel, lstm_tensors

ALL_MODELS = ["Naive", "LightGBM", "RandomForest", "BiLSTM"]
MODEL_DIR = Path("models")
REPORT_DIR = Path("reports")


# ---------------------------------------------------------------- 기간 나누기
def split_days(dd: DayData, test_days: int):
    """평가 = 전체 합계 96칸이 온전한 마지막 test_days 일 (중간 결측 기간은 자동 제외)."""
    complete_total = np.where(dd.complete[0])[0]
    has_data = ~np.isnan(dd.y).all(axis=(0, 2))
    forecast_day = int(np.where(has_data)[0].max()) + 1      # 마지막 데이터 다음 날
    test = [int(d) for d in complete_total[-test_days:]]
    train = list(range(7, test[0]))
    return train, test, forecast_day


# ---------------------------------------------------------------- 평가 지표
def evaluate(pred: dict, dd: DayData, series_idx, day_idx) -> dict:
    """pred[(s, d)] = 96칸 kW 예측. 96칸 실제값이 모두 있는 날만 평가."""
    slot_err, peak_err, ape, hit = [], [], [], []
    for s in series_idx:
        for d in day_idx:
            if (s, d) not in pred or not dd.complete[s, d]:
                continue
            a, p = dd.y[s, d], pred[(s, d)]
            slot_err.append(np.abs(a - p).mean())
            peak_err.append(abs(a.max() - p.max()))
            if a.max() >= 0.1:                                  # 0.1kW 미만 채널은 % 오차에서 제외
                ape.append(abs(a.max() - p.max()) / a.max() * 100)
            hit.append(abs(int(a.argmax()) - int(p.argmax())) <= 4)  # ±1시간
    return dict(slot_mae_kw=float(np.mean(slot_err)), peak_mae_kw=float(np.mean(peak_err)),
                peak_mape=float(np.mean(ape)) if ape else None, peak_time_hit=float(np.mean(hit) * 100))


# ---------------------------------------------------------------- 모델별 학습·예측
def run_tree(name: str, dd: DayData, s_idx, train, test, fc_day, refit: bool):
    channel_scope = len(s_idx) > 1
    m = NaiveModel() if name == "Naive" else TreeModel(name, channel_scope)
    tr = features.build(dd, s_idx, train)
    te = features.build(dd, s_idx, test)
    t0 = time.time()
    m.fit(tr)
    te["pred"] = m.predict(te)
    pred = {(s, d): g.sort_values("slot")["pred"].to_numpy() for (s, d), g in te.groupby(["series", "day"])}
    if refit and name != "Naive":
        m.fit(pd.concat([tr, te]))
    fc = features.build(dd, s_idx, [fc_day], with_target=False)
    fc["pred"] = m.predict(fc)
    for (s, d), g in fc.groupby(["series", "day"]):
        pred[(s, d)] = g.sort_values("slot")["pred"].to_numpy()
    print(f"    학습·예측 {time.time() - t0:.1f}초")
    return m, pred


def run_lstm(dd: DayData, train, test, fc_day, refit: bool):
    s_all = list(range(len(dd.series)))
    val = train[-14:]
    m = BiLSTMModel(n_series=len(dd.series))
    t0 = time.time()
    m.fit(lstm_tensors(dd, s_all, train[:-14]), lstm_tensors(dd, s_all, val))
    print(f"    최적 epoch {m.best_epoch}")
    te = lstm_tensors(dd, s_all, test)
    pred = {k: v for k, v in zip(te[4], m.predict(te, dd.scale))}
    if refit:  # 최종 예측용: 전체 기간으로 다시 학습
        m.fit(lstm_tensors(dd, s_all, train + test), epochs=m.best_epoch, verbose=False)
    fc = lstm_tensors(dd, s_all, [fc_day])
    pred.update({k: v for k, v in zip(fc[4], m.predict(fc, dd.scale))})
    print(f"    학습·예측 {time.time() - t0:.1f}초")
    return m, pred


# ---------------------------------------------------------------- DB 저장
def _ts(engine: Engine, ts: pd.Timestamp):
    return ts.tz_localize("Asia/Seoul") if engine.dialect.name == "postgresql" else ts.to_pydatetime()


def save_run(engine: Engine, model, scope: str, metrics: dict, pred: dict, dd: DayData,
             s_idx, train, test, fc_day) -> int:
    with engine.begin() as con:
        run_id = con.execute(model_run.insert().returning(model_run.c.run_id), dict(
            model_name=model.name, target_scope=scope,
            train_start=dd.days[train[0]].date(), train_end=dd.days[train[-1]].date(),
            test_start=dd.days[test[0]].date(), test_end=dd.days[test[-1]].date(),
            params=json.dumps(model.params, ensure_ascii=False, default=str), **metrics)).scalar_one()

        rows = []
        for s in s_idx:
            for d in test + [fc_day]:
                if (s, d) not in pred:
                    continue
                p = pred[(s, d)]
                k = int(p.argmax())
                actual = float(np.nanmax(dd.y[s, d])) if d < dd.y.shape[1] and dd.complete[s, d] else None
                rows.append(dict(run_id=run_id, channel_id=None if dd.series[s] == TOTAL else dd.series[s],
                                 target_date=dd.days[d].date(), peak_kw=float(p.max()),
                                 peak_time=_ts(engine, dd.days[d] + pd.Timedelta(minutes=15 * k)),
                                 actual_peak_kw=actual))
        con.execute(peak_forecast.insert(), rows)

        ids = {(r.channel_id, r.target_date): r.forecast_id for r in con.execute(
            select(peak_forecast.c.forecast_id, peak_forecast.c.channel_id, peak_forecast.c.target_date)
            .where(peak_forecast.c.run_id == run_id))}
        slots = []
        for s in s_idx:
            for d in test + [fc_day]:
                # 15분 예측값은 '전체'의 평가기간 + 모든 시계열의 다음날 예측만 저장 (용량 절약)
                if (s, d) not in pred or (d != fc_day and dd.series[s] != TOTAL):
                    continue
                fid = ids[(None if dd.series[s] == TOTAL else dd.series[s], dd.days[d].date())]
                for k, v in enumerate(pred[(s, d)]):
                    slots.append(dict(forecast_id=fid, slot_time=_ts(engine, dd.days[d] + pd.Timedelta(minutes=15 * k)),
                                      predicted_kw=float(v)))
        con.execute(forecast_slot.insert(), slots)
    return run_id


# ---------------------------------------------------------------- 그래프
def plot_total(dd: DayData, preds: dict, test, path: Path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    idx = np.arange(len(test) * SLOTS)                       # 결측 기간을 건너뛰고 평가일만 이어 붙임
    fig, ax = plt.subplots(2, 1, figsize=(14, 8))
    ax[0].plot(idx, dd.y[0, test].ravel(), color="black", lw=1, label="Actual")
    for name, p in preds.items():
        ax[0].plot(idx, np.concatenate([p[(0, d)] for d in test]), lw=0.9, label=name)
    ax[0].set_xticks(idx[::SLOTS], [f"{dd.days[d]:%m-%d}" for d in test], rotation=60, fontsize=8)
    ax[0].set_title("Total demand (kW) - test days, 15-min"); ax[0].legend(ncol=5)
    x = np.arange(len(test))
    ax[1].plot(x, dd.y[0, test].max(axis=1), "ko-", label="Actual peak")
    for name, p in preds.items():
        ax[1].plot(x, [p[(0, d)].max() for d in test], ".-", label=name)
    ax[1].set_xticks(x, [f"{dd.days[d]:%m-%d}" for d in test], rotation=60, fontsize=8)
    ax[1].set_title("Daily peak (kW)"); ax[1].legend(ncol=5)
    fig.tight_layout(); fig.savefig(path, dpi=120); plt.close(fig)


# ---------------------------------------------------------------- 메인
def main() -> None:
    ap = argparse.ArgumentParser(description="전체·채널별 피크 예측 모델 학습/평가")
    ap.add_argument("--models", nargs="+", default=ALL_MODELS, choices=ALL_MODELS)
    ap.add_argument("--test-days", type=int, default=20, help="평가에 쓸 마지막 온전한 날 수")
    ap.add_argument("--no-refit", action="store_true", help="최종 예측 때 전체 기간 재학습 생략(빠름)")
    ap.add_argument("--no-db", action="store_true", help="DB 저장 생략")
    a = ap.parse_args()

    engine = get_engine()
    print("1) DB에서 데이터 읽기")
    wide = read_wide(engine)
    dd = to_days(wide, extra_days=1)
    train, test, fc_day = split_days(dd, a.test_days)
    set_scale(dd, np.array(train))
    ch_idx = list(range(1, len(dd.series)))
    print(f"   시계열 {len(dd.series)}개(전체 1 + 채널 {len(ch_idx)}) / "
          f"학습 {dd.days[train[0]].date()}~{dd.days[train[-1]].date()} ({len(train)}일) / "
          f"평가 {dd.days[test[0]].date()}~{dd.days[test[-1]].date()} ({len(test)}일) / "
          f"예측일 {dd.days[fc_day].date()}")

    MODEL_DIR.mkdir(exist_ok=True); REPORT_DIR.mkdir(exist_ok=True)
    results, total_preds = [], {}
    for name in a.models:
        print(f"2) {name}")
        if name == "BiLSTM":
            m, pred = run_lstm(dd, train, test, fc_day, not a.no_refit)
            import torch
            torch.save({"state": m.net.state_dict(), "params": m.params, "series": dd.series,
                        "scale": dd.scale}, MODEL_DIR / "BiLSTM.pt")
            fitted = {"TOTAL": (m, pred), "CHANNEL": (m, pred)}
        else:
            print("   [전체]")
            fitted = {"TOTAL": run_tree(name, dd, [0], train, test, fc_day, not a.no_refit)}
            print("   [채널별]")
            fitted["CHANNEL"] = run_tree(name, dd, ch_idx, train, test, fc_day, not a.no_refit)
            if name != "Naive":
                joblib.dump({k: v[0] for k, v in fitted.items()} | {"series": dd.series, "scale": dd.scale},
                            MODEL_DIR / f"{name}.joblib", compress=3)
        for scope, (m, pred) in fitted.items():
            s_idx = [0] if scope == "TOTAL" else ch_idx
            met = evaluate(pred, dd, s_idx, test)
            fc_peak = pred[(0, fc_day)].max() if scope == "TOTAL" else None
            results.append(dict(model=name, scope=scope, **met,
                                next_day_total_peak_kw=fc_peak))
            print(f"   {scope:7s} 15분MAE {met['slot_mae_kw']:.3f}kW | 피크MAE {met['peak_mae_kw']:.3f}kW | "
                  f"피크MAPE {met['peak_mape']:.1f}% | 피크시각적중 {met['peak_time_hit']:.0f}%")
            if not a.no_db:
                save_run(engine, m, scope, met, pred, dd, s_idx, train, test, fc_day)
        total_preds[name] = fitted["TOTAL"][1]

    res = pd.DataFrame(results)
    res.to_csv(REPORT_DIR / "metrics.csv", index=False, encoding="utf-8-sig")
    plot_total(dd, total_preds, test, REPORT_DIR / "total_test_forecast.png")
    print("\n=== 평가 결과 (평가기간", dd.days[test[0]].date(), "~", dd.days[test[-1]].date(), ") ===")
    print(res.round(3).to_string(index=False))
    print(f"\n{dd.days[fc_day].date()} 전체 피크 예측(kW):",
          {k: round(float(v[(0, fc_day)].max()), 2) for k, v in total_preds.items()})


if __name__ == "__main__":
    main()
