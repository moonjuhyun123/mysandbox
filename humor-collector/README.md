# humor-collector — 인기 유머 게시글 수집 엔진

여러 커뮤니티에서 **이미 많은 사람의 반응으로 검증된 유머·공감 게시글**을 모아 SQLite에 저장합니다.
제목·본문·원본 링크·인기 지표를 함께 저장하고, 중복과 재업로드를 판정한 뒤 커뮤니티별로
정규화한 인기 점수와 소재 분류를 붙여 JSONL로 내보냅니다. 4컷 만화 소재 분석의 입력 단계입니다.

- 사이트별 조사 결과와 선정 근거: [docs/RESEARCH.md](docs/RESEARCH.md)
- 수집한 원문은 **분석 참고자료**입니다. 그대로 재게시하지 마세요(아래 "정책 준수" 참고).
- 만화에 가끔 넣을 "모두가 아는" 고전 개그·밈 사전: [reference/CLASSICS.md](reference/CLASSICS.md)
  (데이터 `reference/classics.toml`, 개그·밈은 `[[classic]]`, 신조어·유행어는 `[[slang]]`). 수집기가 아니라 웹 조사로
  만든 정리본입니다. 항목마다 원본, 뜻, 쓰기 좋은 장면, 음식 캐릭터 활용, 주의 사항(출연자 논란·상표 등), 출처를
  적었습니다. `tests/test_classics.py`가 파일 형식을 검사합니다.

## 수집 소스

| 소스 | 방식 | 인기글 목록 | 확보 지표 | 상태 |
|---|---|---|---|---|
| `ruliweb` 루리웹 | HTML | 유머 BEST(실시간), 힛갤 + 과거 페이지 | 추천·비추천·조회·댓글, 상단 순위, 정확한 작성시각, 본문, 이미지 URL | 1차 구현 |
| `bobaedream` 보배드림 | HTML | 베스트 실시간·주간·월간 + **명예의 전당**(역대 순위) | 추천·조회·댓글, 순위, 원 게시판, 본문, 이미지 URL | 1차 구현 |
| `dogdrip` 개드립 | HTML(목록만) | 개드립 게시판 목록 | 추천·댓글, 인기 배지 순서, 대략적 시각. **본문 없음** | 보조(게시글 페이지가 Cloudflare 챌린지) |
| `lemmy` Lemmy | **공식 API** | c/funny·c/jokes의 TopDay/TopWeek + TopAll | upvote·downvote·댓글, 순위, 본문, 미디어 URL | 해외 소스 |
| `bluesky` Bluesky | **공식 공개 API**(계정 불필요) | 한국어 인기 피드 2개("30 리포스트 이상 잼얘 모음", "한국어 리포스트 많은 순") | 좋아요·댓글·리포스트·인용, 피드 순위, 본문, 이미지 URL | 비로그인 노출 거부 작성자·성인 라벨 글은 저장 안 함. `refresh`로 삭제된 글 정리 |

YouTube는 수집 소스가 아니라 **조회 도구**(`python main.py youtube ...`)입니다. 소유자의 API 키가
필요하고, YouTube 정책 때문에 결과를 별도로 짧게 캐시합니다(기본 24시간, 최대 29일). 자세한 내용은 아래
"밈·짤 찾기"를 보세요.

에펨코리아, 더쿠, 인스티즈, 디시인사이드, 네이트판, 웃긴대학, 뽐뿌, 9GAG, Reddit, Instagram, TikTok, X,
Threads, 네이버(카페·블로그·뉴스·웹툰), 오늘의유머는 수집하지 않습니다. 사유는 `python main.py sources`와 [docs/RESEARCH.md](docs/RESEARCH.md)에 있습니다
(CAPTCHA·챌린지, robots.txt 금지, 계정 필요 등). 로그인 우회, CAPTCHA 우회, 브라우저 위장 같은
차단 회피는 구현하지 않았습니다. 수집 중 챌린지를 받으면 해당 소스를 일정 시간 멈추고 사유를
기록합니다.

## 설치 (Ubuntu, Python 3.10+)

```bash
sudo apt install -y python3-venv          # 필요할 때만
cd /opt/humor-collector                   # 이 디렉터리를 원하는 위치에 두세요
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt          # 실행용
.venv/bin/pip install -r requirements-dev.txt      # 테스트까지
```

