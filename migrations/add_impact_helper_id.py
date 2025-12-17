import sys
import os

# Add project root to Python path
sys.path.append(os.path.dirname(os.path.dirname(__file__)))

# migrations/add_impact_helper_id.py
from app import app, db
from sqlalchemy import text

with app.app_context():
    conn = db.engine.connect()

    try:
        conn.execute(text("ALTER TABLE impact_log ADD COLUMN helper_id INTEGER"))
        print("✅ Added column: impact_log.helper_id")
    except Exception as e:
        print("⚠️ helper_id already exists or failed:", e)

    conn.close()
    print("ImpactLog helper_id migration finished.")
