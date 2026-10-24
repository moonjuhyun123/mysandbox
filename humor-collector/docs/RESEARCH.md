# 1단계 조사 결과: 기존 수집기 · API · 접근 정책 (2026-10-24 확인)

## 조사 방법과 한계

- **접근 확인**: 각 사이트의 `robots.txt`와 인기글 목록·게시글 페이지를 정직한 User-Agent
  (`humor-collector/0.1 ...`)로 사이트당 몇 번씩만 직접 요청했습니다(요청 사이 간격 3~11초).
  요청은 **미국 데이터센터 IP의 클라우드 컨테이너**에서 나갔습니다. 국내 가정용 IP나 국내
  VPS에서는 결과가 다를 수 있습니다. 특히 Cloudflare 챌린지는 IP 평판에 따라 달라집니다.
- **이용약관**: 루리웹은 이용약관 개정 안내문, 보배드림은 약관 전문을 직접 읽었습니다.
  다른 사이트의 약관 전문은 확인하지 못했습니다(미검증).
- **GitHub/PyPI**:
  - PyPI 메타데이터는 `pypi.org/pypi/<pkg>/json`으로 직접 확인했습니다.
  - GitHub 저장소 정보(라이선스, 마지막 push, 보관 여부, 스타 수)는 GitHub 검색 결과와 raw
    README·소스 파일로 확인했습니다.
  - 이 실행 환경은 GitHub API 접근이 이 저장소로 제한되어 있어, 저장소별 API(`repos/...`)
    직접 조회는 하지 못했습니다. 스타 수 일부는 미검증입니다.
  - 루리웹·보배드림 전용 수집기는 일반 웹 검색으로만 찾았고 발견하지 못했습니다.
- 확인하지 못한 내용은 표에 **미검증**으로 표시했습니다.

## 사이트별 결과 요약

