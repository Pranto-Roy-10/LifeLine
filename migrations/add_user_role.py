import sys
import os

# Add project root to Python path
sys.path.append(os.path.dirname(os.path.dirname(__file__)))

# migrations/add_user_role.py
from app import app, db
from sqlalchemy import text

with app.app_context():
    conn = db.engine.connect()

    try:
        conn.execute(text(
            "ALTER TABLE user ADD COLUMN role TEXT DEFAULT 'user'"
        ))
        print("Added column: user.role")
    except Exception as e:
        print("role skipped or failed:", e)

    conn.close()
    print("Migration finished.")
