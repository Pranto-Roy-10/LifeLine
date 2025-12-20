import os

# Ensure eventlet monkey-patching happens before importing the Flask app.
# This is important for Flask-SocketIO + Gunicorn eventlet worker.
try:
    import eventlet  # type: ignore

    eventlet.monkey_patch()
except Exception:
    pass

from app import app as _app

app = _app
