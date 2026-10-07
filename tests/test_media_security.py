"""The URL-fetch path: SSRF, DNS rebinding, and hostile response bodies.

Nothing here touches the network. `getaddrinfo` is scripted, so a test can play a
hostile DNS server that answers one way the first time and another way after, and
httpx gets a MockTransport, so a test can see exactly which address each request was
really sent to.
"""

import io
import socket

import httpx
import pytest
from PIL import Image

from game import media

PUBLIC = "93.184.216.34"
PUBLIC_2 = "93.184.216.35"
METADATA = "169.254.169.254"


def png_bytes():
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (200, 160, 40)).save(buf, "PNG")
    return buf.getvalue()


class DNS:
    """A scripted resolver. `answers[name]` is consumed one lookup at a time and the
    last entry repeats - which is how a TTL-0 rebinding server behaves."""

    def __init__(self, answers):
        self.answers = {k: list(v) for k, v in answers.items()}
        self.lookups = []

    def __call__(self, host, port, *args, **kwargs):
        self.lookups.append(host)
        queue = self.answers.get(host)
        if not queue:
            raise socket.gaierror("no such host")
        ip = queue.pop(0) if len(queue) > 1 else queue[0]
        if ":" in ip:
            return [(socket.AF_INET6, socket.SOCK_STREAM, 6, "", (ip, port, 0, 0))]
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]


class Net:
    """Scripts both halves of the network: what DNS says, and what the far end does."""

    def __init__(self, monkeypatch):
        self.monkeypatch = monkeypatch
        self.seen = []                     # every request that reached the "network"
        self.dns = None

    def resolve(self, answers):
        self.dns = DNS(answers)
        self.monkeypatch.setattr(media.socket, "getaddrinfo", self.dns)
        return self.dns

    def serve(self, handler):
        def record(request):
            self.seen.append(request)
            return handler(request)

        transport = httpx.MockTransport(record)
        real = httpx.Client
        self.monkeypatch.setattr(
            media.httpx, "Client", lambda *a, **kw: real(*a, transport=transport, **kw))


@pytest.fixture
def net(monkeypatch):
    return Net(monkeypatch)


def image_response(request):
    return httpx.Response(200, headers={"content-type": "image/png"}, content=png_bytes())


# --------------------------------------------------------------------------- #
# DNS rebinding
# --------------------------------------------------------------------------- #

def test_fetch_connects_to_the_address_it_validated(net):
    """The attack: a public answer for the check, the metadata endpoint for the fetch.

    Handing the *name* to the HTTP client would let its own lookup get the second
    answer. Pinned, there is no second lookup, and the request goes to the address
    that was actually checked.
    """
    dns = net.resolve({"img.example": [PUBLIC, METADATA]})
    net.serve(image_response)

    assert media.fetch("http://img.example/cat.png")

    assert dns.lookups == ["img.example"]                     # resolved exactly once
    sent = net.seen[0]
    assert sent.url.host == PUBLIC                            # to the validated address
    assert sent.headers["host"] == "img.example"              # still asking for the site
    assert sent.extensions["sni_hostname"] == "img.example"   # and verifying its cert


def test_https_keeps_the_real_name_for_tls(net):
    net.resolve({"img.example": [PUBLIC]})
    net.serve(image_response)
    media.fetch("https://img.example:8443/cat.png")
    sent = net.seen[0]
    assert (sent.url.host, sent.url.port) == (PUBLIC, 8443)
    assert sent.headers["host"] == "img.example:8443"
    assert sent.extensions["sni_hostname"] == "img.example"


@pytest.mark.parametrize("address", [
    "127.0.0.1", "10.0.0.5", "192.168.1.1", METADATA, "::1", "fd00::1",
    "::ffff:127.0.0.1",                # a private v4 address wearing a v6 coat
])
def test_private_addresses_are_refused_before_any_request(net, address):
    net.resolve({"img.example": [address]})
    net.serve(image_response)
    with pytest.raises(media.MediaError, match="isn't allowed"):
        media.fetch("http://img.example/cat.png")
    assert net.seen == []


def test_a_name_with_any_private_address_is_refused(net, monkeypatch):
    """Fail closed, rather than quietly using whichever address looked acceptable."""
    def both(host, port, *a, **kw):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (PUBLIC, port)),
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", port))]

    net.serve(image_response)
    monkeypatch.setattr(media.socket, "getaddrinfo", both)
    with pytest.raises(media.MediaError):
        media.fetch("http://img.example/cat.png")
    assert net.seen == []


@pytest.mark.parametrize("port", [22, 6379, 5432, 11211])
def test_service_ports_are_refused(net, port):
    net.resolve({"img.example": [PUBLIC]})
    net.serve(image_response)
    with pytest.raises(media.MediaError):
        media.fetch(f"http://img.example:{port}/cat.png")
    assert net.seen == []


