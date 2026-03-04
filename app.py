# -*- coding: utf-8 -*-

# Load environment variables from .env early (local dev convenience)
try:
    from dotenv import load_dotenv  # type: ignore[import-not-found]

    load_dotenv()
except Exception:
    pass

from datetime import datetime, timedelta
import base64
import json
import os
import math
import time
import random
import string
import requests

from reputation_service import analyze_review_quality, calculate_reputation_points
from smart_suggestion_service import (
    SmartSuggestionService, WeatherService, LocationMatcher, DemandAnalyzer
)
from shop_suggestion_service import ShopSuggestionAnalyzer
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




# ------------------ FCM INIT ------------------
# Initialize Firebase Admin SDK ONCE
try:
    if not firebase_admin._apps:
        service_account_json = (os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON") or "").strip()
        service_account_json_b64 = (os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON_BASE64") or "").strip()
        service_account_path = (os.getenv("FIREBASE_SERVICE_ACCOUNT_PATH") or "").strip()
        google_app_creds_path = (os.getenv("GOOGLE_APPLICATION_CREDENTIALS") or "").strip()

        cred = None
        if service_account_json:
            cred = credentials.Certificate(json.loads(service_account_json))
        elif service_account_json_b64:
            decoded = base64.b64decode(service_account_json_b64.encode("utf-8")).decode("utf-8")
            cred = credentials.Certificate(json.loads(decoded))
        else:
            for path in (service_account_path, google_app_creds_path, "firebase-service-account.json"):
                if path and os.path.exists(path):
                    cred = credentials.Certificate(path)
                    break

        if cred is not None:
            firebase_admin.initialize_app(cred)
            print("[FCM] Firebase Admin initialized.")
        else:
            # No credentials configured: keep app running; push notifications disabled.
            print(
                "[FCM] Firebase Admin not configured; push notifications disabled. "
                "Set FIREBASE_SERVICE_ACCOUNT_JSON (or FIREBASE_SERVICE_ACCOUNT_JSON_BASE64), "
                "or provide a credentials file via FIREBASE_SERVICE_ACCOUNT_PATH/GOOGLE_APPLICATION_CREDENTIALS."
            )
    else:
        print("[FCM] Firebase Admin already initialized.")
except Exception as e:
    print(f"[FCM] Firebase Admin init failed; push notifications disabled. Error: {e}")


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

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

def _normalize_database_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        return ""

    # Common copy/paste format: psql 'postgresql://...'
    lower = url.lower()
    if lower.startswith("psql "):
        url = url[5:].strip()

    # Strip surrounding quotes (single or double)
    if len(url) >= 2 and url[0] in ("'", '"') and url[-1] == url[0]:
        url = url[1:-1].strip()

    # Heroku-style scheme compatibility
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]

    return url


# Prefer DATABASE_URL (Render/Neon). Fallback to local SQLite for dev.
# Also accept alternative env var names to reduce deployment friction.
_raw_db_url = (
    os.getenv("DATABASE_URL")
    or os.getenv("RENDER_DATABASE_URL")
    or os.getenv("POSTGRES_URL")
    or os.getenv("POSTGRESQL_URL")
    or ""
)
_env_db_url = _normalize_database_url(_raw_db_url)
if _env_db_url:
    app.config["SQLALCHEMY_DATABASE_URI"] = _env_db_url
    # Production-friendly defaults for Postgres.
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
        "pool_pre_ping": True,
        "pool_recycle": 280,
    }
else:
    DB_PATH = os.path.join(BASE_DIR, "lifeline.db")  # keep it in project root
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///" + DB_PATH

app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["UPLOAD_FOLDER"] = os.path.join("static", "uploads", "profile_photos")
app.config["PRESCRIPTION_FOLDER"] = os.path.join("static", "uploads", "prescriptions")
app.config["ALLOWED_IMAGE_EXTENSIONS"] = {"png", "jpg", "jpeg", "gif"}
os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
os.makedirs(app.config["PRESCRIPTION_FOLDER"], exist_ok=True)


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
app.config["MAIL_TIMEOUT"] = int(os.getenv("MAIL_TIMEOUT", "10"))

# Fix cookie issues for localhost (Chrome blocks otherwise)
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.getenv("SESSION_COOKIE_SECURE", "0") == "1"
app.config["SESSION_COOKIE_HTTPONLY"] = True

# Some embedded webviews (including VS Code Simple Browser) can be aggressive about
# clearing non-persistent session cookies on reload/navigation. Use a permanent
# session cookie with a reasonable lifetime so logins persist reliably.
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=7)

mail = Mail(app)

# Your Google Maps API key
GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY", "").strip()

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

    role = db.Column(db.String(20), default="user")

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

    impact_stories = db.relationship('ImpactStory', back_populates='user', cascade='all, delete-orphan')

    fcm_token = db.Column(db.String(512), nullable=True)


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



# ------------------ MODEL: Impact Story ------------------
class ImpactStory(db.Model):
    __tablename__ = "impact_stories"


    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    title = db.Column(db.String(255))
    body = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship("User", back_populates="impact_stories")


# ------------------ MODEL: Emotional Ping ------------------
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


# ------------------ MODEL: User Activity (Radar) ------------------
class UserActivity(db.Model):
    __tablename__ = "user_activity"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    lat = db.Column(db.Float, nullable=True)
    lng = db.Column(db.Float, nullable=True)
    activity_type = db.Column(db.String(50), nullable=False, default="ping")
    device_motion = db.Column(db.Float, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)

    user = db.relationship("User", backref="activities")

    def to_dict(self):
        return {
            "id": self.id,
            "user_id": self.user_id,
            "lat": self.lat,
            "lng": self.lng,
            "activity_type": self.activity_type,
            "device_motion": self.device_motion,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


# ------------------ MODEL: Shop/Service Provider ------------------
class Shop(db.Model):
    __tablename__ = "shops"
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    shop_type = db.Column(db.String(100), nullable=False)  # tire_shop, hospital, pharmacy, grocery, stationary, etc.
    address = db.Column(db.Text, nullable=False)
    phone = db.Column(db.String(20), nullable=True)
    lat = db.Column(db.Float, nullable=False)
    lng = db.Column(db.Float, nullable=False)
    area = db.Column(db.String(150), nullable=True)  # e.g. Mohammadpur, Badda
    landmark = db.Column(db.String(150), nullable=True)
    description = db.Column(db.Text, nullable=True)
    rating = db.Column(db.Float, default=4.0)    # 1.0 to 5.0 stars
    service_fee = db.Column(db.Float, default=100.0)  # Fee to dispatch a worker (BDT)
    labor_cost = db.Column(db.Float, default=300.0)   # Typical labor charge (BDT)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def to_dict(self, user_lat=None, user_lng=None):
        d = {
            "id": self.id,
            "name": self.name,
            "shop_type": self.shop_type,
            "address": self.address,
            "phone": self.phone,
            "lat": self.lat,
            "lng": self.lng,
            "area": self.area,
            "landmark": self.landmark,
            "description": self.description,
            "rating": self.rating or 4.0,
            "service_fee": self.service_fee or 100.0,
            "labor_cost": 0.0 if self.shop_type in ("grocery", "pharmacy", "stationary", "restaurant") else (self.labor_cost or 300.0),
        }
        if user_lat is not None and user_lng is not None:
            try:
                d["distance_km"] = round(
                    haversine_distance_km(user_lat, user_lng, self.lat, self.lng), 2
                )
            except Exception:
                d["distance_km"] = None
        return d


# ------------------ MODEL: Shop Request (user request to shop) ------------------
class ShopRequest(db.Model):
    __tablename__ = "shop_requests"
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    shop_id = db.Column(db.Integer, db.ForeignKey("shops.id"), nullable=False)
    request_id = db.Column(db.Integer, db.ForeignKey("requests.id"), nullable=True)  # Link to original help request
    
    title = db.Column(db.String(255), nullable=False)  # e.g. "Flat tire repair needed"
    description = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(20), default="sent")  # sent, accepted, completed, cancelled
    
    user_lat = db.Column(db.Float, nullable=True)
    user_lng = db.Column(db.Float, nullable=True)
    user_area = db.Column(db.String(150), nullable=True)

    # Fee breakdown
    service_fee = db.Column(db.Float, default=0.0)   # Dispatch fee
    labor_cost = db.Column(db.Float, default=0.0)    # Labor charge
    parts_cost = db.Column(db.Float, default=0.0)    # Parts / materials cost (estimated)
    lifeline_commission = db.Column(db.Float, default=0.0)  # LifeLine platform fee
    total_cost = db.Column(db.Float, default=0.0)    # Grand total
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    accepted_at = db.Column(db.DateTime, nullable=True)
    completed_at = db.Column(db.DateTime, nullable=True)
    
    user = db.relationship("User", backref="shop_requests")
    shop = db.relationship("Shop", backref="shop_requests")
    request = db.relationship("Request", backref="shop_requests")
    
    def to_dict(self):
        return {
            "id": self.id,
            "user_id": self.user_id,
            "shop_id": self.shop_id,
            "request_id": self.request_id,
            "title": self.title,
            "description": self.description,
            "status": self.status,
            "shop_name": self.shop.name if self.shop else None,
            "shop_phone": self.shop.phone if self.shop else None,
            "shop_address": self.shop.address if self.shop else None,
            "created_at": int(self.created_at.timestamp()),
            "accepted_at": int(self.accepted_at.timestamp()) if self.accepted_at else None,
            "completed_at": int(self.completed_at.timestamp()) if self.completed_at else None,
        }


# ------------------ MODEL: Psychiatry Appointment ------------------
class PsychiatryAppointment(db.Model):
    __tablename__ = "psychiatry_appointments"

    id = db.Column(db.Integer, primary_key=True)

    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    psychiatrist_id = db.Column(db.String(50), nullable=False, index=True)
    psychiatrist_name = db.Column(db.String(255), nullable=False)
    hospital_name = db.Column(db.String(255), nullable=False)
    rating = db.Column(db.Float, default=0.0)

    appointment_date = db.Column(db.String(10), nullable=False)  # YYYY-MM-DD
    appointment_time = db.Column(db.String(5), nullable=False)   # HH:MM

    notes = db.Column(db.Text, nullable=True)

    fee_bdt = db.Column(db.Integer, default=0)
    platform_fee_bdt = db.Column(db.Integer, default=0)
    total_bdt = db.Column(db.Integer, default=0)

    payment_method = db.Column(db.String(20), nullable=True)  # stripe, bkash
    payment_status = db.Column(db.String(30), default="pending_payment", index=True)
    stripe_session_id = db.Column(db.String(255), nullable=True)
    paid_at = db.Column(db.DateTime, nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    user = db.relationship("User", backref="psychiatry_appointments")


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


def build_flagged_map_for_requests(requests_list):
    """Compute AI risk flags for templates (need_help/list_requests).

    Returns a dict keyed by request id.
    """
    try:
        from behavior_verifier_service import verify_request_behavior
    except Exception:
        return {}

    if not requests_list:
        return {}

    now = datetime.utcnow()
    # Keep a generous lookback so duplicate detection doesn't silently vanish
    # when a user reposts after a few weeks.
    window_start = now - timedelta(days=120)

    user_ids = {r.user_id for r in requests_list if getattr(r, "user_id", None) is not None}
    if not user_ids:
        return {}

    recent_by_user = {uid: [] for uid in user_ids}
    recent_rows = []
    try:
        # Fast path: filter by created_at when the column/typing supports it.
        recent_rows = (
            Request.query.filter(
                Request.user_id.in_(list(user_ids)),
                Request.created_at.isnot(None),
                Request.created_at >= window_start,
            )
            .order_by(Request.created_at.desc())
            .limit(500)
            .all()
        )
    except Exception:
        # Fallback: if created_at filtering fails for any reason (schema drift,
        # nulls, sqlite typing quirks), still fetch a bounded recent sample.
        try:
            recent_rows = (
                Request.query.filter(Request.user_id.in_(list(user_ids)))
                .order_by(Request.id.desc())
                .limit(500)
                .all()
            )
        except Exception:
            recent_rows = []

    for rr in recent_rows:
        try:
            recent_by_user.setdefault(rr.user_id, []).append(rr)
        except Exception:
            continue

    flagged_map = {}
    for r in requests_list:
        try:
            # Do not compute or show AI flags for SOS posts
            if (getattr(r, "category", "") or "").lower() == "sos":
                continue

            u = getattr(r, "user", None)
            if u is None:
                u = User.query.get(r.user_id)
            if u is None:
                continue

            recent_same_user = [x for x in recent_by_user.get(r.user_id, []) if x.id != r.id][:25]
            res = verify_request_behavior(
                user=u,
                title=getattr(r, "title", "") or "",
                description=getattr(r, "description", "") or "",
                category=getattr(r, "category", "") or "",
                contact_info=getattr(r, "contact_info", "") or "",
                recent_same_user_requests=recent_same_user,
            )
            if not res or not res.get("is_flagged"):
                continue

            reasons_list = res.get("reasons") or []
            reasons_text = "; ".join([str(x) for x in reasons_list if x])
            flagged_map[int(r.id)] = {
                "risk_score": int(res.get("risk_score") or 0),
                "reasons": reasons_text,
                "matched_request_id": res.get("matched_request_id"),
            }
        except Exception:
            continue

    return flagged_map

        
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

    # Offers were originally modeled as (request_id, helper_id). Some later code
    # also writes/reads user_id/title/body. To be resilient to schema drift in
    # local SQLite DBs, we model both and set both when creating new offers.
    id = db.Column(db.Integer, primary_key=True)
    request_id = db.Column(db.Integer, db.ForeignKey("requests.id"), nullable=False)

    # Canonical helper reference (matches Alembic initial schema)
    helper_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)

    # Legacy/compat column (some environments have this column instead of using helper_id)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)

    status = db.Column(db.String(20), default="pending")
    title = db.Column(db.String(255))
    body = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Relationships
    helper = db.relationship("User", foreign_keys=[helper_id], backref="sent_offers")
    request = db.relationship(
        "Request", backref=db.backref("offers", cascade="all, delete-orphan")
    )


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

# ------------------ IMPACT ANALYTICS ------------------
def calculate_impact(user_id):
    completed = Request.query.filter_by(
        helper_id=user_id,
        status="completed"
    ).all()

    # Prefer verified review duration hours if present.
    hours = (
        db.session.query(func.coalesce(func.sum(Review.duration_hours), 0.0))
        .filter(Review.helper_id == user_id)
        .scalar()
    ) or 0.0

    items = Resource.query.filter_by(user_id=user_id).count()

    rides = [r for r in completed if r.category == "ride"]
    carbon = len(rides) * 2.5

    return {
        "helped": len(completed),
        "hours": round(hours, 1),
        "items": items,
        "carbon": round(carbon, 1)
    }

# ------------------ EVENT MODEL ------------------
class Event(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    creator_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)

    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    event_type = db.Column(db.String(50))
    date = db.Column(db.DateTime, nullable=False)

    lat = db.Column(db.Float, nullable=False)
    lng = db.Column(db.Float, nullable=False)
    area = db.Column(db.String(150))

    completed = db.Column(db.Boolean, default=False)
    completed_at = db.Column(db.DateTime, nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    

class EventInterest(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.Integer, db.ForeignKey('event.id'))
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


# ------------------ DONATION MODEL ------------------
class Donation(db.Model):
    __tablename__ = "donations"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)  # null = anonymous
    amount = db.Column(db.Float, nullable=False)
    donor_name = db.Column(db.String(150), default="Anonymous")
    message = db.Column(db.Text, default="")
    payment_method = db.Column(db.String(50), default="bkash")  # bkash, card
    trx_id = db.Column(db.String(100), default="")
    donation_type = db.Column(db.String(50), default="general")  # general, event
    event_id = db.Column(db.Integer, db.ForeignKey("event.id"), nullable=True)
    status = db.Column(db.String(20), default="completed")  # completed, pending
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship("User", backref="donations")
    event = db.relationship("Event", backref="donations")


# ------------------ CATERING SERVICE MODEL ------------------
class CateringService(db.Model):
    __tablename__ = "catering_services"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, default="")
    cuisine_type = db.Column(db.String(100), default="Mixed")
    price_per_head = db.Column(db.Float, default=0)
    min_order = db.Column(db.Integer, default=20)
    phone = db.Column(db.String(50), default="")
    rating = db.Column(db.Float, default=4.0)
    total_reviews = db.Column(db.Integer, default=0)
    lat = db.Column(db.Float, nullable=True)
    lng = db.Column(db.Float, nullable=True)
    area = db.Column(db.String(150), default="")
    image_url = db.Column(db.String(500), default="")
    is_active = db.Column(db.Boolean, default=True)


# ------------------ EVENT FUND MODEL ------------------
class EventFund(db.Model):
    __tablename__ = "event_funds"
    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.Integer, db.ForeignKey("event.id"), nullable=False)
    fund_goal = db.Column(db.Float, default=0)  # target amount in BDT
    fund_raised = db.Column(db.Float, default=0)  # current amount raised
    description = db.Column(db.Text, default="")
    bkash_number = db.Column(db.String(20), default="")  # creator's bKash/Nagad number for receiving funds
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    event = db.relationship("Event", backref=db.backref("fund", uselist=False))


# ------------------ EVENT SPONSOR MODEL ------------------
class EventSponsor(db.Model):
    __tablename__ = "event_sponsors"
    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.Integer, db.ForeignKey("event.id"), nullable=False)
    sponsor_name = db.Column(db.String(200), nullable=False)
    sponsor_type = db.Column(db.String(50), default="gold")  # platinum, gold, silver, bronze
    contribution_bdt = db.Column(db.Float, default=0)
    logo_url = db.Column(db.String(500), default="")
    added_by_admin = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    event = db.relationship("Event", backref="sponsors")


# ------------------ EVENT CATERING SELECTION ------------------
class EventCatering(db.Model):
    __tablename__ = "event_caterings"
    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.Integer, db.ForeignKey("event.id"), nullable=False)
    catering_id = db.Column(db.Integer, db.ForeignKey("catering_services.id"), nullable=False)
    guests_count = db.Column(db.Integer, default=50)
    special_requests = db.Column(db.Text, default="")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    event = db.relationship("Event", backref="catering_selections")
    catering = db.relationship("CateringService")


# ------------------ IMPACT MODEL ------------------
class ImpactLog(db.Model):
    __tablename__ = "impact_log"

    id = db.Column(db.Integer, primary_key=True)

    helper_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    event_id = db.Column(db.Integer, db.ForeignKey("event.id"), nullable=True)  # ✅ REQUIRED

    hours = db.Column(db.Float, default=0)
    items = db.Column(db.Integer, default=0)
    carbon = db.Column(db.Float, default=0)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    helper = db.relationship("User", backref="impact_logs")


# ------------------ NOTIFICATION MODEL ------------------
class Notification(db.Model):
    __tablename__ = "notifications"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    type = db.Column(db.String(50), nullable=False)  # sos, emotional_ping, chat, system, etc.
    message = db.Column(db.Text, nullable=False)
    link = db.Column(db.String(255), nullable=True)  # URL to redirect when clicked
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship("User", backref="notifications")

    def to_dict(self):
        created_at = self.created_at
        try:
            created_at_str = created_at.strftime("%Y-%m-%d %H:%M") if created_at else ""
        except Exception:
            created_at_str = str(created_at) if created_at else ""

        return {
            "id": self.id,
            "type": self.type,
            "message": self.message,
            "link": self.link,
            "is_read": bool(self.is_read),
            "created_at": created_at_str,
        }


# ------------------ VOUCHER / COUPON MODEL ------------------
class Voucher(db.Model):
    __tablename__ = "vouchers"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    store_name = db.Column(db.String(100), nullable=False)          # Daraz, Rokomari, etc.
    code = db.Column(db.String(6), unique=True, nullable=False)     # unique 6-char alphanumeric
    discount_percent = db.Column(db.Integer, nullable=False)        # e.g. 5, 10, 15, 20, 25
    points_spent = db.Column(db.Integer, nullable=False, default=0) # kindness points redeemed
    design_variant = db.Column(db.Integer, default=1)               # 1-6 for different designs
    is_used = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship("User", backref="vouchers")

    def to_dict(self):
        return {
            "id": self.id,
            "store_name": self.store_name,
            "code": self.code,
            "discount_percent": self.discount_percent,
            "points_spent": self.points_spent,
            "design_variant": self.design_variant,
            "is_used": self.is_used,
            "created_at": self.created_at.strftime("%b %d, %Y") if self.created_at else "",
        }


# Voucher tiers: (min_kindness_points,  discount_percent,  points_cost)
VOUCHER_TIERS = [
    (20,   5,  20),
    (50,  10,  50),
    (80,  15,  80),
    (120, 20, 120),
    (200, 25, 200),
]

VOUCHER_STORES = ["Daraz", "Rokomari", "Chaldal", "Othoba", "Pickaboo", "Evaly"]


def _generate_voucher_code():
    """Generate a unique 6-character alphanumeric voucher code."""
    while True:
        code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
        if not Voucher.query.filter_by(code=code).first():
            return code


# ------------------- EVENT NOTIFICATION HELPERS -------------------
def notify_interested_users(event, message):
    interests = EventInterest.query.filter_by(event_id=event.id).all()
    for i in interests:
        user = User.query.get(i.user_id)
        if user:
            print(f"[EVENT] Notify interested user {user.email}: {message}")
            # later you can replace print with FCM / email


def auto_add_event_impact(event):
    # Do not auto-add synthetic impact entries. Impact is recorded
    # only when real actions are verified elsewhere in the app.
    return


# --------------- CHAT HELPERS ----------------
def get_or_create_conversation(user1_id, user2_id):
    a, b = sorted([int(user1_id), int(user2_id)])
    conv = Conversation.query.filter_by(user_a=a, user_b=b).first()
    if conv:
        return conv
    conv = Conversation(user_a=a, user_b=b)
    db.session.add(conv)
    db.session.commit()
    return conv


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


