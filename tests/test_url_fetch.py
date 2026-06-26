import pytest

from app.utils.url_fetch import _is_blocked_ip, extract_text, is_safe_url


@pytest.mark.parametrize("ip,blocked", [
    ("8.8.8.8", False),
    ("93.184.216.34", False),
    ("127.0.0.1", True),       # loopback
    ("10.0.0.1", True),        # private
    ("172.16.5.4", True),      # private
    ("192.168.1.1", True),     # private
    ("169.254.169.254", True), # link-local (cloud metadata)
    ("::1", True),             # ipv6 loopback
    ("fc00::1", True),         # ipv6 unique-local
    ("not-an-ip", True),       # unparseable -> unsafe
])
def test_is_blocked_ip(ip, blocked):
    assert _is_blocked_ip(ip) is blocked


def test_rejects_non_https():
    ok, err = is_safe_url("http://example.com")
    assert not ok
    assert "https" in err


def test_rejects_loopback_without_network():
    # 127.0.0.1 resolves offline; no DNS needed.
    ok, _ = is_safe_url("https://127.0.0.1/path")
    assert not ok


def test_rejects_cloud_metadata_ip():
    ok, _ = is_safe_url("https://169.254.169.254/latest/meta-data/")
    assert not ok


def test_allows_public_host_with_injected_resolver():
    fake_resolver = lambda host, port, **kw: [(2, 1, 6, "", ("93.184.216.34", port))]
    ok, err = is_safe_url("https://example.com", resolver=fake_resolver)
    assert ok and err is None


def test_blocks_public_host_that_resolves_internally():
    # DNS-rebinding style: public name resolving to a private address.
    fake_resolver = lambda host, port, **kw: [(2, 1, 6, "", ("10.1.2.3", port))]
    ok, _ = is_safe_url("https://sneaky.example.com", resolver=fake_resolver)
    assert not ok


def test_extract_text_strips_scripts_and_keeps_title_and_paragraphs():
    html = """
        <html><head><title>Big News</title></head>
        <body><p>We launched today.</p><script>evil()</script><p>Thanks all.</p></body></html>
    """
    text = extract_text(html)
    assert text.startswith("Big News")
    assert "We launched today." in text
    assert "Thanks all." in text
    assert "evil" not in text
