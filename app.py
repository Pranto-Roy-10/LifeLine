# -*- coding: utf-8 -*-

from datetime import datetime, timedelta
import os
import math
import time
import random
import string


from reputation_service import analyze_review_quality, calculate_reputation_points
from smart_suggestion_service import (
    SmartSuggestionService, WeatherService, LocationMatcher, DemandAnalyzer
)
from sqlalchemy import func
from flask_cors import CORS

from flask import (
    Flask, render_template, request,
    redirect, url_for, session, flash, jsonify
)
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from flask_migrate import Migrate

from flask_mail import Mail, Message
from email_config import EMAIL_ADDRESS, EMAIL_PASSWORD

import firebase_admin
from firebase_admin import credentials, auth as firebase_auth, messaging


from flask_jwt_extended import (
    JWTManager, create_access_token, jwt_required, get_jwt_identity
)
from flask_socketio import SocketIO, emit, join_room, leave_room

from behavior_verifier_service import verify_request_behavior



# ------------------ FCM INIT ------------------
# Initialize Firebase Admin SDK ONCE
try:
    if not firebase_admin._apps:
        cred = credentials.Certificate("firebase-service-account.json")
        firebase_admin.initialize_app(cred)
        print("[FCM] Firebase Admin initialized.")
    else:
        print("[FCM] Firebase Admin already initialized.")
except Exception as e:
    print(f"[FCM] Error initializing Firebase Admin SDK: {e}")
    print("[FCM] Push notifications will be disabled on this machine.")


# Translation libraries: try official Google Cloud first, fallback to googletrans
USE_GOOGLE_CLOUD_TRANSLATE = os.getenv("USE_GOOGLE_CLOUD_TRANSLATE", "0") == "1"
try:
    if USE_GOOGLE_CLOUD_TRANSLATE:
        from google.cloud import translate_v2 as translate
        gcloud_translate_client = translate.Client()
    else:
        raise Exception("skip gcloud")
except Exception:
    # fallback to googletrans (no credentials required)
    try:
        from googletrans import Translator

        gt_translator = Translator()
    except Exception:
        gt_translator = None

# email -> {"request_times": [...], "last_code_sent_at": datetime}
otp_store = {}

app = Flask(__name__)
CORS(app, supports_credentials=True)




# ------------------ CONFIG ------------------
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-secret-change-me")
app.config["JWT_SECRET_KEY"] = os.getenv("JWT_SECRET_KEY", "dev-jwt-secret")

# SQLite for now (file lifeline.db in project root).
# app.config["SQLALCHEMY_DATABASE_URI"] = "postgresql+psycopg2://postgres:error101@localhost:5432/lifeline_db"
import os

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DB_PATH = os.path.join(BASE_DIR, "lifeline.db")   # keep it in project root
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///" + DB_PATH

app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["UPLOAD_FOLDER"] = os.path.join("static", "uploads", "profile_photos")
app.config["ALLOWED_IMAGE_EXTENSIONS"] = {"png", "jpg", "jpeg", "gif"}
os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)


def allowed_image(filename):
    if "." not in filename:
        return False
    ext = filename.rsplit(".", 1)[1].lower()
    return ext in app.config["ALLOWED_IMAGE_EXTENSIONS"]


db = SQLAlchemy(app)
jwt = JWTManager(app)
migrate = Migrate(app, db)

# Socket.IO for real-time chat (eventlet will be auto-detected)
socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    ping_timeout=60,
    ping_interval=25,
    max_http_buffer_size=100000000  # 100MB for file uploads
)

# --- Email / OTP mail config ---
app.config["MAIL_SERVER"] = "smtp.gmail.com"
app.config["MAIL_PORT"] = 587
app.config["MAIL_USE_TLS"] = True
app.config["MAIL_USERNAME"] = EMAIL_ADDRESS
app.config["MAIL_PASSWORD"] = EMAIL_PASSWORD
app.config["MAIL_DEFAULT_SENDER"] = EMAIL_ADDRESS

# Fix cookie issues for localhost (Chrome blocks otherwise)
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = False
app.config["SESSION_COOKIE_HTTPONLY"] = True

mail = Mail(app)

# Your Google Maps API key
GOOGLE_MAPS_API_KEY = os.getenv(
    "GOOGLE_MAPS_API_KEY",
    "AIzaSyAVtLNl7YZaPZeSnuA_5Gxm8VdtFXxreYo"  
)

EMERGENCY_GUEST_EMAIL = "guest_emergency@lifeline.local"

# ------------------ MODELS ------------------
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False)
    name = db.Column(db.String(120), nullable=False)
    lat = db.Column(db.Float, nullable=True)  # User's last known latitude
    lng = db.Column(db.Float, nullable=True)  # User's last known longitude
    # For normal email/password users (nullable so Firebase-only accounts still work)
    password_hash = db.Column(db.String(255), nullable=True)

    # Trusted helper flag + ID
    is_trusted_helper = db.Column(db.Boolean, default=False)
    govt_id_number = db.Column(db.String(50), nullable=True)

    # For Firebase / Google login
    firebase_uid = db.Column(db.String(255), unique=True, nullable=True)

    # For OTP login
    otp_code = db.Column(db.String(6), nullable=True)
    otp_expires_at = db.Column(db.DateTime, nullable=True)

    # Trusted helper verification fields
    id_verification_status = db.Column(db.String(20), default="none")  # none/pending/approved/rejected
    id_document_note = db.Column(db.String(255), nullable=True)

    # Integer trust points and cached kindness score fields:
    trust_score = db.Column(db.Integer, default=0)        
    kindness_score = db.Column(db.Integer, default=0)

    # profile photo, phone and dob
    profile_photo = db.Column(db.String(255), default="default.png")
    phone = db.Column(db.String(20))
    emergency_number = db.Column(db.String(30), nullable=True)
    dob = db.Column(db.String(20))  # or Date type if you prefer
    
    fcm_tokens = db.relationship('FCMToken', backref='user', lazy='dynamic')
    is_premium = db.Column(db.Boolean, default=False)
    premium_expiry = db.Column(db.DateTime, nullable=True)
    is_admin = db.Column(db.Boolean, default=False)
    

    def set_password(self, password: str):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        if not self.password_hash:
            return False
        return check_password_hash(self.password_hash, password)

    def set_otp(self, code, minutes_valid=5):
        self.otp_code = code
        self.otp_expires_at = datetime.utcnow() + timedelta(minutes=minutes_valid)

    def clear_otp(self):
        self.otp_code = None
        self.otp_expires_at = None

    def calculate_badge(self):
        score = self.kindness_score or 0

        if score >= 121:
            return "Community Star", "text-yellow-300"
        elif score >= 71:
            return "Gold Helper", "text-yellow-400"
        elif score >= 31:
            return "Silver Helper", "text-slate-300"
        elif score >= 11:
            return "Bronze Helper", "text-amber-300"
        else:
            return "Newbie", "text-slate-400"
class Payment(db.Model):
    __tablename__ = "payments"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    
    amount = db.Column(db.Float, nullable=False)
    bkash_number = db.Column(db.String(20), nullable=False) # User's phone number
    trx_id = db.Column(db.String(50), unique=True, nullable=False) # The ID they enter
    
    status = db.Column(db.String(20), default="pending") # pending, approved, rejected
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    user = db.relationship("User", backref="payments")    

class Request(db.Model):
    __tablename__ = "requests"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)

    # core
    title = db.Column(db.String(255), nullable=False)
    category = db.Column(db.String(50), nullable=False)  # medicine, food, tutoring, repair, ride, ...
    description = db.Column(db.Text, nullable=True)

    # location / meta
    lat = db.Column(db.Float, nullable=True)
    lng = db.Column(db.Float, nullable=True)
    area = db.Column(db.String(150), nullable=True)         # e.g. Mohammadpur, Iqbal Road
    landmark = db.Column(db.String(150), nullable=True)     # nearest mosque / school

    # urgency & timing
    urgency = db.Column(db.String(50), nullable=True)       # low / normal / high / emergency
    time_window = db.Column(db.String(50), nullable=True)   # anytime_today / this_week / ...

    # contact
    contact_method = db.Column(db.String(50), nullable=True)  # lifeline_chat / phone / ...
    contact_info = db.Column(db.String(100), nullable=True)   # phone / WhatsApp etc.

    # offer-specific meta
    is_offer = db.Column(db.Boolean, default=False)        # false = Need Help, true = Offering Help
    radius_pref = db.Column(db.String(50), nullable=True)  # e.g. "2" (km)
    frequency = db.Column(db.String(50), nullable=True)    # one_time / few_times_week / daily

    image_url = db.Column(db.String(300), nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)
    helper_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    completed_at = db.Column(db.DateTime, nullable=True)

    status = db.Column(db.String(20), default="open")  # open / claimed / closed

    user = db.relationship("User",backref="requests",foreign_keys=[user_id])   # tell SQLAlchemy exactly which FK is for "user"
    helper = db.relationship("User",foreign_keys=[helper_id],backref="helped_requests")


    def to_dict(self, include_user=False, user_lat=None, user_lng=None):
        d = {
            "id": self.id,
            "user_id": self.user_id,
            "title": self.title,
            "category": self.category,
            "description": self.description,
            "lat": self.lat,
            "lng": self.lng,
            "area": self.area,
            "landmark": self.landmark,
            "urgency": self.urgency,
            "time_window": self.time_window,
            "contact_method": self.contact_method,
            "contact_info": self.contact_info,
            "is_offer": self.is_offer,
            "radius_pref": self.radius_pref,
            "frequency": self.frequency,
            "image_url": self.image_url,
            "created_at": int(self.created_at.timestamp()),
            "expires_at": int(self.expires_at.timestamp()),
            "status": self.status,
        }
        if include_user:
            d["user_name"] = self.user.name if self.user else None
        now = datetime.utcnow()
        d["seconds_remaining"] = max(0, int((self.expires_at - now).total_seconds()))
        if (
            user_lat is not None and user_lng is not None
            and self.lat is not None and self.lng is not None
        ):
            try:
                d["distance_km"] = round(
                    haversine_distance_km(user_lat, user_lng, self.lat, self.lng), 2
                )
            except Exception:
                d["distance_km"] = None
        return d

