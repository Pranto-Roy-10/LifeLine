from app import app, db
from sqlalchemy import text

with app.app_context():
    conn = db.engine.connect()

    try:
        conn.execute(text("ALTER TABLE event ADD COLUMN completed BOOLEAN DEFAULT 0"))
        print("✅ Added event.completed column")
    except Exception as e:
        print("⚠️ completed column already exists:", e)

    conn.close()
