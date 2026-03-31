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
            tags = _tagger.tags_for_asset(asset)

            if not tags:
                continue

            for tag_value in tags:
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
        "✔  Tagging job complete — %d asset(s) processed, %d tag application(s).",
        total_assets,
        total_tagged,
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
