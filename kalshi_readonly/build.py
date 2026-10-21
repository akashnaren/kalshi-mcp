from __future__ import annotations

import re
from pathlib import Path
from typing import Final

UNKNOWN: Final = "unknown"
_FILE_NAME: Final = "DEPLOYED_SHA"
_MAX_BYTES: Final = 128
_HEX: Final = re.compile(r"^[0-9a-fA-F]{12,64}$")
_INSTALL_ROOT: Final = Path(__file__).resolve().parents[1]


def parse_build_sha(raw: bytes | None) -> str:
    """First 12 characters of one hex token, or ``unknown``."""
    if raw is None or len(raw) > _MAX_BYTES:
        return UNKNOWN
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError:
        return UNKNOWN
    token = text.strip()
    if _HEX.fullmatch(token) is None:
        return UNKNOWN
    return token[:12]


def _read(path: Path) -> bytes | None:
    try:
        with path.open("rb") as handle:
            data = handle.read(_MAX_BYTES + 1)
    except OSError:
        return None
    if len(data) > _MAX_BYTES:
        return None
    return data


BUILD_SHA: Final[str] = parse_build_sha(_read(_INSTALL_ROOT / _FILE_NAME))
