-- =====================================================================
-- 피크가드(PeakGuard) 데이터베이스 스키마  (PostgreSQL / Supabase)
-- Supabase 대시보드 > SQL Editor 에 붙여넣고 Run 하면 테이블이 만들어집니다.
-- (python -m peakguard.load_data 를 실행해도 같은 구조가 자동 생성됩니다)
--
-- 제약조건 약어
--   PK: Primary Key(기본키)   FK: Foreign Key(외래키)   UK: Unique Key(고유키)
--   NN: Not Null(필수값)      CK: Check(체크 조건)
-- =====================================================================

-- 1) 전력량계(계량기) : 층·구역 단위 계량기 8대 (M001~M008)
CREATE TABLE IF NOT EXISTS meter (
    meter_id     VARCHAR(10)  PRIMARY KEY,                               -- PK  예: M004
    building     VARCHAR(50)  NOT NULL DEFAULT '생활지원로봇센터',        -- NN
    floor        SMALLINT     NOT NULL CHECK (floor BETWEEN 1 AND 10),   -- NN, CK
    area_type    VARCHAR(10)  NOT NULL CHECK (area_type IN ('공용','호실별')), -- NN, CK
    source_file  VARCHAR(100)
);

-- 2) 채널 : 계량기의 개별 회로(호실·설비). 호실명은 기업 확인 후 room_name 에 입력
CREATE TABLE IF NOT EXISTS channel (
    channel_id   VARCHAR(16)  PRIMARY KEY,                               -- PK  예: M004-CH03
    meter_id     VARCHAR(10)  NOT NULL REFERENCES meter(meter_id),       -- FK, NN
    channel_no   SMALLINT     NOT NULL CHECK (channel_no >= 1),          -- NN, CK
    room_name    VARCHAR(50),                                            -- 호실명(선택)
    is_active    BOOLEAN      NOT NULL DEFAULT TRUE,                     -- 전 기간 0 kW 채널은 FALSE
    CONSTRAINT uk_channel_meter_no UNIQUE (meter_id, channel_no)         -- UK
);

-- 3) 측정값 : 15분 간격 계측 원본 (약 100만 건)
CREATE TABLE IF NOT EXISTS measurement (
    channel_id   VARCHAR(16)  NOT NULL REFERENCES channel(channel_id),   -- FK, NN
    measured_at  TIMESTAMPTZ  NOT NULL,                                  -- 15분 단위로 보정한 시각
    power_kw     REAL         NOT NULL,                                  -- 현재 전력(kW), 음수 가능(계측 오차)
    energy_kwh   REAL         NOT NULL CHECK (energy_kwh >= 0),          -- 누적 사용량(kWh)
    demand_kw    REAL         NOT NULL CHECK (demand_kw >= 0),           -- 수요 전력(kW) ← 예측 대상
    PRIMARY KEY (channel_id, measured_at)                                -- PK (복합키)
);
CREATE INDEX IF NOT EXISTS ix_measurement_time ON measurement (measured_at);

-- 4) 모델 실행 이력 : 어떤 모델을 어떤 기간으로 학습·평가했는지
CREATE TABLE IF NOT EXISTS model_run (
    run_id        SERIAL       PRIMARY KEY,
    model_name    VARCHAR(20)  NOT NULL CHECK (model_name IN ('LightGBM','RandomForest','BiLSTM','Naive')),
    target_scope  VARCHAR(10)  NOT NULL CHECK (target_scope IN ('TOTAL','CHANNEL')),
    train_start   DATE         NOT NULL,
    train_end     DATE         NOT NULL,
    test_start    DATE,
    test_end      DATE,
    slot_mae_kw   REAL,        -- 15분 수요전력 평균절대오차
    peak_mae_kw   REAL,        -- 일 최대(피크) 평균절대오차
    peak_mape     REAL,        -- 일 피크 평균절대백분율오차(%)
    peak_time_hit REAL,        -- 피크 시각 ±1시간 적중률(%)
    params        TEXT,        -- 하이퍼파라미터(JSON 문자열)
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- 5) 피크 예측 결과 : 날짜별 예측 피크(kW)·피크 시각
--    channel_id 가 NULL 이면 '전체(건물 합계)' 예측
CREATE TABLE IF NOT EXISTS peak_forecast (
    forecast_id   SERIAL       PRIMARY KEY,
    run_id        INTEGER      NOT NULL REFERENCES model_run(run_id) ON DELETE CASCADE,
    channel_id    VARCHAR(16)  REFERENCES channel(channel_id),
    target_date   DATE         NOT NULL,
    peak_kw       REAL         NOT NULL CHECK (peak_kw >= 0),
    peak_time     TIMESTAMPTZ  NOT NULL,
    actual_peak_kw REAL,        -- 실제값이 있으면(평가 기간) 함께 저장
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    CONSTRAINT uk_forecast UNIQUE (run_id, channel_id, target_date)
);

-- 6) 15분 단위 예측값 (그래프용)
CREATE TABLE IF NOT EXISTS forecast_slot (
    forecast_id   INTEGER      NOT NULL REFERENCES peak_forecast(forecast_id) ON DELETE CASCADE,
    slot_time     TIMESTAMPTZ  NOT NULL,
    predicted_kw  REAL         NOT NULL,
    PRIMARY KEY (forecast_id, slot_time)
);

-- 7) 뷰 : 건물 전체 15분 수요전력 합계
CREATE OR REPLACE VIEW v_total_demand AS
SELECT measured_at, SUM(demand_kw) AS total_demand_kw, COUNT(*) AS n_channels
FROM measurement
GROUP BY measured_at;
