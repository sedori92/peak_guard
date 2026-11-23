"""[7] 동작확인 — API·화면·보고서·업로드가 제대로 동작하는지 빠르게 점검

    python run.py 7          (= pytest -q tests)

DB 구축(run.py 2)과 예측모델 학습(run.py 3)이 끝난 상태에서 실행합니다.
업로드 시험은 이미 있는 데이터를 다시 보내므로 DB가 바뀌지 않습니다(전부 중복 처리).
"""
from __future__ import annotations

import io

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from peakguard.api.app import app
from peakguard.config import DATA_RAW

client = TestClient(app)


@pytest.fixture(scope="module")
def day():
    r = client.get("/api/days")
    assert r.status_code == 200, r.text
    days = r.json()["days"]
    assert days, "재생 가능한 날짜가 없습니다 (run.py 3 실행 필요)"
    return days[len(days) // 2]["date"]


def test_health():
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["measurements"] > 1_000_000


def test_index_and_static():
    assert client.get("/").status_code == 200
    for f in ("style.css", "app.js", "bg.js"):
        assert client.get(f"/static/{f}").status_code == 200


def test_replay_total_line(day):
    """전체 피크 선: 프레임마다 실측 1개 + 예측 4개 + 피크 기준."""
    d = client.get("/api/replay", params={"date": day, "model": "LightGBM"}).json()
    assert len(d["frames"]) >= 48
    f = d["frames"][40]
    assert len(f["pred"]) == 4 and all(v is not None for v in f["pred"])
    assert f["thr"] and f["y"] > 0
    assert len(f["rooms"]) == 34 and len(f["proom"]) == 34
    assert set(d["perf"]) >= {"LightGBM", "Random Forest"}


def test_channel_line(day):
    """채널별 피크 선: 호실 채널 실측·예측·채널 기준."""
    d = client.get("/api/channel", params={"date": day, "channel": "M008-04", "model": "LightGBM"}).json()
    assert len(d["actual"]) >= 48 and d["pred"] and d["thr"] is not None
    assert all(len(v) == 4 for v in d["pred"].values())


def test_errors():
    assert client.get("/api/replay", params={"date": "2026-07-01"}).status_code == 404
    assert client.get("/api/replay", params={"date": "2026-10-28", "model": "없는모델"}).status_code == 400


def test_eval_and_report():
    e = client.get("/api/eval").json()
    assert any(r["model_name"] == "LightGBM" and r["scope"] == "TOTAL" for r in e["metrics"])
    r = client.get("/api/report", params={"period": "week", "date": "2026-10-28"})
    assert r.status_code == 200 and "주간 보고서" in r.text and "일별 피크" in r.text


def test_upload_is_idempotent():
    """기존 CSV 일부를 다시 보내면 전부 중복 처리되어 DB가 바뀌지 않아야 함."""
    src = sorted(DATA_RAW.glob("*.csv"))[0]
    part = pd.read_csv(src, encoding="utf-8-sig").head(200)
    buf = io.BytesIO(part.to_csv(index=False).encode("utf-8-sig"))
    r = client.post("/api/upload", files={"file": ("test.csv", buf, "text/csv")})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["inserted"] == 0 and j["duplicates"] == j["valid_rows"] > 0
    bad = client.post("/api/upload", files={"file": ("bad.csv", io.BytesIO(b"a,b\n1,2\n"), "text/csv")})
    assert bad.status_code == 400


def test_report_peaks_and_xlsx(day):
    """[10] 최고관리자 보고서: 일·주·월·연 피크 변동기록 + 기여도 Top10, 스프레드시트."""
    for period, n_min in (("day", 12), ("week", 5), ("month", 15), ("year", 3)):
        r = client.get("/api/report/peaks", params={"period": period, "date": day}).json()
        assert len(r["records"]) >= n_min, period
        assert len(r["top10"]) == 10 and r["top10"][0]["share_building"] > 0
        assert r["summary"]["peak_kw"] == max(x["peak_kw"] for x in r["records"])
    x = client.get("/api/report/xlsx", params={"period": "week", "date": day})
    assert x.status_code == 200 and x.content[:2] == b"PK"
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(x.content))
    assert wb.sheetnames == ["요약", "피크 변동기록", "기여도 Top10", "15분 기록"]


