"""Safety layer that runs in plain Python, outside the model, so a tricky prompt cannot switch it off."""

import csv
import re
import time

from config import LOG_DIR

YALE_EMAIL = re.compile(r"^[A-Za-z0-9._%+-]+@yale\.edu$")

INJECTION = re.compile(
    r"ignore (all |any )?(previous|prior|above) instructions|system prompt|you are now|disregard .{0,30}instructions"
    r"|rank me|list (every|all) .{0,20}(email|phone|address)|developer mode|jailbreak",
    re.IGNORECASE,
)
CONTACT = re.compile(
    r"[\w.+-]+@[\w-]+\.[\w.]+"                      # emails
    r"|\+?\d[\d\s().-]{7,}\d"                       # phone numbers
    r"|\b(?:ig|insta|instagram|snap|snapchat|whatsapp|telegram)\b\s*[:@]?\s*@?\w+"  # social handles
    r"|@\w{3,}",
    re.IGNORECASE,
)


def is_yale_email(email: str) -> bool:
    return bool(YALE_EMAIL.match((email or "").strip()))


def log_event(kind: str, detail: str) -> None:
    LOG_DIR.mkdir(exist_ok=True)
    path = LOG_DIR / "safety.csv"
    new = not path.exists()
    with path.open("a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["time", "event", "detail"])
        w.writerow([time.strftime("%Y-%m-%d %H:%M:%S"), kind, detail[:200]])


def clean_bio(student_id: str, bio: str) -> str:
    """Hides bios that try to instruct the agent; strips contact info from the rest."""
    if INJECTION.search(bio or ""):
        log_event("injection_blocked", f"{student_id}: {bio}")
        return "[bio hidden: failed safety check]"
    cleaned = CONTACT.sub("[contact hidden]", bio or "")
    if cleaned != bio:
        log_event("contact_scrubbed", student_id)
    return cleaned


def eligible(me: dict, them: dict, mode: str, blocked: set[str]) -> bool:
    """Who may appear in my results. Dating needs both people to opt in and match each other's preferences."""
    if them.get("email") == me.get("email") or them.get("email") in blocked:
        return False
    if mode != "dating":
        return True
    return (
        me.get("dating_open") and them.get("dating_open")
        and them.get("gender") in me.get("dating_interested_in", [])
        and me.get("gender") in them.get("dating_interested_in", [])
    )


def is_undergrad(student: dict) -> bool:
    school = (student.get("school") or "").lower()
    return "yale college" in school or "undergrad" in school


def level_ok(student: dict, level: str) -> bool:
    """level: 'all', 'undergrad' or 'grad' (graduate and professional schools)."""
    return level == "all" or (level == "undergrad") == is_undergrad(student)


def club_level_ok(club: dict, level: str) -> bool:
    who = club.get("who", "").lower()
    if level == "all" or "all yale" in who or "everyone" in who:
        return True
    if level == "undergrad":
        return "undergrad" in who
    return bool(re.search(r"\bgrad|professional|som\b", who))


def public_view(student: dict) -> dict:
    """What the matcher agent is allowed to see. No email, no dating details."""
    return {
        "id": student["id"], "name": student["name"], "school": student["school"], "year": student.get("year", ""),
        "interests": student.get("interests", []), "availability": student.get("availability", []),
        "looking_for": student.get("looking_for", ""), "bio": clean_bio(student["id"], student.get("bio", "")),
        "prompts": [{"q": p["q"], "a": clean_bio(student["id"], p["a"])} for p in student.get("prompts", [])],
    }
