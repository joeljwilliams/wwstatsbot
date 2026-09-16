"""Structured logging setup (structlog layered over the stdlib).

A single StreamHandler on the root logger renders every record — ours and those
emitted by third-party libraries we don't control (telegram, httpx, asyncpg) —
through one structlog ``ProcessorFormatter``, so all output shares one shape.

Output is JSON (one object per line) by default, which is what log collectors
(CloudWatch, Loki, the k8s json-file driver, ...) expect. On an interactive
terminal it falls back to structlog's colourised console renderer for humans.

Configuration via environment:
    LOG_LEVEL   root level: DEBUG / INFO / WARNING / ...   (default INFO)
    LOG_FORMAT  "json" or "console"   (default: console on a TTY, else json)

Application code should do::

    import structlog
    logger = structlog.get_logger(__name__)
    logger.info("event_name", key=value, ...)

i.e. a short snake_case event name plus structured key/value context, rather
than a pre-formatted message string.
"""

import logging
import os
import sys

import structlog


def _use_json(fmt):
    """Decide between JSON and console rendering.

    An explicit LOG_FORMAT wins; otherwise emit human-readable output only when
    stderr is an interactive terminal (local dev) and JSON everywhere else
    (containers, CI, orchestrators — where stdout is a pipe).
    """
    if fmt:
        return fmt.strip().lower() != "console"
    return not sys.stderr.isatty()


def configure_logging():
    """Configure structlog + the stdlib root logger. Idempotent-ish: safe to call
    once at process start (it clears any pre-existing root handlers first)."""
    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    json_logs = _use_json(os.environ.get("LOG_FORMAT"))

    # UTC ISO-8601 timestamps: unambiguous across hosts and sortable. (The old
    # print()-based logging stamped a hardcoded UTC+8 local time.)
    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)

    # Processors shared by both paths — structlog-native loggers and "foreign"
    # stdlib records — so a log line looks the same whoever emitted it.
    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        timestamper,
        structlog.processors.StackInfoRenderer(),
    ]

    structlog.configure(
        processors=shared_processors
        + [
            # Prepare the event dict, then hand it to the ProcessorFormatter that
            # is attached to the stdlib handler below (which does the rendering).
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    if json_logs:
        render_chain = [
            # Render any exc_info to a plain traceback string under "exception".
            # (Deliberately not dict_tracebacks, which dumps frame locals — those
            # can contain secrets like BOT_TOKEN / DATABASE_URL.)
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ]
    else:
        render_chain = [structlog.dev.ConsoleRenderer()]

    formatter = structlog.stdlib.ProcessorFormatter(
        # foreign_pre_chain runs on records that did NOT originate from structlog
        # (i.e. plain logging.getLogger() calls in third-party libraries).
        foreign_pre_chain=shared_processors + [structlog.stdlib.ExtraAdder()],
        processors=[structlog.stdlib.ProcessorFormatter.remove_processors_meta] + render_chain,
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    # The HTTP stack logs a line per request at INFO; that's pure noise for a
    # polling bot (one long-poll every few seconds). Keep it at WARNING.
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
