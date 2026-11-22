"""Scene file locations: an asset ``href`` from STAC turned into a GDAL path we built ourselves.

The SSRF guard of the raster reads (AGENTS.md §12.4). An href is accepted only if it is https on
the one allow-listed Azure storage account, in the one allow-listed container, with a plain blob
name ending in ``.tif``/``.tiff``. GDAL then opens ``/vsiaz/<container>/<blob>``: the account
and the SAS token go in as GDAL config options, never in the path, so neither can be redirected
and the token never appears in a path, a log line or an error.
"""

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

#: A blob name: letters, digits and ``_ . - /`` only, ending in .tif/.tiff.
_BLOB_RE = re.compile(r"^[A-Za-z0-9_.\-/]{1,512}\.tiff?$")


@dataclass(frozen=True)
class BlobRef:
    """A scene file on the allow-listed storage account."""

    container: str  #: the container (allow-listed)
    blob: str  #: the blob name inside it


def blob_ref(href: str, *, account: str, container: str) -> BlobRef:
    """The blob an asset href names, if it passes every check; else ``ValueError``."""
    if len(href) > 1024:
        raise ValueError("asset href too long")
    parts = urlsplit(href)
    if parts.scheme != "https" or parts.username is not None or parts.password is not None:
        raise ValueError("asset href is not https")
    if parts.hostname != f"{account}.blob.core.windows.net":
        raise ValueError("asset href is not on the allowed storage account")
    try:
        port = parts.port
    except ValueError:
        raise ValueError("asset href has a bad port") from None
    if port is not None or parts.query or parts.fragment or "%" in href or "\\" in href:
        raise ValueError("asset href has extra parts")
    prefix = f"/{container}/"
    if not parts.path.startswith(prefix):
        raise ValueError("asset href is not in the allowed container")
    blob = parts.path.removeprefix(prefix)
    if (
        not _BLOB_RE.match(blob)
        or "//" in blob
        or any(s in ("", ".", "..") for s in blob.split("/"))
    ):
        raise ValueError("asset href has an unexpected blob name")
    return BlobRef(container=container, blob=blob)


def vsiaz_path(ref: BlobRef) -> str:
    """The GDAL path of a blob: ``/vsiaz/<container>/<blob>`` (no query, no token)."""
    return f"/vsiaz/{ref.container}/{ref.blob}"
