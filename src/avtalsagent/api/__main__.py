"""Command line of the API: `python -m avtalsagent.api`.

What:
    Serves the API with uvicorn at API_HOST:API_PORT (127.0.0.1:8000 by
    default; the container listens on 0.0.0.0). `RedactingFormatter` is
    the log format, which leaves out the OpenAI key and the database
    password.

Why:
    One command for the container and for local work, with every setting
    from the environment or `.env`, as the rest of the system has them. The
    log is where a failed run's details go (the browser gets a fixed text,
    `agui.py`): ag-ui-langgraph logs the exception of a failed run with its
    traceback, and an error's text can quote the database address or the
    key, so every line is cleaned before it is written.

How:
    The root logger gets one handler at LOG_LEVEL with
    `RedactingFormatter`, which formats a record as usual, traceback
    included, and then removes the secrets (`Settings.redact`). uvicorn
    gets `log_config=None`, so it adds no handlers of its own: its loggers,
    the access log included, go through the root's. httpx's line per
    request (each model and tool call) is left out below WARNING. On
    SIGTERM (docker stop) uvicorn waits at most `SHUTDOWN_SECONDS` for runs
    still streaming, then closes the app's connections.
"""

import logging

import uvicorn

from avtalsagent.api.app import create_app
from avtalsagent.config import Settings, get_settings

# Seconds a stopping server waits for open streams before it closes them.
SHUTDOWN_SECONDS = 5

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


class RedactingFormatter(logging.Formatter):
    """A formatter whose lines, tracebacks included, never show the settings' secrets."""

    def __init__(self, settings: Settings) -> None:
        super().__init__(LOG_FORMAT)
        self._settings = settings

    def format(self, record: logging.LogRecord) -> str:
        return self._settings.redact(super().format(record))


def configure_logging(settings: Settings) -> None:
    """One handler on the root logger, at LOG_LEVEL, through `RedactingFormatter`."""
    handler = logging.StreamHandler()
    handler.setFormatter(RedactingFormatter(settings))
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(settings.log_level.upper())
    # httpx logs every request the API makes (each model call and each tool call) at INFO.
    logging.getLogger("httpx").setLevel(logging.WARNING)


def main() -> None:
    settings = get_settings()
    configure_logging(settings)
    uvicorn.run(
        create_app(settings),
        host=settings.api_host,
        port=settings.api_port,
        log_config=None,
        timeout_graceful_shutdown=SHUTDOWN_SECONDS,
    )


if __name__ == "__main__":
    main()
