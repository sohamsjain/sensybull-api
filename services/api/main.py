import eventlet
eventlet.monkey_patch()

import logging
import os

from pythonjsonlogger import jsonlogger


def _configure_logging():
    level = os.environ.get("LOG_LEVEL", "INFO").upper()

    class _RequestIdFilter(logging.Filter):
        def filter(self, record):
            if not hasattr(record, "request_id"):
                record.request_id = "-"
            return True

    handler = logging.StreamHandler()

    if os.environ.get("LOG_FORMAT", "text") == "json":
        formatter = jsonlogger.JsonFormatter(
            "%(asctime)s %(levelname)s %(name)s %(message)s %(request_id)s",
            rename_fields={"asctime": "timestamp", "levelname": "level"},
        )
    else:
        formatter = logging.Formatter(
            "%(asctime)s  %(levelname)-8s  %(name)s: %(message)s  [req=%(request_id)s]",
        )

    handler.setFormatter(formatter)
    handler.addFilter(_RequestIdFilter())

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    root.addHandler(handler)


_configure_logging()

from app import create_app
from app.services.realtime.socketio_setup import socketio

app = create_app()

if __name__ == "__main__":
    # use_reloader=False: the reloader spawns a second process, and since
    # create_app() starts the Redis subscriber at import, that would create a
    # duplicate subscriber. One process => one subscriber.
    socketio.run(app, debug=True, host="0.0.0.0", port=5000,
                 use_reloader=False)
