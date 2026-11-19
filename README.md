# 피크가드 (PeakGuard) — 건물 전체·채널별 전력 피크 예측

생활지원로봇센터(1~4층) 전력량계 8대·채널 96개의 15분 수요전력 데이터(약 100만 건)를
DB(Database, 데이터베이스)에 적재하고, **LightGBM · RandomForest · Bi-LSTM** 세 모델로
**다음날 전체 피크와 채널별 피크(kW, 발생 시각)** 를 예측합니다.

```
CSV 원본 ──▶ load_data.py ──▶ DB (Supabase PostgreSQL 또는 로컬 SQLite)
                                  │
                                  ▼
                        train.py : 학습 · 평가 · 다음날 예측 ──▶ DB 저장 + reports/
                        predict.py : 저장된 모델로 다음날 예측 ──▶ DB 저장
```

## 1. 폴더 구조

| 경로 | 설명 |
|---|---|
| `db/schema.sql` | 테이블 정의서(SQL). Supabase SQL Editor에 그대로 실행 가능 |
| `peakguard/db.py` | DB 연결·테이블 정의 (SQLAlchemy) |
| `peakguard/load_data.py` | CSV 전처리 후 DB 적재 |
| `peakguard/dataset.py` | DB → '날짜 × 96칸(15분)' 행렬 변환, 결측 보정 |
| `peakguard/features.py` | 트리 모델용 특징(전날·1주 전·같은 유형 날 값 등) |
| `peakguard/models.py` | Naive(기준선), LightGBM, RandomForest, Bi-LSTM |
| `peakguard/train.py` | 학습 → 평가 → 다음날 예측 → DB 저장 |
| `peakguard/predict.py` | 저장된 모델로 다음날 예측만 실행 |
| `reports/` | 평가 지표(metrics.csv), 예측 그래프 |

> 원본 CSV(`data/`), DB 파일, 학습된 모델(`models/`), 비밀번호(`.env`)는 보안·용량 문제로 깃허브에 올리지 않습니다.

## 2. 실행 방법

```bash
pip install -r requirements.txt

# (1) 원본 CSV 8개를 data/raw/ 에 넣기
# (2) DB 연결: .env.example 을 .env 로 복사 후 Supabase 주소 입력 (비워두면 로컬 SQLite 사용)
python -m peakguard.load_data --data-dir data/raw

# (3) 학습·평가·다음날 예측 (CPU 기준 약 15~20분, Bi-LSTM 이 대부분)
python -m peakguard.train
python -m peakguard.train --models LightGBM RandomForest   # 일부 모델만

# (4) 새 데이터 적재 후 다음날 예측만
python -m peakguard.predict
```

**Google Colab에서 실행**: 런타임에서 아래처럼 실행하면 됩니다.
```python
!git clone https://github.com/<계정>/peakguard.git
%cd peakguard
!pip install -q -r requirements.txt
# 왼쪽 파일 탭에서 data/raw/ 폴더에 CSV 업로드 (또는 구글 드라이브 마운트)
!python -m peakguard.load_data --data-dir data/raw
!python -m peakguard.train
```

## 3. 데이터베이스 설계

| 테이블 | 내용 | 키·제약조건 |
|---|---|---|
| `meter` | 전력량계 8대 (층, 공용/호실별) | PK meter_id, CK floor·area_type |
| `channel` | 채널 96개 (호실명, 활성 여부) | PK channel_id(`M004-CH03`), FK meter_id, UK(meter_id, channel_no) |
| `measurement` | 15분 측정값 약 101만 건 | PK(channel_id, measured_at), FK channel_id, CK demand_kw ≥ 0 |
| `model_run` | 모델 실행 이력·성능 지표 | PK run_id, CK model_name·target_scope |
| `peak_forecast` | 날짜별 예측 피크(kW)·시각, 실제 피크 | PK forecast_id, FK run_id·channel_id, UK(run_id, channel_id, target_date) |
| `forecast_slot` | 15분 단위 예측값(그래프용) | PK(forecast_id, slot_time), FK forecast_id |
| `v_total_demand` (뷰) | 건물 전체 15분 수요전력 합계 | — |

- `peak_forecast.channel_id` 가 **NULL 이면 '전체(건물 합계)'** 예측입니다.
- PostgreSQL 기준 전체 용량 약 **165MB** → Supabase 무료 요금제(500MB) 안에 들어갑니다.
- `channel.room_name` 은 비어 있습니다. 기업에서 채널별 호실명을 받으면 입력하세요.

### 전처리 규칙
1. 측정 시각을 15분 단위로 맞춤 (`16:29:59.999` → `16:30:00`). 1분 이상 어긋난 초기 시험 데이터 129건 제외
2. 같은 채널·시각 중복 13건은 마지막 값 사용
3. 전 기간 수요전력이 0인 채널 35개는 `is_active = FALSE` → 예측 대상에서 제외 (활성 61개)
4. 1시간 이하 끊김은 선형 보간, 그보다 긴 결측일은 학습 대상에서 제외(입력으로 쓸 때만 직전 같은 유형 날 값으로 대체)
5. 전체 합계(TOTAL)는 61개 채널이 모두 있는 시각만 계산

