# 인기 유머 글로 4컷 만화 대본 만들기

커뮤니티에서 반응이 좋았던 유머 글을 모으고, 그중 하나를 골라 **4컷 만화 대본과 그림 생성용 지시문**으로 바꾸는 도구입니다.
인스타그램에 캐릭터 만화를 올리는 계정을 위해 만들었습니다. 프로그램은 `humor-collector/` 폴더에 있습니다.

```
인기 글 수집 ─▶ 소재 고르기 ─▶ 플롯 쓰기 ─▶ 캐릭터 입히기 ─▶ 대본 문서 ─▶ 그림 지시문 ─▶ 그림 만들기·올리기
  collect      plot candidates   plot write     script cast      script show    script prompts      (사람이 함)
```

| 프로그램이 하는 일 | 사람이 하는 일 |
|---|---|
| 인기 글 수집, 중복 묶기, 인기 점수 매기기 | 어떤 글로 만들지 고르기 |
| LLM에 줄 지시문 만들기, LLM의 답 검사하기(원문을 베꼈는지, 글자 수, 금지 표현, 개인 정보) | 지시문을 채팅 LLM에 붙여 넣고 답을 파일로 저장하기 |
| 캐릭터 배정, 대본 문서, 그림 생성 도구에 붙여 넣을 지시문 만들기 | 그림 생성 도구로 그림 만들기, 글자 확인, 게시 |

결과물이 어떻게 생겼는지 먼저 보려면 [샘플 대본 6편](humor-collector/docs/samples/README.md)을 여세요. 설치 없이 볼 수 있습니다.

## 필요한 것

- Linux 또는 macOS, Python 3.10 이상. Windows에서는 WSL에서 실행하세요(실행 중복을 막는 잠금이 유닉스 전용입니다).
- 계정이나 API 키는 필요 없습니다. 수집은 로그인 없이 공개된 인기 글 목록만 읽습니다.
- 대본을 쓰는 단계에는 쓰던 채팅 LLM(ChatGPT, Claude 등)을 씁니다. 지시문을 붙여 넣고 답을 받아 오는 방식이라 추가 비용이 없습니다.
- 유료 API를 직접 부르는 기능도 있지만 기본은 꺼져 있습니다(`[studio.llm] enabled = false`).
- YouTube에서 영상을 찾아보는 `youtube` 명령만 본인의 API 키가 필요합니다. 쓰지 않아도 나머지는 다 됩니다.

## 빠른 시작

아래 명령은 새로 받은 저장소에서 그대로 실행해 확인했습니다(2026-11-12).

### 1. 받아서 설치하기

