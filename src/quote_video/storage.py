"""Transfers limited to signed objects in the configured Supabase project."""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

import httpx

MAX_MEDIA_BYTES = 50 * 1024 * 1024


def signed_object_key(url: str, *, upload: bool = False) -> str:
    project = urlsplit(os.environ.get("SUPABASE_URL", ""))
    target = urlsplit(url)
    endpoint = "object/upload/sign" if upload else "object/sign"
    prefix = f"/storage/v1/{endpoint}/uploads/"
    if (
        project.scheme != "https" or not project.netloc
        or (target.scheme, target.netloc) != (project.scheme, project.netloc)
        or not target.path.startswith(prefix)
        or not parse_qs(target.query).get("token")
        or target.fragment
    ):
        raise ValueError("Expected a signed Supabase uploads URL")
    key = unquote(target.path[len(prefix):])
    if any(part in ("", ".", "..") for part in key.split("/")) or "\\" in key:
        raise ValueError("Invalid storage object key")
    return key


def download_clip(client: httpx.Client, url: str, path: Path) -> None:
    with client.stream("GET", url) as response:
        response.raise_for_status()
        size = 0
        with path.open("wb") as output:
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > MAX_MEDIA_BYTES:
                    raise ValueError("Source video exceeds the upload limit")
                output.write(chunk)
        if not size:
            raise ValueError("Source video is empty")


def upload_result(client: httpx.Client, url: str, path: Path, content_type: str) -> int:
    size = path.stat().st_size
    if not 0 < size <= MAX_MEDIA_BYTES:
        raise ValueError("Invalid render output size")
    with path.open("rb") as content:
        client.put(url, content=iter(lambda: content.read(1024 * 1024), b""), headers={
            "Content-Type": content_type,
            "Content-Length": str(size),
            "Cache-Control": "max-age=3600",
            "x-upsert": "true",
        }).raise_for_status()
    return size
