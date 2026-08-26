import os
import re
import io
import hashlib
import argparse
import shutil
import zipfile
import tarfile
import subprocess
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
import json
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse, parse_qs  # 상대 URL 조합을 위해 추가
import schedule
import time
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import unquote
from rhwp_convert_only import HWP_EXTENSIONS, convert_hwp_to_pdf_with_rhwp, find_rhwp

try:
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except Exception:
    urllib3 = None

# 첨부파일 스크랩핑은 제거됨

# 네트워크/파서 상수
SCRIPT_DIR = Path(__file__).resolve().parent
BASE_PATH = SCRIPT_DIR.parent
BASE_DIR = str(BASE_PATH)
load_dotenv(BASE_PATH / '.env')

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
SSL_VERIFY_DISABLED_HOSTS = {'hannam.ac.kr', 'www.hannam.ac.kr', 'my.hnu.kr'}
FACTCHAT_BASE_URL = os.getenv('FACTCHAT_BASE_URL', 'https://factchat-cloud.mindlogic.ai/v1/gateway').rstrip('/')
FACTCHAT_API_KEY = os.getenv('FACTCHAT_API_KEY', '')
FACTCHAT_MODEL = os.getenv('FACTCHAT_MODEL', 'gpt-5.6-luna')

# 모니터링할 게시판 기본 목록 (필요 시 이곳만 수정)
DEFAULT_BOARDS = [
    {
        'id': 'general',
        'name': '일반소식',
        'url': 'https://plus.cnu.ac.kr/_prog/_board/?code=sub07_0701&site_dvs_cd=kr&menu_dvs_cd=0701',
        'max_articles': MAX_ARTICLES,
    },
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
    {
        'id': 'startup',
        'name': '사업단 창업ㆍ교육',
        'url': 'https://plus.cnu.ac.kr/_prog/_board/?code=sub07_0709&site_dvs_cd=kr&menu_dvs_cd=0709',
        'max_articles': MAX_ARTICLES,
    },
    {
        'id': 'recruitment',
        'name': '채용초빙',
        'url': 'https://plus.cnu.ac.kr/_prog/_board/?code=sub07_0705&site_dvs_cd=kr&menu_dvs_cd=0705',
        'max_articles': MAX_ARTICLES,
    },
    {
        'id': 'scholarship',
        'name': '장학정보',
        'url': 'https://plus.cnu.ac.kr/_prog/_board/?code=sub07_0713&site_dvs_cd=kr&menu_dvs_cd=0713',
        'max_articles': MAX_ARTICLES,
    }
]


def _default_board_url() -> str:
    return DEFAULT_BOARDS[0]['url'] if DEFAULT_BOARDS else ''

def _normalize_whitespace(text: str) -> str:
    """Collapse all whitespace to single spaces while preserving content."""
    return ' '.join(text.split()) if text else ''

def _normalize_text_preserve_newlines(text: str) -> str:
    """
    Normalize text while preserving user-visible line breaks.
    - Keep paragraph breaks (blank lines)
    - Collapse excessive spaces/tabs within each line
    - Collapse 3+ consecutive newlines to 2
    """
    if not text:
        return ''
    value = text.replace('\r\n', '\n').replace('\r', '\n')
    lines = value.split('\n')
    cleaned_lines: list[str] = []
    for line in lines:
        # keep empty lines as paragraph separators
        if not line or not line.strip():
            cleaned_lines.append('')
            continue
        cleaned_lines.append(_normalize_whitespace(line))
    normalized = '\n'.join(cleaned_lines).strip()
    normalized = re.sub(r'\n{3,}', '\n\n', normalized)
    return normalized


