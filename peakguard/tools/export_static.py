"""[6] 화면 — 하루치 관리자 화면을 서버 없이 열리는 HTML 한 파일로 내보내기 (발표·시연용)

    python run.py 6 --date 2026-10-28   (--model 은 처음 열 때 고를 모델, 기본 LightGBM)
    → reports/peakguard_2026-10-28.html (더블클릭으로 열림, 회사 데이터가 들어 있으니 외부 공유 주의)
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..config import DEFAULT_MODEL, REPORT_DIR, WEB_DIR


def export(date: str, model: str = DEFAULT_MODEL, out: str | None = None) -> Path:
    """그 날 하루치를 세 화면 모두 서버 없이 볼 수 있게 담습니다.

    - 첫 번째 화면: 4개 모델 × 2개 방식 재생 자료 (콤보버튼으로 바꿔 볼 수 있음). 실시간 연결·연락처·경보 발령은 서버가 있어야 함
    - 두 번째 화면: 그 날 기준 일간·주간·월간·연간 보고서, 표의 각 피크 시각 배치도와 건물 전체 영향요인
                    (호실별 영향요인은 기간별 최대 피크 시각만)
    - 세 번째 화면: 모델성능 및 오경보율 비교 전체
    """
    from urllib.parse import quote

    from ..api import dashboard, history, performance
    from ..api.alerts import DEFAULT_MESSAGE
    from ..reports.executive import build
    enc = lambda v: quote(v, safe="")                                     # 화면의 encodeURIComponent 와 같은 주소
    api: dict = {}
    days = dashboard.days()
    days["days"] = [d for d in days["days"] if d["date"] == date]
    api["api/days"] = days
    for f in days["families"]:
        for mid in (f["direct"], f["sum"]):
            api[f"api/replay?date={date}&model={enc(mid)}"] = dashboard.replay(date, mid)
    api["api/performance"] = performance.performance()
    api["api/alerts/template"] = {"message": DEFAULT_MESSAGE}
    api["api/contacts"] = []                                              # 연락처(개인정보)는 시연 파일에 넣지 않음
    rooms = list(api[f"api/replay?date={date}&model={enc(model)}"]["frames"][0]["rooms"])
    times, peaks = set(), set()
    for p in ("day", "week", "month", "year"):
        r = build(p, date)
        r.pop("series", None)
        api[f"api/report/peaks?period={p}&date={date}"] = r
        times |= {x["peak_time"] for x in r["records"]}
        if r["records"]:
            peaks.add(r["summary"]["peak_time"])
    for t in sorted(times):
        try:
            api["api/snapshot?time=" + enc(t)] = history.snapshot(t)
            for sid in ["TOTAL"] + (rooms if t in peaks else []):
                api[f"api/factors?time={enc(t)}&series={enc(sid)}"] = history.factors(t, sid)
        except Exception:                                                  # 그 시각 자료가 없으면 건너뜀
            pass
    html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    css = (WEB_DIR / "static" / "style.css").read_text(encoding="utf-8")
    inject = "<script>window.PEAKGUARD_STATIC = " + json.dumps({"date": date, "api": api}, ensure_ascii=False, default=str) + ";</script>"
    html = html.replace('<link rel="stylesheet" href="static/style.css">', "<style>\n" + css + "</style>")
    html = html.replace('<script src="static/bg.js"></script>', inject + "\n<script>\n" + (WEB_DIR / "static" / "bg.js").read_text(encoding="utf-8") + "</script>")
    for js in ("common", "screen1_predict", "screen1_alerts", "screen2_history", "screen3_perf", "app"):
        html = html.replace(f'<script src="static/{js}.js"></script>',
                            "<script>\n" + (WEB_DIR / "static" / f"{js}.js").read_text(encoding="utf-8") + "</script>")
    path = Path(out or REPORT_DIR / f"peakguard_{date}.html")
    path.parent.mkdir(exist_ok=True)
    path.write_text(html, encoding="utf-8")
    print(f"저장: {path} ({path.stat().st_size / 1024:.0f} KB, 조회 자료 {len(api)}개)")
    return path


def main() -> None:
    ap = argparse.ArgumentParser(description="시연용 정적 HTML 내보내기")
    ap.add_argument("--date", default="2026-10-28")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    export(a.date, a.model, a.out)


if __name__ == "__main__":
    main()
