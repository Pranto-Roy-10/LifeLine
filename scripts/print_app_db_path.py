from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app  # noqa: E402


def main() -> None:
    print("DB_PATH:", getattr(app, "DB_PATH", None))
    print("SQLALCHEMY_DATABASE_URI:", app.app.config.get("SQLALCHEMY_DATABASE_URI"))
    try:
        with app.app.app_context():
            print("ENGINE_DB:", app.db.engine.url.database)
            print("ENGINE_URL:", str(app.db.engine.url))
    except Exception as exc:
        print("ENGINE_DB_ERROR:", repr(exc))


if __name__ == "__main__":
    main()
