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

GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY", "YOUR_API_KEY_HERE")

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


# ------------------ DEMO API FOR MAP (from earlier step) ------------------
DEMO_REQUESTS = [
    {
        "id": 1,
        "title": "Medicine Run – Urgent",
        "type": "medicine",
        "lat": 23.7555,
        "lng": 90.3650,
        "description": "BP tablets needed for elderly neighbor.",
        "created_at": time.time() - 60
    },
    {
        "id": 2,
        "title": "Ride Offer – Office",
        "type": "ride",
        "lat": 23.7510,
        "lng": 90.3800,
        "description": "Going to Dhanmondi, 2 seats free.",
        "created_at": time.time() - 300
    },
    {
        "id": 3,
        "title": "Need Help – Laptop Repair",
        "type": "help",
        "lat": 23.7600,
        "lng": 90.3700,
        "description": "Laptop not turning on, need quick check.",
        "created_at": time.time() - 900
    },
]


def haversine_distance_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    lat1_r, lon1_r = math.radians(lat1), math.radians(lon1)
    lat2_r, lon2_r = math.radians(lat2), math.radians(lon2)
    dlat = lat2_r - lat1_r
    dlon = lon2_r - lon1_r
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1_r) * math.cos(lat2_r) * math.sin(dlon / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


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


# ------------------ MAIN ------------------
if __name__ == "__main__":
    with app.app_context():
        db.create_all()  # create tables if not exist
    app.run(debug=True)
