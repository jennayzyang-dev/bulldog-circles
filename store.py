"""SQLite storage for users, verification codes, photos, events, blocks, reports, likes, duos and messages."""

import json
import random
import secrets
import sqlite3
import time
from contextlib import contextmanager

from config import DB_FILE, STUDENTS_FILE

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (email TEXT PRIMARY KEY, profile TEXT, created REAL);
CREATE TABLE IF NOT EXISTS sessions (token TEXT PRIMARY KEY, email TEXT, created REAL);
CREATE TABLE IF NOT EXISTS codes (email TEXT PRIMARY KEY, code TEXT, expires REAL, tries INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS blocks (blocker TEXT, blocked TEXT, time REAL, PRIMARY KEY (blocker, blocked));
CREATE TABLE IF NOT EXISTS reports (reporter TEXT, reported TEXT, reason TEXT, time REAL);
CREATE TABLE IF NOT EXISTS likes (liker TEXT, liked TEXT, mode TEXT, time REAL, PRIMARY KEY (liker, liked, mode));
CREATE TABLE IF NOT EXISTS user_events (id INTEGER PRIMARY KEY AUTOINCREMENT, creator TEXT, name TEXT, category TEXT,
                                        sub TEXT, place TEXT, time_text TEXT, price TEXT, link TEXT, details TEXT,
                                        created REAL, event_date TEXT, event_time TEXT);
CREATE TABLE IF NOT EXISTS duos (id INTEGER PRIMARY KEY AUTOINCREMENT, a TEXT, b TEXT, status TEXT, created REAL);
CREATE TABLE IF NOT EXISTS duo_likes (from_duo TEXT, to_duo TEXT, liked INTEGER, time REAL, PRIMARY KEY (from_duo, to_duo));
CREATE TABLE IF NOT EXISTS photos (student_id TEXT PRIMARY KEY, mime TEXT, data BLOB, time REAL);
CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY AUTOINCREMENT, a TEXT, b TEXT, sender TEXT, text TEXT,
                                     time REAL);
"""


@contextmanager
def db():
    conn = sqlite3.connect(DB_FILE)
    conn.executescript(SCHEMA)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# ---------- verification codes ----------
def new_code(email: str) -> str:
    code = f"{random.SystemRandom().randint(0, 999999):06d}"
    with db() as c:
        c.execute("REPLACE INTO codes VALUES (?, ?, ?, 0)", (email, code, time.time() + 600))
    return code


def check_code(email: str, code: str) -> bool:
    with db() as c:
        row = c.execute("SELECT code, expires, tries FROM codes WHERE email=?", (email,)).fetchone()
        if not row or row[1] < time.time() or row[2] >= 5:
            return False
        if row[0] != code.strip():
            c.execute("UPDATE codes SET tries = tries + 1 WHERE email=?", (email,))
            return False
        c.execute("DELETE FROM codes WHERE email=?", (email,))
        return True


# ---------- sign-in sessions (in the database, so a server restart doesn't sign anyone out) ----------
SESSION_DAYS = 7


def create_session(email: str) -> str:
    token = secrets.token_urlsafe(24)
    with db() as c:
        c.execute("INSERT INTO sessions VALUES (?, ?, ?)", (token, email, time.time()))
    return token


def session_email(token: str | None) -> str | None:
    if not token:
        return None
    with db() as c:
        row = c.execute("SELECT email, created FROM sessions WHERE token=?", (token,)).fetchone()
    if not row or time.time() - row[1] > SESSION_DAYS * 86400:
        return None
    return row[0]


def end_session(token: str | None) -> None:
    with db() as c:
        c.execute("DELETE FROM sessions WHERE token=?", (token or "",))


# ---------- profiles ----------
def save_profile(email: str, profile: dict) -> None:
    with db() as c:
        c.execute("REPLACE INTO users VALUES (?, ?, ?)", (email, json.dumps(profile), time.time()))


def get_profile(email: str) -> dict | None:
    with db() as c:
        row = c.execute("SELECT profile FROM users WHERE email=?", (email,)).fetchone()
    return json.loads(row[0]) if row else None


def delete_user(email: str) -> None:
    """Deletes the profile and everything tied to it (data retention promise)."""
    with db() as c:
        for table, cols in [("users", ["email"]), ("codes", ["email"]), ("likes", ["liker", "liked"]),
                            ("messages", ["a", "b"]), ("sessions", ["email"]),
                            ("user_events", ["creator"]), ("duos", ["a", "b"])]:
            for col in cols:
                c.execute(f"DELETE FROM {table} WHERE {col}=?", (email,))
        c.execute("DELETE FROM photos WHERE student_id=?", (student_id(email),))


def student_id(email: str) -> str:
    return "U-" + email.split("@")[0]


def all_students() -> list[dict]:
    """Sample dataset plus everyone who signed up in the app."""
    students = json.loads(STUDENTS_FILE.read_text())
    with db() as c:
        rows = c.execute("SELECT email, profile FROM users").fetchall()
    for email, profile in rows:
        p = json.loads(profile)
        students.append({**p, "id": student_id(email), "email": email})
    return students


def toggle_going(email: str, event_id: str) -> bool | None:
    """Adds or removes an event on the user's profile. Returns True if now going, None if no profile yet."""
    p = get_profile(email)
    if p is None:
        return None
    events = set(p.get("events", []))
    events.symmetric_difference_update({event_id})
    p["events"] = sorted(events)
    save_profile(email, p)
    return event_id in events


