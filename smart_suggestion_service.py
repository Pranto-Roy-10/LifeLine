"""
Smart Suggestion AI Service

Provides intelligent recommendations based on:
- Real-time weather data (OpenWeatherMap API)
- Current time and date
- Local demand patterns (user requests in database)
- User location and preferences
- ML-based matching algorithm
"""

import requests
import os
from datetime import datetime, timedelta
from typing import List, Dict, Any, Tuple, Optional
from math import radians, cos, sin, asin, sqrt
import json
from collections import defaultdict

# Environment variables for APIs
OPENWEATHER_API_KEY = os.getenv("OPENWEATHER_API_KEY", "")
SUGGESTION_RADIUS_KM = float(os.getenv("SUGGESTION_RADIUS_KM", "5"))  # Default 5km radius
# Note: DEMO_WEATHER is read at call-time in get_weather() to support dynamic env changes


class WeatherService:
    """Fetches and processes weather data from OpenWeatherMap API"""
    
    BASE_URL = "https://api.openweathermap.org/data/2.5/weather"
    
    @staticmethod
    def get_weather(lat: float, lng: float) -> Optional[Dict[str, Any]]:
        """
        Fetch weather data for given coordinates
        Returns: dict with weather info or None if API fails
        """
        # Re-read key in case environment changed after import
        key = os.getenv("OPENWEATHER_API_KEY", OPENWEATHER_API_KEY)
        demo_weather = os.getenv("DEMO_WEATHER", "0") == "1"  # Read at call-time
        
        if not key:
            if demo_weather:
                return {
                    "weather": [{"main": "Rain", "description": "light rain"}],
                    "main": {"temp": 29.0, "feels_like": 31.0, "humidity": 75},
                    "wind": {"speed": 3.2},
                    "visibility": 8000,
                    "rain": {"1h": 0.6},
                }
            print("[WeatherService] WARNING: OPENWEATHER_API_KEY not configured")
            return None
            
        try:
            params = {
                "lat": lat,
                "lon": lng,
                "appid": key,
                "units": "metric"
            }
            response = requests.get(WeatherService.BASE_URL, params=params, timeout=5)
            if response.status_code == 401 and demo_weather:
                # Demo fallback when key invalid
                return {
                    "weather": [{"main": "Rain", "description": "scattered showers"}],
                    "main": {"temp": 28.0, "feels_like": 30.0, "humidity": 78},
                    "wind": {"speed": 2.5},
                    "visibility": 7000,
                    "rain": {"1h": 0.4},
                }
            response.raise_for_status()
            return response.json()
        except Exception as e:
            print(f"[WeatherService] Error fetching weather: {e}")
            if demo_weather:
                return {
                    "weather": [{"main": "Clouds", "description": "demo cloudy"}],
                    "main": {"temp": 27.0, "feels_like": 29.0, "humidity": 70},
                    "wind": {"speed": 2.0},
                    "visibility": 9000
                }
            return None
    
    @staticmethod
    def extract_conditions(weather_data: Dict) -> Dict[str, Any]:
        """Extract meaningful weather conditions from API response"""
        if not weather_data:
            return {}
        
        return {
            "condition": weather_data.get("weather", [{}])[0].get("main", "Unknown"),
            "description": weather_data.get("weather", [{}])[0].get("description", ""),
            "temp": weather_data.get("main", {}).get("temp"),
            "feels_like": weather_data.get("main", {}).get("feels_like"),
            "humidity": weather_data.get("main", {}).get("humidity"),
            "wind_speed": weather_data.get("wind", {}).get("speed"),
            "visibility": weather_data.get("visibility"),
            "rainfall": weather_data.get("rain", {}).get("1h", 0),
        }