설정 파일은 `config/settings.toml`(소스, 요청 간격, 점수와 중복 기준)과
`config/categories.toml`(분류 키워드)입니다. `general.user_agent`에 연락처(이메일이나 URL)를
넣어 두기를 권합니다.

## 실행

```bash
# 최근 인기글 수집 (전체 소스, 소스당 최대 100건)
.venv/bin/python main.py collect --source all --limit 100

# 특정 사이트만 (쉼표로 여러 개 지정 가능)
.venv/bin/python main.py collect --source ruliweb --limit 100

# 과거 인기글: 명예의 전당, 과거 베스트 페이지. 지난번에 멈춘 페이지부터 이어서 수집
.venv/bin/python main.py collect --source bobaedream --mode archive --max-pages 3

# 목록에서 내려간 최근 3일 글의 숫자를 게시글 페이지에서 다시 읽어 갱신
.venv/bin/python main.py refresh --source ruliweb --max-age-days 3

# 인기 게시글 조회 (재업로드 묶음당 1건. 정치·선정·민감 플래그 글은 기본 제외, --all이면 포함)
.venv/bin/python main.py top --limit 50
.venv/bin/python main.py top --days 7 --category 음식·다이어트

# JSONL 내보내기 (기본 경로: data/exports/posts-YYYYmmdd-HHMMSS.jsonl)
.venv/bin/python main.py export --format jsonl
.venv/bin/python main.py export --format jsonl --require-body --min-score 60 --out picks.jsonl

# 수집 통계, 소스 상태, 최근 실행·오류
.venv/bin/python main.py stats

# 어댑터 목록과 제외 사이트 사유, 게시글 1건과 지표 이력
.venv/bin/python main.py sources
.venv/bin/python main.py show 42

# config/categories.toml을 고친 뒤 저장된 글 전체를 다시 분류
.venv/bin/python main.py reclassify

# Bluesky: 저장한 글을 다시 확인해 숫자를 갱신하고, 지워졌거나 비공개 전환된 글은 삭제
.venv/bin/python main.py refresh --source bluesky --max-age-days 3650 --limit 1000
```

## 밈·짤 찾기 (`trends`, `youtube`)

"담배피는 눈사람", "음료수 주르륵"처럼 지금 퍼지는 밈을 찾는 기능입니다.

**1. 커뮤니티에서 반복되는 문구 (`trends`, 키 불필요)**

여러 커뮤니티의 최근 인기글 제목에서 반복해 나타나는 문구를 찾습니다.

- 띄어쓰기와 조사가 달라도 같은 문구로 봅니다. 예: "담배피는 눈사람.jpg", "담배 피는 눈사람 근황"
- 서로 다른 글 3개 이상, 또는 서로 다른 커뮤니티 2곳 이상에서 나와야 합니다.
- 이전 기간보다 크게 늘어난 문구만 남깁니다. "근황", "레전드" 같은 일반 단어는 뺍니다.

```bash
.venv/bin/python main.py trends --days 7                      # 문구 + 예시 글 + 이미지 링크
.venv/bin/python main.py trends --days 7 --html trends.html   # 예시 이미지를 카드로 보는 HTML
```

**2. 그 문구로 YouTube 인기 영상 조회 (`youtube`, API 키 필요)**

```bash
export YOUTUBE_API_KEY=...                                     # 본인 Google Cloud 프로젝트의 키
.venv/bin/python main.py youtube accept-terms                  # 처음 한 번: 이용약관·개인정보 고지 동의
.venv/bin/python main.py youtube search "담배피는 눈사람" --html yt.html
.venv/bin/python main.py trends --youtube 5 --html trends.html # 상위 5개 문구를 YouTube에서도 검색
.venv/bin/python main.py youtube comments VIDEO_ID             # 베스트 댓글 (화면 표시만, 저장 안 함)
.venv/bin/python main.py youtube list --html yt.html           # 캐시된 결과 다시 보기
.venv/bin/python main.py youtube purge --all                   # YouTube 데이터와 페이지 즉시 전부 삭제
```

