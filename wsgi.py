import os

# Ensure eventlet monkey-patching happens before importing the Flask app.
# This is important for Flask-SocketIO + Gunicorn eventlet worker.
try:
    import eventlet  # type: ignore

    eventlet.monkey_patch()
except Exception:
    pass

from app import app as _app, db, _run_startup_migrations_and_bootstrap_admin

app = _app

# When running under gunicorn, __name__ != "__main__" in app.py,
# so db.create_all() and startup migrations never execute.
# Run them here so that new tables (shops, shop_requests, etc.) are created on Render.
with app.app_context():
    db.create_all()
    _run_startup_migrations_and_bootstrap_admin()
