"""Shared settings: .env lookup, model, prices, paths."""

import json
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent

# Rule from the assignment: look in the repo folder first, then one level up.
for env_dir in (ROOT, ROOT.parent):
    if (env_dir / ".env").exists():
        load_dotenv(env_dir / ".env")
        break

MODEL_NAME = "gpt-6-luna"  # the only model allowed on the course key
PRICES = json.loads((ROOT / "data" / "prices.json").read_text())[MODEL_NAME]

# Swap datasets by setting STUDENTS_FILE / CLUBS_FILE in .env (see README).
STUDENTS_FILE = ROOT / os.getenv("STUDENTS_FILE", "data/students_sample.json")
CLUBS_FILE = ROOT / os.getenv("CLUBS_FILE", "data/clubs.json")
DB_FILE = ROOT / "data" / "app.db"
LOG_DIR = ROOT / "logs"

# Safety limits per agent run (stops loops and overspending).
MAX_REQUESTS_PER_RUN = 6
MAX_TOKENS_PER_RUN = 60_000

# Optional real email for verification codes. Without these, demo mode shows the code on screen.
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
