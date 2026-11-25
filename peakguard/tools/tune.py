"""[3-보조] 하이퍼파라미터(hyperparameter, 모델 설정값) 비교 — 검증기간만 사용

연결 ─ 화면: — (설정값 비교, 결과는 docs/TUNING.md)
       API : —
       DB  : measurement (읽기), models/tuning.json (쓰기)

    python -m peakguard.tools.tune            # 트리 3종 + Bi-LSTM (CPU 2코어 약 15분)
    python -m peakguard.tools.tune --skip-lstm
    python -m peakguard.tools.tune --keep-lstm   # 트리만 다시, Bi-LSTM 은 이전 결과 유지

대상: 선정 방식인 '채널별 합산 후 예측'(건물 합계 한 줄을 바로 예측)의 4개 모델.
      학습기간으로 학습 → 검증기간(2026-09-15 ~ 10-06) 건물 합계 MAE 로 비교. 시험기간은 보지 않음.
결과: models/tuning.json  (docs/TUNING.md 는 peakguard.tools.model_report 가 함께 씀)
판단: 현재 설정보다 검증 MAE 가 2% 이상 좋은 설정이 있으면 교체(모델 파일의 PARAMS_TOTAL 에 반영 후 재학습), 아니면 유지.
주의: GRID 첫 줄('현재')은 반영 전 처음 설정입니다. 반영 후 다시 돌리면 첫 줄을 새 설정으로 바꾸세요.
"""
from __future__ import annotations

import argparse
import json
import time

import numpy as np
import pandas as pd

from ..config import H, MODEL_DIR, TRAIN_END, VAL_END, VAL_START, WEEK
from ..database.connection import get_engine
from ..evaluation.metrics import peak_detection
from ..models import bilstm_model, lightgbm_model, random_forest_model, xgboost_model
from ..models.features import FEAT_COLS, features
from ..models.grid import load_grid, take, thresholds

GRID = {
    "LightGBM": [dict(num_leaves=63, min_child_samples=40, learning_rate=0.05, n=800),     # 현재
                 dict(num_leaves=31, min_child_samples=40, learning_rate=0.05, n=800),
                 dict(num_leaves=127, min_child_samples=40, learning_rate=0.05, n=800),
                 dict(num_leaves=63, min_child_samples=20, learning_rate=0.05, n=800),
                 dict(num_leaves=63, min_child_samples=80, learning_rate=0.05, n=800),
                 dict(num_leaves=63, min_child_samples=40, learning_rate=0.03, n=1300),
                 dict(num_leaves=15, min_child_samples=40, learning_rate=0.05, n=800),   # 2차: 단순한 쪽이 좋아 더 탐색
                 dict(num_leaves=31, min_child_samples=80, learning_rate=0.05, n=800),
                 dict(num_leaves=31, min_child_samples=20, learning_rate=0.05, n=800),
                 dict(num_leaves=31, min_child_samples=40, learning_rate=0.05, n=400)],
    "XGBoost": [dict(max_depth=7, min_child_weight=20, learning_rate=0.05, n=800),          # 현재
                dict(max_depth=5, min_child_weight=20, learning_rate=0.05, n=800),
                dict(max_depth=9, min_child_weight=20, learning_rate=0.05, n=800),
                dict(max_depth=7, min_child_weight=5, learning_rate=0.05, n=800),
                dict(max_depth=7, min_child_weight=50, learning_rate=0.05, n=800),
                dict(max_depth=3, min_child_weight=20, learning_rate=0.05, n=800),        # 2차
                dict(max_depth=4, min_child_weight=20, learning_rate=0.05, n=800),
                dict(max_depth=5, min_child_weight=50, learning_rate=0.05, n=800),
                dict(max_depth=5, min_child_weight=20, learning_rate=0.05, n=400)],
    "RandomForest": [dict(min_samples_leaf=3, max_features=0.6, n=150),                     # 현재
                     dict(min_samples_leaf=1, max_features=0.6, n=150),
                     dict(min_samples_leaf=10, max_features=0.6, n=150),
                     dict(min_samples_leaf=3, max_features=0.33, n=150),
                     dict(min_samples_leaf=3, max_features=1.0, n=150),
                     dict(min_samples_leaf=20, max_features=0.6, n=150),                  # 2차
                     dict(min_samples_leaf=40, max_features=0.6, n=150),
                     dict(min_samples_leaf=10, max_features=0.33, n=150),
                     dict(min_samples_leaf=20, max_features=0.33, n=150)],
    "BiLSTM": [dict(hidden=48, lr=2e-3),                                                    # 현재
               dict(hidden=32, lr=2e-3),
               dict(hidden=64, lr=2e-3),
               dict(hidden=48, lr=1e-3)],
}