def _extract_table_text(table) -> str:
    """Render an HTML table as readable, row-based plain text.

    Do not use ``get_text(separator='\\n')`` for tables: the CNU editor splits
    short text such as ``(1차)`` into several ``span`` elements, which would turn
    it into separate lines.  Text inside a cell keeps its original inline order,
    while the table itself remains row/column based.  ``rowspan`` and ``colspan`` are
    expanded so rows retain their relationship when copied to the dashboard.
    """
    rows: list[list[str]] = []
    pending_rowspans: dict[int, tuple[int, str]] = {}

    for tr in table.find_all('tr'):
        row: list[str] = []
        column = 0

        def fill_pending() -> None:
            nonlocal column
            while column in pending_rowspans:
                remaining, value = pending_rowspans[column]
                # The value was already emitted at the top of its merged cell.
                # Keep the following rows aligned without repeating it.
                row.append('')
                if remaining <= 1:
                    del pending_rowspans[column]
                else:
                    pending_rowspans[column] = (remaining - 1, value)
                column += 1

        for cell in tr.find_all(['th', 'td'], recursive=False):
            fill_pending()
            # A separator between every descendant incorrectly changes
            # ``<span>(1</span><span>차)</span>`` to ``(1 차)``.  Keep inline
            # text adjacent, but turn explicit HTML line breaks into spaces.
            for line_break in cell.find_all('br'):
                line_break.replace_with(' ')
            # Do not use ``strip=True`` here: BeautifulSoup would remove the
            # trailing space in ``</span><span>) </span>`` before joining it.
            paragraphs = cell.find_all('p', recursive=False)
            if paragraphs:
                # Editors often split one visual line into multiple <p>s for
                # wrapping, so keep them as one cell value rather than adding
                # artificial line breaks or punctuation.
                value = ' '.join(
                    _normalize_whitespace(paragraph.get_text('', strip=False))
                    for paragraph in paragraphs
                )
            else:
                value = _normalize_whitespace(cell.get_text('', strip=False))
            colspan = max(1, int(cell.get('colspan', 1) or 1))
            rowspan = max(1, int(cell.get('rowspan', 1) or 1))
            for offset in range(colspan):
                # Emit a merged cell once (top-left), then retain its shape
                # as empty aligned cells instead of duplicating its text.
                row.append(value if offset == 0 else '')
                if rowspan > 1:
                    pending_rowspans[column + offset] = (rowspan - 1, value)
            column += colspan

        fill_pending()
        if any(row):
            rows.append(row)

    if not rows:
        return ''

    rendered_rows: list[str] = []
    for row in rows:
        while row and not row[-1]:
            row.pop()
        rendered_rows.append(' | '.join(row))
    return '\n'.join(rendered_rows)


def _extract_content_node(node) -> str:
    """Extract direct body children, rendering tables independently."""
    parts: list[str] = []
    table_number = 0

    for child in node.find_all(recursive=False):
        if not getattr(child, 'name', None):
            continue
        if child.name == 'table':
            table_text = _extract_table_text(child)
            if table_text:
                table_number += 1
                parts.append(f'[표 {table_number}]\n{table_text}')
            continue
        # This hidden HWP-editor payload duplicates the visible table data.
        if child.get('id') == 'hwpEditorBoardContent':
            continue
        text = _normalize_text_preserve_newlines(child.get_text('\n', strip=True))
        if text:
            parts.append(text)

    return _normalize_text_preserve_newlines('\n\n'.join(parts))


def _summarize_complex_content(*, title: str, raw_content: str) -> str | None:
    """Create a factual, student-friendly Korean summary through FactChat."""
    if not FACTCHAT_API_KEY or not raw_content:
        return None

    prompt = f'''다음 충남대학교 공지의 본문을 학생이 바로 이해할 수 있는 한국어 안내문으로 정리해 주세요.

원칙:
- 원문에 있는 사실, 날짜, 대상, 상태만 사용하고 추측하거나 누락하지 마세요.
- 표와 흐름도는 마크다운 표로 복사하지 말고, "신청 절차", "대상", "기간 및 현재 상태", "확인 방법"처럼 읽기 쉬운 항목으로 풀어 쓰세요.
- 날짜·상태가 여러 개면 각각의 대상/단계와 정확히 연결하세요.
- 불명확한 정보는 단정하지 말고 "원문 확인 필요"라고 쓰세요.
- 불필요한 인사, 해시태그, 원문 링크, 설명은 넣지 마세요.

제목: {title}

원문 추출본:
{raw_content}
'''
    try:
        response = requests.post(
            f'{FACTCHAT_BASE_URL}/chat/completions/',
            headers={
                'Authorization': f'Bearer {FACTCHAT_API_KEY}',
                'Content-Type': 'application/json',
            },
            json={
                'model': FACTCHAT_MODEL,
                'messages': [
                    {'role': 'system', 'content': 'You faithfully rewrite Korean university notices without inventing facts.'},
                    {'role': 'user', 'content': prompt},
                ],
            },
            timeout=45,
        )
        response.raise_for_status()
        payload = response.json()
        summary = payload['choices'][0]['message']['content']
        return _normalize_text_preserve_newlines(summary) if isinstance(summary, str) else None
    except (requests.RequestException, KeyError, IndexError, TypeError, ValueError) as exc:
        print(f'[AI 본문 정리 건너뜀] {exc}', file=sys.stderr)
        return None


