# 피크가드 ↔ 기업 시스템 연계 확장 아키텍처 (MariaDB)

> 기업 답변(2026-11-11): 기업 DB는 **MariaDB**, 기업 API 제공 **불가**, 피크가드 API를 기업 시스템에 직접 연결하는 것도 **불가** →
> "확장 방향은 **별도 아키텍처와 코드로 제출** 가능". 이 문서와 아래 코드가 그 제출물입니다.
> 지금은 연결 정보가 없으므로 CSV(파일 끌어다놓기)로 동작하고, 연결 정보를 받으면 **설정만 바꿔** 그대로 연계됩니다.

## 1. 전체 구조

```mermaid
flowchart LR
  subgraph 기업["유큐브 (기업 측)"]
    M[계측기 96채널<br/>CT 센서] --> EP[AI 에너지플래너]
    EP --> MDB[(MariaDB<br/>15분 계측 테이블)]
  end
  subgraph 기상["기상청"]
    KMA[ASOS 광주(156)<br/>분자료 API / CSV]
  end
  subgraph PG["피크가드 (PoC)"]
    S[live/sync.py<br/>증분 적재] --> DB[(SQLite → 운영 시 MariaDB/PostgreSQL)]
    W[collect/weather.py<br/>기상 증분 적재] --> DB
    DB --> P[live/predictor.py<br/>다음 1시간 15분×4 예측]
    P --> API[FastAPI REST/JSON<br/>/api/*]
    API --> UI[관리자 화면 3개]
  end
  MDB -- "읽기 전용 계정<br/>SELECT ... WHERE 측정시각 > :after" --> S
  CSV[CSV 파일<br/>끌어다놓기 · data/incoming] --> S
  KMA --> W
  API -. "확장: 에너지플래너가 예측 결과를 조회<br/>(기업 승인 후)" .-> EP
```

- **읽기 전용 증분 적재**: 피크가드는 기업 MariaDB에서 `마지막 실측 시각(:after)` 이후 행만 읽습니다. 쓰기 권한이 필요 없습니다.
- **중복 없음**: 측정값 장부(`measurement`)의 기본키가 (채널, 측정시각)이라 같은 행을 여러 번 읽어도 한 번만 저장됩니다.
- **가짜 데이터 없음**: 빠진 구간을 채우지 않습니다. 긴 결측은 결측으로 두고, 예측 입력에서 1시간 이하 끊김만 보간합니다.
- **기상**: 같은 방식으로 기상청 ASOS 분자료(API 또는 CSV)를 마지막 기상 시각 이후만 받아 15분 값으로 바꿔 넣습니다.

## 2. 연결 설정 (`.env`)

```dotenv
# MariaDB 드라이버: pip install pymysql  (requirements.txt 에 포함)
LIVE_DB_URL=mysql+pymysql://peakguard_ro:비밀번호@기업DB주소:3306/energyplanner?charset=utf8mb4
# 결과 열 순서: 전력량계 ID, 채널 번호, 측정 시간, 현재 전력(kW), 누적 사용량(kWh), 수요 전력(kW)  ← 기업 CSV와 같은 순서
LIVE_DB_QUERY=SELECT meter_id, channel_no, measured_at, power_kw, energy_kwh, demand_kw FROM tb_measure_15m WHERE measured_at > :after ORDER BY measured_at

# 기상청 API허브 ASOS 분자료 (주소는 발급 화면의 예시 URL을 그대로 붙이고 기간·키 자리만 {start} {end} {key}로)
WEATHER_API_URL=
WEATHER_API_KEY=
```

- 표·열 이름(`tb_measure_15m` 등)은 예시입니다. 기업이 알려 주는 실제 이름으로 `LIVE_DB_QUERY` 만 바꾸면 됩니다.
- 기업 측에는 **해당 표 SELECT 권한만 있는 읽기 전용 계정**을 요청합니다.

## 3. 동작 순서 (화면 [실시간 연결] 버튼 또는 15분 스케줄)

1. `POST /api/live/sync` — CSV 폴더 → 기업 MariaDB(설정 시) → 기상(폴더·API) 순서로 증분 적재, 결과 요약 반환
2. `POST /api/live/predict?model=BiLSTM` — 가장 최근 실측 시각 기준 다음 1시간 예측, `live_forecast` 장부에 기록
3. 화면은 5분마다 1·2를 반복 (연결 중일 때)

운영 서버에서 화면 없이 돌리려면 15분마다 위 두 API를 부르는 스케줄(cron, 작업 스케줄러)을 둡니다.

## 4. 에너지플래너에 결과를 주는 방법 (확장안, 기업 승인 필요)

| 방법 | 내용 | 기업 측 작업 |
|---|---|---|
| ① 조회 API | 에너지플래너가 피크가드 `GET /api/forecast?issued_at=&model=`, `GET /api/live/status` 를 호출 | 호출 코드 추가 |
| ② 결과 테이블 | 피크가드가 기업 MariaDB의 별도 스키마(예: `peakguard.forecast`)에 예측 결과를 씀 | 스키마·쓰기 계정 제공 |
| ③ 파일 | 15분마다 예측 결과 CSV를 공유 폴더에 저장 | 파일 읽기 |

현재 PoC는 ①의 API를 이미 제공합니다 (자동 문서 `/docs`).

## 5. 관련 코드

| 파일 | 역할 |
|---|---|
| `peakguard/live/sync.py` | CSV 폴더·기업 DB(MariaDB) 증분 적재, 기상 업데이트 호출 |
| `peakguard/collect/weather.py` | 기상 CSV·API 증분 적재 (`update`, `upsert`, `fetch_api`) |
| `peakguard/api/upload.py` | 화면 끌어다놓기: 전력 CSV / 기상 CSV 자동 구분 |
| `peakguard/live/predictor.py` | 최근 실측 기준 예측, `live_forecast` 기록 |
| `peakguard/api/live.py` | `/api/live/status·sync·predict` |
| `peakguard/database/connection.py` | `DATABASE_URL` 로 저장소 전환 (SQLAlchemy). PoC 검증은 SQLite 에서 했으며, MariaDB 로 옮길 때는 `db/schema.sql` 의 뷰·자료형을 한 번 점검 |
