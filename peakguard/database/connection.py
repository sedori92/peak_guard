"""[2] DB 구축 — 연결과 기본 테이블 정의 (SQLAlchemy)

- 기본: 로컬 SQLite 파일(peakguard.db)
- .env 에 DATABASE_URL 이 있으면 그 DB(예: PostgreSQL)에 연결
테이블 구조는 db/schema.sql 과 같습니다. 예측·기상 테이블은 tables_forecast.py 에 있습니다.
"""
from __future__ import annotations

from sqlalchemy import (
    Boolean, CheckConstraint, Column, Date, DateTime, Float, ForeignKey, Integer,
    MetaData, PrimaryKeyConstraint, SmallInteger, String, Table, Text,
    UniqueConstraint, create_engine, func, text,
)
from sqlalchemy.engine import Engine

metadata = MetaData()

meter = Table(
    "meter", metadata,
    Column("meter_id", String(10), primary_key=True),
    Column("building", String(50), nullable=False, server_default="생활지원로봇센터"),
    Column("floor", SmallInteger, nullable=False),
    Column("area_type", String(10), nullable=False),
    Column("source_file", String(100)),
    CheckConstraint("floor BETWEEN 1 AND 10", name="ck_meter_floor"),
    CheckConstraint("area_type IN ('공용','호실별')", name="ck_meter_area"),
)

channel = Table(
    "channel", metadata,
    Column("channel_id", String(16), primary_key=True),
    Column("meter_id", String(10), ForeignKey("meter.meter_id"), nullable=False),
    Column("channel_no", SmallInteger, nullable=False),
    Column("room_name", String(50)),
    Column("is_active", Boolean, nullable=False, server_default=text("TRUE")),
    UniqueConstraint("meter_id", "channel_no", name="uk_channel_meter_no"),
    CheckConstraint("channel_no >= 1", name="ck_channel_no"),
)

measurement = Table(
    "measurement", metadata,
    Column("channel_id", String(16), ForeignKey("channel.channel_id"), nullable=False),
    Column("measured_at", DateTime(timezone=True), nullable=False, index=True),
    Column("power_kw", Float, nullable=False),
    Column("energy_kwh", Float, nullable=False),
    Column("demand_kw", Float, nullable=False),
    PrimaryKeyConstraint("channel_id", "measured_at"),
    CheckConstraint("energy_kwh >= 0", name="ck_meas_energy"),
    CheckConstraint("demand_kw >= 0", name="ck_meas_demand"),
)

model_run = Table(
    "model_run", metadata,
    Column("run_id", Integer, primary_key=True, autoincrement=True),
    Column("model_name", String(20), nullable=False),
    Column("target_scope", String(10), nullable=False),
    Column("train_start", Date, nullable=False),
    Column("train_end", Date, nullable=False),
    Column("test_start", Date),
    Column("test_end", Date),
    Column("slot_mae_kw", Float),
    Column("peak_mae_kw", Float),
    Column("peak_mape", Float),
    Column("peak_time_hit", Float),
    Column("params", Text),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint("model_name IN ('LightGBM','RandomForest','BiLSTM','Naive')", name="ck_run_model"),
    CheckConstraint("target_scope IN ('TOTAL','CHANNEL')", name="ck_run_scope"),
)

peak_forecast = Table(
    "peak_forecast", metadata,
    Column("forecast_id", Integer, primary_key=True, autoincrement=True),
    Column("run_id", Integer, ForeignKey("model_run.run_id", ondelete="CASCADE"), nullable=False),
    Column("channel_id", String(16), ForeignKey("channel.channel_id")),  # NULL = 전체
    Column("target_date", Date, nullable=False),
    Column("peak_kw", Float, nullable=False),
    Column("peak_time", DateTime(timezone=True), nullable=False),
    Column("actual_peak_kw", Float),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint("run_id", "channel_id", "target_date", name="uk_forecast"),
    CheckConstraint("peak_kw >= 0", name="ck_forecast_peak"),
)

forecast_slot = Table(
    "forecast_slot", metadata,
    Column("forecast_id", Integer, ForeignKey("peak_forecast.forecast_id", ondelete="CASCADE"), nullable=False),
    Column("slot_time", DateTime(timezone=True), nullable=False),
    Column("predicted_kw", Float, nullable=False),
    PrimaryKeyConstraint("forecast_id", "slot_time"),
)

VIEW_SQL = """
CREATE VIEW IF NOT EXISTS v_total_demand AS
SELECT measured_at, SUM(demand_kw) AS total_demand_kw, COUNT(*) AS n_channels
FROM measurement GROUP BY measured_at
"""


def get_engine(url: str | None = None) -> Engine:
    from ..config import DATABASE_URL
    url = url or DATABASE_URL
    return create_engine(url, pool_pre_ping=True)


def create_schema(engine: Engine) -> None:
    """기본 테이블 + 예측·기상 테이블 + 뷰를 한 번에 생성 (이미 있으면 그대로 둠)."""
    from .tables_forecast import meta as forecast_meta
    metadata.create_all(engine)
    forecast_meta.create_all(engine)
    sql = VIEW_SQL
    if engine.dialect.name == "postgresql":
        sql = sql.replace("CREATE VIEW IF NOT EXISTS", "CREATE OR REPLACE VIEW")
    with engine.begin() as con:
        con.execute(text(sql))