**키 발급:** Google Cloud Console에서 새 프로젝트를 만듭니다. "YouTube Data API v3"를 사용 설정하고,
사용자 인증 정보 → API 키를 만듭니다(무료). 키는 환경변수로만 넣으세요. 설정 파일이나 채팅에 붙이지
마세요. 키는 로그와 오류 기록에서 `***`로 가려집니다. 프로젝트는 하나만 쓰고, 한도를 늘리려고 키를
여러 개 만들지 마세요(III.D).

**한도:** 검색은 하루 100회까지입니다. 이 도구는 로컬에서 세다가 90회에서 멈춥니다
(`[youtube] daily_search_cap`). 같은 검색은 24시간 동안 캐시를 다시 씁니다. 한도는 태평양 시간 자정에
초기화됩니다.

**YouTube 정책 때문에 다르게 처리하는 점** (Developer Policies, 2026-10-15 개정). 독립 검토 2건을 거쳤고,
위험하다는 지적이 나온 기능은 뺐습니다.

- **III.E.2 (집계 금지):**
  - **트렌드 계산은 커뮤니티 데이터로만** 하고, YouTube 결과는 사람이 보는 검색 결과로만 씁니다.
    트렌드·점수·중복 판정 코드는 YouTube 테이블을 읽지 않으며, 테스트로 확인합니다.
  - 정기 자동 수집과 YouTube 인기 차트 복제는 하지 않습니다. 그래서 키워드 일괄 수집(`popular`)과 차트는
    넣지 않았고, cron에도 YouTube를 넣지 마세요. 검색은 사용자가 직접 실행할 때만 합니다.
- **III.E.4.h (파생 지표 금지):** 조회수·좋아요로 점수나 비율을 만들지 않습니다. YouTube가 준 순서와 숫자를
  조회 시각과 함께 그대로 보여 줍니다. 검색어 옆에는 "YouTube 데이터가 아님"을 표시합니다.
- **III.E.4.d (최대 30일 보관):** 결과는 `youtube_results` 테이블에 따로 둡니다.
  - 기본 24시간이 지나면 지웁니다(`cache_hours`, 최대 29일).
  - 그 결과로 만든 HTML 파일도 함께 지우고, DB 파일을 VACUUM합니다.
  - 이 정리는 **모든 명령이 시작될 때** 실행됩니다.
- **III.C.5 / III.F.2 (출처 표시):** YouTube 결과는 커뮤니티 글과 섞지 않습니다. 텍스트 출력에서는 블록을
  나누고, HTML은 **별도 페이지**로 만듭니다. 항목마다 "YouTube"를 표시하고, 제목을 자르지 않고, 썸네일도
  자르지 않으며, "YouTube에서 보기" 링크를 붙입니다.
  - 공식 YouTube 로고 이미지는 넣지 않았고 텍스트로 출처를 표시합니다. 이것으로 브랜드 지침을 충족하는지는
    확인하지 못했습니다.
- **댓글:** 영상 제목과 채널명을 함께 보여 줍니다. 작성자 표시 이름은 YouTube가 준 그대로 화면에만 보여
  주고(작성자 표시를 지우지 말라는 약관 조항), 저장하지 않습니다. 댓글마다 YouTube 링크를 붙입니다.
- **III.A (이용약관·개인정보 고지):** 혼자 쓰는 도구에도 적용됩니다. 그래서
  [docs/TERMS_AND_PRIVACY.md](docs/TERMS_AND_PRIVACY.md)를 두고, `youtube accept-terms` 동의 전에는
  YouTube 기능이 켜지지 않습니다. HTML 하단에는 YouTube 약관과 Google 개인정보처리방침 링크가 들어갑니다.
- **종료 시:** YouTube 기능을 그만 쓰면 `youtube purge --all`로 모두 지웁니다(API 약관 24.3).

## Bluesky 삭제 요청과 차단 (`forget`)

Bluesky 개발자 가이드라인은 삭제 요청 처리와 차단 수단을 요구합니다.

```bash
# 글 하나 삭제 (URL, at:// URI, source_post_id 모두 가능) + 다시 수집하지 않음
.venv/bin/python main.py forget --source bluesky --post https://bsky.app/profile/did:plc:.../post/... \
    --note "작성자 삭제 요청"
# 작성자(DID) 전체 삭제 + 차단
.venv/bin/python main.py forget --source bluesky --author did:plc:...
```

