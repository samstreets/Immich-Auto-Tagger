"""
tagger.py — Converts raw Immich asset metadata into tag strings.

This is the single module that knows *what* tags to generate.
Add new tag strategies here by implementing the TagStrategy protocol.
"""

from __future__ import annotations

import base64
import json
import logging
import os
from datetime import datetime, timezone
from typing import Protocol

import requests

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
            (asset.get("exifInfo") or {}).get("dateTimeOriginal")
            or asset.get("fileCreatedAt")
        )
        if not raw:
            return []

        try:
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
# Strategy: Face / people
# ---------------------------------------------------------------------------


class FaceTagStrategy:
    """
    Produces tags like:
        people/Jeff

    Names come directly from Immich — whatever name you have set on each
    person in the Immich UI is used as-is.  People with no name set are
    silently skipped.
    """

    def __init__(self) -> None:
        self._cache: dict[str, str | None] = {}

    def tags_for_asset(self, asset: dict) -> list[str]:
        people = asset.get("people") or []
        tags: list[str] = []

        for person in people:
            pid: str = person.get("id", "")
            if not pid:
                continue

            name: str | None = person.get("name") or None

            if name is None and pid in self._cache:
                name = self._cache[pid]

            if name is None and pid not in self._cache:
                name = self._fetch_person_name(pid)
                self._cache[pid] = name

            if name:
                tags.append(f"people/{name.strip()}")

        return tags

    def _fetch_person_name(self, person_id: str) -> str | None:
        import immich_api

        try:
            data = immich_api.get_person(person_id)
            return data.get("name") or None
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not fetch person %s: %s", person_id, exc)
            return None

    def clear_cache(self) -> None:
        self._cache.clear()


# ---------------------------------------------------------------------------
# Strategy: Camera make / model
# ---------------------------------------------------------------------------


class CameraTagStrategy:
    """
    Produces tags like:
        camera/Sony/ILCE-7M4
        camera/Apple/iPhone 15 Pro

    Both `make` and `model` come from exifInfo.  If only one is present a
    single-level tag is produced (e.g. camera/Sony).
    """

    def tags_for_asset(self, asset: dict) -> list[str]:
        exif = asset.get("exifInfo") or {}
        make: str | None = exif.get("make")
        model: str | None = exif.get("model")

        if not make and not model:
            return []

        parts = []
        if make:
            parts.append(make.strip().title())
        if model:
            model_clean = model.strip()
            # Strip leading make word if the model string duplicates it
            if make and model_clean.lower().startswith(make.strip().lower()):
                model_clean = model_clean[len(make.strip()):].strip()
            if model_clean:
                parts.append(model_clean)

        if not parts:
            return []

        return ["camera/" + "/".join(parts)]


# ---------------------------------------------------------------------------
# Strategy: Season
# ---------------------------------------------------------------------------


class SeasonTagStrategy:
    """
    Produces tags like:
        season/Spring

    Uses meteorological seasons for the Northern Hemisphere.
    """

    _MONTH_TO_SEASON: dict[int, str] = {
        12: "Winter", 1: "Winter",  2: "Winter",
        3:  "Spring", 4: "Spring",  5: "Spring",
        6:  "Summer", 7: "Summer",  8: "Summer",
        9:  "Autumn", 10: "Autumn", 11: "Autumn",
    }

    def tags_for_asset(self, asset: dict) -> list[str]:
        raw = (
            (asset.get("exifInfo") or {}).get("dateTimeOriginal")
            or asset.get("fileCreatedAt")
        )
        if not raw:
            return []

        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(
                timezone.utc
            )
        except ValueError:
            return []

        season = self._MONTH_TO_SEASON.get(dt.month)
        return [f"season/{season}"] if season else []


# ---------------------------------------------------------------------------
# Strategy: Day of week
# ---------------------------------------------------------------------------


class DayOfWeekTagStrategy:
    """
    Produces tags like:
        day/Monday
        day/weekday
        day/weekend
    """

    _DAY_NAMES = [
        "Monday", "Tuesday", "Wednesday", "Thursday",
        "Friday", "Saturday", "Sunday",
    ]

    def tags_for_asset(self, asset: dict) -> list[str]:
        raw = (
            (asset.get("exifInfo") or {}).get("dateTimeOriginal")
            or asset.get("fileCreatedAt")
        )
        if not raw:
            return []

        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(
                timezone.utc
            )
        except ValueError:
            return []

        day_name = self._DAY_NAMES[dt.weekday()]
        group = "weekend" if dt.weekday() >= 5 else "weekday"
        return [f"day/{day_name}", f"day/{group}"]


# ---------------------------------------------------------------------------
# Strategy: Media type
# ---------------------------------------------------------------------------


class MediaTypeTagStrategy:
    """
    Produces tags like:
        type/photo
        type/video
        type/live-photo
    """

    def tags_for_asset(self, asset: dict) -> list[str]:
        if asset.get("livePhotoVideoId"):
            return ["type/live-photo"]

        raw_type: str = (asset.get("type") or "").upper()
        mapping = {"IMAGE": "photo", "VIDEO": "video"}
        label = mapping.get(raw_type)
        return [f"type/{label}"] if label else []


