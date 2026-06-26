# app/utils/url_fetch.py
# SSRF-sandboxed URL fetching for Content Inbox submissions (§11.1):
#   - https scheme only
#   - reject hosts that resolve to private / loopback / link-local / reserved IPs
#   - re-validate on every redirect hop
#   - strict timeouts and a capped response body
# Returns extracted readable text; the generation pipeline summarizes it later.

import ipaddress
import socket
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

_USER_AGENT = "GhostProBot/1.0 (+https://ghostpro.app)"
_TIMEOUT = (3, 5)          # (connect, read) seconds
_MAX_BYTES = 2_000_000     # cap downloaded body at ~2 MB
_MAX_TEXT = 5000           # store at most this many chars of extracted text
_MAX_REDIRECTS = 3


def _is_blocked_ip(ip_str):
    """True if an IP literal is in a range we must never fetch from."""
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True  # unparseable -> treat as unsafe
    return (
        ip.is_private or ip.is_loopback or ip.is_link_local
        or ip.is_reserved or ip.is_multicast or ip.is_unspecified
    )


def is_safe_url(url, resolver=socket.getaddrinfo):
    """Validate scheme and that every resolved address is publicly routable.

    `resolver` is injectable for testing. Returns (ok: bool, error: str|None).
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return False, "Invalid URL"

    if parsed.scheme != "https":
        return False, "Only https URLs are allowed"
    host = parsed.hostname
    if not host:
        return False, "Invalid URL"

    try:
        infos = resolver(host, parsed.port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return False, "Could not resolve host"

    addresses = {info[4][0] for info in infos}
    if not addresses:
        return False, "Could not resolve host"
    for addr in addresses:
        if _is_blocked_ip(addr):
            return False, "URL resolves to a disallowed address"
    return True, None


def _read_capped(response):
    total = 0
    chunks = []
    for chunk in response.iter_content(8192):
        chunks.append(chunk)
        total += len(chunk)
        if total >= _MAX_BYTES:
            break
    return b"".join(chunks)


def extract_text(html, encoding=None):
    """Pull a readable title + paragraph text out of an HTML document."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    title = soup.title.get_text(strip=True) if soup.title else ""
    paragraphs = [p.get_text(" ", strip=True) for p in soup.find_all("p")]
    body = " ".join(p for p in paragraphs if p)
    text = (f"{title}\n\n{body}" if title else body).strip()
    return text[:_MAX_TEXT]


def fetch_url_text(url):
    """SSRF-guarded fetch + text extraction. Returns (text, error)."""
    current = url
    for _ in range(_MAX_REDIRECTS + 1):
        ok, error = is_safe_url(current)
        if not ok:
            return None, error
        try:
            resp = requests.get(
                current, timeout=_TIMEOUT, allow_redirects=False, stream=True,
                headers={"User-Agent": _USER_AGENT},
            )
        except requests.RequestException:
            return None, "Could not fetch URL"

        try:
            if resp.status_code in (301, 302, 303, 307, 308):
                location = resp.headers.get("Location")
                if not location:
                    return None, "Redirect without a destination"
                current = urljoin(current, location)
                continue
            if resp.status_code != 200:
                return None, f"Fetch failed (HTTP {resp.status_code})"
            content_type = resp.headers.get("Content-Type", "")
            if "html" not in content_type and "text" not in content_type:
                return None, "URL did not return readable text"
            raw = _read_capped(resp)
        finally:
            resp.close()

        text = extract_text(raw, resp.encoding)
        if not text:
            return None, "No readable content found at URL"
        return text, None

    return None, "Too many redirects"
