from unittest.mock import patch

import pytest

from core.security import UrlRejected, validate_source_url

ALLOW = ["http", "https"]
DENY = ["localhost", "metadata"]


def test_file_scheme_rejected():
    with pytest.raises(UrlRejected) as exc:
        validate_source_url("file:///etc/passwd", ALLOW, DENY)
    assert exc.value.reason == "scheme"


def test_javascript_scheme_rejected():
    with pytest.raises(UrlRejected) as exc:
        validate_source_url("javascript:alert(1)", ALLOW, DENY)
    assert exc.value.reason == "scheme"


def test_localhost_rejected_by_name():
    with pytest.raises(UrlRejected) as exc:
        validate_source_url("http://localhost/a.mp4", ALLOW, DENY)
    assert exc.value.reason == "hostname"


def test_local_tld_rejected():
    with pytest.raises(UrlRejected) as exc:
        validate_source_url("http://foo.local/a.mp4", ALLOW, DENY)
    assert exc.value.reason == "hostname"


def test_empty_hostname_rejected():
    with pytest.raises(UrlRejected) as exc:
        validate_source_url("http:///a.mp4", ALLOW, DENY)
    assert exc.value.reason == "hostname"


def test_private_ip_rejected():
    with patch(
        "core.security.socket.getaddrinfo",
        return_value=[(0, 0, 0, "", ("10.0.0.1", 0))],
    ):
        with pytest.raises(UrlRejected) as exc:
            validate_source_url("http://internal.example.com/a.mp4", ALLOW, DENY)
        assert exc.value.reason == "private_ip"


def test_loopback_ip_rejected():
    with patch(
        "core.security.socket.getaddrinfo",
        return_value=[(0, 0, 0, "", ("127.0.0.1", 0))],
    ):
        with pytest.raises(UrlRejected) as exc:
            validate_source_url("http://example.test/a.mp4", ALLOW, DENY)
        assert exc.value.reason == "private_ip"


def test_public_ip_accepted():
    with patch(
        "core.security.socket.getaddrinfo",
        return_value=[(0, 0, 0, "", ("93.184.216.34", 0))],
    ):
        validate_source_url("https://example.com/a.mp4", ALLOW, DENY)  # no raise


def test_public_ip_literal_accepted_without_dns():
    # IP literal path should not hit DNS at all.
    with patch(
        "core.security.socket.getaddrinfo",
        side_effect=AssertionError("DNS should not be called for IP literals"),
    ):
        validate_source_url("http://93.184.216.34/a.mp4", ALLOW, DENY)


def test_private_ip_literal_rejected_by_default():
    with pytest.raises(UrlRejected) as exc:
        validate_source_url("http://192.168.1.10/a.mp4", ALLOW, DENY)
    assert exc.value.reason == "private_ip"


def test_private_ip_literal_allowed_when_flag_set():
    # With allow_private_ips=True, 192.168/16 passes.
    validate_source_url(
        "http://192.168.1.10/a.mp4", ALLOW, DENY, allow_private_ips=True
    )


def test_loopback_ip_literal_still_rejected_when_private_allowed():
    # Even with the flag, 127/8 stays blocked.
    with pytest.raises(UrlRejected) as exc:
        validate_source_url(
            "http://127.0.0.1/a.mp4", ALLOW, DENY, allow_private_ips=True
        )
    assert exc.value.reason == "private_ip"


def test_private_hostname_allowed_when_flag_set():
    with patch(
        "core.security.socket.getaddrinfo",
        return_value=[(0, 0, 0, "", ("10.0.0.50", 0))],
    ):
        validate_source_url(
            "http://internal.lan/a.mp4", ALLOW, DENY, allow_private_ips=True
        )


def test_ipv6_loopback_literal_rejected():
    with pytest.raises(UrlRejected) as exc:
        validate_source_url("http://[::1]/a.mp4", ALLOW, DENY, allow_private_ips=True)
    assert exc.value.reason == "private_ip"
