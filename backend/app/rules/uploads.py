"""What may be uploaded: file types decided by content (never the name or the browser's
content type), size limits and the consent each kind of file needs."""

import enum
from typing import Final

from app.models.enums import ConsentType, FileKind

MAX_IMAGE_BYTES: Final = 12 * 1024 * 1024
MAX_PDF_BYTES: Final = 10 * 1024 * 1024
MAX_SIDE: Final = 2000  # the stored original's long side
THUMBNAIL_SIDE: Final = 400
SNIFF_BYTES: Final = 32  # enough of the start of a file to recognise it


class SniffedType(enum.StrEnum):
    JPEG = "jpeg"
    PNG = "png"
    WEBP = "webp"
    HEIC = "heic"
    PDF = "pdf"


# HEIF brands for still images in HEVC, as phones write them. AVIF (AV1) is not one.
_HEIC_BRANDS: Final = frozenset(
    {b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"mif1"}
)

_IMAGES: Final = frozenset(
    {SniffedType.JPEG, SniffedType.PNG, SniffedType.WEBP, SniffedType.HEIC}
)
_ALLOWED: Final = {
    FileKind.INBODY: _IMAGES | {SniffedType.PDF},  # scans often arrive as PDFs
    FileKind.FOOD: _IMAGES,
    FileKind.BODY_PHOTO: _IMAGES,
    FileKind.CHAT: _IMAGES,
}

REQUIRED_CONSENT: Final = {
    FileKind.INBODY: ConsentType.HEALTH_DATA,
    FileKind.BODY_PHOTO: ConsentType.BODY_PHOTOS,
    FileKind.FOOD: ConsentType.HEALTH_DATA,
    FileKind.CHAT: ConsentType.AI_PROCESSING,
}


def sniff_type(head: bytes) -> SniffedType | None:
    """Recognise a file from its first bytes (its "magic number")."""
    if head.startswith(b"\xff\xd8\xff"):
        return SniffedType.JPEG
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return SniffedType.PNG
    if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return SniffedType.WEBP
    if len(head) >= 12 and head[4:8] == b"ftyp" and head[8:12] in _HEIC_BRANDS:
        return SniffedType.HEIC
    if head.startswith(b"%PDF-"):
        return SniffedType.PDF
    return None


def is_allowed(kind: FileKind, sniffed: SniffedType) -> bool:
    return sniffed in _ALLOWED[kind]


def size_limit(sniffed: SniffedType) -> int:
    return MAX_PDF_BYTES if sniffed is SniffedType.PDF else MAX_IMAGE_BYTES


def upload_cap(kind: FileKind) -> int:
    """The most bytes worth reading before the type is known."""
    return max(size_limit(t) for t in _ALLOWED[kind])
