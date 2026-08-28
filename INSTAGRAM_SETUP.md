# 인스타그램 대조 기능 설정

앱의 `⋯ > 인스타그램 대조`는 @cnu_info 계정의 실제 게시물을 읽어와, 어떤 공지가 올라갔고
어떤 공지가 아직 안 올라갔는지 판별한다. 캡션이 대시보드의 '본문 복사' 텍스트와 같으므로
캡션을 정규화해 공지 본문과 맞춰 보는 방식이다.

이 기능은 **액세스 토큰을 등록하기 전까지 동작하지 않는다.** 토큰이 없으면 앱이
"인스타그램 연동이 설정되지 않았습니다"라고 안내하고, 나머지 기능은 그대로 쓸 수 있다.

## 왜 토큰이 필요한가

인스타그램은 로그인 없이 게시물 목록을 읽을 수 없다(프로필 페이지를 긁는 방식은 막혀 있고
약관 위반이기도 하다). 공식 경로는 **Instagram API with Instagram Login**이며,
본인 계정만 읽는 경우에는 **앱 심사(App Review)가 필요 없다.**

## 준비 (한 번만, 약 15분)

### 1. 인스타그램 계정을 프로페셔널로 전환

@cnu_info 앱에서 `설정 > 계정 유형 및 도구 > 프로페셔널 계정으로 전환` →
**비즈니스** 또는 **크리에이터** 중 아무거나. 페이스북 페이지 연결은 필요 없다.

> 개인(Personal) 계정은 API로 읽을 수 없다. 이 전환이 유일한 필수 조건이다.

### 2. Meta 앱 만들기

1. https://developers.facebook.com/apps → `앱 만들기`
2. 사용 사례에서 **Instagram** 선택 (이름은 아무거나, 예: `cnu-info-sync`)
3. 좌측 `Instagram > API 설정` 으로 이동
4. **"Instagram 계정으로 로그인하는 Instagram API"** 섹션을 사용한다
5. `2단계: Instagram 비즈니스 로그인 설정`에서 **Instagram 테스터**로 @cnu_info를 추가하고,
   인스타그램 앱의 `설정 > 앱 및 웹사이트 > 테스터 초대`에서 수락한다

앱은 **개발 모드**로 두면 된다. 본인 계정만 읽으므로 심사가 필요 없다.

### 3. 액세스 토큰 발급

`Instagram > API 설정` 화면의 **"액세스 토큰 생성"** 버튼을 누르면
`instagram_business_basic` 권한이 포함된 장기 토큰이 나온다. 이 값을 복사한다.

토큰은 **60일** 유효하고, 서버가 **30일마다 자동으로 갱신**한다.

### 4. 서버에 토큰 등록

```bash
ssh moon@moon-p151emx.tail70d104.ts.net   # 또는 ssh moon@moonhome.kro.kr -p 2222

# .env에 한 줄 추가 (첫 실행 때 data/instagram_token.json으로 옮겨진다)
echo 'INSTAGRAM_ACCESS_TOKEN=<복사한_토큰>' >> /srv/cnuinfo/.env
sudo systemctl restart cnu-info-web
```

등록 확인:

```bash
curl -s -H "X-API-Key: $(cat .app_api_key)" \
  https://moon-p151emx.tail70d104.ts.net/api/instagram/status | python3 -m json.tool
# → "configured": true
```

## 사용

앱 목록 화면 `⋯ > 인스타그램 대조`를 누르면 **최근 게시물 12개**만 읽어 대조한다(증분 방식).
조회 구간보다 오래된 공지는 건드리지 않고, 이미 확인된 공지는 결과를 그대로 유지한다.

과거 전체를 다시 훑고 싶을 때만 `max_items`를 키워 직접 호출한다:

```bash
curl -s -X POST -H "X-API-Key: $(cat .app_api_key)" \
  -H 'Content-Type: application/json' -d '{"max_items": 700}' \
  https://moonhome.kro.kr:8443/api/instagram/sync
```

- **업로드 확인** — 인스타그램에서 실제 게시물을 찾음. 상세 화면에 `인스타그램 게시물 보기` 링크가 생긴다.
- **확인 안 됨** — 앱에서 완료 표시는 했지만 조회 구간 안에서 게시물을 찾지 못함.
  캡션을 많이 고쳤거나 게시물을 삭제한 경우다.
- **미업로드** — 아직 올리지 않은 공지.