def _compute_unread_chat_count(user_id: int) -> int:
    try:
        return int(
            ChatMessage.query.join(Conversation, ChatMessage.conversation_id == Conversation.id)
            .filter(ChatMessage.read == False)
            .filter(ChatMessage.sender_id != int(user_id))
            .filter((Conversation.user_a == int(user_id)) | (Conversation.user_b == int(user_id)))
            .count()
        )
    except Exception:
        return 0


def _emit_counts_update(user_id: int):
    """Push authoritative unread counts to the user's personal socket room."""
    try:
        u = User.query.get(int(user_id))
        notif_count = get_notification_count(u) if u else 0
        chat_unread = _compute_unread_chat_count(int(user_id))
        socketio.emit(
            "counts_update",
            {
                "notification_count": int(notif_count or 0),
                "unread_chat_count": int(chat_unread or 0),
            },
            room=f"user_{int(user_id)}",
        )
    except Exception:
        pass


# ------------------ AUTH HELPERS ------------------
def login_user(user: User):
    # Persist session across reloads (important for webviews)
    session.permanent = True
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


def _is_admin_user(user) -> bool:
    try:
        if not user:
            return False
        return bool(getattr(user, "is_admin", False)) or getattr(user, "email", "") == "admin@lifeline.com"
    except Exception:
        return False


def admin_required(view_func):
    from functools import wraps

    @wraps(view_func)
    def wrapper(*args, **kwargs):
        user = current_user()
        if not _is_admin_user(user):
            flash("Access Denied: Admins Only.", "error")
            return redirect(url_for("home"))
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


def push_notification(user_id, type, message, link=None):
    """Create a notification in the database for the bell icon."""
    try:
        notification = Notification(
            user_id=user_id,
            type=type,
            message=message,
            link=link
        )
        db.session.add(notification)
        db.session.commit()
        return notification
    except Exception as e:
        print(f"[NOTIFICATION] Error creating notification: {e}")


@app.route("/api/notifications", methods=["GET"])
@login_required
def api_notifications():
    """Return unread notifications for the current user (used by the bell UI)."""
    user = current_user()
    if not user:
        return jsonify([])

    rows = (
        Notification.query.filter_by(user_id=user.id, is_read=False)
        .order_by(Notification.created_at.desc())
        .limit(20)
        .all()
    )
    return jsonify([n.to_dict() for n in rows])


@app.route("/api/notifications/read", methods=["POST"])
@login_required
def api_notifications_mark_all_read():
    """Mark all notifications as read for the current user."""
    user = current_user()
    if not user:
        return jsonify({"ok": False}), 401

    try:
        Notification.query.filter_by(user_id=user.id, is_read=False).update({"is_read": True})
        db.session.commit()
    except Exception:
        db.session.rollback()
        return jsonify({"ok": False}), 500
    return jsonify({"ok": True})


def get_notification_count(user: User) -> int:
    if not user:
        return 0
    return Notification.query.filter_by(user_id=user.id, is_read=False).count()


@app.route("/api/notification-count", methods=["GET"])
@login_required
def api_notification_count():
    user = current_user()
    if not user:
        return jsonify({"count": 0})
    return jsonify({"count": get_notification_count(user)})
    


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

    unread_chat_count = _compute_unread_chat_count(user.id) if user else 0

    return dict(
        current_user=user,
        translation_enabled=translation_enabled,
        unread_chat_count=int(unread_chat_count or 0),
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

# ------------------ EVENT NOTIFICATION SERVICE ------------------
def notify_nearby_users(event):
    users = User.query.filter(User.lat.isnot(None), User.lng.isnot(None)).all()
    notified_count = 0

    for u in users:
        # Skip the event creator
        if u.id == event.creator_id:
            continue

        try:
            url = "https://maps.googleapis.com/maps/api/distancematrix/json"
            params = {
                "origins": f"{u.lat},{u.lng}",
                "destinations": f"{event.lat},{event.lng}",
                "key": GOOGLE_MAPS_API_KEY,
                "units": "metric"  # Ensure we get meters
            }

            res = requests.get(url, params=params, timeout=10).json()

            # Check if API response is valid
            if res.get("status") != "OK":
                print(f"[DISTANCE API] Error for user {u.email}: {res.get('status')}")
                continue

            # Check if we have valid elements
            if not res.get("rows") or not res["rows"][0].get("elements"):
                print(f"[DISTANCE API] No elements in response for user {u.email}")
                continue

            element = res["rows"][0]["elements"][0]
            if element.get("status") != "OK":
                print(f"[DISTANCE API] Element status not OK for user {u.email}: {element.get('status')}")
                continue

            distance_m = element["distance"]["value"]
            print(f"[DISTANCE API] User {u.email} is {distance_m}m from event")

            if distance_m <= 3000:  # 3 km = 3000 meters
                send_event_notification(u, event)
                notified_count += 1
                print(f"[EVENT] Notified user {u.email} about event '{event.title}'")

        except requests.exceptions.RequestException as e:
            print(f"[DISTANCE API] Request error for user {u.email}: {e}")
        except (KeyError, IndexError, ValueError) as e:
            print(f"[DISTANCE API] Parsing error for user {u.email}: {e}")
        except Exception as e:
            print(f"[DISTANCE API] Unexpected error for user {u.email}: {e}")

    print(f"[EVENT] Notified {notified_count} users about event '{event.title}'")

def send_event_notification(user, event):
    # Send FCM push notification for nearby event
    title = f"New Community Event Near You: {event.title}"
    body = f"{event.description[:100]}... Date: {event.date.strftime('%B %d, %Y')}"
    data = {
        "event_id": str(event.id),
        "type": "event_nearby"
    }
    send_push_to_user(user, title, body, data)


# ------------------ COMPLETE REQUEST ------------------
def update_user_scores(user):
    """Recompute and store user's trust_score and kindness_score based on completed requests."""
    if not user:
        return

    # IMPORTANT: use verified Review.duration_hours.
    # Using (completed_at - created_at) is unreliable because a request may stay open for days.
    helped_count = (
        db.session.query(func.count(func.distinct(Review.request_id)))
        .filter(Review.helper_id == user.id)
        .scalar()
    ) or 0

    total_hours = (
        db.session.query(func.coalesce(func.sum(Review.duration_hours), 0.0))
        .filter(Review.helper_id == user.id)
        .scalar()
    ) or 0.0
    total_hours = round(float(total_hours), 2)

    # Simple scoring (tweak if Module-1 has other rules)
    trust_score = min(100, helped_count * 5)           # example: +5 trust per complete
    kindness_score = helped_count * 10 + int(total_hours * 2) + trust_score

    # Save to DB
    user.trust_score = int(trust_score)
    user.kindness_score = int(kindness_score)

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
    # Total donations collected (general only, excludes event fund contributions)
    try:
        total_donations = db.session.query(func.coalesce(func.sum(Donation.amount), 0)).filter(Donation.status == "completed", Donation.donation_type != "event").scalar() or 0
        donation_count = Donation.query.filter(Donation.status == "completed", Donation.donation_type != "event").count()
        recent_donations = Donation.query.filter(Donation.status == "completed", Donation.donation_type != "event").order_by(Donation.created_at.desc()).limit(5).all()
    except Exception:
        total_donations = 0
        donation_count = 0
        recent_donations = []
    return render_template("home.html", items=items, total_donations=total_donations, donation_count=donation_count, recent_donations=recent_donations)


@app.route("/debug/session")
def debug_session():
    return jsonify(dict(session))


@app.route("/map")
def map_page():
    # Map is normally for logged-in users, but we also allow an emergency guest
    # to view/resolve the SOS they just created (tracked in session).
    viewer = current_user()
    focus_request_id = request.args.get("focus_request_id", type=int)
    is_sos_caller = request.args.get("sos_caller") in ("1", "true", "True")
    guest_sos_request_id = session.get("guest_sos_request_id")

    allow_guest_sos_view = (
        (not viewer)
        and is_sos_caller
        and focus_request_id
        and guest_sos_request_id
        and int(guest_sos_request_id) == int(focus_request_id)
    )

    if not viewer and not allow_guest_sos_view:
        return redirect(url_for("login", next=request.full_path))

    return render_template(
        "map.html",
        google_maps_key=GOOGLE_MAPS_API_KEY,
        user=viewer,
        is_sos_caller=bool(is_sos_caller),
        guest_sos_request_id=guest_sos_request_id,
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
    google_login_enabled = bool(getattr(firebase_admin, "_apps", None))
    return render_template(
        "login.html",
        google_login_enabled=google_login_enabled,
    )


@app.route("/logout")
def logout():
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for("home"))


# Quick SOS trigger used by the header form (templates/base.html -> url_for('trigger_sos'))
#@app.route("/trigger_sos", methods=["POST"])
#def trigger_sos():
    """Create a simple emergency request and notify nearby trusted helpers.
    This is a lightweight fallback so templates that call `trigger_sos` won't fail.
    """
    #user = current_user()
    #if user is None:
        #user = get_emergency_user()
        #login_user(user)

    #title = "Emergency Help Requested"
    #description = "SOS triggered from UI"
    #expires_at = datetime.utcnow() + timedelta(minutes=60)

    #req = Request(
        #user_id=user.id,
        #title=title,
        #category="emergency",
        #description=description,
        #is_offer=False,
        #expires_at=expires_at,)
    #db.session.add(req)
    #db.session.commit()

    # Notify a small set of trusted helpers (best-effort)
    #try:
        #helpers = User.query.filter(User.is_trusted_helper == True, User.id != user.id).limit(10).all()
        #for h in helpers:
            #try:
                #send_push_to_user(h, "Emergency nearby", f"{user.name} needs help: {title}", data={"request_id": req.id})
            #except Exception:
                #pass
    #except Exception:
        #db.session.rollback()

    #flash("SOS triggered — local helpers notified.", "success")
    #return redirect(url_for("home"))

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
def emotional_ping_placeholder():
    try:
        from psychiatry_data import list_featured_psychiatrists

        psychiatrists = list_featured_psychiatrists()
    except Exception:
        psychiatrists = []

    return render_template("emotional_ping.html", psychiatrists=psychiatrists)

from flask_jwt_extended import jwt_required, get_jwt_identity
from datetime import datetime

@app.route("/api/emotional_ping", methods=["POST"])
@login_required
def api_emotional_ping():
    ping_committed = False
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
        ping = EmotionalPing(user_id=user.id, mood=mood, message=message)
        db.session.add(ping)
        db.session.commit()
        ping_committed = True

        # 2️⃣ Notify trusted helpers (best-effort). If notifications fail, the ping
        # is still valid and should not show "Failed to send" on the client.
        notified = 0
        try:
            helpers = get_trusted_helpers_for_ping(user.id)
        except Exception as e:
            helpers = []
            print("EMOTIONAL_PING helpers lookup error:", e)

        for helper in helpers:
            try:
                push_notification(
                    user_id=helper.id,
                    type="emotional_ping",
                    message=f"{user.name} is feeling {mood}",
                    link=url_for("emotional_ping_placeholder"),
                )
                notified += 1
            except Exception as e:
                print("EMOTIONAL_PING notification error:", e)

            # 3️⃣ Optional FCM push (best-effort)
            try:
                if helper.fcm_tokens.count() > 0:
                    send_fcm_to_user(
                        helper,
                        title="New Emotional Ping 💙",
                        body=f"{user.name} is feeling {mood}",
                        data={
                            "type": "EMOTIONAL_PING",
                            "sender_id": str(user.id),
                            "ping_id": str(ping.id),
                        },
                    )
            except Exception as e:
                print("EMOTIONAL_PING FCM error:", e)

        return jsonify({"message": "Ping sent", "ping_id": ping.id, "notified": notified}), 200

    except Exception as e:
        if not ping_committed:
            db.session.rollback()
        print("EMOTIONAL_PING POST ERROR:", e)
        return jsonify({"error": "Failed to send ping"}), 500


@app.route("/psychiatrists")
@login_required
def psychiatrists_list():
    from psychiatry_data import list_featured_psychiatrists

    psychiatrists = list_featured_psychiatrists()
    return render_template("psychiatrists.html", psychiatrists=psychiatrists)


@app.route("/psychiatrists/<string:psychiatrist_id>/book", methods=["GET", "POST"])
@login_required
def book_psychiatrist(psychiatrist_id):
    from psychiatry_data import get_psychiatrist_by_id

    # Slot rules: user must select from predefined dates/times.
    # Dates are NOT all available; they come from the psychiatrist's schedule,
    # but are always limited to future-only within the next 14 days.
    today = datetime.utcnow().date()

    p = get_psychiatrist_by_id(psychiatrist_id)
    if not p:
        flash("Psychiatrist not found.", "error")
        return redirect(url_for("psychiatrists_list"))

    available_weekdays = tuple(getattr(p, "available_weekdays", ()) or ())
    allowed_dates = []
    for i in range(1, 15):
        d = today + timedelta(days=i)
        if available_weekdays and d.weekday() not in available_weekdays:
            continue
        allowed_dates.append(d.isoformat())

    allowed_date_options = []
    for iso in allowed_dates:
        try:
            day_name = datetime.strptime(iso, "%Y-%m-%d").strftime("%a")
        except Exception:
            day_name = ""
        label = f"{day_name} • {iso}" if day_name else iso
        allowed_date_options.append({"value": iso, "day": day_name, "label": label})

    allowed_times = list(getattr(p, "available_times", None) or [])
    # Fallback in case availability wasn't configured
    if not allowed_times:
        allowed_times = ["10:00", "12:00", "15:00", "17:00"]

    if request.method == "POST":
        appt_date = (request.form.get("appointment_date") or "").strip()
        appt_time = (request.form.get("appointment_time") or "").strip()
        notes = (request.form.get("notes") or "").strip() or None

        if not appt_date or not appt_time:
            flash("Please choose a date and time.", "error")
            return render_template(
                "book_psychiatrist.html",
                psychiatrist=p,
                allowed_dates=allowed_dates,
                allowed_date_options=allowed_date_options,
                allowed_times=allowed_times,
            )

        if appt_date not in allowed_dates or appt_time not in allowed_times:
            flash("Please select a valid slot within the next 2 weeks.", "error")
            return render_template(
                "book_psychiatrist.html",
                psychiatrist=p,
                allowed_dates=allowed_dates,
                allowed_date_options=allowed_date_options,
                allowed_times=allowed_times,
            )

        # Simple fee model (BDT): doctor fee + small platform fee
        platform_fee = max(50, int(round(p.fee_bdt * 0.05)))
        total = int(p.fee_bdt) + int(platform_fee)

        u = current_user()
        appt = PsychiatryAppointment(
            user_id=u.id,
            psychiatrist_id=p.id,
            psychiatrist_name=p.name,
            hospital_name=p.hospital,
            rating=float(p.rating or 0.0),
            appointment_date=appt_date,
            appointment_time=appt_time,
            notes=notes,
            fee_bdt=int(p.fee_bdt),
            platform_fee_bdt=int(platform_fee),
            total_bdt=int(total),
            payment_status="pending_payment",
        )
        db.session.add(appt)
        db.session.commit()

        return redirect(url_for("pay_psychiatry_appointment", appointment_id=appt.id))

    return render_template(
        "book_psychiatrist.html",
        psychiatrist=p,
        allowed_dates=allowed_dates,
        allowed_date_options=allowed_date_options,
        allowed_times=allowed_times,
    )


def _stripe_is_configured() -> bool:
    return bool(os.getenv("STRIPE_SECRET_KEY"))


@app.route("/appointments/<int:appointment_id>/pay", methods=["GET", "POST"])
@login_required
def pay_psychiatry_appointment(appointment_id):
    u = current_user()
    appt = PsychiatryAppointment.query.get_or_404(appointment_id)
    if appt.user_id != u.id:
        flash("Not authorized.", "error")
        return redirect(url_for("home"))

    stripe_ready = _stripe_is_configured()

    if request.method == "POST":
        method = (request.form.get("payment_method") or "").strip().lower()
        if method not in ("stripe", "bkash"):
            flash("Choose a payment method.", "error")
            return render_template(
                "appointment_pay.html", appointment=appt, stripe_ready=stripe_ready
            )

        if method == "stripe":
            if not stripe_ready:
                flash("Stripe payment is not configured on this server.", "error")
                return render_template(
                    "appointment_pay.html", appointment=appt, stripe_ready=stripe_ready
                )
            return redirect(url_for("pay_psychiatry_appointment_stripe", appointment_id=appt.id))

        # bKash manual payment submit
        bkash_number = (request.form.get("bkash_number") or "").strip()
        trx_id = (request.form.get("trx_id") or "").strip().upper()
        if not bkash_number or not trx_id:
            flash("Please provide your bKash number and TrxID.", "error")
            return render_template(
                "appointment_pay.html", appointment=appt, stripe_ready=stripe_ready
            )

        appt.payment_method = "bkash"
        appt.payment_status = "submitted"
        appt.notes = (appt.notes or "") + f"\n\n[bKash] Number: {bkash_number} | TrxID: {trx_id}"
        db.session.commit()
        flash("Payment submitted. We’ll confirm and lock the appointment.", "success")
        return redirect(url_for("pay_psychiatry_appointment", appointment_id=appt.id))

    return render_template(
        "appointment_pay.html", appointment=appt, stripe_ready=stripe_ready
    )


@app.route("/appointments/<int:appointment_id>/pay/stripe")
@login_required
def pay_psychiatry_appointment_stripe(appointment_id):
    u = current_user()
    appt = PsychiatryAppointment.query.get_or_404(appointment_id)
    if appt.user_id != u.id:
        flash("Not authorized.", "error")
        return redirect(url_for("home"))

    if not _stripe_is_configured():
        flash("Stripe payment is not configured on this server.", "error")
        return redirect(url_for("pay_psychiatry_appointment", appointment_id=appt.id))

    try:
        import stripe

        stripe.api_key = os.getenv("STRIPE_SECRET_KEY")

        base_url = request.url_root.rstrip("/")
        success_url = (
            base_url
            + url_for("pay_psychiatry_appointment_stripe_success", appointment_id=appt.id)
            + "?session_id={CHECKOUT_SESSION_ID}"
        )
        cancel_url = base_url + url_for("pay_psychiatry_appointment", appointment_id=appt.id)

        session_obj = stripe.checkout.Session.create(
            mode="payment",
            payment_method_types=["card"],
            line_items=[
                {
                    "price_data": {
                        "currency": os.getenv("STRIPE_CURRENCY", "bdt"),
                        "product_data": {
                            "name": f"Psychiatry Appointment — {appt.psychiatrist_name}",
                            "description": f"{appt.hospital_name} • {appt.appointment_date} {appt.appointment_time}",
                        },
                        "unit_amount": int(appt.total_bdt) * 100,
                    },
                    "quantity": 1,
                }
            ],
            customer_email=(u.email if getattr(u, "email", None) else None),
            success_url=success_url,
            cancel_url=cancel_url,
            metadata={"appointment_id": str(appt.id)},
        )

        appt.payment_method = "stripe"
        appt.stripe_session_id = session_obj.id
        db.session.commit()

        return redirect(session_obj.url)
    except Exception as e:
        flash(f"Stripe error: {e}", "error")
        return redirect(url_for("pay_psychiatry_appointment", appointment_id=appt.id))


@app.route("/appointments/<int:appointment_id>/pay/stripe/success")
@login_required
def pay_psychiatry_appointment_stripe_success(appointment_id):
    u = current_user()
    appt = PsychiatryAppointment.query.get_or_404(appointment_id)
    if appt.user_id != u.id:
        flash("Not authorized.", "error")
        return redirect(url_for("home"))

    session_id = (request.args.get("session_id") or "").strip()
    if not session_id:
        flash("Missing Stripe session.", "error")
        return redirect(url_for("pay_psychiatry_appointment", appointment_id=appt.id))

    if not _stripe_is_configured():
        flash("Stripe payment is not configured on this server.", "error")
        return redirect(url_for("pay_psychiatry_appointment", appointment_id=appt.id))

    try:
        import stripe

        stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
        sess = stripe.checkout.Session.retrieve(session_id)

        paid_ok = (
            getattr(sess, "payment_status", "") == "paid"
            or getattr(sess, "status", "") == "complete"
        )

        if paid_ok:
            appt.payment_status = "paid"
            appt.paid_at = datetime.utcnow()
            appt.stripe_session_id = session_id
            db.session.commit()
            flash("Payment received. Appointment booked.", "success")
        else:
            flash("Payment not completed yet.", "error")
    except Exception as e:
        flash(f"Stripe verification error: {e}", "error")

    return redirect(url_for("pay_psychiatry_appointment", appointment_id=appt.id))
    
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

    wants_json = (
        request.headers.get("X-Requested-With") == "XMLHttpRequest"
        or request.accept_mimetypes.best == "application/json"
    )

    if not user:
        msg = "No account with this email."
        if wants_json:
            return jsonify({"ok": False, "error": msg}), 404
        flash(msg, "error")
        return redirect(url_for("login", mode="otp"))

    if not (EMAIL_ADDRESS and EMAIL_PASSWORD):
        msg = "OTP email is not configured on the server. Please use password login for now."
        if wants_json:
            return jsonify({"ok": False, "error": msg}), 503
        flash(msg, "error")
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
        msg = "Too many OTP requests. Please try again later."
        if wants_json:
            return jsonify({"ok": False, "error": msg}), 429
        flash(msg, "error")
        otp_store[email] = log
        return redirect(url_for("login", mode="otp"))

    # Cooldown: at least OTP_COOLDOWN_SECONDS between sends
    if log["request_times"]:
        last_request = max(log["request_times"])
        elapsed = (now - last_request).total_seconds()
        if elapsed < OTP_COOLDOWN_SECONDS:
            remaining = int(OTP_COOLDOWN_SECONDS - elapsed)
            msg = f"Please wait {remaining}s before requesting a new OTP."
            if wants_json:
                return jsonify({"ok": False, "error": msg, "retry_after": remaining}), 429
            flash(msg, "error")
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
    try:
        mail.send(msg)
    except Exception as e:
        print("[OTP] Email send failed:", e)
        try:
            user.clear_otp()
            db.session.commit()
        except Exception:
            db.session.rollback()

        err = "Could not send OTP email. Please try again later."
        if wants_json:
            return jsonify({"ok": False, "error": err}), 502
        flash(err, "error")
        return redirect(url_for("login", mode="otp"))

    ok_msg = "OTP sent. Check your inbox and spam/junk folder."
    if wants_json:
        return jsonify({"ok": True, "message": ok_msg, "cooldown": OTP_COOLDOWN_SECONDS}), 200

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

    def _unverified_claims(token: str) -> dict:
        try:
            import base64
            import json

            parts = (token or "").split(".")
            if len(parts) != 3:
                return {}

            payload_b64 = parts[1]
            # base64url padding
            payload_b64 += "=" * (-len(payload_b64) % 4)
            payload = base64.urlsafe_b64decode(payload_b64.encode("utf-8"))
            return json.loads(payload.decode("utf-8")) if payload else {}
        except Exception:
            return {}

    def _friendly_auth_error(err: Exception, token: str) -> str:
        msg = str(err) or err.__class__.__name__
        low = msg.lower()
        claims = _unverified_claims(token)

        # Helpful, common failures
        if "default firebase app" in low and "does not exist" in low:
            return (
                "Firebase Admin is not initialized on the server. Configure Firebase Admin credentials "
                "(e.g., FIREBASE_SERVICE_ACCOUNT_JSON / FIREBASE_SERVICE_ACCOUNT_JSON_BASE64)."
            )
        if "error while fetching public key certificates" in low or "fetching public key" in low or "x509" in low:
            return "Server cannot reach Google to verify the token (check internet/proxy/firewall)."
        if "expired" in low or "token used too late" in low:
            return "Token appears expired (check your system clock/time sync)."
        if "incorrect \"aud\"" in low or "audience" in low:
            aud = claims.get("aud")
            iss = claims.get("iss")
            return f"Token audience mismatch (aud={aud}, iss={iss}). Make sure Firebase project matches server credentials."

        # Minimal detail fallback (still actionable)
        iss = claims.get("iss")
        aud = claims.get("aud")
        if iss or aud:
            return f"Token verification failed (iss={iss}, aud={aud}). {msg}"
        return f"Token verification failed: {msg}"

    # Tolerate small system clock drift (common on Windows): prevents
    # 'Token used too early' errors for a second or two.
    clock_skew_seconds = int(os.getenv("FIREBASE_TOKEN_CLOCK_SKEW_SECONDS", "10"))

    try:
        decoded = firebase_auth.verify_id_token(id_token, clock_skew_seconds=clock_skew_seconds)
    except Exception as e:
        print("Firebase verify error:", e)

        # If the client ever sends a raw Google OAuth ID token (not Firebase),
        # we can optionally verify it if GOOGLE_CLIENT_ID is configured.
        claims = _unverified_claims(id_token)
        iss = (claims.get("iss") or "").strip()
        if iss in {"accounts.google.com", "https://accounts.google.com"}:
            google_client_id = os.getenv("GOOGLE_CLIENT_ID")
            if not google_client_id:
                return jsonify({
                    "error": "Google login failed: server is missing GOOGLE_CLIENT_ID for OAuth token verification."
                }), 401
            try:
                from google.oauth2 import id_token as google_id_token
                from google.auth.transport import requests as google_requests

                decoded = google_id_token.verify_oauth2_token(
                    id_token,
                    google_requests.Request(),
                    audience=google_client_id,
                )
            except Exception as e2:
                print("Google OAuth verify error:", e2)
                return jsonify({"error": f"Google login failed: {_friendly_auth_error(e2, id_token)}"}), 401
        else:
            return jsonify({"error": f"Google login failed: {_friendly_auth_error(e, id_token)}"}), 401

    email = decoded.get("email")
    uid = decoded.get("uid")
    name = decoded.get("name") or (email.split("@")[0] if email else "Google user")
    # Firebase ID tokens use 'picture'; some payloads may use other keys.
    photo_url = decoded.get("picture") or decoded.get("photo_url") or decoded.get("photo")


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
        if (not user.profile_photo or user.profile_photo == "default.png") and photo_url:
            user.profile_photo = photo_url

    db.session.commit()


    # Log into Flask session
    login_user(user)
    flash("Logged in with Google.", "success")

    # Optional: create a JWT for API usage
    access_token = create_jwt_for_user(user)
    # NOTE: Do not stash JWTs in the Flask session cookie; it can become too large
    # and some webviews/browsers will drop it, making the user appear "logged out".
    # If you need the token for debugging, log it server-side instead.

    # Frontend JS will redirect based on this
    return jsonify({"redirect_url": url_for("home")})


@app.route("/google-auth", methods=["POST"])
def google_auth():
    "Verify Google ID token from Firebase, log user in, return redirect URL."
    data = request.get_json() or {}
    id_token = data.get("id_token")

    if not id_token:
        return jsonify({"error": "Missing id_token"}), 400

    clock_skew_seconds = int(os.getenv("FIREBASE_TOKEN_CLOCK_SKEW_SECONDS", "10"))
    try:
        decoded = firebase_auth.verify_id_token(id_token, clock_skew_seconds=clock_skew_seconds)
    except Exception as e:
        print("Firebase verify_id_token error:", e)
        return jsonify({"error": "Invalid or expired Google token"}), 401

    uid = decoded["uid"]
    email = decoded.get("email")
    name = decoded.get("name") or (email.split("@")[0] if email else "Google User")
    photo_url = decoded.get("picture")
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
        db.session.add(user)
    else:
        if (not user.profile_photo or user.profile_photo == "default.png") and photo_url:
            user.profile_photo = photo_url
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
    # See note above about not storing JWTs in session cookies.

    # Where to go next
    next_url = session.pop("next_after_google", url_for("home"))
    return jsonify({"redirect_url": next_url})

# ---------- TRUSTED HELPER VERIFICATION ----------
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
        user.role = "helper"
        db.session.commit()

        # 🔄 Keep session in sync so navbar updates
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
        type="emotional_reply",
        message=f"{current_user().name} replied to your emotional ping",
        link=url_for("chat_with_user", other_user_id=current_user().id)
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

# ------------------ API: Availability Radar ------------------
@app.route("/api/activity/ping", methods=["POST"])
@login_required
def api_activity_ping():
    user = current_user()
    data = request.get_json() or {}

    try:
        lat = float(data.get("lat")) if data.get("lat") is not None else None
        lng = float(data.get("lng")) if data.get("lng") is not None else None
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "lat and lng must be numbers"}), 400

    activity_type = (data.get("activity_type") or "ping").strip() or "ping"
    device_motion_raw = data.get("device_motion")
    try:
        device_motion = float(device_motion_raw) if device_motion_raw is not None else None
    except (TypeError, ValueError):
        device_motion = None

    activity = UserActivity(
        user_id=user.id,
        lat=lat,
        lng=lng,
        activity_type=activity_type[:50],
        device_motion=device_motion,
    )

    if lat is not None and lng is not None:
        user.lat = lat
        user.lng = lng

    try:
        db.session.add(activity)
        db.session.commit()
    except Exception:
        db.session.rollback()
        return jsonify({"ok": False, "error": "Failed to record activity"}), 500

    return (
        jsonify({
            "ok": True,
            "activity_id": activity.id,
            "lat": lat,
            "lng": lng,
            "activity_type": activity.activity_type,
            "device_motion": activity.device_motion,
        }),
        201,
    )


