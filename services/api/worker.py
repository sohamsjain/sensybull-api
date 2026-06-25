"""
worker.py — entrypoint for the analysis worker process.

Mirrors main.py: gevent monkey-patching first (so the SEC/Groq HTTP calls and
the blocking Redis BRPOP cooperate), then structured logging, then the worker
loop. Run on Render as a separate worker service.

Start command:  flask db upgrade && python worker.py
(or just: python worker.py — migrations are owned by the web service)
"""
from gevent import monkey
monkey.patch_all()

import logging  # noqa: E402
import os  # noqa: E402

from pythonjsonlogger import jsonlogger  # noqa: E402


def _configure_logging():
    level = os.environ.get("LOG_LEVEL", "INFO").upper()
    handler = logging.StreamHandler()
    if os.environ.get("LOG_FORMAT", "text") == "json":
        formatter = jsonlogger.JsonFormatter(
            "%(asctime)s %(levelname)s %(name)s %(message)s",
            rename_fields={"asctime": "timestamp", "levelname": "level"},
        )
    else:
        formatter = logging.Formatter("%(asctime)s  %(levelname)-8s  %(name)s: %(message)s")
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    root.addHandler(handler)


_configure_logging()

from app.services.analysis.worker import run  # noqa: E402

if __name__ == "__main__":
    run()
