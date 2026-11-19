"""하루치 재생 화면을 서버 없이 열리는 HTML 한 파일로 내보내기 (발표·시연용)

    python -m peakguard.export_static --date 2026-10-28 --model LightGBM
    → reports/peakguard_2026-10-28_LightGBM.html (더블클릭으로 열림)
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import api


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="2026-10-28")
    ap.add_argument("--model", default="LightGBM")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    data = api.replay(a.date, a.model)
    html = api.WEB.read_text(encoding="utf-8")
    inject = "<script>window.PEAKGUARD_DATA = " + json.dumps(data, ensure_ascii=False) + ";</script>\n"
    html = html.replace('<canvas id="bgnet"', inject + '<canvas id="bgnet"', 1)
    # 정적 파일에서는 날짜·모델 선택 상자를 숨김
    html = html.replace('<select id="dsel"', '<select hidden id="dsel"').replace('<select id="msel"', '<select hidden id="msel"')
    out = Path(a.out or f"reports/peakguard_{a.date}_{a.model}.html")
    out.parent.mkdir(exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"저장: {out} ({out.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
