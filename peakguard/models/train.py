"""[3] 예측모델 — 학습·예측·평가·DB 저장을 한 번에 실행

    python run.py 3                  # 전체 (CPU 2코어 약 6분)
    python run.py 3 --skip-lstm      # Bi-LSTM 제외 (약 4분)

발행 시각 t(15분마다)에 그때까지 알 수 있는 값만으로 t+15·30·45·60분 수요전력(kW)을 예측합니다.
결과: DB 의 st_forecast(예측값), st_eval(성능), st_peak_eval(피크 감지), st_explain(SHAP),
      peak_threshold(피크 기준), st_meta(실행 정보) + models/ 폴더의 모델 파일
"""
from __future__ import annotations

import argparse
import json
import time

import joblib
import numpy as np
import pandas as pd
from sqlalchemy import text

from ..config import (DAY, H, MAPE_MIN_CHANNEL_KW, MAPE_MIN_TOTAL_KW, MODEL_DIR, MODELS, BASE_DAY, BASE_LAST,
                      TEST_START, WEEK)
from ..database.connection import create_schema, get_engine
from ..database.tables_forecast import st_meta
from ..evaluation.metrics import metrics, peak_detection
from . import baseline, bilstm_model, lightgbm_model, random_forest_model
from .features import FEAT_COLS, FEATURES, features
from .grid import load_grid, take, thresholds

TREE_MODELS = {"LightGBM": lightgbm_model, "RandomForest": random_forest_model}


def main() -> None:
    ap = argparse.ArgumentParser(description="다음 1시간(15분×4) 예측 학습·평가·저장")
    ap.add_argument("--test-start", default=TEST_START, help="평가 시작일 (이전은 학습)")
    ap.add_argument("--skip-lstm", action="store_true")
    a = ap.parse_args()
    run(a.test_start, a.skip_lstm)