@pytest.mark.parametrize("url", [
    "file:///etc/passwd", "ftp://img.example/x.png", "gopher://img.example/", "not a url",
])
def test_non_http_links_are_refused(net, url):
    net.serve(image_response)
    with pytest.raises(media.MediaError):
        media.fetch(url)
    assert net.seen == []


# --------------------------------------------------------------------------- #
# redirects
# --------------------------------------------------------------------------- #

def test_every_redirect_hop_is_resolved_and_pinned(net):
    dns = net.resolve({"short.example": [PUBLIC], "cdn.example": [PUBLIC_2, METADATA]})

    def handler(request):
        if request.headers["host"] == "short.example":
            return httpx.Response(302, headers={"location": "https://cdn.example/a.png"})
        return image_response(request)

    net.serve(handler)
    assert media.fetch("http://short.example/x")
    assert dns.lookups == ["short.example", "cdn.example"]
    assert [r.url.host for r in net.seen] == [PUBLIC, PUBLIC_2]


def test_redirect_into_the_private_network_is_refused(net):
    net.resolve({"short.example": [PUBLIC], "internal.example": [METADATA]})
    net.serve(lambda r: httpx.Response(
        302, headers={"location": "http://internal.example/latest/meta-data/"}))
    with pytest.raises(media.MediaError, match="isn't allowed"):
        media.fetch("http://short.example/x")
    assert len(net.seen) == 1                  # the private hop was never requested


def test_relative_redirects_resolve_against_the_current_url(net):
    net.resolve({"img.example": [PUBLIC]})

    def handler(request):
        if request.url.path == "/old":
            return httpx.Response(301, headers={"location": "/new.png"})
        return image_response(request)

    net.serve(handler)
    assert media.fetch("http://img.example/old")
    assert net.seen[-1].url.path == "/new.png"
    assert net.seen[-1].headers["host"] == "img.example"


def test_redirect_loops_stop(net):
    net.resolve({"img.example": [PUBLIC]})
    net.serve(lambda r: httpx.Response(302, headers={"location": "/again"}))
    with pytest.raises(media.MediaError, match="too many"):
        media.fetch("http://img.example/start")
    assert len(net.seen) == media.MAX_REDIRECTS + 1


# --------------------------------------------------------------------------- #
# hostile bodies
# --------------------------------------------------------------------------- #

class Firehose:
    """A body that would go on for ever. Counts what was actually pulled from it."""

    def __init__(self, chunk=64 * 1024):
        self.chunk = b"\0" * chunk
        self.served = 0

    def __iter__(self):
        while True:
            self.served += 1
            yield self.chunk


def test_an_endless_body_is_cut_off_at_the_cap(net, monkeypatch):
    monkeypatch.setattr(media, "MAX_UPLOAD_BYTES", 256 * 1024)
    net.resolve({"img.example": [PUBLIC]})
    body = Firehose()
    net.serve(lambda r: httpx.Response(
        200, headers={"content-type": "image/png"}, content=body))

    with pytest.raises(media.MediaError, match="over"):
        media.fetch("http://img.example/huge.png")
    # stopped on the chunk that crossed the line, rather than buffering for ever
    assert body.served <= (256 * 1024) // (64 * 1024) + 1


def test_a_declared_oversize_body_is_refused_unread(net, monkeypatch):
    monkeypatch.setattr(media, "MAX_UPLOAD_BYTES", 256 * 1024)
    net.resolve({"img.example": [PUBLIC]})
    body = Firehose()
    net.serve(lambda r: httpx.Response(
        200, headers={"content-type": "image/png",
                      "content-length": str(50 * 1024 * 1024)}, content=body))

    with pytest.raises(media.MediaError, match="over"):
        media.fetch("http://img.example/huge.png")
    assert body.served == 0


def test_a_body_that_is_not_an_image_is_refused(net):
    net.resolve({"img.example": [PUBLIC]})
    net.serve(lambda r: httpx.Response(
        200, headers={"content-type": "text/html"}, content=b"<html>login</html>"))
    with pytest.raises(media.MediaError, match="isn't an image"):
        media.fetch("http://img.example/page")


def test_a_lying_content_type_still_meets_pillow(net):
    """The header is defence in depth. Pillow's decode is the check that counts."""
    net.resolve({"img.example": [PUBLIC]})
    net.serve(lambda r: httpx.Response(
        200, headers={"content-type": "image/png"}, content=b"not really a png"))
    with pytest.raises(media.MediaError):
        media.fetch("http://img.example/fake.png")


def test_an_error_status_is_reported(net):
    net.resolve({"img.example": [PUBLIC]})
    net.serve(lambda r: httpx.Response(404))
    with pytest.raises(media.MediaError, match="404"):
        media.fetch("http://img.example/gone.png")