# ---------- block / report / like ----------
def block(blocker: str, blocked: str) -> None:
    with db() as c:
        c.execute("REPLACE INTO blocks VALUES (?, ?, ?)", (blocker, blocked, time.time()))


def blocked_pairs(email: str) -> set[str]:
    """Everyone this user blocked or was blocked by: they never see each other again."""
    with db() as c:
        rows = c.execute("SELECT blocked FROM blocks WHERE blocker=? UNION SELECT blocker FROM blocks WHERE blocked=?",
                         (email, email)).fetchall()
    return {r[0] for r in rows}


def report(reporter: str, reported: str, reason: str) -> None:
    with db() as c:
        c.execute("INSERT INTO reports VALUES (?, ?, ?, ?)", (reporter, reported, reason, time.time()))
    block(reporter, reported)


def like(liker: str, liked: str, mode: str) -> bool:
    """Records interest. Returns True if it is mutual (then contact info may be revealed)."""
    with db() as c:
        c.execute("REPLACE INTO likes VALUES (?, ?, ?, ?)", (liker, liked, mode, time.time()))
        return c.execute("SELECT 1 FROM likes WHERE liker=? AND liked=? AND mode=?",
                         (liked, liker, mode)).fetchone() is not None


# ---------- photos ----------
def save_photo(sid: str, mime: str, data: bytes) -> None:
    with db() as c:
        c.execute("REPLACE INTO photos VALUES (?, ?, ?, ?)", (sid, mime, data, time.time()))


def get_photo(sid: str) -> tuple[str, bytes] | None:
    with db() as c:
        row = c.execute("SELECT mime, data FROM photos WHERE student_id=?", (sid,)).fetchone()
    return (row[0], row[1]) if row else None


# ---------- matches and chat ----------
def matches_of(email: str) -> list[tuple[str, str]]:
    """(other_email, mode) for every mutual like, newest first. Blocked pairs are left out."""
    with db() as c:
        rows = c.execute("""SELECT l1.liked, l1.mode FROM likes l1 JOIN likes l2
                            ON l2.liker = l1.liked AND l2.liked = l1.liker AND l2.mode = l1.mode
                            WHERE l1.liker = ? ORDER BY MAX(l1.time, l2.time) DESC""", (email,)).fetchall()
    blocked = blocked_pairs(email)
    return [(o, m) for o, m in rows if o not in blocked]


def is_match(a: str, b: str) -> bool:
    return any(o == b for o, _m in matches_of(a))


def unmatch(a: str, b: str) -> None:
    with db() as c:
        c.execute("DELETE FROM likes WHERE (liker=? AND liked=?) OR (liker=? AND liked=?)", (a, b, b, a))


def send_message(sender: str, to: str, text: str) -> None:
    send_to(sender, to, sender, text)


