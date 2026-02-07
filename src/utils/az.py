from __future__ import annotations

import base64
import json
from typing import Any, Dict, Iterable, List, Optional, Tuple

from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient

from src.utils.vars import REPORTS_STORAGE_ACCOUNT, REPORTS_PREFIX, REPORTS_CONTAINER


def require(name: str, value: str) -> None:
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")


def blob_client() -> BlobServiceClient:
    require("REPORTS_STORAGE_ACCOUNT", REPORTS_STORAGE_ACCOUNT)
    cred = DefaultAzureCredential(exclude_interactive_browser_credential=True)
    return BlobServiceClient(
        account_url=f"https://{REPORTS_STORAGE_ACCOUNT}.blob.core.windows.net",
        credential=cred,
    )


def build_blob_dir(suite: str, env: str, platform: str, run_id: str) -> str:
    # <prefix>/<suite>/<env>/<platform>/<run_id>
    if REPORTS_PREFIX:
        return f"{REPORTS_PREFIX}/{suite}/{env}/{platform}/{run_id}"
    return f"{suite}/{env}/{platform}/{run_id}"


def public_blob_url(path: str) -> str:
    return f"https://{REPORTS_STORAGE_ACCOUNT}.blob.core.windows.net/{REPORTS_CONTAINER}/{path}"


# -------------------- Prefix-based listing --------------------

def compute_name_starts_with(
    suite: Optional[str],
    env: Optional[str],
    platform: Optional[str],
) -> str:
    """
    Compute the narrowest possible prefix for Azure Storage listing.

    Layout:
      <REPORTS_PREFIX>/<suite>/<env>/<platform>/<run_id>/run.json

    Only leading segments can narrow the prefix. If suite is missing, we fall back
    to <REPORTS_PREFIX>/ and filter client-side.
    """
    base = (REPORTS_PREFIX or "").strip("/")

    s = (suite or "").strip()
    e = (env or "").strip()
    p = (platform or "").strip()

    if s.lower() == "all":
        s = ""

    parts = [base] if base else []

    if s:
        parts.append(s)
        if e:
            parts.append(e)
            if p:
                parts.append(p)

    prefix = "/".join(parts)
    return f"{prefix}/" if prefix else ""


def iter_run_json_blobs(
    bsc: BlobServiceClient,
    suite: Optional[str],
    env: Optional[str],
    platform: Optional[str],
) -> Iterable[str]:
    """
    Non-paginated iterator (still useful for simple callers).
    """
    require("REPORTS_CONTAINER", REPORTS_CONTAINER)
    prefix = compute_name_starts_with(suite=suite, env=env, platform=platform)

    cc = bsc.get_container_client(REPORTS_CONTAINER)

    # ✅ Use server-side suffix filtering when supported (cheaper for full scans)
    try:
        it = cc.list_blobs(name_starts_with=prefix, name_ends_with="/run.json")
        for blob in it:
            yield blob.name
        return
    except TypeError:
        # Older SDKs don't support name_ends_with
        pass

    for blob in cc.list_blobs(name_starts_with=prefix):
        if blob.name.endswith("/run.json"):
            yield blob.name


def download_json(
    bsc: BlobServiceClient,
    blob_name: str,
) -> Optional[Dict[str, Any]]:
    require("REPORTS_CONTAINER", REPORTS_CONTAINER)
    cc = bsc.get_container_client(REPORTS_CONTAINER)
    try:
        raw = cc.download_blob(blob_name).readall()
        return json.loads(raw)
    except Exception:
        return None


# -------------------- Pagination helpers (NEW) --------------------

def list_run_json_blobs_page(
    bsc: BlobServiceClient,
    suite: Optional[str],
    env: Optional[str],
    platform: Optional[str],
    continuation_token: Optional[str],
    results_per_page: int,
) -> Tuple[List[str], Optional[str]]:
    """
    Returns a single Azure page of blob names (filtered to */run.json)
    + the next Azure continuation token.

    NOTE:
      Some azure-storage-blob versions do NOT support results_per_page in by_page(),
      so we pass it to list_blobs() instead.
    """
    require("REPORTS_CONTAINER", REPORTS_CONTAINER)

    prefix = compute_name_starts_with(suite=suite, env=env, platform=platform)
    cc = bsc.get_container_client(REPORTS_CONTAINER)

    # ✅ Prefer server-side suffix filtering when supported (helps index rebuild)
    try:
        try:
            paged = cc.list_blobs(
                name_starts_with=prefix,
                name_ends_with="/run.json",
                results_per_page=results_per_page,
            )
        except TypeError:
            # fallback if results_per_page isn't supported on this SDK version
            paged = cc.list_blobs(name_starts_with=prefix, name_ends_with="/run.json")
        pages = paged.by_page(continuation_token=continuation_token)

        try:
            page = next(pages)
        except StopIteration:
            return [], None

        names = [b.name for b in page]
        next_token = pages.continuation_token
        return names, next_token
    except TypeError:
        # Older SDKs don't support name_ends_with (fall back to client-side filtering)
        pass

    # ✅ results_per_page belongs on list_blobs() for many SDK versions
    try:
        paged = cc.list_blobs(name_starts_with=prefix, results_per_page=results_per_page)
    except TypeError:
        # fallback for very old versions that don't support results_per_page
        paged = cc.list_blobs(name_starts_with=prefix)

    pages = paged.by_page(continuation_token=continuation_token)

    try:
        page = next(pages)
    except StopIteration:
        return [], None

    names = [b.name for b in page if b.name.endswith("/run.json")]
    next_token = pages.continuation_token
    return names, next_token


def encode_cursor(token: Optional[str], skip: int, page_size: int) -> Optional[str]:
    """
    Cursor packs:
      - token: Azure continuation token (page boundary)
      - skip:  how many run.json items to skip within that page (resume mid-page)
      - page_size: keep page sizing stable across requests
    """
    if token is None and skip == 0:
        return None
    payload = {"token": token, "skip": skip, "page_size": page_size}
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("utf-8").rstrip("=")


def decode_cursor(cursor: Optional[str]) -> Tuple[Optional[str], int, Optional[int]]:
    if not cursor:
        return None, 0, None
    pad = "=" * (-len(cursor) % 4)
    raw = base64.urlsafe_b64decode((cursor + pad).encode("utf-8"))
    payload = json.loads(raw.decode("utf-8"))
    return payload.get("token"), int(payload.get("skip", 0)), payload.get("page_size")