# ---------------------------------------------------------------------------
# Strategy: File format
# ---------------------------------------------------------------------------


class FileFormatTagStrategy:
    """
    Produces tags like:
        format/JPEG
        format/ARW
        format/RAW   ← added for any recognised RAW extension

    Derives the format from the original file name extension.
    """

    _RAW_EXTENSIONS = {
        "arw", "cr2", "cr3", "nef", "orf", "raf",
        "rw2", "dng", "pef", "srw", "x3f", "raw",
    }

    def tags_for_asset(self, asset: dict) -> list[str]:
        filename: str = asset.get("originalFileName") or ""
        if not filename or "." not in filename:
            return []

        ext = filename.rsplit(".", 1)[-1].lower()
        if not ext:
            return []

        tags = [f"format/{ext.upper()}"]
        if ext in self._RAW_EXTENSIONS:
            tags.append("format/RAW")

        return tags


# ---------------------------------------------------------------------------
# Strategy: Object / scene detection via Claude Vision (plain HTTP, no SDK)
# ---------------------------------------------------------------------------


class ObjectTagStrategy:
    """
    Uses the Anthropic Messages API over plain HTTP to identify the main
    subjects/objects in a photo and produces tags like:
        object/dog
        object/aeroplane
        object/sunset

    No third-party Anthropic SDK is required — only the `requests` library
    already present in requirements.txt.

    Requirements:
        ANTHROPIC_API_KEY env var must be set.

    Only IMAGE assets are processed; videos are silently skipped.
    Tune label count with OBJECT_TAG_MAX_LABELS (default 5).
    """

    _ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
    _MODEL = "claude-sonnet-4-20250514"
    _MAX_LABELS = int(os.environ.get("OBJECT_TAG_MAX_LABELS", "5"))
    _IMMICH_URL: str = os.environ.get("IMMICH_URL", "").rstrip("/")
    _IMMICH_API_KEY: str = os.environ.get("IMMICH_API_KEY", "")
    _ANTHROPIC_API_KEY: str = os.environ.get("ANTHROPIC_API_KEY", "")

    _PROMPT = (
        "Look at this photo and list up to {max_labels} of the most prominent "
        "objects, animals, vehicles, scenes, or subjects you can see. "
        "Reply with ONLY a comma-separated list of lowercase singular nouns "
        "(e.g. dog, aeroplane, mountain, sunset). "
        "No explanations, no punctuation other than commas."
    )

    def __init__(self) -> None:
        if not self._ANTHROPIC_API_KEY:
            logger.warning(
                "ObjectTagStrategy: ANTHROPIC_API_KEY not set — "
                "object tagging disabled."
            )

    def tags_for_asset(self, asset: dict) -> list[str]:
        if not self._ANTHROPIC_API_KEY:
            return []

        if (asset.get("type") or "").upper() != "IMAGE":
            return []

        asset_id: str = asset.get("id", "")
        if not asset_id:
            return []

        image_data = self._fetch_thumbnail(asset_id)
        if not image_data:
            return []

        labels = self._analyse(image_data)
        logger.debug("Object tags for %s: %s", asset_id, labels)
        return [f"object/{label}" for label in labels]

    def _fetch_thumbnail(self, asset_id: str) -> bytes | None:
        url = f"{self._IMMICH_URL}/api/assets/{asset_id}/thumbnail?size=preview"
        try:
            resp = requests.get(
                url,
                headers={"x-api-key": self._IMMICH_API_KEY},
                timeout=15,
            )
            resp.raise_for_status()
            return resp.content
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not fetch thumbnail for %s: %s", asset_id, exc)
            return None

    def _analyse(self, image_data: bytes) -> list[str]:
        b64 = base64.standard_b64encode(image_data).decode()
        payload = {
            "model": self._MODEL,
            "max_tokens": 128,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/jpeg",
                                "data": b64,
                            },
                        },
                        {
                            "type": "text",
                            "text": self._PROMPT.format(max_labels=self._MAX_LABELS),
                        },
                    ],
                }
            ],
        }
        headers = {
            "x-api-key": self._ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        try:
            resp = requests.post(
                self._ANTHROPIC_URL,
                headers=headers,
                data=json.dumps(payload),
                timeout=30,
            )
            resp.raise_for_status()
            raw: str = resp.json().get("content", [{}])[0].get("text", "")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Claude vision call failed: %s", exc)
            return []

        labels = [lbl.strip().lower() for lbl in raw.split(",") if lbl.strip()]
        labels = [lbl for lbl in labels if len(lbl.split()) <= 3]
        return labels[: self._MAX_LABELS]


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
            CameraTagStrategy(),
            SeasonTagStrategy(),
            DayOfWeekTagStrategy(),
            MediaTypeTagStrategy(),
            FileFormatTagStrategy(),
            ObjectTagStrategy(),
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
        seen: set[str] = set()
        result: list[str] = []
        for t in all_tags:
            if t not in seen:
                seen.add(t)
                result.append(t)
        return result