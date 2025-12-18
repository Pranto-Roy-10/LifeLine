# Human Availability Radar - Quick Start Guide

## What is the Availability Radar?

The **Human Availability Radar** is a real-time feature that shows you where nearby users and helpers are active in your area. It uses activity tracking (API pings) to create a live heatmap of community engagement.

## How to Use It

### 1. Navigate to the Live Map
- Go to **http://127.0.0.1:5000/map**
- Sign in with your account
- You should see the map with requests and helper offers

### 2. Activate the Radar
- Look for the **purple gradient button** labeled **"Availability Radar"** at the top of the page
- Click the button to turn the radar ON
- The button will change to show **"Radar: ON"** with a pulsing icon

### 3. View the Heatmap
Once activated, you'll see:

- **Thermal Heatmap Layer**: Shows where activity is concentrated
  - Red/Orange areas = high activity
  - Yellow areas = moderate activity
  - Blue areas = low activity
  - Gray areas = no activity

- **User Markers**: Individual markers for the most active users
  - 🔵 **Blue circles** = Verified helpers
  - 🟣 **Purple circles** = Community members
  - Larger circles = More active users
  - Click markers for user details

### 4. Radar Behavior
- Updates automatically every 3 seconds
- Tracks your location and activity
- Shows users within your current radius (default 3 km)
- Continues until you turn it OFF

### 5. Turn Off Radar
- Click the "Radar: ON" button to deactivate
- Button returns to normal state
- Heatmap and markers disappear
- Activity tracking stops

## What Data is Tracked?

When radar is ON:
- Your **location** (latitude, longitude)
- **Activity type** (automated pings)
- **Device motion** (simulated activity intensity)
- **Timestamp** of each event

This data is only retained for 5-10 minutes and is used to:
- Calculate your availability score
- Generate the heatmap
- Show you nearby active users

## Understanding the Heatmap

### Intensity Colors
- 🔴 Red = Peak activity hotspots
- 🟠 Orange = High activity zones  
- 🟡 Yellow = Moderate activity
- 🔵 Blue = Light activity
- ⚪ Gray = No activity

### Availability Score (for individual users)
- **90-100**: Very active right now
- **70-89**: Actively engaged
- **50-69**: Moderately active
- **30-49**: Light activity
- **0-29**: Minimal recent activity

## Practical Use Cases

1. **Find Active Helpers**
   - See which helpers are online and engaged
   - Helps you connect with responsive community members
   - Identify high-activity zones for faster response times

2. **Analyze Community Activity**
   - Understand when and where help is most available
   - Plan when to post requests for better visibility
   - See community engagement patterns

3. **Emergency Response**
   - Identify nearby active helpers during emergencies
   - See which areas have the most engaged community members
   - Understand response potential in your region

4. **Smart Matching**
   - Find helpers actively engaged in your area
   - Connect with community members right when they're available
   - Increase chances of successful request matching

## Technical Details

### Heatmap Points
- Each active user = one heatmap point
- Weight (intensity) = 0 to 1 scale
- Weight calculated from:
  - Frequency of activity (pings)
  - Recency (how fresh the activity is)
  - Device motion indicators

### User Markers
- Top 15 most active users displayed
- Size indicates activity level
- Color indicates user role (helper vs. member)
- Click for more information

### Data Sources
- API ping frequency (automatic calls to track presence)
- Device motion simulation (indicates engagement level)
- Location updates
- Activity type (currently defaulting to "ping")

## Performance

- **Refresh Rate**: 3 seconds (automatic updates)
- **Search Window**: Last 5 minutes of activity
- **Radius**: Adjustable, default 3 km
- **Max Users Shown**: Top 50 most active

## Privacy & Security

✓ Only processes your data when radar is active
✓ Activity data automatically expires after 5-10 minutes
✓ Heatmap is aggregated (doesn't show individual locations)
✓ Individual user info shown only in clickable markers
✓ No data is stored long-term
✓ You control when to enable/disable radar

## Troubleshooting

### Radar Button Not Showing
- Ensure you're logged in
- Check that you're on the map page (/map)
- Refresh the page (F5)

### Heatmap Not Updating
- Check browser console for errors (F12)
- Ensure location permission is granted
- Try turning radar off and on again
- Check network connectivity

### No Users Showing Up
- You may be in an area with low activity
- Try expanding the radius (if option becomes available)
- Wait a moment for activities to accumulate
- Other users need to be active in your area

### Performance Issues
- Radar uses 3-second refresh intervals
- For slower connections, disable radar
- Clear browser cache if experiencing lag

## Tips for Best Results

1. **Allow Location Access**: Grant permission for accurate location detection
2. **Keep Radar On During Peak Hours**: More activity = better heatmap
3. **Wait 10-15 Seconds**: Let the heatmap accumulate initial data
4. **Click Markers**: Get detailed info about active helpers
5. **Combine with Filters**: Use existing filters + radar for best results

## API Reference (For Developers)

### Record Activity Ping
```
POST /api/activity/ping
{
  "lat": 23.8,
  "lng": 90.4,
  "activity_type": "ping",
  "device_motion": 45
}
```

### Get Heatmap Data
```
POST /api/radar/heatmap
{
  "user_lat": 23.8,
  "user_lng": 90.4,
  "radius_km": 3
}
```

### Get Active Users List
```
POST /api/radar/active-users
{
  "user_lat": 23.8,
  "user_lng": 90.4,
  "radius_km": 3
}
```

## Related Features

- **Live Map** (existing): View all requests and offers
- **Help Requests**: Post when you need assistance
- **Helper Offers**: Share your availability to help
- **SOS Feature**: Emergency signal to nearby helpers
- **Smart Suggestions**: AI-powered activity recommendations

## Feature Status

✅ **Fully Implemented and Tested**
- Backend API endpoints working
- Database model ready
- Frontend visualization complete
- Real-time updates functional
- User location tracking integrated

## Contact & Support

For issues or feature requests:
- Check AVAILABILITY_RADAR_GUIDE.md for technical details
- Review server logs for API errors
- Verify database connectivity
- Check browser console for client-side errors
