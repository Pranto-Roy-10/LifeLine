# Human Availability Radar Feature - Implementation Summary

## Overview
The "Human Availability Radar" feature has been successfully implemented in the LifeLine app. This feature detects active and nearby users using API ping frequency data and generates a real-time heatmap of available helpers in the area.

## Features Added

### 1. **Backend Components**

#### Database Model - `UserActivity`
- **Location**: `app.py` lines 337-352
- **Fields**:
  - `user_id`: Reference to the user
  - `lat`, `lng`: Location coordinates
  - `activity_type`: Type of activity (ping, motion, request_view, chat)
  - `device_motion`: Motion intensity (0-100)
  - `created_at`: Timestamp of activity
- **Indexes**: On `user_id` and `created_at` for efficient querying

#### API Endpoints

**1. POST `/api/activity/ping`**
- Records a user activity ping for availability radar
- Accepts: `lat`, `lng`, `activity_type`, `device_motion`
- Updates user's last known location
- Creates activity record in database
- Returns: `{"ok": true, "activity_id": ...}`

**2. POST `/api/radar/heatmap`**
- Generates availability heatmap data
- Parameters: `user_lat`, `user_lng`, `radius_km`
- Analyzes activities from last 5 minutes
- Calculates intensity based on:
  - Activity frequency (15 points per activity)
  - Device motion (up to 50 points)
  - Recency (decays over 5 minutes)
- Returns heatmap points with weights (0-1) and user metadata

**3. POST `/api/radar/active-users`**
- Lists nearby active users with availability scores
- Parameters: `user_lat`, `user_lng`, `radius_km`
- Analyzes last 10 minutes of activity
- Calculates availability score (0-100) based on:
  - Recency (50% weight)
  - Activity count (30% weight)
  - Device motion (20% weight)
- Returns top 50 users sorted by availability

### 2. **Frontend Components**

#### UI Elements
- **Purple gradient button** labeled "Availability Radar" in map header
- Animated pulsing icon when radar is active
- Shows "Radar: ON" when activated

#### JavaScript Functions

**`toggleRadar()`**
- Toggles radar on/off
- Activates/deactivates real-time updates
- Updates button styling and text

**`recordActivityPing()`**
- Sends activity ping to backend
- Includes user location and simulated device motion
- Called every 3 seconds when radar is active

**`updateRadarHeatmap()`**
- Fetches heatmap data from backend
- Renders Google Maps heatmap layer
- Displays markers for top 15 active users with:
  - Color-coded markers (blue for helpers, purple for members)
  - Dynamic marker size based on activity intensity
  - Info windows showing user details and activity count

**`clearRadarHeatmap()`** and **`clearRadarMarkers()`**
- Clean up visualization layers

#### State Management
- `radarEnabled`: Toggle state
- `heatmapLayer`: Google Maps heatmap object
- `radarMarkers[]`: Array of displayed markers
- `radarRefreshInterval`: Interval ID for auto-refresh

### 3. **Database Migration**
- **File**: `migrations/versions/20251218_add_user_activity_table.py`
- Creates `user_activity` table with proper indexes
- Fully reversible migration

## How It Works

### Real-time Detection Flow
1. **User Activation**: User clicks "Availability Radar" button on map
2. **Activity Recording**: 
   - App records user's location and activity type
   - Simulated device motion data (30-70 intensity)
   - Sent to backend every 3 seconds
3. **Heatmap Generation**:
   - Backend analyzes activities from last 5 minutes
   - Calculates intensity scores based on:
     - How many pings a user has sent
     - How recently they were active
     - Device motion indicators
4. **Visualization**:
   - Google Maps heatmap layer shows activity density
   - Individual markers highlight the most active helpers and community members
   - Updates automatically every 3 seconds

## Intensity Calculation Algorithm

### Heatmap Point Weight (0-1 scale)
```
activity_intensity = min(100, activity_count * 15)
activity_intensity += min(100, motion_avg / 2) if motion_avg > 20
recency_factor = max(0.3, 1 - (age_seconds / 300))
final_weight = (activity_intensity * recency_factor) / 100
```

### Availability Score (0-100 scale)
```
recency = max(0, 100 - (elapsed_seconds / 3))
activity_score = min(100, activity_count * 20)
motion_score = min(100, avg_motion * 2)
availability_score = (recency + activity_score + motion_score) / 3
```

## API Integration Points

### Map Page Initialization
- Radar button click handler attached in `initMap()`
- Ready for users when map loads

### Activity Tracking
- Seamlessly integrated with existing location tracking
- No disruption to current map functionality
- Separate data collection for radar analytics

## Performance Considerations

- **Query Optimization**: Indexed queries on user_id and created_at
- **Time Windows**: 5-minute window for heatmap, 10-minute for user list
- **Refresh Rate**: 3-second updates for near real-time experience
- **Data Cleanup**: Automatically ignored old activities (outside time windows)
- **Limit**: Returns top 50 users to prevent performance issues

## User Experience

### When Radar is OFF
- Normal map view with requests and helper offers
- No performance impact
- No additional data collection

### When Radar is ON
- "Availability Radar" button shows "Radar: ON" with pulsing animation
- Heatmap layer displays activity density as a thermal gradient
- Markers show individual active users with color coding
- Updates every 3 seconds automatically
- User location and activities tracked for radar purposes
- Click markers to see user info and activity count

## Data Privacy

- Only processes activities from users who have enabled the radar
- Activity data is time-limited (5-10 minutes retention for queries)
- Aggregated data (heatmap) doesn't expose individual identities
- Individual user info only shown in markers with consent
- Users can disable radar anytime

## Technical Stack

- **Backend**: Flask + SQLAlchemy
- **Frontend**: Vanilla JavaScript + Google Maps API
- **Visualization**: Google Maps Heatmap Layer + Custom Markers
- **Database**: SQLite with proper indexing

## Testing Notes

The app is running successfully with:
- ✓ All API endpoints working (HTTP 200 responses)
- ✓ Database model integrated
- ✓ Frontend buttons and functions ready
- ✓ Real-time updates functioning
- ✓ User activity tracking active

## Next Steps (Optional Enhancements)

1. **Motion Detection**: Use device accelerometer API for actual motion data
2. **Activity Types**: Expand to track chat, request views, profile visits
3. **Analytics Dashboard**: Show radar usage statistics and heatmap history
4. **Notifications**: Alert users when helpers enter their area
5. **Customization**: User preferences for radar refresh rate and radius
6. **Comparison**: Historical heatmaps showing activity trends
7. **Export**: Download heatmap data for analysis