def send_to(x: str, y: str, sender: str, text: str) -> None:
    """Message in the conversation keyed by (x, y): two emails for 1:1, two duo IDs for a Double Date group."""
    a, b = sorted([x, y])
    with db() as c:
        c.execute("INSERT INTO messages (a, b, sender, text, time) VALUES (?, ?, ?, ?, ?)",
                  (a, b, sender, text, time.time()))


def get_messages(x: str, y: str, limit: int = 200) -> list[dict]:
    a, b = sorted([x, y])
    with db() as c:
        rows = c.execute("SELECT sender, text, time FROM messages WHERE a=? AND b=? ORDER BY id DESC LIMIT ?",
                         (a, b, limit)).fetchall()
    return [{"sender": r[0], "text": r[1], "time": r[2]} for r in reversed(rows)]


# ---------- events posted by students ----------
def add_event(creator: str, e: dict) -> str:
    with db() as c:
        cur = c.execute("INSERT INTO user_events (creator, name, category, sub, place, time_text, price, link, details, "
                        "created, event_date, event_time) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (creator, e["name"], e["category"], e["sub"], e["where"], e["when"], e["price"], e["link"],
                         e["details"], time.time(), e.get("date"), e.get("time")))
    return f"P{cur.lastrowid}"


def user_events() -> list[dict]:
    with db() as c:
        rows = c.execute("SELECT id, creator, name, category, sub, place, time_text, price, link, details, "
                         "event_date, event_time FROM user_events ORDER BY id DESC").fetchall()
    return [{"id": f"P{r[0]}", "creator": r[1], "name": r[2], "category": r[3], "sub": r[4], "where": r[5],
             "when": r[6], "price": r[7], "link": r[8], "details": r[9], "date": r[10], "time": r[11],
             "tags": [r[4].lower()], "posted": True}
            for r in rows]


# Sample meetups so the calendar isn't empty: (days from today, time, name, category, type, place, price, details)
DEMO_MEETUPS = [
    (0, "19:00", "Pickup soccer", "Sports", "Soccer", "Payne Whitney Gym fields", "Free", "Casual game, all levels."),
    (0, "21:30", "Dance night at Toad's Place", "Nightlife & DJ shows", "Dance night", "Toad's Place, York St",
     "$10–15", "Meeting outside at 9:15."),
    (0, "18:30", "Ramen run downtown", "Day trips & fun", "Food", "Downtown New Haven", "~$20", "Walking from Cross Campus."),
    (1, "20:00", "Board game night", "Day trips & fun", "Games", "Hall of Graduate Studies", "Free",
     "Bring a game if you have one."),
    (2, "19:30", "Formula 1 watch party", "Sports", "Formula 1", "Evans Hall, SOM", "Free", "Big screen, snacks."),
    (3, "22:00", "DJ night in New Haven", "Nightlife & DJ shows", "DJ show", "College Street Music Hall", "$20",
     "Going as a group, splitting Ubers back."),
    (4, "10:00", "Day trip to Beardsley Zoo", "Day trips & fun", "Zoos & aquariums", "Bridgeport, CT", "$20 + Uber split",
     "Leaving from Phelps Gate."),
    (5, "07:00", "Sunrise hike up East Rock", "Sports", "Hiking", "East Rock Park", "Free", "Coffee after."),
    (6, "11:00", "Metro-North to NYC: MoMA", "Art & culture", "Museums", "New York, NY", "~$30 train", "11:05 train from Union Station."),
    (8, "19:00", "Knicks game — group tickets", "Sports", "Basketball", "Madison Square Garden, NYC", "~$85",
     "Looking for 3 more people."),
    (10, "20:00", "Salsa night (free lesson first)", "Nightlife & DJ shows", "Dance night", "Downtown New Haven", "$10",
     "Beginners welcome."),
]