def _extract_content_strict(detail_soup: BeautifulSoup) -> tuple[str, str]:
    """Strict extractor: only div.board_viewDetail (excluding PDF viewer)."""
    node = detail_soup.select_one('div.board_viewDetail:not(.board_pdf_viewer)')
    if node:
        normalized = _extract_content_node(node)
        return normalized, 'css:div.board_viewDetail:not(.board_pdf_viewer)(table-aware)'
    return '', 'css:div.board_viewDetail:not(.board_pdf_viewer)(strict-miss)'

def _get_soup(session: requests.Session, url: str) -> BeautifulSoup | None:
    try:
        headers = dict(HEADERS)
        headers['Referer'] = url
        parsed = urlparse(url)
        verify = parsed.hostname not in SSL_VERIFY_DISABLED_HOSTS
        resp = session.get(url, headers=headers, timeout=TIMEOUT, allow_redirects=True, verify=verify)
        resp.encoding = 'utf-8'
        return BeautifulSoup(resp.content, 'html.parser', from_encoding='utf-8')
    except requests.RequestException:
        return None


def _parse_content_disposition_filename(content_disposition: str) -> str:
    """
    Parse filename from Content-Disposition.
    Supports simple `filename=` and RFC 5987 `filename*=utf-8''...` forms.
    """
    if not content_disposition:
        return ''
    # Prefer RFC 5987 filename*
    m = re.search(r"filename\*\s*=\s*([^']*)''([^;]+)", content_disposition, flags=re.IGNORECASE)
    if m:
        # charset = m.group(1)  # currently unused
        value = m.group(2).strip().strip('"').strip()
        try:
            return unquote(value)
        except Exception:
            return value
    # Fallback filename=
    m = re.search(r'filename\s*=\s*("?)([^";]+)\1', content_disposition, flags=re.IGNORECASE)
    if m:
        return m.group(2).strip()
    return ''


def _looks_like_html_bytes(data: bytes) -> bool:
    if not data:
        return False
    head = data[:1024].lstrip()
    head_low = head.lower()
    return (
        head_low.startswith(b'<!doctype html')
        or head_low.startswith(b'<html')
        or b'<html' in head_low[:256]
        or b'<head' in head_low[:256]
        or b'<body' in head_low[:256]
    )


_CNU_DOWNLOAD_ERROR = b'Error can not open file!!'
_OLE_SIGNATURE = bytes.fromhex('D0CF11E0A1B11AE1')
_SIGNATURES_BY_EXTENSION = {
    '.pdf': (b'%PDF-',),
    '.png': (b'\x89PNG\r\n\x1a\n',),
    '.jpg': (b'\xff\xd8\xff',),
    '.jpeg': (b'\xff\xd8\xff',),
    '.gif': (b'GIF87a', b'GIF89a'),
    '.bmp': (b'BM',),
    '.webp': (b'RIFF',),
    '.doc': (_OLE_SIGNATURE,),
    '.xls': (_OLE_SIGNATURE,),
    '.ppt': (_OLE_SIGNATURE,),
    '.hwp': (_OLE_SIGNATURE, b'HWP Document File'),
    '.docx': (b'PK\x03\x04',),
    '.xlsx': (b'PK\x03\x04',),
    '.pptx': (b'PK\x03\x04',),
    '.hwpx': (b'PK\x03\x04',),
    '.zip': (b'PK\x03\x04', b'PK\x05\x06'),
}
_ZIP_EXTENSIONS = {'.docx', '.xlsx', '.pptx', '.hwpx', '.zip'}


