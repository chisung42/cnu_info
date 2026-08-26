# CNU Info iOS 앱

새로 크롤링된 공지 이미지를 사진 앱에 저장하고, 업로드용 본문을 클립보드에 복사한 뒤 인스타그램을 여는 SwiftUI 앱.

## 구조

- 서버 API: `web_dashboard.py`의 `/api/*` (X-API-Key 헤더 필요, nginx에서 Basic Auth 없이 통과)
  - `GET /api/notices` — 공지 목록 (`?unposted=1`, `?since=` 지원)
  - `GET /api/notices/<key>` — 상세 (이미지 URL, 복사용 본문)
  - `GET /api/media/<path>` — 이미지 파일
  - `POST /api/notices/<key>/done` — 인스타 업로드 완료 표시 (`{"posted": true|false}`)
- 접근 경로: Tailscale — `https://moon-p151emx.tail70d104.ts.net` (iPhone에 Tailscale 연결 필요)
- API 키: 서버 `.env`의 `APP_API_KEY` (로컬 사본: 저장소 루트 `.app_api_key`, git 제외)

## 빌드

```bash
brew install xcodegen   # 최초 1회
cd ios
xcodegen generate
open CnuInfo.xcodeproj
```

Xcode에서:
1. CnuInfo 타깃 > Signing & Capabilities에서 Team 선택 (개인 Apple ID 가능)
2. iPhone 연결 후 실행
3. 앱 설정(톱니바퀴)에서 API 키 입력 — `cat .app_api_key`

## 테스트

```bash
xcodebuild -project CnuInfo.xcodeproj -scheme CnuInfo \
  -destination 'platform=iOS Simulator,name=iPhone 17 Pro Max' test
```

UI 테스트가 실서버를 호출하므로 시뮬레이터에 API 키가 UserDefaults로 설정돼 있어야 한다:

```bash
xcrun simctl spawn booted defaults write kr.moonhome.cnuinfo apiKey "$(cat ../.app_api_key)"
```

## 사용 흐름

텔레그램 새 공지 알림 → 앱 열기 → 공지 탭 → "저장하고 인스타그램 열기" →
(이미지 전체 사진 앱 저장 + 본문 클립보드 복사 + 인스타 실행) → 인스타에서 사진 선택, 캡션 붙여넣기 → 게시 → 앱에서 "완료로 표시"
