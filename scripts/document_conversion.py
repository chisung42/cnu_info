from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

HWP_EXTENSIONS = (".hwp", ".hwpx")
HWP_CFB_MAGIC = bytes.fromhex("d0cf11e0a1b11ae1")
ZIP_MAGIC = b"PK\x03\x04"
_WARNED_INVALID_RHWP_CLI: set[str] = set()


def _bundled_rhwp_paths(repo_root: Path) -> list[Path]:
    """레포에 포함된 빌드 산출물(있을 때만)."""
    return [
        repo_root / "vendor" / "rhwp" / "target" / "release" / "rhwp",
        repo_root
        / "cnu_info_codex"
        / "vendor"
        / "rhwp"
        / "target"
        / "release"
        / "rhwp",
    ]


def _rhwp_candidates() -> list[str]:
    configured = os.environ.get("RHWP_CLI", "").strip()
    candidates = []
    if configured and Path(configured).exists():
        candidates.append(configured)
    elif configured and configured not in _WARNED_INVALID_RHWP_CLI:
        _WARNED_INVALID_RHWP_CLI.add(configured)
        print(f"[경고] RHWP_CLI 경로가 존재하지 않아 무시합니다: {configured}")
    path_candidate = shutil.which("rhwp")
    if path_candidate:
        candidates.append(path_candidate)
    repo_root = Path(__file__).resolve().parent.parent
    for bundled in _bundled_rhwp_paths(repo_root):
        if bundled.exists():
            candidates.append(str(bundled))
    for fallback in (
        Path.home() / ".cargo" / "bin" / "rhwp",
        Path("/opt/homebrew/bin/rhwp"),
        Path("/usr/local/bin/rhwp"),
    ):
        if fallback.exists():
            candidates.append(str(fallback))
    seen: set[str] = set()
    result: list[str] = []
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        result.append(candidate)
    return result


def has_rhwp_cli() -> bool:
    return bool(_rhwp_candidates())


def _is_valid_hwp_payload(path: str) -> tuple[bool, str]:
    try:
        with open(path, "rb") as fh:
            head = fh.read(16)
    except Exception as exc:
        return False, f"파일을 읽을 수 없습니다: {exc}"

    lower = path.lower()
    if lower.endswith(".hwp"):
        if head.startswith(HWP_CFB_MAGIC):
            return True, ""
        preview = head.decode("utf-8", errors="replace").strip()
        return False, f"유효한 HWP(CFB) 파일이 아닙니다: {preview or head.hex()}"
    if lower.endswith(".hwpx"):
        if head.startswith(ZIP_MAGIC):
            return True, ""
        preview = head.decode("utf-8", errors="replace").strip()
        return False, f"유효한 HWPX(ZIP) 파일이 아닙니다: {preview or head.hex()}"
    return False, "지원하지 않는 확장자입니다."


def _run_converter(cmd: list[str], *, timeout: int = 300) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            text=True,
        )
    except FileNotFoundError:
        return False, f"command not found: {cmd[0]}"
    except Exception as exc:
        return False, str(exc)
    output = "\n".join(part for part in (result.stdout, result.stderr) if part)
    return result.returncode == 0, output.strip()


def convert_hwp_to_pdf(hwp_path: str, out_pdf_path: str) -> bool:
    """
    Convert HWP/HWPX to PDF.

    Preferred path is the open-source rhwp CLI:
        rhwp export-pdf input.hwp -o output.pdf

    rhwp is the only conversion backend. Set RHWP_CLI when the binary is not
    available on PATH.
    """
    if not hwp_path or not out_pdf_path:
        return False
    if not hwp_path.lower().endswith(HWP_EXTENSIONS):
        return False

    valid, reason = _is_valid_hwp_payload(hwp_path)
    if not valid:
        print(f"[경고] HWP 변환 건너뜀: {hwp_path} ({reason})")
        return False

    os.makedirs(os.path.dirname(out_pdf_path), exist_ok=True)

    candidates = _rhwp_candidates()
    if not candidates:
        print(
            "[경고] rhwp CLI를 찾을 수 없습니다. RHWP_CLI·PATH·"
            "레포 내 vendor/rhwp 또는 cnu_info_codex/vendor/rhwp 빌드를 확인하세요."
        )
        return False

    errors: list[str] = []
    for rhwp in candidates:
        cmd = [rhwp, "export-pdf", hwp_path, "-o", out_pdf_path]
        ok, message = _run_converter(cmd, timeout=300)
        if ok and os.path.exists(out_pdf_path):
            return True
        errors.append(f"{rhwp}: {message or 'PDF output was not created'}")

    for error in errors:
        print(f"[경고] rhwp 변환 실패: {error}")

    return False
