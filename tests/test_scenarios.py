"""서비스 시나리오 기반 기능 테스트 (기업 과제 원문 세부 7, 결과표: docs/TEST_SCENARIOS.md)

관리자가 실제로 하는 일을 순서대로 따라가며, 화면이 쓰는 API 결과가 기업 답변의 규칙대로 나오는지 확인합니다.
    python -m pytest tests/test_scenarios.py -v
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from peakguard.api.app import app

client = TestClient(app)


@pytest.fixture(scope="module")
def days():
    return client.get("/api/days").json()


def _risk(f):
    """기업 추가 답변 2번: 다음 1시간 예측 4칸 중 하나라도 기준 이상 = 위험."""
    return f.get("pred") and max(f["pred"]) >= f["thr"]


def test_s1_open_dashboard_default(days):
    """S1 관리자가 첫 화면을 연다 → 기본값(채널별 합산 후 예측·Bi-LSTM·2026-11-02)으로 1시간 예측 4칸이 보인다."""
    assert days["default_date"] == "2026-11-02"
    d = client.get("/api/replay", params={"date": days["default_date"], "model": "BiLSTM"}).json()
    assert d["method"] == "direct" and d["model_id"] == "BiLSTM"
    fr = [f for f in d["frames"] if f.get("pred")]
    assert fr and all(len(f["pred"]) == 4 for f in fr)              # 1차 답변 C3: 15분 × 4
    assert d["thr_options"]["recent30"]["kw"] > 0                     # 1차 답변 C1: 최근 30일 P95


def test_s2_any_test_day_replay(days):
    """S2 시험기간 어느 날을 골라도 재생된다 (10-20 ~ 11-02)."""
    for day in days["days"]:
        r = client.get("/api/replay", params={"date": day["date"] if isinstance(day, dict) else day, "model": "BiLSTM"})
        assert r.status_code == 200


def test_s3_risk_alert_rule():
    """S3 피크 위험 판정: 4칸 중 하나라도 기준 이상이면 위험, 최초 위험 시각·최대 예상·초과량이 계산된다 (추가 답변 2번)."""
    d = client.get("/api/replay", params={"date": "2026-10-28", "model": "BiLSTM"}).json()
    risky = [f for f in d["frames"] if _risk(f)]
    assert risky, "10-28(수)에는 위험 판정이 있어야 함"
    f = risky[0]
    first = next(i for i, v in enumerate(f["pred"]) if v >= f["thr"])
    mx = max(f["pred"])
    assert 0 <= first < 4 and mx - f["thr"] >= 0
    calm = [f for f in d["frames"] if f.get("pred") and not _risk(f)]
    assert all(max(f["pred"]) < f["thr"] for f in calm)               # 2단계: 나머지는 정상 (1차 답변 C2)


def test_s4_top5_rooms_for_inspection():
    """S4 위험일 때 점검 대상 = 예측 사용량 상위 5개 호실 (1차 답변 C4), kW 순서."""
    d = client.get("/api/replay", params={"date": "2026-10-28", "model": "BiLSTM"}).json()
    f = next(f for f in d["frames"] if _risk(f))
    top = sorted(f["proom"].items(), key=lambda kv: -kv[1])[:5]
    assert len(top) == 5 and top[0][1] >= top[-1][1]
    assert all(k in f["rp"] for k, _ in top)                          # 막대·추이 그래프에 같은 호실


def test_s5_cooperation_request_message():
    """S5 입주기업 협조 요청 문구가 준비되어 있다 (1차 답변 C4, 실제 연락처는 내부 DB에만)."""
    m = client.get("/api/alerts/template").json()["message"]
    assert "피크" in m and len(m) > 20


def test_s6_change_threshold():
    """S6 피크 기준을 바꾸면 성능 화면의 오분류표가 그 기준으로 다시 판정된다 (5가지 기준)."""
    keys = [o["key"] for o in client.get("/api/performance").json()["thr_options"]]
    assert keys == ["recent30", "this_month", "prev_month", "summer", "winter"]
    a = client.get("/api/performance", params={"thr": "recent30"}).json()["thr"]
    b = client.get("/api/performance", params={"thr": "prev_month"}).json()["thr"]
    assert a["key"] != b["key"]


def test_s7_peak_history_influence():
    """S7 지난 피크를 고르면 그 시각 실측 영향도(건물 대비·호실 34개 대비)가 보인다 (3차 답변 ③④)."""
    t = client.get("/api/report/peaks", params={"period": "week", "date": "2026-10-28"}).json()["summary"]["peak_time"]
    s = client.get("/api/snapshot", params={"time": t}).json()
    rooms = [c for c in s["channels"] if c["kind"] == "호실"]
    assert rooms and len(rooms) <= 34
    x = client.get("/api/report/xlsx", params={"period": "week", "date": "2026-10-28"})
    assert x.status_code == 200 and x.content[:2] == b"PK"            # 스프레드시트 내려받기


def test_s8_usage_pattern_analysis():
    """S8 사용패턴 분석: 시간대·요일·월별, 피크 반복, 기상 관계, 이상 패턴, 피크 전 패턴, 절감 시간대 (과제 목표 1, 세부 2·4·5)."""
    p = client.get("/api/patterns", params={"range": "all", "date": "2026-11-02"}).json()
    for k in ("heat", "monthly", "peak_hours", "weather", "anomalies", "pre_peak", "saving", "weather_review"):
        assert p[k], k
    assert p["weather_review"]["slots"] > 0                            # 기상 연계


def test_s9_model_goal_and_selection():
    """S9 모델 비교: 선정 모델이 기준모델 대비 MAE 10% 이상 개선, 재현율 우선 선정 (1차 답변 D1·D2)."""
    p = client.get("/api/performance").json()
    sel = p["selection"]
    best = next(r for s in p["sections"] for r in s["rows"] if r["id"] == sel["best"])
    assert best["gain"] >= 10
    for k in ("timing", "by_condition", "by_day", "weather", "prep"):
        assert p[k], k


def test_s10_live_connect_and_upload():
    """S10 [실시간 연결]: 자료를 받아 현재 시각 예측을 만든다 (기업 DB 정보가 없으면 받을 자료 없음으로 안내)."""
    s = client.post("/api/live/sync").json()
    assert "inserted" in s or "message" in s or "status" in s
    st = client.get("/api/live/status").json()
    assert "api_configured" in st
