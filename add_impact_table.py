import sqlite3
from datetime import datetime

# Connect to database
conn = sqlite3.connect('instance/lifeline.db')
cursor = conn.cursor()

# Check if table exists
cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='impact_log'")
if cursor.fetchone():
    print("✓ ImpactLog table already exists")
else:
    # Create the impact_log table
    cursor.execute('''
        CREATE TABLE impact_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            helper_id INTEGER NOT NULL,
            event_id INTEGER,
            hours REAL DEFAULT 0,
            items INTEGER DEFAULT 0,
            carbon REAL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (helper_id) REFERENCES user(id),
            FOREIGN KEY (event_id) REFERENCES event(id)
        )
    ''')
    conn.commit()
    print("✓ ImpactLog table created successfully!")

# Show table structure
cursor.execute("PRAGMA table_info(impact_log)")
columns = cursor.fetchall()
print("\nTable structure:")
for col in columns:
    print(f"  {col[1]} ({col[2]})")

# Count existing records
cursor.execute("SELECT COUNT(*) FROM impact_log")
count = cursor.fetchone()[0]
print(f"\nCurrent records: {count}")

conn.close()