def _validate_attachment_bytes(data: bytes, target_ext: str) -> tuple[bool, str]:
    """Return whether a known attachment type has a plausible file payload."""
    signatures = _SIGNATURES_BY_EXTENSION.get(target_ext)
    if not signatures:
        return True, ''
    if not any(data.startswith(signature) for signature in signatures):
        return False, '파일 형식 시그니처가 확장자와 일치하지 않습니다'
    if target_ext in _ZIP_EXTENSIONS:
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                if archive.testzip() is not None:
                    return False, '압축 파일 구조가 올바르지 않습니다'
                names = archive.namelist()
                if target_ext in {'.docx', '.xlsx', '.pptx'} and '[Content_Types].xml' not in names:
                    return False, 'Office 압축 파일 구조가 올바르지 않습니다'
                if target_ext == '.hwpx' and not (
                    'mimetype' in names or 'Contents/content.hpf' in names
                ):
                    return False, 'HWPX 압축 파일 구조가 올바르지 않습니다'
        except (OSError, ValueError, zipfile.BadZipFile) as exc:
            return False, f'압축 파일을 열 수 없습니다 ({type(exc).__name__})'
    return True, ''


def _strip_cnu_download_error_wrapper(data: bytes, target_ext: str) -> tuple[bytes, bool, str]:
    """Remove CNU's stray PHP error output surrounding an otherwise valid file.

    Some CNU download responses start and end with ``Error can not open file!!``
    while still embedding the requested binary between them.  Saving that stream
    verbatim corrupts Office documents and images.  Only unwrap a response that
    unmistakably starts with this known server error and validates afterwards.
    """
    if not data.startswith(_CNU_DOWNLOAD_ERROR):
        return data, False, ''

    signatures = _SIGNATURES_BY_EXTENSION.get(target_ext)
    if not signatures:
        return data, False, 'CNU 오류 응답을 복구할 수 없는 확장자입니다'

    offsets = [data.find(signature, len(_CNU_DOWNLOAD_ERROR)) for signature in signatures]
    offsets = [offset for offset in offsets if offset >= 0]
    if not offsets:
        return data, False, '오류 응답 안에서 실제 파일 데이터를 찾지 못했습니다'

    payload = data[min(offsets):]
    suffix = payload.rfind(_CNU_DOWNLOAD_ERROR)
    if suffix > 0:
        payload = payload[:suffix]

    valid, reason = _validate_attachment_bytes(payload, target_ext)
    if not valid:
        return data, False, reason
    return payload, True, ''


def _download_file_safely(
    session: requests.Session,
    url: str,
    filepath: str,
    headers: dict,
    timeout: int = TIMEOUT,
    max_retries: int = 3,
) -> bool:
    """
    Download binary file safely:
    - stream to a temp `.part` file then atomic replace
    - detect and reject HTML/error pages saved as office/hwp/etc
    - verify Content-Length when available
    - retry on transient errors
    """
    binary_exts = {
        '.hwp', '.hwpx',
        '.doc', '.docx',
        '.xls', '.xlsx',
        '.ppt', '.pptx',
        '.pdf',
        '.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp',
    }
    target_ext = os.path.splitext(filepath)[1].lower()
    tmp_path = f"{filepath}.part"

    dl_headers = dict(headers or {})
    dl_headers.setdefault('Accept', '*/*')

    last_err: str = ''
    for attempt in range(1, max_retries + 1):
        try:
            with session.get(
                url,
                headers=dl_headers,
                timeout=(max(3, int(timeout / 3)), timeout),
                allow_redirects=True,
                stream=True,
                verify=urlparse(url).hostname not in SSL_VERIFY_DISABLED_HOSTS,
            ) as resp:
                if resp.status_code != 200:
                    last_err = f"status={resp.status_code}"
                    raise requests.RequestException(last_err)

                expected_len = None
                try:
                    cl = resp.headers.get('Content-Length')
                    if cl:
                        expected_len = int(cl)
                except Exception:
                    expected_len = None

                written = 0
                try:
                    resp.raw.decode_content = True
                except Exception:
                    pass

                with open(tmp_path, 'wb') as f:
                    for chunk in resp.iter_content(chunk_size=1024 * 128):
                        if not chunk:
                            continue
                        f.write(chunk)
                        written += len(chunk)

                if written <= 0:
                    last_err = "empty body"
                    raise requests.RequestException(last_err)

                if expected_len is not None and written < expected_len:
                    last_err = f"incomplete download {written}/{expected_len}"
                    raise requests.RequestException(last_err)

                # Reject HTML/error pages being saved as binary docs.
                content_type = (resp.headers.get('Content-Type') or '').lower()
                head_bytes = b''
                try:
                    with open(tmp_path, 'rb') as f:
                        head_bytes = f.read(1024)
                except Exception:
                    head_bytes = b''

                if (('text/html' in content_type) or _looks_like_html_bytes(head_bytes)) and (
                    target_ext in binary_exts or ('text/html' in content_type)
                ):
                    # try to surface original filename for debugging
                    cd = resp.headers.get('Content-Disposition') or ''
                    server_name = _parse_content_disposition_filename(cd)
                    try:
                        os.remove(tmp_path)
                    except Exception:
                        pass
                    print(f"[첨부 다운로드 거부] HTML 응답으로 판단: {url} (Content-Type={content_type}, filename={server_name or os.path.basename(filepath)})")
                    return False

                # CNU's attachment endpoint sometimes emits PHP error text before
                # and after the actual binary payload despite returning HTTP 200.
                # Unwrap only the known response shape, then validate the result.
                with open(tmp_path, 'rb') as f:
                    downloaded_bytes = f.read()
                cleaned_bytes, recovered, validation_error = _strip_cnu_download_error_wrapper(
                    downloaded_bytes, target_ext
                )
                if recovered:
                    with open(tmp_path, 'wb') as f:
                        f.write(cleaned_bytes)
                    print(f"[첨부 다운로드 복구] CNU 오류 출력 제거: {os.path.basename(filepath)}")
                elif downloaded_bytes.startswith(_CNU_DOWNLOAD_ERROR):
                    last_err = validation_error or 'CNU 오류 응답에서 파일을 복구하지 못했습니다'
                    raise requests.RequestException(last_err)

                valid, validation_error = _validate_attachment_bytes(cleaned_bytes, target_ext)
                if target_ext in binary_exts and not valid:
                    last_err = validation_error
                    raise requests.RequestException(last_err)

                try:
                    os.replace(tmp_path, filepath)
                finally:
                    if os.path.exists(tmp_path):
                        try:
                            os.remove(tmp_path)
                        except Exception:
                            pass
                return True
        except Exception as e:
            last_err = str(e) or last_err
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except Exception:
                pass
            if attempt < max_retries:
                time.sleep(1.0 * (2 ** (attempt - 1)))
                continue
            print(f"[첨부 다운로드 실패] {url} -> {filepath} ({last_err})")
            return False

