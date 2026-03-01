"""
Seed script to populate the shops database with nationwide Bangladesh shop data.
"""

import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(__file__))

from app import app, db, Shop
from shop_data import generate_all_shops, EXPECTED_SHOP_COUNT


def seed_shops(force=False):
    """Populate the shops table with nationwide data.
    
    Args:
        force: If True, clear existing shops and re-seed even if enough exist.
    """
    with app.app_context():
        existing_count = Shop.query.count()

        if not force and existing_count >= EXPECTED_SHOP_COUNT:
            print(f"✓ Shops already seeded ({existing_count} shops exist)")
            return

        # Clear old shops
        if existing_count > 0:
            Shop.query.delete()
            db.session.commit()
            print(f"  Cleared {existing_count} old shops")

        # Generate and insert all shops
        all_shops = generate_all_shops()
        for shop_data in all_shops:
            db.session.add(Shop(**shop_data))

        db.session.commit()
        print(f"✓ Successfully seeded {len(all_shops)} shops across Bangladesh")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Seed shops database")
    parser.add_argument("--force", action="store_true",
                        help="Force re-seed even if shops already exist")
    args = parser.parse_args()
    seed_shops(force=args.force)
