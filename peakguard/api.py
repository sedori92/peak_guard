"""피크가드 조회 API + 관리자 화면 (FastAPI)

실행
    uvicorn peakguard.api:app --port 8000        # 브라우저에서 http://localhost:8000

엔드포인트 (요구사항 FR-AD-002)
    GET /                       관리자 화면 (web/dashboard.html)
    GET /api/days               재생 가능한 날짜·모델 목록
    GET /api/replay?date=&model=  하루치 재생 데이터 (화면이 사용하는 DATA 형식)
    GET /api/forecast?issued_at=&model=  특정 발행 시각의 15분×4 예측 (전체 + 호실)
    GET /api/eval               모델 성능 (전체·채널, 선행시간별)
"""
from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path

import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import text

from .db import get_engine

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web" / "dashboard.html"
FLOORS = {"M002": 1, "M004": 2, "M006": 3, "M008": 4}
DISPLAY = {"전일 동일시각": "전일 동일시각", "최근값 유지": "최근값 유지", "RandomForest": "Random Forest",
           "LightGBM": "LightGBM", "BiLSTM": "Bi-LSTM"}
WEEK = "월화수목금토일"

app = FastAPI(title="PeakGuard API", version="1.0")
engine = get_engine()


def q(sql: str, **params) -> pd.DataFrame:
    with engine.connect() as con:
        return pd.read_sql(text(sql), con, params=params)


@lru_cache(maxsize=1)
def info() -> dict:
    d = q("SELECT value FROM st_meta WHERE key='info'")
    if d.empty:
        raise HTTPException(503, "예측 결과가 없습니다. 먼저 python -m peakguard.shortterm 을 실행하세요.")
    return json.loads(d.value[0])


def room_key(cid: str) -> str:          # 'M004-CH03' → 'M004-03' (화면 표기)
    return cid.replace("-CH", "-")


def num(v, d=3):
    return None if v is None or (isinstance(v, float) and math.isnan(v)) else round(float(v), d)


def check_model(model: str) -> str:
    if model not in info()["models"]:
        raise HTTPException(400, f"알 수 없는 모델: {model} (가능: {', '.join(info()['models'])})")
    return model


@app.get("/")
def index():
    return FileResponse(WEB)


@app.get("/api/days")
def days():
    d = q("""SELECT substr(issued_at,1,10) AS date, COUNT(DISTINCT issued_at) AS n
             FROM st_forecast WHERE model_name='LightGBM' AND series_id='TOTAL' GROUP BY 1 ORDER BY 1""")
    d = d[d.n >= 48]                                   # 하루 절반 이상 예측이 있는 날만
    return {"days": d.to_dict("records"), "models": [{"id": m, "name": DISPLAY.get(m, m)} for m in info()["models"]],
            "default_model": "LightGBM", "test_start": info()["test_start"], "test_end": info()["test_end"]}


@app.get("/api/eval")
def evaluation():
    ev = q("SELECT * FROM st_eval ORDER BY scope, model_name, horizon_min")
    pk = q("SELECT * FROM st_peak_eval")
    return {"metrics": ev.to_dict("records"), "peak_detection": pk.to_dict("records"),
            "test_start": info()["test_start"], "test_end": info()["test_end"],
            "note": "MAPE 는 실제값이 전체 1kW·채널 0.1kW 미만인 칸 제외. 공통 표본(모든 모델이 예측한 칸)만 비교."}


@app.get("/api/forecast")
def forecast(issued_at: str = Query(..., description="예: 2026-10-28 14:00:00"), model: str = "LightGBM"):
    check_model(model)
    d = q("SELECT series_id, horizon_min, predicted_kw FROM st_forecast WHERE model_name=:m AND issued_at=:t",
          m=model, t=issued_at)
    if d.empty:
        raise HTTPException(404, f"{issued_at} 발행 예측이 없습니다.")
    t0 = pd.Timestamp(issued_at)
    out = {}
    for sid, g in d.groupby("series_id"):
        out[sid] = [{"target_time": str(t0 + pd.Timedelta(minutes=int(r.horizon_min))), "horizon_min": int(r.horizon_min),
                     "predicted_kw": num(r.predicted_kw)} for r in g.sort_values("horizon_min").itertuples()]
    return {"model": model, "issued_at": issued_at, "unit": "kW", "series": out}