필터 버튼(`≡`)으로 이 세 가지 상태별로 목록을 걸러 볼 수 있다.

대조는 **비파괴적**이다. 게시물을 못 찾아도 기존 완료 표시를 지우지 않는다.
반대로 게시물을 찾으면 완료 표시가 자동으로 켜진다.

## 매칭 규칙

`scripts/instagram_sync.py`

- 캡션과 공지 본문에서 해시태그와 공백 차이를 제거한 뒤 비교한다.
- 앞 60자가 같으면 `exact`로 판정한다. 캡션 뒤에 문구를 덧붙였거나 해시태그를 바꿔도 잡힌다.
- 앞부분까지 고쳤으면 유사도 0.78 이상인 공지를 찾아 `fuzzy`로 판정한다(앱에 "캡션 일부 일치"로 표시).
- 어디에도 맞지 않는 게시물(행사 사진 등)은 무시한다.

실제 공지 991건으로 시험했을 때 정확/변형 캡션 14건을 모두 맞히고 오탐은 없었다.

## 문제가 생기면

| 증상 | 확인할 것 |
| --- | --- |
| `configured: false` | `.env`의 `INSTAGRAM_ACCESS_TOKEN` 또는 `data/instagram_token.json`, 그리고 웹 서비스 재시작 |
| `인스타그램 API 오류 400` | 계정이 프로페셔널인지, 테스터 초대를 수락했는지 |
| `인스타그램 API 오류 190` | 토큰 만료(60일 방치). 3단계에서 새로 발급 |
| 확인 안 됨이 많다 | 캡션을 많이 고쳤거나 조회 구간을 벗어난 오래된 게시물. `max_items`를 늘려 호출 |
| 오래전 공지가 미업로드로 남아 있다 | 증분 대조는 최근 12개만 본다. `max_items`를 늘려 한 번 전체 대조 |

## 앱에서 바로 게시하기 (자동 업로드)

상세 화면의 **`인스타그램에 바로 올리기`** 버튼이 공지 이미지와 본문을 캐러셀로 즉시 게시한다.
확인 창을 한 번 거치며, 게시하면 앱에서 되돌릴 수 없다(삭제는 인스타그램에서 직접).

### 동작 방식

인스타그램 콘텐츠 게시 API는 **파일 업로드를 받지 않는다.** 공개 URL을 주면 Meta가 그 URL로
이미지를 직접 가져간다. 그래서 서명된 임시 공개 경로를 따로 뒀다.

```
/pub/media/<만료시각>/<서명>/<이미지경로>
```

- 서명은 `APP_API_KEY`로 만든 HMAC이고 기본 2시간 뒤 만료된다. 서명이 틀리거나 만료되면 403이다.
- 이 경로만 nginx에서 Basic Auth 없이 통과한다.
- 공개 주소는 **Tailscale Funnel**(`https://moon-p151emx.tail70d104.ts.net`)을 쓴다.
  포트를 열지 않고도 유효한 인증서로 공개되므로 Meta가 접근할 수 있다.
  자체 서명 인증서를 쓰는 8443은 Meta가 거부하므로 게시에는 쓸 수 없다.

게시 순서: 이미지별 자식 컨테이너 생성 → 처리 완료 대기 → 캐러셀 컨테이너 생성 →
`media_publish` → 결과 permalink를 `notices_db.json`에 기록하고 완료 표시.

### 제약

| 항목 | 값 |
| --- | --- |
| 캐러셀 이미지 | **최대 10장** (인스타그램 앱은 20장이지만 API는 10장) |
| 이미지 형식 | JPEG만 |
| 캡션 | 2200자까지 (넘으면 자동으로 잘림) |
| 게시 한도 | 계정당 24시간에 100건 |

이미지가 10장을 넘는 공지는 **앞 10장만** 올라가고 앱에 경고가 표시된다.
전부 올려야 하면 `직접 올리기`로 사진 앱에 저장한 뒤 인스타그램에서 수동으로 올린다.

### 게시하지 않고 점검만 하기

`dry_run`을 주면 인스타그램이 이미지를 가져올 수 있는지까지만 확인하고 게시하지 않는다.

```bash
curl -s -X POST -H "X-API-Key: $(cat .app_api_key)" \
  -H 'Content-Type: application/json' -d '{"dry_run": true}' \
  https://moon-p151emx.tail70d104.ts.net/api/notices/<공지키>/publish
```

