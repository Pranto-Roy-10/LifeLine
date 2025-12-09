# migrations/add_dashboard_columns.py
# Run once: python migrations/add_dashboard_columns.py
# Adds helper_id and completed_at to requests table,
# and trust_score / kindness_score to user table (SQLite friendly).

from app import app, db
from sqlalchemy import text

with app.app_context():
    conn = db.engine.connect()
    # Add helper_id
    try:
        conn.execute(text("ALTER TABLE requests ADD COLUMN helper_id INTEGER"))
        print("Added column: requests.helper_id")
    except Exception as e:
        print("helper_id skipped or failed:", e)

    # Add completed_at
    try:
        conn.execute(text("ALTER TABLE requests ADD COLUMN completed_at DATETIME"))
        print("Added column: requests.completed_at")
    except Exception as e:
        print("completed_at skipped or failed:", e)

    # Add trust_score to user table
    try:
        conn.execute(text("ALTER TABLE user ADD COLUMN trust_score INTEGER DEFAULT 0"))
        print("Added column: user.trust_score")
    except Exception as e:
        print("trust_score skipped or failed:", e)

    # Add kindness_score to user table
    try:
        conn.execute(text("ALTER TABLE user ADD COLUMN kindness_score INTEGER DEFAULT 0"))
        print("Added column: user.kindness_score")
    except Exception as e:
        print("kindness_score skipped or failed:", e)

    conn.close()
    print("Migration script finished.")