## 4. 예측 방법

- **예측 대상**: 다음날 하루치 15분 수요전력 96칸 → 그 최댓값 = **일 피크(kW)**, 최댓값 시각 = **피크 시각**
- **대상 시계열**: 전체(건물 합계) 1개 + 활성 채널 61개
- **입력 정보(전날 자정 기준으로 알 수 있는 값만)**: 전날·1주 전 같은 시각 값, 직전 같은 유형(평일/휴일) 날들의 값·최댓값, 요일, 공휴일(대체공휴일·추석 포함)
- **채널 크기 정규화**: 채널마다 kW 크기가 달라서 학습기간 95백분위수로 나눠 학습 후 되돌림

| 모델 | 구성 |
|---|---|
| Naive(기준선) | 직전 같은 유형 날을 그대로 사용 — 모델이 이보다 나아야 의미 있음 |
| LightGBM | 그래디언트 부스팅. 전체 모델 1개 + 채널 통합 모델 1개(채널을 범주형 변수로) |
| RandomForest | 랜덤 포레스트. LightGBM과 같은 특징 사용 |
| Bi-LSTM | 과거 2일(192칸)을 양방향 LSTM으로 읽고, 예측일 달력정보·같은 유형 날 값과 결합해 96칸 출력. 전체+채널 62개 시계열을 함께 학습(시계열 임베딩), 전체 시계열은 10배 가중 |

### 평가 방법
- 시간 순서대로 분할: 마지막 21일을 평가기간, 그 이전을 학습기간 (Bi-LSTM은 학습기간 마지막 14일로 조기 종료)
- 지표: 15분 MAE(Mean Absolute Error, 평균절대오차), 피크 MAE, 피크 MAPE(Mean Absolute Percentage Error, 평균절대백분율오차, 실제 피크 0.1kW 미만 채널-일 제외), 피크 시각 ±1시간 적중률

## 5. 결과

평가기간: 전체 합계가 온전한 마지막 20일 (2026-09-28 ~ 11-01, 10/6~10/20 계측 공백 제외)
학습기간: 2026-07-06 ~ 09-27 (84일)

**전체(건물 합계)**

| 모델 | 15분 MAE (kW) | 일 피크 MAE (kW) | 일 피크 MAPE | 피크 시각 ±1h 적중 |
|---|---|---|---|---|
| Naive(기준선) | 1.53 | 1.67 | 8.8% | 25% |
| LightGBM | 1.18 | 1.56 | 7.6% | 30% |
| RandomForest | **1.17** | 1.67 | 8.0% | 25% |
| Bi-LSTM | 1.18 | **1.49** | **7.4%** | 30% |

**채널별(활성 61개 평균)**

| 모델 | 15분 MAE (kW) | 일 피크 MAE (kW) | 일 피크 MAPE | 피크 시각 ±1h 적중 |
|---|---|---|---|---|
| Naive(기준선) | 0.065 | **0.099** | **28.3%** | **45%** |
| LightGBM | 0.056 | 0.107 | 29.1% | 14% |
| RandomForest | 0.056 | 0.108 | 29.2% | 13% |
| Bi-LSTM | **0.055** | 0.115 | 32.5% | 19% |

![평가기간 전체 예측](reports/total_test_forecast.png)

**해석**
- 전체: 세 모델 모두 15분 오차를 기준선보다 약 **23% 줄였고**, 일 피크 오차도 8.8% → 7.4~8.0%로 개선. Bi-LSTM이 피크 예측이 가장 좋음
- 채널별: 15분 오차는 약 14% 개선됐지만, 일 피크는 기준선과 비슷하거나 약간 나쁨. 개별 호실은 사용 패턴이 들쭉날쭉해서 모델이 '평균적인' 곡선을 그리기 때문 (최댓값이 낮게 잡힘)
- 데이터가 약 3개월(학습 84일)뿐이고 기온 정보가 없어 개선 폭이 제한됨 → **기상청 기온 데이터 추가**, 데이터 기간 확대가 가장 효과적인 다음 개선책
- 이 건물은 평일 약 25kW / 주말·휴일 약 12kW로 패턴이 매우 규칙적이라 기준선 자체가 강함

2026-11-03(화) 전체 피크 예측: LightGBM 26.2kW(14:30), RandomForest 25.9kW(16:30), Bi-LSTM 26.3kW(14:30)
채널 피크 상위: M008-CH04(약 5.2~5.5kW) > M004-CH02 > M004-CH04 > M004-CH09

> 실행 시간(CPU 2코어 기준): 적재 약 25초(PostgreSQL), LightGBM 20초, RandomForest 80초, Bi-LSTM 약 8분

## 6. 다음 단계
- Streamlit 대시보드에서 `peak_forecast`, `forecast_slot`, `v_total_demand` 를 읽어 시각화
- 피크 판정 기준(최근 30일 P95, 계약전력 등)과 예측 피크 비교 → 사전 경보
- 채널 호실명 입력 후 '호실별 피크 영향도' 계산
