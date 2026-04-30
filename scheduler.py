"""
scheduler.py — APScheduler wiring for the auto-tagger job.

The job runs on a configurable interval (default: every 15 minutes).
It uses a persistent state file to track the last-processed timestamp so
restarts don't re-tag every asset from scratch.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

import immich_api
from tagger import AssetTagger

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_INTERVAL_MINUTES = int(os.environ.get("SCAN_INTERVAL_MINUTES", "15"))
_PAGE_SIZE = int(os.environ.get("SCAN_PAGE_SIZE", "100"))
_STATE_FILE = Path(os.environ.get("STATE_FILE", "/app/state/last_run.json"))
_INITIAL_SCAN_DAYS = int(os.environ.get("INITIAL_SCAN_DAYS", "0"))
# 0 = scan ALL assets on first run; >0 = only scan assets updated in last N days

# Tag prefixes that this service "owns" — stale tags under these prefixes
# will be removed automatically.  Manually applied tags are never touched.
_MANAGED_PREFIXES = (
    "date/", "season/", "day/", "location/",
    "people/", "camera/", "type/", "format/", "object/",
)

# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------


def _load_state() -> dict:
    if _STATE_FILE.exists():
        try:
            return json.loads(_STATE_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            logger.warning("Corrupt state file — resetting.")
    return {}


def _save_state(state: dict) -> None:
    _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Core job
# ---------------------------------------------------------------------------

_tagger = AssetTagger()


def _run_tagging_job() -> None:
    logger.info("▶  Tagging job started.")

    # Clear the per-run person-name cache so any renames in Immich are picked up
    for strategy in _tagger._strategies:
        if hasattr(strategy, "clear_cache"):
            strategy.clear_cache()

    state = _load_state()
    last_run: str | None = state.get("last_run_utc")

    # On very first run, optionally limit look-back window
    updated_after: str | None = last_run
    if updated_after is None and _INITIAL_SCAN_DAYS > 0:
        from datetime import timedelta

        cutoff = datetime.now(timezone.utc) - timedelta(days=_INITIAL_SCAN_DAYS)
        updated_after = cutoff.isoformat()
        logger.info("First run — scanning assets updated after %s.", updated_after)
    elif updated_after is None:
        logger.info("First run — scanning ALL assets.")

    # Record start time *before* scanning so we don't miss assets added mid-run
    run_start = datetime.now(timezone.utc).isoformat()

    # ------------------------------------------------------------------
    # Build tag-value → tag-id cache from existing tags
    # ------------------------------------------------------------------
    existing_tags: dict[str, str] = immich_api._build_existing_cache(
        immich_api.get_all_tags()
    )
    logger.info("Loaded %d existing tags from Immich.", len(existing_tags))

    # ------------------------------------------------------------------
    # Page through assets
    # ------------------------------------------------------------------
    page = 1
    total_assets = 0
    total_tagged = 0
    total_removed = 0

    _, grand_total = immich_api.search_assets(
        page=1, page_size=1, updated_after=updated_after
    )
    logger.info("Found %d asset(s) to process.", grand_total)

    while True:
        assets, _ = immich_api.search_assets(
            page=page,
            page_size=_PAGE_SIZE,
            updated_after=updated_after,
        )
        if not assets:
            break

        logger.info("Processing page %d (%d assets)…", page, len(assets))

        for asset in assets:
            asset_id: str = asset["id"]
            new_tags = set(_tagger.tags_for_asset(asset))

            # ------------------------------------------------------------------
            # Fetch tags already on this asset and identify stale ones.
            # We only remove tags under prefixes this service manages — manually
            # applied tags are never touched.
            # ------------------------------------------------------------------
            try:
                current_asset_tags = immich_api.get_asset_tags(asset_id)
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "Could not fetch current tags for asset %s: %s", asset_id, exc
                )
                current_asset_tags = []

            current_managed: dict[str, str] = {
                t["value"]: t["id"]
                for t in current_asset_tags
                if t.get("value", "").startswith(_MANAGED_PREFIXES)
            }

            stale_tags = set(current_managed.keys()) - new_tags

            # Remove stale tags
            for stale_value in stale_tags:
                try:
                    stale_id = current_managed[stale_value]
                    immich_api.remove_tags_from_asset(stale_id, [asset_id])
                    logger.info(
                        "Removed stale tag '%s' from asset %s.", stale_value, asset_id
                    )
                    total_removed += 1
                except Exception as exc:  # noqa: BLE001
                    logger.error(
                        "Failed to remove stale tag '%s' from asset %s: %s",
                        stale_value,
                        asset_id,
                        exc,
                    )

            # Apply new/updated tags
            for tag_value in new_tags:
                try:
                    tag_id = immich_api.upsert_tag(tag_value, existing_tags)
                    immich_api.apply_tags_to_assets(tag_id, [asset_id])
                    total_tagged += 1
                except Exception as exc:  # noqa: BLE001
                    logger.error(
                        "Failed to apply tag '%s' to asset %s: %s",
                        tag_value,
                        asset_id,
                        exc,
                    )

        total_assets += len(assets)
        # Stop when we've seen fewer assets than a full page — last page
        if len(assets) < _PAGE_SIZE:
            break
        page += 1

    # ------------------------------------------------------------------
    # Persist state
    # ------------------------------------------------------------------
    state["last_run_utc"] = run_start
    _save_state(state)

    logger.info(
        "✔  Tagging job complete — %d asset(s) processed, "
        "%d tag application(s), %d stale tag(s) removed.",
        total_assets,
        total_tagged,
        total_removed,
    )


# ---------------------------------------------------------------------------
# Scheduler factory
# ---------------------------------------------------------------------------


def start_scheduler() -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(
        _run_tagging_job,
        trigger=IntervalTrigger(minutes=_INTERVAL_MINUTES),
        id="tagging_job",
        name="Immich Auto-Tagger",
        replace_existing=True,
        max_instances=1,  # prevent overlapping runs
        next_run_time=datetime.now(timezone.utc),  # run immediately on startup
    )
    scheduler.start()
    logger.info(
        "Scheduler started — job fires every %d minute(s).", _INTERVAL_MINUTES
    )
    return scheduler
