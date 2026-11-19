"""[6] 화면 — 하루치 관리자 화면을 서버 없이 열리는 HTML 한 파일로 내보내기 (발표·시연용)

    python run.py 6 --date 2026-10-28 --model LightGBM
    → reports/peakguard_2026-10-28_LightGBM.html (더블클릭으로 열림, 회사 데이터가 들어 있으니 외부 공유 주의)
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..config import DEFAULT_MODEL, REPORT_DIR, WEB_DIR


def export(date: str, model: str = DEFAULT_MODEL, out: str | None = None) -> Path:
    from ..api import dashboard
    from ..api.common import info, room_key
    data = dashboard.replay(date, model)
    channels = {}
    for cid in info()["rooms"]:
        try:
            channels[room_key(cid)] = dashboard.channel_line(date, room_key(cid), model)
        except Exception:      # 데이터 없는 채널은 건너뜀
            pass
    from ..api.compare import sum_compare
    from ..reports.executive import build
    try:
        sumcmp = sum_compare(date)
    except Exception:
        sumcmp = None
    report = {p: build(p, date) for p in ("day", "week", "month", "year")}
    for r in report.values():
        r.pop("series", None)
    html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    css = (WEB_DIR / "static" / "style.css").read_text(encoding="utf-8")
    bg = (WEB_DIR / "static" / "bg.js").read_text(encoding="utf-8")
    app = (WEB_DIR / "static" / "app.js").read_text(encoding="utf-8")
    inject = ("<script>window.PEAKGUARD_DATA = " + json.dumps(data, ensure_ascii=False) + ";\n"
              "window.PEAKGUARD_CHANNELS = " + json.dumps(channels, ensure_ascii=False) + ";\n"
              "window.PEAKGUARD_SUMCMP = " + json.dumps(sumcmp, ensure_ascii=False) + ";\n"
              "window.PEAKGUARD_REPORT = " + json.dumps(report, ensure_ascii=False) + ";</script>")
    html = html.replace('<link rel="stylesheet" href="static/style.css">', "<style>\n" + css + "</style>")
    html = html.replace('<script src="static/bg.js"></script>', inject + "\n<script>\n" + bg + "</script>")
    html = html.replace('<script src="static/app.js"></script>', "<script>\n" + app + "</script>")
    html = html.replace('<select id="dsel"', '<select hidden id="dsel"').replace('<select id="msel"', '<select hidden id="msel"')
    path = Path(out or REPORT_DIR / f"peakguard_{date}_{model}.html")
    path.parent.mkdir(exist_ok=True)
    path.write_text(html, encoding="utf-8")
    print(f"저장: {path} ({path.stat().st_size / 1024:.0f} KB)")
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
