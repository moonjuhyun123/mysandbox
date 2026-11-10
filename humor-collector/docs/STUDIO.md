# 대본 만들기 (`plot`, `script`)

수집한 인기 글 하나를 골라 4컷 만화 대본으로 만드는 단계입니다. 두 단계로 나뉩니다.

```
수집한 글 ──(plot)──▶ 플롯: 캐릭터 없이 역할(A, B)로만 쓴 4컷 대본
플롯 + 캐릭터 묶음 ──(script)──▶ 최종 대본: 컷별 장면·표정·대사, 게시글 본문, 그림 규격
```

캐릭터는 플롯에 들어 있지 않고 파라미터(캐릭터 묶음 파일)로 받습니다. 같은 플롯에 다른 파일을 주면
다른 캐릭터로 대본이 나옵니다. 그림 규격과 제작 방식 표시도 설정으로 받습니다.

이 단계는 LLM(글 쓰는 AI)을 씁니다. 두 가지 방법이 있습니다.

| 방법 | 필요한 것 | 품질 관리 |
|---|---|---|
| 채팅에 붙여 넣기(`--prompt-only`) | 쓰던 채팅 LLM(예: Claude 앱). 추가 비용 없음 | 지시문 1개 → 답 1개. 자동 검사는 같음 |
| API 자동 호출 | `ANTHROPIC_API_KEY`, `pip install -r requirements-llm.txt`, 설정에서 켜기. **유료** | 초안 3개 → 편집자 채점 → 고쳐 쓰기 → 자동 검사에 걸리면 다시 |

API는 `config/settings.toml`의 `[studio.llm] enabled = true`로 켜기 전에는 한 번도 부르지 않습니다.

## 1. 소재 고르기

```bash
.venv/bin/python main.py plot candidates --days 14        # 본문이 있는 인기 글, 점수순
.venv/bin/python main.py plot rate --prompt-only          # 어떤 글이 만화가 되는지 LLM에 묻는 지시문
.venv/bin/python main.py plot rate                        # (API) 4컷 적합·공감·웃음 점수와 각색 아이디어
```

인기 점수가 높다고 좋은 소재는 아닙니다. 2026-11-10에 인기 글 45개를 채점해 보니 두 채점자가 모두
추천한 글은 8개였고, 나머지는 대부분 정치·종교·실존 인물·비극이라 빠졌습니다.

## 2. 플롯 쓰기

```bash
# 채팅 방식
.venv/bin/python main.py plot write 400 --prompt-only > 지시문.txt   # 이 글을 채팅 LLM에 붙여 넣음
.venv/bin/python main.py plot import 400 답.json --dry-run           # 검사만
.venv/bin/python main.py plot import 400 답.json                     # 검사 + 저장
.venv/bin/python main.py plot fix 3 --prompt-only                    # 검사에 걸렸을 때 고칠 지시문

# API 방식
.venv/bin/python main.py plot write 400            # 초안 3개 → 채점 → 고쳐 쓰기 → 저장
.venv/bin/python main.py plot write 400 --no-classics   # 이번에는 고전 개그 없이

.venv/bin/python main.py plot list
.venv/bin/python main.py plot show 3               # 읽기 좋은 문서로 보기 (--json은 원본)
```

플롯에 들어 있는 것: 제목, 한 줄 요약, 웃음 구조, 보내고 싶은 사람, 역할(A·B와 필요한 성격),
첫 컷 문구 후보 3개와 고른 것, 컷 4개(장면, 행동·표정, 대사, 내레이션, 효과음, 영상용 시간),
게시글 본문, 댓글 질문, 해시태그, 쓴 고전 개그와 신조어, 원문에서 바꾼 점.

### 고전 개그와 신조어

- `reference/classics.toml`의 개그·밈 410개와 신조어 164개 **목록 전체**가 지시문에 들어갑니다.
  LLM은 목록에 있는 것만 쓸 수 있습니다.
- 고전 개그는 이야기에 자연스럽게 맞을 때만, 한 편에 최대 1개입니다. 대부분의 플롯은 쓰지 않는 것이 정상입니다.
  편집 단계에서 억지스럽다고 판정되면 뺍니다.
- 쓴 개그의 **주의 사항**(실존 인물, 상표, 피할 시기 등)은 고쳐 쓰는 단계에 전달되고, 최종 대본에도 그대로 적혀 나옵니다.
- 최근 15개 플롯에서 쓴 개그는 피합니다(`classic_cooldown`).
- 사전의 "일부러 뺀 것"에 있는 밈이 글에 보이면 오류로 막습니다.
- 캐릭터에만 맞는 말장난(예: 특정 음식 이름으로 하는 개그)은 플롯에 넣지 않습니다. 캐릭터를 입힐 때,
  플롯에 고전 개그가 없고 캐릭터 파일에 `food`가 적혀 있으면 사전에서 그 음식에 맞는 개그를 후보로 줍니다.

### 자동 검사 (LLM 없이 프로그램이 판정)

오류가 하나라도 있으면 플롯은 "고칠 것 있음"으로 저장되고, 캐릭터를 입힐 수 없습니다.

