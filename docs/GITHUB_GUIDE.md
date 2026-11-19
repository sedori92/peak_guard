# 깃허브 공동 작업 안내 (8·9단계)

## 0. 팀장이 한 번만: 팀원 초대와 main 보호

1. **팀원 초대**: 저장소 → **설정(Settings)** → 왼쪽 **협력자(Collaborators)** → **사람 추가(Add people)** → 팀원 GitHub 아이디 입력 → 초대.
   팀원은 메일이나 GitHub 알림에서 **초대 수락**.
2. **main 보호** (실수로 main을 덮어쓰지 않게):
   설정 → **규칙 세트(Rulesets)** → **새로운 규칙 세트 → 새 브랜치 규칙 세트**
   - 이름: `main 보호`, 적용 상태(Enforcement): **활성(Active)**
   - 대상 브랜치: **기본 브랜치 포함(Include default branch)**
   - **끌어오기 요청 필요(Require a pull request before merging)** 체크, 승인 1명
   - **강제 푸시 차단(Block force pushes)** 체크 → 만들기
3. (선택) `.github/CODEOWNERS` 파일에 파트별 담당자 아이디를 적으면, 그 폴더를 바꾼 PR에 담당자가 자동으로 검토자로 지정됩니다.

## 1. 팀원이 처음 한 번: 내려받기

```bash
git clone https://github.com/sedori92/peak_guard.git
cd peak_guard
pip install -r requirements.txt
```
- **데이터는 깃허브에 없습니다.** 구글 드라이브 팀 폴더에서 받아
  `data/raw/`(전력 CSV 8개), `data/weather/`(ASOS CSV)에 넣으세요.
- 그다음 `python run.py 1` → `python run.py 2` → `python run.py 3` 순서로 실행합니다.

## 2. 매번 작업 순서

```bash
git switch main
git pull                                  # 1) 최신 내용 받기
git switch -c front/채널그래프              # 2) 내 작업 브랜치 만들기 (파트/내용)
#   ... 내 파트 파일 수정 ...
python run.py 7                           # 3) 동작확인 (시험 통과 확인)
python run.py 8 -m "[Front] 채널 그래프에 일 피크 표시 추가"   # 4) 반영(커밋)
python run.py 9                           # 5) 푸시
```
6) GitHub 저장소 화면에 뜨는 **Compare & pull request** 버튼 → 무엇을 바꿨는지 적고 **PR 만들기**
7) 다른 팀원 1명이 확인(Approve) → **Merge** → 브랜치 삭제

### 커밋 메시지 규칙
`[파트] 무엇을 바꿨는지` — 예) `[Back] /api/channel 에 일 피크 추가`, `[DB] measurement 인덱스 추가`, `[Model] 기온 특징 추가`

## 3. 올리면 안 되는 것 (`.gitignore` 로 막혀 있음)
- `data/` 원본 데이터, `peakguard.db`, `models/`, `.env`(비밀번호), `reports/*.html`(회사 데이터가 들어간 시연 파일)

## 4. 충돌이 났을 때
`run.py 9` 에서 "충돌"이 나오면:
1. `git status` 로 충돌 파일 확인
2. 파일을 열어 `<<<<<<<`, `=======`, `>>>>>>>` 표시 사이에서 남길 내용만 남기고 표시 줄 삭제
3. `git add 파일이름` → `git rebase --continue` → 다시 `python run.py 9`
4. 어려우면 `git rebase --abort` 로 되돌리고 팀장에게 문의

## 5. 웹에서 바로 고치기 (간단한 수정)
저장소 화면 → 파일 클릭 → 오른쪽 위 **연필 아이콘** → 수정 → 아래 **Commit changes** →
"**Create a new branch for this commit and start a pull request**" 선택 → PR 만들기