- 로컬 데이터만 지우며 Bluesky 원본에는 영향이 없습니다. 다른 소스도 `--post`로 같은 방식으로 지울 수 있습니다.
- `refresh --source bluesky`는 저장된 모든 글을 오래 확인 안 한 순서대로 다시 확인합니다. 다음에
  해당하는 글은 지웁니다.
  - 원본이 삭제된 글
  - 작성자가 비로그인 노출을 거부한 글
  - 성인 라벨이 붙은 글
  - 작성자가 "알고리즘 추천에서 제외"를 선언한 글
- 확인이 불확실하면 지우지 않습니다. 보관 기간은 처음 본 날부터 90일입니다(`retention_days`).
- 인용된 다른 사람의 글 본문은 저장하지 않습니다.

종료 코드는 모든 소스가 `ok`, `partial`, `skipped`이면 0, 실패나 차단이 있으면 1입니다.
로그는 콘솔과 `data/logs/collector.log`(5MB씩 회전)에 남습니다.

## 자동 실행

`deploy/run_collect.sh`가 가상환경으로 `collect`를 실행합니다. 실행이 겹치면 lock 파일 때문에
나중 실행은 바로 종료합니다.

**systemd timer (권장)**: 최근 인기글은 3시간마다, 과거 인기글은 하루 1회 수집합니다.

```bash
sudo cp deploy/humor-collector*.service deploy/humor-collector*.timer /etc/systemd/system/
sudo sed -i 's#/opt/humor-collector#'"$PWD"'#g; s#YOUR_USER#'"$USER"'#' /etc/systemd/system/humor-collector*.service
sudo systemctl daemon-reload
sudo systemctl enable --now humor-collector.timer humor-collector-archive.timer
systemctl list-timers 'humor-collector*'
journalctl -u humor-collector.service -n 50
```

**cron**: `deploy/crontab.example`의 경로를 고친 뒤 `crontab -e`에 붙여 넣으세요.

## 동작 방식

```
목록 페이지 ─▶ 행 파싱 ─▶ 필터(게시판·최소 추천) ─▶ 게시글 페이지(새 글만) ─▶ 분류 ─▶ 저장 ─▶ 재업로드 판정
     ▲                                                                                       │
 robots.txt / 호스트별 간격 / 재시도 / 챌린지 감지                    실행이 끝나면 전체 점수 재계산 ◀┘
```

```
humor-collector/
├── main.py                 CLI (collect / refresh / top / export / stats / sources / show / reclassify /
│                           trends / youtube)
├── pipeline.py             수집 흐름, 실패 격리, 차단 시 일시 정지, 과거 페이지 커서
├── settings.py             기본값과 config/settings.toml 병합
├── collectors/
│   ├── base.py             어댑터 인터페이스 (Listing, parse_listing, parse_detail, accept)
│   ├── http.py             PoliteHttpClient: robots(protego), 간격, 재시도, 타임아웃, 챌린지 감지, 문자셋
│   ├── registry.py         구현 어댑터 목록과 제외 사이트 사유
│   ├── ruliweb.py  bobaedream.py  dogdrip.py  lemmy.py  bluesky.py
│   ├── youtube.py          YouTube Data API 조회 도구 (수집기 아님, 별도 테이블, 24시간 캐시)
│   └── timeutil.py         "15 시간 전", "10/20", "2026.10.24 (토) 10:36" 같은 시각 문자열 파싱
├── models/post.py          ListingEntry / PostDetail / PostRecord / Metrics
├── storage/db.py           SQLite 스키마, upsert, 지표 이력, 소스 상태, 실행·오류 기록
├── processing/
│   ├── normalize.py        URL·제목·본문 정규화
│   ├── dedup.py            재업로드 묶음(cluster) 판정
│   ├── scoring.py          커뮤니티별 백분위로 정규화한 인기 점수
│   ├── classifier.py       ContentClassifier 인터페이스, KeywordClassifier, LLMClassifier
│   ├── trends.py           반복되는 밈·짤 문구 감지 (공백 무시 n-gram, 이전 기간 대비 급증)
│   └── report.py           trends / youtube HTML 갤러리
├── config/settings.toml  config/categories.toml
├── deploy/                 run_collect.sh, systemd service/timer, crontab 예시
├── docs/RESEARCH.md        1단계 조사 보고서
└── tests/                  pytest와 fixtures/ (실제 페이지 구조를 유지하고 내용은 익명화)
```

