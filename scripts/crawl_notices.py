import os
import re
import argparse
import shutil
import zipfile
import tarfile
import subprocess
import requests
from bs4 import BeautifulSoup
import json
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse, parse_qs  # 상대 URL 조합을 위해 추가
import schedule
import time
import sys

# 첨부파일 스크랩핑은 제거됨

# 네트워크/파서 상수
SCRIPT_DIR = Path(__file__).resolve().parent
BASE_PATH = SCRIPT_DIR.parent
BASE_DIR = str(BASE_PATH)

if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
if str(BASE_PATH) not in sys.path:
    sys.path.insert(0, str(BASE_PATH))


def _to_abs(path: str | None) -> str:
    if not path:
        return ''
    p = Path(path)
    if not p.is_absolute():
        p = (BASE_PATH / p).resolve()
    return str(p)


def _to_rel(path: str | None) -> str:
    if not path:
        return ''
    p = Path(path)
    if not p.is_absolute():
        return str(p)
    try:
        return str(p.relative_to(BASE_PATH))
    except ValueError:
        return str(p)


HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Accept-Language': 'ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7',
    'Referer': '',
}
TIMEOUT = 20
MAX_ARTICLES = 10
DEFAULT_ATTACHMENTS_DIR = 'attachments'
PREV_NOTICES_FILE = 'previous_notices.json'  # 이전 notices 저장 파일
DEFAULT_INTERVAL_HOURS = 1  # 기본 주기 1시간

# 모니터링할 게시판 기본 목록 (필요 시 이곳만 수정)
DEFAULT_BOARDS = [
    {
        'id': 'academics',
        'name': '학사정보',
        'url': 'https://plus.cnu.ac.kr/_prog/_board/?code=sub07_0702&site_dvs_cd=kr&menu_dvs_cd=0702',
        'max_articles': MAX_ARTICLES,
    },
    {
        'id': 'education',
        'name': '교육정보',
        'url': 'https://plus.cnu.ac.kr/_prog/_board/?code=sub07_0704&site_dvs_cd=kr&menu_dvs_cd=0704',
        'max_articles': MAX_ARTICLES,
    },
]


def _default_board_url() -> str:
    return DEFAULT_BOARDS[0]['url'] if DEFAULT_BOARDS else ''

def _normalize_whitespace(text: str) -> str:
    """Collapse all whitespace to single spaces while preserving content."""
    return ' '.join(text.split()) if text else ''

def _extract_content_strict(detail_soup: BeautifulSoup) -> tuple[str, str]:
    """Strict extractor: only div.board_viewDetail (excluding PDF viewer)."""
    node = detail_soup.select_one('div.board_viewDetail:not(.board_pdf_viewer)')
    if node:
        raw_text = node.get_text(separator=' ', strip=True)
        normalized = _normalize_whitespace(raw_text)
        return normalized, 'css:div.board_viewDetail:not(.board_pdf_viewer)(strict)'
    return '', 'css:div.board_viewDetail:not(.board_pdf_viewer)(strict-miss)'

def _get_soup(session: requests.Session, url: str) -> BeautifulSoup | None:
    try:
        headers = dict(HEADERS)
        headers['Referer'] = url
        resp = session.get(url, headers=headers, timeout=TIMEOUT, allow_redirects=True)
        resp.encoding = 'utf-8'
        return BeautifulSoup(resp.content, 'html.parser', from_encoding='utf-8')
    except requests.RequestException:
        return None

def _sanitize_filename(name: str) -> str:
    if not name:
        return ''
    # 제거: 제어문자 및 파일명 금지 문자
    name = re.sub(r'[\\/:*?"<>|\r\n\t]+', '_', name)
    name = name.strip(' .')
    return name or 'attachment'

def _build_notice_id(post_url: str | None, title: str, fallback_index: int) -> str:
    """URL/제목 기반으로 공지 고유 ID 생성."""
    if post_url:
        try:
            parsed = urlparse(post_url)
            qs = parse_qs(parsed.query)
            code = (qs.get('code', [''])[0] or '')
            no = (qs.get('no', [''])[0] or '')
            m = re.search(r'^sub(.+)$', code)
            sub_part = m.group(1) if m else code
            sub_part = _sanitize_filename(sub_part)
            no = _sanitize_filename(no)
            if sub_part and no:
                return f"{sub_part}_{no}"
            path_candidate = _sanitize_filename((parsed.path or '/').replace('/', '_'))
            if path_candidate:
                return path_candidate
        except Exception:
            pass
    title_candidate = _sanitize_filename(title)
    if title_candidate:
        return f"{title_candidate}_{fallback_index + 1:03d}"
    return f"notice_{fallback_index + 1:03d}"


