# Bulldog Circles

Find Yale people who share your interests, across every school. A student describes themselves in their own words; AI agents turn that into a profile, search the student pool and Yale clubs, explain each match, and draft a first message. Dating mode is optional, off by default, and only matches people who both opted in.

## Run it (about 5 minutes)

Needs Python 3.11+.

```bash
git clone <this repo> bulldog-circles
cd bulldog-circles
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Put a `.env` file with your key **either in this folder or one folder above it** (the app checks this folder first, then the parent):

```
PORTKEY_API_KEY=your-key-here
```

Then:

```bash
python app.py
```

Open http://127.0.0.1:8050

Six tabs: **Discover · Events · Friends · Dating · Chats · Profile** (top bar on a laptop, bottom bar on a phone). The footer links to **How it works / Safety & privacy**.

1. **As a guest:** Discover shows *Happening tonight*, popular events and circles. **Events** has four tabs: *Calendar* (the next 3 days as columns, later days, then popular big events), *Events & trips* (Sports, Music festivals, Nightlife & DJ shows, Concerts & shows, Art & culture, Day trips & fun), *Interest circles* and *My events*. **Dating** shows a locked preview.
2. **Sign in** (top right or **Get started**): any `@yale.edu` email → **Continue**. Demo mode skips the email code; with `SMTP_USER`/`SMTP_PASSWORD` set, a 6-digit code is emailed instead.
3. **Profile:** sign in (existing account) or create an account (new), then enter your name, school and year (dropdowns), write a few sentences about yourself, optionally pick up to 3 prompts, and **Save profile**. The AI builds your profile; prompt answers are checked for prompt injection and hurtful content. Then **Next steps** appear. Optionally upload a photo (AI-screened) and **Get verified ✓** (selfie copying a random pose, checked against your photo; the selfie is not stored).
4. **Friends → Find my friends:** the AI's top picks for friends, each with a reason, then groups for you and *More people you might like*. Buttons on each card: draft a hello, connect, block, report.
5. **Events:** **I'm going**, **See who's going**, **Add to calendar** (.ics for Apple/Google/Outlook), and **+ Post an event** (name, category, location, date, time, price, link, details). Posts are checked for prompt injection and hurtful content, contact info is hidden, and dated posts appear on the calendar and in *My events*.
6. **Dating:** in Profile tick *Show me in Dating* and pick *I am* / *Interested in*. The Dating tab unlocks → **Find dates** (filter by event or verified profiles only).
   - **Double Date:** invite someone you've connected with to be your duo (sample profiles accept instantly). **Find duos** shows other pairs, filtered by everyone's dating preferences and blocks; like or pass. When both duos like each other, a group chat for all four opens. Unmatch, block or report there applies to the whole duo.
7. **Chats:** open when both people say yes. Sample (demo) profiles accept right away and send one auto-reply so you can try it. Messages are AI-checked before sending; you can unmatch, block or report.

Sample data: 48 fake students, 13 sample duos and 11 *demo meetups* dated relative to today, so the calendar always has something tonight. If an AI call fails, the app shows "Our AI is busy, please try again" instead of stalling, and safety checks fail closed (nothing is posted or sent unchecked).

Safety tests (no API key needed): `python tests/test_safety.py`

## Project documents

- `report.html`: the final report (open by double-clicking; all images are embedded).
- `slides.html`: the slide deck (reveal.js; arrow keys to move, F for full screen).
- `docs/user_testing.md`: the real-user test (times, ratings, quotes, fixes), with screenshots in `docs/user_test/`.
- `docs/evidence/`: measured token log (`usage.csv`), safety log (`safety.csv`), a traced agent run (`traced_run.json`) and the safety test output.
- To rebuild the report and slides after editing `docs/*_template.html`: `python scripts/build_docs.py`.

## How it works

| Piece | File | Job |
|---|---|---|
| Intake agent | `agents.py`, `prompts/intake.md` | Free text → structured `Profile` |
| Guard (plain Python) | `guard.py` | Yale-email check, hides injected bios, scrubs contact info, dating opt-in and block filters |
| Matcher agent | `agents.py`, `prompts/matcher.md` | Tools `search_students` and `search_clubs`; returns ranked matches with reasons. Code then drops any ID the tools never returned |
| Icebreaker agent | `agents.py`, `prompts/icebreaker.md` | Writes a short first message |
| Photo screen agent | `prompts/photo_check.md` | Rejects explicit photos, non-people, and photos showing contact info |
| Selfie verification agent | `prompts/selfie_check.md` | Pose + same person + looks live → ✓ Verified badge. The selfie is never stored |
| Message review agent | `prompts/message_check.md` | Flags hurtful or pressuring chat messages with a kind nudge before sending |
| Storage | `store.py` | SQLite (`data/app.db`): users, codes, photos, student-posted events, blocks, reports, likes (matches), duos and duo likes, messages (1:1 and group) |

Model: `gpt-6-luna` through Portkey. Each run is capped at 6 model requests and 60k tokens.

Logs: `logs/usage.csv` (tokens and cost per agent call) and `logs/safety.csv` (blocked injections, scrubbed contact info, dropped invented matches, reports).

## Data

- `data/students_sample.json`: 48 **fake** students (regenerate with `python scripts/make_sample_data.py`). Two include safety test cases: a prompt-injection bio and a bio with a phone number.
- `data/clubs.json`: Yale clubs and centers.
- Anyone who signs up in the app is added to the pool.

To switch datasets, add `STUDENTS_FILE=path/to/students.json` or `CLUBS_FILE=...` to `.env`, using the same fields as the sample files.

## Optional: real email codes

Add a Gmail address and [app password](https://support.google.com/accounts/answer/185833) to `.env` as `SMTP_USER` and `SMTP_PASSWORD`. Codes are then emailed instead of shown on screen.

## Privacy

Emails are never sent to the model and never shown until both people click Connect / I'm interested. **Delete my data** removes the profile, photo, plans, matches, chats and posts.