def _resolve_position(payload, user):
    lat = payload.get("user_lat") if payload else None
    lng = payload.get("user_lng") if payload else None
    try:
        lat = float(lat if lat is not None else user.lat)
        lng = float(lng if lng is not None else user.lng)
    except (TypeError, ValueError):
        lat = None
        lng = None
    return lat, lng


@app.route("/api/radar/heatmap", methods=["POST"])
@login_required
def api_radar_heatmap():
    user = current_user()
    data = request.get_json() or {}

    user_lat, user_lng = _resolve_position(data, user)
    if user_lat is None or user_lng is None:
        return jsonify({"ok": False, "error": "user_lat and user_lng are required"}), 400

    try:
        radius_km = float(data.get("radius_km", 3.0))
    except (TypeError, ValueError):
        radius_km = 3.0
    radius_km = max(0.5, min(radius_km, 30.0))

    try:
        window_min = int(data.get("window_min", 10))
    except (TypeError, ValueError):
        window_min = 10
    window_min = max(5, min(window_min, 180))

    cutoff = datetime.utcnow() - timedelta(minutes=window_min)

    activities = UserActivity.query.filter(
        UserActivity.created_at >= cutoff,
        UserActivity.lat.isnot(None),
        UserActivity.lng.isnot(None),
    ).all()

    aggregated = {}
    for activity in activities:
        dist = haversine_distance_km(user_lat, user_lng, activity.lat, activity.lng)
        if dist > radius_km:
            continue

        entry = aggregated.get(activity.user_id)
        if not entry:
            u = User.query.get(activity.user_id)
            if not u:
                continue
            entry = {
                "user": u,
                "lat_sum": 0.0,
                "lng_sum": 0.0,
                "count": 0,
                "motion_sum": 0.0,
                "distance": dist,
            }
            aggregated[activity.user_id] = entry

        entry["lat_sum"] += activity.lat
        entry["lng_sum"] += activity.lng
        entry["count"] += 1
        entry["distance"] = min(entry["distance"], dist)
        if activity.device_motion is not None:
            entry["motion_sum"] += activity.device_motion

    heatmap_points = []
    for uid, entry in aggregated.items():
        count = entry["count"] or 1
        motion_avg = (entry["motion_sum"] / count) if entry["motion_sum"] else 0
        activity_intensity = min(100, count * 15)
        if motion_avg > 20:
            activity_intensity = min(100, activity_intensity + (motion_avg / 2))
        weight = round(min(1.0, activity_intensity / 100), 3)
        user_obj = entry["user"]

        heatmap_points.append({
            "user_id": uid,
            "name": user_obj.name if user_obj else "User",
            "lat": entry["lat_sum"] / count,
            "lng": entry["lng_sum"] / count,
            "weight": weight,
            "is_helper": bool(user_obj.is_trusted_helper) if user_obj else False,
            "activity_count": count,
            "motion_avg": round(motion_avg, 1) if motion_avg else 0,
            "distance_km": round(entry["distance"], 2),
        })

    heatmap_points.sort(key=lambda x: x["weight"], reverse=True)
    helpers_nearby = sum(1 for p in heatmap_points if p.get("is_helper"))

    return jsonify({
        "ok": True,
        "heatmap_points": heatmap_points,
        "total_active_nearby": len(heatmap_points),
        "helpers_nearby": helpers_nearby,
        "radius_km": radius_km,
        "window_min": window_min,
    })


@app.route("/api/radar/active-users", methods=["POST"])
@login_required
def api_radar_active_users():
    user = current_user()
    data = request.get_json() or {}

    user_lat, user_lng = _resolve_position(data, user)
    if user_lat is None or user_lng is None:
        return jsonify({"ok": False, "error": "user_lat and user_lng are required"}), 400

    try:
        radius_km = float(data.get("radius_km", 3.0))
    except (TypeError, ValueError):
        radius_km = 3.0
    radius_km = max(0.5, min(radius_km, 30.0))

    try:
        window_min = int(data.get("window_min", 10))
    except (TypeError, ValueError):
        window_min = 10
    window_min = max(5, min(window_min, 180))

    cutoff = datetime.utcnow() - timedelta(minutes=window_min)

    recent_activities = (
        db.session.query(
            UserActivity.user_id,
            func.max(UserActivity.created_at).label("last_activity"),
            func.avg(UserActivity.lat).label("avg_lat"),
            func.avg(UserActivity.lng).label("avg_lng"),
            func.count(UserActivity.id).label("activity_count"),
            func.avg(UserActivity.device_motion).label("avg_motion"),
        )
        .filter(UserActivity.created_at >= cutoff)
        .group_by(UserActivity.user_id)
        .all()
    )

    active_users = []
    now = datetime.utcnow()

    for record in recent_activities:
        user_id = record[0]
        last_activity = record[1]
        avg_lat = record[2]
        avg_lng = record[3]
        activity_count = record[4]
        avg_motion = record[5] or 0

        if avg_lat is None or avg_lng is None:
            continue

        dist = haversine_distance_km(user_lat, user_lng, avg_lat, avg_lng)
        if dist > radius_km:
            continue

        u = User.query.get(user_id)
        if not u:
            continue

        age_secs = (now - last_activity).total_seconds() if last_activity else 0
        recency = max(0, 100 - (age_secs / 3))
        activity_score = min(100, activity_count * 20)
        motion_score = min(100, avg_motion * 2) if avg_motion else 0
        availability_score = int((recency + activity_score + motion_score) / 3)

        active_users.append({
            "user_id": user_id,
            "name": u.name,
            "is_helper": bool(u.is_trusted_helper),
            "lat": avg_lat,
            "lng": avg_lng,
            "distance_km": round(dist, 2),
            "availability_score": availability_score,
            "activity_count": int(activity_count),
            "recency": round(recency, 1),
            "activity_score": round(activity_score, 1),
            "motion_score": round(motion_score, 1),
            "last_activity_ago_secs": int(age_secs),
        })

    active_users.sort(key=lambda x: x["availability_score"], reverse=True)
    helpers_nearby = sum(1 for u in active_users if u.get("is_helper"))

    return jsonify({
        "ok": True,
        "active_users": active_users,
        "total_active_nearby": len(active_users),
        "helpers_nearby": helpers_nearby,
        "radius_km": radius_km,
        "window_min": window_min,
    })

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

    # Handle prescription image for medicine requests
    image_url = None
    if category.lower() == "medicine":
        if "prescription_image" not in request.files or request.files["prescription_image"].filename == "":
            flash("A prescription photo is required for medicine requests.", "error")
            return redirect(url_for("new_request"))
        img = request.files["prescription_image"]
        if img and allowed_image(img.filename):
            filename = secure_filename(img.filename)
            filename = f"{int(time.time())}_{filename}"
            save_path = os.path.join(app.config["PRESCRIPTION_FOLDER"], filename)
            img.save(save_path)
            image_url = f"/static/uploads/prescriptions/{filename}"
        else:
            flash("Invalid image format. Please upload PNG, JPG, JPEG, or GIF.", "error")
            return redirect(url_for("new_request"))

    expires_at = datetime.utcnow() + timedelta(minutes=expiry_minutes)

    req = Request(
        user_id=current_user().id,
        title=title,
        category=category,
        description=description,
        is_offer=is_offer,
        image_url=image_url,
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
    can_post = True  # Guests can always post emergency requests
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

        # Handle prescription image for medicine requests
        image_url = None
        if category.lower() == "medicine":
            if "prescription_image" not in request.files or request.files["prescription_image"].filename == "":
                flash("A prescription photo is required for medicine requests.", "error")
                return redirect(url_for("need_help"))
            img = request.files["prescription_image"]
            if img and allowed_image(img.filename):
                filename = secure_filename(img.filename)
                filename = f"{int(time.time())}_{filename}"
                save_path = os.path.join(app.config["PRESCRIPTION_FOLDER"], filename)
                img.save(save_path)
                image_url = f"/static/uploads/prescriptions/{filename}"
            else:
                flash("Invalid image format. Please upload PNG, JPG, JPEG, or GIF.", "error")
                return redirect(url_for("need_help"))

        # Decide which user will own this request
        if user is None:
            user = get_emergency_user()

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
            image_url=image_url,
            lat=lat,
            lng=lng,
            expires_at=expires_at,
        )
        db.session.add(req)
        db.session.commit()


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

    flagged_map = build_flagged_map_for_requests(posts)

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
        "need_help.html",
        posts=posts,
        total_need=total_need,
        total_offer=total_offer,
        categories=categories,
        can_post=can_post,
        flagged_map=flagged_map,
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

    # Decide who receives the score bump (the service/provider), not the reviewer.
    # In normal flow:
    # - Need-help post (is_offer=False): provider is req_obj.helper_id
    # - Offer-help post (is_offer=True): provider is req_obj.user_id
    # But to be resilient to inconsistent legacy data, infer "the other participant"
    # from the current user.
    giver_id = None
    try:
        if req_obj.helper_id is not None:
            if user.id == req_obj.user_id:
                giver_id = req_obj.helper_id
            elif user.id == req_obj.helper_id:
                giver_id = req_obj.user_id
    except Exception:
        giver_id = None

    if giver_id is None:
        giver_id = req_obj.user_id if req_obj.is_offer else req_obj.helper_id

    # Final safety: never credit the reviewer.
    if giver_id is None or giver_id == user.id:
        flash("Could not determine who to credit for this completion.", "error")
        return redirect(url_for("dashboard"))

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


@app.route("/sos/<int:request_id>/complete/<int:helper_id>", methods=["POST"], endpoint="complete_sos")
@login_required
def complete_sos(request_id: int, helper_id: int):
    req_obj = Request.query.get_or_404(request_id)
    user = current_user()

    if not user or req_obj.user_id != user.id:
        flash("Unauthorized.", "error")
        return redirect(url_for("dashboard"))

    if (req_obj.category or "").lower() != "sos":
        flash("This is not an SOS request.", "error")
        return redirect(url_for("dashboard"))

    # Ensure the helper actually responded to this SOS
    responded = SOSResponse.query.filter_by(request_id=req_obj.id, helper_id=helper_id).first()
    if not responded:
        flash("That helper has not responded to this SOS.", "error")
        return redirect(url_for("dashboard"))

    rating = int(request.form.get("rating", 5))
    comment = request.form.get("comment", "")
    try:
        duration_input = float(request.form.get("hours", 1.0))
    except ValueError:
        duration_input = 1.0
    actual_hours = max(0.1, duration_input)

    # Mark SOS completed
    req_obj.status = "completed"
    req_obj.completed_at = datetime.utcnow()

    from reputation_service import analyze_review_quality

    ai_result = analyze_review_quality(comment, rating)
    review = Review(
        request_id=req_obj.id,
        reviewer_id=user.id,
        helper_id=helper_id,
        rating=rating,
        comment=comment,
        duration_hours=actual_hours,
        sentiment_score=ai_result["sentiment_score"],
        is_flagged_fake=ai_result["is_suspicious"],
        flag_reason=ai_result["flag_reason"],
    )
    db.session.add(review)
    db.session.commit()

    helper_user = User.query.get(helper_id)
    if helper_user:
        update_user_scores(helper_user)
        flash(f"SOS marked complete. {actual_hours} hours added to {helper_user.name}.", "success")
    else:
        flash("SOS marked complete.", "success")

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

    flagged_map = build_flagged_map_for_requests(requests_list)

    user = current_user()
    try:
        offered_ids = {
            int(o.request_id)
            for o in Offer.query.filter(
                (Offer.helper_id == user.id) | (Offer.user_id == user.id)
            ).all()
            if getattr(o, "request_id", None) is not None
        }
    except Exception:
        offered_ids = set()

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
        flagged_map=flagged_map,
    )


def _create_offer_record(req, user):
    """Create an offer entry with shared validation for HTML and AJAX flows."""
    if req.status != "open" or req.helper_id is not None:
        return False, "This request is no longer available.", 409, None

    if req.user_id == user.id:
        return False, "You cannot offer help on your own request.", 403, None

    if req.category and str(req.category).lower() == "sos":
        return False, "SOS posts can't be responded to using offers.", 400, None

    existing_offer = Offer.query.filter_by(request_id=req.id, helper_id=user.id).first()
    if existing_offer:
        return True, "You have already contacted this person.", 200, existing_offer.id

    new_offer = Offer(request_id=req.id, helper_id=user.id, user_id=user.id, status="pending")
    db.session.add(new_offer)
    db.session.commit()

    # Push: notify the owner of this request (best-effort)
    try:
        title = "New response on your LifeLine post"
        if req.is_offer:
            body = f"{user.name} is interested in your offer: “{req.title}”"
        else:
            body = f"{user.name} offered help on: “{req.title}”"

        send_fcm_to_user(
            req.user_id,
            title=title,
            body=body,
            data={
                "type": "REQUEST_REPLY",
                "request_id": req.id,
                "from_user_id": user.id,
            },
        )
    except Exception as e:
        print("[FCM] Failed to send offer notification:", e)

    # Auto-start chat (best-effort)
    try:
        get_or_create_conversation(req.user_id, user.id)
    except Exception:
        pass

    return True, "Your offer has been sent!", 201, new_offer.id


@app.route("/make_offer/<int:request_id>", methods=["POST"])
@login_required
def make_offer(request_id):
    user = current_user()
    req = Request.query.get_or_404(request_id)

    ok, message, status_code, offer_id = _create_offer_record(req, user)

    if status_code >= 400:
        flash(message, "error")
        return redirect(request.referrer or url_for("list_requests"))

    flash(message, "success" if status_code == 201 else "info")
    return redirect(request.referrer or url_for("list_requests", mode="offer" if req.is_offer else "need"))


@app.route("/requests/<int:request_id>/offer", methods=["POST"])
@login_required
def api_make_offer(request_id):
    """AJAX-friendly endpoint for sending an offer from AI suggestions."""
    user = current_user()
    req = Request.query.get_or_404(request_id)

    ok, message, status_code, offer_id = _create_offer_record(req, user)
    response = {"ok": ok, "message": message}
    if offer_id:
        response["offer_id"] = offer_id

    return jsonify(response), status_code


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

#@app.route("/accept_offer/<int:offer_id>", methods=["POST"])
#@login_required
#def accept_offer(offer_id):
    # Since there's no Offer model, this might be a placeholder
    # For now, assume offer_id is request_id or something
    # But in dashboard, it's offer.id, but offers don't exist
    # Perhaps this route is not needed, or I need to implement offers
    # For now, I'll make it a placeholder
    #flash("Accept offer functionality not implemented yet.", "error")
    #return redirect(url_for("dashboard"))

