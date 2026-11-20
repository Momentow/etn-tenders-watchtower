import pytest

from watchtower.core.urls import normalize_url, portal_id_for


@pytest.mark.parametrize(
    "a, b",
    [
        ("https://Example.COM/tenders", "https://example.com/tenders"),
        ("https://example.com/tenders/", "https://example.com/tenders"),
        ("https://example.com:443/tenders", "https://example.com/tenders"),
        ("http://example.com:80/", "http://example.com"),
        ("example.com/tenders", "https://example.com/tenders"),
        ("  https://example.com/tenders#top  ", "https://example.com/tenders"),
        ("https://example.com/t?b=2&a=1", "https://example.com/t?a=1&b=2"),
        ("https://example.com./t", "https://example.com/t"),
    ],
)
def test_same_address_same_id(a, b):
    assert normalize_url(a) == normalize_url(b)
    assert portal_id_for(a) == portal_id_for(b)


@pytest.mark.parametrize(
    "a, b",
    [
        ("https://example.com/Tenders", "https://example.com/tenders"),  # path case is significant
        ("http://example.com/t", "https://example.com/t"),
        ("https://example.com:8443/t", "https://example.com/t"),
        ("https://example.com/t?a=1", "https://example.com/t?a=2"),
        ("https://www.example.com/t", "https://example.com/t"),
    ],
)
def test_different_addresses_differ(a, b):
    assert portal_id_for(a) != portal_id_for(b)


def test_normalized_form():
    assert normalize_url("HTTPS://Example.com:443/a/b/?x=1#f") == "https://example.com/a/b?x=1"


@pytest.mark.parametrize("bad", ["", "   ", "ftp://example.com/x", "https:///nohost"])
def test_rejects_invalid(bad):
    with pytest.raises(ValueError):
        normalize_url(bad)
