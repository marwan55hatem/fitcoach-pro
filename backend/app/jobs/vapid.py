"""Print a new web push (VAPID) key pair for backend/.env. Never commit the keys.

python -m app.jobs.vapid
"""

import base64

from cryptography.hazmat.primitives import serialization
from py_vapid import Vapid01


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def new_keys() -> tuple[str, str]:
    """(public, private) in the URL-safe base64 form browsers and pywebpush expect."""
    vapid = Vapid01()
    vapid.generate_keys()
    private = vapid.private_key.private_numbers().private_value.to_bytes(32, "big")
    public = vapid.public_key.public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    return _b64(public), _b64(private)


if __name__ == "__main__":
    public, private = new_keys()
    print(f"VAPID_PUBLIC_KEY={public}")
    print(f"VAPID_PRIVATE_KEY={private}")
