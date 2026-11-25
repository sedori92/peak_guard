"""[5] 실시간 연결 ② 가장 최근 실측 시각 기준으로 다음 1시간(15분×4) 예측을 만듭니다.

연결 ─ 화면: ① [실시간 연결] 뒤 그래프·판정·Top5
       API : POST /api/live/predict
       DB  : measurement (읽기), live_forecast (쓰기)

화면 왼쪽 위 '방식 선택'과 '예측모델 선택'에서 고른 조합을 그대로 씁니다.
  - 채널별 합산 후 예측 : 저장된 건물 직접 모델로 건물 합계를 바로 예측
  - 채널별 예측 후 합산 : 저장된 채널 모델로 채널 61개를 예측한 뒤 같은 시각끼리 더함
  - 호실 숫자(호실 순위·Top5)는 두 방식 모두 같은 모델의 채널 예측을 씁니다.

피크 기준선:
  - 예측하는 날의 직전 30일 건물 합계 P95 (95번째 백분위수). 직전 30일에 7일치 이상 실측이 있어야 계산합니다.
  - 7일치가 안 되면(빠진 구간이 길 때) 마지막으로 계산된 기준을 '임시 기준'으로 씁니다.
결과는 '실시간 예측 기록 장부'(live_forecast)에 남깁니다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sqlalchemy import text

from ..config import DAY, FLOORS, H, MODEL_DIR, PEAK_PERCENTILE, PEAK_WINDOW_DAYS, SUM_BASE
from ..database.tables_ops import live_forecast
from ..models import bilstm_model, lightgbm_model, random_forest_model, xgboost_model
from ..models.features import features
from ..models.grid import load_grid
from ..models.thresholds import current_peak, options as thr_options

TREE = {"LightGBM": lightgbm_model, "XGBoost": xgboost_model, "RandomForest": random_forest_model}
_GRID: dict = {}


def grid(engine):
    """15분 격자(시계열 × 시각)를 만들고, 측정값이 바뀌지 않았으면 다시 만들지 않습니다."""
    with engine.connect() as con:
        key = tuple(con.execute(text("SELECT COUNT(*), MAX(measured_at) FROM measurement")).fetchone())
    if _GRID.get("key") != key:
        _GRID.update(key=key, g=load_grid(engine))
    return _GRID["g"]


def _load(base: str):
    if base == "BiLSTM":
        import torch
        ck = torch.load(MODEL_DIR / "st_BiLSTM.pt", weights_only=False)
        net = bilstm_model.BiLSTM(len(ck["series"]), hidden=ck["params"]["hidden"], emb=ck["params"]["emb"])
        net.net.load_state_dict(ck["state"])
        return {"net": net, "scale": ck["scale"], "series": ck["series"]}
    import joblib
    return joblib.load(MODEL_DIR / f"st_{base}.joblib")


def _threshold(g, i: int, engine) -> tuple[float | None, str]:
    """예측하는 날의 피크 기준선 (직전 30일 P95). 부족하면 마지막 기준을 임시로."""
    day = g.times[min(i + 1, len(g.times) - 1)].normalize()
    tot = pd.Series(g.y[0], index=g.times)
    w = tot[(tot.index >= day - pd.Timedelta(days=PEAK_WINDOW_DAYS)) & (tot.index < day)].dropna()
    if len(w) >= 7 * DAY:
        return float(np.percentile(w, PEAK_PERCENTILE)), f"{day:%m-%d} 직전 30일 P95"
    with engine.connect() as con:
        r = con.execute(text("SELECT date, threshold_kw FROM peak_threshold WHERE threshold_kw IS NOT NULL "
                             "ORDER BY date DESC LIMIT 1")).fetchone()
    return (float(r[1]), f"임시 기준 ({r[0]} 기준값, 최근 실측 7일치 부족)") if r else (None, "기준 없음")


def predict_latest(engine, model_id: str) -> dict:
    """버튼 [실시간 연결]의 2단계. model_id 예: LightGBM(합산 후 예측) / LGBM_SUM(예측 후 합산)."""
    base = SUM_BASE.get(model_id, model_id)
    g = grid(engine)
    ok = np.where(~np.isnan(g.y[0]))[0]                  # 96개 채널이 모두 들어온 가장 최근 칸
    if not len(ok):
        raise ValueError("측정값이 없습니다.")
    i = int(ok[-1])
    m = _load(base)
    if list(m["series"]) != list(g.series):
        raise ValueError("활성 채널 구성이 학습 때와 다릅니다. run.py 3 으로 다시 학습하세요.")
    scale, S = m["scale"], len(g.series)
    iss = np.array([i])
    P = {}
    if base == "BiLSTM":
        for s in range(S):
            P[s] = m["net"].predict(bilstm_model.lstm_arrays(g, s, iss, scale[s]))[0] * scale[s]
    else:
        mod = TREE[base]
        x0 = features(g, 0, iss, scale[0]).sort_values("h")
        P[0] = mod.predict(m["TOTAL"], x0) * scale[0]
        xc = pd.concat([features(g, s, iss, scale[s]) for s in range(1, S)], ignore_index=True)
        pc = mod.predict(m["CHANNEL"], xc)
        for s, ix in xc.groupby("series").indices.items():
            d = xc.iloc[ix]
            P[int(s)] = pc[ix][np.argsort(d["h"].to_numpy())] * scale[int(s)]
    total = np.sum([P[s] for s in range(1, S)], axis=0) if model_id in SUM_BASE else P[0]
    thr, thr_note = _threshold(g, i, engine)
    k = int(np.argmax(total))
    t0 = g.times[i]
    rooms, proom, room_thr, rooms_pred = {}, {}, {}, {}
    with engine.connect() as con:
        ch = pd.read_sql(text("SELECT channel_id, meter_id FROM channel"), con)
    room_ids = ch[ch.meter_id.isin(FLOORS)].channel_id.sort_values().tolist()
    idx = {c: j for j, c in enumerate(g.series)}
    w0 = max(0, i - PEAK_WINDOW_DAYS * DAY)
    for cid in room_ids:
        key = cid.replace("-CH", "-")
        j = idx.get(cid)
        rooms[key] = round(float(np.nan_to_num(g.y[j][i])) if j is not None else 0.0, 3)
        pv = P[j] if j is not None else np.zeros(H)
        rooms_pred[key] = [round(float(v), 3) for v in pv]
        proom[key] = round(float(pv[k]), 3)
        hist = g.y[j][w0:i + 1] if j is not None else np.array([])
        hist = hist[~np.isnan(hist)]
        room_thr[key] = round(float(np.percentile(hist, PEAK_PERCENTILE)), 3) if len(hist) >= 7 * DAY else None
    now = pd.Timestamp.now()
    rows = [{"model_name": model_id, "series_id": "TOTAL", "issued_at": t0.to_pydatetime(), "horizon_min": 15 * (h + 1),
             "predicted_kw": float(total[h]), "created_at": now.to_pydatetime()} for h in range(H)]
    with engine.begin() as con:
        con.execute(live_forecast.delete().where((live_forecast.c.model_name == model_id)
                                                 & (live_forecast.c.issued_at == t0.to_pydatetime())))
        con.execute(live_forecast.insert(), rows)
    # 화면 그래프용: 오늘(최근 실측 시각이 속한 날) 0시부터 지금까지 건물 합계·호실 실측
    d0 = int(np.searchsorted(g.times, t0.normalize()))
    hist = [{"t": f"{g.times[x]:%H:%M}", "y": None if np.isnan(g.y[0][x]) else round(float(g.y[0][x]), 2),
             "rooms": {c.replace("-CH", "-"): (0.0 if idx.get(c) is None or np.isnan(g.y[idx[c]][x]) else round(float(g.y[idx[c]][x]), 3))
                       for c in room_ids}} for x in range(d0, i + 1)]
    return {"t": f"{t0:%H:%M}", "time": f"{t0:%Y-%m-%d %H:%M}", "date": f"{t0:%Y-%m-%d}", "hist": hist, "y": round(float(g.y[0][i]), 2),
            "p": round(float(np.nan_to_num(g.p[0][i])), 2), "pred": [round(float(v), 2) for v in total],
            "thr": None if thr is None else round(thr, 2), "thr_note": thr_note, "rooms": rooms, "proom": proom,
            "rooms_pred": rooms_pred, "room_thr": room_thr,
            "thr_options": thr_options(pd.Series(g.y[0], index=g.times), g.times[min(i + 1, len(g.times) - 1)].normalize()),
            "peak_now": current_peak(pd.Series(g.y[0], index=g.times), t0.normalize()),
            "common": round(float(np.nan_to_num(g.y[0][i])) - sum(rooms.values()), 2),
            "age_min": int((now.floor("15min") - t0) / pd.Timedelta("1min"))}