```bash
git clone https://github.com/moonjuhyun123/mysandbox.git
cd mysandbox/humor-collector
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

### 2. 인기 글 수집하기

```bash
.venv/bin/python main.py collect --source all --limit 100
.venv/bin/python main.py stats            # 얼마나 모였는지 확인
```

처음 실행에 약 15분이 걸렸고 259건이 모였습니다(2026-11-12 측정, 소스마다 최대 100건). 사이트에 부담을 주지 않으려고
요청 사이에 간격을 두기 때문에 오래 걸립니다. 빨리 해 보려면 소스를 좁히세요. Bluesky만 받으면 1분 남짓입니다.

```bash
.venv/bin/python main.py collect --source bluesky --limit 100
```

끝나면 소스별 결과가 한 줄씩 나옵니다. `ok`가 아니라 `partial`인 소스가 있어도 나머지는 정상입니다
(예: Lemmy는 그날 올라온 글이 없는 목록이 있으면 `partial`로 표시됩니다). 다시 실행하면 새 글만 더합니다.

### 3. 만화로 만들 글 고르기

```bash
.venv/bin/python main.py plot candidates --days 14     # 본문이 있는 인기 글, 점수순. 맨 앞의 #숫자가 글 번호
.venv/bin/python main.py show 400                      # 글 하나 자세히 보기(400은 예시 번호)
```

인기 점수가 높다고 좋은 소재는 아닙니다. 정치, 실존 인물, 비극이 중심인 글은 피하세요.
어떤 글이 만화가 될 만한지 LLM에게 물어보려면 `plot rate --prompt-only`가 만든 지시문을 채팅 LLM에 붙여 넣으세요.

### 4. 플롯 쓰기 (채팅 LLM 사용)

```bash
.venv/bin/python main.py plot write 400 --prompt-only > 지시문.txt
```

1. `지시문.txt`의 내용 전체를 채팅 LLM에 붙여 넣습니다. 개그 사전 목록이 들어 있어서 깁니다(약 8만 자). 줄이지 말고 통째로 넣으세요.
2. LLM이 준 답(JSON)을 `답.json`으로 저장합니다. 앞뒤에 설명 문장이나 코드 블록 표시가 붙어 있어도 됩니다.
3. 검사하고 저장합니다.

```bash
.venv/bin/python main.py plot import 400 답.json
```

`플롯 #1 저장 (통과)`가 나오면 됩니다. `확인:`으로 시작하는 줄은 참고 사항이라 그대로 두어도 됩니다.
`오류:`가 있어서 `고칠 것 있음`이 나오면 아래처럼 고칠 지시문을 받아 같은 방법으로 다시 넣습니다.

```bash
.venv/bin/python main.py plot fix 1 --prompt-only > 고칠지시문.txt
```

플롯에는 캐릭터가 없습니다. 등장인물을 역할(A, B)로만 써 두고, 캐릭터는 다음 단계에서 입힙니다.

### 5. 캐릭터 입히기

```bash
.venv/bin/python main.py script cast 1 --cast config/casts/example.toml
```

LLM 없이 바로 됩니다. 역할과 성격이 맞는 캐릭터를 골라 붙이고, 대사는 플롯 그대로 둡니다.
대사를 캐릭터 말투로 다듬고 장면을 세계관에 맞게 고치려면 LLM을 한 번 더 씁니다.

```bash
.venv/bin/python main.py script cast 1 --cast config/casts/example.toml --prompt-only > 지시문2.txt
# 채팅 LLM에 붙여 넣고 답을 답2.json으로 저장한 뒤
.venv/bin/python main.py script import 1 --cast config/casts/example.toml 답2.json
```

다듬은 대본은 새 번호로 따로 저장됩니다. 번호는 `script list`로 확인하세요.

### 6. 대본과 그림 지시문 뽑기

```bash
.venv/bin/python main.py script list                                   # 대본 번호 확인
.venv/bin/python main.py script show 1 --output 대본.md                # 컷별 대본, 게시글 본문, 해시태그, 확인할 것
.venv/bin/python main.py script prompts 1 --one-image --output 그림지시문.md   # 4컷을 그림 한 장에
.venv/bin/python main.py script prompts 1 --output 그림지시문_컷별.md          # 컷마다 한 장
```

`그림지시문.md`를 열어 코드 블록 안의 글을 그림 생성 도구(예: ChatGPT)에 그대로 붙여 넣습니다. 쓰는 법은 그 파일 맨 위에 있습니다.

## 내 캐릭터로 바꾸기

`config/casts/example.toml`을 복사해 이름, 생김새, 성격, 말투를 고치고 `--cast`에 그 파일을 주면 됩니다.
같은 플롯에 다른 캐릭터 파일을 주면 다른 캐릭터로 대본이 나옵니다. 생김새(`look`)는 그림 지시문에 그대로 들어가므로
몸 모양과 색을 구체적으로 적을수록 그림이 덜 흔들립니다.

## 꼭 알아 둘 것

- **수집한 글은 참고 자료입니다. 그대로 다시 올리지 마세요.** 대본은 웃음의 구조만 빌리고 문장은 모두 새로 씁니다.
  프로그램이 원문과 겹치는 문장을 검사하지만, 사건의 순서까지 같은지는 사람이 봐야 합니다.
