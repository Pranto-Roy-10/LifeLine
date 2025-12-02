from datetime import datetime, timedelta
from sqlalchemy import func

from flask import (
    Flask, render_template, request,
    redirect, url_for, session, flash, jsonify
)
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
import os
import math
import time

app = Flask(__name__)

# ------------------ CONFIG ------------------
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-secret-change-me")

# SQLite for now (file lifeline.db in project root). Later change to Postgres URI.
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///lifeline.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)

# Your Google Maps API key
GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY", "AIzaSyAVtLNl7YZaPZeSnuA_5Gxm8VdtFXxreYo")

# ------------------ MODELS ------------------
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False)
    name = db.Column(db.String(120), nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    is_trusted_helper = db.Column(db.Boolean, default=False)

    def set_password(self, password: str):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)


class Request(db.Model):
    __tablename__ = "requests"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    title = db.Column(db.String(255), nullable=False)
    category = db.Column(db.String(50), nullable=False)  # medicine, food, tutoring, repair, ride
    description = db.Column(db.Text, nullable=True)
    lat = db.Column(db.Float, nullable=True)
    lng = db.Column(db.Float, nullable=True)
    is_offer = db.Column(db.Boolean, default=False)  # false = Need Help, true = Offering Help
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
            "is_offer": self.is_offer,
            "created_at": int(self.created_at.timestamp()),
            "expires_at": int(self.expires_at.timestamp()),
            "status": self.status,
        }
        if include_user:
            d["user_name"] = self.user.name if self.user else None
        now = datetime.utcnow()
        d["seconds_remaining"] = max(0, int((self.expires_at - now).total_seconds()))
        if user_lat is not None and user_lng is not None and self.lat is not None and self.lng is not None:
            try:
                d["distance_km"] = round(haversine_distance_km(user_lat, user_lng, self.lat, self.lng), 2)
            except Exception:
                d["distance_km"] = None
        return d

# ------------------ AUTH HELPERS ------------------
def login_user(user: User):
    session["user_id"] = user.id
    session["user_name"] = user.name


def logout_user():
    session.pop("user_id", None)
    session.pop("user_name", None)


def current_user():
    if "user_id" in session:
        return User.query.get(session["user_id"])
    return None


def login_required(view_func):
    # simple decorator to protect routes
    from functools import wraps

    @wraps(view_func)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            # redirect to login and remember where to return
            next_url = request.path
            return redirect(url_for("login", next=next_url))
        return view_func(*args, **kwargs)

    return wrapper


# Make current user available in all templates as `current_user`
@app.context_processor
def inject_user():
    return {"current_user": current_user()}


# ------------------ DEMO DATA FOR MAP ------------------
DEMO_REQUESTS = [
    {
        "id": 1,
        "title": "Medicine Run – Urgent",
        "type": "medicine",   # 'medicine' / 'help' / 'ride'
        "lat": 23.7555,
        "lng": 90.3650,
        "description": "BP tablets needed for elderly neighbor.",
        "created_at": time.time() - 60  # 1 minute ago
    },
    {
        "id": 2,
        "title": "Ride Offer – Office",
        "type": "ride",
        "lat": 23.7510,
        "lng": 90.3800,
        "description": "Going to Dhanmondi, 2 seats free.",
        "created_at": time.time() - 300  # 5 minutes ago
    },
    {
        "id": 3,
        "title": "Need Help – Laptop Repair",
        "type": "help",
        "lat": 23.7600,
        "lng": 90.3700,
        "description": "Laptop not turning on, need quick check.",
        "created_at": time.time() - 900  # 15 minutes ago
    },
]


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


@app.route("/map")
@login_required
def map_page():
    # full live map – only for logged-in users
    return render_template("map.html", google_maps_key=GOOGLE_MAPS_API_KEY)


# ------------------ ROUTES: AUTH ------------------
@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm", "")

        # basic validation
        if not name or not email or not password:
            flash("Please fill in all fields.", "error")
            return redirect(url_for("signup"))

        if password != confirm:
            flash("Passwords do not match.", "error")
            return redirect(url_for("signup"))

        # check if email already exists
        existing = User.query.filter_by(email=email).first()
        if existing:
            flash("An account with this email already exists. Please log in.", "error")
            return redirect(url_for("login"))

        # create user
        user = User(name=name, email=email)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()

        login_user(user)
        flash("Welcome to LifeLine! Your account has been created.", "success")

        next_url = request.args.get("next") or url_for("home")
        return redirect(next_url)

    # GET
    return render_template("signup.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        user = User.query.filter_by(email=email).first()
        if not user or not user.check_password(password):
            flash("Invalid email or password.", "error")
            return redirect(url_for("login"))

        login_user(user)
        flash("Logged in successfully.", "success")

        next_url = request.args.get("next") or url_for("home")
        return redirect(next_url)

    # GET
    return render_template("login.html")


@app.route("/logout")
def logout():
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for("home"))


# ------------------ API: NEARBY REQUESTS FOR MAP ------------------
@app.route("/api/requests/nearby")
@login_required
def api_requests_nearby():
    try:
        user_lat = float(request.args.get("lat"))
        user_lng = float(request.args.get("lng"))
    except (TypeError, ValueError):
        return jsonify({"error": "lat and lng are required"}), 400

    radius_km = float(request.args.get("radius_km", 3.0))

    now = time.time()
    max_age_seconds = 60 * 60  # last 1 hour

    nearby = []
    for r in DEMO_REQUESTS:
        if now - r["created_at"] > max_age_seconds:
            continue
        dist = haversine_distance_km(user_lat, user_lng, r["lat"], r["lng"])
        if dist <= radius_km:
            item = dict(r)
            item["distance_km"] = round(dist, 2)
            nearby.append(item)

    return jsonify({"requests": nearby})

# Create a new request/offer
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
        q = q.filter(Request.is_offer == False)

    results = []
    for r in q.all():
        if user_lat is not None and user_lng is not None and r.lat is not None and r.lng is not None:
            dist = haversine_distance_km(user_lat, user_lng, r.lat, r.lng)
            if dist > radius_km:
                continue
            results.append(r.to_dict(include_user=True, user_lat=user_lat, user_lng=user_lng))
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


@app.route("/requests")
@login_required
def list_requests():
    # Show only requests that haven't expired and are open
    now = datetime.utcnow()
    requests_list = Request.query.filter(
        Request.expires_at > now,
        Request.status == "open"
    ).order_by(Request.created_at.desc()).all()
    return render_template("list_requests.html", requests=requests_list)


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
    app.run(debug=True)










