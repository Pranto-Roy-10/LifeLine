"""
Test script for the Impact Tracker Feature
This demonstrates how the feature works
"""

from app import app, db, ImpactLog, Event, User
import requests
import json

print("\n" + "="*60)
print("TESTING IMPACT TRACKER FEATURE")
print("="*60)

with app.app_context():
    # 1. Show current impact data in database
    print("\n📊 STEP 1: Current Impact Data in Database")
    print("-" * 60)
    
    all_impacts = ImpactLog.query.all()
    print(f"Total Impact Records: {len(all_impacts)}")
    
    total_hours = sum(i.hours for i in all_impacts)
    total_items = sum(i.items for i in all_impacts)
    total_carbon = sum(i.carbon for i in all_impacts)
    
    print(f"  🕒 Total Hours: {total_hours:.1f}h")
    print(f"  📦 Total Items: {total_items}")
    print(f"  🌱 Total CO₂ Saved: {total_carbon:.1f}kg")
    
    # 2. Test API Endpoint - Summary Stats
    print("\n📡 STEP 2: Testing API - /api/community/impact")
    print("-" * 60)
    
    try:
        response = requests.get('http://127.0.0.1:5000/api/community/impact', timeout=5)
        if response.status_code == 200:
            data = response.json()
            print("✅ API Response:")
            print(json.dumps(data, indent=2))
        else:
            print(f"❌ API returned status code: {response.status_code}")
    except requests.exceptions.ConnectionError:
        print("❌ Cannot connect to server. Make sure Flask app is running!")
    except Exception as e:
        print(f"❌ Error: {e}")
    
    # 3. Test API Endpoint - Graph Data
    print("\n📈 STEP 3: Testing API - /api/community/impact-over-time")
    print("-" * 60)
    
    try:
        response = requests.get('http://127.0.0.1:5000/api/community/impact-over-time', timeout=5)
        if response.status_code == 200:
            data = response.json()
            print("✅ API Response:")
            print(f"  Months: {data['labels']}")
            print(f"  Hours: {data['hours']}")
            print(f"  Items: {data['items']}")
            print(f"  Carbon: {data['carbon']}")
        else:
            print(f"❌ API returned status code: {response.status_code}")
    except requests.exceptions.ConnectionError:
        print("❌ Cannot connect to server. Make sure Flask app is running!")
    except Exception as e:
        print(f"❌ Error: {e}")
    
    # 4. How the feature works
    print("\n🎯 STEP 4: How the Feature Works")
    print("-" * 60)
    print("""
1. When an event is COMPLETED:
   - User clicks "Mark Completed" button
   - System auto-calculates impact based on event type:
     • Cleanup → 3 hours + 5kg CO₂
     • Donation → 10 items
     • Repair → 2 hours
   - Impact is saved to ImpactLog table

2. Homepage displays impact:
   - On page load, JavaScript calls:
     a) /api/community/impact → Updates 3 stat cards
     b) /api/community/impact-over-time → Draws Chart.js graph
   - Auto-refreshes every 30 seconds

3. Graph shows 3 lines:
   - Blue: Volunteer Hours
   - Orange: Items Shared  
   - Green: CO₂ Saved

4. Updates are "live" (refresh every 30 sec on homepage)
    """)
    
    # 5. Next steps
    print("\n✅ NEXT STEPS TO SEE IT WORKING:")
    print("-" * 60)
    print("""
1. Make sure Flask app is running: python app.py
2. Open browser: http://127.0.0.1:5000/
3. Scroll down to "Community Impact Dashboard"
4. You should see:
   - Stats showing real numbers
   - Graph with 3 colored lines
   - Auto-updates every 30 seconds

5. To add more data:
   - Go to Events page
   - Click "Mark Completed" on an event
   - Refresh homepage to see impact increase!
    """)
    
    print("="*60)
    print("Test Complete!")
    print("="*60 + "\n")
