from datetime import datetime, timedelta
import os
import math
import time
import random
import string


from reputation_service import analyze_review_quality, calculate_reputation_points
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

# -------- Firebase Admin (for Google sign-in + FCM) --------
FIREBASE_CRED_PATH = os.getenv(
    "GOOGLE_APPLICATION_CREDENTIALS",
    "firebase-service-account.json",  # default path
)

if not firebase_admin._apps:
    try:
        cred = credentials.Certificate(FIREBASE_CRED_PATH)
        firebase_admin.initialize_app(cred)
        print("[Firebase] Initialized with", FIREBASE_CRED_PATH)
    except Exception as e:
        # IMPORTANT: don't crash the app if Firebase isn't configured locally
        print("[Firebase] WARNING: could not initialize Firebase Admin:", e)
        print("[Firebase] Google login / push notifications will be disabled on this machine.")
# ------------- FCM PUSH HELPERS -------------
def send_push_notification(token, title, body, data=None):
    """
    Low-level helper: send a push to a single FCM token.
    Safe to call even if Firebase Admin isn't configured.
    """
    if not token:
        return

    # If Firebase Admin didn't initialize (e.g. teammate doesn't have JSON),
    # just skip sending silently.
    if not firebase_admin._apps:
        print("[FCM] Skipping push; Firebase Admin not initialized on this machine.")
        return

    try:
        msg = messaging.Message(
            token=token,
            notification=messaging.Notification(
                title=title,
                body=body,
            ),
            data={k: str(v) for k, v in (data or {}).items()},
        )
        resp = messaging.send(msg)
        print("[FCM] Sent push, id:", resp)
    except Exception as e:
        print("[FCM] Error sending push:", e)


def send_push_to_user(user, title, body, data=None):
    """
    Convenience wrapper; reads user.fcm_token.
    """
    if not user or not getattr(user, "fcm_token", None):
        return
    send_push_notification(user.fcm_token, title, body, data=data)

# ------------------ CONFIG ------------------
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-secret-change-me")
app.config["JWT_SECRET_KEY"] = os.getenv("JWT_SECRET_KEY", "dev-jwt-secret")

# SQLite for now (file lifeline.db in project root).
# app.config["SQLALCHEMY_DATABASE_URI"] = "postgresql+psycopg2://postgres:error101@localhost:5432/lifeline_db"
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///lifeline.db"
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
    dob = db.Column(db.String(20))  # or Date type if you prefer
    
    fcm_token = db.Column(db.String(512), nullable=True)

    

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
# ------------------ FCM HELPER FUNCTIONS ------------------

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
def send_fcm_to_user(user, title, body, data=None):
    """
    Send a push notification to a single user if they have an fcm_token.
    Returns True on success, False otherwise.
    """
    if not user or not getattr(user, "fcm_token", None):
        print("[FCM] No token for user", getattr(user, "id", None))
        return False

    try:
        msg = messaging.Message(
            token=user.fcm_token,
            notification=messaging.Notification(
                title=title,
                body=body,
            ),
            data={k: str(v) for k, v in (data or {}).items()},
        )
        resp = messaging.send(msg)
        print("[FCM] Sent push:", resp)
        return True
    except Exception as e:
        print("[FCM] Error sending push:", e)
        return False
def send_fcm_for_emotional_chat(listener, from_user):
    """
    Notify listener AND save to DB for the bell icon.
    """
    try:
        # 1. Send Mobile Push (Popup)
        send_push_to_user(
            listener,
            title="Emotional Support Request",
            body=f"{from_user.name} is feeling low and wants to chat.",
            data={"type": "EMOTIONAL_CHAT", "sender_id": str(from_user.id)}
        )

        # 2. SAVE TO DB (This fixes the Bell Icon)
        push_notification(
            user_id=listener.id,
            type="chat",
            message=f"{from_user.name} is feeling low and wants to chat.",
            link=url_for('chat_with_user', other_user_id=from_user.id)
        )
        print(f"[FCM] Emotional chat alert saved for listener {listener.id}")

    except Exception as e:
        print(f"[FCM] Error in send_fcm_for_emotional_chat: {e}")
