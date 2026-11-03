import pytest

from app.models.enums import ConsentType, FileKind
from app.rules.uploads import (
    MAX_IMAGE_BYTES,
    MAX_PDF_BYTES,
    REQUIRED_CONSENT,
    SniffedType,
    is_allowed,
    size_limit,
    sniff_type,
    upload_cap,
)

JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00"
PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 "
PDF = b"%PDF-1.7\n%\xe2\xe3"


def heif(brand: bytes) -> bytes:
    return b"\x00\x00\x00\x18ftyp" + brand + b"\x00\x00\x00\x00mif1heic"


@pytest.mark.parametrize(
    ("head", "expected"),
    [
        (JPEG, SniffedType.JPEG),
        (PNG, SniffedType.PNG),
        (WEBP, SniffedType.WEBP),
        (PDF, SniffedType.PDF),
        (heif(b"heic"), SniffedType.HEIC),
        (heif(b"heix"), SniffedType.HEIC),
        (heif(b"hevc"), SniffedType.HEIC),
        (heif(b"mif1"), SniffedType.HEIC),
    ],
)
def test_sniff_by_content(head: bytes, expected: SniffedType) -> None:
    assert sniff_type(head) is expected


@pytest.mark.parametrize(
    "head",
    [
        b"",
        b"hello, I am text pretending to be a .jpg",
        b"<svg xmlns='http://www.w3.org/2000/svg'/>",
        b"GIF89a\x01\x00",
        b"RIFF\x24\x00\x00\x00WAVEfmt ",  # RIFF, but audio
        heif(b"avif"),  # an ftyp box that is not HEIC
        b"\x00\x00\x00\x18ftypisom",  # an MP4 video
        b"\xff\xd8",  # too short to be sure
        b"PK\x03\x04",  # a ZIP (or .docx)
    ],
)
def test_anything_else_is_rejected(head: bytes) -> None:
    assert sniff_type(head) is None


def test_pdf_only_for_inbody() -> None:
    assert is_allowed(FileKind.INBODY, SniffedType.PDF)
    for kind in (FileKind.FOOD, FileKind.BODY_PHOTO, FileKind.CHAT):
        assert not is_allowed(kind, SniffedType.PDF)
    for kind in FileKind:
        for image in (SniffedType.JPEG, SniffedType.PNG, SniffedType.WEBP):
            assert is_allowed(kind, image)
        assert is_allowed(kind, SniffedType.HEIC)


def test_size_limits() -> None:
    assert MAX_IMAGE_BYTES == 12 * 1024 * 1024
    assert MAX_PDF_BYTES == 10 * 1024 * 1024
    assert size_limit(SniffedType.JPEG) == MAX_IMAGE_BYTES
    assert size_limit(SniffedType.HEIC) == MAX_IMAGE_BYTES
    assert size_limit(SniffedType.PDF) == MAX_PDF_BYTES
    # While streaming the type is not known yet: stop at the largest allowed size.
    for kind in FileKind:
        assert upload_cap(kind) == MAX_IMAGE_BYTES


def test_each_kind_needs_its_consent() -> None:
    assert REQUIRED_CONSENT == {
        FileKind.INBODY: ConsentType.HEALTH_DATA,
        FileKind.BODY_PHOTO: ConsentType.BODY_PHOTOS,
        FileKind.FOOD: ConsentType.HEALTH_DATA,
        FileKind.CHAT: ConsentType.AI_PROCESSING,
    }
