"""Images belonging to a campaign: portraits, handouts, scene art.

Everything that arrives here is treated as hostile until proven otherwise. An upload is
decoded with Pillow rather than trusted by its extension or content-type, then
re-encoded — which strips EXIF (GPS coordinates included) and destroys anything hidden
in the container. SVG is refused outright: it is a script container, not an image.

Files are content-addressed (`<sha256>.<ext>`), so duplicates cost nothing and a
filename can never be steered out of its folder.
"""

import hashlib
import io
import ipaddress
import os
import socket
from urllib.parse import urlparse

import httpx
from PIL import Image, UnidentifiedImageError

MEDIA_DIR = os.environ.get("DND_MEDIA", os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "media"))

# What we accept, and what we store it as. No SVG, ever.
ACCEPTED = {"PNG": "png", "JPEG": "jpg", "WEBP": "webp", "GIF": "gif"}

MAX_UPLOAD_BYTES = int(os.environ.get("MEDIA_MAX_BYTES", 8 * 1024 * 1024))
MAX_EDGE = int(os.environ.get("MEDIA_MAX_EDGE", 2048))          # longest side, px
MAX_PIXELS = 40_000_000                                          # decompression bomb guard
PORTRAIT_EDGE = 512
MAX_PER_CAMPAIGN = int(os.environ.get("MEDIA_MAX_PER_CAMPAIGN", 60))

Image.MAX_IMAGE_PIXELS = MAX_PIXELS


class MediaError(ValueError):
    """Something about this image isn't acceptable. The message is shown to the player."""


# --------------------------------------------------------------------------- #
# validation
# --------------------------------------------------------------------------- #

def process(raw, kind="handout"):
    """Validate and normalise image bytes.

    Returns (clean_bytes, ext, mime, width, height). Raises MediaError.
    """
    if not raw:
        raise MediaError("that file was empty")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise MediaError(f"images must be under {MAX_UPLOAD_BYTES // (1024 * 1024)}MB")

    head = raw[:200].lstrip()[:5].lower()
    if head.startswith(b"<svg") or head.startswith(b"<?xml"):
        raise MediaError("SVG isn't accepted - please use PNG, JPEG or WebP")

    try:
        probe = Image.open(io.BytesIO(raw))
        probe.verify()                      # structural check; consumes the file object
        img = Image.open(io.BytesIO(raw))   # reopen, verify() leaves it unusable
        img.load()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        raise MediaError("that doesn't look like an image file")

    if img.format not in ACCEPTED:
        raise MediaError(f"{img.format or 'that format'} isn't supported - "
                         "use PNG, JPEG or WebP")

    limit = PORTRAIT_EDGE if kind == "portrait" else MAX_EDGE
    if max(img.size) > limit:
        img.thumbnail((limit, limit), Image.LANCZOS)

    # Re-encode from decoded pixels: nothing from the original container survives.
    # Keep PNG as PNG — re-encoding drawn art or a portrait to JPEG smears it with
    # ringing artifacts. Photographs arrive as JPEG and stay JPEG, where it belongs.
    source_format = img.format
    has_alpha = img.mode in ("RGBA", "LA") or (
        img.mode == "P" and "transparency" in img.info)

    if has_alpha or source_format in ("PNG", "GIF"):
        img = img.convert("RGBA" if has_alpha else "RGB")
        out_format, ext, mime = "PNG", "png", "image/png"
    else:
        img = img.convert("RGB")
        out_format, ext, mime = "JPEG", "jpg", "image/jpeg"

    buf = io.BytesIO()
    if out_format == "JPEG":
        img.save(buf, "JPEG", quality=86, optimize=True)
    else:
        img.save(buf, "PNG", optimize=True)

    return buf.getvalue(), ext, mime, img.width, img.height


# --------------------------------------------------------------------------- #
# fetching a URL the player pasted
# --------------------------------------------------------------------------- #

# Ports with no business serving an image, and plenty of business being probed.
BLOCKED_PORTS = {22, 23, 25, 110, 143, 445, 3306, 5432, 6379, 11211, 27017}
MAX_REDIRECTS = 3
CHUNK = 64 * 1024


def _refuse(ip):
    """Is this an address the server has no business reaching?"""
    if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
            or ip.is_multicast or ip.is_unspecified):
        return True
    # ::ffff:10.0.0.1 is a private address wearing an IPv6 coat
    mapped = getattr(ip, "ipv4_mapped", None)
    return bool(mapped is not None and _refuse(mapped))


def resolve_public(host, port):
    """Resolve `host` once and return the addresses we are willing to talk to.

    Returning the addresses, rather than a yes-or-no, is the whole point: the caller
    connects to one of *these*. Checking a name and then handing the same name to an
    HTTP client lets a hostile DNS server answer twice - a public address for the
    check and 169.254.169.254 for the fetch - and only the first answer gets looked at.
    """
    if port in BLOCKED_PORTS:
        raise MediaError("that address isn't allowed")
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror:
        raise MediaError("couldn't look up that address")

    addresses = []
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if _refuse(ip):
            # fail closed: a name that resolves anywhere private is refused outright,
            # rather than quietly using whichever of its addresses looked acceptable
            raise MediaError("that address isn't allowed")
        addresses.append(str(ip))
    if not addresses:
        raise MediaError("couldn't look up that address")
    return addresses