# --- 1. NEARBY HELP REQUEST ---
# --- UPDATE IN app.py ---


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
                if u.fcm_token:
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
    """
    SOS Alert -> Notify Verified Helpers AND save to DB stack.
    """
    try:
        # FIX: Get ALL Trusted Helpers (removed fcm_token filter)
        helpers = User.query.filter(
            User.is_trusted_helper == True,
            User.id != from_user.id
        ).all()

        alert_count = 0
        for h in helpers:
            # 1. SAVE TO DB (Always do this for the Bell Icon)
            push_notification(
                user_id=h.id,
                type="sos",
                message=f"🚨 SOS: {from_user.name} needs immediate help!",
                link=url_for('chat_with_user', other_user_id=from_user.id)
            )

            # 2. Send Mobile Push (Only if they have a token)
            if h.fcm_token:
                send_fcm_to_user(
                    h,
                    title="🚨 SOS ALERT!",
                    body=f"EMERGENCY: {from_user.name} needs help!",
                    data={"type": "SOS_ALERT", "from_user_id": str(from_user.id)}
                )
            
            alert_count += 1
            
        print(f"[FCM] SOS alerts saved for {alert_count} trusted helpers.")

    except Exception as e:
        print(f"[FCM] Error in send_fcm_for_sos: {e}")
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
    if "user_id" in session:
        return db.session.get(User, session["user_id"])
    return None


def login_required(view_func):
    from functools import wraps

    @wraps(view_func)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
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
    """
    How many 'notifications' the user has.
    - New offers on my requests (still pending)
    - Active jobs where I'm the helper (requests in progress)
    """
    if not user:
        return 0

    # 1) Offers that other users made on *my* requests
    pending_offers_for_me = (
        Offer.query
        .join(Request, Offer.request_id == Request.id)
        .filter(
            Request.user_id == user.id,
            Offer.status == "pending"
        )
        .count()
    )

    # 2) Requests where I am the assigned helper and it's still in progress
    my_active_jobs = (
        Request.query
        .filter(
            Request.helper_id == user.id,
            Request.status == "in_progress"
        )
        .count()
    )

    return pending_offers_for_me + my_active_jobs

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
    """
    Best-effort push via FCM.
    - Does nothing if Firebase isn't configured
    - Does nothing if user has no fcm_token
    - Never crashes a request; logs errors only
    """
    try:
        # If Firebase wasn't initialised successfully at startup, skip
        if not firebase_admin._apps:
            return

        if not user or not getattr(user, "fcm_token", None):
            return

        message = messaging.Message(
            token=user.fcm_token,
            notification=messaging.Notification(
                title=title,
                body=body,
            ),
            data={k: str(v) for k, v in (data or {}).items()},
        )
        resp = messaging.send(message)
        print("[FCM] Sent push:", resp)
    except Exception as e:
        print("[FCM] ERROR sending push:", e)
        
