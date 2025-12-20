import os

# Render/local deployment: configure these as environment variables.
# For local dev you can also set them in a `.env` file (loaded by app.py).
EMAIL_ADDRESS = os.getenv("EMAIL_ADDRESS", "")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD", "")
