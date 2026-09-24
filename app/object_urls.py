"""Resolve judge downloads through an explicitly configured internal endpoint."""

from urllib.parse import urljoin, urlsplit, urlunsplit


def resolve_object_url(url: str, api_base: str, public_origins: str = "") -> str:
    if url.startswith("/"):
        return urljoin(api_base.rstrip("/") + "/", url)
    parts = urlsplit(url)
    allowed = {origin.strip().rstrip("/") for origin in public_origins.split(",") if origin.strip()}
    if f"{parts.scheme}://{parts.netloc}" in allowed and parts.path.startswith("/minio/"):
        internal = urlsplit(api_base)
        # Keep the signed path/query unchanged. The existing nginx MinIO proxy
        # supplies the original upstream Host used to sign the request.
        return urlunsplit((internal.scheme, internal.netloc, parts.path, parts.query, parts.fragment))
    return url