def _derive_post_subdir(base_dir: str, notice_id: str) -> str:
    """지정된 notice_id를 사용하여 하위 디렉터리 경로 계산."""
    try:
        base_dir_abs = _to_abs(base_dir)
        safe_id = _sanitize_filename(notice_id)
        if not safe_id:
            safe_id = 'notice'
        return os.path.join(base_dir_abs, safe_id)
    except Exception:
        return _to_abs(base_dir)

def _is_archive_file(path: str) -> bool:
    lower = path.lower()
    return lower.endswith('.zip') or lower.endswith('.tar') or lower.endswith('.tgz') \
        or lower.endswith('.tar.gz') or lower.endswith('.tar.bz2') or lower.endswith('.tbz') \
        or lower.endswith('.tbz2')

def _extract_archive(archive_path: str, dest_dir: str) -> bool:
    try:
        if zipfile.is_zipfile(archive_path):
            with zipfile.ZipFile(archive_path, 'r') as zf:
                zf.extractall(dest_dir)
            return True
        # tar variants
        if tarfile.is_tarfile(archive_path):
            with tarfile.open(archive_path, 'r:*') as tf:
                tf.extractall(dest_dir)
            return True
    except Exception:
        return False
    return False

def _postprocess_attachments(post_dir: str, title: str) -> None:
    """Within post_dir, organize attachments:
    - Move all downloaded files into post_dir/original (create if not exists)
    - Extract archives into original
    - Create pdfs dir; move PDFs from original; convert HWPs to PDFs (Vertopal CLI if available)
    - Create pngs dir; convert all PDFs to PNG pages
    """
    original_dir = os.path.join(post_dir, 'original')
    pdfs_dir = os.path.join(post_dir, 'pdfs')
    pngs_dir = os.path.join(post_dir, 'pngs')

    os.makedirs(original_dir, exist_ok=True)

    # 1) Move any files in post_dir root (excluding our subdirs) into original
    try:
        for name in os.listdir(post_dir):
            src = os.path.join(post_dir, name)
            if not os.path.isfile(src):
                continue
            if name in ('original', 'pdfs', 'pngs'):
                continue
            dst = os.path.join(original_dir, name)
            if os.path.abspath(src) != os.path.abspath(dst):
                try:
                    shutil.move(src, dst)
                except Exception:
                    pass
    except Exception:
        pass

    # 2) Extract archives inside original (non-recursive)
    try:
        for name in os.listdir(original_dir):
            src = os.path.join(original_dir, name)
            if os.path.isfile(src) and _is_archive_file(src):
                _extract_archive(src, original_dir)
    except Exception:
        pass

    # 3) Prepare pdfs dir and process original recursively
    os.makedirs(pdfs_dir, exist_ok=True)

    def unique_path(dest_dir: str, filename: str) -> str:
        base, ext = os.path.splitext(filename)
        candidate = filename
        idx = 1
        while os.path.exists(os.path.join(dest_dir, candidate)):
            candidate = f"{base}_{idx}{ext}"
            idx += 1
        return os.path.join(dest_dir, candidate)

    def convert_hwp_to_pdf(hwp_path: str, out_pdf_path: str) -> bool:
        # Try Vertopal CLI variants
        cmd_variants = [
            ['vertopal', 'convert', hwp_path, out_pdf_path],
            ['vertopal', '-i', hwp_path, '-o', out_pdf_path],
            ['vertopal-cli', 'convert', hwp_path, out_pdf_path],
        ]
        for cmd in cmd_variants:
            try:
                res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=300)
                if res.returncode == 0 and os.path.exists(out_pdf_path):
                    return True
            except Exception:
                continue
        return False

    # Move PDFs and convert HWPs
    for root, _, files in os.walk(original_dir):
        for fname in files:
            src = os.path.join(root, fname)
            lower = fname.lower()
            if lower.endswith('.pdf'):
                safe_name = _sanitize_filename(fname)
                dst = unique_path(pdfs_dir, safe_name)
                try:
                    shutil.move(src, dst)
                except Exception:
                    pass
            elif lower.endswith('.hwp'):
                safe_base = _sanitize_filename(os.path.splitext(fname)[0])
                out_pdf = unique_path(pdfs_dir, f"{safe_base}.pdf")
                convert_hwp_to_pdf(src, out_pdf)

    # 4) Convert PDFs to PNGs
    os.makedirs(pngs_dir, exist_ok=True)

    def pdf_to_pngs(pdf_path: str, out_dir: str) -> None:
        base = _sanitize_filename(os.path.splitext(os.path.basename(pdf_path))[0])
        # First: pdf2image (poppler required)
        try:
            from pdf2image import convert_from_path  # type: ignore
            images = convert_from_path(pdf_path, dpi=200)
            for i, img in enumerate(images):
                out_path = os.path.join(out_dir, f"{base}_{i+1:03d}.png")
                img.save(out_path, 'PNG')
            return
        except Exception:
            pass
        # Fallback: PyMuPDF
        try:
            import fitz  # type: ignore
            doc = fitz.open(pdf_path)
            try:
                for i, page in enumerate(doc):
                    pix = page.get_pixmap(matrix=fitz.Matrix(2.0, 2.0))
                    out_path = os.path.join(out_dir, f"{base}_{i+1:03d}.png")
                    pix.save(out_path)
            finally:
                doc.close()
            return
        except Exception:
            pass
        # Final fallback: ImageMagick 'magick'
        try:
            out_pattern = os.path.join(out_dir, f"{base}_%03d.png")
            subprocess.run(['magick', '-density', '200', pdf_path, '-quality', '92', out_pattern],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=300)
        except Exception:
            pass

    try:
        for name in os.listdir(pdfs_dir):
            if name.lower().endswith('.pdf'):
                pdf_to_pngs(os.path.join(pdfs_dir, name), pngs_dir)
    except Exception:
        pass

