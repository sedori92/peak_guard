"""[11] CSV → API 붙여 보내기 — 새 전력 CSV 파일을 API 서버로 보내 DB에 추가

연결 ─ 화면: — (명령줄에서 CSV 보내기, python run.py 11)
       API : POST /api/upload 호출
       DB  : —

    python run.py 11 --file data/new/생활지원로봇센터_1층_공용.csv
    python run.py 11 --file new.csv --dry-run              # 검사만 하고 저장하지 않음
    python run.py 11 --file new.csv --url http://서버주소:8000

API 서버(run.py 5)가 먼저 실행 중이어야 합니다.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import httpx


def send(path: str, url: str = "http://localhost:8000", dry_run: bool = False) -> dict:
    p = Path(path)
    with p.open("rb") as f:
        r = httpx.post(f"{url.rstrip('/')}/api/upload", params={"dry_run": str(dry_run).lower()},
                       files={"file": (p.name, f, "text/csv")}, timeout=300)
    if r.status_code != 200:
        raise SystemExit(f"업로드 실패 ({r.status_code}): {r.text}")
    return r.json()


def main() -> None:
    ap = argparse.ArgumentParser(description="CSV 를 API 로 업로드")
    ap.add_argument("--file", required=True, nargs="+", help="보낼 CSV 파일(여러 개 가능)")
    ap.add_argument("--url", default="http://localhost:8000")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    for f in a.file:
        res = send(f, a.url, a.dry_run)
        print(f"■ {f}")
        print(json.dumps(res, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
