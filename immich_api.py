"""
immich_api.py — Thin wrapper around the Immich REST API.

All HTTP calls live here so the rest of the codebase never touches
`requests` directly.  Every method raises on non-2xx responses so callers
can rely on exceptions rather than inspecting status codes.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration (read once at import time)
# ---------------------------------------------------------------------------

IMMICH_URL: str = os.environ["IMMICH_URL"].rstrip("/")
IMMICH_API_KEY: str = os.environ["IMMICH_API_KEY"]

_SESSION = requests.Session()
_SESSION.headers.update(
    {
        "x-api-key": IMMICH_API_KEY,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
)

_DEFAULT_TIMEOUT = int(os.environ.get("IMMICH_TIMEOUT", "30"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get(path: str, params: dict | None = None) -> Any:
    url = f"{IMMICH_URL}{path}"
    resp = _SESSION.get(url, params=params, timeout=_DEFAULT_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def _post(path: str, payload: Any) -> Any:
    url = f"{IMMICH_URL}{path}"
    resp = _SESSION.post(url, json=payload, timeout=_DEFAULT_TIMEOUT)
    resp.raise_for_status()
    if resp.status_code == 204 or not resp.content:
        return {}
    return resp.json()


def _put(path: str, payload: Any) -> Any:
    url = f"{IMMICH_URL}{path}"
    resp = _SESSION.put(url, json=payload, timeout=_DEFAULT_TIMEOUT)
    resp.raise_for_status()
    if resp.status_code == 204 or not resp.content:
        return {}
    return resp.json()


# ---------------------------------------------------------------------------
# Asset endpoints
# ---------------------------------------------------------------------------


def search_assets(
    *,
    page: int = 1,
    page_size: int = 100,
    updated_after: str | None = None,
) -> tuple[list[dict], int]:
    """
    Search assets with optional recency filter.

    Returns (assets, total_count).
    `updated_after` is an ISO-8601 string, e.g. "2024-01-01T00:00:00.000Z".
    """
    body: dict[str, Any] = {
        "page": page,
        "size": page_size,
        "withExif": True,
        "withPeople": True,
    }
    if updated_after:
        body["updatedAfter"] = updated_after

    data = _post("/api/search/metadata", body)
    assets: list[dict] = data.get("assets", {}).get("items", [])
    total: int = data.get("assets", {}).get("total", 0)
    return assets, total


def get_asset(asset_id: str) -> dict:
    """Fetch full asset detail including EXIF and face annotations."""
    return _get(f"/api/assets/{asset_id}")


def get_asset_tags(asset_id: str) -> list[dict]:
    """Return the list of tags currently applied to an asset."""
    asset = get_asset(asset_id)
    return asset.get("tags") or []


# ---------------------------------------------------------------------------
# Tag endpoints
# ---------------------------------------------------------------------------


def get_all_tags() -> list[dict]:
    """Return all existing tags."""
    return _get("/api/tags")


def _create_single_tag(name: str, parent_id: str | None = None) -> dict:
    """
    POST /api/tags to create one tag level.

    Immich builds nested hierarchies via parentId — NOT via a slash-separated
    value field.  Each segment of e.g. "date/2024/03" must be created
    individually: first "date" (no parent), then "2024" (parentId=date.id),
    then "03" (parentId=2024.id).
    """
    payload: dict[str, Any] = {"name": name}
    if parent_id:
        payload["parentId"] = parent_id
    return _post("/api/tags", payload)


def _build_existing_cache(tags: list[dict]) -> dict[str, str]:
    """
    Convert the flat list returned by GET /api/tags into a path→id map.

    Immich returns each tag with a `value` field that is the full slash path
    (e.g. "date/2024/03") when tags are nested via parentId.  Fall back to
    `name` for root-level tags that have no parent.
    """
    cache: dict[str, str] = {}
    for t in tags:
        key = t.get("value") or t.get("name", "")
        if key:
            cache[key] = t["id"]
    return cache


def upsert_tag(tag_value: str, existing: dict[str, str]) -> str:
    """
    Return the tag ID for the full slash-path `tag_value`, creating every
    level of the hierarchy that does not yet exist.

    `existing` is a path→id dict that is updated in-place, e.g.:
        "date"         → "abc-123"
        "date/2024"    → "def-456"
        "date/2024/03" → "ghi-789"

    This function walks each path segment in order.  For each level it:
      1. Returns immediately if the path is already cached.
      2. Creates the tag (with the correct parentId) if it is missing.
      3. On 400/409 (race condition / already exists) refreshes the cache
         and retries the lookup before giving up.
    """
    if tag_value in existing:
        return existing[tag_value]

    parts = tag_value.split("/")
    parent_id: str | None = None

    for i, part in enumerate(parts):
        path_so_far = "/".join(parts[: i + 1])

        if path_so_far in existing:
            parent_id = existing[path_so_far]
            continue

        try:
            tag = _create_single_tag(part, parent_id)
            tag_id: str = tag["id"]
            existing[path_so_far] = tag_id
            logger.info("Created tag '%s' (id=%s)", path_so_far, tag_id)
            parent_id = tag_id

        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code in (400, 409):
                # Tag already exists (created by another run or a race).
                # Re-fetch the full tag list and update our cache.
                logger.debug(
                    "Tag '%s' already exists — refreshing cache.", path_so_far
                )
                existing.update(_build_existing_cache(get_all_tags()))
                if path_so_far in existing:
                    parent_id = existing[path_so_far]
                    continue
                # Still not found — something else went wrong
            raise

    return existing[tag_value]


def apply_tags_to_assets(tag_id: str, asset_ids: list[str]) -> None:
    """Associate `tag_id` with every asset in `asset_ids`."""
    _put(f"/api/tags/{tag_id}/assets", {"ids": asset_ids})
    logger.debug("Applied tag %s to %d asset(s).", tag_id, len(asset_ids))


def remove_tags_from_asset(tag_id: str, asset_ids: list[str]) -> None:
    """Detach `tag_id` from every asset in `asset_ids`."""
    url = f"{IMMICH_URL}/api/tags/{tag_id}/assets"
    resp = _SESSION.delete(url, json={"ids": asset_ids}, timeout=_DEFAULT_TIMEOUT)
    resp.raise_for_status()
    logger.debug("Removed tag %s from %d asset(s).", tag_id, len(asset_ids))


# ---------------------------------------------------------------------------
# Person / face endpoints
# ---------------------------------------------------------------------------


def get_person(person_id: str) -> dict:
    """Fetch the Immich ML person record (includes `.name` if labelled)."""
    return _get(f"/api/people/{person_id}")