def run(test_start: str = TEST_START, skip_lstm: bool = False) -> None:
    t_start = time.time()
    engine = get_engine()
    create_schema(engine)

    print("1) DB에서 15분 격자 만들기")
    g = load_grid(engine)
    T, S = len(g.times), len(g.series)
    test0 = int(np.searchsorted(g.times, pd.Timestamp(test_start)))
    val0 = test0 - 14 * DAY
    all_issues = np.arange(WEEK, T - 1)
    tr_iss, te_iss = all_issues[all_issues + H < test0], all_issues[all_issues >= test0]
    tr_iss_lstm, va_iss = tr_iss[tr_iss < val0 - H], tr_iss[tr_iss >= val0]
    scale = np.array([max(np.nanpercentile(g.y[s, WEEK:test0], 95), 0.05) for s in range(S)])
    print(f"   시계열 {S}개, 학습 발행 {len(tr_iss):,}개, 평가 발행 {len(te_iss):,}개 "
          f"({g.times[te_iss[0]]:%Y-%m-%d} ~ {g.times[te_iss[-1]]:%Y-%m-%d})")

    print("2) 피크 기준(직전 30일 P95)")
    thr = thresholds(g)

    print("3) 특징 만들기")
    tr = {s: features(g, s, tr_iss, scale[s]) for s in range(S)}
    te = {s: features(g, s, te_iss, scale[s]) for s in range(S)}
    ch_tr = pd.concat([tr[s] for s in range(1, S)], ignore_index=True).dropna(subset=["y", "y0"])
    ch_te = pd.concat([te[s] for s in range(1, S)], ignore_index=True)
    tot_tr = tr[0].dropna(subset=["y", "y0"])

    preds: dict[str, dict[int, np.ndarray]] = {}          # 모델 → 시계열 → (평가 발행 수, 4) kW

    def to_mat(df: pd.DataFrame, col: np.ndarray, s: int) -> np.ndarray:
        out = np.full((len(te_iss), H), np.nan)
        pos = np.searchsorted(te_iss, df["issue"].to_numpy())
        out[pos, df["h"].to_numpy().astype(int) - 1] = col
        return out * scale[s]

    def split_series(df: pd.DataFrame, p: np.ndarray) -> dict[int, np.ndarray]:
        return {int(s): to_mat(df.iloc[idx], p[idx], int(s)) for s, idx in df.groupby("series").indices.items()}

    print("4) 기준모델")
    for name in (BASE_DAY, BASE_LAST):
        preds[name] = {s: to_mat(te[s], baseline.predict(te[s], name), s) for s in range(S)}

    MODEL_DIR.mkdir(exist_ok=True)
    explain = None
    for name, mod in TREE_MODELS.items():
        print(f"5) {name}")
        t0 = time.time()
        m_tot, m_ch = mod.fit(tot_tr, False), mod.fit(ch_tr, True)
        preds[name] = {0: to_mat(te[0], mod.predict(m_tot, te[0]), 0)} | split_series(ch_te, mod.predict(m_ch, ch_te))
        joblib.dump({"TOTAL": m_tot, "CHANNEL": m_ch, "scale": scale, "series": g.series},
                    MODEL_DIR / f"st_{name}.joblib", compress=3)
        print(f"   {time.time() - t0:.0f}초")
        if name == "LightGBM":                              # 전체 합산 1시간 뒤 예측의 SHAP
            x4 = te[0][te[0]["h"] == H]
            explain = (x4["issue"].to_numpy(), lightgbm_model.explain(m_tot, x4, scale[0]))

    if not skip_lstm:
        print("6) Bi-LSTM (전체 + 채널 함께 학습)")
        t0 = time.time()
        cat = lambda parts: tuple(np.concatenate(z) for z in zip(*parts))   # noqa: E731
        trp = [bilstm_model.lstm_arrays(g, 0, tr_iss_lstm, scale[0])] * 8
        trp += [bilstm_model.lstm_arrays(g, s, tr_iss_lstm[::3], scale[s]) for s in range(1, S)]
        vap = [bilstm_model.lstm_arrays(g, 0, va_iss, scale[0])]
        vap += [bilstm_model.lstm_arrays(g, s, va_iss[::4], scale[s]) for s in range(1, S)]
        net = bilstm_model.BiLSTM(S).fit(cat(trp), cat(vap))
        preds["BiLSTM"] = {}
        for s in range(S):
            pv = net.predict(bilstm_model.lstm_arrays(g, s, te_iss, scale[s])) * scale[s]
            pv[np.isnan(take(g.yi[s], te_iss))] = np.nan          # 최신값 없는 발행은 예측 불가
            preds["BiLSTM"][s] = pv
        import torch
        torch.save({"state": net.net.state_dict(), "params": net.params, "scale": scale, "series": g.series},
                   MODEL_DIR / "st_BiLSTM.pt")
        print(f"   {time.time() - t0:.0f}초")

    models = [m for m in MODELS if m in preds]
    for s in range(S):                     # 발행 시점 최신 실측이 없으면 모든 모델 '예측 불가' (FR-AI-004)
        stale = np.isnan(take(g.yi[s], te_iss))
        for m in models:
            preds[m][s][stale] = np.nan

    print("7) 평가 (모든 모델이 예측한 칸만 비교 = 공통 표본)")
    actual = {s: np.stack([take(g.y[s], te_iss + h) for h in range(1, H + 1)], 1) for s in range(S)}
    common = {s: np.all([~np.isnan(preds[m][s]) for m in models], axis=0) & ~np.isnan(actual[s]) for s in range(S)}
    t_s, t_e = g.times[te_iss[0]].date().isoformat(), g.times[te_iss[-1]].date().isoformat()
    day_thr = thr["threshold_kw"].reindex([d.date().isoformat() for d in g.times[te_iss]]).to_numpy()
    ev_rows, peak_rows = [], []
    for m in models:
        for scope, ss, low in (("TOTAL", [0], MAPE_MIN_TOTAL_KW), ("CHANNEL", range(1, S), MAPE_MIN_CHANNEL_KW)):
            for h in range(0, H + 1):
                cols = range(H) if h == 0 else [h - 1]
                A = np.concatenate([actual[s][:, c][common[s][:, c]] for s in ss for c in cols])
                P = np.concatenate([preds[m][s][:, c][common[s][:, c]] for s in ss for c in cols])
                ev_rows.append(dict(model_name=m, scope=scope, horizon_min=15 * h, **metrics(A, P, low),
                                    test_start=t_s, test_end=t_e))
        ok = common[0].all(1) & ~np.isnan(day_thr)
        peak_rows.append(dict(model_name=m, **peak_detection(actual[0], preds[m][0], day_thr, ok)))
    ev = pd.DataFrame(ev_rows)
    print(ev[ev.horizon_min == 0][["model_name", "scope", "mae", "rmse", "mape", "n"]].round(3).to_string(index=False))
    print(pd.DataFrame(peak_rows).round(3).to_string(index=False))

    print("8) DB 저장")
    keep = [0] + [i for i, c in enumerate(g.series) if c in set(g.rooms_all)]   # 전체 + 호실 채널만 저장
    iss_txt = g.times[te_iss].strftime("%Y-%m-%d %H:%M:%S").to_numpy()
    rows = []
    for m in models:
        for s in keep:
            P = preds[m][s]
            for h in range(H):
                ok = ~np.isnan(P[:, h])
                rows.append(pd.DataFrame({"model_name": m, "series_id": g.series[s], "issued_at": iss_txt[ok],
                                          "horizon_min": 15 * (h + 1), "predicted_kw": np.round(P[ok, h], 4)}))
    fc = pd.concat(rows, ignore_index=True)
    with engine.begin() as con:
        for t in ("st_forecast", "st_eval", "st_peak_eval", "st_explain", "st_meta", "peak_threshold"):
            con.execute(text(f"DELETE FROM {t}"))
    fc.to_sql("st_forecast", engine, if_exists="append", index=False, chunksize=50_000)
    ev.to_sql("st_eval", engine, if_exists="append", index=False)
    pd.DataFrame(peak_rows).to_sql("st_peak_eval", engine, if_exists="append", index=False)
    thr.reset_index(names="date").assign(method="직전 30일 전체 합산 P95").dropna(subset=["threshold_kw"]) \
        .to_sql("peak_threshold", engine, if_exists="append", index=False)
    imp, base = pd.Series(dtype=float), None
    if explain is not None:
        iss, contrib = explain
        names = [FEATURES[c] for c in FEAT_COLS]
        ex = pd.DataFrame(contrib[:, :-1], columns=names)
        ex["issued_at"] = g.times[iss].strftime("%Y-%m-%d %H:%M:%S")
        ex = ex.melt(id_vars="issued_at", var_name="feature", value_name="contribution_kw")
        ex[ex.contribution_kw.abs() > 1e-4].to_sql("st_explain", engine, if_exists="append", index=False,
                                                    chunksize=50_000)
        imp = pd.DataFrame(np.abs(contrib[:, :-1]), columns=names).mean().sort_values(ascending=False)
        base = float(contrib[0, -1])
    info = {"test_start": t_s, "test_end": t_e, "shap_base": base,
            "importance": [[k, round(float(v), 3)] for k, v in imp.head(10).items()],
            "rooms": g.rooms_all, "inactive": sorted(g.inactive), "models": models,
            "scale_total": float(scale[0])}
    with engine.begin() as con:
        con.execute(st_meta.insert(), [{"key": "info", "value": json.dumps(info, ensure_ascii=False)}])
    print(f"완료: 예측 {len(fc):,}행 저장, 총 {time.time() - t_start:.0f}초")


if __name__ == "__main__":
    main()