### 저장 데이터 (`posts` 테이블)

| 필드 | 설명 |
|---|---|
| `source`, `source_post_id`, `url` | 플랫폼, 사이트 내부 ID, 원본 링크(**항상 보존**) |
| `title`, `body` | 제목, 본문 텍스트. `""`는 수집했지만 글자가 없는 글(이미지만 있는 글), NULL은 본문을 수집하지 못한 경우(목록 전용 소스, 게시글 페이지 실패) |
| `published_at` | 작성 시각(UTC ISO-8601). 목록의 "3 시간 전"처럼 근사값이면 `published_at_approx=1`, 연도를 알 수 없으면 NULL |
| `collected_at`, `first_seen_at`, `last_seen_at` | 본문 수집 시각, 처음 본 시각, 마지막으로 본 시각 |
| `category` | 사이트의 게시판·분류명 |
| `views`, `likes`, `dislikes`, `comments_count` | 사이트가 보여 주는 값만 저장합니다. **없으면 NULL이고 0으로 채우지 않습니다** |
| `ranking`, `ranking_listing`, `rankings` | 사이트가 매긴 순위(가장 높은 순위와 그 목록 이름), 목록별 순위 |
| `media_urls` | 첨부 이미지·영상의 원본 URL(바이너리는 저장하지 않음) |
| `popularity_score` | 0~100 정규화 점수 |
| `content_category`, `content_labels`, `content_flags` | 주 분류, 해당 분류 전체, 플래그(politics/nsfw/sensitive/off_topic) |
| `cluster_id` | 재업로드 묶음 ID |
| `detail_status` | `ok`, `failed`(다음 실행에서 재시도), `list_only`, `not_needed`(API가 전부 제공) |

그 밖의 테이블:

- `metrics_history`: 숫자나 순위가 바뀔 때마다 한 줄씩 쌓입니다.
- `source_state`: 소스별 상태, 마지막 성공 시각, 차단 사유와 해제 시각, 과거 페이지 커서.
- `runs`, `fetch_errors`: 실행 기록과 실패한 URL·단계·오류.

댓글 전문, 작성자 닉네임과 ID, 이미지 바이너리는 수집하지 않습니다.

### 인기 점수 (`processing/scoring.py`)

커뮤니티마다 반응 규모가 다릅니다(보배드림 주간 베스트는 조회 10만, 루리웹 유머 베스트는 1만대).
그래서 원시 숫자를 직접 비교하지 않고 다음 순서로 계산합니다.

1. 지표(추천, 댓글, 조회)마다 **같은 소스 안에서의 백분위**를 `log1p` 값으로 구합니다.
   그 소스의 게시글이 적으면 0.5 쪽으로 수축합니다.
2. 사이트가 제공하는 지표만 가중 평균합니다(추천 0.5, 댓글 0.25, 조회 0.25).
   **없는 지표는 계산에서 빼고 남은 가중치를 다시 정규화합니다.**
3. 비추천이 공개된 사이트는 비추천 비율만큼 감점합니다.
4. 사이트가 매긴 순위는 가산점으로만 반영합니다. 순위가 낮다고 감점하지는 않습니다.
5. 같은 글이 다른 커뮤니티에서도 발견되면 커뮤니티 하나당 +5점(최대 +10점)입니다.

가중치와 계수는 `config/settings.toml`의 `[scoring]`에서 바꿉니다. 게시 직후 글은 숫자가 아직
작습니다. 목록에 머무는 동안은 실행할 때마다 갱신되고, 목록에서 내려간 뒤에는 `refresh`로
갱신합니다. 숫자는 줄어들지 않는 값으로 다룹니다. 캐시된 목록 값이 더 작게 나와도 점수가
흔들리지 않게 하기 위해서입니다.

### 중복과 재업로드 (`processing/dedup.py`)

