"""Seed a few nearby dummy requests for testing smart suggestions.

Run:
    cd "D:/CSE471 Project/LifeLine"
    ./.venv/Scripts/python.exe scripts/seed_dummy_requests.py
"""
from datetime import datetime, timedelta
import sys
from pathlib import Path

from werkzeug.security import generate_password_hash

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import app, db, Request, User


def get_or_create_user():
    user = User.query.filter_by(email="helper@example.com").first()
    if user:
        return user

    user = User(
        name="Test Helper",
        email="helper@example.com",
        password_hash=generate_password_hash("password123"),
        is_trusted_helper=True,
        lat=23.8103,
        lng=90.4125,
    )
    db.session.add(user)
    db.session.commit()
    return user


def seed_requests(user):
    existing = Request.query.filter(Request.title.ilike("[Dummy]%")).all()
    for r in existing:
        db.session.delete(r)
    db.session.commit()

    now = datetime.utcnow()
    expires = now + timedelta(days=2)

    samples = [
        {
            "title": "[Dummy] Umbrella delivery for nearby elder",
            "category": "umbrella",
            "description": "Elderly neighbor needs an umbrella before evening rain.",
            "urgency": "high",
            "time_window": "this_evening",
            "lat": 23.8103,
            "lng": 90.4125,
        },
        {
            "title": "[Dummy] ORS and water drop",
            "category": "medicine",
            "description": "Family with mild dehydration requests ORS packs.",
            "urgency": "emergency",
            "time_window": "anytime_today",
            "lat": 23.8120,
            "lng": 90.4100,
        },
        {
            "title": "[Dummy] Evening study help for class 6",
            "category": "tutoring",
            "description": "Math revision for 60 minutes near your area.",
            "urgency": "normal",
            "time_window": "evening",
            "lat": 23.8085,
            "lng": 90.4145,
        },
    ]

    for s in samples:
        req = Request(
            user_id=user.id,
            title=s["title"],
            category=s["category"],
            description=s["description"],
            urgency=s["urgency"],
            time_window=s["time_window"],
            lat=s["lat"],
            lng=s["lng"],
            expires_at=expires,
            status="open",
            is_offer=False,
        )
        db.session.add(req)
    db.session.commit()


if __name__ == "__main__":
    with app.app_context():
        user = get_or_create_user()
        seed_requests(user)
        print("Seeded dummy requests near fallback coordinates.")