def _sanitize_filename(name: str) -> str:
    if not name:
        return ''
    # 제거: 제어문자 및 파일명 금지 문자
    name = re.sub(r'[\\/:*?"<>|\r\n\t]+', '_', name)
    name = name.strip(' .')
    name = name or 'attachment'

    # Linux filesystems limit a single filename component to 255 bytes.  Korean
    # names can exceed that limit much earlier than their character count implies.
    # Keep a readable prefix and a stable digest so attachment paths remain safe
    # on the deployment server as well as macOS.
    max_bytes = 180
    if len(name.encode('utf-8')) <= max_bytes:
        return name
    stem, ext = os.path.splitext(name)
    suffix = f"_{hashlib.sha256(name.encode('utf-8')).hexdigest()[:12]}{ext}"
    budget = max(1, max_bytes - len(suffix.encode('utf-8')))
    prefix = stem.encode('utf-8')[:budget].decode('utf-8', 'ignore').rstrip(' ._')
    return f"{prefix or 'attachment'}{suffix}"

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


def _postprocess_worker_count(task_count: int) -> int:
    if task_count <= 1:
        return 1
    cpu_count = os.cpu_count() or 2
    return max(1, min(4, cpu_count, task_count))


def _run_parallel_tasks(tasks: list[tuple], worker, error_prefix: str) -> None:
    if not tasks:
        return
    max_workers = _postprocess_worker_count(len(tasks))
    if max_workers == 1:
        for task in tasks:
            try:
                worker(*task)
            except Exception as exc:
                print(f"[경고] {error_prefix}: {task[0]} ({exc})")
        return
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {executor.submit(worker, *task): task for task in tasks}
        for future in as_completed(future_map):
            task = future_map[future]
            try:
                future.result()
            except Exception as exc:
                print(f"[경고] {error_prefix}: {task[0]} ({exc})")

