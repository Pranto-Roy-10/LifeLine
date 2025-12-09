import re
from textblob import TextBlob

# Keywords that might indicate spam or low-effort fake reviews
SUSPICIOUS_KEYWORDS = [
    "scam", "fake", "money", "fraud", "bot", 
    "test", "asdf", "1234", "...", "no comment"
]

def analyze_review_quality(text, rating):
    """
    AI Logic to detect fake/biased feedback and analyze sentiment.
    Returns a dictionary with analysis results.
    """
    # Safety check if text is None
    if text is None:
        text = ""
        
    text = text.lower().strip()
    is_suspicious = False
    flag_reason = None
    
    # 1. Keyword Matching (Spam Detection)
    if any(word in text for word in SUSPICIOUS_KEYWORDS):
        is_suspicious = True
        flag_reason = "Contains suspicious keywords"

    # 2. Length Check (Low effort detection)
    if len(text) < 5:
        is_suspicious = True
        flag_reason = "Review too short"

    # 3. Sentiment Analysis using TextBlob
    # Polarity ranges from -1 (Negative) to +1 (Positive)
    blob = TextBlob(text)
    sentiment_score = blob.sentiment.polarity

    # 4. Consistency Check (AI Logic)
    # Detects if user gave 5 stars but wrote a hate comment, or 1 star and wrote "Great job"
    if rating >= 4 and sentiment_score < -0.3:
        is_suspicious = True
        flag_reason = "Rating (High) contradicts Text Sentiment (Negative)"
    elif rating <= 2 and sentiment_score > 0.5:
        is_suspicious = True
        flag_reason = "Rating (Low) contradicts Text Sentiment (Positive)"

    return {
        "sentiment_score": sentiment_score,
        "is_suspicious": is_suspicious,
        "flag_reason": flag_reason
    }

def calculate_reputation_points(rating, is_suspicious):
    """
    Calculates points to add to the Kindness Score.
    """
    if is_suspicious:
        return 0 # No points for fake/suspicious reviews
    
    # Base points for helping + Bonus for high rating
    base_points = 10
    rating_bonus = (rating * 2) # e.g., 5 stars = +10 points
    
    return base_points + rating_bonus