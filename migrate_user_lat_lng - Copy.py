"""
Migration script to add latitude and longitude fields to the User model
"""
from app import app, db
from sqlalchemy import text

def column_exists(conn, table_name, column_name):
    """Check if a column exists in a table (SQLite specific)"""
    result = conn.execute(text(f"PRAGMA table_info({table_name})"))
    for row in result:
        if row[1] == column_name:
            return True
    return False

def migrate():
    with app.app_context():
        print("Checking 'user' table for missing location columns...")
        try:
            with db.engine.connect() as conn:
                # 1. Check and Add 'lat'
                if not column_exists(conn, 'user', 'lat'):
                    conn.execute(text("ALTER TABLE user ADD COLUMN lat FLOAT"))
                    print("✓ Added 'lat' column to user table")
                else:
                    print("- 'lat' column already exists")
                
                # 2. Check and Add 'lng'
                if not column_exists(conn, 'user', 'lng'):
                    conn.execute(text("ALTER TABLE user ADD COLUMN lng FLOAT"))
                    print("✓ Added 'lng' column to user table")
                else:
                    print("- 'lng' column already exists")
                
                conn.commit()
            
            print("\n✅ User table migration completed successfully!")
            
        except Exception as e:
            print(f"❌ Migration failed: {str(e)}")

if __name__ == "__main__":
    migrate()