"""피크가드 실행기 — 번호만 입력하면 해당 단계를 실행합니다.

    python run.py              # 단계 목록 보기
    python run.py 1            # 환경설정 점검
    python run.py 2            # DB 구축 (테이블 생성 + 전력 + 기상 적재)
    python run.py 3            # 예측모델 학습·평가·저장  (--skip-lstm 로 Bi-LSTM 제외)
    python run.py 4            # 예측모델별 평가 표·그래프
    python run.py 5            # API 서버 + 관리자 화면 (http://localhost:8000)
    python run.py 6            # 화면: 시연용 HTML 한 파일 내보내기 (--date --model)
    python run.py 7            # 동작확인 (자동 시험)
    python run.py 8 -m "메시지"  # 깃허브 반영 (변경 파일 확인 → 커밋)
    python run.py 9            # 깃허브 푸시 (최신 내용 받아온 뒤 올리기)
    python run.py 10 --period week --date 2026-10-28   # 주간·월간 보고서
    python run.py 10x --period month --date 2026-10-15 # 최고관리자 피크 보고서 (.xlsx, 일·주·월·연)
    python run.py 11 --file 새파일.csv                  # CSV → API 업로드
    python run.py eda          # 사용패턴·피크 분석 그래프
    python run.py calendar     # 공휴일 달력 CSV
    python run.py reviews      # 앱 리뷰 수집 (인터넷 필요)
"""
from __future__ import annotations

import subprocess
import sys

STEPS = {
    "1": ("환경설정 점검", "peakguard/setup_check.py"),
    "2": ("DB 구축", "db/schema.sql, peakguard/database/, peakguard/collect/"),
    "3": ("예측모델 학습 (LightGBM·RandomForest·Bi-LSTM)", "peakguard/models/"),
    "4": ("예측모델별 평가 결과 표·그래프", "peakguard/evaluation/"),
    "5": ("API 서버 + 관리자 화면", "peakguard/api/, web/"),
    "6": ("화면: 시연용 HTML 내보내기", "peakguard/tools/export_static.py"),
    "7": ("동작확인", "tests/"),
    "8": ("깃허브 반영 (커밋)", "docs/GITHUB_GUIDE.md"),
    "9": ("깃허브 푸시", "docs/GITHUB_GUIDE.md"),
    "10": ("보고서 (주간·월간) 텍스트", "peakguard/reports/periodic.py"),
    "10x": ("최고관리자 피크 보고서 스프레드시트 (일·주·월·연)", "peakguard/reports/executive.py"),
    "11": ("CSV → API 업로드", "peakguard/api/upload.py, peakguard/tools/send_csv.py"),
    "eda": ("사용패턴·피크 분석", "peakguard/eda/patterns.py"),
    "calendar": ("공휴일 달력", "peakguard/collect/calendar_kr.py"),
    "reviews": ("앱 리뷰 수집", "peakguard/collect/reviews.py"),
}


def sh(cmd: list[str]) -> int:
    print("$", " ".join(cmd))
    return subprocess.call(cmd)


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] not in STEPS:
        print(__doc__)
        for k, (name, where) in STEPS.items():
            print(f"  {k:>8}  {name:34s} {where}")
        return
    step, rest = sys.argv[1], sys.argv[2:]
    sys.argv = [f"run.py {step}"] + rest
    print(f"=== [{step}] {STEPS[step][0]} ===")
    if step == "1":
        from peakguard.setup_check import main as m; m()
    elif step == "2":
        from peakguard.collect.weather import load as load_weather
        from peakguard.database.load_power import load as load_power
        load_power(reset="--reset" in rest)
        load_weather()
    elif step == "3":
        from peakguard.models.train import main as m; m()
    elif step == "4":
        from peakguard.evaluation.report import main as m; m()
    elif step == "5":
        port = rest[rest.index("--port") + 1] if "--port" in rest else "8000"
        print(f"브라우저에서 http://localhost:{port}  (API 문서 http://localhost:{port}/docs, 종료 Ctrl+C)")
        sh([sys.executable, "-m", "uvicorn", "peakguard.api.app:app", "--port", port])
    elif step == "6":
        from peakguard.tools.export_static import main as m; m()
    elif step == "7":
        sys.exit(sh([sys.executable, "-m", "pytest", "-q", "tests"]))
    elif step == "8":
        msg = rest[rest.index("-m") + 1] if "-m" in rest else None
        sh(["git", "status", "--short"])
        if not msg:
            print('\n커밋하려면: python run.py 8 -m "무엇을 바꿨는지"')
            return
        sh(["git", "add", "-A"])
        sh(["git", "commit", "-m", msg])
    elif step == "9":
        branch = subprocess.check_output(["git", "rev-parse", "--abbrev-ref", "HEAD"], text=True).strip()
        if sh(["git", "pull", "--rebase", "origin", branch]) != 0:
            print("충돌이 있습니다. docs/GITHUB_GUIDE.md 의 '충돌 해결'을 보세요.")
            return
        sh(["git", "push", "-u", "origin", branch])
    elif step == "10":
        from peakguard.reports.periodic import main as m; m()
    elif step == "10x":
        from peakguard.reports.executive import main as m; m()
    elif step == "11":
        from peakguard.tools.send_csv import main as m; m()
    elif step == "eda":
        from peakguard.eda.patterns import main as m; m()
    elif step == "calendar":
        from peakguard.collect.calendar_kr import main as m; m()
    elif step == "reviews":
        from peakguard.collect.reviews import main as m; m()


if __name__ == "__main__":
    main()