@app.get("/api/replay")
def replay(date: str = Query(..., description="YYYY-MM-DD"), model: str = "LightGBM"):
    """화면(web/dashboard.html)이 쓰는 하루치 재생 데이터."""
    check_model(model)
    inf = info()
    rooms = inf["rooms"]
    inactive = set(inf["inactive"])
    t0, t1 = f"{date} 00:00:00", f"{date} 23:45:00"

    fc = q("""SELECT series_id, issued_at, horizon_min, predicted_kw FROM st_forecast
              WHERE model_name=:m AND issued_at BETWEEN :a AND :b""", m=model, a=t0, b=t1)
    if fc.empty:
        raise HTTPException(404, f"{date} {model} 예측이 없습니다. (/api/days 참고)")
    fc["issued_at"] = pd.to_datetime(fc.issued_at).dt.strftime("%H:%M")
    tot = fc[fc.series_id == "TOTAL"].pivot_table(index="issued_at", columns="horizon_min", values="predicted_kw")
    room_fc = fc[fc.series_id != "TOTAL"].set_index(["issued_at", "series_id", "horizon_min"]).predicted_kw

    m = q("""SELECT m.channel_id, c.meter_id, m.measured_at, m.demand_kw, m.power_kw
             FROM measurement m JOIN channel c ON c.channel_id = m.channel_id
             WHERE m.measured_at BETWEEN :a AND :b""", a=t0, b=t1)
    m["measured_at"] = pd.to_datetime(m.measured_at)
    if getattr(m.measured_at.dt, "tz", None) is not None:
        m["measured_at"] = m.measured_at.dt.tz_convert("Asia/Seoul").dt.tz_localize(None)
    m["t"] = m.measured_at.dt.strftime("%H:%M")
    n_ch = int(q("SELECT COUNT(*) AS n FROM channel").n[0])
    agg = m.groupby("t").agg(y=("demand_kw", "sum"), p=("power_kw", "sum"), n=("demand_kw", "size"))
    agg.loc[agg.n < n_ch, ["y", "p"]] = float("nan")              # 일부 채널 미수신이면 합산 표시 안 함
    common = m[~m.meter_id.isin(FLOORS)].groupby("t").demand_kw.sum()
    room_now = m[m.meter_id.isin(FLOORS)].pivot_table(index="t", columns="channel_id", values="demand_kw")

    w = q("SELECT measured_at, temperature_c FROM weather_15m WHERE measured_at BETWEEN :a AND :b", a=t0, b=t1) \
        if _has_table("weather_15m") else pd.DataFrame(columns=["measured_at", "temperature_c"])
    temp = dict(zip(pd.to_datetime(w.measured_at).dt.strftime("%H:%M"), w.temperature_c))
    th = q("SELECT threshold_kw FROM peak_threshold WHERE date=:d", d=date)
    thr = float(th.threshold_kw[0]) if len(th) else None

    ex = q("SELECT issued_at, feature, contribution_kw FROM st_explain WHERE issued_at BETWEEN :a AND :b", a=t0, b=t1)
    ex["issued_at"] = pd.to_datetime(ex.issued_at).dt.strftime("%H:%M")
    lg = q("""SELECT issued_at, predicted_kw FROM st_forecast WHERE model_name='LightGBM' AND series_id='TOTAL'
              AND horizon_min=60 AND issued_at BETWEEN :a AND :b""", a=t0, b=t1)
    lg_pred = dict(zip(pd.to_datetime(lg.issued_at).dt.strftime("%H:%M"), lg.predicted_kw))

    frames = []
    for t in tot.index:
        pred = [tot.loc[t].get(h) for h in (15, 30, 45, 60)]
        if t not in agg.index or any(v is None or math.isnan(v) for v in pred) or math.isnan(agg.loc[t, "y"]):
            continue
        k = int(max(range(4), key=lambda i: pred[i]))
        rooms_now, proom = {}, {}
        for cid in rooms:
            now = room_now.loc[t, cid] if (t in room_now.index and cid in room_now.columns) else float("nan")
            now = 0.0 if math.isnan(now) else float(now)
            rooms_now[room_key(cid)] = round(now, 3)
            if cid in inactive:
                pv = 0.0
            else:
                pv = room_fc.get((t, cid, 15 * (k + 1)), now)
            proom[room_key(cid)] = round(float(pv), 3)
        e = ex[ex.issued_at == t].copy()
        e = e.reindex(e.contribution_kw.abs().sort_values(ascending=False).index).head(7)
        frames.append({
            "t": t, "y": num(agg.loc[t, "y"], 2), "p": num(agg.loc[t, "p"], 2),
            "temp": num(temp.get(t), 1), "pred": [num(v, 2) for v in pred], "thr": num(thr, 2),
            "rooms": rooms_now, "common": num(common.get(t, 0.0), 2), "proom": proom,
            "shap": [[r.feature, num(r.contribution_kw, 2)] for r in e.itertuples()],
            "shap_base": num(inf["shap_base"], 2), "shap_pred": num(lg_pred.get(t), 2),
        })
    if not frames:
        raise HTTPException(404, f"{date} 은 실측 또는 예측이 비어 있어 재생할 수 없습니다.")

    ev = q("SELECT * FROM st_eval WHERE scope='TOTAL'")
    perf = {}
    for name in inf["models"]:
        e = ev[ev.model_name == name].set_index("horizon_min")
        if 0 not in e.index:
            continue
        perf[DISPLAY.get(name, name)] = {"mae": num(e.loc[0, "mae"]), "rmse": num(e.loc[0, "rmse"]),
                                         "mape": num(e.loc[0, "mape"]),
                                         "by_h": [num(e.loc[h, "mae"]) for h in (15, 30, 45, 60)], "n": int(e.loc[0, "n"])}
    pk = q("SELECT * FROM st_peak_eval")
    peak = {DISPLAY.get(r.model_name, r.model_name): {"precision": num(r.precision), "recall": num(r.recall),
            "f1": num(r.f1), "tp": int(r.tp), "fp": int(r.fp), "fn": int(r.fn)} for r in pk.itertuples()}

    d = pd.Timestamp(date)
    return {"day": f"{date}({WEEK[d.dayofweek]})", "date": date, "model": DISPLAY.get(model, model), "model_id": model,
            "frames": frames, "imp": inf["importance"], "perf": perf, "peak": peak, "floors": FLOORS,
            "test_start": inf["test_start"], "test_end": inf["test_end"],
            "thr_method": "직전 30일 전체 합산 15분 수요전력 P95"}


def _has_table(name: str) -> bool:
    with engine.connect() as con:
        return name in engine.dialect.get_table_names(con)
