"""
Migration script to add latitude and longitude fields to Resource, ResourceWantedItem, and ResourceRequest models
"""
from app import app, db

def column_exists(conn, table_name, column_name):
    """Check if a column exists in a table"""
    result = conn.execute(db.text(f"PRAGMA table_info({table_name})"))
    columns = [row[1] for row in result]
    return column_name in columns

def migrate():
    with app.app_context():
        try:
            # Add columns to resources table
            with db.engine.connect() as conn:
                if not column_exists(conn, 'resources', 'latitude'):
                    conn.execute(db.text("ALTER TABLE resources ADD COLUMN latitude FLOAT"))
                    print("✓ Added latitude to resources table")
                else:
                    print("- latitude already exists in resources table")
                
                if not column_exists(conn, 'resources', 'longitude'):
                    conn.execute(db.text("ALTER TABLE resources ADD COLUMN longitude FLOAT"))
                    print("✓ Added longitude to resources table")
                else:
                    print("- longitude already exists in resources table")
                
                conn.commit()
            
            # Add columns to resource_wanted_items table
            with db.engine.connect() as conn:
                if not column_exists(conn, 'resource_wanted_items', 'latitude'):
                    conn.execute(db.text("ALTER TABLE resource_wanted_items ADD COLUMN latitude FLOAT"))
                    print("✓ Added latitude to resource_wanted_items table")
                else:
                    print("- latitude already exists in resource_wanted_items table")
                
                if not column_exists(conn, 'resource_wanted_items', 'longitude'):
                    conn.execute(db.text("ALTER TABLE resource_wanted_items ADD COLUMN longitude FLOAT"))
                    print("✓ Added longitude to resource_wanted_items table")
                else:
                    print("- longitude already exists in resource_wanted_items table")
                
                conn.commit()
            
            # Add columns to resource_requests table
            with db.engine.connect() as conn:
                if not column_exists(conn, 'resource_requests', 'latitude'):
                    conn.execute(db.text("ALTER TABLE resource_requests ADD COLUMN latitude FLOAT"))
                    print("✓ Added latitude to resource_requests table")
                else:
                    print("- latitude already exists in resource_requests table")
                
                if not column_exists(conn, 'resource_requests', 'longitude'):
                    conn.execute(db.text("ALTER TABLE resource_requests ADD COLUMN longitude FLOAT"))
                    print("✓ Added longitude to resource_requests table")
                else:
                    print("- longitude already exists in resource_requests table")
                
                conn.commit()
            
            print("\n✅ Migration completed successfully!")
            
        except Exception as e:
            print(f"❌ Migration failed: {str(e)}")
            raise

if __name__ == "__main__":
    migrate()
