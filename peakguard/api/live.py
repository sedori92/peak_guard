"""[5] API — 화면 '예측 및 경보' 오른쪽 위 [실시간 연결] 버튼

연결 ─ 화면: ① 오른쪽 위 [실시간 연결] 버튼 (screen1_predict.js S.goLive)
       API : GET /api/live/status · POST /api/live/sync · POST /api/live/predict
       DB  : measurement·weather_15m (쓰기), live_forecast (쓰기)

  POST /api/live/sync                 빠진 구간 채우기 (새 데이터 폴더·기업 DB → 측정값 장부, 중복 없이)
  POST /api/live/predict?model=       가장 최근 실측 시각 기준 다음 1시간 예측 (방식·모델 조합)
  GET  /api/live/status               마지막 실측 시각, 오늘까지 빠진 15분 칸 수

버튼을 누르면 화면이 sync → predict 순서로 부릅니다. 새 데이터가 없으면 마지막 실측(2026-11-02 16:30) 기준으로 예측하고
"새 데이터 없음"을 알려 줍니다. 빈 시각을 가짜 값으로 채우지 않습니다.
"""
from __future__ import annotations

import os

import pandas as pd
from fastapi import APIRouter, HTTPException

from ..live.predictor import predict_latest
from ..live.sync import INCOMING, last_measured, sync
from .common import check_model, engine

router = APIRouter(prefix="/api/live", tags=["live"])


@router.get("/status")
def status():
    last = last_measured(engine)
    now = pd.Timestamp.now().floor("15min")
    return {"last_measured": last, "now": f"{now:%Y-%m-%d %H:%M}",
            "missing_slots_to_now": int((now - pd.Timestamp(last)) / pd.Timedelta("15min")) if last else None,
            "incoming_folder": "data/incoming", "waiting_files": sorted(p.name for p in INCOMING.glob("*.csv")),
            "api_configured": bool(os.getenv("LIVE_DB_URL") and os.getenv("LIVE_DB_QUERY")),
            "weather_last": _wlast(), "weather_api_configured": bool(os.getenv("WEATHER_API_URL"))}


def _wlast():
    from ..collect.weather import last_weather
    return last_weather(engine)   # 기업 API(DB) 연결 설정 여부


@router.post("/sync")
def do_sync():
    return sync(engine)


@router.post("/predict")
def do_predict(model: str = "LightGBM"):
    check_model(model)
    try:
        return predict_latest(engine, model)
    except ValueError as e:
        raise HTTPException(409, str(e))