1. **같은 플랫폼 + 같은 게시글 ID**이면 같은 행으로 보고 숫자만 갱신합니다(UNIQUE 제약).
2. **정규화 URL**이 같아도 같은 행입니다(www/m 제거, page·utm 같은 파라미터 제거).
3. **제목·본문 유사도**가 높으면 행을 합치지 않고 `cluster_id`로 묶습니다. 출처별 링크와 숫자는
   그대로 남습니다. 서로 다른 글을 합치지 않도록 기준을 보수적으로 잡았습니다.
   - 양쪽에 본문이 있으면 제목 유사도 ≥ 90이고 본문 3-gram Jaccard ≥ 0.6이어야 합니다.
   - 양쪽 본문이 150자 이상이면 본문 Jaccard ≥ 0.85만으로도 묶습니다(제목만 바꾼 재업로드).
   - 이미지 글이나 목록 전용 소스는 정규화 제목이 8자 이상이면서 완전히 같아야 합니다.
     "아줌마 특" 같은 짧은 제목은 묶지 않습니다.
   - 모든 경우 게시 시각이 30일 안이어야 합니다.

### 분류 (`processing/classifier.py`)

분류는 일상 공감, 직장·학교, 연애·인간관계, 가족, 음식·다이어트, 황당한 사건, 반전 유머,
대화형 유머, 기타 중 하나입니다.

- `KeywordClassifier`: `config/categories.toml`의 키워드를 셉니다. 제목에서 나오면 2점, 본문은
  1점입니다. "엄마:" 같은 화자 줄이 3줄 이상이면 대화형 유머로 봅니다. 정치·선정·민감 키워드와
  게시판으로 플래그도 붙입니다.
- `LLMClassifier`: `complete(prompt) -> str` 함수만 넘기면 어떤 LLM이든 연결됩니다. 응답이
  이상하면 키워드 분류기로 대체합니다. 유료 API는 기본으로 호출하지 않습니다.

## 테스트

```bash
.venv/bin/python -m pytest -q
```

- 오프라인 테스트로, 인터넷 요청이 나가면 테스트가 실패하도록 막아 두었습니다.
- `tests/fixtures/`는 2026-10-24에 실제 사이트에서 받은 페이지로 만들었습니다. 태그·클래스 구조와
  숫자는 그대로 두고, 제목·본문은 합성 텍스트로 바꿨으며, 닉네임·회원 ID·IP·댓글은 제거했습니다.
- 재시도와 타임아웃 테스트는 로컬 HTTP 서버(127.0.0.1)를 씁니다.

## 검증 기록 (2026-10-24)

환경은 클라우드 컨테이너(미국 데이터센터 IP)이고, 요청은 소스당 30건 안팎의 소규모로 했습니다.

### 자동 테스트

- `pytest` **70개 통과**. Python 3.10.20, 3.12.3, 3.13.17 모두 통과했습니다.
- 다루는 범위:
  - 사이트별 파서
  - HTTP: 503/502 재시도, 타임아웃 재시도, 연결 거부, 404는 재시도하지 않음, Retry-After 상한,
    robots 와일드카드와 Crawl-delay, 문자셋 처리
  - 수집 흐름:
    - 재실행해도 중복이 생기지 않음, 지표 갱신, 숫자가 줄지 않음
    - 게시글 1건이 실패해도 나머지는 정상 처리되고, 실패한 글은 다음 실행에서 재시도
    - 파싱 실패 격리, 목록 페이지 네트워크 오류 재시도
    - 챌린지를 받으면 해당 소스만 정지하고 다른 소스는 계속, 어댑터 예외 격리
    - 과거 페이지 커서, 중단 후 재실행, 교차 사이트 재업로드 묶음, off_topic 플래그
    - refresh, reclassify
  - CLI: export, top, stats

### 실제 수집

| 실행 | 결과 |
|---|---|
| 1차 recent (전체) | 루리웹 신규 30(게시글 페이지 30/30 성공), 개드립 30(목록 전용), Lemmy 신규 12 + 갱신 3(TopDay와 TopWeek에 동시에 있는 글). **보배드림 실패**: 응답 헤더에 charset이 없어 한글이 깨짐 → 수정 |
| 2차 recent 보배드림 | 신규 28, 갱신 2 |
| 3차 recent 재실행 | 보배드림 신규 0 / 갱신 30, 개드립 신규 5(그 사이 새로 올라온 글) / 갱신 25, 루리웹 신규 15(힛갤에 새로 선정된 글 포함) / 갱신 15 |
| archive | 보배드림 명예의 전당 1~30위(2010~2026년 글), 루리웹 과거 베스트 23건, Lemmy TopAll 10건. 커서 저장 확인 |
| Lemmy 재실행 | 신규 0 / 갱신 15 |

