# CNU Info 현재 상태

기준일: 2026-08-27 (KST)  
기준 커밋: `6ce4365` (`fix: match Telegram body with dashboard copy text`)

## 한눈에 보기

CNU Info는 충남대학교 공지를 30분마다 확인해 새 글만 수집하고, 첨부파일과 본문 이미지를 정리해 웹 대시보드와 텔레그램으로 전달하는 서버형 도구다.

- 서버 경로: `/srv/cnuinfo`
- Git 브랜치: `main`
- 웹 서비스: `cnu-info-web` — 정상 실행 중 (`active`)
- 수집 서비스: `cnu-info-monitor` — 정상 실행 중 (`active`)
- 최신 확인: 2026-08-27 00:37 KST 수집 주기에서 6개 게시판 모두 신규 링크 없음

## 수집 대상 게시판

| ID | 게시판 |
| --- | --- |
| `general` | 일반소식 |
| `academics` | 학사정보 |
| `education` | 교육정보 |
| `startup` | 사업단 창업ㆍ교육 |
| `recruitment` | 채용초빙 |
| `scholarship` | 장학정보 |

게시판 목록과 URL은 `scripts/crawl_notices.py`의 `DEFAULT_BOARDS`에서 관리한다.

## 처리 흐름

```text
충남대학교 게시판 확인 (30분 주기 또는 /r)
  → 새 링크만 식별
  → 본문·첨부파일 수집
  → PDF/HWP/HWPX 등 문서 변환 및 이미지 생성
  → data/에 공지 메타데이터 저장, attachments/에 파일 저장
  → 웹 대시보드 갱신
  → 텔레그램 알림·이미지·업로드용 본문 전송
```

런타임 데이터는 Git에 포함하지 않는다.

- `data/`: `notice_links.json`, `notices_db.json`, 텔레그램 업데이트 위치 등
- `attachments/`: 원본 첨부파일, 본문 이미지, 생성 이미지
- `.env`: API 키·텔레그램 토큰·대화방 ID 등 민감 설정

## 웹 대시보드

주요 구현 파일은 `scripts/web_dashboard.py`다. 서버에서는 Gunicorn이 `127.0.0.1:8003`에서 실행하며, 외부 노출과 인증은 서버 앞단 구성에서 처리한다.

공지 카드에서 할 수 있는 일:

- 제목 또는 본문 복사
- 생성 이미지 미리보기 및 ZIP 다운로드
- 이미지 업로드, 순서 변경, 개별 삭제
- 썸네일 제목·헤더 수정
- 공지 재크롤링
- 공지 삭제
- `✨ AI 본문 정리`를 눌러 필요할 때만 AI로 본문 정리

삭제된 공지는 데이터베이스에서 제거하고 링크에 `hidden: true`를 기록한다. 모니터도 `hidden` 상태를 다시 확인하므로 삭제한 글이 다음 수집에서 되살아나지 않게 되어 있다.

## 본문 텍스트와 AI 정리

### 기본 원칙

모든 글에 AI를 자동 호출하지 않는다. 표·복잡한 레이아웃처럼 원문을 읽기 어렵다고 판단한 경우, 운영자가 대시보드의 `✨ AI 본문 정리` 버튼을 눌러 수동으로 실행한다.

- 원문은 `raw_content`에 보존한다.
- AI 정리 결과는 `content`에 저장한다.
- AI 호출에는 `.env`의 FactChat 관련 설정이 필요하다.
- API 키나 토큰은 Git·문서·로그에 기록하지 않는다.

### 복사용 본문 형식

`scripts/notice_display.py`가 대시보드와 텔레그램이 공통으로 쓰는 복사용 텍스트를 만든다.

```text
[충남대학교 학사정보]
본문 전체

#충남대학교 #충남대 #충대 #cnu
```

즉, ‘본문 복사’와 텔레그램의 별도 본문 메시지는 게시판 헤더·본문·해시태그까지 동일하다. 제목은 이 메시지에 넣지 않는다.

## 이미지·글꼴

- 공지 이미지는 `scripts/generate_instagram_images.py`에서 생성한다.
- 현재 공통 글꼴은 프로젝트에 포함된 `assets/fonts/Pretendard-Regular.otf`다.
- 해당 글꼴의 라이선스는 `assets/fonts/LICENSE-Pretendard.txt`에 있다.
- 서버와 Mac에서 같은 글꼴을 사용해 한글 깨짐과 결과 차이를 줄였다.

## 텔레그램 알림

수집이 완료된 새 공지는 다음 순서로 전송한다.

1. 제목·게시판·날짜·링크 알림
2. 생성 이미지 — 2~10장씩 앨범으로 보내며, 10장을 넘으면 나누어 전송
3. 대시보드 ‘본문 복사’와 같은 업로드용 본문 메시지

