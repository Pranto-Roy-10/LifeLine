"""WSGI entrypoint for production (Render/Gunicorn).

Render often expects `wsgi:app` as the import target.
"""

from app import app  # noqa: F401

# Gunicorn looks for a module-level variable named `app`.