합계는 다음과 같습니다.

- **183건**: 루리웹 68, 보배드림 58, 개드립 35, Lemmy 22.
- 행 수 183 = 고유 (source, id) 183 = 고유 URL 183이므로 **중복 0**입니다.
- 게시글 페이지 요청 126건이 **모두 성공**했습니다.
- 2회 이상 관측해 지표 이력이 쌓인 글은 77건입니다. 예: 추천/조회/댓글이 `18/318/2 → 19/518/2`로 바뀜.
- 기록된 오류는 위의 문자셋 문제 3건뿐입니다.

실수집 중에 찾아 고친 문제는 두 가지이며, 둘 다 회귀 테스트를 추가했습니다.

1. `Content-Type`에 charset이 없는 페이지를 `requests`가 ISO-8859-1로 읽던 문제.
   이제 `<meta charset>`을 따릅니다.
2. `--limit`이 한 페이지 행 수보다 작으면 과거 페이지 커서가 넘어가지 않던 문제.
   이제 archive 모드에서는 새 글만 limit에 셉니다.

### 관찰된 한계

- **개드립**: 본문이 없습니다. 조회수도 제공되지 않아 NULL입니다.
- **Lemmy**: 커뮤니티 활동량이 적습니다. TopDay가 1~2건, TopWeek가 2~10건이었습니다.
- **보배드림 명예의 전당**: 자유게시판 글이 대부분이라 30건 중 24건이 `off_topic`입니다.
  기본 출력에서는 빠지지만 게시글 페이지 요청은 발생합니다.
- **재업로드 묶음**: 실데이터에서는 0건이었습니다. 유사 후보로 개드립 "어느 위스키의 굉장한 사은품"과
  루리웹 "어느 위스키의 대단한 사은품.jpg"(제목 유사도 83)가 있었지만, 개드립 쪽에 본문이 없어
  보수적 기준(제목 완전 일치)에 따라 묶지 않았습니다.
- **키워드 분류**: 183건 중 108건이 "기타"입니다. 제목만 있는 소스와 영어 글은 분류가 어렵습니다.
  정확도를 높이려면 LLM 분류기를 연결하세요.
- **네트워크**: 모든 결과는 데이터센터 IP 기준입니다. 운영 서버에서는 `stats`의 소스 상태와 오류를
  먼저 확인하세요.

### 추가 검증 (2026-11-01: Bluesky, 밈 트렌드, YouTube 조회)

- **테스트:** 124개 모두 통과했습니다(Python 3.10.20 / 3.12.3 / 3.13.17).
- **Bluesky 실수집:**
  - 피드 2개에서 100건을 받아 42건 저장, 3건은 두 피드 중복으로 갱신, 55건은 걸렀습니다.
  - `refresh` 재확인에서는 작성자 32명의 PDS 22곳을 조회했습니다. 그중 "알고리즘 추천 제외"를 선언한
    작성자 1명의 글 2건을 지웠습니다.
  - 같은 선언이 공개 AppView에서는 보이지 않는 것도 실측으로 확인했습니다. 그래서 PDS에서 확인합니다.
- **밈 트렌드 실데이터:** 띄어쓰기가 달라도 묶이는 것을 확인했습니다. 예: "아이폰 듀오"(2개 사이트 5건),
  "엣지러너2"/"엣지러너 2".
- **YouTube:** API 키가 없어 **실제 호출은 하지 않았습니다**. 공식 레퍼런스 형식으로 만든 합성 응답으로만
  테스트했습니다. 키를 넣은 뒤 `youtube accept-terms` → `youtube search "밈"`으로 처음 확인하세요.
- **정책 검토와 코드 리뷰:**
  - 독립 정책 검토 2건의 지적은 "정책 때문에 다르게 처리하는 점"에 반영했습니다.
  - 코드 리뷰에서 결함 22건이 보고됐고 모두 고쳤으며, 각각 회귀 테스트를 붙였습니다. 사용자 예시
    "예나 선정이 딸이에요 음료수 주르륵"이 잘리던 문제도 여기에 포함됩니다. 재현 검증을 거친 것은
    그중 12건입니다.