def _host_header(url):
    """The Host the origin expects: the real name, bracketed if it is IPv6."""
    host = url.host
    if ":" in host:
        host = f"[{host}]"
    return host if url.port is None else f"{host}:{url.port}"


def _get_pinned(client, url):
    """GET `url`, connecting to an address we resolved and validated ourselves.

    The request travels to the IP while `Host` and the TLS server name stay the real
    hostname, so virtual hosting and certificate verification behave normally - and
    there is no second lookup for DNS to answer differently.
    """
    parsed = httpx.URL(url)
    if parsed.scheme not in ("http", "https"):
        raise MediaError("the link must start with http:// or https://")
    if not parsed.host:
        raise MediaError("that doesn't look like a link")

    default_port = 443 if parsed.scheme == "https" else 80
    ip = resolve_public(parsed.host, parsed.port or default_port)[0]
    request = client.build_request(
        "GET", parsed.copy_with(host=ip),
        headers={"Host": _host_header(parsed), "Accept": "image/*"},
        extensions={"sni_hostname": parsed.host},
    )
    try:
        return client.send(request, stream=True)
    except httpx.HTTPError as e:
        raise MediaError(f"couldn't fetch that link ({type(e).__name__})")


def _read_capped(response, cap=None):
    """Read a streaming response, stopping the moment it goes over the cap.

    `len(response.content)` buffers the whole body before anyone can object, which
    is an out-of-memory button for anybody who can get a URL past validation.
    """
    cap = MAX_UPLOAD_BYTES if cap is None else cap
    too_big = f"that image is over {cap // (1024 * 1024)}MB"

    declared = response.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > cap:
        raise MediaError(too_big)

    chunks, total = [], 0
    try:
        for chunk in response.iter_bytes(CHUNK):
            total += len(chunk)
            if total > cap:            # content-length is a hint, not a promise
                raise MediaError(too_big)
            chunks.append(chunk)
    except httpx.HTTPError as e:
        raise MediaError(f"couldn't fetch that link ({type(e).__name__})")
    return b"".join(chunks)


def fetch(url, kind="handout"):
    """Download an image from a URL a player supplied, with the obvious guardrails."""
    with httpx.Client(timeout=httpx.Timeout(10.0, read=20.0),
                      follow_redirects=False) as http:
        target = url
        for _ in range(MAX_REDIRECTS + 1):
            response = _get_pinned(http, target)
            if response.status_code in (301, 302, 303, 307, 308):
                location = response.headers.get("location")
                response.close()
                if not location:
                    raise MediaError("that link went nowhere")
                # every hop is resolved and validated afresh by _get_pinned: a public
                # URL is allowed to redirect, but not to somewhere private
                target = str(httpx.URL(target).join(location))
                continue
            try:
                if response.status_code >= 400:
                    raise MediaError(f"that link returned HTTP {response.status_code}")
                ctype = response.headers.get("content-type", "").split(";")[0].strip()
                if ctype and not ctype.startswith("image/"):
                    # defence in depth; Pillow's decode below is the real check
                    raise MediaError("that link isn't an image")
                raw = _read_capped(response)
            finally:
                response.close()     # a streamed response holds its connection open
            return process(raw, kind)
    raise MediaError("that link redirects too many times")


# --------------------------------------------------------------------------- #
# the file store
# --------------------------------------------------------------------------- #

def campaign_dir(cid):
    return os.path.join(MEDIA_DIR, cid)


def put(cid, data, ext):
    """Write bytes into the campaign's folder. Returns the stored filename."""
    digest = hashlib.sha256(data).hexdigest()
    name = f"{digest}.{ext}"
    folder = campaign_dir(cid)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, name)
    if not os.path.exists(path):                 # identical image, already stored
        with open(path, "wb") as f:
            f.write(data)
    return name


def read(cid, name):
    """Read one stored file. `name` comes from the database, never from a URL path."""
    if os.sep in name or "/" in name or ".." in name:
        raise MediaError("bad file reference")
    path = os.path.join(campaign_dir(cid), name)
    if not os.path.exists(path):
        return None
    with open(path, "rb") as f:
        return f.read()


def remove(cid, name):
    try:
        os.remove(os.path.join(campaign_dir(cid), name))
    except OSError:
        pass


def drop_campaign(cid):
    folder = campaign_dir(cid)
    if not os.path.isdir(folder):
        return
    for f in os.listdir(folder):
        try:
            os.remove(os.path.join(folder, f))
        except OSError:
            pass
    try:
        os.rmdir(folder)
    except OSError:
        pass
