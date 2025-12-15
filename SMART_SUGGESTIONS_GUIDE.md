# Smart Suggestion AI System

## Overview

The Smart Suggestion AI system provides intelligent, context-aware recommendations for help actions based on real-time data:

- **Weather Data** - OpenWeatherMap API integration
- **Time & Scheduling** - Current time-based suggestions
- **Local Demand** - Analyzes nearby user requests
- **ML-Based Scoring** - Relevance scoring algorithm
- **User Profile** - Trust and kindness scores

## Features

### 1. AI-Powered Recommendations
- Analyzes 20+ nearby requests
- Generates relevance scores (0-100)
- Provides human-readable explanations
- Updates in real-time

### 2. Weather-Aware Suggestions
```
Example: "It's going to rain. Someone nearby requested umbrella delivery."
```

**Weather-Category Mapping:**
- Rain/Drizzle → Umbrellas, rides, groceries
- Thunder → Emergencies, medicine
- Snow → Rides, groceries
- Clear → Outdoor activities, sports
- High Temp → Water, cooling supplies
- Low Temp → Blankets, heaters, warm clothes

### 3. Time-Based Opportunities
- Morning: Groceries, breakfast delivery, rides to work
- Afternoon: Lunch, repairs, tutoring, outdoor activities
- Evening: Dinner delivery, groceries, rides home
- Night: Emergencies, medicine, security

### 4. Trending Categories
- Real-time view of what help is most needed
- Filtered by time period (24h, 7d, 30d)
- Shows request counts by category

### 5. User Insights Dashboard
```
GET /api/suggestion-insights
```

Returns:
- Trust and kindness scores
- User badge/achievement
- Nearby request statistics
- Trending categories
- Weather-based opportunities

## Setup

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

Requirements include:
- `requests>=2.31.0` - For API calls
- All other LifeLine dependencies

### 2. Configure OpenWeatherMap API

Get your free API key from: https://openweathermap.org/api

Set environment variable:
```bash
export OPENWEATHER_API_KEY="your_api_key_here"
```

Or set it in your `.env` file (if using python-dotenv):
```
OPENWEATHER_API_KEY=your_api_key_here
SUGGESTION_RADIUS_KM=5
```

### 3. Enable User Geolocation

The system requires user location. Ensure:
1. Users have granted geolocation permission
2. Location is captured in user profile (lat/lng)
3. Requests have location data (lat/lng)

## API Endpoints

### Get Smart Suggestions
```
POST /api/suggestions
Content-Type: application/json

{
    "lat": 40.7128,
    "lng": -74.0060,
    "max_suggestions": 5
}
```

**Response:**
```json
{
    "success": true,
    "suggestions": [
        {
            "id": 123,
            "title": "Need umbrella delivery",
            "category": "umbrella",
            "description": "...",
            "urgency": "high",
            "distance_km": 0.8,
            "relevance_score": 92.5,
            "explanation": "Rain expected • Only 0.8km away • High urgency"
        }
    ],
    "generated_at": "2025-12-15T10:30:00"
}
```

### Get Current Weather
```
GET /api/weather?lat=40.7128&lng=-74.0060
```

**Response:**
```json
{
    "success": true,
    "weather": {
        "condition": "Rain",
        "description": "light rain",
        "temp": 12.5,
        "feels_like": 11.2,
        "humidity": 85,
        "wind_speed": 3.5,
        "visibility": 8000,
        "rainfall": 0.5
    }
}
```

### Get Trending Categories
```
GET /api/trending-categories?hours=24&limit=5
```

**Response:**
```json
{
    "success": true,
    "trending_categories": [
        {"category": "groceries", "count": 15},
        {"category": "medicine", "count": 12},
        {"category": "ride", "count": 10}
    ],
    "period_hours": 24
}
```

### Get User Insights
```
GET /api/suggestion-insights
```

**Response:**
```json
{
    "success": true,
    "insights": {
        "total_nearby_requests": 47,
        "weather_summary": {
            "condition": "Clouds",
            "temperature": 15.2,
            "humidity": 72
        },
        "current_time_period": "afternoon",
        "trending_categories": [...],
        "user_score": {
            "trust_score": 250,
            "kindness_score": 180,
            "badge": "Gold Helper"
        },
        "recommendations": {
            "weather_opportunities": ["outdoor_activity", "repair"],
            "time_opportunities": ["lunch", "repair", "tutoring"],
            "temperature_opportunities": ["repair", "outdoor_activity"]
        }
    }
}
```

### Get Nearby Requests
```
GET /api/nearby-requests?limit=10
```

## Frontend Integration

### 1. Include the Smart Suggestions Widget

In your template, include:
```html
{% include 'smart_suggestions_widget.html' %}
```

### 2. View the Full Dashboard

