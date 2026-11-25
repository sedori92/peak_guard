# 화면 · API · DB 연결표

화면에 보이는 것 하나하나가 어느 코드와 어느 DB 표에서 오는지 정리한 표입니다.
읽는 순서: **화면 요소 → 화면 코드(web/static) → API 주소 → 계산 코드(peakguard) → DB 표**.
각 파이썬 파일 맨 위 설명에도 같은 내용이 "연결 ─ 화면 / API / DB" 세 줄로 들어 있습니다.

## 계층 구조

```
[화면] web/index.html + web/static/*.js        사람이 보는 부분. 숫자를 계산하지 않고 API 결과를 그림
   │  fetch('api/...')
[API]  peakguard/api/*.py  (FastAPI)            화면 한 칸 = API 하나. 계산 모듈을 부르고 JSON 으로 돌려줌
   │  함수 호출
[계산] peakguard/models · evaluation · eda · reports · live
   │  SQL (SQLAlchemy)
[DB]   peakguard.db (SQLite) — 표 정의 db/schema.sql, peakguard/database/*.py
```

API 는 DB 를 직접 고치지 않고(업로드·연락처·경보 기록 제외) 계산 모듈을 거칩니다. 화면 코드는 DB 를 모릅니다.

## ① 예측 및 경보

| 화면 요소 | 화면 코드 | API | 계산 코드 | DB 표 |
|---|---|---|---|---|
| 방식·예측모델·재생 기준일 콤보 | screen1_predict.js `S.init` | `GET /api/days` | api/dashboard.py `days` | st_forecast |
| 실측·예측 그래프, 판정(정상·위험) | screen1_predict.js `drawChart`, `S.render` | `GET /api/replay` | api/dashboard.py `replay` | st_forecast·measurement·peak_threshold |
| 96채널 합계 최대, 피크 기준 콤보 | screen1_predict.js `S.setThrOptions` | `/api/replay` 의 `peak_now`·`thr_options` | models/thresholds.py | measurement |
| 계측 상태 줄·점검 필요 채널 창 | screen1_predict.js `S.meter` | `GET /api/meter_status` | eda/meter_status.py | measurement |
| 점검 우선 Top 5 호실 추이 | screen1_predict.js `drawTop5` | `/api/replay` 의 `rp` | api/dashboard.py | st_forecast (호실 34개) |
| 호실별 피크 예측 가능성 | screen1_predict.js `drawPossibility` | `/api/replay` 의 `proom`·`room_thr` | api/dashboard.py | st_forecast·measurement |
| 합산 수요전력 구성(호실/공용) | screen1_predict.js `drawShare` | `/api/replay` 의 `rooms`·`common` | api/dashboard.py | measurement |
| 경보발령 선택·안내문구·연락처·발령 | screen1_alerts.js | `/api/alerts/template`·`/api/contacts`·`/api/alerts` | api/alerts.py | tenant_contact·alert_log (개인정보, 깃허브 제외) |
| [실시간 연결] | screen1_predict.js `S.goLive` | `POST /api/live/sync` → `POST /api/live/predict` | live/sync.py → live/predictor.py | measurement·weather_15m (쓰기), live_forecast (쓰기) |
| [파일 끌어다놓기] | screen1_predict.js `uploadDialog` | `POST /api/upload` | api/upload.py, collect/power_csv.py, collect/weather.py | measurement·weather_15m·upload_log |

## ② 피크이력 및 영향도 분석 (모두 실측)

| 화면 요소 | 화면 코드 | API | 계산 코드 | DB 표 |
|---|---|---|---|---|
| 최고관리자 보고서 — 피크 변동기록·영향도 Top 10 | screen2_history.js | `GET /api/report/peaks` | reports/executive.py | measurement·channel·peak_threshold |
| 평소 대비 증가 Top 10 | screen2_history.js | `GET /api/rise` | reports/influence.py | measurement |
| 스프레드시트 보기·내려받기·출력 | screen2_history.js | `/api/report/preview`·`/api/report/xlsx`·`/api/printer` | reports/executive.py | measurement |
| 층별 호실 배치도 | screen2_history.js | `GET /api/snapshot` | api/history.py | measurement·channel |
| 전체 및 채널별 영향도 그래프 | screen2_history.js | `GET /api/influence` | reports/influence.py | measurement |
| 사용패턴 분석 7칸 | screen2_patterns.js | `GET /api/patterns` | eda/analysis.py | measurement·weather_15m |
| 기상 데이터 검토 상자 | screen2_patterns.js `weather` | `/api/patterns` 의 `weather_review` | evaluation/extra.py `weather_summary` | weather_15m·st_eval |

## ③ 모델성능 및 오경보율 비교

| 화면 요소 | 화면 코드 | API | 계산 코드 | DB 표 / 파일 |
|---|---|---|---|---|
| 피크 기준 콤보·데이터 정제 기록 | screen3_perf.js `P3.show` | `GET /api/performance?thr=` | evaluation/peak_by_threshold.py, api/common.py `info` | st_forecast·st_meta |
| 최적 모델 선정 결론 | screen3_perf.js `selectionCard` | 같음 (`selection`) | evaluation/extra.py `selection` | st_eval·st_peak_eval |
| ① 예측 오차 표 · ② 오분류표 | screen3_perf.js `section` | 같음 (`sections`) | api/performance.py | st_eval·st_peak_eval |
| ③ 발생시각 오차 · ④ 조건별 · ⑥ 기간별 | screen3_perf.js `extraCards` | 같음 (`timing`·`by_condition`·`by_day`) | evaluation/extra.py | st_forecast·measurement |
| ⑤ 기상 포함/미포함 | screen3_perf.js `extraCards` | 같음 (`weather`) | evaluation/extra.py `weather_compare` | st_eval, models/compare_weather.json |

## DB 표를 만드는 곳

| DB 표 | 만드는 명령 | 코드 |
|---|---|---|
| measurement·channel·meter·holiday | `python run.py 2` (이후 업로드·실시간 연결로 추가) | database/load_power.py, collect/*.py |
| weather_15m | `python run.py 2`, 기상 CSV 업로드, 실시간 연결 | collect/weather.py |
| st_forecast·st_eval·st_peak_eval·st_channel_eval·st_explain·st_meta·peak_threshold | `python run.py 3` | models/train.py |
| live_forecast | [실시간 연결] | live/predictor.py |
| tenant_contact·alert_log | ① 연락처 관리·경보 발령 | api/alerts.py |
| upload_log | [파일 끌어다놓기], `python run.py 11` | api/upload.py |
