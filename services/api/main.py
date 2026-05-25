import logging

# Surface INFO logs (incl. the Redis subscriber's "stored + emitted" lines) when
# run as a dev server / via docker-compose. Production should configure its own.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s: %(message)s",
)

from app import create_app
from app.services.realtime.socketio_setup import socketio

app = create_app()

if __name__ == "__main__":
    # allow_unsafe_werkzeug: this entry point runs the Werkzeug dev server
    # (async_mode="threading"). For production, serve via gunicorn with an
    # eventlet/gevent worker instead of `python main.py`.
    # use_reloader=False: the reloader runs a second process, and since
    # create_app() starts the Redis subscriber at import, that would spawn a
    # duplicate subscriber. One process => one subscriber.
    socketio.run(app, debug=True, host="0.0.0.0", port=5000,
                 allow_unsafe_werkzeug=True, use_reloader=False)