def _fit_tree(name: str, cfg: dict, df: pd.DataFrame):
    from ..config import SEED
    if name == "LightGBM":
        import lightgbm as lgb
        p = {**lightgbm_model.PARAMS, **{k: v for k, v in cfg.items() if k != "n"}}
        return lgb.LGBMRegressor(n_estimators=cfg["n"], **p).fit(df[FEAT_COLS], df["y"]), lightgbm_model
    if name == "XGBoost":
        from xgboost import XGBRegressor
        p = {**xgboost_model.PARAMS, **{k: v for k, v in cfg.items() if k != "n"}}
        return XGBRegressor(n_estimators=cfg["n"], **p).fit(df[FEAT_COLS], df["y"]), xgboost_model
    from sklearn.ensemble import RandomForestRegressor
    m = RandomForestRegressor(n_estimators=cfg["n"], min_samples_leaf=cfg["min_samples_leaf"],
                              max_features=cfg["max_features"], n_jobs=-1, random_state=SEED)
    return m.fit(df[FEAT_COLS].fillna(-1), df["y"]), random_forest_model


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-lstm", action="store_true")
    ap.add_argument("--keep-lstm", action="store_true", help="트리만 다시 돌리고 Bi-LSTM 결과는 기존 tuning.json 것을 씀")
    a = ap.parse_args()
    t_all = time.time()
    g = load_grid(get_engine())
    T, S = len(g.times), len(g.series)
    pos = lambda ts: int(np.searchsorted(g.times, pd.Timestamp(ts)))   # noqa: E731
    tr_end, va_a, va_b = pos(TRAIN_END), pos(VAL_START), pos(VAL_END)
    iss = np.arange(WEEK, T - 1)
    tr, va = iss[iss + H < tr_end], iss[(iss >= va_a) & (iss + H <= va_b)]
    scale = np.array([max(np.nanpercentile(g.y[s, WEEK:tr_end], 95), 0.05) for s in range(S)])
    thr = thresholds(g)["threshold_kw"]
    day_thr = thr.reindex([d.date().isoformat() for d in g.times[va]]).to_numpy()
    A = np.stack([take(g.y[0], va + h) for h in range(1, H + 1)], 1)
    stale = np.isnan(take(g.yi[0], va))

    Xtr = features(g, 0, tr, scale[0]).dropna(subset=["y", "y0"])
    Xtr = Xtr[~g.p_bad[0, Xtr["issue"].to_numpy().astype(int)]]
    Xva = features(g, 0, va, scale[0])

    def score(P: np.ndarray) -> dict:
        P = P.copy(); P[stale] = np.nan
        ok = ~np.isnan(A) & ~np.isnan(P)
        mae = float(np.mean(np.abs(P - A)[ok]))
        okr = ok.all(1) & ~np.isnan(day_thr)
        pk = peak_detection(A, np.round(P, 4), day_thr, okr)
        return {"val_mae": round(mae, 4), "tp": pk["tp"], "fn": pk["fn"], "fp": pk["fp"], "n": int(ok.sum())}

    def to_mat(df, col):
        out = np.full((len(va), H), np.nan)
        out[np.searchsorted(va, df["issue"].to_numpy()), df["h"].to_numpy().astype(int) - 1] = col
        return out * scale[0]

    res = {}
    for name in ("LightGBM", "XGBoost", "RandomForest"):
        res[name] = []
        for i, cfg in enumerate(GRID[name]):
            t0 = time.time()
            m, mod = _fit_tree(name, cfg, Xtr)
            r = {"cfg": cfg, "current": i == 0, **score(to_mat(Xva, mod.predict(m, Xva))), "sec": round(time.time() - t0)}
            res[name].append(r)
            print(name, r, flush=True)

    if a.keep_lstm and (MODEL_DIR / "tuning.json").exists():
        old = json.loads((MODEL_DIR / "tuning.json").read_text(encoding="utf-8"))["models"].get("BiLSTM")
        if old:
            res["BiLSTM"] = old["rows"]
    elif not a.skip_lstm:
        cat = lambda parts: tuple(np.concatenate(z) for z in zip(*parts))   # noqa: E731
        def tr_arr(s, ii):
            x = bilstm_model.lstm_arrays(g, s, ii, scale[s]); x[2][g.p_bad[s][ii]] = np.nan
            return x
        trp = cat([tr_arr(0, tr)] * 8 + [tr_arr(s, tr[::3]) for s in range(1, S)])
        vap = cat([bilstm_model.lstm_arrays(g, 0, va, scale[0])] +
                  [bilstm_model.lstm_arrays(g, s, va[::4], scale[s]) for s in range(1, S)])
        v0 = bilstm_model.lstm_arrays(g, 0, va, scale[0])
        res["BiLSTM"] = []
        for i, cfg in enumerate(GRID["BiLSTM"]):
            t0 = time.time()
            net = bilstm_model.BiLSTM(S, hidden=cfg["hidden"]).fit(trp, vap, lr=cfg["lr"])
            r = {"cfg": cfg, "current": i == 0, **score(net.predict(v0) * scale[0]), "sec": round(time.time() - t0)}
            res["BiLSTM"].append(r)
            print("BiLSTM", r, flush=True)

    for name, rows in res.items():
        cur = rows[0]["val_mae"]
        best = min(rows, key=lambda r: r["val_mae"])
        gain = (cur - best["val_mae"]) / cur * 100
        app = best["cfg"] if gain >= 2 else rows[0]["cfg"]
        for r in rows:
            r["applied"] = r["cfg"] == app
        res[name] = {"rows": rows, "best": best["cfg"], "gain_pct": round(gain, 1),
                     "decision": "현재 설정 유지 (차이 2% 미만)" if gain < 2 else "검증 MAE가 가장 작은 설정으로 교체"}
    out = {"period": {"train_to": TRAIN_END, "val": [VAL_START, VAL_END], "val_issues": int(len(va))},
           "rule": "학습기간으로 학습, 검증기간 건물 합계 MAE 비교 (시험기간 미사용). 2% 이상 좋으면 교체 후보.",
           "models": res}
    (MODEL_DIR / "tuning.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"완료: {MODEL_DIR / 'tuning.json'} · {time.time() - t_all:.0f}초")


if __name__ == "__main__":
    main()
