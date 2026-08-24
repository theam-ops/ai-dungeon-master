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

def _is_public(host):
    """Refuse anything that resolves inside the network the server is sitting on.

    Without this, 'paste a URL' hands anyone a way to make the server fetch its own
    localhost, a private LAN box, or a cloud metadata endpoint and hand back the result.
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        raise MediaError("couldn't look up that address")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
                or ip.is_multicast or ip.is_unspecified):
            raise MediaError("that address isn't allowed")
    return True


def fetch(url, kind="handout"):
    """Download an image from a URL the player supplied, with the obvious guardrails."""
    parsed = urlparse(url or "")
    if parsed.scheme not in ("http", "https"):
        raise MediaError("the link must start with http:// or https://")
    if not parsed.hostname:
        raise MediaError("that doesn't look like a link")
    _is_public(parsed.hostname)

    try:
        with httpx.Client(timeout=20, follow_redirects=False) as http:
            seen = 0
            target = url
            while True:
                r = http.get(target, headers={"Accept": "image/*"})
                if r.status_code in (301, 302, 303, 307, 308):
                    seen += 1
                    if seen > 3:
                        raise MediaError("that link redirects too many times")
                    target = str(r.next_request.url) if r.next_request else None
                    if not target:
                        raise MediaError("that link went nowhere")
                    # every hop gets checked: a public URL can redirect to a private one
                    hop = urlparse(target)
                    if hop.scheme not in ("http", "https") or not hop.hostname:
                        raise MediaError("that link redirects somewhere unsupported")
                    _is_public(hop.hostname)
                    continue
                break
    except httpx.HTTPError as e:
        raise MediaError(f"couldn't fetch that link ({type(e).__name__})")

    if r.status_code >= 400:
        raise MediaError(f"that link returned HTTP {r.status_code}")
    if len(r.content) > MAX_UPLOAD_BYTES:
        raise MediaError(f"that image is over {MAX_UPLOAD_BYTES // (1024 * 1024)}MB")

    return process(r.content, kind)


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
