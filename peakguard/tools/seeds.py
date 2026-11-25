"""[3-보조] 시드(난수 시작값)별 흔들림 확인 — 설정값 판단이 '우연'이 아닌지 검사

    python -m peakguard.tools.seeds              # 트리 3종 × 처음/현재 설정 × 시드 5개 + Bi-LSTM (CPU 2코어 약 40분)
    python -m peakguard.tools.seeds --skip-lstm

연결 ─ 화면: — (결과는 docs/TUNING.md '시드별 흔들림' 표, peakguard.tools.model_report 가 씀)
       API : —
       DB  : measurement (읽기), models/seeds.json (쓰기)

무엇을 하나
  - 같은 설정을 시드만 바꿔 여러 번 학습 → 검증기간 MAE 의 평균·최소·최대
  - '처음 설정 → 현재 설정' 교체가 시드 흔들림보다 큰 개선인지 확인 (판단 기준)
  - 시험기간 성적(MAE·피크 맞힘)도 시드별로 기록 — 고르는 데는 쓰지 않고 '흔들림 폭' 보고용
대상: 채널 합산 후 예측(건물 합계를 바로 예측)의 4개 모델. tune.py 와 같은 학습·검증 자료.
같은 시드로 두 번 학습하면 결과가 완전히 같은지도 한 번 확인 (재현성).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import time

import numpy as np
import pandas as pd

from ..config import H, MODEL_DIR, SEED, TEST_END, TEST_START, TRAIN_END, VAL_END, VAL_START, WEEK
from ..database.connection import get_engine
from ..evaluation.metrics import peak_detection
from ..models import bilstm_model
from ..models.features import features
from ..models.grid import load_grid, take, thresholds
from .tune import GRID, _fit_tree

SEEDS = [SEED, 7, 123, 2026, 31415]
LSTM_ALT_SEEDS = 3                                   # Bi-LSTM 비교 설정(학습률 0.001)은 시간 때문에 시드 3개


def _applied(name: str) -> tuple[dict, dict]:
    """(처음 설정, 현재 적용 설정) — models/tuning.json 의 applied 표시 기준."""
    T = json.loads((MODEL_DIR / "tuning.json").read_text(encoding="utf-8"))["models"][name]["rows"]
    first = T[0]["cfg"]
    cur = next((r["cfg"] for r in T if r.get("applied")), first)
    if name == "BiLSTM":                                 # Bi-LSTM 은 현재 = 처음, 비교 대상 = 검증 MAE 가장 작은 설정
        alt = min(T, key=lambda r: r["val_mae"])["cfg"]
        return cur, alt
    return first, cur


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-lstm", action="store_true")
    a = ap.parse_args()
    t_all = time.time()
    g = load_grid(get_engine())
    T, S = len(g.times), len(g.series)
    pos = lambda ts: int(np.searchsorted(g.times, pd.Timestamp(ts)))   # noqa: E731
    iss = np.arange(WEEK, T - 1)
    tr = iss[iss + H < pos(TRAIN_END)]
    # 시험기간은 세 번째 화면과 같은 공통 표본(모든 모델이 예측한 1,155개 시점)만 → 화면 숫자와 같은 기준
    from ..api.common import q
    fc = q("SELECT model_name, issued_at, COUNT(*) n FROM st_forecast WHERE series_id='TOTAL' GROUP BY 1, 2")
    full = fc[fc.n == H].groupby("issued_at").model_name.nunique()
    common = pd.DatetimeIndex(pd.to_datetime(full[full == full.max()].index))
    te = iss[(iss >= pos(TEST_START)) & (iss <= pos(TEST_END))]
    te = te[np.isin(g.times[te], common)]
    sets = {"val": iss[(iss >= pos(VAL_START)) & (iss + H <= pos(VAL_END))], "test": te}
    scale = np.array([max(np.nanpercentile(g.y[s, WEEK:pos(TRAIN_END)], 95), 0.05) for s in range(S)])
    thr = thresholds(g)["threshold_kw"]
    ctx = {}
    for k, v in sets.items():
        ctx[k] = dict(v=v, A=np.stack([take(g.y[0], v + h) for h in range(1, H + 1)], 1),
                      stale=np.isnan(take(g.yi[0], v)),
                      thr=thr.reindex([d.date().isoformat() for d in g.times[v]]).to_numpy().round(2),
                      X=features(g, 0, v, scale[0]))
    Xtr = features(g, 0, tr, scale[0]).dropna(subset=["y", "y0"])
    Xtr = Xtr[~g.p_bad[0, Xtr["issue"].to_numpy().astype(int)]]

    def score(k, P):
        c = ctx[k]; P = P.copy(); P[c["stale"]] = np.nan
        ok = ~np.isnan(c["A"]) & ~np.isnan(P)
        okr = ok.all(1) & ~np.isnan(c["thr"])
        pk = peak_detection(c["A"], np.round(P, 4), c["thr"], okr)
        return {"mae": round(float(np.mean(np.abs(P - c["A"])[ok])), 4), "tp": pk["tp"], "fn": pk["fn"], "fp": pk["fp"]}

    def to_mat(k, df, col):
        v = ctx[k]["v"]; out = np.full((len(v), H), np.nan)
        out[np.searchsorted(v, df["issue"].to_numpy()), df["h"].to_numpy().astype(int) - 1] = col
        return out * scale[0]

    def tree_run(name, cfg, seed):
        c = {**cfg}
        if name == "RandomForest":
            from sklearn.ensemble import RandomForestRegressor
            from ..models import random_forest_model as mod
            m = RandomForestRegressor(n_estimators=c["n"], min_samples_leaf=c["min_samples_leaf"], max_features=c["max_features"],
                                      n_jobs=-1, random_state=seed).fit(Xtr[mod.FEAT_COLS].fillna(-1), Xtr["y"])
        else:
            import importlib
            mod = importlib.import_module(f"peakguard.models.{'lightgbm_model' if name == 'LightGBM' else 'xgboost_model'}")
            base = {**mod.PARAMS, **{k: v for k, v in c.items() if k != "n"}, "random_state": seed}
            if name == "LightGBM":
                import lightgbm as lgb
                m = lgb.LGBMRegressor(n_estimators=c["n"], **base).fit(Xtr[mod.FEAT_COLS], Xtr["y"])
            else:
                from xgboost import XGBRegressor
                m = XGBRegressor(n_estimators=c["n"], **base).fit(Xtr[mod.FEAT_COLS], Xtr["y"])
        out = {}
        for k in ("val", "test"):
            p = mod.predict(m, ctx[k]["X"])
            out[k] = score(k, to_mat(k, ctx[k]["X"], p))
            if k == "val":
                out["hash"] = hashlib.md5(np.round(p, 8).tobytes()).hexdigest()[:10]
        return out

    res = {"seeds": SEEDS, "models": {}}
    for name in ("LightGBM", "XGBoost", "RandomForest"):
        first, cur = _applied(name)
        rows = {"처음": [], "현재": []}
        for label, cfg in (("처음", first), ("현재", cur)):
            for sd in SEEDS:
                r = tree_run(name, cfg, sd); r["seed"] = sd; rows[label].append(r)
                print(name, label, sd, r, flush=True)
        again = tree_run(name, cur, SEED)
        res["models"][name] = {"cfg_first": first, "cfg_current": cur, "rows": rows,
                               "reproducible": again["hash"] == rows["현재"][0]["hash"]}

    if not a.skip_lstm:
        cat = lambda parts: tuple(np.concatenate(z) for z in zip(*parts))   # noqa: E731
        def tr_arr(s, ii):
            x = bilstm_model.lstm_arrays(g, s, ii, scale[s]); x[2][g.p_bad[s][ii]] = np.nan
            return x
        trp = cat([tr_arr(0, tr)] * 8 + [tr_arr(s, tr[::3]) for s in range(1, S)])
        vap = cat([bilstm_model.lstm_arrays(g, 0, sets["val"], scale[0])] +
                  [bilstm_model.lstm_arrays(g, s, sets["val"][::4], scale[s]) for s in range(1, S)])
        arr = {k: bilstm_model.lstm_arrays(g, 0, sets[k], scale[0]) for k in sets}
        cur, alt = _applied("BiLSTM")
        rows = {"현재": [], "비교": []}
        for label, cfg, seeds in (("현재", cur, SEEDS), ("비교", alt, SEEDS[:LSTM_ALT_SEEDS])):
            for sd in seeds:
                t0 = time.time()
                net = bilstm_model.BiLSTM(S, hidden=cfg["hidden"], seed=sd).fit(trp, vap, lr=cfg["lr"])
                r = {k: score(k, net.predict(arr[k]) * scale[0]) for k in sets}
                r["seed"] = sd; r["sec"] = round(time.time() - t0)
                rows[label].append(r)
                print("BiLSTM", label, sd, r, flush=True)
        res["models"]["BiLSTM"] = {"cfg_current": cur, "cfg_alt": alt, "rows": rows}

    def summ(rs, k, f):
        v = [r[k][f] for r in rs]
        return {"mean": round(float(np.mean(v)), 4), "min": min(v), "max": max(v)}
    for name, R in res["models"].items():
        keys = list(R["rows"])
        R["summary"] = {lab: {k: {"mae": summ(R["rows"][lab], k, "mae"), "tp": summ(R["rows"][lab], k, "tp"),
                                  "fp": summ(R["rows"][lab], k, "fp")} for k in ("val", "test")} for lab in keys}
        a_, b_ = R["summary"][keys[0]]["val"]["mae"], R["summary"][keys[1]]["val"]["mae"]
        spread = max(a_["max"] - a_["min"], b_["max"] - b_["min"])
        diff = a_["mean"] - b_["mean"]                       # + 면 뒤쪽 설정(현재 또는 비교)이 평균적으로 좋음
        R["judge"] = {"diff_mean": round(diff, 4), "spread": round(spread, 4),
                      "diff_pct": round(diff / a_["mean"] * 100, 1),
                      "clear": bool(abs(diff) > spread)}
    (MODEL_DIR / "seeds.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"완료: {MODEL_DIR / 'seeds.json'} · {time.time() - t_all:.0f}초")


if __name__ == "__main__":
    main()