남은 게시 한도는 `GET /api/instagram/quota`로 확인한다.

---

# 푸시 알림 설정 (APNs)

새 공지가 수집되면 iOS 앱으로 푸시 알림이 오고, 알림을 탭하면 그 공지 상세가 바로 열린다.
텔레그램 알림과 별개로 동작하며, **APNs 인증 키를 등록하기 전까지는 조용히 비활성**이다.

## 준비 (한 번만, 약 10분)

### 1. APNs 인증 키(.p8) 발급

1. https://developer.apple.com/account/resources/authkeys/list → `+`
2. 이름은 아무거나(예: `CNU Info Push`), **Apple Push Notifications service (APNs)** 체크
3. `Continue` → `Register` → **Download** (`AuthKey_XXXXXXXXXX.p8`)
   - **한 번만 내려받을 수 있다.** 잃어버리면 키를 새로 만들어야 한다.
4. 화면의 **Key ID**(10자)를 적어 둔다. 코드 서명 인증서 이름의 괄호 안 값은
   Team ID가 아니다. Team ID는 프로비저닝 프로파일의 `TeamIdentifier`이거나
   개발자 사이트 우측 상단 계정 정보에 있다. Team ID는 `7W7426UKBH`다.

### 2. 앱에 푸시 기능 켜기

Xcode에서 `CnuInfo` 타깃 → `Signing & Capabilities` → `+ Capability` → **Push Notifications**.
자동 서명이면 Xcode가 App ID(`kr.moonhome.cnuinfo`)에 푸시를 등록해 준다.
(저장소에 `ios/CnuInfo/CnuInfo.entitlements`가 이미 들어 있어 `xcodegen generate`만 해도 붙는다.)

### 3. 서버에 키 등록

```bash
# .p8 파일을 서버로 복사
scp -P 2222 ~/Downloads/AuthKey_XXXXXXXXXX.p8 moon@moonhome.kro.kr:/srv/cnuinfo/secrets/
ssh moon@moonhome.kro.kr -p 2222
chmod 600 /srv/cnuinfo/secrets/AuthKey_*.p8

cat >> /srv/cnuinfo/.env <<'EOF'
APNS_KEY_PATH=/srv/cnuinfo/secrets/AuthKey_XXXXXXXXXX.p8
APNS_KEY_ID=XXXXXXXXXX
APNS_TEAM_ID=7W7426UKBH
APNS_BUNDLE_ID=kr.moonhome.cnuinfo
EOF
sudo systemctl restart cnu-info-web cnu-info-monitor
```

확인:

```bash
curl -s -H "X-API-Key: $(cat .app_api_key)" \
  https://moon-p151emx.tail70d104.ts.net/api/push/status | python3 -m json.tool
# → "configured": true
```

### 4. 앱에서 알림 허용

앱 `⋯ > 설정 > 푸시 알림`에서 `알림 허용하기` → iOS 권한 창에서 허용.
허용하면 기기 토큰이 서버에 자동 등록된다(`devices`가 1 이상이 된다).
`시험 알림 보내기`로 바로 확인할 수 있다.

## 개발 빌드와 배포 빌드

APNs는 두 환경이 나뉘어 있고 토큰도 서로 다르다. 앱이 알아서 구분해 서버에 알린다.

| 빌드 | 환경 | APNs 서버 |
| --- | --- | --- |
| Xcode에서 직접 설치 (Debug) | sandbox | `api.sandbox.push.apple.com` |
| TestFlight / App Store (Release) | production | `api.push.apple.com` |

TestFlight로 배포하려면 `ios/CnuInfo/CnuInfo.entitlements`의 `aps-environment`를
`production`으로 바꿔야 한다.

## 동작

- 알림 발송 지점은 `monitor_new_notices.py`의 새 공지 처리 구간이다. 텔레그램 전송 직후 보낸다.
- 같은 공지를 두 번 알리지 않도록 최근 300건의 발송 이력을 `data/push_tokens.json`에 남긴다.
- 알림 실패는 수집을 막지 않는다. 로그에 `[푸시 경고]`만 남는다.
- APNs가 410(Unregistered) 또는 `BadDeviceToken`을 돌려주면 그 토큰을 자동으로 지운다.
  앱을 지운 기기나 환경이 틀린 토큰이 쌓이지 않는다.
- 알림 페이로드에 `notice_key`가 들어 있어, 탭하면 앱이 그 공지 상세로 이동한다.
