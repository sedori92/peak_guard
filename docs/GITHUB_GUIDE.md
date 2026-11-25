# 깃허브 공동 작업 안내 (8·9단계)

## 0. 팀장이 한 번만: 팀원 초대와 main 보호

> GitHub 화면 메뉴는 영어 그대로 적고 (괄호) 안에 뜻을 달았습니다. 브라우저 자동 번역을 켜면 메뉴 이름이 엉뚱하게 바뀌므로
> (예: Blame → "비난", 한글 문장도 다시 번역되어 "무엇을 바치기나요"처럼 깨짐) **github.com 은 번역하지 않음**으로 두세요.

1. **팀원 초대**: 저장소 → **Settings**(설정) → 왼쪽 **Collaborators**(함께 작업하는 사람) → **Add people**(사람 추가) → 팀원 GitHub 아이디 입력 → 초대.
   팀원은 메일이나 GitHub 알림에서 초대를 **Accept**(수락).
2. **main 보호** (실수로 main을 덮어쓰지 않게):
   **Settings** → **Rules → Rulesets**(규칙 묶음) → **New ruleset → New branch ruleset**(새 브랜치 규칙)
   - Ruleset name(이름): `main 보호`, **Enforcement status**(적용 여부): **Active**(켜짐)
   - **Target branches**(대상 브랜치): **Add target → Include default branch**(기본 브랜치 = main 포함)
   - **Require a pull request before merging**(합치기 전에 Pull Request 검토 필수) 체크, 승인 1명
   - **Block force pushes**(강제로 덮어쓰기 막기) 체크 → **Create**(만들기)
3. (선택) `.github/CODEOWNERS` 파일에 파트별 담당자 아이디를 적으면, 그 폴더를 바꾼 PR(Pull Request, 변경 요청)에 담당자가 자동으로 검토자로 지정됩니다.

## 1. 팀원이 처음 한 번: 내려받기

```bash
git clone https://github.com/sedori92/peak_guard.git   # 저장소를 내 컴퓨터로 복사
cd peak_guard                                           # 복사한 폴더로 이동
pip install -r requirements.txt                         # 필요한 파이썬 프로그램 설치
```
- **데이터는 깃허브에 없습니다.** 구글 드라이브 팀 폴더에서 받아
  `data/raw/`(전력 CSV 8개), `data/weather/`(ASOS CSV)에 넣으세요.
- 그다음 `python run.py 1` → `python run.py 2` → `python run.py 3` 순서로 실행합니다.

## 2. 매번 작업 순서

```bash
git switch main                           # 0) main(기준 줄기)으로 이동
git pull                                  # 1) 최신 내용 받기
git switch -c front/채널그래프              # 2) 내 작업 브랜치 만들기 (파트/내용)
#   ... 내 파트 파일 수정 ...
python run.py 7                           # 3) 동작확인 (시험 통과 확인)
python run.py 8 -m "[Front] 채널 그래프에 일 피크 표시 추가"   # 4) 반영(커밋)
python run.py 9                           # 5) 푸시
```
6) GitHub 저장소 화면에 뜨는 **Compare & pull request**(비교하고 변경 요청 만들기) 버튼 → 바꾼 내용을 적고 **Create pull request**(변경 요청 만들기)
7) 다른 팀원 1명이 확인 **Approve**(승인) → **Merge pull request**(main에 합치기) → **Delete branch**(작업 브랜치 지우기)

### 커밋 메시지 규칙
`[파트] 무엇을 바꿨는지` — 예) `[Back] /api/channel 에 일 피크 추가`, `[DB] measurement 인덱스 추가`, `[Model] 기온 특징 추가`

## 3. 올리면 안 되는 것 (`.gitignore` 로 막혀 있음)
- `data/` 원본 데이터, `peakguard.db`, `models/`, `.env`(비밀번호), `reports/*.html`(회사 데이터가 들어간 시연 파일)

## 4. 충돌이 났을 때
`run.py 9` 에서 "충돌"이 나오면:
1. `git status`(상태 보기) 로 충돌 파일 확인
2. 파일을 열어 `<<<<<<<`, `=======`, `>>>>>>>` 표시 사이에서 남길 내용만 남기고 표시 줄 삭제
3. `git add 파일이름`(고친 파일 표시) → `git rebase --continue`(이어서 진행) → 다시 `python run.py 9`
4. 어려우면 `git rebase --abort`(취소하고 원래대로) 로 되돌리고 팀장에게 문의

## 5. 웹에서 바로 고치기 (간단한 수정)
저장소 화면 → 파일 클릭 → 오른쪽 위 **연필 아이콘**(Edit this file, 파일 고치기) → 수정 → **Commit changes**(변경 저장) →
"**Create a new branch for this commit and start a pull request**"(새 브랜치에 저장하고 변경 요청 시작) 선택 → **Propose changes**(변경 제안) → PR 만들기
