"""[2] DB 구축 — 운영용 표 (화면 '예측 및 경보'의 실시간 연결·경보발령에서 씀)

연결 ─ 화면: ① 실시간 연결·경보발령
       API : —
       DB  : live_forecast·tenant_contact·alert_log·upload_log 정의

여기 표들은 모델을 다시 학습해도 지워지지 않습니다 (입주자 연락처·발령 기록 보존).
입주자 연락처는 개인정보이므로 DB 파일(peakguard.db)에만 저장하고 깃허브에는 올리지 않습니다(.gitignore).

  tenant_contact : 입주자 연락처 장부 — 호실 채널별 담당자 이름·메일·전화번호 (화면 '경보발령 선택 > 연락처 관리')
  alert_log      : 경보 발령 기록 장부 — 언제, 어떤 예측을 보고, 어느 호실에, 어떤 안내문을 보냈는지
  live_forecast  : 실시간 예측 기록 장부 — '실시간 연결'을 누를 때마다 만든 다음 1시간 예측값
"""
from __future__ import annotations

from sqlalchemy import (Boolean, Column, DateTime, Float, Integer, MetaData, PrimaryKeyConstraint, String, Table,
                        Text)

meta = MetaData()

tenant_contact = Table(
    "tenant_contact", meta,
    Column("contact_id", Integer, primary_key=True, autoincrement=True),
    Column("channel_id", String(16), nullable=False),        # 호실 채널 (예: M008-CH04 = 4층 04번)
    Column("name", String(60)),                               # 입주자(담당자) 이름
    Column("email", String(120)),                             # 메일 주소
    Column("phone", String(30)),                              # 전화번호
    Column("memo", Text),                                     # 메모 (회사명 등)
    Column("created_at", DateTime), Column("updated_at", DateTime),
)

alert_log = Table(
    "alert_log", meta,
    Column("alert_id", Integer, primary_key=True, autoincrement=True),
    Column("created_at", DateTime, nullable=False),          # 발령 버튼을 누른 실제 시각
    Column("ref_time", String(16)),                          # 화면 기준 시각 (시연이면 재생 시각, 실시간이면 최신 실측 시각)
    Column("mode", String(10)),                              # 시연 / 실시간
    Column("model_name", String(20)),                        # 발령 근거가 된 예측 (예: LightGBM)
    Column("predicted_max_kw", Float), Column("threshold_kw", Float),
    Column("channels", Text),                                 # 발령 대상 호실 채널 (쉼표로 구분)
    Column("include_common", Boolean),                       # 공용부분 포함 여부 (최후 수단)
    Column("message", Text),                                  # 안내문구
    Column("recipients", Integer),                            # 연락처가 등록된 받는 사람 수
    Column("email_sent", Integer),                            # 실제 보낸 메일 수 (메일 서버 설정 시)
    Column("status", String(40)),                             # 기록만 / 메일 발송 / 일부 실패 등
)

live_forecast = Table(
    "live_forecast", meta,
    Column("model_name", String(20), nullable=False),
    Column("series_id", String(16), nullable=False),         # TOTAL(건물 합계) 또는 채널
    Column("issued_at", DateTime, nullable=False),           # 예측 기준 시각 (가장 최근 실측 시각)
    Column("horizon_min", Integer, nullable=False),          # 15·30·45·60분 뒤
    Column("predicted_kw", Float, nullable=False),
    Column("created_at", DateTime),
    PrimaryKeyConstraint("model_name", "series_id", "issued_at", "horizon_min"),
)
