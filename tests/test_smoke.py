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


def test_split_and_sum_models(day):
    """학습·검증·시험 기간 분할 + 채널합산 모델 + 오분류표."""
    e = client.get("/api/eval").json()
    assert e["splits"]["train"][1] < "2026-09-15" and e["splits"]["test"][0] == "2026-10-20 15:15"
    names = {(r["model_name"], r["split"]) for r in e["peak_detection"]}
    assert ("RF_SUM", "test") in names and ("RF_SUM", "val") in names
    for r in e["peak_detection"]:
        assert r["tp"] + r["fp"] + r["fn"] + r["tn"] == r["n"]
    c = client.get("/api/sumcompare", params={"date": day}).json()
    assert c["lines"]["RF_SUM"] and c["lines"]["RandomForest"] and len(c["rooms"]) >= 20
    assert c["perf"]["Random Forest 채널합산"]["method"] == "채널합산"
    d = client.get("/api/replay", params={"date": day, "model": "RF_SUM"}).json()
    assert len(d["frames"][40]["proom"]) == 34
