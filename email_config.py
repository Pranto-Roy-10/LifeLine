import os

# Read from environment variables. Never hard-code credentials in the repo.
EMAIL_ADDRESS = os.getenv("EMAIL_ADDRESS", "")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD", "")
