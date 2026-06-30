"""Upload file validation: size limits, extensions, and basic content checks."""

from __future__ import annotations

import os
import zipfile
from pathlib import Path

SUPPORTED_EXTENSIONS = {".csv", ".xlsx", ".xls", ".kml", ".kmz", ".zip"}

# Magic-byte signatures for common upload types (prefix match).
_SIGNATURES: dict[str, tuple[bytes, ...]] = {
    ".kmz": (b"PK\x03\x04",),
    ".xlsx": (b"PK\x03\x04",),
    ".xls": (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",),
    ".zip": (b"PK\x03\x04",),
    ".kml": (b"<?xml", b"<kml", b"<KML"),
    ".csv": (),  # text; validated by extension only
}


def max_upload_bytes() -> int:
    raw = os.environ.get("FTTH_MAX_UPLOAD_BYTES", "104857600").strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return 104_857_600  # 100 MiB


def max_zip_entries() -> int:
    raw = os.environ.get("FTTH_MAX_ZIP_ENTRIES", "500").strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return 500


def validate_extension(path: Path) -> str | None:
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        return f"{path.name}: unsupported file type"
    return None


def validate_file_size(path: Path, limit: int | None = None) -> str | None:
    limit = limit or max_upload_bytes()
    size = path.stat().st_size
    if size > limit:
        mb = limit / (1024 * 1024)
        return f"{path.name}: exceeds maximum upload size ({mb:.0f} MiB)"
    if size == 0:
        return f"{path.name}: empty file"
    return None


def validate_content_signature(path: Path) -> str | None:
    suffix = path.suffix.lower()
    expected = _SIGNATURES.get(suffix)
    if expected is not None and not expected:
        return None
    if expected is None:
        return None
    with open(path, "rb") as fh:
        head = fh.read(64)
    if suffix == ".csv":
        return None
    if suffix in {".kml"}:
        text_head = head.lstrip()
        if any(text_head.startswith(sig) for sig in expected):
            return None
        return f"{path.name}: content does not match KML format"
    if not any(head.startswith(sig) for sig in expected):
        return f"{path.name}: content does not match expected {suffix} format"
    return None


def validate_zip_archive(path: Path) -> str | None:
    if path.suffix.lower() != ".zip":
        return None
    try:
        with zipfile.ZipFile(path, "r") as zf:
            if len(zf.namelist()) > max_zip_entries():
                return f"{path.name}: ZIP contains too many entries (max {max_zip_entries()})"
            total_uncompressed = sum(info.file_size for info in zf.infolist())
            if total_uncompressed > max_upload_bytes() * 5:
                return f"{path.name}: ZIP uncompressed size exceeds safe limit"
    except zipfile.BadZipFile:
        return f"{path.name}: invalid ZIP archive"
    except OSError as exc:
        return f"{path.name}: could not read ZIP ({exc.__class__.__name__})"
    return None


def validate_uploaded_file(path: Path) -> str | None:
    for check in (validate_extension, validate_file_size, validate_content_signature, validate_zip_archive):
        err = check(path)
        if err:
            return err
    return None