def load_previous_notices(filename: str = PREV_NOTICES_FILE) -> list:
    """이전 notices JSON 로드"""
    filename_abs = _to_abs(filename)
    if os.path.exists(filename_abs):
        try:
            with open(filename_abs, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return []

def save_notices(notices: list, filename: str = PREV_NOTICES_FILE) -> None:
    """notices JSON 저장"""
    try:
        filename_abs = _to_abs(filename)
        with open(filename_abs, 'w', encoding='utf-8') as f:
            json.dump(notices, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

def detect_new_notices(current_notices: list, previous_notices: list) -> list:
    """새 게시물 ID 비교로 새 notices 반환"""
    if not previous_notices:
        return current_notices

    def _notice_key(item: dict) -> str:
        if not isinstance(item, dict):
            return ''
        notice_id = item.get('id')
        if notice_id:
            return str(notice_id)
        url = item.get('url', '')
        if isinstance(url, str) and 'no=' in url:
            return url.split('no=')[-1].split('&')[0]
        return url if isinstance(url, str) else ''

    prev_ids = {_notice_key(item) for item in previous_notices if isinstance(item, dict)}
    new_notices = [item for item in current_notices if not isinstance(item, dict) or _notice_key(item) not in prev_ids]
    return new_notices


def list_notice_links(
    base_url: str | None = None,
    *,
    max_articles: int = MAX_ARTICLES,
    session: requests.Session | None = None,
) -> list[dict]:
    """공지사항 목록에서 게시물 링크만 추출."""
    own_session = False
    if session is None:
        session = requests.Session()
        own_session = True

    try:
        target_url = base_url or _default_board_url()
        if not target_url:
            return []

        soup = _get_soup(session, target_url)
        if soup is None:
            return []

        links: list[dict] = []
        articles = soup.find_all('tr')

        for idx, article in enumerate(articles[:max_articles]):
            title_elem = article.find('a') or article.find(
                'td',
                string=lambda text: text and len(text.strip()) > 10,
            )
            if not title_elem:
                continue

            title = (
                title_elem.get_text().strip()
                if hasattr(title_elem, 'get_text')
                else str(title_elem).strip()
            )
            if len(title) < 5:
                continue

            link = title_elem.get('href')
            if not link:
                all_links = article.find_all('a')
                if all_links:
                    link = all_links[0].get('href') if all_links[0].get('href') else None
            if not link:
                continue

            full_link = urljoin(target_url, link)
            notice_id = _build_notice_id(full_link, title, idx)

            links.append(
                {
                    'id': notice_id,
                    'url': full_link,
                    'title': title,
                    'index': idx,
                }
            )

        return links
    finally:
        if own_session:
            session.close()


def crawl_notice_detail(
    url: str,
    notice_id: str | None = None,
    *,
    session: requests.Session | None = None,
    download_attachments: bool = False,
    attachments_dir: str = DEFAULT_ATTACHMENTS_DIR,
    fallback_index: int = 0,
    title_hint: str | None = None,
    board_id: str | None = None,
    board_name: str | None = None,
    board_url: str | None = None,
) -> dict | None:
    """단일 게시물 상세 크롤링."""
    if not url:
        return None

    own_session = False
    if session is None:
        session = requests.Session()
        own_session = True

    try:
        detail_soup = _get_soup(session, url)
        if detail_soup is None:
            return None

        content, matched_strategy = _extract_content_strict(detail_soup)

        title = (title_hint or '').strip()
        if not title:
            title_candidates = [
                detail_soup.select_one('div.board_view > div.top h2'),
                detail_soup.select_one('div.board_view > div.top > strong'),
                detail_soup.select_one('div.board_view h2'),
                detail_soup.select_one('h2.title'),
                detail_soup.select_one('h2'),
            ]
            for node in title_candidates:
                if node and node.get_text(strip=True):
                    title = node.get_text(strip=True)
                    break

        date_text = ''
        date_elem = (
            detail_soup.select_one('#txt > ul > li.date')
            or detail_soup.select_one('div.board_view > ul > li.date')
            or detail_soup.select_one('li.date')
        )
        if date_elem and date_elem.get_text():
            date_text = re.sub(r'^등록일', '', date_elem.get_text().strip()).strip()

        if not notice_id:
            notice_id = _build_notice_id(url, title or (title_hint or ''), fallback_index)

        attachments: list[str] = []
        downloaded_files: list[str] = []
        post_dir: str | None = None
        if download_attachments and notice_id:
            post_dir = _derive_post_subdir(attachments_dir, notice_id)
            try:
                os.makedirs(post_dir, exist_ok=True)
            except Exception:
                pass

        try:
            attach_nodes = detail_soup.select('li.file a[href]')
        except Exception:
            attach_nodes = []

        download_targets: list[tuple[str, str]] = []
        for i, a_tag in enumerate(attach_nodes, start=1):
            href = a_tag.get('href')
            if not href:
                continue
            abs_url = urljoin(url, href)
            if abs_url not in attachments:
                attachments.append(abs_url)
            if download_attachments:
                title_attr = a_tag.get('title') or ''
                basename = os.path.basename(urlparse(abs_url).path) or ''
                suggested = title_attr or basename or f'attachment_{i}'
                download_targets.append((abs_url, _sanitize_filename(suggested)))

        if download_targets and post_dir:
            dl_headers = dict(HEADERS)
            dl_headers['Referer'] = url
            for idx, (file_url, name) in enumerate(download_targets, start=1):
                safe_title_prefix = _sanitize_filename((title or 'notice')[:50]) or 'notice'
                filename = f"{safe_title_prefix}_{idx}_{name}"
                filepath = os.path.join(post_dir, filename)
                try:
                    resp = session.get(
                        file_url,
                        headers=dl_headers,
                        timeout=TIMEOUT,
                        allow_redirects=True,
                    )
                    if resp.status_code == 200 and resp.content:
                        with open(filepath, 'wb') as f:
                            f.write(resp.content)
                        downloaded_files.append(filepath)
                except Exception:
                    continue

            try:
                _postprocess_attachments(post_dir, title)
            except Exception:
                pass

        pdf_dir = os.path.join(post_dir, 'pdfs') if post_dir else None
        png_dir = os.path.join(post_dir, 'pngs') if post_dir else None

        pdf_files = (
            sorted(
                os.path.join(pdf_dir, name)
                for name in os.listdir(pdf_dir)
                if name.lower().endswith('.pdf')
            )
            if pdf_dir and os.path.isdir(pdf_dir)
            else []
        )
        png_files = (
            sorted(
                os.path.join(png_dir, name)
                for name in os.listdir(png_dir)
                if name.lower().endswith('.png')
            )
            if png_dir and os.path.isdir(png_dir)
            else []
        )

        notice_key = f"{board_id}::{notice_id}" if board_id else notice_id

        record = {
            'id': notice_id,
            'title': title or (title_hint or ''),
            'date': date_text,
            'content': content,
            'selector': matched_strategy,
            'url': url,
            'attachments': attachments,
            'downloaded_files': [_to_rel(p) for p in downloaded_files],
            'attachment_dir': _to_rel(post_dir),
            'pdf_files': [_to_rel(p) for p in pdf_files],
            'png_files': [_to_rel(p) for p in png_files],
            'crawled_at': datetime.now().isoformat(),
            'board_id': board_id,
            'board_name': board_name,
            'board_url': board_url,
            'notice_key': notice_key,
        }
        return record
    finally:
        if own_session:
            session.close()

def crawl_notices_once(
    download_attachments: bool = False,
    attachments_dir: str = DEFAULT_ATTACHMENTS_DIR,
    *,
    board_url: str | None = None,
    board_id: str | None = None,
    board_name: str | None = None,
    max_articles: int = MAX_ARTICLES,
) -> list:
    """한 번 크롤링 실행 (기존 함수 기반)"""
    session = requests.Session()
    notices: list[dict] = []
    printed_count = 0

    try:
        target_url = board_url or _default_board_url()
        attachments_dir_abs = _to_abs(attachments_dir)
        link_entries = list_notice_links(
            target_url,
            max_articles=max_articles,
            session=session,
        )
        for entry in link_entries:
            detail = crawl_notice_detail(
                entry.get('url'),
                entry.get('id'),
                session=session,
                download_attachments=download_attachments,
                attachments_dir=attachments_dir_abs,
                fallback_index=entry.get('index', 0),
                title_hint=entry.get('title'),
                board_id=board_id,
                board_name=board_name,
                board_url=target_url,
            )
            if not detail:
                continue

            if printed_count > 0:
                print()
            print(f"id: {detail.get('id')}")
            print(f"title: {detail.get('title')}")
            print(f"date: {detail.get('date')}")
            print(f"content: {detail.get('content')}")
            print(f"selector: {detail.get('selector')}")
            printed_count += 1

            notices.append(detail)
        return notices
    finally:
        session.close()

def monitor_notices(interval_hours: int = DEFAULT_INTERVAL_HOURS, download_attachments: bool = False, attachments_dir: str = DEFAULT_ATTACHMENTS_DIR):
    """주기적 모니터링: 새 게시물 확인 및 알림"""
    print(f"공지사항 모니터링 시작 (주기: {interval_hours}시간)")
    previous = load_previous_notices()
    
    def job():
        nonlocal previous
        current = crawl_notices_once(download_attachments, attachments_dir)
        new = detect_new_notices(current, previous)
        if new:
            print(f"\n새 게시물 발견: {len(new)}개")
            for item in new:
                print(f"- 제목: {item['title']}")
                print(f"  URL: {item['url']}")
                print(f"  내용: {item['content'][:200]}...")
            # 새 notices 전체 저장 (이전 + 새)
            all_notices = previous + new
            save_notices(all_notices)
            previous = all_notices
            # 새 notices JSON 저장
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            new_filename = f'new_notices_{timestamp}.json'
            with open(new_filename, 'w', encoding='utf-8') as f:
                json.dump(new, f, ensure_ascii=False, indent=2)
            print(f"새 게시물 저장: {new_filename}")
        else:
            print(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} - 새 게시물 없음")
        # 전체 notices 저장
        save_notices(current)
        previous = current
    
    # 첫 실행
    job()
    
    # 스케줄 설정
    schedule.every(interval_hours).hours.do(job)
    
    while True:
        schedule.run_pending()
        time.sleep(60)  # 1분 대기

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='CNU notices crawler (strict mode)')
    parser.add_argument('--download-attachments', action='store_true', help='첨부파일을 attachments 디렉토리에 다운로드')
    parser.add_argument('--attachments-dir', default=DEFAULT_ATTACHMENTS_DIR, help='첨부파일 저장 디렉토리 (기본: attachments)')
    parser.add_argument('--monitor', action='store_true', help='주기적 모니터링 모드 실행')
    parser.add_argument('--interval', type=int, default=DEFAULT_INTERVAL_HOURS, help='모니터링 주기 (시간, 기본: 1)')
    args = parser.parse_args()
    
    if args.monitor:
        monitor_notices(args.interval, args.download_attachments, args.attachments_dir)
    else:
        notices = crawl_notices_once(args.download_attachments, args.attachments_dir)
        if notices:
            filename = f'notices_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json'
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(notices, f, ensure_ascii=False, indent=2)
            print(f"크롤링 완료: {filename} ({len(notices)}개 게시물)")

