from datetime import datetime, timedelta
import os
import math
import time
import random
import string

from sqlalchemy import func

from flask import (
    Flask, render_template, request,
    redirect, url_for, session, flash, jsonify
)
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

from flask_mail import Mail, Message
from email_config import EMAIL_ADDRESS, EMAIL_PASSWORD

import firebase_admin
from firebase_admin import credentials, auth as firebase_auth

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

# -------- Firebase Admin (for Google sign-in) --------
FIREBASE_CRED_PATH = os.getenv(
    "GOOGLE_APPLICATION_CREDENTIALS",
    "firebase-service-account.json",  # default path
)

if not firebase_admin._apps:
    cred = credentials.Certificate(FIREBASE_CRED_PATH)
    firebase_admin.initialize_app(cred)

# ------------------ CONFIG ------------------
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-secret-change-me")
app.config["JWT_SECRET_KEY"] = os.getenv("JWT_SECRET_KEY", "dev-jwt-secret")

# SQLite for now (file lifeline.db in project root).
# app.config["SQLALCHEMY_DATABASE_URI"] = "postgresql+psycopg2://postgres:error101@localhost:5432/lifeline_db"
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///lifeline.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["UPLOAD_FOLDER"] = os.path.join("static", "uploads")
app.config["ALLOWED_IMAGE_EXTENSIONS"] = {"png", "jpg", "jpeg", "gif"}
os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)


def allowed_image(filename):
    if "." not in filename:
        return False
    ext = filename.rsplit(".", 1)[1].lower()
    return ext in app.config["ALLOWED_IMAGE_EXTENSIONS"]


db = SQLAlchemy(app)
jwt = JWTManager(app)

# Socket.IO for real-time chat (eventlet will be auto-detected)
socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    ping_timeout=60,
    ping_interval=25
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
    status = db.Column(db.String(20), default="open")  # open / claimed / closed

    user = db.relationship("User", backref="requests")

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
        return User.query.get(session["user_id"])
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


# Make current user available in all templates as `current_user`
@app.context_processor
def inject_user():
    # Expose whether translation is available to templates
    translation_enabled = False
    if globals().get('gcloud_translate_client'):
        translation_enabled = True
    elif globals().get('gt_translator'):
        translation_enabled = True
    return dict(current_user=current_user(), translation_enabled=translation_enabled)

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

# ------------------ ROUTES: CORE PAGES ------------------
@app.route("/")
def home():
    return render_template("home.html")


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

    email = decoded.get("email")
    uid = decoded.get("uid")
    name = decoded.get("name") or (email.split("@")[0] if email else "Google user")

    if not email:
        return jsonify({"error": "Google account has no email"}), 400

    # Find or create local user
    user = User.query.filter_by(email=email).first()
    if not user:
        user = User(
            email=email,
            name=name,
            firebase_uid=uid,
        )
        db.session.add(user)
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

    # 1) Try find user by firebase_uid
    user = User.query.filter_by(firebase_uid=uid).first()

    # 2) Or by email (if they signed up with password earlier)
    if not user and email:
        user = User.query.filter_by(email=email).first()

    # 3) If still no user, create one
    if not user:
        user = User(email=email, name=name, firebase_uid=uid)
        db.session.add(user)
    else:
        # Link existing account with this Firebase UID
        if not user.firebase_uid:
            user.firebase_uid = uid

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

        # 🔄 Keep session in sync so navbar updates
        session["is_trusted_helper"] = True

        flash("You are now a VERIFIED Trusted Helper!", "success")
        return redirect(url_for("trusted_helper"))

    return render_template("trusted_helper.html", user=user)

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

    return render_template(
        "list_requests.html",
        requests=requests_list,
        mode=mode,
    )


# ------------------ CHAT PAGES & API ------------------
@app.route("/chat/<int:other_user_id>")
@login_required
def chat_with_user(other_user_id):
    user = current_user()
    other = User.query.get_or_404(other_user_id)
    conv = get_or_create_conversation(user.id, other.id)
    # prefetch last 100 messages
    messages = (
        ChatMessage.query.filter_by(conversation_id=conv.id).order_by(ChatMessage.created_at.asc()).limit(100).all()
    )
    # serialize messages for JSON/template safety
    messages_serialized = [serialize_message(m) for m in messages]
    return render_template("chat.html", conversation=conv, other_user=other, messages=messages_serialized)


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
        conversations.append({
            "conversation": c,
            "other": other,
            "last_message": last.text if last else None,
            "last_time": int(last.created_at.timestamp()) if last else None,
        })

    # also show some nearby helpers to start a new chat (trusted helpers)
    helpers = User.query.filter(User.is_trusted_helper == True, User.id != user.id).limit(10).all()
    return render_template("chat_index.html", conversations=conversations, helpers=helpers)


@app.route("/emotional")
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
    # data: {conversation_id, text, language (optional)}
    conv_id = data.get("conversation_id")
    text = data.get("text", "")
    temp_id = data.get("temp_id")
    lang = data.get("language")
    user = current_user()
    print(f"[SEND_MESSAGE] From user {user.id if user else 'None'} to conv {conv_id}: {text[:50]}")
    if not user or not conv_id or not text:
        print(f"[SEND_MESSAGE] Rejected: missing user, conv_id, or text")
        return
    conv = Conversation.query.get(conv_id)
    if not conv or user.id not in conv.participants():
        print(f"[SEND_MESSAGE] Rejected: conv not found or user not participant")
        return

    msg = ChatMessage(conversation_id=conv.id, sender_id=user.id, text=text, language=lang)
    db.session.add(msg)
    db.session.commit()
    print(f"[SEND_MESSAGE] Saved message {msg.id} to DB")

    payload = serialize_message(msg)
    # include the client's temporary id so client can replace optimistic UI
    if temp_id:
        payload['temp_id'] = temp_id
    room = f"chat_{conv_id}"
    print(f"[SEND_MESSAGE] Broadcasting to room {room}")
    # send to room; clients should acknowledge
    emit("new_message", payload, room=room)
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

# ------------------ MAIN ------------------
if __name__ == "__main__":
    with app.app_context():
        db.create_all()  # create tables if not exist
    socketio.run(app, debug=True)