class RequestFlag(db.Model):
    __tablename__ = "request_flag"
    id = db.Column(db.Integer, primary_key=True)
    request_id = db.Column(db.Integer, db.ForeignKey('requests.id'), nullable=False, index=True)
    risk_score = db.Column(db.Integer, nullable=False, default=0)
    reasons = db.Column(db.Text, nullable=False, default="")
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    request = db.relationship('Request', backref=db.backref('flags', lazy=True))

    
class EmotionalPing(db.Model):
    __tablename__ = "emotional_pings"
    id = db.Column(db.Integer, primary_key=True)

    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    mood = db.Column(db.String(50), nullable=False)
    message = db.Column(db.Text, nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    is_active = db.Column(db.Boolean, default=True)
    is_listened = db.Column(db.Boolean, default=False)
    uplift_count = db.Column(db.Integer, default=0)
    last_uplift_at = db.Column(db.DateTime, nullable=True)

    user = db.relationship("User", backref="emotional_pings")




def get_trusted_helpers_for_ping(sender_id):
    return User.query.filter(
        User.is_trusted_helper == True,
        User.id != sender_id
    ).all()


# ------------------ FCM HELPER FUNCTIONS ------------------

def send_fcm_to_token(token, title, body, data=None):
    """
    Low-level helper: send a push to a single FCM token.
    Safe: will not crash the app if Firebase isn't configured.
    """
    if not token:
        return False

    # If Firebase Admin failed to initialize on this machine, skip
    if not firebase_admin._apps:
        print("[FCM] Skipping send_fcm_to_token: Firebase not initialized")
        return False

    try:
        msg = messaging.Message(
            notification=messaging.Notification(title=title, body=body),
            data={k: str(v) for k, v in (data or {}).items()},
            token=token,
        )
        resp = messaging.send(msg)
        print("[FCM] Sent message:", resp)
        return True
    except Exception as e:
        print("[FCM] ERROR sending FCM:", e)
        return False


# --------- FCM HELPER ---------
def send_fcm_to_user(target_user, title, body, data=None):
    """
    Sends an FCM push notification to all devices registered for the target user.

    Accepts:
      - target_user as a User object OR an int user_id
    """
    if not firebase_admin._apps:
        print("[FCM] Firebase not initialized, cannot send notification.")
        return False

    # Allow caller to pass user_id (int) or User object
    try:
        if isinstance(target_user, int):
            target_user = User.query.get(target_user)
        elif isinstance(target_user, str) and target_user.isdigit():
            target_user = User.query.get(int(target_user))
    except Exception as e:
        print("[FCM] Invalid target_user provided:", e)
        return False

    if not target_user:
        print("[FCM] Target user not found.")
        return False

    tokens = [t.token for t in target_user.fcm_tokens.all()]
    if not tokens:
        print(f"[FCM] User {target_user.id} has no FCM tokens registered.")
        return False

    # Ensure data is string:string
    data_payload = {k: str(v) for k, v in (data or {}).items()}

    message = messaging.MulticastMessage(
        notification=messaging.Notification(title=title, body=body),
        data=data_payload,
        tokens=tokens,
        android=messaging.AndroidConfig(priority="high"),
        apns=messaging.APNSConfig(headers={"apns-priority": "10"}),
    )

    try:
        response = messaging.send_multicast(message)

        # Cleanup invalid tokens
        if response.failure_count > 0:
            bad_tokens = []
            for idx, resp in enumerate(response.responses):
                if not resp.success:
                    # resp.exception can be None; guard it
                    code = getattr(getattr(resp, "exception", None), "code", None)
                    if code in ("INVALID_ARGUMENT", "UNREGISTERED"):
                        bad_tokens.append(tokens[idx])

            if bad_tokens:
                FCMToken.query.filter(FCMToken.token.in_(bad_tokens)).delete(synchronize_session=False)
                db.session.commit()
                print(f"[FCM] Cleaned up {len(bad_tokens)} expired tokens for user {target_user.id}.")

        print(f"[FCM] Multicast sent: Success={response.success_count}, Failures={response.failure_count}")
        return response.success_count > 0

    except Exception as e:
        print(f"[FCM] Error sending multicast: {e}")
        return False


def send_fcm_to_trusted_helpers(title, body, data=None, exclude_user_id=None):
    """Best-effort broadcast to all trusted helpers that have FCM tokens."""
    try:
        q = User.query.filter(User.is_trusted_helper == True)  # noqa: E712
        if exclude_user_id is not None:
            q = q.filter(User.id != exclude_user_id)

        sent = 0
        for helper in q.all():
            try:
                if helper.fcm_tokens.count() > 0:
                    if send_fcm_to_user(helper, title=title, body=body, data=data):
                        sent += 1
            except Exception:
                continue

        return sent
    except Exception as e:
        print("[FCM] Error in send_fcm_to_trusted_helpers:", e)
        return 0

    
# --- 1. NEARBY HELP REQUEST ---
# --- UPDATE IN app.py ---

# ----------------------------------------------------------------------
# NEW HELPER: For Emotional Ping
# ----------------------------------------------------------------------
def send_fcm_for_emotional_chat(listener, from_user):
    try:
        send_fcm_to_user(
            listener,
            title="Emotional Support Request",
            body=f"{from_user.name} is feeling low and wants to chat.",
            data={"type": "EMOTIONAL_CHAT", "sender_id": str(from_user.id)}
        )

        push_notification(
            user_id=listener.id,
            type="chat",
            message=f"{from_user.name} is feeling low and wants to chat.",
            link=url_for('chat_with_user', other_user_id=from_user.id)
        )
    except Exception as e:
        print(f"[FCM] Error in send_fcm_for_emotional_chat: {e}")


def send_fcm_for_need_request(req_obj):
    """
    Notify users nearby AND save to database for the bell icon stack.
    """
    try:
        # FIX: Get ALL users with location (removed fcm_token filter)
        candidates = User.query.filter(
            User.lat.isnot(None),
            User.lng.isnot(None),
            User.id != req_obj.user_id 
        ).all()

        count = 0
        radius_km = 5.0 

        for u in candidates:
            dist = haversine_distance_km(req_obj.lat, req_obj.lng, u.lat, u.lng)
            
            if dist <= radius_km:
                title = f"Help needed nearby ({round(dist, 1)}km)"
                body = f"{req_obj.user.name} needs help with {req_obj.category}: {req_obj.title}"
                
                # 1. SAVE TO DB (Always do this for the Bell Icon)
                push_notification(
                    user_id=u.id,
                    type="nearby",
                    message=body,
                    link=url_for('list_requests') 
                )

                # 2. Send Mobile Push (Only if they have a token)
                if u.fcm_tokens.count() > 0:
                    send_fcm_to_user(
                        u, 
                        title=title, 
                        body=body,
                        data={"type": "NEARBY_REQUEST", "request_id": str(req_obj.id)}
                    )
                
                count += 1
        
        print(f"[FCM] Saved nearby alerts for {count} users.")

    except Exception as e:
        print(f"[FCM] Error in send_fcm_for_need_request: {e}")


def send_fcm_for_sos(from_user):
    try:
        helpers = User.query.filter(
            User.is_trusted_helper == True,
            User.id != from_user.id
        ).all()

        for h in helpers:
            push_notification(
                user_id=h.id,
                type="sos",
                message=f"🚨 SOS: {from_user.name} needs immediate help!",
                link=url_for("chat_with_user", other_user_id=from_user.id)
            )

            if h.fcm_tokens.count() > 0:
                send_fcm_to_user(
                    h,
                    title="🚨 SOS ALERT!",
                    body=f"EMERGENCY: {from_user.name} needs help!",
                    data={"type": "SOS_ALERT", "from_user_id": str(from_user.id)}
                )

        return True
    except Exception as e:
        print("[SOS] Error:", e)
        return False


def get_trusted_helpers_within_radius_km(center_lat, center_lng, radius_km, exclude_user_id=None):
    q = User.query.filter(User.is_trusted_helper == True)  # noqa: E712
    if exclude_user_id is not None:
        q = q.filter(User.id != exclude_user_id)

    q = q.filter(User.lat.isnot(None), User.lng.isnot(None))

    results = []
    for h in q.all():
        try:
            dist = haversine_distance_km(center_lat, center_lng, h.lat, h.lng)
        except Exception:
            continue
        if dist <= radius_km:
            results.append((h, dist))

    results.sort(key=lambda t: t[1])
    return [h for h, _ in results]

        
# NEW MODEL FOR FCM TOKENS
class FCMToken(db.Model):
    __tablename__ = 'fcm_token'
    id = db.Column(db.Integer, primary_key=True)
    # This foreign key links the token back to the User who owns it
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    # The token string itself, must be unique across all tokens
    token = db.Column(db.String(255), unique=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    
class Offer(db.Model):
    __tablename__ = "offers"
    id = db.Column(db.Integer, primary_key=True)
    request_id = db.Column(db.Integer, db.ForeignKey("requests.id"), nullable=False)
    helper_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    status = db.Column(db.String(20), default="pending")  # pending, accepted, rejected
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Relationships
    request = db.relationship("Request", backref=db.backref("offers", cascade="all, delete-orphan"))
    helper = db.relationship("User", backref="sent_offers")   


class SOSResponse(db.Model):
    __tablename__ = "sos_responses"
    id = db.Column(db.Integer, primary_key=True)
    request_id = db.Column(db.Integer, db.ForeignKey("requests.id"), nullable=False)
    helper_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    responder_lat = db.Column(db.Float, nullable=True)
    responder_lng = db.Column(db.Float, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    request = db.relationship(
        "Request", backref=db.backref("sos_responses", cascade="all, delete-orphan")
    )
    helper = db.relationship("User", backref="sos_responses")
# In app.py
class Review(db.Model):
    __tablename__ = "reviews"
    id = db.Column(db.Integer, primary_key=True)
    request_id = db.Column(db.Integer, db.ForeignKey("requests.id"), nullable=False)
    reviewer_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    helper_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    
    rating = db.Column(db.Integer, nullable=False)
    comment = db.Column(db.Text, nullable=True)
    
    # NEW FIELD: Store the actual hours worked
    duration_hours = db.Column(db.Float, default=1.0) 

    # AI Analysis Results
    sentiment_score = db.Column(db.Float, default=0.0)
    is_flagged_fake = db.Column(db.Boolean, default=False)
    flag_reason = db.Column(db.String(255), nullable=True)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    request = db.relationship("Request", backref=db.backref("review", uselist=False))

# ------------------ MODELS: Chat ------------------
class Conversation(db.Model):
    __tablename__ = "conversations"
    id = db.Column(db.Integer, primary_key=True)
    # participant user ids (two users, one-on-one)
    user_a = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    user_b = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def participants(self):
        return {self.user_a, self.user_b}


class ChatMessage(db.Model):
    __tablename__ = "chat_messages"
    id = db.Column(db.Integer, primary_key=True)
    conversation_id = db.Column(db.Integer, db.ForeignKey("conversations.id"), nullable=False)
    sender_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    text = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    delivered = db.Column(db.Boolean, default=False)
    read = db.Column(db.Boolean, default=False)
    language = db.Column(db.String(10), nullable=True)  # original language code if known

    conversation = db.relationship("Conversation", backref="messages")
# ------------------ MODELS: Notifications ------------------
class Notification(db.Model):
    __tablename__ = "notifications"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)

    type = db.Column(db.String(50), nullable=False)  # "chat", "offer", "accepted", "sos", "nearby"
    message = db.Column(db.String(255), nullable=False)
    link = db.Column(db.String(255), nullable=True)  # where to go when clicked

    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship("User", backref="notifications")

# ------------------ MODELS: Resources ------------------

class Resource(db.Model):
    __tablename__ = "resources"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    title = db.Column(db.String(255), nullable=False)
    category = db.Column(db.String(80), nullable=False)
    quantity = db.Column(db.String(50), nullable=True)
    description = db.Column(db.Text, nullable=True)
    area = db.Column(db.String(150), nullable=True)
    contact_info = db.Column(db.String(150), nullable=True)
    image_url = db.Column(db.String(300), nullable=True)
    latitude = db.Column(db.Float, nullable=True)
    longitude = db.Column(db.Float, nullable=True)
    status = db.Column(db.String(20), default="available")  # available / claimed / removed
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship("User", backref="resources")

    def to_dict(self):
        return {
            "id": self.id,
            "user_id": self.user_id,
            "title": self.title,
            "category": self.category,
            "quantity": self.quantity,
            "description": self.description,
            "area": self.area,
            "contact_info": self.contact_info,
            "image_url": self.image_url,
            "status": self.status,
            "created_at": int(self.created_at.timestamp()),
            "user_name": self.user.name if self.user else None,
        }


class ResourceRequest(db.Model):
    __tablename__ = "resource_requests"
    id = db.Column(db.Integer, primary_key=True)
    resource_id = db.Column(db.Integer, db.ForeignKey("resources.id"), nullable=False)
    requester_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    message = db.Column(db.Text, nullable=True)
    latitude = db.Column(db.Float, nullable=True)
    longitude = db.Column(db.Float, nullable=True)
    status = db.Column(db.String(20), default="pending")  # pending / accepted / rejected
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    resource = db.relationship("Resource", backref="requests")
    requester = db.relationship("User", backref="resource_requests")


class ResourceWantedItem(db.Model):
    """Wanted items posted by users in the resource sharing system"""
    __tablename__ = "resource_wanted_items"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    title = db.Column(db.String(255), nullable=False)
    category = db.Column(db.String(50), nullable=False)
    description = db.Column(db.Text, nullable=True)
    contact_info = db.Column(db.String(100), nullable=True)
    image_url = db.Column(db.String(300), nullable=True)
    latitude = db.Column(db.Float, nullable=True)
    longitude = db.Column(db.Float, nullable=True)
    status = db.Column(db.String(20), default="open")  # open / closed
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)

    user = db.relationship("User", backref="wanted_items")


def get_or_create_conversation(user1_id, user2_id):
    a, b = sorted([int(user1_id), int(user2_id)])
    conv = Conversation.query.filter_by(user_a=a, user_b=b).first()
    if conv:
        return conv
    conv = Conversation(user_a=a, user_b=b)
    db.session.add(conv)
    db.session.commit()
    return conv

def push_notification(user_id, type, message, link=None):
    n = Notification(
        user_id=user_id,
        type=type,
        message=message,
        link=link
    )
    db.session.add(n)
    db.session.commit()


def serialize_message(msg: ChatMessage):
    return {
        "id": msg.id,
        "conversation_id": msg.conversation_id,
        "sender_id": msg.sender_id,
        "sender_name": (User.query.get(msg.sender_id).name if User.query.get(msg.sender_id) else None),
        "text": msg.text,
        "created_at": int(msg.created_at.timestamp()),
        "delivered": bool(msg.delivered),
        "read": bool(msg.read),
        "language": msg.language,
    }


# ------------------ AUTH HELPERS ------------------
def login_user(user: User):
    session["user_id"] = user.id
    session["user_name"] = user.name
    session["is_trusted_helper"] = bool(user.is_trusted_helper)


def logout_user():
    session.pop("user_id", None)
    session.pop("user_name", None)
    session.pop("is_trusted_helper", None)


def current_user():
    uid = session.get("user_id")
    if not uid:
        return None

    try:
        user = db.session.get(User, uid)
    except Exception:
        user = None

    # If the DB was reset or user deleted, avoid hard-crashing downstream.
    if user is None:
        logout_user()
        return None

    return user


def login_required(view_func):
    from functools import wraps

    @wraps(view_func)
    def wrapper(*args, **kwargs):
        # Require both a session user_id and a resolvable User row.
        if current_user() is None:
            next_url = request.path
            return redirect(url_for("login", next=next_url))
        return view_func(*args, **kwargs)

    return wrapper


def get_emergency_user():
    """
    Return (or create) a special guest user used for emergency posts
    when the requester is not logged in.
    """
    guest = User.query.filter_by(email=EMERGENCY_GUEST_EMAIL).first()
    if guest is None:
        guest = User(
            email=EMERGENCY_GUEST_EMAIL,
            name="Emergency Guest",
        )
        # random password so nobody can actually log in with this
        guest.password_hash = generate_password_hash(os.urandom(16).hex())
        db.session.add(guest)
        db.session.commit()
    return guest


def create_jwt_for_user(user: User):
    """Create a JWT access token for API use."""
    additional_claims = {
        "email": user.email,
        "is_trusted_helper": user.is_trusted_helper,
    }
    token = create_access_token(identity=str(user.id), additional_claims=additional_claims)
    return token


def generate_otp_code():
    """Generate a 6-digit numeric OTP."""
    return "".join(random.choices(string.digits, k=6))

def get_notification_count(user: User) -> int:
    if not user:
        return 0
    return Notification.query.filter_by(user_id=user.id, is_read=False).count()
    

# Make current user available in all templates as `current_user`
@app.context_processor
def inject_user():
    # Expose whether translation is available to templates
    translation_enabled = False
    if globals().get('gcloud_translate_client'):
        translation_enabled = True
    elif globals().get('gt_translator'):
        translation_enabled = True

    user = current_user()
    notification_count = get_notification_count(user) if user else 0

    return dict(
        current_user=user,
        translation_enabled=translation_enabled,
        notification_count=notification_count,
    )


# ------------------ GEO UTILS ------------------
def haversine_distance_km(lat1, lon1, lat2, lon2):
    R = 6371.0  # Earth radius in KM
    lat1_r, lon1_r = math.radians(lat1), math.radians(lon1)
    lat2_r, lon2_r = math.radians(lat2), math.radians(lon2)
    dlat = lat2_r - lat1_r
    dlon = lon2_r - lon1_r

    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1_r) * math.cos(lat2_r) * math.sin(dlon / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return R * c

# ------------------ COMPLETE REQUEST ------------------
def update_user_scores(user):
    """
    Recompute user scores. 
    Relies on the REVIEW table because 'complete_request' guarantees 
    Review.helper_id is ALWAYS the person who deserves the credit.
    """
    if not user:
        return

    # 1. Get all reviews where this user was the Giver (Review.helper_id)
    valid_reviews = Review.query.filter_by(helper_id=user.id, is_flagged_fake=False).all()
    total_reviews = len(valid_reviews)

    # 2. Calculate Kindness Points
    current_kindness = 0
    from reputation_service import calculate_reputation_points
    
    # Points from Ratings
    for rev in valid_reviews:
        current_kindness += calculate_reputation_points(rev.rating, rev.is_flagged_fake)
        
        # Points from Hours (Add 5 points per hour worked)
        # We use the duration stored in the review
        if rev.duration_hours:
             current_kindness += int(rev.duration_hours * 5)

    # 3. Calculate Trust Score
    if total_reviews > 0:
        positive_reviews = sum(1 for r in valid_reviews if r.rating >= 4)
        trust_percentage = (positive_reviews / total_reviews) * 100
        
        fake_count = Review.query.filter_by(helper_id=user.id, is_flagged_fake=True).count()
        trust_percentage -= (fake_count * 10)
        
        user.trust_score = max(0, min(100, int(trust_percentage)))
    else:
        user.trust_score = 50

    user.kindness_score = int(current_kindness)
    db.session.commit()

def send_push_to_user(user: User, title: str, body: str, data: dict | None = None):
    """Send a push notification to the user (FCM)."""
    return send_fcm_to_user(user, title=title, body=body, data=data)
def check_post_limit(user):
    """
    Returns True if user can post, False if limit reached.
    Rules: Premium = Unlimited. Free = 2 posts per 21 days.
    """
    if not user: return True # Guests handled separately or allowed
    
    # 1. Premium Check
    if user.is_premium:
        if user.premium_expiry and user.premium_expiry > datetime.utcnow():
            return True
        else:
            # Expired? Downgrade automatically
            user.is_premium = False
            db.session.commit()

    # 2. Free User Logic
    three_weeks_ago = datetime.utcnow() - timedelta(days=21)
    
    # Count posts in last 21 days
    recent_posts = Request.query.filter(
        Request.user_id == user.id,
        Request.created_at >= three_weeks_ago
    ).count()

    return recent_posts < 2
        
def send_fcm_notification(token, title, body, data=None):
    if not token:
        print("[FCM] No token provided, skipping")
        return False

    try:
        payload_data = {"title": title, "body": body}
        if data:
            payload_data.update({str(k): str(v) for k, v in data.items()})

        message = messaging.Message(
            token=token,
            data=payload_data,  # data-only
        )
        response = messaging.send(message)
        print("[FCM] Successfully sent message:", response)
        return True  # ✅
    except Exception as e:
        print("[FCM] Error sending message:", e)
        return False  # ✅

# ------------------ ROUTES: CORE PAGES ------------------
@app.route("/")
def home():
    # Try to include a short preview of shared resources on the homepage.
    try:
        from resources import Resource
        now = datetime.utcnow()
        items = (
            Resource.query.filter(Resource.status == "available")
            .order_by(Resource.created_at.desc())
            .limit(5)
            .all()
        )
    except Exception:
        items = []
    return render_template("home.html", items=items)


@app.route("/debug/session")
def debug_session():
    return jsonify(dict(session))


@app.route("/map")
@login_required
def map_page():
    return render_template(
        "map.html",
        google_maps_key=GOOGLE_MAPS_API_KEY,
        user=current_user(),
    )

# ------------------ ROUTES: AUTH ------------------
@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        print("SIGNUP ROUTE HIT")  # DEBUG

        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm", "")

        if not name or not email or not password:
            flash("Please fill in all fields.", "error")
            return redirect(url_for("signup"))

        if password != confirm:
            flash("Passwords do not match.", "error")
            return redirect(url_for("signup"))

        existing = User.query.filter_by(email=email).first()
        if existing:
            flash("An account with this email already exists. Please log in.", "error")
            return redirect(url_for("login"))

        # Create the new user
        user = User(name=name, email=email)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()

        # Log the user in immediately after signup
        login_user(user)
        flash("Welcome to LifeLine! Your account has been created.", "success")

        # Generate JWT and print it (for demo in terminal)
        access_token = create_jwt_for_user(user)
        print("JWT for new user:", access_token)

        next_url = request.args.get("next") or url_for("home")
        return redirect(next_url)

    # GET
    return render_template("signup.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        print("LOGIN ROUTE HIT")  # DEBUG

        # NORMAL PASSWORD LOGIN -----------------------------
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        # If both email & password fields are filled → Password login
        if email and password:
            user = User.query.filter_by(email=email).first()

            if not user or not user.check_password(password):
                print("LOGIN FAILED (password wrong)")  # DEBUG
                flash("Invalid email or password.", "error")
                return redirect(url_for("login"))

            print("LOGIN SUCCESS (password), creating JWT")  # DEBUG
            login_user(user)
            print("SESSION AFTER LOGIN:", dict(session))
            flash("Logged in successfully.", "success")

            access_token = create_jwt_for_user(user)
            print("JWT after login:", access_token)  # DEBUG

            next_url = request.args.get("next") or url_for("home")
            return redirect(next_url)

        # OTP LOGIN -----------------------------------------
        otp_email = request.form.get("otp_email", "").strip().lower()
        otp_code = request.form.get("otp_code", "").strip()

        # If OTP fields are filled → OTP login
        if otp_email and otp_code:
            print("OTP LOGIN ATTEMPT")  # DEBUG

            user = User.query.filter_by(email=otp_email).first()

            if not user:
                flash("No user with this email.", "error")
                return redirect(url_for("login"))

            # Check the OTP stored earlier
            if otp_store.get(otp_email) != otp_code:
                print("OTP FAILED")  # DEBUG
                flash("Invalid OTP.", "error")
                return redirect(url_for("login"))

            print("OTP SUCCESS — creating JWT")  # DEBUG
            login_user(user)
            print("SESSION AFTER LOGIN (otp):", dict(session))
            flash("Logged in with OTP!", "success")

            access_token = create_jwt_for_user(user)
            print("JWT after OTP login:", access_token)  # DEBUG

            # Remove OTP after use
            otp_store.pop(otp_email, None)

            return redirect(url_for("home"))

        # If neither method is triggered
        flash("Please fill in login details.", "error")
        return redirect(url_for("login"))

    # GET
    next_param = request.args.get("next")
    if next_param:
        session["next_after_google"] = next_param
    return render_template("login.html")


@app.route("/logout")
def logout():
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for("home"))

# ---------- OTP / EMAIL HELPERS & ROUTES ----------
OTP_TTL_SECONDS = 120          # OTP valid for 2 minutes
OTP_COOLDOWN_SECONDS = 30      # at least 30s between OTP sends per email
OTP_MAX_PER_HOUR = 5           # no more than 5 OTPs per email per hour


def build_otp_email_html(user, code):
    return f'''<html>
  <body style="font-family: system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background-color:#020617; padding:16px;">
    <div style="max-width:480px;margin:0 auto;background:#020617;border-radius:16px;border:1px solid #1e293b;padding:20px;">
      <h2 style="color:#a7f3d0;margin-top:0;">LifeLine login code</h2>
      <p style="color:#e5e7eb;font-size:14px;">Hi {user.name},</p>

      <p style="color:#e5e7eb;font-size:14px;">
        Your one-time code for logging into <strong>LifeLine</strong> is:
      </p>

      <div style="margin:16px 0;padding:12px 16px;border-radius:999px;background:#022c22;border:1px solid #22c55e;text-align:center;">
        <span style="color:#bbf7d0;font-size:20px;letter-spacing:0.35em;font-weight:600;">{code}</span>
      </div>

      <p style="color:#9ca3af;font-size:12px;">
        This code will expire in <strong>{OTP_TTL_SECONDS // 60} minutes</strong>.
        If you did not request this, you can safely ignore this email.
      </p>

      <p style="color:#6b7280;font-size:11px;margin-top:24px;border-top:1px solid #1f2937;padding-top:12px;">
        Sent by LifeLine - University project demo
      </p>
    </div>
  </body>
</html>'''

@app.route("/emotional_ping")
@login_required
def emotional_ping():
    return render_template("emotional_ping.html")

@app.route("/emotional")
@login_required
def emotional():
    return redirect(url_for("emotional_ping"))



from flask_jwt_extended import jwt_required, get_jwt_identity
from datetime import datetime

@app.route("/api/emotional_ping", methods=["POST"])
@login_required
def api_emotional_ping():
    try:
        user = current_user()
        if not user:
            return jsonify({"error": "Unauthorized"}), 401

        data = request.get_json(silent=True) or {}
        mood = data.get("mood")
        message = data.get("message")

        if not mood:
            return jsonify({"error": "Mood is required"}), 400

        # 1️⃣ Save ping
        ping = EmotionalPing(
            user_id=user.id,
            mood=mood,
            message=message
        )
        db.session.add(ping)
        db.session.commit()

        # 2️⃣ Notify trusted helpers (BELL)
        helpers = get_trusted_helpers_for_ping(user.id)

        for helper in helpers:
            push_notification(
                user_id=helper.id,
                type="emotional_ping",
                message=f"{user.name} is feeling {mood}",
                link=url_for("emotional_ping")
            )

            # 3️⃣ Optional FCM push
            if helper.fcm_tokens.count() > 0:
                send_fcm_to_user(
                    helper,
                    title="New Emotional Ping 💙",
                    body=f"{user.name} is feeling {mood}",
                    data={
                        "type": "EMOTIONAL_PING",
                        "sender_id": str(user.id),
                        "ping_id": str(ping.id)
                    }
                )

        return jsonify({"message": "Ping sent"}), 200

    except Exception as e:
        db.session.rollback()
        print("EMOTIONAL_PING POST ERROR:", e)
        return jsonify({"error": "Failed to send ping"}), 500
    
@app.route("/api/emotional_ping/<int:ping_id>/listen", methods=["POST"])
@login_required
def mark_ping_listened(ping_id):
    try:
        ping = EmotionalPing.query.get_or_404(ping_id)
        ping.is_listened = True
        db.session.commit()
        return jsonify({"message": "Marked as listened"}), 200
    except Exception as e:
        db.session.rollback()
        print("LISTEN ERROR:", e)
        return jsonify({"error": "Failed"}), 500


@app.route("/login/otp/request", methods=["POST"])
def login_otp_request():
    email = request.form.get("email", "").strip().lower()
    user = User.query.filter_by(email=email).first()

    if not user:
        flash("No account with this email.", "error")
        return redirect(url_for("login", mode="otp"))

    now = datetime.utcnow()
    log = otp_store.get(email)

    # Initialize log structure if first time
    if not log:
        log = {"request_times": []}

    # Keep only requests from the last hour
    one_hour_ago = now - timedelta(hours=1)
    log["request_times"] = [t for t in log["request_times"] if t > one_hour_ago]

    # Rate limiting: no more than OTP_MAX_PER_HOUR per hour
    if len(log["request_times"]) >= OTP_MAX_PER_HOUR:
        flash("Too many OTP requests. Please try again later.", "error")
        otp_store[email] = log
        return redirect(url_for("login", mode="otp"))

    # Cooldown: at least OTP_COOLDOWN_SECONDS between sends
    if log["request_times"]:
        last_request = max(log["request_times"])
        elapsed = (now - last_request).total_seconds()
        if elapsed < OTP_COOLDOWN_SECONDS:
            remaining = int(OTP_COOLDOWN_SECONDS - elapsed)
            flash(f"Please wait {remaining}s before requesting a new OTP.", "error")
            otp_store[email] = log
            return redirect(url_for("login", mode="otp"))

    # Generate and save OTP (2 minutes)
    code = "".join(random.choices(string.digits, k=6))
    user.set_otp(code, minutes_valid=OTP_TTL_SECONDS // 60 or 1)
    db.session.commit()

    # Update log data
    log["last_code_sent_at"] = now
    log["request_times"].append(now)
    otp_store[email] = log

    # Send HTML email (with plain-text fallback)
    msg = Message("Your LifeLine Login OTP", recipients=[email])
    msg.body = (
        f"Hello {user.name}, your LifeLine login code is: {code} "
        f"(valid for {OTP_TTL_SECONDS // 60} minutes)."
    )
    msg.html = build_otp_email_html(user, code)
    mail.send(msg)

    flash("OTP sent to your email.", "success")
    return redirect(url_for("login", mode="otp"))


@app.route("/login/otp/verify", methods=["POST"])
def login_otp_verify():
    email = request.form.get("email", "").strip().lower()
    code = request.form.get("otp", "").strip()

    user = User.query.filter_by(email=email).first()
    if not user or not user.otp_code:
        flash("No active OTP for this email. Please request a new code.", "error")
        return redirect(url_for("login", mode="otp"))

    now = datetime.utcnow()

    # Check expiration
    if not user.otp_expires_at or user.otp_expires_at < now:
        user.clear_otp()
        db.session.commit()
        flash("Your OTP has expired. Please request a new code.", "error")
        return redirect(url_for("login", mode="otp"))

    # Check code
    if user.otp_code != code:
        flash("Incorrect OTP. Please double-check the code.", "error")
        return redirect(url_for("login", mode="otp"))

    # Success: clear OTP and log user in
    user.clear_otp()
    db.session.commit()

    login_user(user)
    flash("Logged in with OTP!", "success")
    return redirect(url_for("home"))

@app.route("/api/emotional_pings", methods=["GET"])
@login_required
def api_emotional_pings():
    pings = (
    EmotionalPing.query
    .filter_by(is_active=True)
    .order_by(
        EmotionalPing.last_uplift_at.is_(None),   # ✅ non-null first
        EmotionalPing.last_uplift_at.desc(),      # ✅ newest uplift first
        EmotionalPing.created_at.desc()           # ✅ fallback
    )
    .all()
)




    out = []
    for p in pings:
        u = User.query.get(p.user_id)
        out.append({
            "id": p.id,
            "user_id": p.user_id,
            "user_name": u.name if u else "Unknown",
            "mood": p.mood,
            "message": p.message,
            "is_listened": p.is_listened,
            "uplift_count": p.uplift_count or 0,

            "created_at_human": p.created_at.strftime("%b %d, %I:%M %p"),
        })

    return jsonify({"pings": out})

@app.route("/api/emotional_pings/<int:ping_id>/listen", methods=["POST"])
@login_required
def api_listen_emotional_ping(ping_id):
    try:
        ping = EmotionalPing.query.get_or_404(ping_id)

        # mark as listened
        ping.is_listened = True
        db.session.commit()

        return jsonify({"ok": True, "ping_id": ping.id, "is_listened": ping.is_listened}), 200

    except Exception as e:
        db.session.rollback()
        print("LISTEN PING ERROR:", e)
        return jsonify({"ok": False, "error": "Failed"}), 500

@app.route("/api/emotional_pings/<int:ping_id>/uplift", methods=["POST"])
@login_required
def api_uplift_ping(ping_id):
    try:
        ping = EmotionalPing.query.get_or_404(ping_id)

        ping.uplift_count = (ping.uplift_count or 0) + 1
        ping.last_uplift_at = datetime.utcnow()

        db.session.commit()

        return jsonify({
            "ok": True,
            "uplift_count": ping.uplift_count
        }), 200

    except Exception as e:
        db.session.rollback()
        print("UPLIFT ERROR:", e)
        return jsonify({"ok": False}), 500



# ---------- GOOGLE / FIREBASE AUTH ----------
@app.route("/auth/google", methods=["POST"])
def auth_google():
    # Expect a JSON body: { "idToken": "..." }
    data = request.get_json(silent=True) or {}
    id_token = data.get("idToken")

    if not id_token:
        return jsonify({"error": "Missing idToken"}), 400

    try:
        decoded = firebase_auth.verify_id_token(id_token)
        print("Firebase decoded token:", decoded)
    except Exception as e:
        print("Firebase verify error:", e)
        return jsonify({"error": "Invalid Google token"}), 401

    decoded = firebase_auth.verify_id_token(id_token)
    email = decoded.get("email")
    uid = decoded.get("uid")
    name = decoded.get("name") or (email.split("@")[0] if email else "Google user")
    photo_url = decoded.get("picture")

    if not email:
        return jsonify({"error": "Google account has no email"}), 400

    # Find or create local user
    user = User.query.filter_by(email=email).first()
    if not user:
        user = User(
            email=email,
            name=name,
            firebase_uid=uid,
            profile_photo=photo_url or "default.png",
        )
        db.session.add(user)
    else:
        # If we have a Google photo and user still has default or empty photo, update it
        if photo_url and (not user.profile_photo or user.profile_photo == "default.png"):
            user.profile_photo = photo_url

    db.session.commit()


    # Log into Flask session
    login_user(user)
    flash("Logged in with Google.", "success")

    # Optional: create a JWT for API usage
    access_token = create_jwt_for_user(user)
    session["api_token"] = access_token  # purely for convenience/debug

    # Frontend JS will redirect based on this
    return jsonify({"redirect_url": url_for("home")})


@app.route("/google-auth", methods=["POST"])
def google_auth():
    "Verify Google ID token from Firebase, log user in, return redirect URL."
    data = request.get_json() or {}
    id_token = data.get("id_token")

    if not id_token:
        return jsonify({"error": "Missing id_token"}), 400

    try:
        decoded = firebase_auth.verify_id_token(id_token)
    except Exception as e:
        print("Firebase verify_id_token error:", e)
        return jsonify({"error": "Invalid or expired Google token"}), 401

    uid = decoded["uid"]
    email = decoded.get("email")
    name = decoded.get("name") or (email.split("@")[0] if email else "Google User")

    photo_url = decoded.get("picture")   # 👈 Google profile image URL


    picture = decoded.get("picture")

    # 1) Try find user by firebase_uid
    user = User.query.filter_by(firebase_uid=uid).first()

    # 2) Or by email (if they signed up with password earlier)
    if not user and email:
        user = User.query.filter_by(email=email).first()

    # 3) If still no user, create one
    if not user:

        user = User(
            email=email,
            name=name,
            firebase_uid=uid,
            profile_photo=photo_url or "default.png",  # 👈 store Google photo
        )

        user = User(email=email, name=name, firebase_uid=uid, profile_photo=picture or "default.png")

        db.session.add(user)
    else:
        if (not user.profile_photo or user.profile_photo == "default.png") and picture:
            user.profile_photo = picture
        # Link existing account with this Firebase UID
        if not user.firebase_uid:
            user.firebase_uid = uid

        # If user still has default/no photo, upgrade to Google photo
        if photo_url and (not user.profile_photo or user.profile_photo == "default.png"):
            user.profile_photo = photo_url

    db.session.commit()

    # Log in using your session-based helper
    login_user(user)
    flash("Logged in with Google.", "success")

    # Optional: issue JWT for API use and stash in session
    token = create_jwt_for_user(user)
    session["api_token"] = token

    # Where to go next
    next_url = session.pop("next_after_google", url_for("home"))
    return jsonify({"redirect_url": next_url})

# ---------- TRUSTED HELPER VERIFICATION ----------
# In app.py

@app.route("/trusted-helper", methods=["GET", "POST"])
@login_required
def trusted_helper():
    user = current_user()

    if request.method == "POST":
        govt_id = request.form.get("govt_id", "").strip()

        if not govt_id:
            flash("Please enter a valid government ID number.", "error")
            return redirect(url_for("trusted_helper"))

        # Save ID and verify user
        user.govt_id_number = govt_id
        user.is_trusted_helper = True
        db.session.commit()

        #  Keep session in sync so navbar updates
        session["is_trusted_helper"] = True

        flash("You are now a VERIFIED Trusted Helper!", "success")
        return redirect(url_for("trusted_helper"))

    return render_template("trusted_helper.html", user=user)

@app.route("/profile")
@login_required
def profile():
    "Show logged-in users profile page."
    user = current_user()
    if not user:
        return redirect(url_for("login", next=url_for("profile")))

    # Ensure a default photo name exists so template doesn't break
    if not user.profile_photo:
        user.profile_photo = "default.png"
        db.session.commit()

    return render_template("profile.html", user=user)
@app.route("/profile/update", methods=["POST"])
@login_required
def update_profile():
    user = current_user()
    if not user:
        return redirect(url_for("login", next=url_for("profile")))

    # ---- basic fields ----
    name = request.form.get("name", "").strip()
    phone = request.form.get("phone", "").strip()
    emergency_number = request.form.get("emergency_number", "").strip()
    dob   = request.form.get("dob", "").strip()

    if name:
        user.name = name
        # keep navbar greeting in sync
        session["user_name"] = name

    user.phone = phone or None
    if emergency_number:
        # allow digits/spaces and common prefixes; keep minimal validation for MVP
        cleaned = "".join(ch for ch in emergency_number if ch.isdigit() or ch in "+- ()")
        cleaned = cleaned.strip()
        if len(cleaned) < 6:
            flash("Emergency number looks too short.", "error")
            return redirect(url_for("profile"))
        user.emergency_number = cleaned
    else:
        user.emergency_number = None
    user.dob   = dob or None

    # ---- profile photo upload ----
    file = request.files.get("profile_photo")
    if file and file.filename:
        if allowed_image(file.filename):
            filename = secure_filename(file.filename)
            # make it unique per user
            filename = f"{user.id}_{int(time.time())}_{filename}"
            save_path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
            file.save(save_path)

            # store only file name (template already uses uploads/profile_photos/)
            user.profile_photo = filename
        else:
            flash("Unsupported image type. Please upload PNG/JPG/JPEG/GIF.", "error")
            return redirect(url_for("profile"))

    db.session.commit()
    flash("Profile updated successfully.", "success")
    return redirect(url_for("profile"))

@app.route("/emotional_ping/<int:ping_id>/accept")
@login_required
def accept_ping(ping_id):
    ping = EmotionalPing.query.get_or_404(ping_id)

    conv = get_or_create_conversation(ping.user_id, current_user().id)


    push_notification(
        user_id=ping.user_id,
        message=f"{current_user.name} replied to your emotional ping"
    )

    return redirect(url_for("chat_with_user", other_user_id=ping.user_id))



# ---------- JWT-Protected API ----------
@app.route("/api/me")
@jwt_required()
def api_me():
    user_id = get_jwt_identity()
    user = User.query.get(int(user_id))
    if not user:
        return jsonify({"error": "User not found"}), 404

    return jsonify(
        {
            "id": user.id,
            "email": user.email,
            "name": user.name,
            "is_trusted_helper": user.is_trusted_helper,
            "id_verification_status": user.id_verification_status,
        }
    )
    


# ------------------ API: NEARBY REQUESTS FOR MAP ------------------
@app.route("/api/requests/nearby")
@login_required
def api_requests_nearby():
    """
    Returns nearby requests for the live map.
    Response objects look like:
    {
      id, title, description, lat, lng, distance_km,
      type: "help"|"medicine"|"ride",
      urgency, is_offer, radius_pref, ...
    }
    """
    try:
        user_lat = float(request.args.get("lat"))
        user_lng = float(request.args.get("lng"))
    except (TypeError, ValueError):
        return jsonify({"error": "lat and lng are required"}), 400

    radius_km = float(request.args.get("radius_km", 3.0))

    now = datetime.utcnow()
    max_age_seconds = 60 * 60  # last 1 hour
    cutoff = now - timedelta(seconds=max_age_seconds)

    # Base query: only open, not expired, created in last hour, with coordinates
    q = Request.query.filter(
        Request.status == "open",
        Request.expires_at > now,
        Request.created_at >= cutoff,
        Request.lat.isnot(None),
        Request.lng.isnot(None),
    )

    viewer = current_user()
    nearby = []
    sos_ids = []

    for r in q.all():
        dist = haversine_distance_km(user_lat, user_lng, r.lat, r.lng)
        if dist > radius_km:
            continue

        item = r.to_dict(include_user=True, user_lat=user_lat, user_lng=user_lng)

        cat = (r.category or "").lower()
        if cat == "medicine":
            t = "medicine"
        elif cat == "ride":
            t = "ride"
        else:
            t = "help"

        item["type"] = t
        item["distance_km"] = round(dist, 2)

        is_sos = cat == "sos"
        item["is_sos"] = is_sos
        if is_sos:
            sos_ids.append(int(r.id))

        nearby.append(item)

    responded_ids = set()
    try:
        if viewer and sos_ids:
            rows = (
                db.session.query(SOSResponse.request_id)
                .filter(SOSResponse.helper_id == viewer.id)
                .filter(SOSResponse.request_id.in_(sos_ids))
                .all()
            )
            responded_ids = {int(rid) for (rid,) in rows}
    except Exception:
        responded_ids = set()

    if responded_ids:
        for item in nearby:
            if (item.get("category") or "").lower() == "sos":
                item["viewer_sos_responded"] = int(item.get("id")) in responded_ids
    else:
        for item in nearby:
            if (item.get("category") or "").lower() == "sos":
                item["viewer_sos_responded"] = False

    return jsonify({"requests": nearby})


# Create a new request/offer (API, logged in only)
@app.route("/api/requests", methods=["POST"])
@login_required
def api_create_request():
    data = request.get_json() or {}
    title = (data.get("title") or "").strip()
    category = (data.get("category") or "").strip()
    description = data.get("description")
    lat = data.get("lat")
    lng = data.get("lng")
    is_offer = bool(data.get("is_offer", False))
    expiry_minutes = int(data.get("expiry_minutes", 60))

    if not title or not category:
        return jsonify({"error": "title and category required"}), 400

    user = current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    created_at = datetime.utcnow()
    expires_at = created_at + timedelta(minutes=expiry_minutes)

    req = Request(
        user_id=user.id,
        title=title,
        category=category,
        description=description,
        lat=lat,
        lng=lng,
        is_offer=is_offer,
        created_at=created_at,
        expires_at=expires_at,
    )
    db.session.add(req)
    db.session.commit()
    return jsonify({"request": req.to_dict(include_user=True)}), 201


# List active requests (optionally nearby)
@app.route("/api/requests", methods=["GET"])
@login_required
def api_list_requests():
    try:
        user_lat = float(request.args.get("lat")) if request.args.get("lat") else None
        user_lng = float(request.args.get("lng")) if request.args.get("lng") else None
    except ValueError:
        return jsonify({"error": "Invalid lat/lng"}), 400

    radius_km = float(request.args.get("radius_km", 5.0))
    category = request.args.get("category")
    include_offers = request.args.get("include_offers", "true").lower() == "true"

    now = datetime.utcnow()
    q = Request.query.filter(Request.expires_at > now, Request.status == "open")

    if category:
        q = q.filter(func.lower(Request.category) == category.lower())

    if not include_offers:
        q = q.filter(Request.is_offer == False)  # noqa: E712

    results = []
    for r in q.all():
        if (
            user_lat is not None
            and user_lng is not None
            and r.lat is not None
            and r.lng is not None
        ):
            dist = haversine_distance_km(user_lat, user_lng, r.lat, r.lng)
            if dist > radius_km:
                continue
            results.append(
                r.to_dict(include_user=True, user_lat=user_lat, user_lng=user_lng)
            )
        else:
            results.append(r.to_dict(include_user=True))

    if user_lat is not None:
        results.sort(key=lambda x: x.get("distance_km", 9999))

    return jsonify({"requests": results})


# Delete a request (owner only)
@app.route("/api/requests/<int:request_id>", methods=["DELETE"])
@login_required
def api_delete_request(request_id):
    user = current_user()
    r = Request.query.get_or_404(request_id)
    if r.user_id != user.id:
        return jsonify({"error": "Unauthorized"}), 403
    db.session.delete(r)
    db.session.commit()
    return jsonify({"ok": True})

# ------------------ PAGES: Create / List Requests (HTML) ------------------
@app.route("/requests/new", methods=["GET"])
@login_required
def new_request():
    # Show form to create a new request / offer
    return render_template("create_request.html")


@app.route("/requests/new", methods=["POST"])
@login_required
def create_request():
    # Handle form submission from create_request.html
    title = request.form.get("title", "").strip()
    category = request.form.get("category", "").strip()
    description = request.form.get("description", "").strip()
    is_offer = request.form.get("is_offer") == "on"
    try:
        expiry_minutes = int(request.form.get("expiry_minutes", 60))
    except (TypeError, ValueError):
        expiry_minutes = 60

    if not title or not category:
        flash("Title and category are required.", "error")
        return redirect(url_for("new_request"))

    expires_at = datetime.utcnow() + timedelta(minutes=expiry_minutes)

    req = Request(
        user_id=current_user().id,
        title=title,
        category=category,
        description=description,
        is_offer=is_offer,
        expires_at=expires_at,
    )
    db.session.add(req)
    db.session.commit()

    flash("Your request has been posted.", "success")
    return redirect(url_for("list_requests"))




@app.route("/need-help", methods=["GET", "POST"])
def need_help():
    """
    Need Help page:
    - If user is logged in -> use their real account
    - If not logged in     -> use Emergency Guest user (quick help)
    """
    user = current_user()
    if user:
        can_post = check_post_limit(user)
    if request.method == "POST":
        if user and not can_post:
            flash("Free limit reached (2 posts/3 weeks). Please go Premium!", "error")
            return redirect(url_for('plans'))
        title = request.form.get("title", "").strip()
        category = request.form.get("category", "").strip()

        area = request.form.get("area", "").strip()
        landmark = request.form.get("landmark", "").strip()
        urgency = request.form.get("urgency", "").strip()
        time_window = request.form.get("time_window", "").strip()
        contact_method = request.form.get("contact_method", "").strip()
        contact_info = request.form.get("contact_info", "").strip()

        description_main = request.form.get("description", "").strip()

        # lat / lng from map picker (optional)
        lat_raw = request.form.get("lat")
        lng_raw = request.form.get("lng")
        try:
            lat = float(lat_raw) if lat_raw else None
            lng = float(lng_raw) if lng_raw else None
        except ValueError:
            lat = None
            lng = None

        try:
            expiry_minutes = int(request.form.get("expiry_minutes", 60))
        except (TypeError, ValueError):
            expiry_minutes = 60

        if not title or not category:
            flash("Title and category are required.", "error")
            return redirect(url_for("need_help"))

        # Decide which user will own this request
        is_guest = False
        if user is None:
            user = get_emergency_user()
            is_guest = True

        # ----------- AI Behavior Verifier (RUN BEFORE SAVING) -----------
        cutoff = datetime.utcnow() - timedelta(minutes=45)
        recent_same_user = (
    Request.query.filter(Request.user_id == user.id)
    .order_by(Request.created_at.desc())
    .limit(10)
    .all()
)

        print("DEBUG recent_same_user:", [r.id for r in recent_same_user])


        print("USING NEW VERIFIER ✅")

        result = verify_request_behavior(
            user=user,
            title=title,
            description=description_main,
            category=category,
            contact_info=contact_info,
            recent_same_user_requests=recent_same_user
        )
        print("DEBUG recent ids:", [r.id for r in recent_same_user])
        print("DEBUG verifier:", result)


        # ✅ Block ONLY guest if scam-like content
        if is_guest and result.get("should_block_guest"):
            flash(
                "Your request looks unsafe (scam-like content). Remove payment/OTP-related words and try again.",
                "error",
            )
            return redirect(url_for("need_help"))
        # ------------------------------------------------------------

        expires_at = datetime.utcnow() + timedelta(minutes=expiry_minutes)

        req = Request(
            user_id=user.id,
            title=title,
            category=category,
            description=description_main,
            is_offer=False,
            area=area or None,
            landmark=landmark or None,
            urgency=urgency or None,
            time_window=time_window or None,
            contact_method=contact_method or None,
            contact_info=contact_info or None,
            lat=lat,
            lng=lng,
            expires_at=expires_at,
        )
        db.session.add(req)
        db.session.commit()
        print("✅ SAVING FLAG FOR REQUEST", req.id, result)

        # Save flag record (non-blocking for normal users)
        if result.get("is_flagged") or result.get("matched_request_id") is not None:
            rf = RequestFlag(
                request_id=req.id,
                risk_score=int(result.get("risk_score", 0)),
                reasons=" | ".join(result.get("reasons", [])),
            )
            db.session.add(rf)
            db.session.commit()
            # ✅ Alert logged-in users that their post got flagged
        if (not is_guest) and (result.get("is_flagged") or result.get("matched_request_id") is not None):
            flash("Your post looks suspicious, so it has been flagged by LifeLine AI.", "warning")

        # ---- Push notifications to helpers ----
        try:
            print(f"[FCM] Trigger nearby helpers for req {req.id}")
            send_fcm_for_need_request(req)
        except Exception as e:
            print("[FCM] nearby help push error:", e)

        # Fallback / additional broadcast: notify all trusted helpers with FCM tokens
        try:
            send_fcm_to_trusted_helpers(
                title="Someone nearby needs help",
                body=f"{user.name} posted: “{title}” (category: {category})",
                data={
                    "type": "NEED_HELP",
                    "request_id": req.id,
                    "category": category,
                },
            )
        except Exception as e:
            print("[FCM] Failed to broadcast need_help notification:", e)

        # ---- Flash + redirect as before ----
        if current_user():
            flash("Your need request has been posted.", "success")
        else:
            flash("Your quick help request has been posted (as guest).", "success")

        return redirect(url_for("need_help"))

    # ---------- GET: show form + sidebar stats ----------
    now = datetime.utcnow()

    posts = (
        Request.query.filter(
            Request.expires_at > now,
            Request.is_offer == False,  # noqa: E712
            Request.status == "open",
        )
        .order_by(Request.created_at.desc())
        .limit(50)
        .all()
    )

    total_need = Request.query.filter(
        Request.is_offer == False,
        Request.expires_at > now,
    ).count()

    total_offer = Request.query.filter(
        Request.is_offer == True,
        Request.expires_at > now,
    ).count()

    categories = (
        db.session.query(Request.category, func.count(Request.id))
        .filter(Request.expires_at > now)
        .group_by(Request.category)
        .all()
    )

    # --- FLAG MAP for showing flagged banner on /need-help ---
    post_ids = [p.id for p in posts]
    flags = RequestFlag.query.filter(RequestFlag.request_id.in_(post_ids)).all()
    flagged_map = {f.request_id: {"risk": f.risk_score, "reasons": f.reasons} for f in flags}


    return render_template(
        "need_help.html",
        posts=posts,
        total_need=total_need,
        total_offer=total_offer,
        categories=categories,
        can_post=can_post,
        google_maps_key=GOOGLE_MAPS_API_KEY,
    )



# I Can Help – login required
@app.route("/can-help", methods=["GET", "POST"])
@login_required
def can_help():
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        category = request.form.get("category", "").strip()

        area = request.form.get("area", "").strip()
        radius_pref = request.form.get("radius_pref", "").strip()
        time_window = request.form.get("time_window", "").strip()
        frequency = request.form.get("frequency", "").strip()
        contact_method = request.form.get("contact_method", "").strip()
        contact_info = request.form.get("contact_info", "").strip()

        description_main = request.form.get("description", "").strip()

        # lat / lng from helper map picker (optional)
        lat_raw = request.form.get("lat")
        lng_raw = request.form.get("lng")
        try:
            lat = float(lat_raw) if lat_raw else None
            lng = float(lng_raw) if lng_raw else None
        except ValueError:
            lat = None
            lng = None

        try:
            expiry_minutes = int(request.form.get("expiry_minutes", 180))
        except (TypeError, ValueError):
            expiry_minutes = 180

        if not title or not category:
            flash("Title and category are required.", "error")
            return redirect(url_for("can_help"))

        image_url = None
        if "image" in request.files:
            img = request.files["image"]
            if img and img.filename != "" and allowed_image(img.filename):
                filename = secure_filename(img.filename)
                filename = f"{int(time.time())}_{filename}"
                save_path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
                img.save(save_path)
                image_url = f"/static/uploads/{filename}"

        expires_at = datetime.utcnow() + timedelta(minutes=expiry_minutes)
        req = Request(
            user_id=current_user().id,
            title=title,
            category=category,
            description=description_main,
            is_offer=True,
            area=area or None,
            radius_pref=radius_pref or None,
            time_window=time_window or None,
            frequency=frequency or None,
            contact_method=contact_method or None,
            contact_info=contact_info or None,
            image_url=image_url,
            lat=lat,
            lng=lng,
            expires_at=expires_at,
        )
        db.session.add(req)
        db.session.commit()
        flash("Your help offer has been posted.", "success")
        return redirect(url_for("can_help"))

    now = datetime.utcnow()
    posts = (
        Request.query.filter(
            Request.expires_at > now,
            Request.is_offer == True,  # noqa: E712
            Request.status == "open",
        )
        .order_by(Request.created_at.desc())
        .limit(50)
        .all()
    )

    total_need = Request.query.filter(
        Request.is_offer == False,
        Request.expires_at > now,
    ).count()
    total_offer = Request.query.filter(
        Request.is_offer == True,
        Request.expires_at > now,
    ).count()
    categories = (
        db.session.query(Request.category, func.count(Request.id))
        .filter(Request.expires_at > now)
        .group_by(Request.category)
        .all()
    )

    return render_template(
        "can_help.html",
        posts=posts,
        total_need=total_need,
        total_offer=total_offer,
        categories=categories,
        google_maps_key=GOOGLE_MAPS_API_KEY,
    )
@app.route("/requests/<int:request_id>/offer", methods=["POST"])
@login_required
def make_offer(request_id):
    req_obj = Request.query.get_or_404(request_id)
    user = current_user()

    # Cannot offer help to your own post
    if req_obj.user_id == user.id:
        flash("You cannot reply to your own post.", "error")
        return redirect(url_for("list_requests", mode="offer" if req_obj.is_offer else "need"))

    # Request must be open
    if req_obj.status != "open":
        flash("This post is no longer active.", "error")
        return redirect(url_for("list_requests", mode="offer" if req_obj.is_offer else "need"))

    # Already contacted?
    existing_offer = Offer.query.filter_by(request_id=req_obj.id, helper_id=user.id).first()
    if existing_offer:
        flash("You have already contacted this person.", "info")
        return redirect(url_for("list_requests", mode="offer" if req_obj.is_offer else "need"))

    # --- FIX: Force new offers to always be 'pending' ---
    new_offer = Offer(
        request_id=req_obj.id,
        helper_id=user.id,
        status="pending"
    )
    db.session.add(new_offer)
    db.session.commit()
        # Push: notify the owner of this request
    try:
        title = "New response on your LifeLine post"
        if req_obj.is_offer:
            body = f"{user.name} is interested in your offer: “{req_obj.title}”"
        else:
            body = f"{user.name} offered help on: “{req_obj.title}”"

        send_fcm_to_user(
            req_obj.user_id,
            title=title,
            body=body,
            data={
                "type": "REQUEST_REPLY",
                "request_id": req_obj.id,
                "from_user_id": user.id,
            },
        )
    except Exception as e:
        print("[FCM] Failed to send offer notification:", e)

    # Auto-start chat
    get_or_create_conversation(req_obj.user_id, user.id)

    flash("Your offer has been sent!", "success")

    return redirect(url_for("list_requests", mode="offer" if req_obj.is_offer else "need"))

@app.route("/debug/fcm-users")
def debug_fcm_users():
    users = User.query.all()
    data = []
    for u in users:
        tokens = [t.token for t in u.fcm_tokens.all()]  # relationship
        data.append({
            "id": u.id,
            "email": u.email,
            "is_trusted_helper": bool(u.is_trusted_helper),
            "token_count": len(tokens),
            "tokens_preview": [tok[:20] + "..." for tok in tokens[:3]],  # show first 3 previews
        })
    return jsonify(data)


@app.route("/requests/offers/<int:offer_id>/accept", methods=["POST"])
@login_required
def accept_offer(offer_id):
    """
    Requester accepts a specific offer. 
    Assigns helper, changes status to 'in_progress'.
    Redirects back to wherever the user clicked the button.
    """
    offer = Offer.query.get_or_404(offer_id)
    req_obj = offer.request
    user = current_user()

    # Security: Only the owner of the request can accept offers
    if req_obj.user_id != user.id:
        flash("Unauthorized action.", "error")
        return redirect(url_for("dashboard"))

    # 1. Update the Request
    req_obj.helper_id = offer.helper_id
    req_obj.status = "in_progress"  # Now it is officially active

    # 2. Update the Offer statuses
    offer.status = "accepted"
    
    # Reject all other pending offers for this request
    other_offers = Offer.query.filter(
        Offer.request_id == req_obj.id, 
        Offer.id != offer_id
    ).all()
    for o in other_offers:
        o.status = "rejected"

    db.session.commit()

    flash(f"You accepted {offer.helper.name}'s help! Check your Dashboard to manage it.", "success")
        #  Push: notify helper that their offer was accepted
    try:
        send_fcm_to_user(
            offer.helper_id,
            title="Your help has been accepted 🎉",
            body=f"{user.name} accepted your offer on: “{req_obj.title}”",
            data={
                "type": "OFFER_ACCEPTED",
                "request_id": req_obj.id,
                "owner_id": user.id,
            },
        )
    except Exception as e:
        print("[FCM] Failed to send offer-accepted notification:", e)

    # SMART REDIRECT: Go back to where the user came from (List or Dashboard)
    return redirect(request.referrer or url_for("dashboard"))



@app.route("/requests/<int:request_id>/complete", methods=["POST"])
@login_required
def complete_request(request_id):
    req_obj = Request.query.get_or_404(request_id)
    user = current_user()

    # 1. PERMISSION CHECK
    is_authorized = False
    if req_obj.is_offer:
        if req_obj.helper_id == user.id: is_authorized = True
    else:
        if req_obj.user_id == user.id: is_authorized = True

    if not is_authorized:
        flash("Unauthorized.", "error")
        return redirect(url_for("dashboard"))

    giver_id = req_obj.user_id if req_obj.is_offer else req_obj.helper_id

    # 2. GET DATA FROM FORM
    rating = int(request.form.get("rating", 5))
    comment = request.form.get("comment", "")
    
    # NEW: Get the actual duration from the user input
    try:
        duration_input = float(request.form.get("hours", 1.0))
    except ValueError:
        duration_input = 1.0
    
    # Ensure realistic limits (e.g., minimum 0.5 hours)
    actual_hours = max(0.1, duration_input)

    # 3. UPDATE REQUEST
    req_obj.status = "completed"
    req_obj.completed_at = datetime.utcnow()

    # 4. AI ANALYSIS
    from reputation_service import analyze_review_quality
    ai_result = analyze_review_quality(comment, rating)

    # 5. CREATE REVIEW (With Duration)
    review = Review(
        request_id=req_obj.id,
        reviewer_id=user.id,
        helper_id=giver_id,
        rating=rating,
        comment=comment,
        duration_hours=actual_hours,  # <--- Saving the real world hours
        sentiment_score=ai_result["sentiment_score"],
        is_flagged_fake=ai_result["is_suspicious"],
        flag_reason=ai_result["flag_reason"]
    )
    db.session.add(review)
    db.session.commit()

    # 6. UPDATE SCORES
    giver_user = User.query.get(giver_id)
    update_user_scores(giver_user)

    flash(f"Verified! {actual_hours} hours added to {giver_user.name}'s profile.", "success")
    return redirect(url_for("dashboard"))

@app.route("/requests")
@login_required
def list_requests():
    """
    List requests as cards.
    mode = 'need'  -> only Need Help posts (is_offer=False)
    mode = 'offer' -> only Offer Help posts (is_offer=True)
    mode = 'all'   -> all open posts
    """
    now = datetime.utcnow()
    mode = request.args.get("mode", "need")  # default: show need-help posts

    q = Request.query.filter(
        Request.expires_at > now,
        Request.status == "open",
    )

    if mode == "offer":
        q = q.filter(Request.is_offer == True)  # noqa: E712
    elif mode == "need":
        q = q.filter(Request.is_offer == False)  # noqa: E712
    # if mode == "all" -> no extra filter

    requests_list = q.order_by(Request.created_at.desc()).all()
    # --- FLAG MAP for showing flagged badge on /requests ---
    req_ids = [r.id for r in requests_list]
    flags = RequestFlag.query.filter(RequestFlag.request_id.in_(req_ids)).all()
    flagged_map = {f.request_id: {"risk": f.risk_score, "reasons": f.reasons} for f in flags}

    # --- NEW LOGIC START ---
    # Find out which requests the current user has already offered to help with
    user = current_user()
    offered_ids = set()
    if user:
        # Get all offers made by this user
        my_offers = Offer.query.filter_by(helper_id=user.id).all()
        # Create a set of request_ids for easy checking
        offered_ids = {o.request_id for o in my_offers}
    # --- NEW LOGIC END ---
    print("flagged_map sample:", list(flagged_map.items())[:5])

    sos_responded_ids = set()
    if user:
        my_sos = SOSResponse.query.filter_by(helper_id=user.id).all()
        sos_responded_ids = {r.request_id for r in my_sos}

    sos_response_counts = {}
    try:
        rows = (
            db.session.query(SOSResponse.request_id, func.count(SOSResponse.id))
            .group_by(SOSResponse.request_id)
            .all()
        )
        sos_response_counts = {int(rid): int(cnt) for rid, cnt in rows}
    except Exception:
        sos_response_counts = {}

    return render_template(
        "list_requests.html",
        requests=requests_list,
        mode=mode,
        offered_ids=offered_ids,  # <--- Pass this to the HTML
        sos_responded_ids=sos_responded_ids,
        sos_response_counts=sos_response_counts,
    )

# ------------------ CHAT PAGES & API ------------------
@app.route("/chat/<int:other_user_id>")
@login_required
def chat_with_user(other_user_id):
    user = current_user()
    other = User.query.get_or_404(other_user_id)
    conv = get_or_create_conversation(user.id, other.id)
    
    # Get all conversations for sidebar
    all_convs = Conversation.query.filter(
        (Conversation.user_a == user.id) | (Conversation.user_b == user.id)
    ).all()
    
    # Build conversation list with details
    conversations_list = []
    for c in all_convs:
        other_user_in_conv = User.query.get(c.user_a if c.user_b == user.id else c.user_b)
        if other_user_in_conv:
            last_msg = ChatMessage.query.filter_by(conversation_id=c.id).order_by(ChatMessage.created_at.desc()).first()
            unread_count = ChatMessage.query.filter_by(conversation_id=c.id, read=False).filter(ChatMessage.sender_id != user.id).count()
            conversations_list.append({
                'id': c.id,
                'other_user': other_user_in_conv,
                'last_message': last_msg.text if last_msg else '',
                'last_message_time': last_msg.created_at if last_msg else None,
                'unread_count': unread_count,
                'is_active': c.id == conv.id
            })
    
    # Sort by last message time (newest first)
    conversations_list.sort(key=lambda x: x['last_message_time'] if x['last_message_time'] else datetime.min, reverse=True)
    
    # prefetch last 100 messages
    messages = (
        ChatMessage.query.filter_by(conversation_id=conv.id).order_by(ChatMessage.created_at.asc()).limit(100).all()
    )
    # mark unread messages (sent by other user) as read when opening the conversation
    try:
        unread_msgs = ChatMessage.query.filter_by(conversation_id=conv.id, read=False).filter(ChatMessage.sender_id != user.id).all()
        if unread_msgs:
            for m in unread_msgs:
                m.read = True
            db.session.commit()
            # notify room that messages were read (so other clients can update UI)
            for m in unread_msgs:
                try:
                    socketio.emit('read', {'message_id': m.id, 'conversation_id': conv.id}, room=f"chat_{conv.id}")
                except Exception:
                    pass
    except Exception:
        # best-effort only; don't block rendering on notification errors
        db.session.rollback()
    # serialize messages for JSON/template safety
    messages_serialized = [serialize_message(m) for m in messages]
    return render_template("chat.html", conversation=conv, other_user=other, messages=messages_serialized, conversations=conversations_list)


@app.route("/chat")
def chat_index():
    user = current_user()
    if not user:
        # allow quick guest access to chat via Emergency Guest account
        user = get_emergency_user()
        login_user(user)
    # list conversations for the user
    convs = Conversation.query.filter(
        (Conversation.user_a == user.id) | (Conversation.user_b == user.id)
    ).all()

    conversations = []
    for c in convs:
        other_id = c.user_a if c.user_b == user.id else c.user_b
        other = User.query.get(other_id)
        last = (
            ChatMessage.query.filter_by(conversation_id=c.id).order_by(ChatMessage.created_at.desc()).first()
        )
        # count unread messages in this conversation that were sent by the other user
        unread_count = ChatMessage.query.filter_by(conversation_id=c.id, read=False).filter(ChatMessage.sender_id != user.id).count()
        conversations.append({
            "conversation": c,
            "other": other,
            "last_message": last.text if last else None,
            "last_time": int(last.created_at.timestamp()) if last else None,
            "unread_count": unread_count,
        })

    # also show some nearby helpers to start a new chat (trusted helpers)
    helpers = User.query.filter(User.is_trusted_helper == True, User.id != user.id).limit(10).all()
    return render_template("chat_index.html", conversations=conversations, helpers=helpers)
@app.route("/suggestions")
@login_required
def suggestions_dashboard():
    """Smart Suggestion AI Dashboard"""
    user = current_user()
    # Note: User location is optional; API will use defaults if not set
    return render_template("suggestions_dashboard.html")


@app.route("/api/nearby-requests", methods=["GET"])
def api_nearby_requests():
    """Get nearby requests using user or fallback location"""
    try:
        user = current_user()
        limit = request.args.get("limit", type=int, default=10)

        fallback_lat = float(os.getenv("DEFAULT_LAT", "23.8103"))
        fallback_lng = float(os.getenv("DEFAULT_LNG", "90.4125"))

        user_lat = request.args.get("lat", type=float)
        user_lng = request.args.get("lng", type=float)

        if user_lat is None:
            user_lat = getattr(user, "lat", None) or fallback_lat
        if user_lng is None:
            user_lng = getattr(user, "lng", None) or fallback_lng

        exclude_user_id = getattr(user, "id", None)

        nearby = LocationMatcher.get_nearby_requests(
            db=db,
            Request=Request,
            user_lat=user_lat,
            user_lng=user_lng,
            exclude_user_id=exclude_user_id,
            limit=limit
        )

        return jsonify({"requests": nearby}), 200
    except Exception as e:
        print(f"[API] Error in api_nearby_requests: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/dashboard")
@login_required
def dashboard():
    user = current_user()

    # 1. Update scores before showing them
    update_user_scores(user)

    # 2. CALCULATE "PEOPLE HELPED" CORRECTLY
    # Case A: I responded to someone's "Need Help" request
    helped_requests = Request.query.filter(
        Request.helper_id == user.id, 
        Request.is_offer == False, 
        Request.status == "completed"
    ).all()

    # Case B: I posted an "Offer" and someone accepted it (I am the giver)
    fulfilled_offers = Request.query.filter(
        Request.user_id == user.id, 
        Request.is_offer == True, 
        Request.status == "completed"
    ).all()

    # Total list of good deeds
    all_good_deeds = helped_requests + fulfilled_offers
    
    # Sort by date (newest first) for the History list
    all_good_deeds.sort(key=lambda x: x.completed_at if x.completed_at else datetime.min, reverse=True)

    helped_count = len(all_good_deeds)

    # 3. CALCULATE REAL HOURS (From Review table)
    # We sum up duration_hours from reviews where THIS user was the "helper_id" (Giver)
    total_hours_db = db.session.query(func.sum(Review.duration_hours))\
        .filter(Review.helper_id == user.id, Review.is_flagged_fake == False)\
        .scalar()
    
    total_hours = round(total_hours_db or 0.0, 2)

    # 4. PREPARE HISTORY DATA FOR TEMPLATE
    history = []
    for r in all_good_deeds[:5]: # Show last 5
        # Find the review to get specific hours for this task
        # Note: In the review table, 'helper_id' is the Giver.
        rev = Review.query.filter_by(request_id=r.id, helper_id=user.id).first()
        hours_spent = round(rev.duration_hours, 1) if rev else 0

        history.append({
            "title": r.title,
            "category": r.category or "General",
            "hours": hours_spent,
            "date": r.completed_at.strftime("%b %d, %Y") if r.completed_at else "",
        })

    # Badge Logic
    badge, badge_color = user.calculate_badge()

    stats = {
        "badge": badge,
        "badge_color": badge_color,
        "helped": helped_count,      # <--- Now correct (Creator gets credit for offers)
        "total_hours": total_hours,
        "trust": user.trust_score or 0,
        "kindness": user.kindness_score or 0,
    }

    # Chart Data
    chart_labels = ["Start", "Today"] 
    chart_trust = [50, stats["trust"]]
    chart_kindness = [0, stats["kindness"]]

    return render_template(
        "dashboard.html",
        user=user,
        stats=stats,
        history=history,
        chart_labels=chart_labels,
        chart_trust=chart_trust,
        chart_kindness=chart_kindness,
    )


@app.route("/api/user/location", methods=["POST"])
@login_required
def update_user_location():
    """
    Updates the logged-in users live location.
    Required for receiving 'nearby' help requests.
    """
    data = request.get_json() or {}
    try:
        lat = float(data.get("lat"))
        lng = float(data.get("lng"))
        
        user = current_user()
        user.lat = lat
        user.lng = lng
        db.session.commit()
        
        return jsonify({"ok": True})
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid location data"}), 400
    

# In app.py
# --- PLANS PAGE ---
@app.route("/plans")
def plans():
    user = current_user()

    latest_payment = None
    if user is not None:
        latest_payment = (
            Payment.query.filter_by(user_id=user.id)
            .order_by(Payment.created_at.desc())
            .first()
        )

    return render_template("plans.html", latest_payment=latest_payment)

# --- MANUAL PAYMENT SUBMISSION ---
@app.route("/pay/manual/submit", methods=["POST"])
@login_required
def submit_manual_payment():
    user = current_user()
    bkash_number = request.form.get("bkash_number", "").strip()
    trx_id = request.form.get("trx_id", "").strip()
    amount_raw = request.form.get("amount", "").strip()
    plan_name = request.form.get("plan_name", "").strip()
    
    if not bkash_number or not trx_id:
        flash("Please provide both bKash number and Transaction ID.", "error")
        return redirect(url_for('plans'))
        
    # Check for duplicate Trx ID
    existing = Payment.query.filter_by(trx_id=trx_id).first()
    if existing:
        flash("This Transaction ID has already been used.", "error")
        return redirect(url_for('plans'))

    # Prevent spamming multiple pending submissions.
    existing_pending = Payment.query.filter_by(user_id=user.id, status="pending").first()
    if existing_pending:
        flash("Your previous payment is still under review. Please wait for admin approval/rejection.", "error")
        return redirect(url_for("plans"))

    allowed_amounts = {500.0, 1000.0, 2000.0, 5000.0}
    try:
        amount = float(amount_raw)
    except (TypeError, ValueError):
        amount = None

    if amount not in allowed_amounts:
        flash("Invalid plan amount selected. Please try again.", "error")
        return redirect(url_for("plans"))

    # Create Payment Request
    pay = Payment(
        user_id=user.id,
        amount=amount,
        bkash_number=bkash_number,
        trx_id=trx_id,
        status="pending"
    )
    db.session.add(pay)
    db.session.commit()
    
    if plan_name:
        flash(f"Payment submitted for {plan_name} (৳{int(amount)}). Wait for Admin approval to activate Premium.", "success")
    else:
        flash(f"Payment submitted (৳{int(amount)}). Wait for Admin approval to activate Premium.", "success")
    return redirect(url_for('dashboard'))

# --- ADMIN DASHBOARD ---
@app.route("/admin/dashboard")
@login_required
def admin_dashboard():
    user = current_user()
    # Simple security: Check is_admin flag OR hardcoded email
    if not user.is_admin and user.email != "admin@lifeline.com":
        flash("Access Denied: Admins Only.", "error")
        return redirect(url_for('home'))

    # --- Monitoring KPIs ---
    total_users = User.query.count()
    premium_users = User.query.filter_by(is_premium=True).count()
    trusted_helpers = User.query.filter_by(is_trusted_helper=True).count()
    pending_helper_verifications = User.query.filter_by(id_verification_status="pending").count()

    open_requests = Request.query.filter_by(status="open").count()
    claimed_requests = Request.query.filter_by(status="claimed").count()
    closed_requests = Request.query.filter_by(status="closed").count()

    active_pings = EmotionalPing.query.filter_by(is_active=True).count()
        
    # Get all pending payments
    pending_payments = Payment.query.filter_by(status="pending").order_by(Payment.created_at.desc()).all()
    
    # Get recent approved payments (for history)
    history = Payment.query.filter(Payment.status != "pending").order_by(Payment.created_at.desc()).limit(20).all()

    # --- Recent activity ---
    latest_users = User.query.order_by(User.id.desc()).limit(10).all()
    latest_requests = Request.query.order_by(Request.created_at.desc()).limit(10).all()
    latest_sos = SOSResponse.query.order_by(SOSResponse.created_at.desc()).limit(10).all()

    # --- Charts (last N days) ---
    days = 14
    start_dt = datetime.utcnow() - timedelta(days=days - 1)
    start_date = start_dt.date()
    date_labels = [(start_date + timedelta(days=i)).isoformat() for i in range(days)]

    req_counts_rows = (
        db.session.query(func.date(Request.created_at), func.count(Request.id))
        .filter(Request.created_at >= start_dt)
        .group_by(func.date(Request.created_at))
        .all()
    )
    req_counts = {str(d): int(c) for d, c in req_counts_rows if d is not None}
    chart_requests_created = [req_counts.get(lbl, 0) for lbl in date_labels]

    sos_counts_rows = (
        db.session.query(func.date(SOSResponse.created_at), func.count(SOSResponse.id))
        .filter(SOSResponse.created_at >= start_dt)
        .group_by(func.date(SOSResponse.created_at))
        .all()
    )
    sos_counts = {str(d): int(c) for d, c in sos_counts_rows if d is not None}
    chart_sos_responses = [sos_counts.get(lbl, 0) for lbl in date_labels]

    pay_counts_rows = (
        db.session.query(func.date(Payment.created_at), Payment.status, func.count(Payment.id))
        .filter(Payment.created_at >= start_dt)
        .group_by(func.date(Payment.created_at), Payment.status)
        .all()
    )
    pay_counts = {}
    for d, status, c in pay_counts_rows:
        if d is None:
            continue
        pay_counts[(str(d), status)] = int(c)
    chart_payments_pending = [pay_counts.get((lbl, "pending"), 0) for lbl in date_labels]
    chart_payments_approved = [pay_counts.get((lbl, "approved"), 0) for lbl in date_labels]
    chart_payments_rejected = [pay_counts.get((lbl, "rejected"), 0) for lbl in date_labels]

    stats = {
        "total_users": total_users,
        "premium_users": premium_users,
        "trusted_helpers": trusted_helpers,
        "pending_helper_verifications": pending_helper_verifications,
        "open_requests": open_requests,
        "claimed_requests": claimed_requests,
        "closed_requests": closed_requests,
        "active_pings": active_pings,
        "pending_payments": len(pending_payments),
    }

    charts = {
        "labels": date_labels,
        "requests_created": chart_requests_created,
        "sos_responses": chart_sos_responses,
        "payments_pending": chart_payments_pending,
        "payments_approved": chart_payments_approved,
        "payments_rejected": chart_payments_rejected,
    }

    return render_template(
        "admin_dashboard.html",
        pending=pending_payments,
        history=history,
        stats=stats,
        charts=charts,
        latest_users=latest_users,
        latest_requests=latest_requests,
        latest_sos=latest_sos,
    )

# --- ADMIN ACTION (APPROVE/REJECT) ---
@app.route("/admin/payment/<int:payment_id>/<action>", methods=["POST"])
@login_required
def admin_payment_action(payment_id, action):
    user = current_user()
    if not user.is_admin and user.email != "admin@lifeline.com":
        return jsonify({"error": "Unauthorized"}), 403
        
    payment = Payment.query.get_or_404(payment_id)
    
    if action == "approve":
        payment.status = "approved"
        # Grant Premium
        payment.user.is_premium = True
        payment.user.premium_expiry = datetime.utcnow() + timedelta(days=30)
        flash(f"Approved payment for {payment.user.name}", "success")
        
        # Notify User
        push_notification(payment.user.id, "system", "🎉 Premium Activated! Your payment was approved.")
        
    elif action == "reject":
        payment.status = "rejected"
        flash(f"Rejected payment for {payment.user.name}", "error")
        push_notification(payment.user.id, "system", "❌ Payment Rejected. Please check Trx ID.")
        
    db.session.commit()
    return redirect(url_for('admin_dashboard'))

@app.route("/sos/trigger", methods=["POST"])
@login_required
def trigger_sos():
    user = current_user()

    if not user:
        flash("Please log in again to send SOS.", "error")
        return redirect(url_for("login", next=request.path))

    # Prefer client-provided GPS for accuracy; fallback to user's last-known location
    sos_lat = None
    sos_lng = None
    try:
        lat_raw = request.form.get("lat")
        lng_raw = request.form.get("lng")
        if lat_raw is not None and lng_raw is not None:
            sos_lat = float(lat_raw)
            sos_lng = float(lng_raw)
    except (TypeError, ValueError):
        sos_lat = None
        sos_lng = None

    used_fallback_location = False
    if sos_lat is None or sos_lng is None:
        # Fallback to last-known location (or configured defaults) so SOS can still be sent.
        try:
            if getattr(user, "lat", None) is not None and getattr(user, "lng", None) is not None:
                sos_lat = float(user.lat)
                sos_lng = float(user.lng)
            else:
                sos_lat = float(os.getenv("DEFAULT_LAT", "23.8103"))
                sos_lng = float(os.getenv("DEFAULT_LNG", "90.4125"))
            used_fallback_location = True
        except Exception:
            flash(
                "Could not determine your location. Please enable location permission and try again.",
                "error",
            )
            return redirect(request.referrer or url_for("map_page"))

    created_at = datetime.utcnow()
    expires_at = created_at + timedelta(minutes=15)

    sos_req = Request(
        user_id=user.id,
        title="🚨 SOS Alert",
        category="sos",
        description=f"Emergency SOS from {user.name}.",
        lat=sos_lat,
        lng=sos_lng,
        urgency="emergency",
        is_offer=False,
        created_at=created_at,
        expires_at=expires_at,
        status="open",
    )
    db.session.add(sos_req)
    db.session.commit()

    # Update last-known location if GPS was provided
    if sos_lat is not None and sos_lng is not None:
        try:
            user.lat = sos_lat
            user.lng = sos_lng
            db.session.commit()
        except Exception:
            db.session.rollback()

    # Broadcast to all trusted helpers (verified helpers). Location filtering can be
    # added later; for now "all trusted helpers" matches the SOS broadcast intent.
    helpers = User.query.filter(User.is_trusted_helper == True, User.id != user.id).all()  # noqa: E712

    for h in helpers:
        try:
            push_notification(
                user_id=h.id,
                type="sos",
                message=f"🚨 SOS: {user.name} needs immediate help nearby!",
                link=url_for("map_page", focus_request_id=sos_req.id),
            )
        except Exception:
            pass

        try:
            if h.fcm_tokens.count() > 0:
                send_fcm_to_user(
                    h,
                    title="🚨 SOS ALERT!",
                    body=f"EMERGENCY: {user.name} needs help nearby!",
                    data={"type": "SOS_ALERT", "request_id": str(sos_req.id)},
                )
        except Exception:
            pass

    # Persist active SOS id so the UI can keep the fallback timer across pages.
    try:
        session["active_sos_request_id"] = sos_req.id
    except Exception:
        pass

    if used_fallback_location:
        flash(
            "SOS SENT using your last saved/default location. Enable Precise location for accuracy.",
            "error",
        )
    else:
        flash("SOS SENT! Trusted helpers have been alerted.", "error")
    return redirect(url_for("map_page", focus_request_id=sos_req.id, sos_caller=1))


@app.route("/sos/<int:request_id>/accept/<int:helper_id>", methods=["POST"])
@login_required
def accept_sos_responder(request_id, helper_id):
    """SOS owner selects a responder to connect with (enables chat/call + completion review flow)."""
    user = current_user()
    req_obj = Request.query.get_or_404(request_id)

    if req_obj.user_id != user.id:
        flash("Unauthorized.", "error")
        return redirect(url_for("dashboard"))

    if (req_obj.category or "").lower() != "sos":
        flash("Not an SOS request.", "error")
        return redirect(url_for("dashboard"))

    if req_obj.status != "open":
        flash("This SOS is no longer open.", "error")
        return redirect(url_for("dashboard"))

    existing = SOSResponse.query.filter_by(request_id=req_obj.id, helper_id=helper_id).first()
    if not existing:
        flash("That user has not responded to this SOS.", "error")
        return redirect(url_for("dashboard"))

    helper_user = User.query.get(helper_id)
    if not helper_user:
        flash("Responder not found.", "error")
        return redirect(url_for("dashboard"))

    req_obj.helper_id = helper_id
    req_obj.status = "in_progress"
    db.session.commit()

    try:
        push_notification(
            user_id=helper_id,
            type="sos_connected",
            message=f"✅ {user.name} accepted your SOS response. Please coordinate in chat.",
            link=url_for("map_page", focus_request_id=req_obj.id),
        )
    except Exception:
        pass

    flash("Connected with the responder. You can chat/call and mark complete.", "success")
    return redirect(url_for("dashboard"))


@app.route("/api/sos/<int:request_id>/respond", methods=["POST"])
@login_required
def api_sos_respond(request_id):
    user = current_user()
    if not user or not user.is_trusted_helper:
        return jsonify({"error": "Only verified helpers can respond"}), 403

    req_obj = Request.query.get_or_404(request_id)
    if (req_obj.category or "").lower() != "sos":
        return jsonify({"error": "Not an SOS request"}), 400
    if req_obj.status != "open":
        return jsonify({"error": "SOS is not open"}), 400
    if req_obj.user_id == user.id:
        return jsonify({"error": "Cannot respond to your own SOS"}), 400

    existing = SOSResponse.query.filter_by(request_id=req_obj.id, helper_id=user.id).first()
    if existing:
        return jsonify({"ok": True, "already": True})

    data = request.get_json(silent=True) or {}
    responder_lat = None
    responder_lng = None
    try:
        if data.get("lat") is not None and data.get("lng") is not None:
            responder_lat = float(data.get("lat"))
            responder_lng = float(data.get("lng"))
    except (TypeError, ValueError):
        responder_lat = None
        responder_lng = None

    # Fallback to helper's last known location if available
    if responder_lat is None or responder_lng is None:
        responder_lat = getattr(user, "lat", None)
        responder_lng = getattr(user, "lng", None)

    resp = SOSResponse(
        request_id=req_obj.id,
        helper_id=user.id,
        responder_lat=responder_lat,
        responder_lng=responder_lng,
    )
    db.session.add(resp)
    db.session.commit()

    try:
        push_notification(
            user_id=req_obj.user_id,
            type="sos_response",
            message=f"✅ {user.name} is responding to your SOS.",
            link=url_for("map_page", focus_request_id=req_obj.id),
        )
    except Exception:
        pass

    return jsonify({"ok": True})


@app.route("/api/sos/<int:request_id>/status", methods=["GET"])
@login_required
def api_sos_status(request_id):
    req_obj = Request.query.get_or_404(request_id)
    if (req_obj.category or "").lower() != "sos":
        return jsonify({"error": "Not an SOS request"}), 400

    viewer = current_user()

    responders = (
        SOSResponse.query.filter_by(request_id=req_obj.id)
        .order_by(SOSResponse.created_at.asc())
        .all()
    )

    names = []
    responder_locations = []
    for r in responders:
        u = User.query.get(r.helper_id)
        if u:
            names.append(u.name)
            if viewer and viewer.id == req_obj.user_id:
                responder_locations.append(
                    {
                        "helper_id": u.id,
                        "name": u.name,
                        "lat": r.responder_lat,
                        "lng": r.responder_lng,
                    }
                )

    # Caller fallback: if no helpers respond within 60 seconds, the UI can prompt
    # the SOS caller to call their saved emergency number (simulated via tel: link).
    # We only expose this flag to the SOS owner.
    should_call = False
    seconds_remaining = None
    try:
        if viewer and viewer.id == req_obj.user_id:
            now = datetime.utcnow()
            elapsed_s = (now - req_obj.created_at).total_seconds() if req_obj.created_at else 0
            seconds_remaining = max(0, int(60 - elapsed_s))
            should_call = (elapsed_s >= 60) and (len(responders) == 0)

            if len(responders) > 0:
                try:
                    session.pop("active_sos_request_id", None)
                except Exception:
                    pass
    except Exception:
        should_call = False
        seconds_remaining = None

    return jsonify(
        {
            "request_id": req_obj.id,
            "responded": len(responders) > 0,
            "responders_count": len(responders),
            "responders": names[:10],
            "responder_locations": responder_locations,
            "should_call": should_call,
            "seconds_remaining": seconds_remaining,
        }
    )

@app.route("/api/conversations/<int:conv_id>/messages")
@login_required
def api_get_messages(conv_id):
    conv = Conversation.query.get_or_404(conv_id)
    user = current_user()
    if user.id not in conv.participants():
        return jsonify({"error": "Unauthorized"}), 403
    msgs = ChatMessage.query.filter_by(conversation_id=conv.id).order_by(ChatMessage.created_at.asc()).all()
    return jsonify({"messages": [serialize_message(m) for m in msgs]})

@app.route("/api/notifications")
def api_notifications():
    if "user_id" not in session:
        return jsonify([])

    user_id = session["user_id"]
    
    notes = Notification.query.filter_by(user_id=user_id)\
        .order_by(Notification.created_at.desc())\
        .limit(10).all()

    return jsonify([
        {
            "id": n.id,
            "type": n.type,
            "message": n.message,
            "link": n.link,
            "created_at": n.created_at.strftime("%I:%M %p")
        }
        for n in notes
    ])
@app.route("/debug/test-push")
@login_required
def debug_test_push():
    user = current_user()
    if not user or not getattr(user, "fcm_token", None):
        return "No FCM token for this user. Make sure initFCM ran.", 400

    send_push_to_user(
        user,
        title="LifeLine test notification",
        body="If you see this, FCM is working ",
        data={
            "type": "DEBUG",
            "user_id": str(user.id),
        },
    )
    return "Test push sent!"



@app.route("/api/conversations/<int:conv_id>/mark_read", methods=["POST"])
@login_required
def api_mark_conversation_read(conv_id):
    conv = Conversation.query.get_or_404(conv_id)
    user = current_user()
    if user.id not in conv.participants():
        return jsonify({"error": "Unauthorized"}), 403

    # mark all unread messages sent by the other participant as read
    try:
        unread_msgs = ChatMessage.query.filter_by(conversation_id=conv.id, read=False).filter(ChatMessage.sender_id != user.id).all()
        ids = []
        if unread_msgs:
            for m in unread_msgs:
                m.read = True
                ids.append(m.id)
            db.session.commit()
            # emit read events so other clients (index page or sender) can update UI
            for mid in ids:
                try:
                    socketio.emit('read', {'message_id': mid, 'conversation_id': conv.id}, room=f"chat_{conv.id}")
                except Exception:
                    pass
        return jsonify({"marked": len(ids)})
    except Exception:
        db.session.rollback()
        return jsonify({"error": "failed"}), 500

@app.route("/api/notification-count")
def api_notification_count():
    if "user_id" not in session:
        return jsonify({"count": 0})

    user_id = session["user_id"]

    unread = Notification.query.filter_by(user_id=user_id, is_read=False).count()
    return jsonify({"count": unread})

@app.route("/api/notifications/read", methods=["POST"])
def api_mark_notifications_read():
    if "user_id" not in session:
        return jsonify({"ok": False})

    user_id = session["user_id"]

    Notification.query.filter_by(user_id=user_id, is_read=False)\
        .update({"is_read": True})

    db.session.commit()

    return jsonify({"ok": True})


@app.route("/api/translate", methods=["POST"])
@login_required
def api_translate():
    data = request.get_json() or {}
    text = data.get("text")
    target = data.get("target")
    if not text or not target:
        return jsonify({"error": "text and target required"}), 400

    def translate_text(t, tgt):
        try:
            if 'gcloud_translate_client' in globals() and gcloud_translate_client:
                resp = gcloud_translate_client.translate(t, target_language=tgt)
                return resp.get("translatedText")
            elif 'gt_translator' in globals() and gt_translator:
                return gt_translator.translate(t, dest=tgt).text
        except Exception:
            pass
        return t

    translated = translate_text(text, target)
    return jsonify({"translated": translated})

@app.route("/api/fcm/register", methods=["POST"])
@login_required
def api_register_fcm():
    user = current_user()
    data = request.get_json() or {}
    token = (data.get("token") or "").strip()

    if not token:
        return jsonify({"error": "token required"}), 400

    # If already exists for this user, do nothing
    existing = FCMToken.query.filter_by(token=token).first()
    if existing:
        if existing.user_id != user.id:
            existing.user_id = user.id  # token moved to another user
            db.session.commit()
        return jsonify({"ok": True, "message": "token already registered"})

    db.session.add(FCMToken(user_id=user.id, token=token))
    db.session.commit()
    return jsonify({"ok": True})


@app.route("/api/trusted_helpers", methods=["GET"])
@login_required
def api_trusted_helpers():
    user = current_user()
    helpers = User.query.filter(
        User.is_trusted_helper == True,
        User.id != user.id
    ).all()

    # simple ordering: kindness desc, then trust desc
    helpers.sort(key=lambda u: ((u.kindness_score or 0), (u.trust_score or 0)), reverse=True)

    return jsonify({
        "helpers": [
            {
                "id": h.id,
                "name": h.name,
                "trust_score": int(h.trust_score or 0),
                "kindness_score": int(h.kindness_score or 0),
            }
            for h in helpers
        ]
    })



@app.route('/firebase-messaging-sw.js')
def firebase_sw():
    return app.send_static_file('firebase-messaging-sw.js')


# ------------------ SOCKET.IO EVENTS ------------------ 
online_users = set()
sid_to_user = {}

# --- REPLACED/MODIFIED BLOCK (for Step 1) ---
@socketio.on("join")
@jwt_required()
def on_join(data):
    user_id = int(get_jwt_identity())

    sid_to_user[request.sid] = user_id
    online_users.add(user_id)

    join_room(f"user_{user_id}")
    print(f"[SOCKETIO] User {user_id} joined personal room user_{user_id}")


@socketio.on("disconnect")
def on_disconnect():
    uid = sid_to_user.pop(request.sid, None)
    if uid:
        online_users.discard(uid)
        print(f"[SOCKETIO] User {uid} disconnected")


# --- NEW BLOCK (for Step 2) ---
import random

@socketio.on("emotional_ping")
@jwt_required()
def on_emotional_ping(data=None):
    user_id = get_jwt_identity()
    ping_user = User.query.get(int(user_id))
    if not ping_user:
        return

    helpers = User.query.filter(
        User.is_trusted_helper == True,
        User.id != ping_user.id
    ).all()

    if not helpers:
        emit("ping_failed", {"message": "No trusted helpers available right now."}, room=request.sid)
        return

    for h in helpers:
        socketio.emit(
            "emotional_alert",
            {"sender_id": ping_user.id, "sender_name": ping_user.name},
            room=f"user_{h.id}"
        )

    emit("ping_success", {"message": "Ping sent to all trusted helpers."}, room=request.sid)

@socketio.on("leave")
def on_leave(data):
    conv_id = data.get("conversation_id")
    user = current_user()
    if not user or not conv_id:
        return
    room = f"chat_{conv_id}"
    leave_room(room)
    emit("user_left", {"user_id": user.id}, room=room)


@socketio.on("typing")
def on_typing(data):
    conv_id = data.get("conversation_id")
    user = current_user()
    if not user or not conv_id:
        return
    room = f"chat_{conv_id}"
    emit("typing", {"user_id": user.id}, room=room, include_self=False)


@socketio.on("stop_typing")
def on_stop_typing(data):
    conv_id = data.get("conversation_id")
    user = current_user()
    if not user or not conv_id:
        return
    room = f"chat_{conv_id}"
    emit("stop_typing", {"user_id": user.id}, room=room, include_self=False)

@socketio.on("send_message")
def on_send_message(data):
    """
    Handle a chat message and send a push notification to the other participant.
    Expected data:
      {
        "conversation_id": int,
        "text": str,
        "temp_id": optional client-side id,
        "language": optional,
        "file_data": optional,
        "file_name": optional,
        "file_size": optional,
        "is_image": optional
      }
    """
    conv_id = data.get("conversation_id")
    text = (data.get("text") or "").strip()
    temp_id = data.get("temp_id")
    lang = data.get("language")
    file_data = data.get("file_data")
    file_name = data.get("file_name")
    file_size = data.get("file_size")
    is_image = data.get("is_image", False)

    user = current_user()
    print(f"[SEND_MESSAGE] from user={user.id if user else None}, conv={conv_id}, text='{text[:50]}'")

    # ---------- Validation ----------
    if not user or not conv_id:
        print("[SEND_MESSAGE] Missing user or conversation_id")
        return

    conv = Conversation.query.get(conv_id)
    if not conv:
        print(f"[SEND_MESSAGE] Conversation {conv_id} not found")
        return

    if user.id not in conv.participants():
        print(f"[SEND_MESSAGE] User {user.id} is not a participant of conv {conv_id}")
        return

    if not text and not file_data:
        print("[SEND_MESSAGE] Empty message and no file; not saving")
        return

    # ---------- Save message ----------
    msg = ChatMessage(
        conversation_id=conv.id,
        sender_id=user.id,
        text=text or "",
        language=lang,
    )
    db.session.add(msg)
    db.session.commit()
    print(f"[SEND_MESSAGE] Saved message {msg.id} to DB")
    
        # ---- NEW: If this conversation is from an Emotional Ping, notify sender on first helper reply ----
    try:
        ping = EmotionalPing.query.filter_by(conversation_id=conv.id).order_by(EmotionalPing.created_at.desc()).first()
        if ping:
            # helper replying?
            if user.id == ping.helper_id and not ping.helper_replied:
                # mark once
                ping.helper_replied = True
                db.session.commit()

                # create bell notification for sender
                push_notification(
                    user_id=ping.sender_id,
                    type="emotional_reply",
                    message=f"💬 {user.name} replied to your emotional ping.",
                    link=url_for("chat_with_user", other_user_id=user.id)
                )
    except Exception as e:
        print("[PING REPLY NOTIFY] error:", e)
        db.session.rollback()


    # ---------- Build payload for Socket.IO ----------
    payload = serialize_message(msg)
    payload["temp_id"] = temp_id

    if file_data:
        payload["has_file"] = True
        payload["file_name"] = file_name
        payload["file_size"] = file_size
        payload["is_image"] = is_image

    room = f"chat_{conv.id}"
    print(f"[SEND_MESSAGE] Emitting 'new_message' to room {room}")
    emit("new_message", payload, room=room)

    # ---------- Determine the other participant ----------
    other_id = conv.user_a if conv.user_b == user.id else conv.user_b
    other_user = User.query.get(other_id) if other_id else None
    has_token = bool(other_user and getattr(other_user, 'fcm_tokens', None) and other_user.fcm_tokens.count() > 0)
    print(f"[SEND_MESSAGE] Other participant user_id={other_id}, has_token={has_token}")

    # ---------- Send push to the other participant ----------

    try:
        # Check if the other user exists and is not the sender
        if other_user and other_user.id != user.id:
            # Check if the user has any registered FCM tokens using the new relationship
            if other_user.fcm_tokens.count() > 0:
                preview = text or "[Attachment]"
                if len(preview) > 80:
                    preview = preview[:80] + "…"

                # Use the new multi-token handler function
                success = send_fcm_to_user(
                    other_user,
                    title=f"New message from {user.name}",
                    body=preview,
                    data={
                        "type": "NEW_CHAT_MESSAGE",
                        "conversation_id": str(conv.id),
                        "sender_id": str(user.id),
                    },
                )
                
                if success:
                    print(f"[FCM] Chat push sent to user {other_user.id}")
                else:
                    # This branch means the server received failure from Firebase
                    print(f"[FCM] Chat push failed to send to user {other_user.id} (Firebase error)")
            else:
                # This branch means no tokens were registered for the user
                print(f"[FCM] Other user {other_user.id} has no registered FCM tokens; skipping chat push")
                
    except Exception as e:
        # This will now catch other errors, not the persistent AttributeError
        print("[FCM] Error sending chat push:", e)


@socketio.on("message_delivered")
def on_message_delivered(data):
    # data: {message_id}
    mid = data.get("message_id")
    user = current_user()
    if not user or not mid:
        return
    msg = ChatMessage.query.get(mid)
    if not msg:
        return
    msg.delivered = True
    db.session.commit()
    room = f"chat_{msg.conversation_id}"
    emit("delivered", {"message_id": mid}, room=room)


@socketio.on("message_read")
def on_message_read(data):
    mid = data.get("message_id")
    user = current_user()
    if not user or not mid:
        return
    msg = ChatMessage.query.get(mid)
    if not msg:
        return
    msg.read = True
    db.session.commit()
    room = f"chat_{msg.conversation_id}"
    emit("read", {"message_id": mid}, room=room)


@app.route("/chat/<int:conv_id>/delete", methods=["POST"])
@login_required
def delete_conversation(conv_id):
    """Delete a conversation (only if user is a participant)."""
    user = current_user()
    conv = Conversation.query.get_or_404(conv_id)
    
    # Check if user is a participant in this conversation
    if user.id not in conv.participants():
        flash("You are not allowed to delete this conversation.", "error")
        return redirect(url_for("chat_index"))
    
    # Delete all messages in this conversation
    ChatMessage.query.filter_by(conversation_id=conv.id).delete()
    # Delete the conversation
    db.session.delete(conv)
    db.session.commit()
    
    flash("Conversation deleted.", "success")
    return redirect(url_for("chat_index"))


@app.route("/requests/<int:request_id>/delete", methods=["POST"])
@login_required
def delete_request(request_id):
    # Allow owner to delete their request via HTML form
    r = Request.query.get_or_404(request_id)
    if r.user_id != current_user().id:
        flash("You are not allowed to delete this request.", "error")
        return redirect(url_for("list_requests"))

    db.session.delete(r)
    db.session.commit()
    flash("Request removed.", "success")
    return redirect(url_for("list_requests"))

@app.route("/debug/fcm-test")
def debug_fcm_test():
    user = User.query.get(3)  # 🔥 FORCE user id 3

    if not user:
        return "User not found", 404

    tokens = [t.token for t in user.fcm_tokens.all()]
    if not tokens:
        return "Sent: False (no tokens)"

    sent_any = False
    for tok in tokens:
        ok = send_fcm_notification(tok, "FCM Test", "This is a test push from Lifeline")

        sent_any = sent_any or bool(ok)

    return f"Sent: {sent_any}"



# ==================== SMART SUGGESTION AI ROUTES ====================

@app.route("/api/suggestions", methods=["POST"])
def get_smart_suggestions():
    """
    Get AI-powered smart suggestions based on:
    - User location (lat/lng)
    - Weather conditions
    - Current time
    - Local demand patterns
    
    Request body:
    {
        "lat": float,
        "lng": float,
        "max_suggestions": int (optional, default 5)
    }
    """
    try:
        data = request.get_json() or {}
        user = current_user()

        # Get user location
        fallback_lat = float(os.getenv("DEFAULT_LAT", "23.8103"))
        fallback_lng = float(os.getenv("DEFAULT_LNG", "90.4125"))

        # Explicitly check if lat/lng are in payload
        # If coords are NOT in payload, skip user profile and go straight to defaults
        # This allows the widget to intentionally use demo data
        has_payload_coords = "lat" in data and "lng" in data and data["lat"] is not None and data["lng"] is not None
        
        if has_payload_coords:
            user_lat = data["lat"]
            user_lng = data["lng"]
            location_source = "payload"
        else:
            # No payload coords = skip user profile, use defaults (for demo/testing)
            user_lat = fallback_lat
            user_lng = fallback_lng
            location_source = "fallback_default"
        
        max_suggestions = min(int(data.get("max_suggestions", 5)), 10)
        
        # Generate suggestions
        suggestions = SmartSuggestionService.get_suggestions(
            db=db,
            Request=Request,
            user_id=getattr(user, "id", 0),
            user_lat=user_lat,
            user_lng=user_lng,
            max_suggestions=max_suggestions,
            include_explanation=True
        )
        
        return jsonify({
            "success": True,
            "suggestions": suggestions,
            "generated_at": datetime.utcnow().isoformat(),
            "location_source": location_source,
            "location_used": {"lat": user_lat, "lng": user_lng}
        }), 200
        
    except Exception as e:
        print(f"[API] Error in get_smart_suggestions: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/weather", methods=["GET"])
def get_weather():
    """
    Get current weather for user's location
    Query params:
    - lat: latitude
    - lng: longitude
    """
    try:
        user = current_user()
        fallback_lat = float(os.getenv("DEFAULT_LAT", "23.8103"))
        fallback_lng = float(os.getenv("DEFAULT_LNG", "90.4125"))

        lat = request.args.get("lat", type=float)
        lng = request.args.get("lng", type=float)

        if lat is None:
            lat = getattr(user, "lat", None) or fallback_lat
        if lng is None:
            lng = getattr(user, "lng", None) or fallback_lng
        
        weather_data = WeatherService.get_weather(lat, lng)
        conditions = WeatherService.extract_conditions(weather_data)
        
        if not conditions:
            # Graceful fallback for UI when weather fails
            demo = {
                "condition": "Unknown",
                "description": "Weather unavailable",
                "temp": None,
                "humidity": None,
                "wind_speed": None,
            }
            return jsonify({"success": False, "weather": demo}), 200
        
        return jsonify({
            "success": True,
            "weather": conditions,
            "raw_data": weather_data
        }), 200
        
    except Exception as e:
        print(f"[API] Error in get_weather: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/trending-categories", methods=["GET"])
def get_trending_categories():
    """
    Get trending help categories across the platform
    Query params:
    - hours: lookback period in hours (default 24)
    - limit: number of categories (default 5)
    """
    try:
        hours = request.args.get("hours", type=int, default=24)
        limit = request.args.get("limit", type=int, default=5)
        
        trending = SmartSuggestionService.get_trending_categories(
            db=db,
            Request=Request,
            hours=max(1, min(hours, 720)),  # Between 1 hour and 30 days
            limit=max(1, min(limit, 20))
        )
        
        return jsonify({
            "success": True,
            "trending_categories": trending,
            "period_hours": hours
        }), 200
        
    except Exception as e:
        print(f"[API] Error in get_trending_categories: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/suggestion-insights", methods=["GET"])
@login_required
def get_suggestion_insights():
    """
    Get insights about suggestions and user opportunities
    """
    try:
        user = current_user()
        
        if not user.lat or not user.lng:
            return jsonify({"error": "User location required"}), 400
        
        # Get all nearby open requests
        nearby_requests = LocationMatcher.get_nearby_requests(
            db=db,
            Request=Request,
            user_lat=user.lat,
            user_lng=user.lng,
            exclude_user_id=user.id,
            limit=50
        )
        
        # Get weather
        weather_data = WeatherService.get_weather(user.lat, user.lng)
        weather_conditions = WeatherService.extract_conditions(weather_data)
        
        # Get time period
        time_period = DemandAnalyzer.get_time_period()
        
        # Get trending categories
        trending = SmartSuggestionService.get_trending_categories(
            db=db, Request=Request, hours=24, limit=5
        )
        
        # Analyze suggestions
        insights = {
            "total_nearby_requests": len(nearby_requests),
            "weather_summary": {
                "condition": weather_conditions.get("condition"),
                "temperature": weather_conditions.get("temp"),
                "humidity": weather_conditions.get("humidity"),
            },
            "current_time_period": time_period,
            "trending_categories": trending,
            "user_score": {
                "trust_score": user.trust_score or 0,
                "kindness_score": user.kindness_score or 0,
                "badge": user.calculate_badge()[0]
            },
            "recommendations": {
                "weather_opportunities": DemandAnalyzer.get_weather_suggestions(
                    weather_conditions.get("condition", "")
                ),
                "time_opportunities": DemandAnalyzer.get_time_suggestions(time_period),
                "temperature_opportunities": DemandAnalyzer.get_temp_suggestions(
                    DemandAnalyzer.categorize_temperature(weather_conditions.get("temp"))
                )
            }
        }
        
        return jsonify({
            "success": True,
            "insights": insights
        }), 200
        
    except Exception as e:
        print(f"[API] Error in get_suggestion_insights: {e}")
        return jsonify({"error": str(e)}), 500


# ==================== END SMART SUGGESTION AI ROUTES ====================


# ------------------ REGISTER BLUEPRINTS ------------------
# Register resource pooling blueprint (must be after all models are defined)
from resources import resources_bp
app.register_blueprint(resources_bp)

# ------------------ SUGGESTIONS DASHBOARD PAGE ------------------
@app.route("/suggestions", endpoint="suggestions")
def suggestions_page():
    """Render the Smart Suggestions dashboard page."""
    try:
        return render_template("suggestions_dashboard.html")
    except Exception as e:
        print(f"[UI] Error rendering suggestions_dashboard: {e}")
        # Fallback to home if template missing
        return redirect(url_for("home"))

# ------------------ MAIN ------------------
if __name__ == "__main__":
    with app.app_context():
        db.create_all()  # create tables if not exist
        
        # Migrate: Add image_url column to resource_wanted_items if it doesn't exist
        try:
            from sqlalchemy import inspect
            inspector = inspect(db.engine)
            columns = [col['name'] for col in inspector.get_columns('resource_wanted_items')]
            if 'image_url' not in columns:
                with db.engine.connect() as conn:
                    conn.execute(db.text('ALTER TABLE resource_wanted_items ADD COLUMN image_url VARCHAR(300)'))
                    conn.commit()
                print("✓ Added image_url column to resource_wanted_items")
        except Exception as e:
            print(f"Migration note: {e}")
        try:
            inspector = inspect(db.engine)
            cols = [c['name'] for c in inspector.get_columns('user')]
            with db.engine.connect() as conn:
                if 'is_premium' not in cols:
                    conn.execute(db.text('ALTER TABLE user ADD COLUMN is_premium BOOLEAN DEFAULT 0'))
                if 'premium_expiry' not in cols:
                    conn.execute(db.text('ALTER TABLE user ADD COLUMN premium_expiry DATETIME'))
                if 'is_admin' not in cols:
                    conn.execute(db.text('ALTER TABLE user ADD COLUMN is_admin BOOLEAN DEFAULT 0'))
                conn.commit()
            print("✓ Added premium/admin columns")
            
            # --- CREATE DEFAULT ADMIN ---
            admin = User.query.filter_by(email="admin@lifeline.com").first()
            if not admin:
                admin = User(email="admin@lifeline.com", name="Super Admin", is_admin=True, is_trusted_helper=True)
                admin.set_password("admin123") # Change this!
                db.session.add(admin)
                db.session.commit()
                print("✓ Created default admin: admin@lifeline.com / admin123")
                
        except Exception as e:
            print(f"Migration error: {e}")    
    debug_mode = os.getenv("FLASK_DEBUG", "0") == "1"
    socketio.run(app, debug=debug_mode, use_reloader=False)
