"""
Shop Suggestion Service

Analyzes user help requests and suggests nearby shops/service providers
based on keywords from title and description.
"""

import math
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime

# Haversine distance calculation
def haversine_distance_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Calculate distance between two lat/lng points in km"""
    R = 6371  # Earth's radius in km
    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)
    delta_lat = math.radians(lat2 - lat1)
    delta_lng = math.radians(lng2 - lng1)
    
    a = math.sin(delta_lat / 2) ** 2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(delta_lng / 2) ** 2
    c = 2 * math.asin(math.sqrt(a))
    return R * c


# Keyword mappings for shop types
SHOP_TYPE_KEYWORDS = {
    "tire_shop": [
        "tire", "tyre", "puncture", "wheel", "flat", "burst", "rubber",
        "car repair", "vehicle repair", "bike repair", "motorcycle",
        "automotive", "garage", "mechanic"
    ],
    "hospital": [
        "hospital", "emergency", "doctor", "doctor visit", "accident", "injury",
        "injured", "medical", "urgent medical", "healthcare", "clinic",
        "patient", "surgery", "operation", "icu", "trauma", "ill", "sick"
    ],
    "pharmacy": [
        "medicine", "pharmacy", "drug", "prescription", "tablet", "injection",
        "medical supply", "doctor prescribed", "medication", "antibiotic",
        "painkiller", "fever", "medicine needed", "doctor", "pills"
    ],
    "grocery": [
        "grocery", "food", "groceries", "rice", "oil", "sugar", "salt",
        "vegetables", "fruits", "provisions", "staples", "ration",
        "shopping", "supplies", "pantry", "daily needs"
    ],
    "stationary": [
        "stationary", "stationery", "pen", "paper", "notebook", "book", "pencil",
        "supplies", "office supplies", "school supplies", "printing",
        "copy", "xerox"
    ],
    "restaurant": [
        "food delivery", "restaurant", "lunch", "dinner", "meal", "food",
        "eating", "catering", "hungry"
    ],
    "school": [
        "school", "tuition", "tutoring", "tutor", "education", "coaching",
        "class", "academic"
    ],
    "salon": [
        "salon", "haircut", "hair", "beauty", "barber", "spa", "grooming",
        "makeup", "cosmetics"
    ]
}

# Reverse mapping for quick lookup
KEYWORD_TO_SHOP_TYPE = {}
for shop_type, keywords in SHOP_TYPE_KEYWORDS.items():
    for keyword in keywords:
        if keyword not in KEYWORD_TO_SHOP_TYPE:
            KEYWORD_TO_SHOP_TYPE[keyword] = []
        KEYWORD_TO_SHOP_TYPE[keyword].append(shop_type)


class ShopSuggestionAnalyzer:
    """Analyzes help requests and suggests relevant shops"""
    
    @staticmethod
    def extract_keywords(text: str) -> List[str]:
        """Extract and normalize keywords from text"""
        if not text:
            return []
        
        # Convert to lowercase and split
        words = text.lower().split()
        # Remove common stop words
        stop_words = {"i", "need", "help", "a", "an", "the", "for", "on", "at", "is", "are", "to", "in", "of", "and", "or", "but"}
        keywords = [w.strip(".,!?;:") for w in words if w.strip(".,!?;:") not in stop_words]
        return keywords
    
    @staticmethod
    def analyze_request(title: str, description: str = "", category: str = "") -> Dict[str, Any]:
        """
        Analyze a help request and determine matching shop types
        
        Args:
            title: Request title
            description: Request description
            category: Request category from form
            
        Returns:
            Dict with matched shop types and confidence scores
        """
        all_text = f"{title} {description} {category}".lower()
        keywords = ShopSuggestionAnalyzer.extract_keywords(all_text)
        
        # Count matches for each shop type - EXACT MATCHES ONLY
        shop_type_scores = {}
        
        for keyword in keywords:
            # Check exact match only (no partial matches to avoid false positives)
            if keyword in KEYWORD_TO_SHOP_TYPE:
                matching_types = KEYWORD_TO_SHOP_TYPE[keyword]
                for shop_type in matching_types:
                    shop_type_scores[shop_type] = shop_type_scores.get(shop_type, 0) + 2
        
        # Category-based direct mapping (strong signal)
        category_map = {
            "repair": ["tire_shop"],
            "medicine": ["pharmacy", "hospital"],
            "food": ["grocery", "restaurant"],
            "tutoring": ["school"],
        }
        
        if category in category_map:
            for shop_type in category_map[category]:
                shop_type_scores[shop_type] = shop_type_scores.get(shop_type, 0) + 5  # High boost
        
        # Sort by score
        sorted_types = sorted(shop_type_scores.items(), key=lambda x: x[1], reverse=True)
        
        return {
            "matched_shop_types": [st[0] for st in sorted_types],
            "scores": dict(sorted_types),
            "keywords": keywords,
        }
    
    @staticmethod
    def get_nearby_shops(
        shops: List[Any],  # Shop model instances
        user_lat: float,
        user_lng: float,
        matched_shop_types: List[str],
        max_distance_km: float = 10.0,
        max_results: int = 5
    ) -> List[Dict[str, Any]]:
        """
        Find nearby shops matching the requested types
        
        Args:
            shops: List of Shop database objects
            user_lat: User's latitude
            user_lng: User's longitude
            matched_shop_types: Shop types to match (in priority order)
            max_distance_km: Maximum distance to consider
            max_results: Max number of suggestions to return
            
        Returns:
            List of shop dicts with distance, sorted by distance
        """
        suggestions = []
        
        for shop in shops:
            # Check if shop type matches
            if shop.shop_type not in matched_shop_types:
                continue
            
            # Calculate distance
            try:
                distance = haversine_distance_km(user_lat, user_lng, shop.lat, shop.lng)
            except Exception:
                continue
            
            # Filter by max distance
            if distance > max_distance_km:
                continue
            
            # Add to suggestions with distance
            shop_dict = shop.to_dict(user_lat=user_lat, user_lng=user_lng)
            shop_dict["distance_km"] = distance
            shop_dict["priority_index"] = matched_shop_types.index(shop.shop_type) if shop.shop_type in matched_shop_types else 999
            suggestions.append(shop_dict)
        
        # Sort: first by priority_index, then by distance
        suggestions.sort(key=lambda x: (x.get("priority_index", 999), x.get("distance_km", float("inf"))))
        
        return suggestions[:max_results]
