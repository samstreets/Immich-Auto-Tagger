"""
Immich Auto-Tagger — Main entry point.

Starts the APScheduler background worker that periodically scans Immich
assets and applies face, location, and date tags.
"""

import logging
import signal
import sys
import time

from scheduler import start_scheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("auto-tagger")


def _handle_shutdown(signum, frame):  # noqa: ARG001
    logger.info("Received signal %s — shutting down.", signum)
    sys.exit(0)


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, _handle_shutdown)
    signal.signal(signal.SIGINT, _handle_shutdown)

    logger.info("Starting Immich Auto-Tagger service.")
    scheduler = start_scheduler()

    try:
        while True:
            time.sleep(60)
    except (KeyboardInterrupt, SystemExit):
        logger.info("Stopping scheduler…")
        scheduler.shutdown()
        logger.info("Goodbye.")
