from app import app, db, Event

with app.app_context():
    # Mark all events as not completed
    events = Event.query.all()
    print(f'\nTotal events in database: {len(events)}')
    print('\nCurrent status:')
    for e in events:
        print(f'  ID {e.id}: {e.title} - Completed: {e.completed}')
    
    # Reset all to not completed
    for e in events:
        e.completed = False
    
    db.session.commit()
    
    print('\nAfter reset:')
    events = Event.query.all()
    for e in events:
        print(f'  ID {e.id}: {e.title} - Completed: {e.completed}')
    
    print('\nAll events are now ACTIVE!')
