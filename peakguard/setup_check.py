"""[1] 환경설정 — 실행 환경 점검

    python run.py 1

파이썬 버전, 필요한 패키지, 데이터 폴더, DB 연결과 테이블 상태를 한 번에 확인합니다.
처음 내려받은 팀원은 이것부터 실행하세요.
"""
from __future__ import annotations

import importlib
import sys

from .config import DATA_RAW, DATA_WEATHER, DATABASE_URL, MODEL_DIR, ROOT

PACKAGES = ["pandas", "numpy", "sqlalchemy", "dotenv", "holidays", "sklearn", "lightgbm", "torch",
            "joblib", "matplotlib", "fastapi", "uvicorn", "multipart", "httpx"]


def check() -> bool:
    ok = True
    print(f"■ 파이썬 {sys.version.split()[0]}", "✓" if sys.version_info >= (3, 10) else "✗ (3.10 이상 필요)")
    print("■ 패키지")
    for p in PACKAGES:
        try:
            m = importlib.import_module(p)
            print(f"  ✓ {p} {getattr(m, '__version__', '')}")
        except Exception:
            ok = False
            print(f"  ✗ {p}  → pip install -r requirements.txt")
    print("■ 데이터 폴더")
    n_raw = len(list(DATA_RAW.glob("*.csv"))) if DATA_RAW.exists() else 0
    n_w = len(list(DATA_WEATHER.glob("*ASOS*.csv"))) if DATA_WEATHER.exists() else 0
    print(f"  {'✓' if n_raw == 8 else '✗'} 전력 CSV {n_raw}개 ({DATA_RAW.relative_to(ROOT)}, 8개 필요)")
    print(f"  {'✓' if n_w else '△'} 기상 CSV {n_w}개 ({DATA_WEATHER.relative_to(ROOT)}, 없으면 기온만 빈칸)")
    ok &= n_raw == 8
    print("■ DB")
    try:
        from sqlalchemy import inspect, text
        from .database.connection import get_engine
        eng = get_engine()
        names = inspect(eng).get_table_names()
        print(f"  ✓ 연결: {eng.url.render_as_string(hide_password=True) if 'sqlite' not in DATABASE_URL else DATABASE_URL.split('/')[-1]}")
        with eng.connect() as con:
            for t in ("measurement", "weather_15m", "st_forecast", "st_eval"):
                n = con.execute(text(f"SELECT COUNT(*) FROM {t}")).scalar() if t in names else None
                mark = "✓" if n else "·"
                print(f"  {mark} {t:12s} {'없음 (아직 생성 전)' if n is None else f'{n:,}행'}")
    except Exception as e:
        ok = False
        print(f"  ✗ DB 오류: {e}")
    print("■ 모델 파일")
    for f in ("st_LightGBM.joblib", "st_RandomForest.joblib", "st_BiLSTM.pt"):
        print(f"  {'✓' if (MODEL_DIR / f).exists() else '·'} {f}")
    print("\n결과:", "준비 완료" if ok else "위의 ✗ 항목을 해결하세요")
    return ok


def main() -> None:
    check()


if __name__ == "__main__":
    main()
