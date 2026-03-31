"""
tagger.py — Converts raw Immich asset metadata into tag strings.

This is the single module that knows *what* tags to generate.
Add new tag strategies here by implementing the TagStrategy protocol.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Protocol

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Protocol — every strategy must implement this
# ---------------------------------------------------------------------------


class TagStrategy(Protocol):
    def tags_for_asset(self, asset: dict) -> list[str]:
        """Return zero or more tag strings for the given Immich asset dict."""
        ...


# ---------------------------------------------------------------------------
# Strategy: Date hierarchy
# ---------------------------------------------------------------------------


class DateTagStrategy:
    """
    Produces tags like:
        date/2024
        date/2024/03
        date/2024/03/15
    """

    def tags_for_asset(self, asset: dict) -> list[str]:
        raw = (
            asset.get("exifInfo", {}).get("dateTimeOriginal")
            or asset.get("fileCreatedAt")
        )
        if not raw:
            return []

        try:
            # Immich returns ISO-8601 strings, potentially with "Z" suffix
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(
                timezone.utc
            )
        except ValueError:
            logger.warning("Cannot parse date '%s' for asset %s", raw, asset.get("id"))
            return []

        year = str(dt.year)
        month = f"{dt.month:02d}"
        day = f"{dt.day:02d}"

        return [
            f"date/{year}",
            f"date/{year}/{month}",
            f"date/{year}/{month}/{day}",
        ]


# ---------------------------------------------------------------------------
# Strategy: Location hierarchy
# ---------------------------------------------------------------------------


class LocationTagStrategy:
    """
    Produces tags from the location fields Immich already resolves itself:
        location/United Kingdom/England/Warwick

    Immich stores country, state, and city in the asset's exifInfo — no
    external geocoding service is needed.
    """

    def tags_for_asset(self, asset: dict) -> list[str]:
        exif = asset.get("exifInfo") or {}

        parts = []
        for field in ("country", "state", "city"):
            val = exif.get(field)
            if val:
                parts.append(val.strip().title())

        if not parts:
            return []

        tag = "location/" + "/".join(parts)
        logger.debug("Location tag: %s", tag)
        return [tag]


# ---------------------------------------------------------------------------
# Strategy: Face / people  (live data from Immich — no config file needed)
# ---------------------------------------------------------------------------


class FaceTagStrategy:
    """
    Produces tags like:
        people/Jeff

    Names come directly from Immich — whatever name you have set on each
    person in the Immich UI is used as-is.  People with no name set are
    silently skipped.

    The asset payload already contains a `people` array (returned by the
    search/metadata endpoint with `withPeople: true`), so no extra API
    calls are needed per asset.  Person names are cached for the lifetime
    of the job run to avoid redundant lookups.
    """

    def __init__(self) -> None:
        # Cache: person_id → name (or None if unnamed)
        self._cache: dict[str, str | None] = {}

    def tags_for_asset(self, asset: dict) -> list[str]:
        people = asset.get("people") or []
        tags: list[str] = []

        for person in people:
            pid: str = person.get("id", "")
            if not pid:
                continue

            # Use name already embedded in the asset payload first
            name: str | None = person.get("name") or None

            # Fall back to cache (populated by previous assets in same run)
            if name is None and pid in self._cache:
                name = self._cache[pid]

            # If still unknown, try a direct People API call and cache result
            if name is None and pid not in self._cache:
                name = self._fetch_person_name(pid)
                self._cache[pid] = name

            if name:
                tags.append(f"people/{name.strip()}")

        return tags

    def _fetch_person_name(self, person_id: str) -> str | None:
        """
        Fetch person details from Immich and return their name, or None if
        unnamed or the call fails.
        """
        import immich_api  # local import to avoid circular dependency

        try:
            data = immich_api.get_person(person_id)
            return data.get("name") or None
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not fetch person %s: %s", person_id, exc)
            return None

    def clear_cache(self) -> None:
        """Call between job runs so renamed people are picked up promptly."""
        self._cache.clear()


# ---------------------------------------------------------------------------
# Composite tagger
# ---------------------------------------------------------------------------


class AssetTagger:
    """
    Runs all strategies against an asset and returns a deduplicated tag list.
    """

    def __init__(self, strategies: list[TagStrategy] | None = None) -> None:
        self._strategies: list[TagStrategy] = strategies or [
            DateTagStrategy(),
            LocationTagStrategy(),
            FaceTagStrategy(),
        ]

    def tags_for_asset(self, asset: dict) -> list[str]:
        all_tags: list[str] = []
        for strategy in self._strategies:
            try:
                all_tags.extend(strategy.tags_for_asset(asset))
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "Strategy %s failed on asset %s: %s",
                    type(strategy).__name__,
                    asset.get("id"),
                    exc,
                )
        # Deduplicate while preserving order
        seen: set[str] = set()
        result: list[str] = []
        for t in all_tags:
            if t not in seen:
                seen.add(t)
                result.append(t)
        return result
