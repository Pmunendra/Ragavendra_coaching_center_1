import os
from dotenv import load_dotenv

load_dotenv()  # .env ఫైల్ లోడ్ చేస్తుంది (DATABASE_URL, SECRET_KEY, etc.)

from app import create_app

app = create_app()

if __name__ == "__main__":
    debug = os.environ.get("FLASK_ENV") != "production"
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=debug)