"""[2] DB 구축 — 기상·단기 예측·평가 테이블 정의 (db/schema.sql 8~14번과 동일)"""
from __future__ import annotations

from sqlalchemy import (Column, DateTime, Float, Integer, MetaData, PrimaryKeyConstraint, String, Table,
                        Text)

meta = MetaData()

weather_15m = Table(  # 기상청 ASOS 15분 집계 (화면 기온 표시용)
    "weather_15m", meta,
    Column("measured_at", DateTime, primary_key=True),
    Column("temperature_c", Float), Column("humidity_pct", Float), Column("wind_speed_m_s", Float),
    Column("valid_minutes", Integer, nullable=False),
)

st_forecast = Table(  # 15분 × 4 예측값 (전체 'TOTAL' + 호실 채널)
    "st_forecast", meta,
    Column("model_name", String(20), nullable=False),
    Column("series_id", String(16), nullable=False),
    Column("issued_at", DateTime, nullable=False),
    Column("horizon_min", Integer, nullable=False),
    Column("predicted_kw", Float, nullable=False),
    PrimaryKeyConstraint("model_name", "series_id", "issued_at", "horizon_min"),
)

st_eval = Table(      # 성능 지표 (horizon_min=0 은 4구간 평균)
    "st_eval", meta,
    Column("model_name", String(20), nullable=False),
    Column("scope", String(10), nullable=False),
    Column("horizon_min", Integer, nullable=False),
    Column("mae", Float), Column("rmse", Float), Column("mape", Float),
    Column("n", Integer), Column("n_mape", Integer), Column("bias", Float),   # bias = 평균(예측−실제)
    Column("split", String(5), nullable=False, server_default="test"),           # val(검증) / test(시험)
    Column("test_start", String(16)), Column("test_end", String(16)),
    PrimaryKeyConstraint("model_name", "scope", "horizon_min", "split"),
)

st_peak_eval = Table(  # 피크 사전 감지 성능 (오분류표: 맞힘 tp·헛경보 fp·놓침 fn·정상 tn)
    "st_peak_eval", meta,
    Column("model_name", String(20), nullable=False),
    Column("split", String(5), nullable=False, server_default="test"),
    Column("precision", Float), Column("recall", Float), Column("f1", Float),
    Column("tp", Integer), Column("fp", Integer), Column("fn", Integer), Column("tn", Integer), Column("n", Integer),
    Column("miscls", Float),                                   # 오분류율 = (헛경보+놓침)/전체
    PrimaryKeyConstraint("model_name", "split"),
)

st_channel_eval = Table(  # 채널(시계열)별 시험 성능 — 보조화면 '채널합산 비교'의 호실별 오차
    "st_channel_eval", meta,
    Column("model_name", String(20), nullable=False),
    Column("series_id", String(16), nullable=False),
    Column("mae", Float), Column("bias", Float), Column("n", Integer),
    PrimaryKeyConstraint("model_name", "series_id"),
)

st_explain = Table(   # LightGBM 전체 합산 1시간 뒤 예측의 SHAP 기여도(kW)
    "st_explain", meta,
    Column("issued_at", DateTime, nullable=False),
    Column("feature", String(30), nullable=False),
    Column("contribution_kw", Float, nullable=False),
    PrimaryKeyConstraint("issued_at", "feature"),
)

peak_threshold = Table(  # 날짜별 피크 기준 (직전 30일 P95)
    "peak_threshold", meta,
    Column("date", String(10), primary_key=True),
    Column("threshold_kw", Float), Column("n_samples", Integer),
    Column("method", String(40), nullable=False),
)

upload_log = Table(   # [11] CSV 업로드 이력
    "upload_log", meta,
    Column("upload_id", Integer, primary_key=True, autoincrement=True),
    Column("file_name", String(200)), Column("uploaded_at", DateTime),
    Column("rows_read", Integer), Column("inserted", Integer), Column("duplicates", Integer),
    Column("rejected", Integer), Column("note", Text),
)

st_meta = Table("st_meta", meta, Column("key", String(40), primary_key=True), Column("value", Text))