@app.route("/complete_request/<int:request_id>", methods=["POST"])
@login_required
def complete_request_placeholder(request_id):
    user = current_user()
    req = Request.query.get_or_404(request_id)

    if req.user_id != user.id:
        flash("You can only complete your own requests.", "error")
        return redirect(url_for("dashboard"))

    rating = request.form.get("rating")
    hours = request.form.get("hours")

    req.status = "completed"
    req.completed_at = datetime.utcnow()
    # Perhaps store rating and hours somewhere, but for now, just complete
    db.session.commit()

    flash("Request completed! Thank you for using LifeLine.", "success")
    return redirect(url_for("dashboard"))

@app.route("/events")
@login_required
def list_events():
    # Show both active and completed events so the UI can display
    # a full created → completed timeline.
    events = Event.query.order_by(Event.created_at.desc()).all()
    
    return render_template("events.html", events=events, google_maps_key=GOOGLE_MAPS_API_KEY)


# ------------------ Event creation ------------------
@app.route("/events/create", methods=["GET", "POST"])
@login_required
def create_event():
    user = current_user()

    if request.method == "POST":
        event = Event(
            creator_id=user.id,
            title=request.form["title"],
            description=request.form["description"],
            event_type=request.form["event_type"],
            date=datetime.strptime(request.form["date"], "%Y-%m-%d"),
            lat=float(request.form["lat"]),
            lng=float(request.form["lng"]),
            area=request.form["area"]
        )
        db.session.add(event)
        db.session.commit()

        # Auto-create fundraising goal if provided
        fund_goal = request.form.get("fund_goal", "0")
        fund_desc = request.form.get("fund_description", "").strip()
        try:
            fund_goal_val = float(fund_goal)
        except (ValueError, TypeError):
            fund_goal_val = 0
        if fund_goal_val > 0:
            bkash_num = request.form.get("bkash_number", "").strip()
            fund = EventFund(event_id=event.id, fund_goal=fund_goal_val,
                             description=fund_desc or f"Help fund '{event.title}'!",
                             bkash_number=bkash_num)
            db.session.add(fund)

        # Attach selected catering services
        catering_ids = request.form.getlist("catering_ids")
        guests_count = request.form.get("guests_count", "50")
        try:
            guests_count_val = int(guests_count)
        except (ValueError, TypeError):
            guests_count_val = 50
        for cid in catering_ids:
            try:
                cid_int = int(cid)
                if CateringService.query.get(cid_int):
                    ec = EventCatering(event_id=event.id, catering_id=cid_int, guests_count=guests_count_val)
                    db.session.add(ec)
            except (ValueError, TypeError):
                pass

        db.session.commit()
        notify_nearby_users(event)

        flash("Event created & nearby users notified!", "success")
        return redirect(url_for("event_fund_page", event_id=event.id))

    caterers = CateringService.query.filter_by(is_active=True).order_by(CateringService.rating.desc()).all()
    return render_template("create_event.html", google_maps_key=GOOGLE_MAPS_API_KEY, caterers=caterers)


@app.route("/events/<int:event_id>/interest", methods=["POST"])
@login_required
def event_interest(event_id):
    user = current_user()
    event = Event.query.get_or_404(event_id)

    exists = EventInterest.query.filter_by(
        event_id=event.id,
        user_id=user.id
    ).first()

    if not exists:
        db.session.add(EventInterest(event_id=event.id, user_id=user.id))
        db.session.commit()
        flash("You marked interest in this event!", "success")
    else:
        flash("You already marked interest.", "info")

    return redirect(url_for("list_events"))


@app.route("/events/map")
@login_required
def events_map():
    events = Event.query.all()

    def _fmt(dt):
        return dt.strftime("%B %d, %Y") if dt else ""

    events_json = []
    for e in events:
        events_json.append(
            {
                "title": e.title or "",
                "description": e.description or "",
                "lat": e.lat,
                "lng": e.lng,
                "date": _fmt(e.date),
                "area": e.area or "",
                "created": _fmt(e.created_at),
                "completed": _fmt((e.completed_at or e.date)) if e.completed else "",
                "isCompleted": bool(e.completed),
            }
        )

    return render_template(
        "events_map.html",
        events_json=events_json,
        google_maps_key=GOOGLE_MAPS_API_KEY,
    )


@app.route("/events/<int:event_id>/notify", methods=["POST"])
@login_required
def notify_event_users(event_id):
    event = Event.query.get_or_404(event_id)
    notify_interested_users(
        event,
        f"Reminder: '{event.title}' is happening soon!"
    )
    flash("Interested users notified!", "success")
    return redirect(url_for("list_events"))


@app.route("/events/<int:event_id>/complete", methods=["POST"])
@login_required
def complete_event(event_id):
    event = Event.query.get_or_404(event_id)
    user = current_user()

    # Creator OR any user who showed interest can complete
    if event.creator_id != user.id:
        # Check if user showed interest in this event
        interest = EventInterest.query.filter_by(event_id=event_id, user_id=user.id).first()
        if not interest:
            flash("You are not allowed to complete this event.", "danger")
            return redirect(url_for("list_events"))

    if event.completed:
        flash("Event already completed.", "info")
        return redirect(url_for("list_events"))

    event.completed = True
    event.completed_at = datetime.utcnow()
    db.session.commit()

    # ✅ AUTO impact update
    update_impact_from_event(event, user)

    flash("Event marked as completed & impact recorded!", "success")
    return redirect(url_for("dashboard"))

def update_impact_from_event(event, user):
    """
    Auto-calculates impact based on event type.
    This satisfies Module-3 'auto impact tracker' requirement.
    """

    hours = 0
    items = 0
    carbon = 0

    if event.event_type == "cleanup":
        hours = 3
        carbon = 5
    elif event.event_type == "donation":
        items = 10
    elif event.event_type == "repair":
        hours = 2

    impact = ImpactLog(
        helper_id=user.id,
        event_id=event.id,
        hours=hours,
        items=items,
        carbon=carbon
    )

    db.session.add(impact)
    db.session.commit()


# ==================== DONATION SYSTEM ====================

