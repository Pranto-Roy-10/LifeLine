# Smart Suggestion AI - Quick Start Guide

## 🚀 Get Started in 3 Minutes

### Step 1: Get Your API Key (1 minute)
```bash
# Visit: https://openweathermap.org/api
# Sign up for free tier (60 calls/minute is plenty)
# Copy your API key
```

### Step 2: Set Environment Variable (1 minute)

**Windows (PowerShell):**
```powershell
$env:OPENWEATHER_API_KEY = "your_api_key_here"
```

**Windows (Command Prompt):**
```cmd
set OPENWEATHER_API_KEY=your_api_key_here
```

**Linux/Mac:**
```bash
export OPENWEATHER_API_KEY="your_api_key_here"
```

**Or create `.env` file:**
```
OPENWEATHER_API_KEY=your_api_key_here
SUGGESTION_RADIUS_KM=5
```

### Step 3: Enable Location (1 minute)
- Open the app in your browser
- Allow location permission when prompted
- Make sure your profile has location enabled

## 📍 View Smart Suggestions

```
http://localhost:5000/suggestions
```

## 🧪 Test the API

### Get Suggestions
```bash
curl -X POST http://localhost:5000/api/suggestions \
  -H "Content-Type: application/json" \
  -d '{"lat": 40.7128, "lng": -74.0060, "max_suggestions": 5}'
```

### Get Weather
```bash
curl "http://localhost:5000/api/weather?lat=40.7128&lng=-74.0060"
```

### Get Trending Categories
```bash
curl "http://localhost:5000/api/trending-categories?hours=24&limit=5"
```

## 🎯 How It Works

The AI analyzes **four factors** to recommend help actions:

1. **📍 Location** - Finds requests within 5km
2. **🌤️ Weather** - "Rain expected? Suggest umbrella delivery"
3. **⏰ Time** - "Morning? Suggest grocery delivery"
4. **⚡ Urgency** - High-urgency requests ranked higher

## 💡 Example Scenario

**Current Conditions:**
- You're at: (40.7128, -74.0060)
- Weather: Rain expected
- Time: 6 PM
- Temperature: 12°C

**System Recommends:**
```
🔴 Urgent: "Need umbrella delivery" 1.2km away
   Reason: Rain expected • Nearby • High urgency
   Relevance: 92%

🟡 High: "Groceries needed for dinner" 2.5km away
   Reason: Evening time • Someone nearby
   Relevance: 78%
```

## 📊 Scoring Breakdown

Each suggestion gets a score (0-100) based on:

| Factor | Max Points | How It Works |
|--------|-----------|--------------|
| Distance | 20 | Closer = higher |
| Urgency | 20 | Emergency > High > Normal > Low |
| Weather Match | 25 | Does category match weather? |
| Time Alignment | 15 | Does request time match now? |
| Freshness | 10 | Newer requests score higher |

## ⚙️ Configuration

### Adjust Search Radius
```python
# In .env file:
SUGGESTION_RADIUS_KM=10  # Search up to 10km instead of 5km
```

### Adjust Suggestion Count
```python
# In app.py, line with @app.route("/api/suggestions"):
max_suggestions = min(int(data.get("max_suggestions", 10)), 15)  # Default 10, max 15
```

### Customize Weather Mappings
```python
# In smart_suggestion_service.py, DemandAnalyzer class:
WEATHER_CATEGORY_MAP = {
    "Rain": ["umbrella", "waterproof", "ride", "groceries"],
    # Add more mappings...
}
```

## 🎨 Integrate Into Your Template

Add widget to any page:

```html
{% include 'smart_suggestions_widget.html' %}
```

Or embed on dashboard:

```html
<!-- In templates/base.html or dashboard -->
<div id="suggestions-container">
  {% include 'smart_suggestions_widget.html' %}
</div>
```

## 📱 Mobile Testing

The widget is fully responsive:
- ✅ Mobile phones
- ✅ Tablets
- ✅ Desktops
- ✅ All browsers (Chrome, Firefox, Safari, Edge)

## 🐛 Troubleshooting

### No suggestions appearing?
```
✓ Check user has location enabled
✓ Check OpenWeatherMap API key is set
✓ Check there are open requests in database
✓ Check browser console for JavaScript errors
```

### Weather not showing?
```
✓ Verify OpenWeatherMap API key is correct
✓ Check API rate limits (60 calls/min free tier)
✓ Ensure coordinates are valid (lat/lng)
```

### Slow performance?
```
✓ Add database indexes (see SMART_SUGGESTIONS_GUIDE.md)
✓ Increase SUGGESTION_RADIUS_KM if no nearby requests
✓ Reduce MAX_SUGGESTIONS_PER_REQUEST to 3
```

## 📚 Full Documentation

For more details, see:
- `SMART_SUGGESTIONS_GUIDE.md` - Comprehensive guide
- `IMPLEMENTATION_SUMMARY.md` - Complete feature list
- `smart_suggestion_service.py` - Source code with comments

## 🎯 Next Steps

1. ✅ Get OpenWeatherMap API key
2. ✅ Set environment variable
3. ✅ Test at `/suggestions`
4. ✅ Create sample requests with locations
5. ✅ Enable geolocation in browser
6. ✅ See AI suggestions appear!

## 💬 Tips & Tricks

**For Better Suggestions:**
- Add location data to all requests
- Include urgency level (emergency, high, normal, low)
- Specify time window (anytime, today, this week, etc.)
- Add descriptive titles and categories

**For Production:**
- Add caching to reduce API calls
- Implement suggestion logging for analytics
- Send suggestions via push notifications
- Create weekly digest emails

## 🚀 API Endpoints Quick Reference

```
POST   /api/suggestions           # Get AI suggestions
GET    /api/weather              # Get weather data
GET    /api/trending-categories  # Get trending categories
GET    /api/suggestion-insights  # Get user insights
GET    /api/nearby-requests      # Get nearby requests
GET    /suggestions              # Dashboard page
```

---

**Need help?** Check the comprehensive guide:
```bash
cat SMART_SUGGESTIONS_GUIDE.md
```

**Ready to go?** Visit:
```
http://localhost:5000/suggestions
```