| 사이트 | 기존 도구 | 수집 방법 | 인기 지표 | 제약 | 권장 방식 |
|---|---|---|---|---|---|
| **개드립** dogdrip.net | 공식 API 없음. Rhymix RSS(`/dogdrip/rss`, `?act=rss`)는 403(비활성). GitHub: chitacan/dogdrip-feed(무라이선스, 2024-06 이후 정체), k-community-trackagent(MIT, 알파) | 목록 HTML `/dogdrip?page=N`은 **200**. 게시글 페이지, `?sort_index=popular`, RSS는 **Cloudflare "Just a moment" 403** | 목록: 추천수, 댓글수, 상대시간("15 시간 전"). 조회수 없음 | robots: `Allow: /`, **Crawl-delay 10**, Content-Signal `ai-train=no, use=reference`. 본문 접근 불가 | **목록 전용 어댑터**(본문 NULL). 인기 신호와 교차 업로드 탐지에 활용. 챌린지는 우회하지 않음 |
| **에펨코리아** fmkorea.com | 공식 API/RSS 없음(`?act=rss` → 430, `/humor/rss` → 404). GitHub: jwm21542/FMKoreaCrawler(무라이선스, Selenium), savior714/fmkorea(무라이선스, playwright-stealth 우회형), elky84/web-crawler(GPL-3.0, C#) | `/best` → **HTTP 430 "에펨코리아 보안 시스템" + Cloudflare Turnstile(CAPTCHA)** | 확인 불가 | robots: 일반 봇은 `Disallow: /`, `/`·`/best`·`/best2`·`/humor`만 허용. CAPTCHA | **제외**(비활성, 사유 기록) |
| **더쿠** theqoo.net | 공식 API 없음. GitHub: Esantomi/theqoo-scraper(무라이선스, 노트북, 2023), birdmoon/theqoo-rss(무라이선스) | `/hot` → **403 "더쿠 - 보안 검사중"(CAPTCHA)** | 확인 불가 | robots: `Allow: /`, Content-Signal `ai-train=no` | **제외** |
| **디시인사이드** dcinside.com | PyPI `dc-api` 0.8.0(MIT, **2021-08-21 이후 릴리스 없음**, 미해결 파싱 버그 보고), gurumnyang/dcinside.js(MIT, Node, 유지 중), RSSHub `/dcinside/board/:id`(AGPL, 제목·시각만) | gall.dcinside.com 목록 → **200이지만 본문이 비어 있음**(Content-Length 0, 비브라우저 UA) | 원래 페이지에는 조회수·추천수·댓글수가 있음(직접 확인 못함) | robots: `Allow: /`, 일부 갤러리만 차단(dcbest·hit은 차단 목록에 없음) | **제외**: 브라우저로 위장해야 하므로 "차단 회피"로 판단. 운영자가 식별된 봇을 허용하면 재검토 |
| **네이트판** pann.nate.com | GitHub: mioham85-cpu/nate-pann-collector(무라이선스), Kottakji/nate_scraper(무라이선스, 2019) | 요청하지 않음 | – | robots: **`User-agent: * Disallow: /`**(검색엔진만 허용) | **제외**(정책상 불가) |
| **웃긴대학** humoruniv.com | GitHub: Memilmook/Crawler-HumorUniv(무라이선스, 2020), elky84/web-crawler(GPL-3.0, C#) | `list.html?table=pds` → JS로 `msg.html?mode=1904`("브라우저 쿠키 발행이 필요합니다")로 이동 | 확인 불가 | robots: `/board/best/`, `/*.php` 차단 | **제외** |
| **인스티즈** instiz.net | GitHub: 2hnK/instiz-crawler(무라이선스, 로그인 쿠키 필요) | robots.txt조차 **Cloudflare 챌린지 403** | 확인 불가 | – | **제외** |
| **루리웹** ruliweb.com *(대체 1)* | 공식 API 없음. 게시판 RSS(`/community/board/300143/rss`)는 **최신 글만, 인기 지표 없음**. 전용 수집기는 웹 검색으로 찾지 못함 | 목록 `/best/humor_only/now?page=N`, `/best/selection`(힛갤)과 게시글 페이지가 **모두 200** | 목록: **추천수·조회수·댓글수**, 상단 순위 1~4. 게시글: 추천·**비추천**·조회·댓글·초 단위 작성시각 | robots: `/best`와 게시글은 허용. `orderby=`, `range=`, `view=`, `cate=` 등은 **차단**되어 1D/7D 추천순 정렬은 쓰지 않음. 약관 개정 안내문에서 자동수집 조항은 확인되지 않음(약관 전문은 미검증) | **구현**(HTML 어댑터, 기본 3초 간격) |
| **보배드림** bobaedream.co.kr *(대체 2)* | 공식 API 없음. 전용 공개 수집기를 찾지 못함(유료 상용 스크래퍼만 있음) | 베스트글 실시간·주간·월간·**명예의 전당**(page 50 = 1471~1500위까지 확인) 목록과 게시글 페이지가 **모두 200** | 목록: **추천수·조회수·댓글수**, 주간/월간/명예의 전당 **순위**. 게시글: 원 게시판, 정확한 작성시각, 본문, 이미지 | robots: `Allow: /`(Amazonbot만 차단). **약관 제11조**: 회원은 회사 사전 승낙 없이 얻은 정보를 복제·변경·출판 등에 사용하거나 타인에게 제공할 수 없고 영리행위를 할 수 없음. 베스트에 차·정치 글이 섞임 | **구현**(유머 게시판만 허용 목록으로 필터) |
| **Reddit** | PRAW 8.0.3(2026-08-12, BSD-2, 유지 중) | `.json` → 403, RSS → 200(점수·댓글수 없음, 연속 요청 시 429) | API에는 있으나 인증이 필요 | robots: **`Disallow: /`**. Data API는 승인된 OAuth 앱과 계정이 필요(Responsible Builder Policy). 2차 출처에 따르면 RSS는 2026-11-13 종료, 공개 Data API는 2027-03 종료 예정(Reddit 원문은 미검증) | **제외**: 계정 연동이 필요해 사용자 동의 없이는 진행하지 않음 |
| **9GAG** | 공식 API 없음. tuvimen/9gag-scraper(GPL-3.0, curl_cffi로 TLS 위장, 우회형), gurza/9gag.com-scraper(MIT, 2020 정체) | 페이지, `/v1/...` JSON, `/rss` 모두 **Cloudflare 챌린지 403** | 확인 불가 | – | **제외** |
| **Lemmy** *(해외 대체)* | **공식 HTTP API v3**(인증 불필요). pythorhead 클라이언트(AGPL)는 쓰지 않고 HTTP로 직접 호출 | `/api/v3/post/list?community_name=…&sort=TopDay` → **200 JSON** | upvotes·downvotes·댓글수·작성시각·원문 링크(ap_id). 조회수 없음 | lemmy.world robots: **Crawl-delay 60**, `/api` 차단 없음 | **구현**(공식 API, 분당 1회) |
| 기타 | 클리앙: robots `Disallow: /*?*`라 페이지 이동이 불가하고 유머 비중이 낮음. 뽐뿌: 유머게시판 403. Mastodon/Bluesky: 공개 API가 있으나 유머 비중이 낮음. Imgur/Tumblr: API 키 필요(401) | | | | 보류 |

## 발견한 GitHub 저장소 정리

"사용 가능" 판단 기준은 다음 네 가지입니다: 라이선스가 있는가, 최근까지 유지되는가, Python인가,
접근 제한을 우회하지 않는가.

| 저장소 / 패키지 | 라이선스 | 마지막 활동 | 내용 | 판단 |
|---|---|---|---|---|
| [0912kth-ops/k-community-trackagent](https://github.com/0912kth-ops/k-community-trackagent) | MIT | push 2026-09-21 | 7개 국내 사이트 어댑터(httpx+BS4). README상 실측은 일부 사이트 1회뿐 | **참고만**: 알파 단계이고 우리가 쓰는 루리웹·보배드림은 포함하지 않음 |
| [song9446/dcinside-python3-api](https://github.com/song9446/dcinside-python3-api) (PyPI `dc-api`의 홈페이지 URL. 저장소 소유자가 바뀐 것으로 보임) | MIT | PyPI 0.8.0 2021-08-21 | m.dcinside 목록·본문·댓글 | **사용 안 함**: 정체되었고 디시 자체도 제외 |
| [gurumnyang/dcinside.js](https://github.com/gurumnyang/dcinside.js) | MIT | 2026-07 | Node 라이브러리 | 사용 안 함(Node) |
| [DIYgod/RSSHub](https://github.com/DIYgod/RSSHub) | AGPL-3.0 | 활발 | 국내 사이트 라우트는 dcinside·etoland뿐, 인기 지표 없음 | 사용 안 함: Node 서버가 필요하고 대상 라우트가 없음 |
| [tuvimen/9gag-scraper](https://github.com/tuvimen/9gag-scraper) | GPL-3.0 | 2025-09 | TLS 위장으로 Cloudflare 통과 | **사용 불가**(우회형) |
| [savior714/fmkorea](https://github.com/savior714/fmkorea), [jwm21542/FMKoreaCrawler](https://github.com/jwm21542/FMKoreaCrawler) | 없음 | 2026-03 / 2024-04 | stealth 브라우저 / Selenium | 사용 불가(무라이선스, 우회형) |
| [Esantomi/theqoo-scraper](https://github.com/Esantomi/theqoo-scraper), [birdmoon/theqoo-rss](https://github.com/birdmoon/theqoo-rss) | 없음 | 2023 / 2026-04 | 노트북 / 단일 카테고리 | 사용 불가(무라이선스) |
| [chitacan/dogdrip-feed](https://github.com/chitacan/dogdrip-feed) | 없음 | 2024-06 | JSON Feed 생성 스크립트 | 사용 불가(무라이선스, 정체) |
| [Memilmook/Crawler-HumorUniv](https://github.com/Memilmook/Crawler-HumorUniv), [elky84/web-crawler](https://github.com/elky84/web-crawler) | 없음 / GPL-3.0 | 2020 / 2026-10 | 웃대 수집 / C# 다중 크롤러 | 사용 안 함 |
| [mioham85-cpu/nate-pann-collector](https://github.com/mioham85-cpu/nate-pann-collector) | 없음 | 2026-03 | Streamlit | 사용 불가(무라이선스, robots 위반 대상) |

## 재사용한 것과 직접 만든 것

**재사용(검증된 범용 라이브러리)**

| 용도 | 라이브러리 | 이유 |
|---|---|---|
| HTTP | `requests` 2.34 (Apache-2.0) | 표준적이고 세션과 타임아웃을 지원 |
| HTML 파싱 | `beautifulsoup4` 4.15 + `lxml` 6.1 (MIT/BSD) | 지저분한 게시판 HTML도 견고하게 파싱 |
| robots.txt | `protego` 0.7.0 (BSD-3, Scrapy 프로젝트) | `*`/`$` 와일드카드와 Crawl-delay를 지원. Python 3.12 이하의 표준 `urllib.robotparser`는 와일드카드를 처리하지 못함 |
| 제목 유사도 | `rapidfuzz` 3.14 (MIT) | 빠른 C 구현 |
| 저장, 설정 | `sqlite3`, `tomllib`(표준 라이브러리) | 단일 서버에서는 추가 서버가 필요 없음 |

**직접 구현한 것과 이유**

- **사이트 어댑터**(루리웹, 보배드림, 개드립 목록, Lemmy API): 유지되면서 라이선스가 있는
  공개 수집기가 없습니다. 각 어댑터는 120~170줄 정도의 순수 파싱 함수이고, 실제 페이지에서
  만든 fixture로 테스트합니다.
- **재시도 루프**: urllib3 `Retry`를 쓰지 않았습니다. 시도마다 호스트별 간격 제한을 적용해야
  하고, 챌린지 페이지를 받으면 재시도 없이 즉시 멈춰야 하기 때문입니다(약 30줄).
- **파이프라인, 저장, 점수, 중복 판정**: 이 프로젝트에 맞춘 연결 코드입니다.
- **사용하지 않은 것**:
  - Scrapy: 단일 서버의 소규모 수집에는 너무 무겁습니다.
  - Playwright 같은 브라우저 자동화: 필요한 사이트가 모두 챌린지 사이트라 우회가 됩니다.
  - datasketch MinHash: 현재 규모에서는 불필요하며, 확장 방안으로 남겨 두었습니다.
  - trafilatura: 게시판별 셀렉터가 더 정확합니다.
  - feedparser: 인기 지표가 있는 RSS가 없습니다.

## 2단계: 구현 대상 선정

우선 검토 대상이던 개드립과 에펨코리아는 다음 이유로 그대로 쓸 수 없었습니다.

- **에펨코리아**: CAPTCHA(Turnstile)가 걸려 있어 제외했습니다.
- **개드립**: 게시글 본문 페이지가 Cloudflare 챌린지에 막혀 있습니다. 목록은 접근할 수 있어
  **목록 전용 보조 소스**로만 구현했습니다.

대체 소스는 선정 기준(유머 양, 인기글 선별, 본문 확보, 안정성, 유지보수, 정책)으로 비교했습니다.

| 기준 | 루리웹 유머 BEST/힛갤 | 보배드림 베스트 | (참고) 개드립 | (해외) Lemmy |
|---|---|---|---|---|
| 유머 소재 양 | 유머게시판 베스트가 하루 수백 건 | 유머·신유머 게시판(베스트에 차·정치가 섞여 필터링) | 많음 | c/funny, c/jokes |
| 인기글 선별 | 베스트 진입 + 추천/조회/댓글 | 실시간/주간/월간 순위 + 명예의 전당 | 인기 배지 + 추천 | TopDay/Week/All |
| 본문 | ○ | ○ | ✕(챌린지) | ○(API) |
| 과거 인기글 | 베스트 목록을 페이지 단위로(수일~) | 명예의 전당 1500위 이상 | 목록 페이지 | TopAll |
| 정책 | robots 허용 경로만 사용 | robots 전체 허용, 약관상 재이용 제한 | robots 허용, Crawl-delay 10 | 공식 API, Crawl-delay 60 |

**결정**: 1차 구현 대상은 **루리웹**과 **보배드림**입니다. 추가로 **개드립(목록 전용)**과
**Lemmy(공식 API, 해외)**를 같은 어댑터 구조로 붙였습니다.