@app.route("/donate", methods=["GET", "POST"])
def donate_page():
    """General donation page — users can donate to LifeLine for community wellbeing."""
    user = current_user()
    if request.method == "POST":
        amount = float(request.form.get("amount", 0))
        if amount <= 0:
            flash("Please enter a valid donation amount.", "danger")
            return redirect(url_for("donate_page"))
        donor_name = request.form.get("donor_name", "Anonymous").strip() or "Anonymous"
        message = request.form.get("message", "").strip()
        payment_method = request.form.get("payment_method", "bkash")
        trx_id = request.form.get("trx_id", "").strip()

        donation = Donation(
            user_id=user.id if user else None,
            amount=amount,
            donor_name=donor_name if donor_name != "Anonymous" else (user.name if user else "Anonymous"),
            message=message,
            payment_method=payment_method,
            trx_id=trx_id,
            donation_type="general",
        )
        db.session.add(donation)
        db.session.commit()

        # Award points for donating — 1 kindness point per ৳100, min 1
        if user:
            bonus = max(1, int(amount // 100))
            user.kindness_score = (user.kindness_score or 0) + bonus
            user.trust_score = min(100, (user.trust_score or 0) + 1)
            db.session.commit()

        flash(f"Thank you for your generous donation of ৳{amount:,.0f}!", "success")
        return redirect(url_for("donate_page"))

    total_donations = db.session.query(func.coalesce(func.sum(Donation.amount), 0)).filter(Donation.status == "completed", Donation.donation_type != "event").scalar() or 0
    donation_count = Donation.query.filter(Donation.status == "completed", Donation.donation_type != "event").count()
    recent_donations = Donation.query.filter(Donation.status == "completed", Donation.donation_type != "event").order_by(Donation.created_at.desc()).limit(10).all()
    return render_template("donate.html", total_donations=total_donations, donation_count=donation_count, recent_donations=recent_donations)


@app.route("/events/<int:event_id>/fund", methods=["GET", "POST"])
@login_required
def event_fund_page(event_id):
    """Event fundraising page — users can donate to a specific event."""
    event = Event.query.get_or_404(event_id)
    user = current_user()

    # Ensure fund record exists
    fund = EventFund.query.filter_by(event_id=event_id).first()
    if not fund:
        fund = EventFund(event_id=event_id, fund_goal=10000, description="Help fund this community event!")
        db.session.add(fund)
        db.session.commit()

    if request.method == "POST":
        amount = float(request.form.get("amount", 0))
        if amount <= 0:
            flash("Please enter a valid amount.", "danger")
            return redirect(url_for("event_fund_page", event_id=event_id))

        payment_method = request.form.get("payment_method", "bkash")
        trx_id = request.form.get("trx_id", "").strip()
        message = request.form.get("message", "").strip()

        donation = Donation(
            user_id=user.id,
            amount=amount,
            donor_name=user.name,
            message=message,
            payment_method=payment_method,
            trx_id=trx_id,
            donation_type="event",
            event_id=event_id,
        )
        db.session.add(donation)

        fund.fund_raised = (fund.fund_raised or 0) + amount
        db.session.commit()

        # Award points for event fund contribution — 1 kindness point per ৳100, min 1
        bonus = max(1, int(amount // 100))
        user.kindness_score = (user.kindness_score or 0) + bonus
        user.trust_score = min(100, (user.trust_score or 0) + 1)
        db.session.commit()

        flash(f"৳{amount:,.0f} donated to '{event.title}'!", "success")
        return redirect(url_for("event_fund_page", event_id=event_id))

    event_donations = Donation.query.filter_by(event_id=event_id, status="completed").order_by(Donation.created_at.desc()).all()
    sponsors = EventSponsor.query.filter_by(event_id=event_id).order_by(EventSponsor.contribution_bdt.desc()).all()
    total_sponsor = sum(s.contribution_bdt for s in sponsors)
    creator = User.query.get(event.creator_id)
    catering_sel = EventCatering.query.filter_by(event_id=event_id).all()

    return render_template("event_fund.html", event=event, fund=fund,
                           event_donations=event_donations, sponsors=sponsors,
                           total_sponsor=total_sponsor, creator=creator,
                           catering_selections=catering_sel)


@app.route("/events/<int:event_id>/fund/setup", methods=["POST"])
@login_required
def event_fund_setup(event_id):
    """Creator sets the fundraising goal."""
    event = Event.query.get_or_404(event_id)
    user = current_user()
    if event.creator_id != user.id:
        flash("Only the event creator can set the fund goal.", "danger")
        return redirect(url_for("event_fund_page", event_id=event_id))

    goal = float(request.form.get("fund_goal", 10000))
    desc = request.form.get("fund_description", "").strip()

    fund = EventFund.query.filter_by(event_id=event_id).first()
    if not fund:
        fund = EventFund(event_id=event_id)
        db.session.add(fund)
    fund.fund_goal = goal
    fund.description = desc or "Help fund this community event!"
    bkash_num = request.form.get("bkash_number", "").strip()
    if bkash_num:
        fund.bkash_number = bkash_num
    db.session.commit()
    flash("Fundraising goal updated!", "success")
    return redirect(url_for("event_fund_page", event_id=event_id))


# ==================== CATERING SERVICES ====================

@app.route("/api/catering/nearby")
@login_required
def api_nearby_catering():
    """Return catering services, optionally filtered by proximity."""
    lat = request.args.get("lat", type=float)
    lng = request.args.get("lng", type=float)
    caterers = CateringService.query.filter_by(is_active=True).all()
    results = []
    for c in caterers:
        dist = None
        if lat and lng and c.lat and c.lng:
            from math import radians, cos, sin, asin, sqrt
            dlat = radians(c.lat - lat)
            dlng = radians(c.lng - lng)
            a = sin(dlat/2)**2 + cos(radians(lat)) * cos(radians(c.lat)) * sin(dlng/2)**2
            dist = 6371 * 2 * asin(sqrt(a))
        results.append({
            "id": c.id, "name": c.name, "description": c.description,
            "cuisine_type": c.cuisine_type, "price_per_head": c.price_per_head,
            "min_order": c.min_order, "phone": c.phone, "rating": c.rating,
            "total_reviews": c.total_reviews, "area": c.area,
            "distance_km": round(dist, 1) if dist else None,
        })
    results.sort(key=lambda x: x.get("distance_km") or 999)
    return jsonify(results)


@app.route("/events/<int:event_id>/catering", methods=["POST"])
@login_required
def add_event_catering(event_id):
    """Attach a catering service to an event."""
    event = Event.query.get_or_404(event_id)
    user = current_user()
    if event.creator_id != user.id:
        flash("Only the creator can select catering.", "danger")
        return redirect(url_for("event_fund_page", event_id=event_id))

    catering_id = int(request.form.get("catering_id", 0))
    guests_count = int(request.form.get("guests_count", 50))
    special_requests = request.form.get("special_requests", "").strip()

    if not CateringService.query.get(catering_id):
        flash("Invalid catering service.", "danger")
        return redirect(url_for("event_fund_page", event_id=event_id))

    existing = EventCatering.query.filter_by(event_id=event_id, catering_id=catering_id).first()
    if existing:
        flash("This caterer is already selected.", "info")
        return redirect(url_for("event_fund_page", event_id=event_id))

    ec = EventCatering(event_id=event_id, catering_id=catering_id,
                       guests_count=guests_count, special_requests=special_requests)
    db.session.add(ec)
    db.session.commit()
    flash("Catering service added to event!", "success")
    return redirect(url_for("event_fund_page", event_id=event_id))


# ==================== ADMIN SPONSOR MANAGEMENT ====================

@app.route("/admin/events/<int:event_id>/sponsor", methods=["POST"])
@login_required
def admin_add_sponsor(event_id):
    """Admin adds a sponsor to an event."""
    user = current_user()
    if not _is_admin_user(user):
        flash("Admin access required.", "danger")
        return redirect(url_for("home"))
    event = Event.query.get_or_404(event_id)

    sponsor = EventSponsor(
        event_id=event_id,
        sponsor_name=request.form.get("sponsor_name", "").strip(),
        sponsor_type=request.form.get("sponsor_type", "gold"),
        contribution_bdt=float(request.form.get("contribution_bdt", 0)),
        logo_url=request.form.get("logo_url", "").strip(),
    )
    db.session.add(sponsor)
    db.session.commit()
    flash(f"Sponsor '{sponsor.sponsor_name}' added to '{event.title}'!", "success")
    return redirect(url_for("event_fund_page", event_id=event_id))


# ==================== ADMIN DONATIONS & FUNDRAISING ====================
@app.route("/admin/donations")
@login_required
def admin_donations():
    """Admin page to view/manage all donations, event funds, and sponsors."""
    user = current_user()
    if not _is_admin_user(user):
        flash("Admin access required.", "danger")
        return redirect(url_for("home"))

    donations = Donation.query.order_by(Donation.created_at.desc()).all()
    event_funds = EventFund.query.order_by(EventFund.created_at.desc()).all()
    sponsors = EventSponsor.query.order_by(EventSponsor.created_at.desc()).all()

    # Summary stats
    total = sum(d.amount for d in donations if d.status == "completed")
    general_donations = [d for d in donations if d.donation_type != "event"]
    event_donations = [d for d in donations if d.donation_type == "event"]
    summary = {
        "total": total,
        "count": len(donations),
        "general_total": sum(d.amount for d in general_donations if d.status == "completed"),
        "general_count": len(general_donations),
        "event_total": sum(d.amount for d in event_donations if d.status == "completed"),
        "event_count": len(event_donations),
        "sponsor_total": sum(s.contribution_bdt for s in sponsors),
        "sponsor_count": len(sponsors),
        "pending_count": len([d for d in donations if d.status == "pending"]),
    }

    return render_template("admin_donations.html",
                           donations=donations, event_funds=event_funds,
                           sponsors=sponsors, summary=summary)


@app.route("/admin/donation/<int:donation_id>/<action>", methods=["POST"])
@login_required
def admin_donation_action(donation_id, action):
    """Admin approves, rejects, resets, or deletes a donation."""
    user = current_user()
    if not _is_admin_user(user):
        flash("Admin access required.", "danger")
        return redirect(url_for("home"))

    donation = Donation.query.get_or_404(donation_id)

    if action == "approve":
        donation.status = "completed"
        db.session.commit()
        flash(f"Donation #{donation_id} approved.", "success")
    elif action == "reject":
        donation.status = "rejected"
        db.session.commit()
        flash(f"Donation #{donation_id} rejected.", "warning")
    elif action == "pending":
        donation.status = "pending"
        db.session.commit()
        flash(f"Donation #{donation_id} set back to pending.", "info")
    elif action == "delete":
        # If it was an event fund, subtract from fund_raised
        if donation.donation_type == "event" and donation.event_id and donation.status == "completed":
            fund = EventFund.query.filter_by(event_id=donation.event_id).first()
            if fund:
                fund.fund_raised = max(0, (fund.fund_raised or 0) - donation.amount)
        db.session.delete(donation)
        db.session.commit()
        flash(f"Donation #{donation_id} deleted.", "warning")
    else:
        flash("Unknown action.", "danger")

    return redirect(url_for("admin_donations"))


@app.route("/admin/sponsor/<int:sponsor_id>/<action>", methods=["POST"])
@login_required
def admin_sponsor_action(sponsor_id, action):
    """Admin deletes a sponsor."""
    user = current_user()
    if not _is_admin_user(user):
        flash("Admin access required.", "danger")
        return redirect(url_for("home"))

    sponsor = EventSponsor.query.get_or_404(sponsor_id)

    if action == "delete":
        db.session.delete(sponsor)
        db.session.commit()
        flash(f"Sponsor '{sponsor.sponsor_name}' deleted.", "warning")
    else:
        flash("Unknown action.", "danger")

    return redirect(url_for("admin_donations"))


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

    # Best-effort: mark chat notifications for this conversation as read.
    try:
        link = url_for("chat_with_user", other_user_id=other.id)
        Notification.query.filter_by(user_id=user.id, is_read=False, type="chat", link=link).update({"is_read": True})
        db.session.commit()
    except Exception:
        db.session.rollback()

    # Push updated counts (avoid stale badges after reading in chat)
    try:
        _emit_counts_update(user.id)
    except Exception:
        pass
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

    # Sort by newest last message so recent chats appear at top.
    conversations.sort(key=lambda x: (x.get("last_time") or 0), reverse=True)

    conversation_ids = [item["conversation"].id for item in conversations]

    # also show some nearby helpers to start a new chat (trusted helpers)
    helpers = User.query.filter(User.is_trusted_helper == True, User.id != user.id).limit(10).all()
    return render_template(
        "chat_index.html",
        conversations=conversations,
        helpers=helpers,
        conversation_ids=conversation_ids,
    )

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

        # Mark which requests already have an offer from this user
        try:
            user_id = getattr(user, "id", None)
            if user_id and nearby:
                nearby_ids = [int(r.get("id")) for r in nearby if r.get("id") is not None]
                if nearby_ids:
                    offered_rows = (
                        Offer.query.filter(Offer.request_id.in_(nearby_ids))
                        .filter((Offer.helper_id == user_id) | (Offer.user_id == user_id))
                        .all()
                    )
                    offered_ids = {int(o.request_id) for o in offered_rows if getattr(o, "request_id", None) is not None}
                    for r in nearby:
                        rid = r.get("id")
                        if rid is not None:
                            r["already_offered"] = int(rid) in offered_ids
        except Exception:
            pass

        return jsonify({"requests": nearby}), 200
    except Exception as e:
        print(f"[API] Error in api_nearby_requests: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/dashboard")
@login_required
def dashboard():
    user = current_user()

    # Make sure scores are up to date
    update_user_scores(user)

    # Badge label + Tailwind color class
    badge, badge_color = user.calculate_badge()

    # Completed help stats should be based on verified reviews.
    review_rows_desc = (
        Review.query.filter(Review.helper_id == user.id)
        .order_by(Review.created_at.desc())
        .all()
    )

    helped_count = len({rv.request_id for rv in review_rows_desc})

    # Compute total hours (all-time) + build small recent history list
    total_hours_all = 0.0
    history = []

    # Preload referenced requests (avoid N+1)
    recent_request_ids = list({rv.request_id for rv in review_rows_desc[:20]})
    req_map = {}
    if recent_request_ids:
        req_map = {r.id: r for r in Request.query.filter(Request.id.in_(recent_request_ids)).all()}

    for rv in review_rows_desc:
        try:
            total_hours_all += float(rv.duration_hours or 0.0)
        except Exception:
            pass

    for rv in review_rows_desc[:5]:
        req_obj = req_map.get(rv.request_id)
        history.append(
            {
                "title": (req_obj.title if req_obj else "Request"),
                "category": ((req_obj.category if req_obj else None) or "General"),
                "hours": round(float(rv.duration_hours or 0.0), 2),
                "date": (
                    req_obj.completed_at.strftime("%b %d, %Y")
                    if (req_obj and req_obj.completed_at)
                    else (rv.created_at.strftime("%b %d, %Y") if rv.created_at else "")
                ),
            }
        )

    total_hours_all = round(float(total_hours_all), 2)

    # Compute available kindness (total - spent on vouchers)
    _voucher_spent = db.session.query(func.coalesce(func.sum(Voucher.points_spent), 0)).filter(Voucher.user_id == user.id).scalar() or 0
    available_kindness = max(0, (user.kindness_score or 0) - int(_voucher_spent))

    # stats object used in dashboard.html
    stats = {
        "badge": badge,
        "badge_color": badge_color,       # e.g. "text-yellow-300"
        "helped": helped_count,          # total people helped
        "total_hours": total_hours_all,      # total hours volunteered (all-time)
        "trust": user.trust_score or 0,  # 0–100 (we already cap in update_user_scores)
        "kindness": available_kindness,
        "kindness_total": user.kindness_score or 0,
    }

    # Chart data: rebuild score progression from completed requests.
    # If a user has 0–1 completed tasks, Chart.js will render a single point.
    # To keep the UI consistent and meaningful, always provide a small time-series
    # that ramps from 0 → current score.
    chart_labels: list[str] = []
    chart_trust: list[int] = []
    chart_kindness: list[int] = []
    # Use review dates when available; otherwise show last N days.
    if review_rows_desc:
        review_rows_asc = list(reversed(review_rows_desc))
        for rv in review_rows_asc:
            chart_labels.append(rv.created_at.strftime("%b %d") if rv.created_at else "")

    # Keep the graph readable: show only the last N points
    max_points = 12
    if len(chart_labels) > max_points:
        chart_labels = chart_labels[-max_points:]

    # Ensure at least 7 points (create missing days if needed)
    min_points = 7
    if len(chart_labels) < min_points:
        end = datetime.utcnow().date()
        labels = [
            (end - timedelta(days=i)).strftime("%b %d")
            for i in range(min_points - 1, -1, -1)
        ]
        if chart_labels:
            pad_len = min_points - len(chart_labels)
            chart_labels = labels[:pad_len] + chart_labels
        else:
            chart_labels = labels

    # Build a smooth 0 → current progression so the chart is never flat/bland.
    end_trust = int(stats.get("trust") or 0)
    end_kindness = int(stats.get("kindness") or 0)
    n = len(chart_labels)
    if n <= 1:
        n = 2
        chart_labels = chart_labels or [datetime.utcnow().strftime("%b %d"), datetime.utcnow().strftime("%b %d")]

    def _smoothstep(t: float) -> float:
        t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
        return t * t * (3.0 - 2.0 * t)

    chart_trust = []
    chart_kindness = []
    for i in range(n):
        t = i / (n - 1) if n > 1 else 1.0
        e = _smoothstep(t)
        chart_trust.append(int(round(end_trust * e)))
        chart_kindness.append(int(round(end_kindness * e)))

    # Dashboard task lists: query explicitly (avoid relying on relationship loader behavior)
    now = datetime.utcnow()

    my_posts = (
        Request.query.filter(
            Request.user_id == user.id,
            Request.status.in_(["open", "in_progress", "claimed"]),
        )
        .order_by(Request.created_at.desc())
        .all()
    )

    # Also include expired posts that received no responses, so the user can remove them.
    # (These are often auto-closed and would otherwise not show up in the dashboard list.)
    try:
        expired_candidates = (
            Request.query.filter(
                Request.user_id == user.id,
                Request.expires_at <= now,
                Request.helper_id == None,  # noqa: E711
            )
            .order_by(Request.created_at.desc())
            .limit(200)
            .all()
        )
        expired_unanswered = []
        for r in expired_candidates:
            cat = (getattr(r, "category", "") or "").lower()
            if cat == "sos":
                if SOSResponse.query.filter(SOSResponse.request_id == r.id).count() == 0:
                    expired_unanswered.append(r)
            else:
                if Offer.query.filter(Offer.request_id == r.id).count() == 0:
                    expired_unanswered.append(r)

        if expired_unanswered:
            seen = {int(r.id) for r in my_posts if getattr(r, "id", None) is not None}
            for r in expired_unanswered:
                rid = getattr(r, "id", None)
                if rid is None:
                    continue
                if int(rid) in seen:
                    continue
                my_posts.append(r)
                seen.add(int(rid))
            # keep newest first
            my_posts.sort(key=lambda x: x.created_at or datetime.min, reverse=True)
    except Exception:
        pass
    active_engagements = (
        Request.query.filter(
            Request.helper_id == user.id,
            Request.status.in_(["in_progress", "claimed"]),
        )
        .order_by(Request.created_at.desc())
        .all()
    )
    pending_offers = (
        Offer.query.filter(
            Offer.user_id == user.id,
            Offer.status == "pending",
            Offer.request_id != None,
        )
        .order_by(Offer.created_at.desc())
        .all()
    )

    # Optional SOS review card (used by dashboard.html)
    sos_review_req = None
    sos_review_helper = None
    try:
        req_id = request.args.get("sos_review_request_id", type=int)
        helper_id = request.args.get("sos_review_helper_id", type=int)
        if req_id and helper_id:
            sos_review_req = Request.query.get(req_id)
            sos_review_helper = User.query.get(helper_id)
    except Exception:
        sos_review_req = None
        sos_review_helper = None

    # Get events where user showed interest OR events they created
    interested_event_ids = [ei.event_id for ei in EventInterest.query.filter_by(user_id=user.id).all()]
    
    # Combine: events user is interested in OR events user created
    user_events = Event.query.filter(
        db.or_(
            Event.id.in_(interested_event_ids) if interested_event_ids else False,
            Event.creator_id == user.id
        )
    ).filter_by(completed=False).order_by(Event.date.desc()).limit(5).all()

    return render_template(
        "dashboard.html",
        user=user,
        stats=stats,
        history=history,
        chart_labels=chart_labels,
        chart_trust=chart_trust,
        chart_kindness=chart_kindness,
        user_events=user_events,
        my_posts=my_posts,
        active_engagements=active_engagements,
        pending_offers=pending_offers,
        sos_review_req=sos_review_req,
        sos_review_helper=sos_review_helper,
        now=now,
    )


@app.route("/requests/<int:request_id>/remove-expired-unanswered", methods=["POST"])
@login_required
def remove_expired_unanswered_request(request_id):
    """Remove a request only when it is expired AND has zero responses."""
    user = current_user()
    r = Request.query.get_or_404(request_id)

    if r.user_id != user.id:
        flash("You are not allowed to remove this post.", "error")
        return redirect(url_for("dashboard"))

    now = datetime.utcnow()
    if not getattr(r, "expires_at", None) or r.expires_at > now:
        flash("You can only remove a post after it expires.", "error")
        return redirect(url_for("dashboard"))

    cat = (getattr(r, "category", "") or "").lower()
    if cat == "sos":
        resp_count = SOSResponse.query.filter(SOSResponse.request_id == r.id).count()
    else:
        resp_count = Offer.query.filter(Offer.request_id == r.id).count()

    if resp_count and int(resp_count) > 0:
        flash("You can't remove this post because someone already responded.", "error")
        return redirect(url_for("dashboard"))

    db.session.delete(r)
    db.session.commit()
    flash("Post removed.", "success")
    return redirect(url_for("dashboard"))


# ------------------ VOUCHER API ------------------

@app.route("/api/vouchers/available-tiers", methods=["GET"])
@login_required
def api_voucher_tiers():
    """Return which voucher tiers the user can currently claim."""
    user = current_user()
    update_user_scores(user)
    kindness = user.kindness_score or 0
    # Subtract points already spent on vouchers
    spent = db.session.query(func.coalesce(func.sum(Voucher.points_spent), 0)).filter(Voucher.user_id == user.id).scalar() or 0
    available = max(0, kindness - int(spent))

    now = datetime.utcnow()
    cutoff = now - timedelta(days=30)

    # Per-tier cooldown: check each tier individually
    tiers = []
    for min_pts, discount, cost in VOUCHER_TIERS:
        # Check if this specific tier (by discount_percent) was claimed in last 30 days
        recent_claim = Voucher.query.filter(
            Voucher.user_id == user.id,
            Voucher.discount_percent == discount,
            Voucher.created_at > cutoff,
        ).first()

        tier_cooldown = False
        tier_cooldown_days = 0
        tier_cooldown_date = None
        if recent_claim:
            tier_cooldown = True
            next_date = recent_claim.created_at + timedelta(days=30)
            tier_cooldown_days = (next_date - now).days + 1
            tier_cooldown_date = next_date.strftime("%b %d, %Y")

        tiers.append({
            "min_points": min_pts,
            "discount_percent": discount,
            "points_cost": cost,
            "can_claim": (available >= cost) and (not tier_cooldown),
            "cooldown": tier_cooldown,
            "cooldown_days": tier_cooldown_days,
            "cooldown_date": tier_cooldown_date,
        })
    return jsonify({
        "kindness_score": available,
        "tiers": tiers,
    })


@app.route("/api/vouchers/claim", methods=["POST"])
@login_required
def api_voucher_claim():
    """Claim a voucher by spending kindness points."""
    user = current_user()
    update_user_scores(user)
    kindness = user.kindness_score or 0
    # Subtract points already spent on vouchers
    spent = db.session.query(func.coalesce(func.sum(Voucher.points_spent), 0)).filter(Voucher.user_id == user.id).scalar() or 0
    available = max(0, kindness - int(spent))

    data = request.get_json(force=True) if request.is_json else {}
    tier_index = data.get("tier_index", None)
    if tier_index is None:
        return jsonify({"error": "Missing tier_index"}), 400
    try:
        tier_index = int(tier_index)
    except (ValueError, TypeError):
        return jsonify({"error": "Invalid tier_index"}), 400

    if tier_index < 0 or tier_index >= len(VOUCHER_TIERS):
        return jsonify({"error": "Invalid tier"}), 400

    min_pts, discount, cost = VOUCHER_TIERS[tier_index]
    if available < cost:
        return jsonify({"error": f"You need at least {cost} available points. You have {available}."}), 400

    # Per-tier 30-day cooldown check
    cutoff = datetime.utcnow() - timedelta(days=30)
    recent_same_tier = Voucher.query.filter(
        Voucher.user_id == user.id,
        Voucher.discount_percent == discount,
        Voucher.created_at > cutoff,
    ).first()
    if recent_same_tier:
        next_date = recent_same_tier.created_at + timedelta(days=30)
        days_left = (next_date - datetime.utcnow()).days + 1
        return jsonify({"error": f"This {discount}% tier is on cooldown! You can claim it again in {days_left} days (after {next_date.strftime('%b %d, %Y')}). Try a different tier!"}), 400

    # Pick random store and design
    store = random.choice(VOUCHER_STORES)
    design = random.randint(1, 6)
    code = _generate_voucher_code()

    voucher = Voucher(
        user_id=user.id,
        store_name=store,
        code=code,
        discount_percent=discount,
        points_spent=cost,
        design_variant=design,
    )
    db.session.add(voucher)
    db.session.commit()

    new_available = max(0, available - cost)
    return jsonify({"voucher": voucher.to_dict(), "new_kindness": new_available})


@app.route("/api/vouchers/my", methods=["GET"])
@login_required
def api_my_vouchers():
    """Return all vouchers claimed by the current user."""
    user = current_user()
    vouchers = Voucher.query.filter_by(user_id=user.id).order_by(Voucher.created_at.desc()).all()
    return jsonify({"vouchers": [v.to_dict() for v in vouchers]})


# ------------------ API: Dashboard & Impact  ------------------

# 1) Dashboard summary
@app.route("/api/dashboard/summary", methods=["GET"])
@login_required
def api_dashboard_summary():
    user = current_user()
    # update scores (make sure values are fresh)
    update_user_scores(user)

    # total hours volunteered (verified review durations)
    total_hours = (
        db.session.query(func.coalesce(func.sum(Review.duration_hours), 0.0))
        .filter(Review.helper_id == user.id)
        .scalar()
    ) or 0.0
    total_hours = round(float(total_hours), 2)

    # people helped: distinct reviewers who left a completion review for this helper
    people_helped = (
        db.session.query(func.count(func.distinct(Review.reviewer_id)))
        .filter(Review.helper_id == user.id)
        .scalar()
    ) or 0

    # Available kindness = total - spent on vouchers
    _v_spent = db.session.query(func.coalesce(func.sum(Voucher.points_spent), 0)).filter(Voucher.user_id == user.id).scalar() or 0
    avail_kindness = max(0, int(user.kindness_score or 0) - int(_v_spent))

    return jsonify({
        "total_hours": total_hours,
        "people_helped": int(people_helped),
        "trust_score": int(user.trust_score or 0),
        "kindness_score": avail_kindness,
    })


# 2) Dashboard kindness meter
@app.route("/api/dashboard/kindness", methods=["GET"])
@login_required
def api_dashboard_kindness():
    user = current_user()
    score = int(user.kindness_score or 0)
    # same logic as calculate_badge thresholds
    if score >= 121:
        level = "Community Star"
    elif score >= 71:
        level = "Gold Helper"
    elif score >= 31:
        level = "Silver Helper"
    elif score >= 11:
        level = "Bronze Helper"
    else:
        level = "Newbie"

    # percent to next (simple example)
    next_threshold = 121 if score >= 121 else (71 if score >= 71 else (31 if score >= 31 else (11 if score >= 11 else 11)))
    prev_threshold = 71 if score >= 71 and score < 121 else (31 if score >= 31 and score < 71 else (11 if score >= 11 and score < 31 else 0))
    span = max(1, next_threshold - prev_threshold)
    progress_in_span = score - prev_threshold
    percent_to_next = min(100, int((progress_in_span / span) * 100)) if span else 100

    return jsonify({
        "score": score,
        "level": level,
        "percent_to_next": percent_to_next
    })


# 3) Dashboard: impact over time (monthly hours) ?months param default 6
@app.route("/api/dashboard/impact-over-time", methods=["GET"])
@login_required
def api_dashboard_impact_over_time():
    user = current_user()
    months = int(request.args.get("months", 6))
    now = datetime.utcnow()
    labels = []
    data = []
    # build monthly buckets
    for i in range(months - 1, -1, -1):
        # approximate month start
        bucket_start = (now - timedelta(days=30 * i)).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        bucket_end = (bucket_start + timedelta(days=32)).replace(day=1)
        hours = (
            db.session.query(func.coalesce(func.sum(Review.duration_hours), 0.0))
            .join(Request, Review.request_id == Request.id)
            .filter(
                Review.helper_id == user.id,
                Review.created_at >= bucket_start,
                Review.created_at < bucket_end,
                Request.status == "completed",
            )
            .scalar()
        ) or 0.0
        hours = float(hours)
        labels.append(bucket_start.strftime("%b %Y"))
        data.append(round(hours, 2))
    return jsonify({"labels": labels, "data": data})

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
    if not _is_admin_user(user):
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
    if not _is_admin_user(user):
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


@app.route("/admin/revenue")
@login_required
@admin_required
def admin_revenue():
    # --- Subscriptions (bKash manual) ---
    sub_rows = Payment.query.order_by(Payment.created_at.desc()).all()

    # --- Shop/Service requests ---
    shop_rows = ShopRequest.query.order_by(ShopRequest.created_at.desc()).all()

    # --- Psychiatry appointments ---
    appt_rows = PsychiatryAppointment.query.order_by(PsychiatryAppointment.created_at.desc()).all()

    transactions = []

    def _fmt_dt(dt):
        try:
            return dt.strftime("%Y-%m-%d %H:%M")
        except Exception:
            return ""

    # Subscription payments
    for p in sub_rows:
        gross = float(p.amount or 0.0)
        realized = (p.status == "approved")
        revenue = gross if realized else 0.0
        profit = revenue
        transactions.append(
            {
                "source": "Subscription",
                "source_id": p.id,
                "user_name": p.user.name if p.user else "Unknown",
                "user_email": p.user.email if p.user else "",
                "status": p.status,
                "gross": gross,
                "revenue": revenue,
                "profit": profit,
                "created_at": p.created_at,
                "created_at_human": _fmt_dt(p.created_at),
                "details": f"bKash {p.bkash_number} · TrxID {p.trx_id}",
            }
        )

    # Shop/service requests (gross is total_cost; revenue/profit is commission)
    for sr in shop_rows:
        gross = float(sr.total_cost or 0.0)
        revenue = float(sr.lifeline_commission or 0.0)
        profit = revenue
        transactions.append(
            {
                "source": "Service",
                "source_id": sr.id,
                "user_name": sr.user.name if sr.user else "Unknown",
                "user_email": sr.user.email if sr.user else "",
                "status": sr.status,
                "gross": gross,
                "revenue": revenue,
                "profit": profit,
                "created_at": sr.created_at,
                "created_at_human": _fmt_dt(sr.created_at),
                "details": f"{sr.title} · Shop: {(sr.shop.name if sr.shop else 'Unknown')} · Fee: ৳{int(sr.service_fee or 0)}",
            }
        )

    # Psychiatry appointments (gross is total; revenue/profit is platform fee only when paid)
    for appt in appt_rows:
        gross = float(appt.total_bdt or 0)
        realized = (appt.payment_status == "paid")
        revenue = float(appt.platform_fee_bdt or 0) if realized else 0.0
        profit = revenue
        transactions.append(
            {
                "source": "Psychiatry",
                "source_id": appt.id,
                "user_name": appt.user.name if appt.user else "Unknown",
                "user_email": appt.user.email if appt.user else "",
                "status": appt.payment_status,
                "gross": gross,
                "revenue": revenue,
                "profit": profit,
                "created_at": appt.created_at,
                "created_at_human": _fmt_dt(appt.created_at),
                "details": f"{appt.psychiatrist_name} · {appt.hospital_name} · {appt.appointment_date} {appt.appointment_time} · {appt.payment_method or 'n/a'}",
                "psy_actionable": bool((appt.payment_method == "bkash") and (appt.payment_status == "submitted")),
            }
        )

    # Sort: newest first
    transactions.sort(key=lambda x: x.get("created_at") or datetime.min, reverse=True)

    total_gross = round(sum(t["gross"] for t in transactions), 2)
    total_revenue = round(sum(t["revenue"] for t in transactions), 2)
    total_profit = round(sum(t["profit"] for t in transactions), 2)

    by_source = {
        "Subscription": {"count": 0, "gross": 0.0, "revenue": 0.0, "profit": 0.0},
        "Service": {"count": 0, "gross": 0.0, "revenue": 0.0, "profit": 0.0},
        "Psychiatry": {"count": 0, "gross": 0.0, "revenue": 0.0, "profit": 0.0},
    }
    for t in transactions:
        s = t.get("source")
        if s not in by_source:
            by_source[s] = {"count": 0, "gross": 0.0, "revenue": 0.0, "profit": 0.0}
        by_source[s]["count"] += 1
        by_source[s]["gross"] += float(t["gross"])
        by_source[s]["revenue"] += float(t["revenue"])
        by_source[s]["profit"] += float(t["profit"])

    for s in by_source:
        by_source[s]["gross"] = round(by_source[s]["gross"], 2)
        by_source[s]["revenue"] = round(by_source[s]["revenue"], 2)
        by_source[s]["profit"] = round(by_source[s]["profit"], 2)

    summary = {
        "total_gross": total_gross,
        "total_revenue": total_revenue,
        "total_profit": total_profit,
        "count": len(transactions),
    }

    return render_template(
        "admin_revenue.html",
        summary=summary,
        by_source=by_source,
        transactions=transactions[:300],
    )


@app.route("/admin/psychiatry-appointment/<int:appointment_id>/<action>", methods=["POST"])
@login_required
@admin_required
def admin_psychiatry_payment_action(appointment_id, action):
    appt = PsychiatryAppointment.query.get_or_404(appointment_id)
    if action == "approve":
        appt.payment_status = "paid"
        if not appt.paid_at:
            appt.paid_at = datetime.utcnow()
        flash("Appointment payment marked as PAID.", "success")
    elif action == "reject":
        appt.payment_status = "rejected"
        flash("Appointment payment rejected.", "error")
    else:
        flash("Invalid action.", "error")
        return redirect(url_for("admin_revenue"))

    db.session.commit()
    return redirect(url_for("admin_revenue"))


# ───────────── User Orders & Payment Receipts ─────────────
@app.route("/my/orders")
@login_required
def my_orders():
    user = current_user()

    orders = []

    def _fmt(dt):
        try:
            return dt.strftime("%d %b %Y, %I:%M %p")
        except Exception:
            return ""

    # Subscription payments
    for p in Payment.query.filter_by(user_id=user.id).order_by(Payment.created_at.desc()).all():
        orders.append({
            "type": "Subscription",
            "id": p.id,
            "date": p.created_at,
            "date_human": _fmt(p.created_at),
            "status": p.status,
            "total": float(p.amount or 0),
            "breakdown": [
                {"label": "Subscription Fee", "amount": float(p.amount or 0)},
            ],
            "method": f"bKash ({p.bkash_number})",
            "trx_id": p.trx_id or "—",
            "details": "LifeLine Premium Subscription",
        })

    # Service / shop requests
    for sr in ShopRequest.query.filter_by(user_id=user.id).order_by(ShopRequest.created_at.desc()).all():
        shop_name = sr.shop.name if sr.shop else "Unknown Shop"
        orders.append({
            "type": "Service",
            "id": sr.id,
            "date": sr.created_at,
            "date_human": _fmt(sr.created_at),
            "status": sr.status,
            "total": float(sr.total_cost or 0),
            "breakdown": [
                {"label": "Service Fee", "amount": float(sr.service_fee or 0)},
                {"label": "Labor Cost", "amount": float(sr.labor_cost or 0)},
                {"label": "Parts Cost", "amount": float(sr.parts_cost or 0)},
                {"label": "LifeLine Commission", "amount": float(sr.lifeline_commission or 0)},
            ],
            "method": "—",
            "trx_id": "—",
            "details": f"{sr.title} · {shop_name}",
        })

    # Psychiatry appointments
    for appt in PsychiatryAppointment.query.filter_by(user_id=user.id).order_by(PsychiatryAppointment.created_at.desc()).all():
        method_label = (appt.payment_method or "—").capitalize()
        orders.append({
            "type": "Psychiatry",
            "id": appt.id,
            "date": appt.created_at,
            "date_human": _fmt(appt.created_at),
            "status": appt.payment_status,
            "total": float(appt.total_bdt or 0),
            "breakdown": [
                {"label": "Consultation Fee", "amount": float(appt.fee_bdt or 0)},
                {"label": "Platform Fee", "amount": float(appt.platform_fee_bdt or 0)},
            ],
            "method": method_label,
            "trx_id": "—",
            "details": f"{appt.psychiatrist_name} · {appt.hospital_name} · {appt.appointment_date} {appt.appointment_time}",
        })

    # Sort newest first
    orders.sort(key=lambda x: x.get("date") or datetime.min, reverse=True)

    totals = {
        "count": len(orders),
        "total_spent": round(sum(o["total"] for o in orders), 2),
    }

    return render_template("my_orders.html", orders=orders, totals=totals)


# ------------------ Impact APIs ------------------

# 4) Impact summary (resources shared, carbon saved simple estimate, hours)
@app.route("/api/impact/summary", methods=["GET"])
@login_required
def api_impact_summary():
    user = current_user()

    # resources shared count (Resource model exists)
    resources_shared = Resource.query.filter(Resource.user_id == user.id).count()
    # hours volunteered (verified review durations)
    hours_vol = (
        db.session.query(func.coalesce(func.sum(Review.duration_hours), 0.0))
        .filter(Review.helper_id == user.id)
        .scalar()
    ) or 0.0
    hours_vol = float(hours_vol)
    # helped people
    helped_people = (
        db.session.query(func.count(func.distinct(Review.reviewer_id)))
        .filter(Review.helper_id == user.id)
        .scalar()
    ) or 0
    # carbon saved estimate (example: each resource share counts as 0.5 "unit" saved)
    carbon_units = db.session.query(func.coalesce(func.sum(Resource.quantity), 0)).filter(Resource.user_id == user.id).scalar() or 0
    # convert to percent (arbitrary scale for UI)
    carbon_saved_percent = min(100, int(carbon_units * 2))  # example formula

    return jsonify({
        "resources_shared": int(resources_shared),
        "hours_volunteered": round(hours_vol, 2),
        "helped_people": int(helped_people),
        "carbon_saved_percent": carbon_saved_percent
    })


# 5) Impact by category (pie data) - uses Resource.category aggregations
@app.route("/api/impact/by-category", methods=["GET"])
@login_required
def api_impact_by_category():
    user = current_user()
    rows = db.session.query(Resource.category, func.coalesce(func.sum(Resource.quantity), 0)).filter(Resource.user_id == user.id).group_by(Resource.category).all()
    labels = [r[0] or "Uncategorized" for r in rows]
    values = [int(r[1]) for r in rows]
    return jsonify({"labels": labels, "values": values})

# 6) Create an impact story
@app.route("/api/impact/story", methods=["POST"])
@login_required
def api_impact_story_create():
    user = current_user()
    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "").strip()
    body = (data.get("body") or "").strip()
    if not title or not body:
        return jsonify({"error": "title and body required"}), 400
    s = ImpactStory(user_id=user.id, title=title[:255], body=body)
    db.session.add(s)
    db.session.commit()
    return jsonify({"id": s.id, "created_at": int(s.created_at.timestamp())}), 201


# 7) List impact stories (pagination)
@app.route("/api/impact/stories", methods=["GET"])
@login_required
def api_impact_stories():
    page = int(request.args.get("page", 1))
    per_page = int(request.args.get("per_page", 10))
    q = ImpactStory.query.order_by(ImpactStory.created_at.desc())
    total = q.count()
    items = q.offset((page - 1) * per_page).limit(per_page).all()
    stories = [{
        "id": it.id,
        "title": it.title,
        "body": it.body,
        "author_name": it.user.name if it.user else None,
        "created_at": int(it.created_at.timestamp())
    } for it in items]
    return jsonify({"total": total, "page": page, "per_page": per_page, "stories": stories})


# 8) Community-wide impact summary
@app.route("/api/community/impact", methods=["GET"])
def api_community_impact():
    # Source of truth: real DB rows only (no synthetic/demo rollups).
    # Hours: verified request-completion reviews + event impact logs.
    review_hours = (
        db.session.query(func.coalesce(func.sum(Review.duration_hours), 0.0)).scalar()
        or 0.0
    )
    event_hours = (
        db.session.query(func.coalesce(func.sum(ImpactLog.hours), 0.0)).scalar()
        or 0.0
    )
    total_hours = float(review_hours) + float(event_hours)

    all_impacts = ImpactLog.query.all()
    total_items = sum(int(impact.items or 0) for impact in all_impacts)
    total_carbon = sum(float(impact.carbon or 0) for impact in all_impacts)

    # CO₂ saved (kg) from completed shared rides.
    # Uses verified request completions (reviews) as the source of truth.
    # Each completed ride is counted once.
    try:
        ride_request_ids = (
            db.session.query(Review.request_id)
            .join(Request, Request.id == Review.request_id)
            .filter(func.lower(Request.category) == "ride")
            .distinct()
            .all()
        )
        ride_count = len(ride_request_ids)
    except Exception:
        ride_count = 0

    # Simple per-ride kg estimate used elsewhere in the codebase.
    total_carbon += float(ride_count) * 2.5

    # Neighbors helped = unique people who received help from the community.
    # Use completed request reviews as verification; infer the recipient from request type.
    recipient_rows = (
        db.session.query(Request.is_offer, Request.user_id, Request.helper_id)
        .join(Review, Review.request_id == Request.id)
        .all()
    )
    recipient_ids = set()
    for is_offer, owner_id, helper_id in recipient_rows:
        recipient_id = helper_id if is_offer else owner_id
        if recipient_id:
            recipient_ids.add(int(recipient_id))
    total_helped = len(recipient_ids)

    return jsonify(
        {
            "total_hours": round(float(total_hours), 1),
            "total_items": int(total_items),
            "total_carbon": round(float(total_carbon), 1),
            "total_helped": int(total_helped),
        }
    )


# 9) Community impact over time (monthly data for graphs)
@app.route("/api/community/impact-over-time", methods=["GET"])
def api_community_impact_over_time():
    # Always return a fixed timeline so the chart renders consistently
    # (even when there is no data for some months).
    try:
        months = int(request.args.get("months", 6))
    except Exception:
        months = 6
    months = max(1, min(months, 24))

    end_date = datetime.utcnow()

    def _month_start(dt: datetime) -> datetime:
        return dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    def _add_months(dt: datetime, delta_months: int) -> datetime:
        # dt is expected to already be at month-start.
        m0 = (dt.month - 1) + int(delta_months)
        year = dt.year + (m0 // 12)
        month = (m0 % 12) + 1
        return dt.replace(year=year, month=month, day=1, hour=0, minute=0, second=0, microsecond=0)

    end_month = _month_start(end_date)
    start_month = _add_months(end_month, -(months - 1))
    end_exclusive = _add_months(end_month, 1)

    # DB-agnostic bucketing: aggregate in Python instead of relying on
    # database-specific strftime/to_char/date_trunc behavior.
    from collections import defaultdict

    buckets = defaultdict(lambda: {"hours": 0.0, "items": 0, "carbon": 0.0})
    rows = (
        db.session.query(
            ImpactLog.created_at,
            ImpactLog.hours,
            ImpactLog.items,
            ImpactLog.carbon,
            ImpactLog.event_id,
        )
        .filter(ImpactLog.created_at >= start_month)
        .filter(ImpactLog.created_at < end_exclusive)
        .all()
    )

    for created_at, hours, items, carbon, event_id in rows:
        if not created_at:
            continue
        key = created_at.strftime("%Y-%m")
        buckets[key]["hours"] += float(hours or 0)
        buckets[key]["items"] += int(items or 0)
        buckets[key]["carbon"] += float(carbon or 0)

    # Also include verified request completion hours via reviews.
    review_rows = (
        db.session.query(Review.created_at, Review.duration_hours)
        .filter(Review.created_at >= start_month)
        .filter(Review.created_at < end_exclusive)
        .all()
    )

    for created_at, duration_hours in review_rows:
        if not created_at:
            continue
        key = created_at.strftime("%Y-%m")
        buckets[key]["hours"] += float(duration_hours or 0)

    # CO₂ saved from completed rides (verified by reviews).
    # Bucket by review month and count each request once per month.
    try:
        ride_review_rows = (
            db.session.query(Review.created_at, Review.request_id)
            .join(Request, Request.id == Review.request_id)
            .filter(func.lower(Request.category) == "ride")
            .filter(Review.created_at >= start_month)
            .filter(Review.created_at < end_exclusive)
            .all()
        )
    except Exception:
        ride_review_rows = []

    seen_ride_requests = set()
    for created_at, request_id in ride_review_rows:
        if not created_at or not request_id:
            continue
        key = created_at.strftime("%Y-%m")
        marker = (key, int(request_id))
        if marker in seen_ride_requests:
            continue
        seen_ride_requests.add(marker)
        buckets[key]["carbon"] += 2.5

    labels = []
    hours_data = []
    items_data = []
    carbon_data = []
    for i in range(months):
        ms = _add_months(start_month, i)
        key = ms.strftime("%Y-%m")
        labels.append(key)
        hours_data.append(round(float(buckets[key]["hours"]), 1))
        items_data.append(int(buckets[key]["items"]))
        carbon_data.append(round(float(buckets[key]["carbon"]), 1))

    return jsonify({"labels": labels, "hours": hours_data, "items": items_data, "carbon": carbon_data})


# Lightweight public summary for the home/hero cards
@app.route("/api/home/summary", methods=["GET"])
def api_home_summary():
    try:
        now = datetime.utcnow()
        week_ago = now - timedelta(days=7)

        open_requests = (
            Request.query.filter(Request.status == "open")
            .filter(Request.expires_at > now)
            .count()
        )

        helpers = User.query.filter(User.is_trusted_helper == True).count()  # noqa: E712

        matched_requests = (
            Request.query.filter(Request.status.in_(["claimed", "closed"])).count()
        )

        # Home stats:
        # - "Helped this week" reflects how many *people* the logged-in user helped (unique counterparts)
        #   based on verified completions (reviews).
        # - "Volunteer hours" shows the logged-in user's total hours (all-time), not community total.

        helped_recent = 0
        volunteer_hours_total = 0.0
        user = current_user()
        if user:
            # Find all requests completed (reviewed) in the last 7 days where this user was the credited helper.
            # Then count unique recipients (the other participant) across those requests.
            rows = (
                db.session.query(Request.is_offer, Request.user_id, Request.helper_id)
                .join(Review, Review.request_id == Request.id)
                .filter(Review.helper_id == user.id)
                .filter(Review.created_at >= week_ago)
                .all()
            )

            helped_user_ids = set()
            for is_offer, owner_id, helper_id in rows:
                if is_offer:
                    recipient_id = helper_id
                else:
                    recipient_id = owner_id

                if recipient_id and recipient_id != user.id:
                    helped_user_ids.add(int(recipient_id))

            helped_recent = len(helped_user_ids)

            # All-time hours credited to this user:
            # - Review.duration_hours for completed requests where this user was credited as helper
            # - ImpactLog.hours for completed events attributed to this user
            review_hours = (
                db.session.query(func.coalesce(func.sum(Review.duration_hours), 0.0))
                .filter(Review.helper_id == user.id)
                .scalar()
                or 0.0
            )
            event_hours = (
                db.session.query(func.coalesce(func.sum(ImpactLog.hours), 0.0))
                .filter(ImpactLog.helper_id == user.id)
                .scalar()
                or 0.0
            )
            volunteer_hours_total = float(review_hours) + float(event_hours)

        return jsonify(
            {
                "open_requests": int(open_requests),
                "helpers": int(helpers),
                "matched_requests": int(matched_requests),
                "neighbors_helped": int(helped_recent),
                "volunteer_hours": round(float(volunteer_hours_total), 1),
            }
        )
    except Exception as e:
        print("[API] home summary error:", e)
        return jsonify({"error": "summary_unavailable"}), 500

@app.route("/impact")
@login_required
def impact_dashboard():
    return render_template("impact.html")


@app.route("/impact")
@login_required
def impact():
    user = current_user()

    if user.role == "helper":
        # Helper sees ONLY their own impact
        logs = ImpactLog.query.filter_by(helper_id=user.id).all()
        title = "Your Volunteer Impact"
    else:
        # Normal users see COMMUNITY impact
        logs = ImpactLog.query.all()
        title = "Community Impact"

    labels = [l.created_at.strftime("%b %Y") for l in logs]
    hours = [l.hours for l in logs]
    items = [l.items for l in logs]
    carbon = [l.carbon for l in logs]

    return render_template(
        "impact.html",
        title=title,
        labels=labels,
        hours=hours,
        items=items,
        carbon=carbon
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


@app.route("/sos/trigger", methods=["POST"])
def trigger_sos():
    viewer = current_user()
    is_guest = viewer is None

    # Allow guests to trigger SOS - use emergency user as the DB owner,
    # but keep the browser "not logged in" by tracking the SOS id in session.
    user = viewer
    if not user:
        user = get_emergency_user()

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

    # Remember this SOS for emergency guests so they can view status and mark
    # it resolved without logging in.
    if is_guest:
        try:
            session["guest_sos_request_id"] = sos_req.id
        except Exception:
            pass

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


@app.route("/api/sos/<int:request_id>/resolve", methods=["POST"])
def api_sos_resolve(request_id: int):
    """Mark an SOS request as resolved.

    Allowed for:
    - Logged-in SOS owner
    - Emergency guest who created the SOS (tracked in session)
    """
    req_obj = Request.query.get_or_404(request_id)
    if (req_obj.category or "").lower() != "sos":
        return jsonify({"error": "Not an SOS request"}), 400

    viewer = current_user()
    guest_sos_request_id = session.get("guest_sos_request_id")
    is_guest_owner = guest_sos_request_id and int(guest_sos_request_id) == int(req_obj.id)

    if not ((viewer and viewer.id == req_obj.user_id) or is_guest_owner):
        return jsonify({"error": "Unauthorized"}), 403

    if req_obj.status in ("completed", "closed"):
        return jsonify({"ok": True, "already": True})

    req_obj.status = "completed"
    req_obj.completed_at = datetime.utcnow()
    db.session.commit()

    if is_guest_owner:
        try:
            session.pop("guest_sos_request_id", None)
            session.pop("active_sos_request_id", None)
        except Exception:
            pass

    return jsonify({"ok": True})


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
def api_sos_status(request_id):
    req_obj = Request.query.get_or_404(request_id)
    if (req_obj.category or "").lower() != "sos":
        return jsonify({"error": "Not an SOS request"}), 400

    viewer = current_user()
    guest_sos_request_id = session.get("guest_sos_request_id")
    is_guest_owner = (
        (not viewer)
        and guest_sos_request_id
        and int(guest_sos_request_id) == int(req_obj.id)
    )

    # Only allow status polling for:
    # - SOS owner (logged in)
    # - Trusted helpers (logged in)
    # - Emergency guest who created this SOS (session-bound)
    if not (
        (viewer and (viewer.id == req_obj.user_id or getattr(viewer, "is_trusted_helper", False)))
        or is_guest_owner
    ):
        return jsonify({"error": "Unauthorized"}), 403

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
            if (viewer and viewer.id == req_obj.user_id) or is_guest_owner:
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
        if (viewer and viewer.id == req_obj.user_id) or is_guest_owner:
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



@app.route("/emotional", endpoint="emotional")
def emotional_ping():
    user = current_user()
    if not user:
        # quick guest flow: use Emergency Guest
        user = get_emergency_user()
        login_user(user)
    # find a trusted helper (listener)
    listener = User.query.filter(User.is_trusted_helper == True, User.id != user.id).first()
    if not listener:
        # create or get a generic listener account
        listener = User.query.filter_by(email="listener@lifeline.local").first()
        if not listener:
            listener = User(email="listener@lifeline.local", name="Listener")
            listener.is_trusted_helper = True
            # set random password
            listener.password_hash = generate_password_hash(os.urandom(12).hex())
            db.session.add(listener)
            db.session.commit()

    conv = get_or_create_conversation(user.id, listener.id)
    return redirect(url_for('chat_with_user', other_user_id=listener.id))



@app.route("/api/conversations/<int:conv_id>/messages")
@login_required
def api_get_messages(conv_id):
    conv = Conversation.query.get_or_404(conv_id)
    user = current_user()
    if user.id not in conv.participants():
        return jsonify({"error": "Unauthorized"}), 403
    msgs = ChatMessage.query.filter_by(conversation_id=conv.id).order_by(ChatMessage.created_at.asc()).all()
    return jsonify({"messages": [serialize_message(m) for m in msgs]})


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
        # Also mark chat notifications related to this conversation as read.
        try:
            other_id = conv.user_a if conv.user_b == user.id else conv.user_b
            link = url_for("chat_with_user", other_user_id=other_id)
            Notification.query.filter_by(user_id=user.id, is_read=False, type="chat", link=link).update({"is_read": True})
            db.session.commit()
        except Exception:
            db.session.rollback()

        try:
            _emit_counts_update(user.id)
        except Exception:
            pass

        return jsonify({"marked": len(ids)})
    except Exception:
        db.session.rollback()
        return jsonify({"error": "failed"}), 500



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

@socketio.on("join")
def on_join(data):
    """Join rooms for real-time updates.

    Supports:
    - session-authenticated web users (primary)
    - optional JWT-authenticated clients (if they connect with auth headers)

    Expected payload: {conversation_id?: int}
    """
    data = data or {}

    user = current_user()
    user_id = user.id if user else None

    # Best-effort: allow JWT identity if present (does not require it).
    if user_id is None:
        try:
            from flask_jwt_extended import verify_jwt_in_request

            verify_jwt_in_request(optional=True)
            ident = get_jwt_identity()
            if ident is not None:
                user_id = int(ident)
        except Exception:
            pass

    if user_id is None:
        return

    sid_to_user[request.sid] = user_id
    online_users.add(user_id)

    # Personal room for notifications/pings.
    join_room(f"user_{user_id}")

    # Optional: conversation room for chat streaming.
    conv_id = data.get("conversation_id")
    if conv_id:
        try:
            conv = Conversation.query.get(int(conv_id))
            if conv and int(user_id) in conv.participants():
                join_room(f"chat_{conv.id}")
                print(f"[SOCKETIO] User {user_id} joined chat room chat_{conv.id}")
        except Exception as e:
            print(f"[SOCKETIO] join chat room failed: {e}")

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
    # data: {conversation_id, text, language (optional), file_data, file_name, file_size, is_image}
    conv_id = data.get("conversation_id")
    text = data.get("text", "")
    temp_id = data.get("temp_id")
    lang = data.get("language")
    file_data = data.get("file_data")
    file_name = data.get("file_name")
    file_size = data.get("file_size")
    is_image = data.get("is_image", False)
    
    user = current_user()
    
    # Log file attachment info
    if file_data:
        file_data_preview = file_data[:50] if file_data else None
        print(f"[SEND_MESSAGE] Has file attachment: {file_name} ({file_size}), is_image: {is_image}, data preview: {file_data_preview}...")
    
    print(f"[SEND_MESSAGE] From user {user.id if user else 'None'} to conv {conv_id}: {text[:50] if text else '[File attachment]'}")
    
    # Allow empty text if there's a file attachment
    if not user or not conv_id or (not text and not file_data):
        print(f"[SEND_MESSAGE] Rejected: missing user, conv_id, or content")
        return
        
    conv = Conversation.query.get(conv_id)
    if not conv or user.id not in conv.participants():
        print(f"[SEND_MESSAGE] Rejected: conv not found or user not participant")
        return

    msg = ChatMessage(conversation_id=conv.id, sender_id=user.id, text=text or "", language=lang)
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


    payload = serialize_message(msg)

    # Include file data in payload if present (not persisted to DB)
    if file_data:
        payload['file_data'] = file_data
        payload['file_name'] = file_name
        payload['file_size'] = file_size
        payload['is_image'] = is_image
        print(f"[SEND_MESSAGE] Including file in payload: {file_name}, is_image: {is_image}")

    # include the client's temporary id so client can replace optimistic UI
    if temp_id:
        payload['temp_id'] = temp_id

    room = f"chat_{conv.id}"
    print(f"[SEND_MESSAGE] Broadcasting to room {room}")
    emit("new_message", payload, room=room)

    # ---------- Determine the other participant ----------
    other_id = conv.user_a if conv.user_b == user.id else conv.user_b
    other_user = User.query.get(other_id) if other_id else None

    # ---------- Create bell notification for the receiver ----------
    try:
        if other_user and other_user.id != user.id:
            preview = (text or "").strip() or "[Attachment]"
            if len(preview) > 120:
                preview = preview[:120] + "…"
            push_notification(
                user_id=other_user.id,
                type="chat",
                message=f"💬 {user.name}: {preview}",
                link=url_for('chat_with_user', other_user_id=user.id),
            )

            # Real-time: refresh receiver badges + dropdown immediately
            _emit_counts_update(other_user.id)
            socketio.emit("notification_new", {"type": "chat"}, room=f"user_{other_user.id}")
    except Exception as e:
        print("[NOTIFICATION] Chat notify error:", e)

    # ---------- Optional FCM push to the receiver (best-effort) ----------
    try:
        if other_user and other_user.id != user.id and hasattr(other_user, "fcm_tokens"):
            if other_user.fcm_tokens.count() > 0:
                preview = (text or "").strip() or "[Attachment]"
                if len(preview) > 80:
                    preview = preview[:80] + "…"

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
    except Exception as e:
        print("[FCM] Error sending chat push:", e)

    # return payload as acknowledgement to sender (Socket.IO ack)
    return payload



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

    # If receiver read it, clear related chat notification and push updated counts.
    try:
        link = url_for("chat_with_user", other_user_id=msg.sender_id)
        Notification.query.filter_by(user_id=user.id, is_read=False, type="chat", link=link).update({"is_read": True})
        db.session.commit()
    except Exception:
        db.session.rollback()

    _emit_counts_update(user.id)


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
            user_id=(getattr(user, "id", 0) or 0),
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
        source = "unavailable"
        if isinstance(weather_data, dict):
            source = weather_data.get("__source") or "openweathermap"
        conditions = WeatherService.extract_conditions(weather_data)

        if not conditions:
            # Graceful fallback for UI when live weather fails
            fallback = {
                "condition": "Unknown",
                "description": "Weather unavailable",
                "temp": None,
                "humidity": None,
                "wind_speed": None,
            }
            return jsonify(
                {
                    "success": False,
                    "source": source,
                    "weather": fallback,
                    "location_used": {"lat": lat, "lng": lng},
                }
            ), 200
        
        return jsonify({
            "success": True,
            "source": source,
            "weather": conditions,
            "location_used": {"lat": lat, "lng": lng},
            "raw_data": weather_data,
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
def get_suggestion_insights():
    """
    Get insights about suggestions and user opportunities
    """
    try:
        user = current_user()

        if user is None:
            return jsonify({"success": False, "error": "Login required"}), 401
        
        if not user.lat or not user.lng:
            return jsonify({"success": False, "error": "User location required"}), 400
        
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


# ==================== SHOP SUGGESTION ROUTES ====================

@app.route("/api/shop-suggestions", methods=["POST"])
def get_shop_suggestions():
    """
    Analyze a help request and suggest nearby shops/services
    
    POST data:
    {
        "title": "Tire is flat",
        "description": "Need tire repair urgently",
        "category": "repair",
        "lat": 23.8103,
        "lng": 90.4125,
        "max_distance_km": 10,
        "max_results": 5
    }
    """
    try:
        data = request.get_json() or {}
        
        title = data.get("title", "")
        description = data.get("description", "")
        category = data.get("category", "")
        user_lat = data.get("lat")
        user_lng = data.get("lng")
        max_distance_km = float(data.get("max_distance_km", 10.0))
        max_results = int(data.get("max_results", 5))
        
        if not title or user_lat is None or user_lng is None:
            return jsonify({
                "success": False,
                "error": "title, lat, and lng are required"
            }), 400
        
        # Analyze request keywords
        analysis = ShopSuggestionAnalyzer.analyze_request(title, description, category)
        matched_types = analysis.get("matched_shop_types", [])
        
        if not matched_types:
            return jsonify({
                "success": True,
                "suggestions": [],
                "message": "No matching shops for this service type"
            }), 200
        
        # Get all shops from database
        all_shops = Shop.query.all()
        
        # Find nearby shops
        suggestions = ShopSuggestionAnalyzer.get_nearby_shops(
            shops=all_shops,
            user_lat=user_lat,
            user_lng=user_lng,
            matched_shop_types=matched_types,
            max_distance_km=max_distance_km,
            max_results=max_results
        )
        
        return jsonify({
            "success": True,
            "matched_types": matched_types,
            "suggestions": suggestions,
            "keywords_found": analysis.get("keywords", [])
        }), 200
        
    except Exception as e:
        print(f"[Shop Suggestions] Error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@app.route("/api/shops/<int:shop_id>", methods=["GET"])
def get_shop(shop_id):
    """Get details of a specific shop"""
    try:
        shop = Shop.query.get(shop_id)
        if not shop:
            return jsonify({"success": False, "error": "Shop not found"}), 404
        
        user_lat = request.args.get("lat")
        user_lng = request.args.get("lng")
        
        shop_data = shop.to_dict()
        if user_lat and user_lng:
            try:
                shop_data["distance_km"] = round(
                    haversine_distance_km(float(user_lat), float(user_lng), shop.lat, shop.lng), 2
                )
            except Exception:
                pass
        
        return jsonify({
            "success": True,
            "shop": shop_data
        }), 200
        
    except Exception as e:
        print(f"[Shop] Error getting shop: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/shop-request", methods=["POST"])
def create_shop_request():
    """
    User creates a request to a shop (makes contact with specific service provider)
    
    POST data:
    {
        "shop_id": 1,
        "title": "Need tire repair",
        "description": "My front left tire is flat and damaged",
        "request_id": 123,  // optional - link to original help request
        "user_lat": 23.8103,
        "user_lng": 90.4125,
        "user_area": "Mohammadpur"
    }
    """
    try:
        user = current_user()
        if not user:
            return jsonify({"success": False, "error": "Login required"}), 401
        
        data = request.get_json() or {}
        
        shop_id = data.get("shop_id")
        title = data.get("title", "").strip()
        description = data.get("description", "").strip()
        request_id = data.get("request_id")
        user_lat = data.get("user_lat")
        user_lng = data.get("user_lng")
        user_area = data.get("user_area", "").strip()
        
        if not shop_id or not title:
            return jsonify({
                "success": False,
                "error": "shop_id and title are required"
            }), 400
        
        # Verify shop exists
        shop = Shop.query.get(shop_id)
        if not shop:
            return jsonify({"success": False, "error": "Shop not found"}), 404
        
        # Verify request exists if provided
        if request_id:
            req = Request.query.get(request_id)
            if not req or req.user_id != user.id:
                return jsonify({"success": False, "error": "Invalid request"}), 400
        
        # ----- Fee calculation (AI-generated estimates) -----
        shop_service_fee = shop.service_fee or 100.0
        # Delivery-type shops have no labor cost (items are picked up / delivered)
        delivery_types = {"grocery", "pharmacy", "stationary", "restaurant"}
        if shop.shop_type in delivery_types:
            shop_labor_cost = 0.0
        else:
            shop_labor_cost = shop.labor_cost or 300.0
        # Estimate parts/materials cost based on shop type
        import random
        parts_estimates = {
            "tire_shop": random.randint(200, 800),
            "hospital": random.randint(500, 3000),
            "pharmacy": random.randint(100, 1500),
            "grocery": 0,
            "stationary": 0,
            "restaurant": random.randint(200, 1000),
            "salon": random.randint(100, 500),
        }
        parts_cost = parts_estimates.get(shop.shop_type, random.randint(100, 500))
        
        subtotal = shop_service_fee + shop_labor_cost + parts_cost
        commission_rate = 0.05  # LifeLine takes 5%
        lifeline_commission = round(subtotal * commission_rate, 2)
        total_cost = round(subtotal + lifeline_commission, 2)
        
        # Create shop request
        shop_req = ShopRequest(
            user_id=user.id,
            shop_id=shop_id,
            request_id=request_id,
            title=title,
            description=description,
            user_lat=user_lat,
            user_lng=user_lng,
            user_area=user_area or user.name,
            status="sent",
            service_fee=shop_service_fee,
            labor_cost=shop_labor_cost,
            parts_cost=parts_cost,
            lifeline_commission=lifeline_commission,
            total_cost=total_cost,
        )
        
        db.session.add(shop_req)
        db.session.commit()
        
        print(f"[Shop Request] User {user.id} requested service from shop {shop_id}: {title} | Total: ৳{total_cost}")
        
        return jsonify({
            "success": True,
            "message": f"Your request has been sent! A worker from {shop.name} will arrive soon.",
            "shop_request_id": shop_req.id,
            "shop_name": shop.name,
            "shop_phone": shop.phone,
            "estimated_wait": "15-30 minutes",
            "receipt": {
                "service_fee": shop_service_fee,
                "labor_cost": shop_labor_cost,
                "parts_cost": parts_cost,
                "subtotal": subtotal,
                "lifeline_commission": lifeline_commission,
                "commission_rate": f"{int(commission_rate*100)}%",
                "total_cost": total_cost,
            }
        }), 200
        
    except Exception as e:
        print(f"[Shop Request] Error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@app.route("/api/shop-requests", methods=["GET"])
@login_required
def get_user_shop_requests():
    """Get all shop requests made by the current user"""
    try:
        user = current_user()
        status = request.args.get("status")  # Filter by status: sent, accepted, completed
        
        query = ShopRequest.query.filter_by(user_id=user.id).order_by(ShopRequest.created_at.desc())
        
        if status:
            query = query.filter_by(status=status)
        
        shop_requests = query.all()
        
        return jsonify({
            "success": True,
            "shop_requests": [sr.to_dict() for sr in shop_requests]
        }), 200
        
    except Exception as e:
        print(f"[Shop Requests] Error: {e}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


# ==================== ADMIN SHOP MANAGEMENT ====================

@app.route("/admin/shops", endpoint="admin_shops")
@login_required
def admin_shops_page():
    """Admin page for managing shops"""
    try:
        user = current_user()
        if not getattr(user, "is_admin", False):
            flash("Admin access required", "error")
            return redirect(url_for("home"))
        
        return render_template("admin_shops.html")
    except Exception as e:
        print(f"[Admin Shops Page] Error: {e}")
        return redirect(url_for("home"))


@app.route("/api/admin/shops", methods=["GET"])
@login_required
def admin_get_shops():
    """Get all shops (admin only)"""
    try:
        user = current_user()
        if not getattr(user, "is_admin", False):
            return jsonify({"success": False, "error": "Admin access required"}), 403
        
        page = request.args.get("page", 1, type=int)
        shop_type = request.args.get("type")
        
        query = Shop.query
        if shop_type:
            query = query.filter_by(shop_type=shop_type)
        
        shops = query.order_by(Shop.name).paginate(page=page, per_page=50)
        
        return jsonify({
            "success": True,
            "shops": [s.to_dict() for s in shops.items],
            "total": shops.total,
            "pages": shops.pages,
            "current_page": page
        }), 200
        
    except Exception as e:
        print(f"[Admin Shops] Error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/admin/shops", methods=["POST"])
@login_required
def admin_add_shop():
    """Add a new shop (admin only)"""
    try:
        user = current_user()
        if not getattr(user, "is_admin", False):
            return jsonify({"success": False, "error": "Admin access required"}), 403
        
        data = request.get_json() or {}
        
        name = data.get("name", "").strip()
        shop_type = data.get("shop_type", "").strip()
        address = data.get("address", "").strip()
        phone = data.get("phone", "").strip()
        lat = data.get("lat")
        lng = data.get("lng")
        area = data.get("area", "").strip()
        landmark = data.get("landmark", "").strip()
        description = data.get("description", "").strip()
        
        if not name or not shop_type or not address or lat is None or lng is None:
            return jsonify({
                "success": False,
                "error": "name, shop_type, address, lat, lng are required"
            }), 400
        
        shop = Shop(
            name=name,
            shop_type=shop_type,
            address=address,
            phone=phone,
            lat=lat,
            lng=lng,
            area=area,
            landmark=landmark,
            description=description
        )
        
        db.session.add(shop)
        db.session.commit()
        
        return jsonify({
            "success": True,
            "shop": shop.to_dict(),
            "message": f"Shop '{name}' added successfully"
        }), 201
        
    except Exception as e:
        print(f"[Admin Add Shop] Error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/admin/shops/bulk", methods=["POST"])
@login_required
def admin_bulk_add_shops():
    """Bulk add shops from JSON array (admin only)"""
    try:
        user = current_user()
        if not getattr(user, "is_admin", False):
            return jsonify({"success": False, "error": "Admin access required"}), 403
        
        data = request.get_json() or {}
        shops_data = data.get("shops", [])
        clear_existing = data.get("clear_existing", False)
        
        if not isinstance(shops_data, list):
            return jsonify({"success": False, "error": "shops must be an array"}), 400
        
        if not shops_data:
            return jsonify({"success": False, "error": "shops array is empty"}), 400
        
        # Clear existing shops if requested
        if clear_existing:
            Shop.query.delete()
            db.session.commit()
            print("[Admin] Cleared all existing shops")
        
        added_count = 0
        errors = []
        
        for i, shop_data in enumerate(shops_data):
            try:
                name = shop_data.get("name", "").strip()
                shop_type = shop_data.get("shop_type", "").strip()
                address = shop_data.get("address", "").strip()
                phone = shop_data.get("phone", "").strip()
                lat = shop_data.get("lat")
                lng = shop_data.get("lng")
                area = shop_data.get("area", "").strip()
                landmark = shop_data.get("landmark", "").strip()
                description = shop_data.get("description", "").strip()
                
                if not name or not shop_type or not address or lat is None or lng is None:
                    errors.append(f"Row {i+1}: Missing required fields")
                    continue
                
                shop = Shop(
                    name=name,
                    shop_type=shop_type,
                    address=address,
                    phone=phone,
                    lat=lat,
                    lng=lng,
                    area=area,
                    landmark=landmark,
                    description=description
                )
                db.session.add(shop)
                added_count += 1
            except Exception as e:
                errors.append(f"Row {i+1}: {str(e)}")
        
        db.session.commit()
        
        result = {
            "success": True,
            "added": added_count,
            "total_processed": len(shops_data)
        }
        
        if errors:
            result["errors"] = errors
        
        return jsonify(result), 200
        
    except Exception as e:
        print(f"[Admin Bulk Add] Error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/admin/shops/<int:shop_id>", methods=["PUT"])
@login_required
def admin_update_shop(shop_id):
    """Update a shop (admin only)"""
    try:
        user = current_user()
        if not getattr(user, "is_admin", False):
            return jsonify({"success": False, "error": "Admin access required"}), 403
        
        shop = Shop.query.get(shop_id)
        if not shop:
            return jsonify({"success": False, "error": "Shop not found"}), 404
        
        data = request.get_json() or {}
        
        if "name" in data:
            shop.name = data["name"].strip()
        if "shop_type" in data:
            shop.shop_type = data["shop_type"].strip()
        if "address" in data:
            shop.address = data["address"].strip()
        if "phone" in data:
            shop.phone = data["phone"].strip()
        if "lat" in data:
            shop.lat = data["lat"]
        if "lng" in data:
            shop.lng = data["lng"]
        if "area" in data:
            shop.area = data["area"].strip()
        if "landmark" in data:
            shop.landmark = data["landmark"].strip()
        if "description" in data:
            shop.description = data["description"].strip()
        
        db.session.commit()
        
        return jsonify({
            "success": True,
            "shop": shop.to_dict(),
            "message": "Shop updated successfully"
        }), 200
        
    except Exception as e:
        print(f"[Admin Update Shop] Error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/admin/shops/<int:shop_id>", methods=["DELETE"])
@login_required
def admin_delete_shop(shop_id):
    """Delete a shop (admin only)"""
    try:
        user = current_user()
        if not getattr(user, "is_admin", False):
            return jsonify({"success": False, "error": "Admin access required"}), 403
        
        shop = Shop.query.get(shop_id)
        if not shop:
            return jsonify({"success": False, "error": "Shop not found"}), 404
        
        shop_name = shop.name
        db.session.delete(shop)
        db.session.commit()
        
        return jsonify({
            "success": True,
            "message": f"Shop '{shop_name}' deleted successfully"
        }), 200
        
    except Exception as e:
        print(f"[Admin Delete Shop] Error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/admin/shops/expand-to-cities", methods=["POST"])
@login_required
def admin_expand_shops():
    """Expand shop database with shops from multiple Bangladesh cities (admin only)"""
    try:
        user = current_user()
        if not getattr(user, "is_admin", False):
            return jsonify({"success": False, "error": "Admin access required"}), 403
        
        # Define expanded shop data for multiple cities
        EXPANDED_SHOPS = [
            # Dhaka (keep existing)
            {"name": "Tire Plus Bangladesh", "shop_type": "tire_shop", "address": "123, Mohammadpur, Dhaka", "phone": "01712345678", "lat": 23.8103, "lng": 90.4125, "area": "Mohammadpur", "landmark": "Near BRACU"},
            {"name": "Wheel & Tire Hub", "shop_type": "tire_shop", "address": "456, Badda, Dhaka", "phone": "01898765432", "lat": 23.8213, "lng": 90.4469, "area": "Badda", "landmark": "Iqbal Road"},
            
            # Chittagong Tire Shops
            {"name": "Chittagong Tire House", "shop_type": "tire_shop", "address": "Ahmed Plaza, Agrabad, Chittagong", "phone": "01812345700", "lat": 22.3475, "lng": 91.8123, "area": "Agrabad", "landmark": "GEC"},
            {"name": "CTG Auto Repair", "shop_type": "tire_shop", "address": "Nasirabad, Chittagong", "phone": "01912345701", "lat": 22.3388, "lng": 91.8361, "area": "Nasirabad", "landmark": "Near Stadium"},
            {"name": "Challenger Tire Center", "shop_type": "tire_shop", "address": "Halishahar, Chittagong", "phone": "01812345750", "lat": 22.3641, "lng": 91.8247, "area": "Halishahar", "landmark": "Port Area"},
            
            # Sylhet Tire Shops  
            {"name": "Sylhet Tire Workshop", "shop_type": "tire_shop", "address": "Dakshin Surma, Sylhet", "phone": "01812345751", "lat": 24.8971, "lng": 91.8732, "area": "Sylhet", "landmark": "City Center"},
            
            # Dhaka Hospitals
            {"name": "United Hospital Dhaka", "shop_type": "hospital", "address": "1 Gulshan Avenue, Dhaka", "phone": "01712345681", "lat": 23.8061, "lng": 90.4167, "area": "Gulshan", "landmark": "Gulshan Avenue"},
            {"name": "Popular Diagnostic Center", "shop_type": "hospital", "address": "Dhanmondi, Dhaka", "phone": "01912345682", "lat": 23.7414, "lng": 90.3694, "area": "Dhanmondi", "landmark": "Lake Dhanmondi"},
            
            # Chittagong Hospitals
            {"name": "Chittagong Medical College Hospital", "shop_type": "hospital", "address": "Agrabad, Chittagong", "phone": "01812345702", "lat": 22.3475, "lng": 91.8123, "area": "Agrabad", "landmark": "GEC"},
            {"name": "Chattogram Shishu Hospital", "shop_type": "hospital", "address": "Kawran Bazar, Chittagong", "phone": "01912345703", "lat": 22.3418, "lng": 91.8342, "area": "Kawran Bazar", "landmark": "Karnafuli"},
            
            # Sylhet Hospitals
            {"name": "Sylhet Sadar Hospital", "shop_type": "hospital", "address": "Sylhet City", "phone": "01812345704", "lat": 24.8971, "lng": 91.8732, "area": "Sylhet", "landmark": "City Center"},
            
            # Dhaka Pharmacies
            {"name": "SafeWay Pharmacy", "shop_type": "pharmacy", "address": "Badda, Dhaka", "phone": "01512345685", "lat": 23.8213, "lng": 90.4469, "area": "Badda", "landmark": "Iqbal Road"},
            {"name": "Care Pharmacy", "shop_type": "pharmacy", "address": "Banani, Dhaka", "phone": "01712345686", "lat": 23.8285, "lng": 90.4151, "area": "Banani", "landmark": "Banani Lake"},
            
            # Chittagong Pharmacies
            {"name": "Chittagong Care Pharmacy", "shop_type": "pharmacy", "address": "Agrabad, Chittagong", "phone": "01812345705", "lat": 22.3475, "lng": 91.8123, "area": "Agrabad", "landmark": "GEC"},
            {"name": "CTG Drug Store", "shop_type": "pharmacy", "address": "Halishahar, Chittagong", "phone": "01912345706", "lat": 22.3641, "lng": 91.8247, "area": "Halishahar", "landmark": "Port Area"},
            
            # Sylhet Pharmacies
            {"name": "Sylhet Medicine House", "shop_type": "pharmacy", "address": "Sylhet City", "phone": "01812345707", "lat": 24.8971, "lng": 91.8732, "area": "Sylhet", "landmark": "City Center"},
            
            # Dhaka Groceries
            {"name": "Fresh Bazaar", "shop_type": "grocery", "address": "Badda, Dhaka", "phone": "01512345690", "lat": 23.8213, "lng": 90.4469, "area": "Badda", "landmark": "Iqbal Road"},
            
            # Chittagong Groceries
            {"name": "CTG Fresh Market", "shop_type": "grocery", "address": "Agrabad, Chittagong", "phone": "01812345708", "lat": 22.3475, "lng": 91.8123, "area": "Agrabad", "landmark": "GEC"},
            
            # Sylhet Groceries
            {"name": "Sylhet Bazaar", "shop_type": "grocery", "address": "Sylhet City", "phone": "01812345710", "lat": 24.8971, "lng": 91.8732, "area": "Sylhet", "landmark": "City Center"},
        ]
        
        # Clear existing and add new
        Shop.query.delete()
        
        for shop_data in EXPANDED_SHOPS:
            shop = Shop(**shop_data)
            db.session.add(shop)
        
        db.session.commit()
        
        return jsonify({
            "success": True,
            "message": f"Expanded shop database with {len(EXPANDED_SHOPS)} shops across Bangladesh",
            "shops_added": len(EXPANDED_SHOPS)
        }), 200
        
    except Exception as e:
        print(f"[Admin Expand Shops] Error: {e}")
        db.session.rollback()
        return jsonify({"success": False, "error": str(e)}), 500


# ==================== END SHOP SUGGESTION ROUTES ====================



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

def _run_startup_migrations_and_bootstrap_admin():
    """Best-effort startup tasks.

    Keep this lightweight and resilient: if a migration fails, the app should
    still boot.
    """
    try:
        from sqlalchemy import inspect
    except Exception as e:
        print(f"[startup] Could not import SQLAlchemy inspect: {e}")
        return

    try:
        inspector = inspect(db.engine)
        table_names = set(inspector.get_table_names() or [])
    except Exception as e:
        print(f"[startup] Could not inspect DB schema: {e}")
        return

    # 1) Migrate: Add image_url column to resource_wanted_items if it doesn't exist
    try:
        if "resource_wanted_items" in table_names:
            columns = [col["name"] for col in inspector.get_columns("resource_wanted_items")]
            if "image_url" not in columns:
                with db.engine.connect() as conn:
                    conn.execute(db.text("ALTER TABLE resource_wanted_items ADD COLUMN image_url VARCHAR(300)"))
                    conn.commit()
                print("✓ Added image_url column to resource_wanted_items")
    except Exception as e:
        print(f"Migration note: {e}")

    # 2) Migrate: Ensure premium/admin columns exist
    try:
        if "user" in table_names:
            cols = [c["name"] for c in inspector.get_columns("user")]
            stmts = []
            if "is_premium" not in cols:
                stmts.append("ALTER TABLE user ADD COLUMN is_premium BOOLEAN DEFAULT 0")
            if "premium_expiry" not in cols:
                stmts.append("ALTER TABLE user ADD COLUMN premium_expiry DATETIME")
            if "is_admin" not in cols:
                stmts.append("ALTER TABLE user ADD COLUMN is_admin BOOLEAN DEFAULT 0")

            if stmts:
                with db.engine.connect() as conn:
                    for stmt in stmts:
                        conn.execute(db.text(stmt))
                    conn.commit()
                print("✓ Added premium/admin columns")
    except Exception as e:
        print(f"Migration error: {e}")

    # 2b) Migrate: Ensure offers columns exist (for pending/accepted flow + dashboard)
    try:
        if "offers" in table_names:
            cols = [c["name"] for c in inspector.get_columns("offers")]

            stmts = []
            if "status" not in cols:
                stmts.append("ALTER TABLE offers ADD COLUMN status VARCHAR(20) DEFAULT 'pending'")
            if "user_id" not in cols:
                # Compatibility column used by some parts of the app.
                # Keep it nullable; we'll backfill from helper_id when possible.
                stmts.append("ALTER TABLE offers ADD COLUMN user_id INTEGER")
            if "title" not in cols:
                stmts.append("ALTER TABLE offers ADD COLUMN title VARCHAR(255)")
            if "body" not in cols:
                stmts.append("ALTER TABLE offers ADD COLUMN body TEXT")

            if stmts:
                with db.engine.connect() as conn:
                    for stmt in stmts:
                        conn.execute(db.text(stmt))
                    # Best-effort backfill: if helper_id exists, mirror it to user_id.
                    try:
                        if "user_id" not in cols:
                            conn.execute(db.text("UPDATE offers SET user_id = helper_id WHERE user_id IS NULL"))
                    except Exception:
                        pass
                    conn.commit()
                print("✓ Added missing columns to offers")
    except Exception as e:
        print(f"Migration note (offers): {e}")

    # 3) Seed shops/service providers (nationwide Bangladesh)
    try:
        from shop_data import generate_all_shops, EXPECTED_SHOP_COUNT
        existing_shops = Shop.query.count()
        if existing_shops < EXPECTED_SHOP_COUNT:
            # Clear old shops if upgrading from partial seed
            if existing_shops > 0:
                Shop.query.delete()
                db.session.commit()
                print(f"  Cleared {existing_shops} old shops for nationwide re-seed")
            # Legacy inline list (kept for reference, overridden below)
            _LEGACY_SHOPS = [
                # ===== TIRE SHOPS (spread across Dhaka) =====
                {"name": "Tire Plus Bangladesh", "shop_type": "tire_shop", "address": "123, Mohammadpur, Dhaka", "phone": "01712345678", "lat": 23.7660, "lng": 90.3590, "area": "Mohammadpur", "landmark": "Mohammadpur Bus Stand", "rating": 4.2, "service_fee": 150, "labor_cost": 400},
                {"name": "Wheel & Tire Hub", "shop_type": "tire_shop", "address": "456, Badda, Dhaka", "phone": "01898765432", "lat": 23.7800, "lng": 90.4260, "area": "Badda", "landmark": "Badda Link Road", "rating": 3.8, "service_fee": 120, "labor_cost": 350},
                {"name": "Auto Care Center", "shop_type": "tire_shop", "address": "789, Banani, Dhaka", "phone": "01512345678", "lat": 23.7940, "lng": 90.4030, "area": "Banani", "landmark": "Banani 11", "rating": 4.5, "service_fee": 200, "labor_cost": 500},
                {"name": "Dhaka Tire Service", "shop_type": "tire_shop", "address": "321, Dhanmondi, Dhaka", "phone": "01912345678", "lat": 23.7470, "lng": 90.3750, "area": "Dhanmondi", "landmark": "Dhanmondi 27", "rating": 4.0, "service_fee": 130, "labor_cost": 380},
                {"name": "Quick Tire Repair", "shop_type": "tire_shop", "address": "654, Gulshan, Dhaka", "phone": "01712345679", "lat": 23.7820, "lng": 90.4150, "area": "Gulshan", "landmark": "Gulshan 1 Circle", "rating": 4.7, "service_fee": 250, "labor_cost": 600},
                {"name": "Central Tire Works", "shop_type": "tire_shop", "address": "Mirpur Road, Dhaka", "phone": "01612345670", "lat": 23.7580, "lng": 90.3680, "area": "Mirpur Road", "landmark": "Science Lab", "rating": 3.9, "service_fee": 100, "labor_cost": 320},
                {"name": "Iqbal Road Auto Tire", "shop_type": "tire_shop", "address": "Iqbal Road, Mohammadpur", "phone": "01612345671", "lat": 23.7620, "lng": 90.3650, "area": "Mohammadpur", "landmark": "Iqbal Road", "rating": 3.6, "service_fee": 100, "labor_cost": 300},
                {"name": "Uttara Tire Center", "shop_type": "tire_shop", "address": "Sector 10, Uttara", "phone": "01612345672", "lat": 23.8750, "lng": 90.3950, "area": "Uttara", "landmark": "Uttara Sector 10", "rating": 4.3, "service_fee": 180, "labor_cost": 450},
                
                # ===== HOSPITALS =====
                {"name": "Dhaka Medical College Hospital", "shop_type": "hospital", "address": "Secretariat, Dhaka", "phone": "01912345680", "lat": 23.7260, "lng": 90.3970, "area": "Secretariat", "landmark": "Near Government offices", "rating": 4.1, "service_fee": 500, "labor_cost": 1500},
                {"name": "United Hospital", "shop_type": "hospital", "address": "Gulshan 2, Dhaka", "phone": "01712345681", "lat": 23.7930, "lng": 90.4150, "area": "Gulshan", "landmark": "Gulshan 2 Circle", "rating": 4.8, "service_fee": 800, "labor_cost": 3000},
                {"name": "Popular Diagnostic Center", "shop_type": "hospital", "address": "Dhanmondi 2, Dhaka", "phone": "01912345682", "lat": 23.7390, "lng": 90.3810, "area": "Dhanmondi", "landmark": "Green Road", "rating": 4.4, "service_fee": 600, "labor_cost": 2000},
                {"name": "Square Hospital", "shop_type": "hospital", "address": "18/F Bir Uttam Qazi Nuruzzaman Sarak, Dhaka", "phone": "01712345683", "lat": 23.7530, "lng": 90.3900, "area": "Panthapath", "landmark": "Panthapath Signal", "rating": 4.7, "service_fee": 750, "labor_cost": 2500},
                {"name": "Bangabandhu Sheikh Mujib Medical University", "shop_type": "hospital", "address": "Shahbag, Dhaka", "phone": "01912345684", "lat": 23.7380, "lng": 90.3960, "area": "Shahbag", "landmark": "Shahbag Circle", "rating": 4.3, "service_fee": 500, "labor_cost": 2000},
                {"name": "Ibn Sina Hospital", "shop_type": "hospital", "address": "House 48, Road 9/A, Dhanmondi", "phone": "01612345680", "lat": 23.7450, "lng": 90.3740, "area": "Dhanmondi", "landmark": "Dhanmondi 9/A", "rating": 4.5, "service_fee": 600, "labor_cost": 2200},
                {"name": "Labaid Hospital", "shop_type": "hospital", "address": "House 6, Road 4, Dhanmondi", "phone": "01612345681", "lat": 23.7510, "lng": 90.3750, "area": "Dhanmondi", "landmark": "Dhanmondi 4", "rating": 4.6, "service_fee": 650, "labor_cost": 2400},
                
                # ===== PHARMACIES =====
                {"name": "SafeWay Pharmacy", "shop_type": "pharmacy", "address": "Badda, Dhaka", "phone": "01512345685", "lat": 23.7810, "lng": 90.4260, "area": "Badda", "landmark": "Badda Link Road", "rating": 4.0, "service_fee": 50, "labor_cost": 0},
                {"name": "Care Pharmacy", "shop_type": "pharmacy", "address": "Banani 11, Dhaka", "phone": "01712345686", "lat": 23.7935, "lng": 90.4020, "area": "Banani", "landmark": "Banani 11", "rating": 4.3, "service_fee": 80, "labor_cost": 0},
                {"name": "MediCare Pharmacy", "shop_type": "pharmacy", "address": "Gulshan, Dhaka", "phone": "01912345687", "lat": 23.7830, "lng": 90.4160, "area": "Gulshan", "landmark": "Gulshan 1", "rating": 4.6, "service_fee": 100, "labor_cost": 0},
                {"name": "Health Plus Pharmacy", "shop_type": "pharmacy", "address": "Dhanmondi, Dhaka", "phone": "01812345688", "lat": 23.7470, "lng": 90.3740, "area": "Dhanmondi", "landmark": "Dhanmondi 27", "rating": 4.1, "service_fee": 60, "labor_cost": 0},
                {"name": "Online Pharmacy BD", "shop_type": "pharmacy", "address": "Motijheel, Dhaka", "phone": "01712345689", "lat": 23.7330, "lng": 90.4170, "area": "Motijheel", "landmark": "Motijheel Circle", "rating": 3.9, "service_fee": 70, "labor_cost": 0},
                {"name": "Lazz Pharma", "shop_type": "pharmacy", "address": "Mirpur Road, Dhaka", "phone": "01612345685", "lat": 23.7590, "lng": 90.3700, "area": "Mirpur Road", "landmark": "Science Lab", "rating": 4.4, "service_fee": 80, "labor_cost": 0},
                {"name": "Mohammadpur Pharmacy", "shop_type": "pharmacy", "address": "Mohammadpur, Dhaka", "phone": "01612345686", "lat": 23.7650, "lng": 90.3600, "area": "Mohammadpur", "landmark": "Town Hall", "rating": 3.7, "service_fee": 50, "labor_cost": 0},
                
                # ===== GROCERY SHOPS =====
                {"name": "Fresh Bazaar", "shop_type": "grocery", "address": "Badda, Dhaka", "phone": "01512345690", "lat": 23.7805, "lng": 90.4250, "area": "Badda", "landmark": "Badda Link Road", "rating": 4.2, "service_fee": 60, "labor_cost": 0},
                {"name": "Daily Needs Store", "shop_type": "grocery", "address": "Banani, Dhaka", "phone": "01712345691", "lat": 23.7950, "lng": 90.4040, "area": "Banani", "landmark": "Banani 11", "rating": 4.0, "service_fee": 80, "labor_cost": 0},
                {"name": "Shyam Bazaar", "shop_type": "grocery", "address": "Gulshan, Dhaka", "phone": "01912345692", "lat": 23.7825, "lng": 90.4155, "area": "Gulshan", "landmark": "Gulshan 1", "rating": 3.8, "service_fee": 70, "labor_cost": 0},
                {"name": "Dhaka Provisions", "shop_type": "grocery", "address": "Dhanmondi, Dhaka", "phone": "01812345693", "lat": 23.7465, "lng": 90.3760, "area": "Dhanmondi", "landmark": "Dhanmondi 27", "rating": 4.1, "service_fee": 50, "labor_cost": 0},
                {"name": "Local Mart", "shop_type": "grocery", "address": "Panthapath, Dhaka", "phone": "01712345694", "lat": 23.7530, "lng": 90.3870, "area": "Panthapath", "landmark": "Panthapath Signal", "rating": 3.9, "service_fee": 60, "labor_cost": 0},
                {"name": "Swapno Superstore", "shop_type": "grocery", "address": "Mohammadpur, Dhaka", "phone": "01612345690", "lat": 23.7640, "lng": 90.3620, "area": "Mohammadpur", "landmark": "Mohammadpur Bus Stand", "rating": 4.5, "service_fee": 100, "labor_cost": 0},
                {"name": "Agora Supermarket", "shop_type": "grocery", "address": "Mirpur Road, Dhaka", "phone": "01612345691", "lat": 23.7575, "lng": 90.3690, "area": "Mirpur Road", "landmark": "New Market", "rating": 4.4, "service_fee": 90, "labor_cost": 0},
                
                # ===== STATIONARY SHOPS =====
                {"name": "Paper Plus Stationary", "shop_type": "stationary", "address": "Badda, Dhaka", "phone": "01512345695", "lat": 23.7815, "lng": 90.4240, "area": "Badda", "landmark": "Badda Link Road", "rating": 3.9, "service_fee": 40, "labor_cost": 0},
                {"name": "Office Supplies Hub", "shop_type": "stationary", "address": "Banani, Dhaka", "phone": "01712345696", "lat": 23.7945, "lng": 90.4035, "area": "Banani", "landmark": "Banani 11", "rating": 4.2, "service_fee": 60, "labor_cost": 0},
                {"name": "School & Office Stationary", "shop_type": "stationary", "address": "Gulshan, Dhaka", "phone": "01912345697", "lat": 23.7835, "lng": 90.4160, "area": "Gulshan", "landmark": "Gulshan 1", "rating": 4.0, "service_fee": 50, "labor_cost": 0},
                {"name": "Dhaka Stationery Co.", "shop_type": "stationary", "address": "Dhanmondi, Dhaka", "phone": "01812345698", "lat": 23.7455, "lng": 90.3755, "area": "Dhanmondi", "landmark": "Dhanmondi 27", "rating": 4.3, "service_fee": 50, "labor_cost": 0},
                {"name": "Paper World", "shop_type": "stationary", "address": "Panthapath, Dhaka", "phone": "01712345699", "lat": 23.7540, "lng": 90.3880, "area": "Panthapath", "landmark": "Panthapath Signal", "rating": 3.7, "service_fee": 40, "labor_cost": 0},
                {"name": "Nilkhet Book Market", "shop_type": "stationary", "address": "Nilkhet, Dhaka", "phone": "01612345695", "lat": 23.7370, "lng": 90.3870, "area": "Nilkhet", "landmark": "Nilkhet Gate", "rating": 4.5, "service_fee": 30, "labor_cost": 0},
                
                # ===== RESTAURANTS =====
                {"name": "Spice Garden Restaurant", "shop_type": "restaurant", "address": "Badda, Dhaka", "phone": "01512345700", "lat": 23.7800, "lng": 90.4255, "area": "Badda", "landmark": "Badda Link Road", "rating": 4.1, "service_fee": 80, "labor_cost": 0},
                {"name": "Dhaka Delight Cafe", "shop_type": "restaurant", "address": "Banani, Dhaka", "phone": "01712345701", "lat": 23.7940, "lng": 90.4045, "area": "Banani", "landmark": "Banani 11", "rating": 4.5, "service_fee": 120, "labor_cost": 0},
                {"name": "Taste of Bangladesh", "shop_type": "restaurant", "address": "Gulshan, Dhaka", "phone": "01912345702", "lat": 23.7840, "lng": 90.4170, "area": "Gulshan", "landmark": "Gulshan 1", "rating": 4.7, "service_fee": 150, "labor_cost": 0},
                {"name": "City Eats Diner", "shop_type": "restaurant", "address": "Dhanmondi, Dhaka", "phone": "01812345703", "lat": 23.7460, "lng": 90.3750, "area": "Dhanmondi", "landmark": "Dhanmondi 27", "rating": 3.8, "service_fee": 80, "labor_cost": 0},
                {"name": "Star Kabab", "shop_type": "restaurant", "address": "Mohammadpur, Dhaka", "phone": "01612345700", "lat": 23.7650, "lng": 90.3610, "area": "Mohammadpur", "landmark": "Mohammadpur Bus Stand", "rating": 4.3, "service_fee": 70, "labor_cost": 0},
                {"name": "Kacchi Bhai", "shop_type": "restaurant", "address": "Mirpur Road, Dhaka", "phone": "01612345701", "lat": 23.7585, "lng": 90.3710, "area": "Mirpur Road", "landmark": "New Market", "rating": 4.6, "service_fee": 100, "labor_cost": 0},
                
                # ===== SALON =====
                {"name": "Beauty & Style Salon", "shop_type": "salon", "address": "Badda, Dhaka", "phone": "01512345704", "lat": 23.7810, "lng": 90.4250, "area": "Badda", "landmark": "Badda Link Road", "rating": 4.0, "service_fee": 100, "labor_cost": 200},
                {"name": "Glamour Salon", "shop_type": "salon", "address": "Banani, Dhaka", "phone": "01712345705", "lat": 23.7930, "lng": 90.4030, "area": "Banani", "landmark": "Banani 11", "rating": 4.6, "service_fee": 200, "labor_cost": 500},
                {"name": "Professional Hair Studio", "shop_type": "salon", "address": "Gulshan, Dhaka", "phone": "01912345706", "lat": 23.7825, "lng": 90.4160, "area": "Gulshan", "landmark": "Gulshan 1", "rating": 4.8, "service_fee": 250, "labor_cost": 600},
                {"name": "Persona Salon", "shop_type": "salon", "address": "Dhanmondi, Dhaka", "phone": "01612345704", "lat": 23.7460, "lng": 90.3740, "area": "Dhanmondi", "landmark": "Dhanmondi 27", "rating": 4.2, "service_fee": 150, "labor_cost": 350},
                {"name": "Mens Zone Salon", "shop_type": "salon", "address": "Mohammadpur, Dhaka", "phone": "01612345705", "lat": 23.7660, "lng": 90.3600, "area": "Mohammadpur", "landmark": "Town Hall", "rating": 3.9, "service_fee": 100, "labor_cost": 250},
            ]

            # Use comprehensive nationwide data (all 64 districts + Dhaka neighbourhoods)
            SAMPLE_SHOPS = generate_all_shops()

            for shop_data in SAMPLE_SHOPS:
                db.session.add(Shop(**shop_data))
            
            db.session.commit()
            print(f"✓ Seeded {len(SAMPLE_SHOPS)} shops across Bangladesh")
        else:
            print(f"✓ Shops already exist in database ({existing_shops} shops)")
    except Exception as e:
        print(f"[startup] Shop seeding error: {e}")
        try:
            db.session.rollback()
        except Exception:
            pass

    # 4) Bootstrap default admin
    try:
        admin = User.query.filter_by(email="admin@lifeline.com").first()
        default_pw = os.getenv("DEFAULT_ADMIN_PASSWORD", "admin123")

        if not admin:
            admin = User(email="admin@lifeline.com", name="Super Admin")
            if hasattr(admin, "is_admin"):
                admin.is_admin = True
            if hasattr(admin, "is_trusted_helper"):
                admin.is_trusted_helper = True
            admin.set_password(default_pw)
            db.session.add(admin)
            db.session.commit()
            print("✓ Created default admin: admin@lifeline.com")
        else:
            changed = False
            if hasattr(admin, "is_admin") and not getattr(admin, "is_admin"):
                admin.is_admin = True
                changed = True
            if hasattr(admin, "is_trusted_helper") and not getattr(admin, "is_trusted_helper"):
                admin.is_trusted_helper = True
                changed = True
            if changed:
                db.session.commit()
                print("✓ Updated admin flags for admin@lifeline.com")
    except Exception as e:
        try:
            db.session.rollback()
        except Exception:
            pass
        print(f"[startup] Admin bootstrap error: {e}")


def _seed_catering_services():
    """Seed sample catering services if none exist."""
    try:
        if CateringService.query.count() > 0:
            return
        caterers = [
            CateringService(name="Dhaka Catering House", description="Traditional Bangladeshi feast catering with authentic flavors. Specializing in biryani, kacchi, and polao for large events.",
                            cuisine_type="Bangladeshi", price_per_head=350, min_order=30, phone="+880-1711-222333",
                            rating=4.7, total_reviews=128, lat=23.8103, lng=90.4125, area="Panthapath, Dhaka"),
            CateringService(name="Royal Feast Catering", description="Premium event catering for weddings, corporate events, and community gatherings. Multi-cuisine menu available.",
                            cuisine_type="Multi-cuisine", price_per_head=500, min_order=50, phone="+880-1812-334455",
                            rating=4.8, total_reviews=95, lat=23.7946, lng=90.4043, area="Dhanmondi, Dhaka"),
            CateringService(name="Green Kitchen Catering", description="Healthy and organic catering services. Vegan & vegetarian options available for conscious events.",
                            cuisine_type="Organic/Vegan", price_per_head=400, min_order=20, phone="+880-1912-445566",
                            rating=4.5, total_reviews=67, lat=23.7808, lng=90.4126, area="Banani, Dhaka"),
            CateringService(name="Spice Garden Events", description="South Asian fusion catering. Indian, Thai, and Bangladeshi dishes prepared by experienced chefs.",
                            cuisine_type="Asian Fusion", price_per_head=450, min_order=25, phone="+880-1611-556677",
                            rating=4.6, total_reviews=84, lat=23.8726, lng=90.3989, area="Uttara, Dhaka"),
            CateringService(name="Mom's Kitchen Catering", description="Home-style Bangladeshi cooking at scale. Perfect for community events and charity programs.",
                            cuisine_type="Home-style", price_per_head=250, min_order=20, phone="+880-1511-667788",
                            rating=4.9, total_reviews=156, lat=23.7465, lng=90.3763, area="Mirpur, Dhaka"),
            CateringService(name="Elite Event Catering", description="Luxury catering with live cooking stations, dessert bars, and premium presentation for upscale events.",
                            cuisine_type="Premium", price_per_head=800, min_order=40, phone="+880-1711-778899",
                            rating=4.4, total_reviews=52, lat=23.7937, lng=90.4066, area="Gulshan, Dhaka"),
            CateringService(name="Street Treats Catering", description="Popular street food catering — fuchka, chotpoti, jhalmuri, and more. Great for casual community events.",
                            cuisine_type="Street Food", price_per_head=150, min_order=30, phone="+880-1811-889900",
                            rating=4.6, total_reviews=112, lat=23.7329, lng=90.3926, area="Old Dhaka"),
            CateringService(name="Bhojon Express", description="Fast and affordable bulk catering for charity events, cleanups, and donation drives. Quick setup guaranteed.",
                            cuisine_type="Budget-friendly", price_per_head=200, min_order=50, phone="+880-1911-990011",
                            rating=4.3, total_reviews=89, lat=23.8223, lng=90.4198, area="Badda, Dhaka"),
        ]
        db.session.add_all(caterers)
        db.session.commit()
        print(f"✓ Seeded {len(caterers)} catering services")
    except Exception as e:
        print(f"[startup] Catering seed error: {e}")
        try:
            db.session.rollback()
        except Exception:
            pass


if __name__ == "__main__":
    debug_mode = os.getenv("FLASK_DEBUG", "0") == "1"
    with app.app_context():
        db.create_all()  # create tables if not exist
        _run_startup_migrations_and_bootstrap_admin()
        _seed_catering_services()

    # Use reloader=False with Socket.IO to avoid double-start issues.
    port = int(os.getenv("PORT", "5000"))
    socketio.run(app, host="0.0.0.0", port=port, debug=debug_mode, use_reloader=False)