def send_fcm_notification(token, title, body, data=None):
    """
    Send a push notification to a single FCM device token.
    We send a DATA-ONLY message so our JS/service worker controls display.
    """
    if not token:
        print("[FCM] No token provided, skipping")
        return

    try:
        payload_data = {
            "title": title,
            "body": body,
        }
        if data:
            # merge any extra keys
            payload_data.update({str(k): str(v) for k, v in data.items()})

        message = messaging.Message(
            token=token,
            data=payload_data,   # <--- DATA ONLY, no "notification" field
        )
        response = messaging.send(message)
        print("[FCM] Successfully sent message:", response)
    except Exception as e:
        print("[FCM] Error sending message:", e)


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
    return render_template("map.html", google_maps_key=GOOGLE_MAPS_API_KEY)

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
    """Return a simple HTML email body for the OTP email."""
    return f"""
    <html>
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
            Sent by LifeLine · University project demo
          </p>
        </div>
      </body>
    </html>
    """


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
    """Verify Google ID token from Firebase, log user in, return redirect URL."""
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
    """Show logged-in user's profile page."""
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
    dob   = request.form.get("dob", "").strip()

    if name:
        user.name = name
        # keep navbar greeting in sync
        session["user_name"] = name

    user.phone = phone or None
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

    nearby = []

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

        nearby.append(item)

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

    if request.method == "POST":
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
        lat=lat,
        lng=lng,
        expires_at=expires_at,
        )
        db.session.add(req)
        db.session.commit()

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

        #  Push: notify trusted helpers
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

    return render_template(
        "need_help.html",
        posts=posts,
        total_need=total_need,
        total_offer=total_offer,
        categories=categories,
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
        data.append({
            "id": u.id,
            "email": u.email,
            "is_trusted_helper": bool(u.is_trusted_helper),
            "has_fcm_token": bool(u.fcm_token),
            "fcm_token": u.fcm_token[:20] + "..." if u.fcm_token else None,
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

    return render_template(
        "list_requests.html",
        requests=requests_list,
        mode=mode,
        offered_ids=offered_ids,  # <--- Pass this to the HTML
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
    Updates the logged-in user's live location.
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
    
@app.route("/emotional")
def emotional_ping():
    user = current_user()
    
    # 1. Guest Handling: If not logged in, use the Emergency Guest account
    if not user:
        user = get_emergency_user()
        login_user(user)

    # 2. Find a Listener (Trusted Helper)
    # Exclude the user themselves so they don't chat with themselves
    listener = User.query.filter(User.is_trusted_helper == True, User.id != user.id).first()
    
    # Fallback: If no trusted helper is found, try to find ANY other user (for demo purposes)
    if not listener:
        listener = User.query.filter(User.id != user.id).first()

    # If still no listener, we can't start a chat
    if not listener:
        flash("No listeners are currently available. Please try posting a request.", "info")
        return redirect(url_for('home'))

    print(f"[Emotional] Connecting user {user.id} with listener {listener.id}")

    # 3. Create or Get Conversation
    conv = get_or_create_conversation(user.id, listener.id)

    # 4. Notify the Listener (Quiet Notification, not SOS)
    try:
        send_fcm_for_emotional_chat(listener, user)
    except Exception as e:
        print(f"[FCM] Error sending emotional chat push: {e}")

    # 5. Redirect to the Chat Room
    return redirect(url_for('chat_with_user', other_user_id=listener.id))

# In app.py


@app.route("/sos/trigger", methods=["POST"])
@login_required
def trigger_sos():
    user = current_user()
    
    # 1. Broadcast the alert
    send_fcm_for_sos(user) 
    
    # 2. Feedback to the user
    flash("SOS BROADCAST SENT! Nearby Trusted Helpers have been alerted.", "error")
    return redirect(request.referrer or url_for("home"))

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
    """
    Save/update the current logged-in user's FCM token.
    Frontend should call this after it gets a token from Firebase Messaging.
    """
    user = current_user()
    data = request.get_json() or {}
    token = (data.get("token") or "").strip()

    if not token:
        return jsonify({"error": "token required"}), 400

    user.fcm_token = token
    db.session.commit()

    return jsonify({"ok": True})

@app.route('/firebase-messaging-sw.js')
def firebase_sw():
    return app.send_static_file('firebase-messaging-sw.js')


# ------------------ SOCKET.IO EVENTS ------------------
@socketio.on("join")
def on_join(data):
    # data = {conversation_id}
    conv_id = data.get("conversation_id")
    user = current_user()
    print(f"[JOIN] User {user.id if user else 'None'} joining conversation {conv_id}")
    if not user or not conv_id:
        print(f"[JOIN] Rejected: no user or conv_id")
        return
    conv = Conversation.query.get(conv_id)
    if not conv or user.id not in conv.participants():
        print(f"[JOIN] Rejected: conv not found or user not participant")
        return
    room = f"chat_{conv_id}"
    join_room(room)
    print(f"[JOIN] Successfully joined room {room}")
    emit("user_joined", {"user_id": user.id}, room=room)


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
    has_token = bool(other_user and getattr(other_user, "fcm_token", None))
    print(f"[SEND_MESSAGE] Other participant user_id={other_id}, has_token={has_token}")

    # ---------- Send push to the other participant ----------
    try:
        if other_user and other_user.fcm_token:
            preview = text or "[Attachment]"
            if len(preview) > 80:
                preview = preview[:80] + "…"

            send_push_to_user(
                other_user,
                title=f"New message from {user.name}",
                body=preview,
                data={
                    "type": "NEW_CHAT_MESSAGE",
                    "conversation_id": str(conv.id),
                    "sender_id": str(user.id),
                },
            )
            print(f"[FCM] Chat push sent to user {other_user.id}")
        else:
            print("[FCM] Other user missing or has no fcm_token; skipping chat push")
    except Exception as e:
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
@login_required
def debug_fcm_test():
    user = current_user()
    ok = send_fcm_to_user(
        user,
        "LifeLine test notification",
        "If you see this, FCM end-to-end works.",
        data={"type": "debug_test"},
    )
    return f"Sent: {ok}"


# ------------------ REGISTER BLUEPRINTS ------------------
# Register resource pooling blueprint (must be after all models are defined)
from resources import resources_bp
app.register_blueprint(resources_bp)

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
    
    socketio.run(app, debug=True)
