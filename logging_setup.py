"""logging_setup.py — one place to configure facilitator/server logging.

Two formats, chosen by env (LOG_FORMAT):
  - "text" (default) — human-readable, for local runs and demos.
  - "json"           — one JSON object per line, for production log pipelines
                       (structured fields: ts, level, logger, msg, plus any
                       extra=... passed to the log call).

Level from LOG_LEVEL (default INFO). Idempotent: safe to call more than once.

Secret hygiene: this module only *formats* records — it never reads keys or
tokens. The codebase's own log calls already avoid logging secrets (private
keys live in env only; ADMIN_API_TOKEN is compared, never logged). Keep it that
way: do not pass secrets into log messages or `extra`.
"""

from __future__ import annotations

import json
import logging
import os
import sys

_CONFIGURED = False

# Standard LogRecord attributes, so we can pick out caller-supplied `extra=...`
# fields for the JSON formatter without dumping internal record machinery.
_RESERVED = set(
    logging.makeLogRecord({}).__dict__.keys()
) | {"message", "asctime", "taskName"}


class JsonFormatter(logging.Formatter):
    """Minimal structured formatter: one JSON object per line."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        # Surface any structured extras the caller attached (log.info(..., extra={...})).
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(*, level: str | None = None, fmt: str | None = None) -> None:
    """Configure the root logger once. `level`/`fmt` override LOG_LEVEL/LOG_FORMAT."""
    global _CONFIGURED

    level_name = (level or os.getenv("LOG_LEVEL", "INFO")).upper()
    fmt_name = (fmt or os.getenv("LOG_FORMAT", "text")).strip().lower()

    handler = logging.StreamHandler(sys.stderr)
    if fmt_name == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%H:%M:%S")
        )

    root = logging.getLogger()
    # Replace prior handlers so repeated calls don't double-log.
    for h in list(root.handlers):
        root.removeHandler(h)
    root.addHandler(handler)
    root.setLevel(getattr(logging, level_name, logging.INFO))
    _CONFIGURED = True
