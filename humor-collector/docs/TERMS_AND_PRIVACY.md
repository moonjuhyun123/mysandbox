# humor-collector 이용약관 및 개인정보 고지 (YouTube 기능)

이 문서는 humor-collector의 YouTube 검색 기능(`python main.py youtube ...`,
`python main.py trends --youtube N`)에 적용됩니다. YouTube API Services 개발자 정책 III.A가 모든 API
클라이언트에 요구하는 내용을 담고 있습니다. 혼자 쓰는 도구라도 예외가 없습니다.

## 이용약관

1. 이 도구는 YouTube API Services를 사용합니다. YouTube 기능을 사용하면
   **[YouTube 서비스 약관](https://www.youtube.com/t/terms)에 동의하고 그 약관을 따르는 것**으로 봅니다.
2. YouTube 기능은 사용자가 직접 실행한 검색 결과를 화면과 로컬 HTML 페이지에 보여 주는 용도로만 씁니다.
   정기 자동 수집, YouTube 데이터의 집계·분석·점수화, 다른 출처 결과와 섞기는 하지 않습니다.
3. 영상, 오디오, 영상 프레임은 내려받거나 저장하지 않습니다. 썸네일은 YouTube 서버에서 직접 불러옵니다.
4. API 키는 본인의 Google Cloud 프로젝트 하나에서 발급한 것만, 환경변수로만 사용합니다. 다른 사람과
   공유하거나 코드 저장소에 넣지 않습니다. 할당량을 늘리려고 키나 프로젝트를 여러 개 쓰지 않습니다.
5. YouTube 기능을 그만 쓰면 `python main.py youtube purge --all`로 저장된 YouTube 데이터를 모두 지웁니다.

## 개인정보 고지

- **사용하는 서비스**: 이 도구는 YouTube API Services를 사용합니다.
  [Google 개인정보처리방침](http://www.google.com/policies/privacy)이 적용됩니다.
- **가져오는 정보**: 검색어로 찾은 공개 영상의 정보입니다. ID, 제목, 채널명, 게시일, 길이, 썸네일 주소,
  조회수·좋아요·댓글 수가 해당합니다. `comments` 명령을 쓰면 공개 댓글(작성자 표시 이름, 내용, 좋아요 수)도 가져옵니다.
- **저장**:
  - 영상 정보는 이 컴퓨터의 SQLite 파일(`data/humor.db`, `youtube_results` 테이블)에 저장합니다.
    보관 기한은 기본 24시간이고, 설정으로 늘려도 최대 29일입니다. 기한이 지나면 자동으로 지웁니다.
  - 이 도구가 만든 YouTube 결과 HTML 파일도 같은 기한에 지웁니다.
  - 댓글은 화면에만 보여 주고 저장하지 않습니다.
  - 사용자 계정 정보는 다루지 않습니다. OAuth 로그인을 쓰지 않고, 공개 데이터만 API 키로 조회합니다.
- **공유**: 수집한 데이터는 누구에게도 전송하거나 공유하지 않습니다.
- **제3자 접속**: HTML 페이지를 열면 브라우저가 썸네일을 YouTube/Google 서버(i.ytimg.com)에서 직접 불러옵니다.
  이때 Google이 브라우저 정보를 받을 수 있습니다.
- **삭제**: 언제든 `python main.py youtube purge --all`로 모든 YouTube 데이터와 페이지를 지울 수 있습니다.
  로컬 데이터를 지워도 YouTube에 있는 원본에는 영향이 없습니다.
- **문의**: 이 도구의 운영자는 설치한 사람 본인입니다. 다른 사람에게 제공한다면 연락처와 공개 게시된
  개인정보처리방침을 따로 마련해야 합니다.

`python main.py youtube accept-terms`를 실행해 위 내용에 동의해야 YouTube 기능이 켜집니다.
