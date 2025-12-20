# LifeLine

## Deploy on Render

This repo is Render-ready via `render.yaml`.

### Start command

Render runs the app with:

`gunicorn -k eventlet -w 1 -b 0.0.0.0:$PORT app:app`

### Python version

Render defaults to Python 3.13.x for new services unless you pin a version.
This repo pins Python to 3.11.9 via:

- `.python-version`
- `PYTHON_VERSION=3.11.9` in `render.yaml`

### Database (SQLite)

By default the app uses SQLite. On Render you must use a persistent disk.

- Disk mount: `/var/data`
- DB file: set `SQLITE_PATH=/var/data/lifeline.db` (already in `render.yaml`)

### Using Neon (Postgres)

If you want NeonDB instead of SQLite, set `DATABASE_URL` (from Neon) in Render.
The app will automatically use it when present.

Note: Neon typically requires TLS; keep `sslmode=require` in the Neon connection string.

### Required environment variables (Render dashboard)

- `OPENWEATHER_API_KEY` (weather/smart suggestions)
- `GOOGLE_MAPS_API_KEY` (maps)
- `EMAIL_ADDRESS`, `EMAIL_PASSWORD` (OTP/email; optional if you don't use email)

### Uploads persistence

Profile photo uploads are persisted on Render by storing them under `/var/data/profile_photos`
and symlinking `static/uploads/profile_photos` to that disk path at runtime.

### Optional environment variables

- `DEFAULT_LAT`, `DEFAULT_LNG` (fallback location)
- `SUGGESTION_RADIUS_KM`
- `DATABASE_URL` (if you switch to Postgres; overrides SQLite)
- `FIREBASE_SERVICE_ACCOUNT_PATH` (path to service account json if not using the default)