봇이 허용된 대화방에서 `/r` 또는 `/refresh`를 받으면 30분 대기 중에도 즉시 수집을 시작한다. 이 명령은 `.env`에 설정된 숫자형 `TELEGRAM_CHAT_ID`와 일치하는 대화방만 처리한다.

## 서버 서비스 설정

### `cnu-info-web`

```text
WorkingDirectory=/srv/cnuinfo/scripts
ExecStart=/srv/cnuinfo/.venv/bin/gunicorn ... --bind 127.0.0.1:8003 ... web_dashboard:app
Restart=always
```

### `cnu-info-monitor`

```text
WorkingDirectory=/srv/cnuinfo
ExecStart=/srv/cnuinfo/.venv/bin/python scripts/monitor_new_notices.py \
  --interval 30 --attachments-dir /srv/cnuinfo/attachments \
  --data-dir /srv/cnuinfo/data --max-images 20 --workers 4
Restart=always
```

서비스 상태와 로그 확인 예시:

```bash
sudo systemctl status cnu-info-web cnu-info-monitor
journalctl -u cnu-info-monitor -n 100 --no-pager
journalctl -u cnu-info-web -n 100 --no-pager
```

## 배포 절차

코드 변경 뒤에는 아래 순서로 반영한다. `data/`, `attachments/`, `.env`는 서버 런타임 데이터이므로 Git으로 덮어쓰지 않는다.

```bash
# Mac 로컬 저장소
git add <변경 파일>
git commit -m "<설명>"
git push origin main

# 서버 (/srv/cnuinfo)
git fetch origin
git merge --ff-only origin/main
sudo systemctl restart cnu-info-web cnu-info-monitor
sudo systemctl is-active cnu-info-web cnu-info-monitor
```

## 검증 체크리스트

- Python 문법 검사: `.venv/bin/python -m py_compile scripts/*.py`
- 대시보드 변경: 브라우저에서 실제 버튼과 화면을 확인
- 텔레그램 변경: 테스트 공지를 준비한 뒤 봇에 `/r`을 보내 메시지·앨범·본문 순서를 확인
- 첨부파일 변경: HTTP 성공 여부만 보지 말고 파일 시그니처, ZIP 구조, 실제 열림 여부를 확인
- 재크롤링 변경: 기존 생성 이미지와 첨부파일이 중복·유실되지 않는지 확인

## 모바일 앱 (2026-08-27 추가)

- `web_dashboard.py`에 앱용 JSON API 추가: `/api/notices`, `/api/notices/<key>`, `/api/media/<path>`, `/api/notices/<key>/done`
  - 모두 `X-API-Key` 헤더 필요 (서버 `.env`의 `APP_API_KEY`, 로컬 사본 `.app_api_key`)
  - nginx `/api/`는 Basic Auth 없이 통과하도록 `sites-available/cnu-info` 수정 (백업: `~/cnu-info.nginx.bak`)
- 접근은 Tailscale HTTPS: `https://moon-p151emx.tail70d104.ts.net` (공유기가 80/443을 안 열어둠, SSH 2222만 개방)
- iOS 앱: `ios/` (SwiftUI, xcodegen). 목록 → 상세 → 이미지 사진 앱 저장 + 본문 클립보드 복사 + 인스타 열기 + 완료 표시
- 게시판 필터 칩, 갤러리형 사진 그리드 + 전체화면 뷰어, 서버 콘솔(status/logs), 크롤러 새로고침·개별 재크롤링·썸네일 제목 수정
- 인스타그램 대조: `scripts/instagram_sync.py`가 @cnu_info의 실제 게시물 캡션을 공지 본문과 맞춰 업로드 여부를 판별한다.
  API는 `/api/instagram/status`, `/api/instagram/sync`. **액세스 토큰 등록 전까지 비활성** — 설정 방법은 `INSTAGRAM_SETUP.md`
- 앱은 서버 주소를 짧은 타임아웃으로 먼저 찔러 보고 캐시한다(`EndpointResolver`). 집 와이파이에서는 헤어핀 NAT로
  공개 주소가 막히므로 이 해석 없이는 장시간 작업이 타임아웃까지 멈춘다
- 시뮬레이터 UI 테스트로 실서버 연동 검증 완료. 실기기 설치(서명)만 남음 — `ios/README.md` 참고

## 다음에 유의할 점

- iOS PWA는 사진 앱에 여러 장을 자동 저장할 수 없다. 한 번에 저장하는 네이티브 경험이 필요하면 SwiftUI + PhotoKit 앱이 적합하다.
- 앱은 서버의 대시보드/API를 사용하고, 크롤링·문서 변환·AI·이미지 생성은 서버에서 계속 수행하는 구조가 안전하다.
- Telegram 봇 토큰, FactChat API 키, 서버 비밀번호는 교체가 필요할 수 있는 민감 정보다. 코드·Git·문서에 넣지 말고 서버 `.env` 등 비밀 저장소에서만 관리한다.