class DemandAnalyzer:
    """Analyzes local demand patterns from database"""
    
    # Weather-based category suggestions
    WEATHER_CATEGORY_MAP = {
        "Rain": ["umbrella", "waterproof", "ride", "groceries"],
        "Drizzle": ["umbrella", "ride", "groceries"],
        "Thunderstorm": ["emergency", "ride", "medicine"],
        "Snow": ["ride", "groceries", "emergency"],
        "Clear": ["outdoor_activity", "sports", "gardening"],
        "Clouds": ["outdoor_activity", "repair", "tutoring"],
        "Mist": ["ride", "groceries"],
        "Smoke": ["medicine", "air_purifier"],
        "Haze": ["medicine", "air_purifier"],
        "Dust": ["medicine", "cleaning_supplies"],
        "Fog": ["ride", "groceries"],
        "Sand": ["emergency", "ride"],
        "Ash": ["medicine", "emergency"],
        "Squall": ["emergency"],
        "Tornado": ["emergency"],
    }
    
    # Temperature-based suggestions
    TEMP_SUGGESTIONS = {
        "hot": ["water_delivery", "cooling_fan", "ice_cream", "beverages"],
        "cold": ["blanket", "heater", "warm_clothes", "tea_coffee"],
        "moderate": ["groceries", "outdoor_activity", "repair"],
    }
    
    # Time-based suggestions
    TIME_SUGGESTIONS = {
        "morning": ["groceries", "breakfast", "delivery", "ride_to_work"],
        "afternoon": ["lunch", "repair", "tutoring", "outdoor_activity"],
        "evening": ["dinner", "groceries", "delivery", "ride_back_home"],
        "night": ["emergency", "medicine", "security", "ride"],
    }
    
    @staticmethod
    def get_time_period() -> str:
        """Get current time period"""
        hour = datetime.now().hour
        if 6 <= hour < 12:
            return "morning"
        elif 12 <= hour < 17:
            return "afternoon"
        elif 17 <= hour < 21:
            return "evening"
        else:
            return "night"
    
    @staticmethod
    def categorize_temperature(temp: Optional[float]) -> str:
        """Categorize temperature"""
        if temp is None:
            return "moderate"
        if temp > 28:
            return "hot"
        elif temp < 10:
            return "cold"
        else:
            return "moderate"
    
    @staticmethod
    def get_weather_suggestions(weather_condition: str) -> List[str]:
        """Get categories suggested by weather condition"""
        suggestions = set()
        for weather, categories in DemandAnalyzer.WEATHER_CATEGORY_MAP.items():
            if weather.lower() in weather_condition.lower():
                suggestions.update(categories)
        return list(suggestions)
    
    @staticmethod
    def get_time_suggestions(time_period: str) -> List[str]:
        """Get categories suggested by time"""
        return DemandAnalyzer.TIME_SUGGESTIONS.get(time_period, [])
    
    @staticmethod
    def get_temp_suggestions(temp_category: str) -> List[str]:
        """Get categories suggested by temperature"""
        return DemandAnalyzer.TEMP_SUGGESTIONS.get(temp_category, [])


