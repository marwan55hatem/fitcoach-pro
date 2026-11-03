from starlette.requests import Request

from app.core.config import Settings
from app.core.rate_limit import client_ip


def request(peer: str, forwarded: str | None = None) -> Request:
    headers = [(b"x-forwarded-for", forwarded.encode())] if forwarded else []
    return Request({"type": "http", "client": (peer, 1234), "headers": headers})


TRUSTING = Settings(_env_file=None, trusted_proxy_ips=["127.0.0.1"])


def test_direct_clients_are_keyed_by_their_own_address() -> None:
    assert client_ip(request("203.0.113.7"), TRUSTING) == "203.0.113.7"


def test_forwarded_address_is_used_only_from_a_trusted_proxy() -> None:
    via_bff = request("127.0.0.1", "198.51.100.4")
    assert client_ip(via_bff, TRUSTING) == "198.51.100.4"
    spoofed = request("203.0.113.7", "198.51.100.4")
    assert client_ip(spoofed, TRUSTING) == "203.0.113.7"


def test_the_last_untrusted_hop_wins_over_a_client_supplied_chain() -> None:
    # The client sent a fake first entry; the BFF appended the real address.
    chain = request("127.0.0.1", "10.9.9.9, 198.51.100.4")
    assert client_ip(chain, TRUSTING) == "198.51.100.4"


def test_trusted_proxy_without_forwarding_header_uses_the_peer() -> None:
    assert client_ip(request("127.0.0.1"), TRUSTING) == "127.0.0.1"


def test_no_trusted_proxies_means_never_trust_the_header() -> None:
    none = Settings(_env_file=None, trusted_proxy_ips=[])
    assert client_ip(request("127.0.0.1", "198.51.100.4"), none) == "127.0.0.1"