- **개그 사전은 저장소에 들어 있습니다.** 고전 개그·밈 410개, 신조어 164개, 일부러 뺀 것 118개가
  `humor-collector/reference/classics.toml`에 있어서 받으면 바로 쓰입니다.
- **수집한 글과 만든 대본은 저장소에 들어 있지 않습니다.** 내 컴퓨터의 `humor-collector/data/humor.db`에만 저장됩니다.
  받은 직후에는 비어 있으니 `collect`를 한 번 돌리세요. 남의 글 원문이 들어 있는 파일이라 공개 저장소에는 올리지 않습니다.
  다른 컴퓨터에서 같은 데이터를 쓰려면 이 파일을 직접 복사해 같은 자리에 두면 됩니다.
- **그림은 프로그램이 만들지 않습니다.** 지시문까지만 만듭니다. 그림 속 한글은 도구가 틀릴 수 있으니 직접 읽어 확인하세요.
- **고전 개그(밈)는 가끔만 들어갑니다.** 사전(`reference/classics.toml`)에 있는 것만, 자연스러울 때 한 편에 최대 1개입니다.
- 원글 작성자가 삭제를 요청하면 `forget` 명령으로 수집한 글을 지웁니다. 올린 만화는 직접 내려야 합니다.
- 접근이 막힌 사이트(로그인, CAPTCHA, 수집 금지)는 수집하지 않습니다. 우회하는 기능은 없습니다.
- 유료 Claude API를 직접 부르는 경로(`studio/llm.py`)는 실제로 호출해 보지 못했습니다. 키가 없는 환경에서 만들었기 때문입니다.

## 문서

| 문서 | 내용 |
|---|---|
| [humor-collector/README.md](humor-collector/README.md) | 수집기 설명서: 수집 소스, 모든 명령, 자동 실행(3시간마다), 동작 방식, 검증 기록 |
| [docs/STUDIO.md](humor-collector/docs/STUDIO.md) | 대본 단계 설명서: 플롯, 캐릭터, 자동 검사 목록, 그림 지시문 |
| [docs/samples](humor-collector/docs/samples/README.md) | 샘플 대본 6편과 그림 지시문 |
| [docs/GROWTH.md](humor-collector/docs/GROWTH.md) | 많이 보이게 하려고 넣은 규칙의 근거, AI 표시, 각색할 때의 저작권 |
| [reference/CLASSICS.md](humor-collector/reference/CLASSICS.md) | 고전 개그·밈 410개, 신조어 164개, 일부러 뺀 것 118개 |
| [docs/RESEARCH.md](humor-collector/docs/RESEARCH.md) | 어떤 사이트를 왜 수집하고 왜 뺐는지 |
| [docs/TERMS_AND_PRIVACY.md](humor-collector/docs/TERMS_AND_PRIVACY.md) | YouTube 조회 도구의 약관과 개인정보 안내 |

## 폴더 안내

```
humor-collector/
  main.py          모든 명령의 시작점 (python main.py <명령>)
  collectors/      사이트별 수집기
  processing/      중복 판정, 인기 점수, 분류
  storage/         SQLite 저장
  studio/          대본 단계: 지시문, 검사, 캐릭터, 대본 문서, 그림 지시문
  config/          settings.toml(설정), categories.toml(분류), casts/(캐릭터 묶음)
  reference/       고전 개그·밈 사전
  docs/            설명서와 샘플
  deploy/          자동 실행용 파일(systemd, cron)
  tests/           자동 테스트
  data/            수집한 글과 로그(저장소에 올라가지 않음)
```

## 테스트

```bash
cd humor-collector
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
```

네트워크와 LLM 없이 돕니다. `anthropic` 패키지를 설치하지 않았으면 그 패키지가 필요한 테스트 2개는 건너뜁니다.
