"""[6] 화면 — 관리자 화면을 서버 없이 열리는 HTML 한 파일로 내보내기 (발표·시연용)

    python run.py 6                 → reports/peakguard_demo.html  (시험기간 모든 날 × 8개 모델)
    python run.py 6 --date 2026-10-28   처음 열 때 보여 줄 재생 기준일 (기본 10-28)
    더블클릭으로 열림. 회사 데이터가 들어 있으니 외부 공유 주의.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..config import DEFAULT_MODEL, REPORT_DIR, WEB_DIR

SHARED = ("t", "y", "p", "temp", "thr", "rooms", "common")       # 모델과 상관없는 실측 (날마다 한 번만 저장)
PER_MODEL = ("pred", "proom", "rp")                                    # 모델마다 다른 예측


def export(date: str = "2026-10-28", model: str = DEFAULT_MODEL, out: str | None = None) -> Path:
    """시험기간 모든 날을 세 화면 모두 서버 없이 볼 수 있게 담습니다.

    - 첫 번째 화면: 시험기간 모든 날 × 4개 모델 × 2개 방식 재생 (재생 기준일·방식·모델 콤보버튼 모두 동작)
                    실시간 연결·연락처 관리·경보 발령은 서버가 있어야 함
    - 두 번째 화면: 시험기간 각 날을 기준일로 한 일간·주간·월간·연간 보고서, 표의 각 피크 시각 배치도와 건물 전체 영향요인
                    (호실별 영향요인은 각 보고서의 최대 피크 시각만)
    - 세 번째 화면: 모델성능 및 오경보율 비교 전체
    파일 크기를 줄이려고 실측은 날마다 한 번만, 예측은 모델마다 따로 담고 화면(common.js)에서 다시 합칩니다.
    """
    from urllib.parse import quote

    from ..api import dashboard, history, performance
    from ..api.alerts import DEFAULT_MESSAGE
    from ..reports.executive import build
    enc = lambda v: quote(v, safe="")                                     # 화면의 encodeURIComponent 와 같은 주소
    api: dict = {}
    replay: dict = {}
    days = dashboard.days()
    api["api/days"] = days
    dates = [d["date"] for d in days["days"]]
    for d in dates:
        day = None
        for f in days["families"]:
            for mid in (f["direct"], f["sum"]):
                r = dashboard.replay(d, mid)
                if day is None:
                    day = {k: v for k, v in r.items() if k not in ("frames", "imp", "perf", "peak", "model", "model_id",
                                                                   "method", "method_name")}
                    day["frames"] = [{k: fr.get(k) for k in SHARED} for fr in r["frames"]]
                    day["models"] = {}
                day["models"][mid] = {"model": r["model"], "model_id": r["model_id"], "method": r["method"],
                                      "method_name": r["method_name"], "t": [fr["t"] for fr in r["frames"]],
                                      **{k: [fr[k] for fr in r["frames"]] for k in PER_MODEL}}
        replay[d] = day
        print(f"  재생 {d} ({len(day['frames'])}칸)")
    from ..models.thresholds import KEYS
    for k in KEYS:                                                         # 세 번째 화면: 피크 기준점 5종
        api[f"api/performance?thr={k}"] = performance.performance(k)
    api["api/alerts/template"] = {"message": DEFAULT_MESSAGE}
    api["api/contacts"] = []                                              # 연락처(개인정보)는 시연 파일에 넣지 않음
    rooms = list(replay[dates[0]]["frames"][0]["rooms"])
    times, peaks = set(), set()
    for d in dates:
        for p in ("day", "week", "month", "year"):
            r = build(p, d)
            r.pop("series", None)
            api[f"api/report/peaks?period={p}&date={d}"] = r
            if r["records"]:                                               # [스프레드시트 보기·출력] 미리보기 (15분 기록은 앞 100줄)
                api[f"api/report/preview?period={p}&date={d}"] = history.report_preview(p, d, 100)
            times |= {x["peak_time"] for x in r["records"]}
            if r["records"]:
                peaks.add(r["summary"]["peak_time"])
    for t in sorted(times):
        try:
            api["api/snapshot?time=" + enc(t)] = history.snapshot(t)
            for sid in ["TOTAL"] + (rooms if t in peaks else []):
                try:
                    api[f"api/factors?time={enc(t)}&series={enc(sid)}"] = history.factors(t, sid)
                except Exception as e:                                     # 예측이 불가했던 시각 등 → 화면에 이유 표시
                    api[f"api/factors?time={enc(t)}&series={enc(sid)}"] = {"__error": getattr(e, "detail", str(e))}
        except Exception:                                                  # 그 시각 자료가 없으면 건너뜀
            pass
    print(f"  보고서 {len(dates) * 4}개 · 피크 시각 {len(times)}개")
    static = {"date": date if date in dates else dates[-1], "model": model, "replay": replay, "api": api}
    html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    css = (WEB_DIR / "static" / "style.css").read_text(encoding="utf-8")
    inject = "<script>window.PEAKGUARD_STATIC = " + json.dumps(static, ensure_ascii=False, default=str, separators=(",", ":")) + ";</script>"
    html = html.replace('<link rel="stylesheet" href="static/style.css">', "<style>\n" + css + "</style>")
    html = html.replace('<script src="static/bg.js"></script>', inject + "\n<script>\n" + (WEB_DIR / "static" / "bg.js").read_text(encoding="utf-8") + "</script>")
    for js in ("common", "screen1_predict", "screen1_alerts", "screen2_history", "screen3_perf", "app"):
        html = html.replace(f'<script src="static/{js}.js"></script>',
                            "<script>\n" + (WEB_DIR / "static" / f"{js}.js").read_text(encoding="utf-8") + "</script>")
    path = Path(out or REPORT_DIR / "peakguard_demo.html")
    path.parent.mkdir(exist_ok=True)
    path.write_text(html, encoding="utf-8")
    print(f"저장: {path} ({path.stat().st_size / 1024 / 1024:.1f} MB, 재생 {len(dates)}일 × {len(replay[dates[0]]['models'])}개 모델)")
    return path


def main() -> None:
    ap = argparse.ArgumentParser(description="시연용 정적 HTML 내보내기")
    ap.add_argument("--date", default="2026-10-28", help="처음 열 때 재생 기준일")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    export(a.date, a.model, a.out)


if __name__ == "__main__":
    main()
