# LifeLine

Community-first help platform where people can request help, offer support, and connect with trusted helpers.

Live app: https://lifeline-rlf3.onrender.com

## Highlights

- Create help requests and offers (with categories, urgency, and location)
- Interactive map experience (Google Maps)
- Real-time chat (Flask-SocketIO)
- Smart Suggestions AI (weather + time + proximity scoring)
- Human Availability Radar (live heatmap of nearby active users)
- Trust/kindness scoring + badges to recognize helpers

## Quick links

- Smart Suggestions dashboard: `/suggestions`
- Map (Radar lives here): `/map`
- Main dashboard: `/dashboard`

## Tech stack

- Backend: Flask, SQLAlchemy, Flask-Migrate
- Realtime: Flask-SocketIO + eventlet
- Auth: sessions + JWT (for selected APIs)
- DB: SQLite (local) or Postgres (production)
- Deploy: Render + Gunicorn

## Run locally (Windows / macOS / Linux)

### 1) Create a virtual environment

```bash
python -m venv .venv
```

Activate it:

```bash
# Windows (PowerShell)
.\.venv\Scripts\Activate.ps1

# macOS / Linux
source .venv/bin/activate
```

### 2) Install dependencies

```bash
pip install -r requirements.txt
```

### 3) Configure environment

Copy the example file and edit values:

```bash
cp .env.example .env
```

At minimum for local development, you can run with defaults. For full features, set:

- `GOOGLE_MAPS_API_KEY` (map + radar UI)
- `OPENWEATHER_API_KEY` (Smart Suggestions weather context)

### 4) Start the app

```bash
python app.py
```

Open:

- http://127.0.0.1:5000

## Environment variables

See `.env.example` for the full list. Common ones:

- Core

  - `SECRET_KEY` (Flask session)
  - `JWT_SECRET_KEY` (JWT signing)
  - `FLASK_DEBUG` (`1` for dev)

- Database

  - `DATABASE_URL` (optional; if not set, local SQLite `lifeline.db` is used)

- Maps

  - `GOOGLE_MAPS_API_KEY`

- Smart Suggestions (optional)

  - `OPENWEATHER_API_KEY`
  - `SUGGESTION_RADIUS_KM`
  - `DEFAULT_LAT`, `DEFAULT_LNG` (fallback location)

- Email / OTP (optional)

  - `EMAIL_ADDRESS`, `EMAIL_PASSWORD`

- Firebase push notifications (optional)
  - `FIREBASE_SERVICE_ACCOUNT_JSON` or `FIREBASE_SERVICE_ACCOUNT_JSON_BASE64`
  - `FIREBASE_SERVICE_ACCOUNT_PATH` / `GOOGLE_APPLICATION_CREDENTIALS`

## Deployment (Render)

This repo includes configuration for Render:

- `render.yaml`
- `Procfile`

Production entrypoint uses Gunicorn with eventlet:

```bash
gunicorn --worker-class eventlet --workers 1 --bind 0.0.0.0:$PORT wsgi:app
```

Set secrets in the Render dashboard (don’t commit them):

- `SECRET_KEY`, `JWT_SECRET_KEY`
- `DATABASE_URL` (Postgres)
- Optional: `GOOGLE_MAPS_API_KEY`, `OPENWEATHER_API_KEY`, Firebase/email settings

## Documentation

- Smart Suggestions: `QUICK_START.md`, `SMART_SUGGESTIONS_GUIDE.md`
- Availability Radar: `DOCUMENTATION_INDEX.md` (entry point)

## License

Add a license file if you plan to open-source this project.