## 정책 준수

- robots.txt의 Disallow와 Crawl-delay를 지킵니다(protego). 루리웹의 `orderby=`/`range=` 정렬,
  개드립 Crawl-delay 10초, lemmy.world Crawl-delay 60초가 그 예입니다.
- 요청은 같은 호스트에 기본 3초 이상 간격을 둡니다. User-Agent로 봇임을 밝힙니다.
- CAPTCHA, 챌린지, 430/403 보안 페이지를 받으면 해당 소스를 12시간 멈추고 사유를 기록합니다.
  우회는 하지 않습니다.
- 보배드림 약관 제11조는 사전 승낙 없는 정보의 복제·변경·출판과 영리 이용을 금지합니다.
  다른 커뮤니티 글의 저작권도 작성자에게 있습니다. 수집 텍스트는 소재 경향을 분석하는
  참고자료로만 쓰고, 문장·이미지를 그대로 옮기거나 재게시하지 마세요. 상업적 인스타그램 운영에
  쓰려면 각 사이트의 정책과 저작권을 직접 확인하세요. 이 도구는 법률 판단을 대신하지 않습니다.

## 확장

### 커뮤니티 추가

1. `collectors/<site>.py`에서 `BaseCollector`를 상속합니다.
   - `default_listings()`: 인기 목록 URL과 종류(`recent`/`archive`), 순위 여부를 정의합니다.
   - `parse_listing()`: 목록 HTML/JSON을 `ListingEntry`로 바꿉니다. 행 단위 오류는 `parse_rows`가 격리합니다.
   - 목록에 본문이 없으면 `parse_detail()`도 구현합니다. API처럼 목록에 본문이 다 있으면
     `needs_detail = False`, `list_has_body = True`로 둡니다.
   - 필요하면 `accept()`로 게시판이나 최소 추천 수를 거릅니다.
2. `collectors/registry.py`의 `COLLECTORS`에 등록하고, `config/settings.toml`에 `[sources.<site>]`를
   추가합니다.
3. 실제 페이지를 한 번 받아 익명화한 fixture를 `tests/fixtures/`에 넣고 파서 테스트를 작성합니다.
4. 먼저 robots.txt와 약관을 확인합니다. 챌린지가 나오면 `UNSUPPORTED`에 사유를 기록합니다.

### AI 소재 분석 연결

- `main.py export --format jsonl --require-body`의 출력이 분석 입력입니다. 한 줄이 한 게시글이며
  `title`, `body`, `url`, 지표, `popularity_score`, `content_category`, `reuploads`를 담습니다.
- 분류를 LLM으로 바꾸려면 `pipeline.CollectionPipeline`에 `LLMClassifier(complete, fallback=KeywordClassifier())`를
  넘깁니다(`main.build_pipeline`의 한 줄).
- 소재 요약·구조 분석(설정→전개→반전, 등장인물 관계, 음식 캐릭터로 바꿀 수 있는지)은 별도
  단계로 두세요. `posts.id`를 키로 결과 테이블을 만들면 수집기를 고치지 않아도 됩니다.

### 4컷 만화 파이프라인 연결 지점

```
[이 프로젝트] collect → top/export(JSONL)
      ↓ posts.id, url(출처), popularity_score, content_category
[다음 단계] 소재 분석(LLM): 웃음 포인트 추출, 4박자 구조화, 원문과 다른 독립 설정으로 재창작
      ↓
[다음 단계] 캐릭터 대본 → 이미지 생성 → 검수(원문 표현과 유사하지 않은지) → 업로드
```

- 연결 키는 `posts.id`(내부)와 `url`(출처)입니다. 재창작물에는 출처 추적용으로 `url`을 남기세요.
- 음식 캐릭터 기획에는 `top --category 음식·다이어트`, `--days 7` 같은 필터가 바로 쓸 수 있습니다.
- 규모가 커지면 다음을 고려하세요.
  - 재업로드 판정: `datasketch` MinHash-LSH로 후보 검색을 바꿉니다.
  - 저장소: SQLite에서 PostgreSQL로 옮깁니다(`storage/db.py`만 교체).
