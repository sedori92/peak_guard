"""[5] API · [6] 화면 연결 — 보조화면 '채널합산(Random Forest) 비교'

    GET /api/sumcompare?date=YYYY-MM-DD

기업 권장 방식(건물 전체 직접 예측)과 비교 방식(채널 예측을 같은 시각끼리 더한 값)을
같은 시험기간·같은 표본으로 비교하기 위한 데이터:
  - 성능: 검증·시험 MAE, 편향(예측−실제), 선행시간별 MAE, 피크 오분류표
  - 그날 건물 합계 실측과 모델별 15분×4 예측 (건물 직접 vs 채널합산)
  - 호실 채널별 Random Forest 예측과 채널별 시험 오차(Random Forest·LightGBM)
"""
from __future__ import annotations

import pandas as pd
from fastapi import APIRouter, HTTPException, Query

from ..config import DISPLAY, FLOORS, METHOD
from .common import info, num, q, room_key, to_local
from .dashboard import performance

router = APIRouter(prefix="/api", tags=["compare"])
LINES = ["RandomForest", "RF_SUM", "LightGBM", "LGBM_SUM"]


@router.get("/sumcompare")
def sum_compare(date: str = Query(..., description="YYYY-MM-DD")):
    inf = info()
    t0, t1 = f"{date} 00:00:00", f"{date} 23:45:00"
    fc = q("""SELECT model_name, series_id, issued_at, horizon_min, predicted_kw FROM st_forecast
              WHERE issued_at BETWEEN :a AND :b AND (series_id='TOTAL' OR model_name='RandomForest')""", a=t0, b=t1)
    if fc.empty:
        raise HTTPException(404, f"{date} 예측이 없습니다 (시험기간 {inf['test_start']} ~ {inf['test_end']}).")
    fc["t"] = to_local(fc.issued_at).dt.strftime("%H:%M")
    tot = fc[fc.series_id == "TOTAL"]
    lines = {}
    for m in LINES:
        g = tot[tot.model_name == m]
        lines[m] = {t: [num(v, 2) for v in x.sort_values("horizon_min").predicted_kw] for t, x in g.groupby("t")}
    rf = fc[(fc.model_name == "RandomForest") & (fc.series_id != "TOTAL")]
    rooms = {room_key(cid): {t: [num(v, 3) for v in x.sort_values("horizon_min").predicted_kw] for t, x in g.groupby("t")}
             for cid, g in rf.groupby("series_id")}

    m = q("""SELECT measured_at, SUM(demand_kw) AS y, COUNT(*) AS n FROM measurement
             WHERE measured_at BETWEEN :a AND :b GROUP BY measured_at""", a=t0, b=t1)
    n_ch = int(q("SELECT COUNT(*) AS n FROM channel").n[0])
    m = m[m.n >= n_ch]
    actual = dict(zip(to_local(m.measured_at).dt.strftime("%H:%M"), [num(v, 2) for v in m.y]))

    ce = q("SELECT model_name, series_id, mae, bias, n FROM st_channel_eval WHERE model_name IN ('RandomForest','LightGBM')")
    room_eval = {}
    for r in ce.itertuples():
        if r.series_id.split("-")[0] in FLOORS:
            room_eval.setdefault(room_key(r.series_id), {})[r.model_name] = {"mae": num(r.mae, 4), "bias": num(r.bias, 4)}
    perf, peak = performance(inf["models"])
    return {"date": date, "lines": lines, "names": {m: DISPLAY.get(m, m) for m in LINES},
            "method": {DISPLAY.get(m, m): METHOD.get(m, "") for m in inf["models"]},
            "actual": actual, "rooms": rooms, "room_eval": room_eval, "perf": perf, "peak": peak,
            "splits": inf.get("splits"), "inactive": [room_key(c) for c in inf["inactive"]]}
