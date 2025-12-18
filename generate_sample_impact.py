from app import app, db, ImpactLog, Event, User
from datetime import datetime, timedelta
import random

with app.app_context():
    # Get some users and events
    users = User.query.limit(3).all()
    events = Event.query.limit(3).all()
    
    if not users or not events:
        print("Need users and events first!")
        exit()
    
    # Clear old impact logs
    ImpactLog.query.delete()
    
    # Generate sample impact data for the last 6 months
    print("Generating sample impact data...\n")
    
    for month in range(6):
        date = datetime.utcnow() - timedelta(days=30 * month)
        
        for _ in range(random.randint(2, 5)):  # 2-5 impacts per month
            user = random.choice(users)
            event = random.choice(events) if events else None
            
            # Random impact based on event type
            event_type = event.event_type if event else random.choice(['cleanup', 'donation', 'repair'])
            
            if event_type == 'cleanup':
                hours = random.uniform(2, 5)
                items = 0
                carbon = random.uniform(3, 8)
            elif event_type == 'donation':
                hours = random.uniform(0.5, 2)
                items = random.randint(5, 20)
                carbon = 0
            else:  # repair
                hours = random.uniform(1, 4)
                items = 0
                carbon = random.uniform(1, 3)
            
            impact = ImpactLog(
                helper_id=user.id,
                event_id=event.id if event else None,
                hours=round(hours, 2),
                items=items,
                carbon=round(carbon, 2),
                created_at=date
            )
            db.session.add(impact)
            print(f"Added: {hours:.1f}h, {items} items, {carbon:.1f}kg CO₂ by {user.name}")
    
    db.session.commit()
    
    # Show totals
    all_impacts = ImpactLog.query.all()
    total_hours = sum(i.hours for i in all_impacts)
    total_items = sum(i.items for i in all_impacts)
    total_carbon = sum(i.carbon for i in all_impacts)
    
    print(f"\n✅ Generated {len(all_impacts)} impact records!")
    print(f"\nTotals:")
    print(f"  🕒 Hours: {total_hours:.1f}h")
    print(f"  📦 Items: {total_items}")
    print(f"  🌱 CO₂ Saved: {total_carbon:.1f}kg")
    print(f"\nNow refresh the Impact page to see the graph!")
