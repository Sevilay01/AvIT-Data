"""Explicit allowlist of operational fields; never serialize exceptions or settings."""

import json
import logging

logger = logging.getLogger("avit.operations")


def configure_operational_logging():
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)


def log_event(operation, event_id, *, status, alarm_id=None, device_id=None, attempts=None):
    logger.info(
        json.dumps(
            {
                "operation": operation,
                "event_id": str(event_id),
                "status": status,
                "alarm_id": alarm_id,
                "device_id": device_id,
                "attempts": attempts,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )
