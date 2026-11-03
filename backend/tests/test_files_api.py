import base64
import datetime as dt
import hashlib
import io
import time
import zipfile
from collections.abc import Iterator
from urllib.parse import parse_qs, urlparse

import httpx
import pillow_heif
import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from PIL import ExifTags, Image
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.rate_limit import limiter
from app.models import MealLog, StoredFile
from app.models.enums import MealSlot, MealSource
from app.rules.uploads import MAX_IMAGE_BYTES, MAX_PDF_BYTES
from app.services import files as svc
from app.services.storage import Storage, user_prefix
from tests.helpers import Account, delete_me, grant_consents, register_and_login

# --- sample files, made here so no real photo is ever needed --------------------------


def photo(width: int = 3000, height: int = 2000, color: str = "red") -> Image.Image:
    img = Image.new("RGB", (width, height), color)
    img.paste("blue", (0, 0, width // 2, height // 2))  # a corner to check rotation
    return img


def jpeg_with_gps(width: int = 3000, height: int = 2000) -> bytes:
    """A phone-style JPEG: rotated by an EXIF tag, with camera and GPS metadata."""
    exif = Image.Exif()
    exif[ExifTags.Base.Orientation] = 6  # display rotated 90 degrees clockwise
    exif[ExifTags.Base.Make] = "TestCam"
    gps = exif.get_ifd(ExifTags.IFD.GPSInfo)
    gps[ExifTags.GPS.GPSLatitudeRef] = "N"
    gps[ExifTags.GPS.GPSLatitude] = (30.0, 2.0, 0.0)
    gps[ExifTags.GPS.GPSLongitudeRef] = "E"
    gps[ExifTags.GPS.GPSLongitude] = (31.0, 14.0, 0.0)
    out = io.BytesIO()
    photo(width, height).save(out, "JPEG", exif=exif)
    return out.getvalue()


def encoded(fmt: str, size: tuple[int, int] = (800, 600)) -> bytes:
    out = io.BytesIO()
    img = photo(*size)
    if fmt == "HEIC":
        pillow_heif.from_pillow(img).save(out, quality=60)
    else:
        img.save(out, fmt)
    return out.getvalue()


def pdf(*colors: str) -> bytes:
    pages = [Image.new("RGB", (600, 800), c) for c in colors]
    out = io.BytesIO()
    pages[0].save(out, "PDF", save_all=True, append_images=pages[1:])
    return out.getvalue()


# --- helpers ----------------------------------------------------------------------------


@pytest.fixture
def user(client: TestClient) -> Account:
    account = register_and_login(client)
    grant_consents(client, account, "health_data", "body_photos", "ai_processing")
    return account


def upload(
    client: TestClient, account: Account, kind: str, data: bytes | Iterator[bytes]
):
    return client.post(
        f"/files?kind={kind}",
        headers={**account.headers, "content-type": "application/octet-stream"},
        content=data,
    )


def uploaded(client: TestClient, account: Account, kind: str, data: bytes) -> dict:
    res = upload(client, account, kind, data)
    assert res.status_code == 201, res.text
    return res.json()


def row(db: Session, file_id: str) -> StoredFile:
    found = db.get(StoredFile, file_id)
    assert found is not None
    return found


def stored_image(storage: Storage, key: str) -> Image.Image:
    data = storage.get_bytes(key)
    assert data is not None
    return Image.open(io.BytesIO(data))


# --- uploads ----------------------------------------------------------------------------


def test_upload_strips_metadata_rotates_and_downsizes(
    client: TestClient, db: Session, storage: Storage, user: Account
) -> None:
    source = jpeg_with_gps()
    assert Image.open(io.BytesIO(source)).getexif().get_ifd(ExifTags.IFD.GPSInfo)

    body = uploaded(client, user, "food", source)
    assert body["kind"] == "food" and body["content_type"] == "image/jpeg"
    f = row(db, body["id"])
    assert f.storage_key.startswith(f"{user_prefix(user.id)}food/")
    assert f.thumbnail_key and f.thumbnail_key.startswith(
        f"{user_prefix(user.id)}food/"
    )

    original = stored_image(storage, f.storage_key)
    assert original.format == "JPEG"
    exif = original.getexif()
    assert len(exif) == 0 and not exif.get_ifd(
        ExifTags.IFD.GPSInfo
    )  # all metadata gone
    assert "exif" not in original.info and "icc_profile" not in original.info
    # Turned upright (3000x2000 shot sideways), then fitted inside 2000 px.
    assert original.size == (1333, 2000)
    assert original.getpixel((original.width - 10, 10))[2] > 200  # blue corner moved

    thumb = stored_image(storage, f.thumbnail_key)
    assert (
        thumb.format == "JPEG" and max(thumb.size) == 400 and len(thumb.getexif()) == 0
    )

    data = storage.get_bytes(f.storage_key)
    assert data is not None
    assert body["size_bytes"] == f.size_bytes == len(data)
    assert body["sha256"] == f.sha256 == hashlib.sha256(data).hexdigest()


def test_small_images_are_not_enlarged(
    client: TestClient, db: Session, storage: Storage, user: Account
) -> None:
    body = uploaded(client, user, "chat", encoded("PNG", (300, 200)))
    f = row(db, body["id"])
    assert stored_image(storage, f.storage_key).size == (300, 200)
    assert stored_image(storage, f.thumbnail_key or "").size == (300, 200)


@pytest.mark.parametrize("fmt", ["PNG", "WEBP", "HEIC"])
def test_png_webp_and_heic_are_accepted(
    client: TestClient, db: Session, storage: Storage, user: Account, fmt: str
) -> None:
    body = uploaded(client, user, "inbody", encoded(fmt))
    original = stored_image(storage, row(db, body["id"]).storage_key)
    assert original.format == "JPEG" and original.size == (800, 600)


def test_png_transparency_becomes_white(
    client: TestClient, db: Session, storage: Storage, user: Account
) -> None:
    out = io.BytesIO()
    Image.new("RGBA", (50, 50), (0, 0, 0, 0)).save(out, "PNG")
    body = uploaded(client, user, "chat", out.getvalue())
    pixel = stored_image(storage, row(db, body["id"]).storage_key).getpixel((25, 25))
    assert min(pixel) > 245


def test_inbody_pdf_keeps_the_first_page_as_an_image(
    client: TestClient, db: Session, storage: Storage, user: Account
) -> None:
    body = uploaded(client, user, "inbody", pdf("red", "blue"))
    original = stored_image(storage, row(db, body["id"]).storage_key)
    assert original.format == "JPEG" and max(original.size) == 2000
    r, _, b = original.getpixel((original.width // 2, original.height // 2))
    assert r > 200 and b < 60  # page 1 (red), not page 2


def test_pdf_is_only_for_inbody(client: TestClient, user: Account) -> None:
    for kind in ("food", "body_photo", "chat"):
        res = upload(client, user, kind, pdf("white"))
        assert res.status_code == 415, kind
        assert res.json()["detail"]["code"] == "unsupported_type"


def test_the_content_decides_not_the_name_or_header(
    client: TestClient, db: Session, user: Account
) -> None:
    text = b"I am a text file called holiday.jpg"
    res = client.post(
        "/files?kind=food",
        headers={**user.headers, "content-type": "image/jpeg"},
        content=text,
    )
    assert res.status_code == 415
    assert res.json()["detail"]["code"] == "unsupported_type"
    # Right magic bytes, broken body.
    res = upload(client, user, "food", b"\xff\xd8\xff\xe0" + b"\x00" * 200)
    assert res.status_code == 415
    assert res.json()["detail"]["code"] == "invalid_image"
    assert not db.scalars(sa.select(StoredFile)).first()


def test_oversize_is_refused(client: TestClient, db: Session, user: Account) -> None:
    too_big = b"\xff\xd8\xff\xe0" + b"\x00" * MAX_IMAGE_BYTES
    res = upload(client, user, "food", too_big)  # announced by Content-Length
    assert res.status_code == 413
    assert res.json()["detail"]["code"] == "too_large"

    def chunks() -> Iterator[bytes]:  # no Content-Length: counted while reading
        yield b"\xff\xd8\xff\xe0"
        for _ in range(MAX_IMAGE_BYTES // (1024 * 1024) + 1):
            yield b"\x00" * (1024 * 1024)

    res = upload(client, user, "food", chunks())
    assert res.status_code == 413

    big_pdf = b"%PDF-1.7\n" + b"\x00" * (MAX_PDF_BYTES + 10)
    assert upload(client, user, "inbody", big_pdf).status_code == 413
    assert not db.scalars(sa.select(StoredFile)).first()


def test_empty_and_unknown_kind(client: TestClient, user: Account) -> None:
    assert upload(client, user, "food", b"").status_code == 415
    assert upload(client, user, "selfie", encoded("PNG")).status_code == 422


def test_each_kind_needs_its_consent(client: TestClient) -> None:
    account = register_and_login(client)
    image = encoded("PNG")
    for kind in ("inbody", "food", "body_photo", "chat"):
        assert upload(client, account, kind, image).status_code == 403
    grant_consents(client, account, "health_data")
    assert upload(client, account, "inbody", image).status_code == 201
    assert upload(client, account, "food", image).status_code == 201
    res = upload(client, account, "body_photo", image)
    assert res.status_code == 403
    assert res.json()["detail"]["consent"] == "body_photos"
    assert upload(client, account, "chat", image).status_code == 403
    grant_consents(client, account, "body_photos", "ai_processing")
    assert upload(client, account, "body_photo", image).status_code == 201
    assert upload(client, account, "chat", image).status_code == 201


def test_uploads_are_rate_limited(
    client: TestClient, user: Account, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "rate_limit_upload", "2/minute")
    limiter.enabled = True
    limiter.reset()
    image = encoded("PNG", (40, 40))
    codes = [upload(client, user, "food", image).status_code for _ in range(3)]
    assert codes == [201, 201, 429]


def test_upload_needs_login(client: TestClient) -> None:
    res = client.post("/files?kind=food", content=encoded("PNG"))
    assert res.status_code == 401


# --- signed links -----------------------------------------------------------------------


def test_signed_links_for_original_and_thumbnail(
    client: TestClient, db: Session, storage: Storage, user: Account
) -> None:
    body = uploaded(client, user, "food", encoded("PNG"))
    res = client.get(f"/files/{body['id']}/url", headers=user.headers)
    assert res.status_code == 200
    links = res.json()
    assert links["expires_in"] == 300
    for name in ("url", "thumbnail_url"):
        query = parse_qs(urlparse(links[name]).query)
        assert query["X-Amz-Expires"] == ["300"]
    f = row(db, body["id"])
    assert httpx.get(links["url"]).content == storage.get_bytes(f.storage_key)
    assert httpx.get(links["thumbnail_url"]).content == storage.get_bytes(
        f.thumbnail_key or ""
    )
    # The bucket itself is private: the same object without a signature is refused.
    plain = links["url"].split("?")[0]
    assert httpx.get(plain).status_code == 403


def test_signed_links_expire(storage: Storage, client: TestClient, db, user) -> None:
    body = uploaded(client, user, "food", encoded("PNG"))
    url = storage.presign_get(row(db, body["id"]).storage_key, 1)
    time.sleep(2)
    assert httpx.get(url).status_code == 403


def test_other_users_files_are_404(client: TestClient, user: Account) -> None:
    body = uploaded(client, user, "food", encoded("PNG"))
    other = register_and_login(client)
    grant_consents(client, other, "health_data")
    assert (
        client.get(f"/files/{body['id']}/url", headers=other.headers).status_code == 404
    )
    assert (
        client.delete(f"/files/{body['id']}", headers=other.headers).status_code == 404
    )
    missing = "00000000-0000-0000-0000-000000000000"
    assert client.get(f"/files/{missing}/url", headers=user.headers).status_code == 404


# --- deletion ---------------------------------------------------------------------------


def test_delete_removes_both_objects_and_clears_references(
    client: TestClient, db: Session, storage: Storage, user: Account
) -> None:
    body = uploaded(client, user, "food", encoded("PNG"))
    f = row(db, body["id"])
    keys = [f.storage_key, f.thumbnail_key or ""]
    meal = MealLog(
        user_id=f.user_id,
        date=dt.date(2026, 1, 1),
        slot=MealSlot.LUNCH,
        source=MealSource.PHOTO,
        name="Koshari",
        items=[],
        file_id=f.id,
    )
    db.add(meal)
    db.commit()

    res = client.delete(f"/files/{body['id']}", headers=user.headers)
    assert res.status_code == 204
    assert not any(storage.exists(k) for k in keys)
    db.expire_all()
    assert db.get(StoredFile, body["id"]) is None
    assert db.get(MealLog, meal.id).file_id is None  # the meal stays, without its photo
    assert (
        client.get(f"/files/{body['id']}/url", headers=user.headers).status_code == 404
    )
    assert (
        client.delete(f"/files/{body['id']}", headers=user.headers).status_code == 404
    )


def test_a_failed_storage_delete_is_finished_later(
    client: TestClient,
    db: Session,
    storage: Storage,
    user: Account,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = uploaded(client, user, "food", encoded("PNG"))
    f = row(db, body["id"])
    keys = [f.storage_key, f.thumbnail_key or ""]

    def boom(self: Storage, key: str) -> None:
        raise RuntimeError("storage down")

    monkeypatch.setattr(Storage, "delete", boom)
    failing = TestClient(client.app, raise_server_exceptions=False)
    assert (
        failing.delete(f"/files/{body['id']}", headers=user.headers).status_code == 500
    )
    monkeypatch.undo()

    # Already hidden from the user, waiting to be purged.
    db.expire_all()
    assert row(db, body["id"]).deleted_at is not None
    assert (
        client.get(f"/files/{body['id']}/url", headers=user.headers).status_code == 404
    )
    assert svc.purge_deleted(db, storage) == 1
    assert not any(storage.exists(k) for k in keys)
    assert db.get(StoredFile, body["id"]) is None


def test_export_and_account_deletion_cover_uploads(
    client: TestClient, db: Session, storage: Storage, user: Account
) -> None:
    body = uploaded(client, user, "body_photo", jpeg_with_gps(600, 400))
    f = row(db, body["id"])
    keys = [f.storage_key, f.thumbnail_key or ""]

    archive = zipfile.ZipFile(
        io.BytesIO(client.get("/me/export", headers=user.headers).content)
    )
    names = archive.namelist()
    assert f"files/{body['id']}.jpg" in names
    assert len([n for n in names if n.startswith("files/")]) == 1  # no thumbnails
    assert archive.read(f"files/{body['id']}.jpg") == storage.get_bytes(f.storage_key)

    assert delete_me(client, user).status_code == 204
    assert not any(storage.exists(k) for k in keys)
    assert storage.delete_prefix(user_prefix(user.id)) == 0  # nothing left behind


# --- images for the AI ------------------------------------------------------------------


def test_load_for_ai_gives_a_small_jpeg(
    client: TestClient, db: Session, storage: Storage, user: Account
) -> None:
    body = uploaded(client, user, "food", jpeg_with_gps())
    owner = row(db, body["id"]).user_id
    image = svc.load_for_ai(db, storage, owner, body["id"])
    assert image.media_type == "image/jpeg"
    decoded = Image.open(io.BytesIO(base64.b64decode(image.data_b64)))
    assert max(decoded.size) == 1600 and len(decoded.getexif()) == 0


def test_body_photos_never_go_to_the_ai(
    client: TestClient, db: Session, storage: Storage, user: Account
) -> None:
    body = uploaded(client, user, "body_photo", encoded("PNG"))
    owner = row(db, body["id"]).user_id
    with pytest.raises(svc.NotForAiError):
        svc.load_for_ai(db, storage, owner, body["id"])


def test_load_for_ai_is_owner_only(
    client: TestClient, db: Session, storage: Storage, user: Account
) -> None:
    body = uploaded(client, user, "food", encoded("PNG"))
    other = register_and_login(client)
    with pytest.raises(svc.StoredFileNotFoundError):
        svc.load_for_ai(db, storage, other.id, body["id"])
