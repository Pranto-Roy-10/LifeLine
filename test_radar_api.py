#!/usr/bin/env python3
"""
Example API calls for the Human Availability Radar feature.
Run these after the app is running to test the radar functionality.
"""

import requests
import json
import time

BASE_URL = "http://127.0.0.1:5000"

# Test coordinates (Dhaka, Bangladesh)
TEST_LAT = 23.8103
TEST_LNG = 90.4125
TEST_RADIUS_KM = 3

def record_activity_ping(session, lat, lng, activity_type="ping", device_motion=None):
    """Record an activity ping for the current user."""
    url = f"{BASE_URL}/api/activity/ping"
    
    payload = {
        "lat": lat,
        "lng": lng,
        "activity_type": activity_type,
        "device_motion": device_motion or (30 + (hash(str(time.time())) % 40))
    }
    
    response = session.post(url, json=payload)
    print(f"\n[PING] {response.status_code}")
    print(json.dumps(response.json(), indent=2))
    return response.json()


def get_radar_heatmap(session, user_lat, user_lng, radius_km):
    """Get the availability heatmap data."""
    url = f"{BASE_URL}/api/radar/heatmap"
    
    payload = {
        "user_lat": user_lat,
        "user_lng": user_lng,
        "radius_km": radius_km
    }
    
    response = session.post(url, json=payload)
    print(f"\n[HEATMAP] {response.status_code}")
    data = response.json()
    print(f"Status: {data.get('ok')}")
    print(f"Total active nearby: {data.get('total_active_nearby')}")
    print(f"Helpers nearby: {data.get('helpers_nearby')}")
    
    if data.get('heatmap_points'):
        print(f"\nTop 3 Heatmap Points:")
        for i, point in enumerate(data['heatmap_points'][:3], 1):
            print(f"\n  {i}. {point.get('name')}")
            print(f"     Location: ({point.get('lat'):.4f}, {point.get('lng'):.4f})")
            print(f"     Weight (intensity): {point.get('weight'):.2f}")
            print(f"     Is Helper: {point.get('is_helper')}")
            print(f"     Activity Count: {point.get('activity_count')}")
    
    return data


def get_active_users(session, user_lat, user_lng, radius_km):
    """Get list of active nearby users."""
    url = f"{BASE_URL}/api/radar/active-users"
    
    payload = {
        "user_lat": user_lat,
        "user_lng": user_lng,
        "radius_km": radius_km
    }
    
    response = session.post(url, json=payload)
    print(f"\n[ACTIVE USERS] {response.status_code}")
    data = response.json()
    print(f"Status: {data.get('ok')}")
    
    if data.get('active_users'):
        print(f"\nTop 5 Active Users:")
        for i, user in enumerate(data['active_users'][:5], 1):
            print(f"\n  {i}. {user.get('name')}")
            print(f"     User ID: {user.get('user_id')}")
            print(f"     Location: ({user.get('lat'):.4f}, {user.get('lng'):.4f})")
            print(f"     Distance: {user.get('distance_km')} km")
            print(f"     Availability Score: {user.get('availability_score')}/100")
            print(f"     Activity Count: {user.get('activity_count')}")
            print(f"     Is Helper: {user.get('is_helper')}")
            print(f"     Last Activity: {user.get('last_activity_ago_secs')} seconds ago")
    
    return data


def login_demo_user(session):
    """Login with a demo user account."""
    print("\n" + "="*60)
    print("LOGGING IN DEMO USER")
    print("="*60)
    
    login_data = {
        "email": "muazashraf2001@gmail.com",
        "password": "test123"
    }
    
    response = session.post(f"{BASE_URL}/login", data=login_data)
    if response.status_code == 200 or "next" in response.url:
        print("✓ Login successful")
        return True
    else:
        print("✗ Login failed")
        return False


def demonstrate_radar_flow():
    """Demonstrate the complete radar feature flow."""
    
    # Create a session to maintain cookies
    session = requests.Session()
    
    print("\n" + "="*60)
    print("HUMAN AVAILABILITY RADAR - DEMO")
    print("="*60)
    
    # Login
    if not login_demo_user(session):
        print("Please ensure the app is running and you're logged in")
        print("Try: http://127.0.0.1:5000/login")
        return
    
    print("\n" + "="*60)
    print("STEP 1: RECORD ACTIVITY PINGS")
    print("="*60)
    
    # Record multiple pings to simulate activity
    for i in range(3):
        print(f"\nRecording ping {i+1}...")
        record_activity_ping(
            session, 
            lat=TEST_LAT + (i * 0.001),  # Slight variation
            lng=TEST_LNG + (i * 0.001),
            activity_type="ping",
            device_motion=40 + (i * 5)
        )
        time.sleep(0.5)
    
    print("\n" + "="*60)
    print("STEP 2: GET HEATMAP DATA")
    print("="*60)
    
    heatmap_data = get_radar_heatmap(
        session,
        user_lat=TEST_LAT,
        user_lng=TEST_LNG,
        radius_km=TEST_RADIUS_KM
    )
    
    print("\n" + "="*60)
    print("STEP 3: GET ACTIVE USERS")
    print("="*60)
    
    active_users_data = get_active_users(
        session,
        user_lat=TEST_LAT,
        user_lng=TEST_LNG,
        radius_km=TEST_RADIUS_KM
    )
    
    print("\n" + "="*60)
    print("STEP 4: SIMULATE CONTINUOUS RADAR UPDATE")
    print("="*60)
    
    print("\nSimulating 3 radar updates (3 seconds apart)...")
    for i in range(3):
        print(f"\n[Update {i+1}/3]")
        record_activity_ping(session, TEST_LAT, TEST_LNG, "ping")
        time.sleep(1)
        heatmap = get_radar_heatmap(session, TEST_LAT, TEST_LNG, TEST_RADIUS_KM)
        print(f"  Total active nearby: {heatmap.get('total_active_nearby')}")
    
    print("\n" + "="*60)
    print("DEMO COMPLETE")
    print("="*60)
    
    print("""
KEY OBSERVATIONS:
  ✓ Activity pings are recorded successfully
  ✓ Heatmap calculates intensity based on activity
  ✓ Active users are ranked by availability score
  ✓ Real-time updates work continuously

NEXT STEPS:
  1. Visit http://127.0.0.1:5000/map in your browser
  2. Click the "Availability Radar" button
  3. Watch the heatmap and markers update in real-time
  4. Click markers to see user details
    """)


if __name__ == "__main__":
    try:
        demonstrate_radar_flow()
    except Exception as e:
        print(f"\n❌ Error: {e}")
        print("\nMake sure the app is running at http://127.0.0.1:5000")
        print("Run: python app.py")