def demo_meetups() -> list[dict]:
    """Dated sample meetups posted by sample students, on dates relative to today."""
    import datetime as dt
    import random as rnd
    students = json.loads(STUDENTS_FILE.read_text())
    today = dt.date.today()
    out = []
    for i, (days, hhmm, name, cat, sub, place, price, details) in enumerate(DEMO_MEETUPS, start=1):
        r = rnd.Random(i)
        creator, *going = r.sample(students, r.randint(3, 8))
        out.append({"id": f"M{i:02d}", "creator": creator["email"], "name": name, "category": cat, "sub": sub,
                    "where": place, "price": price, "details": details, "link": "", "tags": [sub.lower(), cat.lower()],
                    "date": (today + dt.timedelta(days=days)).isoformat(), "time": hhmm, "when": "",
                    "posted": True, "demo": True, "demo_going": [creator["email"]] + [g["email"] for g in going]})
    return out


def delete_event(creator: str, event_id: str) -> None:
    with db() as c:
        c.execute("DELETE FROM user_events WHERE id=? AND creator=?", (int(event_id.lstrip("P")), creator))


# ---------- Double Date (Tinder-style duos) ----------
def sample_duos() -> list[dict]:
    """Fake duos made from sample profiles who are open to dating, so the feature can be tried."""
    people = [s for s in json.loads(STUDENTS_FILE.read_text()) if s.get("dating_open")]
    return [{"id": f"D-{x['id']}-{y['id']}", "members": [x["email"], y["email"]], "status": "active", "demo": True}
            for x, y in zip(people[0::2], people[1::2])]


def user_duos() -> list[dict]:
    with db() as c:
        rows = c.execute("SELECT id, a, b, status FROM duos").fetchall()
    return [{"id": f"U{r[0]}", "members": [r[1], r[2]], "status": r[3], "demo": False} for r in rows]


def all_duos() -> list[dict]:
    return sample_duos() + user_duos()


def my_duo(email: str) -> dict | None:
    """My active duo, or a pending invite I sent or received."""
    for d in user_duos():
        if email in d["members"] and d["status"] in ("active", "pending"):
            return d
    return None


def invite_duo(inviter: str, friend: str, auto_accept: bool) -> None:
    with db() as c:
        c.execute("INSERT INTO duos (a, b, status, created) VALUES (?, ?, ?, ?)",
                  (inviter, friend, "active" if auto_accept else "pending", time.time()))


def accept_duo(email: str) -> None:
    with db() as c:
        c.execute("UPDATE duos SET status='active' WHERE b=? AND status='pending'", (email,))


def leave_duo(email: str) -> None:
    d = my_duo(email)
    if d:
        with db() as c:
            c.execute("DELETE FROM duos WHERE id=?", (int(d["id"][1:]),))
            c.execute("DELETE FROM duo_likes WHERE from_duo=? OR to_duo=?", (d["id"], d["id"]))


def rate_duo(from_duo: str, to_duo: str, liked: bool) -> bool:
    """Like or pass. Returns True if it's a mutual like (then a group chat opens)."""
    with db() as c:
        c.execute("REPLACE INTO duo_likes VALUES (?, ?, ?, ?)", (from_duo, to_duo, int(liked), time.time()))
        return liked and c.execute("SELECT 1 FROM duo_likes WHERE from_duo=? AND to_duo=? AND liked=1",
                                   (to_duo, from_duo)).fetchone() is not None


def rated_duos(duo_id: str) -> set[str]:
    with db() as c:
        return {r[0] for r in c.execute("SELECT to_duo FROM duo_likes WHERE from_duo=?", (duo_id,)).fetchall()}


def duo_matches(duo_id: str) -> list[str]:
    with db() as c:
        rows = c.execute("""SELECT l1.to_duo FROM duo_likes l1 JOIN duo_likes l2
                            ON l2.from_duo = l1.to_duo AND l2.to_duo = l1.from_duo AND l2.liked = 1
                            WHERE l1.from_duo = ? AND l1.liked = 1""", (duo_id,)).fetchall()
    return [r[0] for r in rows]


def unmatch_duos(a: str, b: str) -> None:
    """Ends the match and records a pass both ways, so the two duos don't see each other again."""
    with db() as c:
        for x, y in ((a, b), (b, a)):
            c.execute("REPLACE INTO duo_likes VALUES (?, ?, 0, ?)", (x, y, time.time()))