Navigate to:
```
/suggestions
```

This displays:
- AI suggestions with explanations
- Recent nearby requests
- Your profile stats
- Trending help categories

### 3. JavaScript API

The widget exposes `SmartSuggestions` object:

```javascript
// Manually refresh suggestions
SmartSuggestions.loadSuggestions(lat, lng);

// Load weather
SmartSuggestions.loadWeather(lat, lng);

// Initialize (auto-called on DOM ready)
SmartSuggestions.init();
```

## Scoring Algorithm

The relevance score (0-100) is calculated using:

1. **Distance Score** (0-20 points)
   - Closer = higher score
   - Max at 0km, decreases with distance

2. **Urgency Score** (0-20 points)
   - Emergency: 20 pts
   - High: 15 pts
   - Normal: 10 pts
   - Low: 5 pts

3. **Weather Match** (0-25 points)
   - Category-weather alignment
   - Example: Rain + Umbrella = 25 pts

4. **Time Alignment** (0-15 points)
   - Request time_window vs current period
   - Flexible times get max points

5. **Freshness** (0-10 points)
   - Newer requests score higher
   - Decreases over 24 hours

**Total: 0-100 (capped)**

## Database Requirements

Ensure your Request model has:
- `lat`, `lng` - Location coordinates
- `category` - Help category
- `urgency` - Low/Normal/High/Emergency
- `time_window` - Anytime, morning, afternoon, etc.
- `created_at` - Request creation timestamp
- `status` - open/claimed/closed
- `user_id` - Requestor

And User model has:
- `lat`, `lng` - User location
- `trust_score` - Integer
- `kindness_score` - Integer

## Error Handling

The system gracefully handles:
- Missing OpenWeatherMap API key (disabled weather features)
- Missing user location (uses provided coordinates)
- API failures (silent fallback, logs errors)
- Invalid coordinates (returns empty suggestions)

## Performance Considerations

- Suggestions query limited to 20 requests per call
- Returned suggestions limited to 5 (configurable)
- Weather API cached at browser level
- Trending categories query optimized with indexes

### Recommended Indexes
```sql
CREATE INDEX idx_request_status ON requests(status);
CREATE INDEX idx_request_location ON requests(lat, lng);
CREATE INDEX idx_request_created ON requests(created_at);
CREATE INDEX idx_request_category ON requests(category);
CREATE INDEX idx_user_location ON user(lat, lng);
```

## Testing

### Test Weather API
```bash
curl "https://api.openweathermap.org/data/2.5/weather?lat=40.7128&lng=-74.0060&appid=YOUR_KEY"
```

### Test Suggestions
```bash
curl -X POST http://localhost:5000/api/suggestions \
  -H "Content-Type: application/json" \
  -d '{"lat": 40.7128, "lng": -74.0060}'
```

### Test Without API Key
The system works without OpenWeatherMap API key, but weather-based suggestions are disabled.

## Future Enhancements

1. **Machine Learning**
   - User preference learning
   - Personalized category weighting
   - Completion rate prediction

2. **Advanced Filtering**
   - User skill matching
   - Reputation filtering
   - Language preferences

3. **Batch Notifications**
   - Send suggestions via push notifications
   - Email digest of opportunities
   - Smart timing based on user activity

4. **Social Features**
   - Show helper profiles
   - Historical ratings
   - Success stories

5. **Analytics**
   - Track suggestion acceptance rate
   - Most helpful categories
   - User engagement metrics

## Troubleshooting

**No suggestions appearing:**
- Check user has location enabled
- Verify OpenWeatherMap API key is set
- Check database has nearby open requests
- Look at browser console for JavaScript errors

**Weather not showing:**
- Verify OpenWeatherMap API key
- Check API rate limits (60 calls/min for free tier)
- Ensure coordinates are valid

**Slow suggestions:**
- Add database indexes (see Performance section)
- Increase `SUGGESTION_RADIUS_KM` if no nearby requests
- Optimize Request query with pagination

**High API costs:**
- Implement suggestion caching (5-10 min)
- Batch weather API calls
- Use free tier API key with rate limiting

## Code Structure

```
smart_suggestion_service.py
├── WeatherService
│   └── get_weather()
│   └── extract_conditions()
├── DemandAnalyzer
│   ├── get_weather_suggestions()
│   ├── get_time_suggestions()
│   ├── get_temp_suggestions()
│   └── get_time_period()
├── LocationMatcher
│   ├── haversine_distance()
│   └── get_nearby_requests()
├── RecommendationEngine
│   ├── calculate_relevance_score()
│   └── _calculate_time_alignment()
└── SmartSuggestionService (Orchestrator)
    ├── get_suggestions()
    ├── _generate_explanation()
    └── get_trending_categories()
```

## License

Part of the LifeLine community help platform.