def test_split_and_models(day):
    """학습·검증·시험 기간 분할 + 4개 모델 × 2개 방식 + 오분류표."""
    e = client.get("/api/eval").json()
    assert e["splits"]["train"][1] < "2026-09-15" and e["splits"]["test"][0] == "2026-10-20 15:15"
    names = {(r["model_name"], r["split"]) for r in e["peak_detection"]}
    for m in ("LightGBM", "XGBoost", "RandomForest", "BiLSTM", "LGBM_SUM", "XGB_SUM", "RF_SUM", "LSTM_SUM"):
        assert (m, "test") in names, m
    for r in e["peak_detection"]:
        assert r["tp"] + r["fp"] + r["fn"] + r["tn"] == r["n"]
    for m in ("XGB_SUM", "BiLSTM"):
        d = client.get("/api/replay", params={"date": day, "model": m}).json()
        assert len(d["frames"][40]["proom"]) == 34 and len(d["room_thr"]) == 34
    o = d["thr_options"]                                     # 피크 기준 콤보박스 5종
    assert list(o) == ["recent30", "prev_month", "this_month", "summer", "winter"]
    assert abs(o["recent30"]["kw"] - d["frames"][40]["thr"]) < 0.02 and o["summer"]["kw"] and o["winter"]["kw"] is None


def test_performance_screen():
    """세 번째 화면: 위 '채널별 예측 후 합산' / 아래 '채널별 합산 후 예측', 재현율·경보 없음 줄."""
    p = client.get("/api/performance").json()
    assert [s["key"] for s in p["sections"]] == ["sum", "direct"]
    for s in p["sections"]:
        assert [r["name"] for r in s["rows"]] == ["전일 동일시각", "LightGBM", "XGBoost", "Random Forest", "Bi-LSTM"]
        na = s["no_alarm"]
        assert na["tp"] == 0 and na["recall"] == 0 and na["fn"] == s["actual_risk"]
        best = max(s["rows"][1:], key=lambda r: r["peak"]["recall"])
        assert s["best_recall"] == best["id"]
    # 피크 기준점별: 최근 30일 = 학습 때 저장한 오분류표와 같아야 함, 다른 기준도 계산됨
    e = {r["model_name"]: r for r in client.get("/api/eval").json()["peak_detection"] if r["split"] == "test"}
    assert all(r["peak"]["tp"] == e[r["id"]]["tp"] and r["peak"]["fp"] == e[r["id"]]["fp"] for r in p["sections"][1]["rows"])
    for k in ("prev_month", "this_month", "summer"):
        q = client.get("/api/performance", params={"thr": k}).json()
        assert q["thr"]["key"] == k and q["thr"]["n"] > 0
    assert client.get("/api/performance", params={"thr": "winter"}).json()["thr"]["n"] == 0


def test_history_screen(day):
    """두 번째 화면: 배치도(그 시각 96개 채널) + 전체·채널별 영향요인."""
    t = client.get("/api/report/peaks", params={"period": "day", "date": day}).json()["summary"]["peak_time"]
    s = client.get("/api/snapshot", params={"time": t}).json()
    assert s["n_channels"] == 96 and abs(s["rooms_kw"] + s["common_kw"] - s["total_kw"]) < 0.05
    for series in ("TOTAL", s["channels"][0]["channel"]):
        f = client.get("/api/factors", params={"time": t, "series": series}).json()
        assert f["items"] and abs(f["base_kw"] + sum(x["kw"] for x in f["items"]) - f["pred_kw"]) < 0.05


def test_contacts_and_alerts():
    """경보발령 선택: 연락처 추가·수정·삭제, 경보 발령 기록 (시험용 연락처는 끝나면 지움)."""
    c = client.post("/api/contacts", json={"channel_id": "M008-04", "name": "시험", "email": "t@example.com"}).json()
    try:
        assert client.post("/api/contacts", json={"channel_id": "M008-04", "email": "bad"}).status_code == 400
        u = client.put(f"/api/contacts/{c['contact_id']}", json={"channel_id": "M008-04", "name": "시험2", "phone": "010-1234-5678"}).json()
        assert u["name"] == "시험2"
        assert "피크기준" in client.get("/api/alerts/template").json()["message"]
        a = client.post("/api/alerts", json={"channels": ["M008-04", "M004-02"], "mode": "시험"}).json()
        assert a["recipients"] == 1 and a["no_contact"] == ["2층 02번"]
        assert client.post("/api/alerts", json={"channels": []}).status_code == 400
    finally:
        client.delete(f"/api/contacts/{c['contact_id']}")
        from sqlalchemy import text
        from peakguard.api.common import engine
        with engine.begin() as con:
            con.execute(text("DELETE FROM alert_log WHERE mode='시험'"))


def test_live_connect():
    """[실시간 연결]: 새 데이터 확인(없으면 그대로) → 가장 최근 실측 기준 예측."""
    s = client.post("/api/live/sync").json()
    assert s["after_last"] >= s["before_last"]
    for m in ("LightGBM", "LGBM_SUM"):
        r = client.post("/api/live/predict", params={"model": m}).json()
        assert len(r["pred"]) == 4 and len(r["rooms_pred"]) == 34 and r["time"] == s["after_last"]
