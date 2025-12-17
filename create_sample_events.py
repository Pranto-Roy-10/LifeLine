from app import app, db, Event
from datetime import datetime

with app.app_context():
    # Delete all existing events
    Event.query.delete()
    db.session.commit()
    
    # Create the 3 sample events
    sample_events = [
        {
            "title": "Neighborhood Cleanup Drive",
            "description": "Join us for a community cleanup in Dhanmondi. Bring gloves and enthusiasm!",
            "event_type": "cleanup",
            "date": datetime(2025, 12, 20, 9, 0),
            "lat": 23.7461,
            "lng": 90.3742,
            "area": "Dhanmondi",
            "completed": False
        },
        {
            "title": "Blood Donation Camp",
            "description": "Help save lives by donating blood at the local hospital.",
            "event_type": "donation",
            "date": datetime(2025, 12, 25, 10, 0),
            "lat": 23.8103,
            "lng": 90.4125,
            "area": "Mohammadpur",
            "completed": False
        },
        {
            "title": "Free Medical Camp",
            "description": "Free health checkups for seniors and low-income families.",
            "event_type": "health",
            "date": datetime(2025, 12, 28, 14, 0),
            "lat": 23.7519,
            "lng": 90.3936,
            "area": "Gulshan",
            "completed": False
        }
    ]
    
    for e in sample_events:
        event = Event(
            creator_id=1,  # Assume user 1 exists
            title=e["title"],
            description=e["description"],
            event_type=e["event_type"],
            date=e["date"],
            lat=e["lat"],
            lng=e["lng"],
            area=e["area"],
            completed=e["completed"]
        )
        db.session.add(event)
    
    db.session.commit()
    print(f"✓ Created 3 sample events successfully!")
    
    # Verify
    events = Event.query.filter_by(completed=False).all()
    print(f"\nActive events in database: {len(events)}")
    for event in events:
        print(f"  - {event.title} ({event.area}) on {event.date.strftime('%b %d, %Y')}")