def _postprocess_attachments(post_dir: str, title: str) -> None:
    """Within post_dir, organize attachments:
    - Move all downloaded files into post_dir/original (create if not exists)
    - Extract archives into original
    - Create pdfs dir; move PDFs from original; convert HWP/HWPX files to PDFs
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

    hwp_tasks: list[tuple[str, str]] = []

    # Move PDFs and collect HWP/HWPX conversion tasks.
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
            elif lower.endswith(HWP_EXTENSIONS):
                safe_base = _sanitize_filename(os.path.splitext(fname)[0])
                out_pdf = unique_path(pdfs_dir, f"{safe_base}.pdf")
                hwp_tasks.append((src, out_pdf))

    if hwp_tasks and not find_rhwp():
        print(
            "[경고] HWP/HWPX 첨부가 있지만 rhwp CLI를 찾을 수 없습니다. "
            "RHWP_CLI·PATH·레포 내 cnu_info_codex/vendor/rhwp 빌드 등을 확인하세요."
        )
    else:
        def convert_hwp_task(src: str, out_pdf: str) -> None:
            convert_hwp_to_pdf_with_rhwp(src, out_pdf)

        _run_parallel_tasks(hwp_tasks, convert_hwp_task, "HWP PDF 변환 실패")

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
        pdf_tasks = [
            (os.path.join(pdfs_dir, name), pngs_dir)
            for name in os.listdir(pdfs_dir)
            if name.lower().endswith('.pdf')
        ]
        _run_parallel_tasks(pdf_tasks, pdf_to_pngs, "PDF PNG 변환 실패")
    except Exception:
        pass


def _resolve_postprocessed_download_paths(paths: list[str], post_dir: str) -> list[str]:
    """Keep DB links aligned after attachments are moved into original/pdfs."""
    resolved: list[str] = []
    for path in paths:
        filename = os.path.basename(path)
        candidates = (
            path,
            os.path.join(post_dir, 'original', filename),
            os.path.join(post_dir, 'pdfs', filename),
        )
        actual = next((candidate for candidate in candidates if os.path.isfile(candidate)), None)
        if actual:
            resolved.append(actual)
    return resolved

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


def _hannam_post_no(url: str | None) -> str:
    if not url:
        return ''
    try:
        parsed = urlparse(url)
        return (parse_qs(parsed.query).get('pPostNo', [''])[0] or '').strip()
    except Exception:
        return ''


def _filename_from_url_or_query(url: str, fallback: str) -> str:
    try:
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        for key in ('filename', 'strFileName', 'fileName'):
            value = (qs.get(key, [''])[0] or '').strip()
            if value:
                return unquote(value)
        basename = os.path.basename(parsed.path)
        if basename:
            return unquote(basename)
    except Exception:
        pass
    return fallback


def _list_hannam_notice_links(
    base_url: str,
    *,
    max_articles: int,
    session: requests.Session,
) -> list[dict]:
    soup = _get_soup(session, base_url)
    if soup is None:
        return []

    links: list[dict] = []
    seen: set[str] = set()
    for idx, a_tag in enumerate(soup.select('.hnuboard a[href*="pPostNo"], table a[href*="pPostNo"]')):
        if len(links) >= max_articles:
            break
        href = a_tag.get('href')
        title = _normalize_whitespace(a_tag.get_text(' ', strip=True))
        title = re.sub(r'\s+NEW$', '', title).strip()
        if not href or not title:
            continue
        full_link = urljoin(base_url, href)
        post_no = _hannam_post_no(full_link)
        if not post_no or post_no in seen:
            continue
        seen.add(post_no)

        date_text = ''
        row = a_tag.find_parent('tr')
        if row:
            cells = row.find_all('td')
            for cell in cells:
                label = cell.select_one('.add-th')
                if label and '작성일' in label.get_text(strip=True):
                    date_text = re.sub(r'^작성일', '', cell.get_text(' ', strip=True)).strip()
                    break
        links.append(
            {
                'id': f'hannam_{post_no}',
                'url': full_link,
                'title': title,
                'date': date_text,
                'index': idx,
            }
        )
    return links


def list_notice_links(
    base_url: str | None = None,
    *,
    max_articles: int = MAX_ARTICLES,
    session: requests.Session | None = None,
    parser: str | None = None,
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
        if (parser or '').lower() == 'hannam':
            return _list_hannam_notice_links(
                target_url,
                max_articles=max_articles,
                session=session,
            )

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
    parser: str | None = None,
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

        is_hannam = (parser or '').lower() == 'hannam'
        if is_hannam:
            content_node = detail_soup.select_one('.view-content')
            content = (
                _normalize_text_preserve_newlines(content_node.get_text('\n', strip=True))
                if content_node
                else ''
            )
            matched_strategy = 'css:.view-content(hannam)'
        else:
            content, matched_strategy = _extract_content_strict(detail_soup)

        title = (title_hint or '').strip()
        if not title:
            title_candidates = [
                detail_soup.select_one('.post-title'),
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
        if is_hannam:
            for node in detail_soup.select('.post-regi-info p'):
                label = node.select_one('span')
                if label and '작성일' in label.get_text(strip=True):
                    date_text = re.sub(r'^작성일', '', node.get_text(' ', strip=True)).strip()
                    break
        else:
            date_elem = (
                detail_soup.select_one('#txt > ul > li.date')
                or detail_soup.select_one('div.board_view > ul > li.date')
                or detail_soup.select_one('li.date')
            )
            if date_elem and date_elem.get_text():
                date_text = re.sub(r'^등록일', '', date_elem.get_text().strip()).strip()

        if not notice_id:
            notice_id = _build_notice_id(url, title or (title_hint or ''), fallback_index)

        raw_content = content
        ai_summary = None

        attachments: list[str] = []
        downloaded_files: list[str] = []
        post_dir: str | None = None
        if download_attachments and notice_id:
            post_dir = _derive_post_subdir(attachments_dir, notice_id)
            try:
                os.makedirs(post_dir, exist_ok=True)
            except Exception:
                pass

        if is_hannam:
            attach_nodes = detail_soup.select('.add-file-list a[href]')
            image_nodes = detail_soup.select('.view-content img[src]')
        else:
            attach_nodes = detail_soup.select('li.file a[href]')
            # CNU posts often put images directly in the visible editor body
            # instead of adding them as attachments.  Restrict the selector to
            # the content area so footer/accessibility images are not collected.
            content_node = detail_soup.select_one('div.board_viewDetail:not(.board_pdf_viewer)')
            image_nodes = content_node.select('img[src]') if content_node else []

        download_targets: list[tuple[str, str]] = []
        queued_download_urls: set[str] = set()
        for i, a_tag in enumerate(attach_nodes, start=1):
            href = a_tag.get('href')
            if not href:
                continue
            abs_url = urljoin(url, href)
            if abs_url not in attachments:
                attachments.append(abs_url)
            if download_attachments:
                title_attr = a_tag.get('title') or a_tag.get_text(' ', strip=True) or ''
                basename = os.path.basename(urlparse(abs_url).path) or ''
                suggested = title_attr or _filename_from_url_or_query(abs_url, basename or f'attachment_{i}')
                if abs_url not in queued_download_urls:
                    download_targets.append((abs_url, _sanitize_filename(suggested)))
                    queued_download_urls.add(abs_url)
        for i, img_tag in enumerate(image_nodes, start=1):
            src = img_tag.get('src')
            if not src:
                continue
            abs_url = urljoin(url, src)
            if abs_url not in attachments:
                attachments.append(abs_url)
            if download_attachments:
                label = img_tag.get('title') or img_tag.get('alt') or ''
                suggested = label or _filename_from_url_or_query(abs_url, f'image_{i}.png')
                if abs_url not in queued_download_urls:
                    download_targets.append((abs_url, _sanitize_filename(suggested)))
                    queued_download_urls.add(abs_url)

        if download_targets and post_dir:
            dl_headers = dict(HEADERS)
            dl_headers['Referer'] = url
            for idx, (file_url, name) in enumerate(download_targets, start=1):
                safe_title_prefix = _sanitize_filename((title or 'notice')[:50]) or 'notice'
                filename = f"{safe_title_prefix}_{idx}_{name}"
                filepath = os.path.join(post_dir, filename)
                ok = _download_file_safely(
                    session=session,
                    url=file_url,
                    filepath=filepath,
                    headers=dl_headers,
                    timeout=TIMEOUT,
                    max_retries=3,
                )
                if ok:
                    downloaded_files.append(filepath)

            try:
                _postprocess_attachments(post_dir, title)
            except Exception:
                pass
            downloaded_files = _resolve_postprocessed_download_paths(downloaded_files, post_dir)

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

        # content가 비어있으면 title을 사용
        final_title = title or (title_hint or '')
        final_content = content if content else final_title

        record = {
            'id': notice_id,
            'title': final_title,
            'date': date_text,
            'content': final_content,
            'raw_content': raw_content,
            'ai_summary': ai_summary,
            'ai_summary_model': FACTCHAT_MODEL if ai_summary else None,
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
    parser: str | None = None,
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
            parser=parser,
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
                parser=parser,
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
