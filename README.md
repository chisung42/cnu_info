# CNU Notice Automation Suite

충남대학교 포털의 여러 공지 게시판을 주기적으로 모니터링하고, 첨부파일을 정리·이미지화하여 수동 업로드용 자료를 제공하는 자동화 프로젝트입니다. 모든 경로는 프로젝트 루트를 기준으로 상대경로로 저장되므로 어떤 위치에서 실행하더라도 일관된 디렉터리 구조가 유지됩니다.

## 주요 기능

- **다중 게시판 모니터링**  
  - `scripts/crawl_notices.py`의 `DEFAULT_BOARDS` 리스트에서 게시판 ID, 이름, URL, 수집 개수를 정의합니다.  
  - 기본적으로 학사정보(`academics`), 교육정보(`education`) 게시판을 모니터링합니다.
- **자동 수집 파이프라인**  
  - `scripts/monitor_new_notices.py`가 주기적으로 게시판을 확인합니다.  
  - 새 공지가 발견되면 첨부파일을 다운로드하고, PDF/HWP 변환 및 이미지 생성(`generate_notice_images.py`)까지 실행합니다.  
  - 결과는 `data/notice_links.json`, `data/notices_db.json`에 저장되며, 경로는 모두 프로젝트 기준 상대경로입니다.
- **웹 대시보드**  
  - `scripts/web_dashboard.py`가 Flask 기반으로 대시보드를 제공합니다.  
  - 게시판별 탭과 페이지(기본 5건씩)로 정돈된 카드 뷰, 제목/본문 복사 버튼, 이미지 미리보기, ZIP 다운로드 링크를 제공합니다.

## 폴더 구조

```
.
├── data/                      # notice_links.json / notices_db.json 저장
├── scripts/
│   ├── crawl_notices.py        # 단일 실행용 크롤러 (개발/디버깅)
│   ├── monitor_new_notices.py  # 주기적 모니터링 엔트리포인트
│   ├── generate_instagram_images.py  # 단일 공지 이미지 생성 유틸
│   ├── web_dashboard.py        # Flask 대시보드 서버
│   └── (기타 유틸 스크립트)
├── attachments/                # 게시판별 첨부 및 생성 이미지 (상대경로로 저장)
└── requirements.txt
```

## 설치

```bash
python -m venv .venv
source .venv/bin/activate  # Windows는 .venv\Scripts\activate
pip install -r requirements.txt
```

## 사용 방법

### 1. (선택) 이전 데이터 초기화

절대경로가 포함된 예전 데이터가 섞여 있다면 아래처럼 백업 후 삭제하고 새로 수집하는 것이 좋습니다.

```bash
mv data/notice_links.json data/notice_links_backup.json 2>/dev/null || true
mv data/notices_db.json data/notices_db_backup.json 2>/dev/null || true
rm -rf attachments  # 첨부/이미지 전체 초기화 (선택)
```

### 2. 모니터링 실행

```bash
python scripts/monitor_new_notices.py --interval 30
```

- `--interval`은 분 단위 주기(기본 60).  
- `--max-images`로 공지별 생성 이미지 최대 수 조절(기본 20).  
- `--boards-config`에 JSON 파일을 지정하면 `DEFAULT_BOARDS` 대신 사용자 정의 게시판 목록을 사용할 수 있습니다.

스크립트가 실행되면 `data/notice_links.json`과 `data/notices_db.json`, 그리고 `attachments/<board_id>/<notice_id>/...` 구조가 자동 생성됩니다.

### 3. 웹 대시보드 실행

```bash
python scripts/web_dashboard.py --host 127.0.0.1 --port 8000
```

- 브라우저에서 `http://127.0.0.1:8000` 접속 → 게시판별 탭과 페이지별 카드 목록 확인.  
- 각 공지 카드에서 제목/본문 복사, 이미지 미리보기, ZIP 다운로드(이미지 전체) 기능을 사용할 수 있습니다.

### 4. 개별 유틸리티

- **단일 크롤링**: `python scripts/crawl_notices.py`  
  - 새 JSON을 만들거나 구조를 점검할 때 사용.  
- **개별 이미지 재생성**: `python scripts/generate_instagram_images.py --notice-id <ID>`  
  - 기존 데이터를 기반으로 특정 공지의 이미지만 다시 만들고 싶을 때.

## 설정 변경

- 모든 게시판 설정은 `scripts/crawl_notices.py`의 `DEFAULT_BOARDS`에서 관리합니다.
  ```python
  DEFAULT_BOARDS = [
      {
          "id": "academics",
          "name": "학사정보",
          "url": "https://plus.cnu.ac.kr/_prog/_board/?code=sub07_0702&site_dvs_cd=kr&menu_dvs_cd=0702",
          "max_articles": MAX_ARTICLES,
      },
      ...
  ]
  ```
  게시판을 추가하면 모니터링/대시보드가 자동으로 반영됩니다.

- 첨부 저장경로(`DEFAULT_ATTACHMENTS_DIR`)와 이전 공지 저장 파일(`PREV_NOTICES_FILE`)은 프로젝트 루트 기준 상대값으로 관리됩니다.

## 주의 사항 및 팁

- 경로는 저장 시 상대경로, 파일 접근 시 절대경로로 자동 변환되므로 실행 위치와 관계없이 동일하게 동작합니다.
- 데이터가 쌓인 뒤 구조를 변경했다면 기존 JSON과 첨부 디렉터리를 초기화하는 것이 가장 안전합니다.
- 프로젝트는 충남대학교 포털 사이트 구조에 의존하므로 DOM 변경 시 셀렉터 수정을 검토해야 합니다.
- 웹 대시보드는 개발용 Flask 서버이므로 운영 환경에서는 적절한 WSGI 서버로 배포하거나 프록시 뒤에 두는 것을 권장합니다.

## 레거시 스크립트

`etc/` 폴더에 Instagram 자동 업로드용 스크립트가 남아 있지만, 현재 워크플로우에서는 사용하지 않습니다. 필요 시 상대경로 기반으로 정리되어 있으니 참고용으로만 유지하고 있습니다.

---

문의나 개선 아이디어가 있다면 언제든지 이슈나 PR로 알려주세요. 😊