| 검사 | 기준(설정으로 바꿀 수 있음) |
|---|---|
| 구조 | 4컷, 역할 3명 이하, 대사의 역할이 역할 목록에 있음 |
| 글자 수 | 컷당 대사 2개 이하, 대사 24자 이하, 내레이션 30자 이하, 첫 컷 문구 20자 이하, 해시태그 5개 이하 |
| 원문과 겹침 | 연속으로 같은 글자 12자 이상이면 오류, 한 문장이 원문과 85점 이상 닮으면 오류, 전체 겹침이 크면 경고 |
| 금지 밈 | 사전의 "일부러 뺀 것"에 있는 표현 |
| 사전 밖 개그 | `classics`에 적은 이름이 사전에 없으면 오류, 한 편에 2개 이상이면 오류 |
| 개인 정보 | 링크, 계정 이름(@), 이메일, 전화번호 |
| 참여 요구 | 본문·댓글 질문·첫 컷 문구에서 태그·좋아요·팔로우 요구, 정해진 댓글 요청, 추첨 |
| 빈 항목 | 원문에서 바꾼 점이 비어 있으면 오류 |

프로그램이 판정할 수 없는 것은 사람이 봐야 합니다. 원문과 다른 이야기로 읽히는지, 웃긴지, 누군가를
깎아내리지 않는지입니다. 대본 끝의 "내부 기록"에 참고한 글 번호와 주소가 남으니 올리기 전에 원문과 나란히 놓고 보세요.

## 3. 캐릭터 입히기

```bash
.venv/bin/python main.py script cast 3 --cast config/casts/example.toml               # LLM 없이: 역할 배정만
.venv/bin/python main.py script cast 3 --cast config/casts/example.toml --as A=mandu,B=ramen
.venv/bin/python main.py script cast 3 --cast config/casts/example.toml --prompt-only  # 말투까지 다듬는 지시문
.venv/bin/python main.py script import 3 --cast config/casts/example.toml 답.json
.venv/bin/python main.py script cast 3 --cast config/casts/example.toml --polish       # (API) 말투까지 다듬기

.venv/bin/python main.py script list
.venv/bin/python main.py script show 1 --output 대본.md
```

- **LLM 없이**: 역할의 성격과 캐릭터의 성격이 가장 겹치는 쪽으로 배정하고, 대사는 플롯 그대로 둡니다.
- **다듬기**: 대사를 각 캐릭터의 말투로 고치고, 장소와 소품을 세계관에 맞게 바꾸고, 그림 메모를 씁니다.
  이야기와 컷 수는 바꾸지 않습니다. 결과는 같은 자동 검사를 다시 거칩니다.

### 캐릭터 묶음 파일

`config/casts/example.toml`을 복사해 고쳐 쓰세요.

```toml
name = "분식 친구들"
world = "음식들이 사람처럼 학교와 회사에 다니는 동네"
style = "굵은 선, 단순한 얼굴, 파스텔 배경"

[[character]]
id = "tteok"                 # --as A=tteok 처럼 가리킬 때 쓰는 이름
name = "떡볶이"
look = "빨간 양념을 두른 통통한 가래떡"
personality = ["직설적", "화끈함"]   # 역할 배정에 씀
speech = "반말. 짧고 세게 말한다"
food = "떡볶이"               # 없어도 됨. 사전에서 이 음식에 맞는 개그를 찾을 때 씀
```

## 4. 대본 문서

`script show`가 내는 문서의 구성입니다.

1. 한 줄 요약, 첫 컷 문구와 다른 후보, 웃음 구조, 보내고 싶은 사람
2. 등장인물 표(역할, 캐릭터, 생김새, 말투)
3. 컷별 대본: 장면, 행동·표정, 대사, 효과음, 영상용 시간, 대체 텍스트
4. 그림 메모와 그림 규격(`[studio.format]`)
5. 게시글: 본문, 댓글 질문, 해시태그
6. 쓰인 개그와 그 주의 사항·출처
7. 자동 검사 결과(원문과 겹친 정도 포함)
8. 올릴 때 확인(`[studio.production]`)
9. 내부 기록: 참고한 글, 바꾼 점 (게시하지 않음)

## 저장

- `plots`, `scripts` 테이블에 저장합니다(`data/humor.db`).
- 원본 글이 삭제되면(예: Bluesky 작성자의 삭제 요청) 플롯은 남고 원본 연결만 끊깁니다. 플롯에는 원문 문장이
  들어 있지 않기 때문입니다.

## 실행해 본 것과 못 해 본 것

- 자동 테스트는 LLM 없이 돕니다(`tests/test_studio.py`).
- **Claude API 호출 경로(`studio/llm.py`)는 실제로 불러 보지 못했습니다.** 이 환경에 키가 없고, 유료 호출은 소유자의
  결정이기 때문입니다. SDK 2.7.0에서 요청 인자가 받아들여지는지만 확인했습니다.
- 샘플 대본은 이 프로그램이 만든 지시문을 그대로 다른 AI에게 주고, 그 답을 `plot import`·`script import`로
  검사해 만든 것입니다. API 방식의 단계(초안 3개 → 채점 → 고쳐 쓰기)를 같은 지시문으로 따라갔습니다.