class LocationMatcher:
    """Matches requests based on location proximity"""
    
    @staticmethod
    def haversine_distance(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
        """
        Calculate the great circle distance between two points 
        on the earth (specified in decimal degrees)
        Returns distance in kilometers
        """
        # Convert decimal degrees to radians
        lat1_rad, lng1_rad = radians(lat1), radians(lng1)
        lat2_rad, lng2_rad = radians(lat2), radians(lng2)
        
        # Haversine formula
        dlat = lat2_rad - lat1_rad
        dlng = lng2_rad - lng1_rad
        a = sin(dlat/2)**2 + cos(lat1_rad) * cos(lat2_rad) * sin(dlng/2)**2
        c = 2 * asin(sqrt(a))
        r = 6371  # Radius of earth in kilometers
        return c * r
    
    @staticmethod
    def get_nearby_requests(
        db,
        Request,
        user_lat: float,
        user_lng: float,
        radius_km: float = SUGGESTION_RADIUS_KM,
        status: str = "open",
        exclude_user_id: Optional[int] = None,
        limit: int = 20
    ) -> List[Dict[str, Any]]:
        """
        Get requests near user location within specified radius
        Returns list of request dicts with distance info
        """
        try:
            # Fetch all requests with matching status
            query = Request.query.filter(Request.status == status)
            if exclude_user_id:
                query = query.filter(Request.user_id != exclude_user_id)
            
            all_requests = query.all()
            
            # Filter by distance
            nearby = []
            for req in all_requests:
                if req.lat is None or req.lng is None:
                    continue
                    
                distance = LocationMatcher.haversine_distance(
                    user_lat, user_lng, req.lat, req.lng
                )
                
                if distance <= radius_km:
                    req_dict = req.to_dict(include_user=True)
                    req_dict["distance_km"] = round(distance, 2)
                    nearby.append(req_dict)
            
            # Sort by distance and limit
            nearby.sort(key=lambda x: x["distance_km"])
            return nearby[:limit]
            
        except Exception as e:
            print(f"[LocationMatcher] Error finding nearby requests: {e}")
            return []


class RecommendationEngine:
    """ML-based recommendation engine"""
    
    @staticmethod
    def calculate_relevance_score(
        request: Dict[str, Any],
        weather_conditions: Dict[str, Any],
        time_period: str,
        distance_km: float,
        user_skills: Optional[List[str]] = None
    ) -> float:
        """
        Calculate relevance score (0-100) for a request
        Based on multiple factors with weighted scoring
        """
        score = 0.0
        
        # Distance scoring (closer is better, max 20 points)
        distance_score = max(0, 20 - (distance_km * 4))
        score += distance_score
        
        # Urgency scoring (max 20 points)
        urgency = request.get("urgency", "normal").lower()
        urgency_scores = {"emergency": 20, "high": 15, "normal": 10, "low": 5}
        score += urgency_scores.get(urgency, 10)
        
        # Category-weather match (max 25 points)
        category = request.get("category", "").lower()
        weather_match_score = 0
        
        weather_condition = weather_conditions.get("condition", "").lower()
        description = weather_conditions.get("description", "").lower()
        
        # Rain-related matches
        if "rain" in weather_condition or "rain" in description:
            if any(w in category for w in ["umbrella", "waterproof", "ride", "delivery"]):
                weather_match_score = 25
        
        # Temperature-based matches
        temp = weather_conditions.get("temp")
        if temp and temp > 28:
            if any(w in category for w in ["water", "cooling", "ice", "beverage"]):
                weather_match_score = 20
        elif temp and temp < 10:
            if any(w in category for w in ["blanket", "heater", "warm", "clothes"]):
                weather_match_score = 20
        
        score += weather_match_score
        
        # Time alignment (max 15 points)
        time_alignment = RecommendationEngine._calculate_time_alignment(
            request.get("time_window", ""), time_period
        )
        score += time_alignment
        
        # Status freshness (max 10 points) - newer requests are more relevant
        created_at = request.get("created_at", 0)
        if created_at:
            age_hours = (datetime.utcnow().timestamp() - created_at) / 3600
            freshness = max(0, 10 - (age_hours / 24))
            score += freshness
        
        # Cap score at 100
        return min(100, score)
    
    @staticmethod
    def _calculate_time_alignment(time_window: str, current_period: str) -> float:
        """Calculate time window alignment score"""
        time_window = (time_window or "").lower()
        
        if "anytime" in time_window or "flexible" in time_window:
            return 15
        elif current_period in time_window:
            return 12
        else:
            return 5


class SmartSuggestionService:
    """Main service orchestrating smart suggestions"""
    
    @staticmethod
    def get_suggestions(
        db,
        Request,
        user_id: int,
        user_lat: float,
        user_lng: float,
        user_skills: Optional[List[str]] = None,
        max_suggestions: int = 5,
        include_explanation: bool = True
    ) -> List[Dict[str, Any]]:
        """
        Get smart suggestions for a user
        
        Args:
            db: SQLAlchemy database instance
            Request: Request model class
            user_id: ID of user requesting suggestions
            user_lat: User's latitude
            user_lng: User's longitude
            user_skills: Optional list of user skills
            max_suggestions: Maximum number of suggestions to return
            include_explanation: Whether to include detailed explanation
            
        Returns:
            List of suggestion dicts with requests and explanations
        """
        try:
            suggestions = []
            
            # 1. Fetch weather data
            weather_data = WeatherService.get_weather(user_lat, user_lng)
            weather_conditions = WeatherService.extract_conditions(weather_data)
            
            # 2. Get time period
            time_period = DemandAnalyzer.get_time_period()
            
            # 3. Get nearby requests
            nearby_requests = LocationMatcher.get_nearby_requests(
                db, Request, user_lat, user_lng,
                exclude_user_id=user_id,
                limit=20
            )
            
            # 4. Score each request
            scored_requests = []
            for req in nearby_requests:
                score = RecommendationEngine.calculate_relevance_score(
                    req, weather_conditions, time_period,
                    req.get("distance_km", 0),
                    user_skills
                )
                
                explanation = ""
                if include_explanation:
                    explanation = SmartSuggestionService._generate_explanation(
                        req, weather_conditions, time_period, score
                    )
                
                scored_requests.append({
                    "request": req,
                    "score": score,
                    "explanation": explanation
                })
            
            # 5. Sort by score and return top suggestions
            scored_requests.sort(key=lambda x: x["score"], reverse=True)
            
            for item in scored_requests[:max_suggestions]:
                suggestion = {
                    "id": item["request"]["id"],
                    "title": item["request"]["title"],
                    "category": item["request"]["category"],
                    "description": item["request"]["description"],
                    "urgency": item["request"]["urgency"],
                    "distance_km": item["request"]["distance_km"],
                    "relevance_score": round(item["score"], 2),
                    "explanation": item["explanation"],
                    "request_full": item["request"]
                }
                suggestions.append(suggestion)
            
            return suggestions
            
        except Exception as e:
            print(f"[SmartSuggestionService] Error generating suggestions: {e}")
            return []
    
    @staticmethod
    def _generate_explanation(
        request: Dict[str, Any],
        weather_conditions: Dict[str, Any],
        time_period: str,
        score: float
    ) -> str:
        """Generate human-readable explanation for suggestion"""
        reasons = []
        
        # Weather reason
        weather = weather_conditions.get("condition", "")
        if weather:
            category = request.get("category", "").lower()
            if "rain" in weather.lower() and any(w in category for w in ["umbrella", "waterproof", "ride", "delivery"]):
                # Natural language explanation aligned with UI example
                reasons.append("It’s going to rain. Someone nearby requested umbrella delivery.")
        
        # Distance reason
        distance = request.get("distance_km", 0)
        if distance < 1:
            reasons.append("Very close to your location")
        elif distance < 3:
            reasons.append(f"Only {distance}km away")
        
        # Urgency reason
        urgency = request.get("urgency", "").lower()
        if urgency == "emergency":
            reasons.append("Marked as emergency")
        elif urgency == "high":
            reasons.append("High urgency")
        
        # Time reason
        time_window = request.get("time_window", "").lower()
        if time_period in time_window:
            reasons.append(f"Needed {time_period}")
        
        if not reasons:
            reasons.append("Good match for you")
        
        return " • ".join(reasons)
    
    @staticmethod
    def get_trending_categories(
        db,
        Request,
        hours: int = 24,
        limit: int = 5
    ) -> List[Dict[str, Any]]:
        """Get trending help categories in the system"""
        try:
            cutoff_time = datetime.utcnow() - timedelta(hours=hours)
            
            # Count requests by category
            from sqlalchemy import func
            trending = db.session.query(
                Request.category,
                func.count(Request.id).label("count")
            ).filter(
                Request.created_at >= cutoff_time,
                Request.status == "open"
            ).group_by(
                Request.category
            ).order_by(
                func.count(Request.id).desc()
            ).limit(limit).all()
            
            return [
                {"category": t[0], "count": t[1]}
                for t in trending
            ]
        except Exception as e:
            print(f"[SmartSuggestionService] Error getting trending categories: {e}")
            return []
