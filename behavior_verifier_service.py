# behavior_verifier_service.py
from difflib import SequenceMatcher
from datetime import datetime, timedelta

SCAM_KEYWORDS = [
    "bkash", "bKash", "nagad", "rocket", "otp", "pin", "password",
    "send money", "payment", "pay", "urgent cash", "agent", "verify account",
    "bank account", "card number"
]

def _normalize(text: str) -> str:
    return " ".join((text or "").lower().split())

def _contains_scam_keywords(text: str):
    t = _normalize(text)
    hits = [kw for kw in SCAM_KEYWORDS if kw.lower() in t]
    return hits

def _similarity(a: str, b: str) -> float:
    a = _normalize(a)
    b = _normalize(b)
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()

def verify_request_behavior(*, user, title: str, description: str, category: str, contact_info: str,
                            recent_same_user_requests):
    """
    recent_same_user_requests: list of Request objects from same user, recent time window
    Returns dict with is_flagged, risk_score, reasons, matched_request_id, should_block_guest
    """
    reasons = []
    risk = 0
    matched_id = None

    combined = f"{title}\n{description}\n{contact_info}\n{category}"

    # A) Scam keyword detection
    scam_hits = _contains_scam_keywords(combined)
    if scam_hits:
        reasons.append(f"Contains scam-like keywords: {', '.join(sorted(set(scam_hits)))}")
        risk += 60

    # B) Same-user duplicate detection
    candidate_text = f"{title}\n{description}"
    best_sim = 0.0
    best_req = None

    for r in (recent_same_user_requests or []):
        other_text = f"{getattr(r, 'title', '')}\n{getattr(r, 'description', '')}"
        sim = _similarity(candidate_text, other_text)
        if sim > best_sim:
            best_sim = sim
            best_req = r

    # Lowered threshold slightly (0.88 was too strict for small edits)
    if best_req is not None and best_sim >= 0.80:
        matched_id = best_req.id
        reasons.append(f"Possible duplicate of request #{best_req.id} (match {best_sim:.0%})")
        risk += 40

    # C) Trust-aware scoring
    trust = getattr(user, "trust_score", 50) or 50
    if trust < 30:
        reasons.append("Low trust score user")
        risk += 15
    elif trust > 80:
        risk -= 10  # small reduction

    risk = max(0, min(100, risk))

    # ✅ IMPORTANT: show duplicates in flagged list even if risk < 50
    is_flagged = (risk >= 50) or (matched_id is not None)

    # Your rule: only block emergency guest if scam keywords
    should_block_guest = bool(scam_hits)

    return {
        "is_flagged": is_flagged,
        "risk_score": risk,
        "reasons": reasons,
        "matched_request_id": matched_id,
        "should_block_guest": should_block_guest
    }
