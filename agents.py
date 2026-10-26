"""The three agents: intake (profile builder), matcher (search tools + ranking), icebreaker (first message).

Pipeline: student text -> Intake agent -> Profile -> Python guard filters the pool -> Matcher agent (tools) ->
Python check drops invented IDs -> match cards -> Icebreaker agent on demand.
"""

import csv
import json
import os
import time
from dataclasses import dataclass, field
from typing import Literal

os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")
from openai import AsyncOpenAI
from pydantic import BaseModel, Field
from pydantic_ai import Agent, BinaryContent, RunContext
from pydantic_ai.models.openai import OpenAIResponsesModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.usage import UsageLimits

import guard
import store
from config import CLUBS_FILE, LOG_DIR, MAX_REQUESTS_PER_RUN, MAX_TOKENS_PER_RUN, MODEL_NAME, PRICES, ROOT

LIMITS = UsageLimits(request_limit=MAX_REQUESTS_PER_RUN, total_tokens_limit=MAX_TOKENS_PER_RUN)
CLUBS = json.loads(CLUBS_FILE.read_text())
CLUB_IDS = {c["id"] for c in CLUBS}
EVENT_NAMES = {e["id"]: e["name"] for e in json.loads((ROOT / "data" / "events.json").read_text())}


def event_names() -> dict[str, str]:
    """Built-in events plus ones students posted."""
    return {**EVENT_NAMES, **{e["id"]: e["name"] for e in store.demo_meetups() + store.user_events()}}


def going_to(student: dict) -> list[str]:
    names = event_names()
    return [names[e] for e in student.get("events", []) if e in names]


def _model() -> OpenAIResponsesModel:
    client = AsyncOpenAI(
        api_key="unused",
        base_url=os.getenv("PORTKEY_BASE_URL", "https://api.portkey.ai/v1"),
        default_headers={"x-portkey-api-key": os.environ["PORTKEY_API_KEY"]},
    )
    return OpenAIResponsesModel(MODEL_NAME, provider=OpenAIProvider(openai_client=client))


def _prompt(name: str) -> str:
    return (ROOT / "prompts" / name).read_text()


# ---------- structured outputs ----------
class Profile(BaseModel):
    name: str = Field(description="First name and last initial, e.g. 'Maya C.'. Use what the student wrote.")
    school: str = Field(description="Yale school, e.g. 'Yale College', 'School of Management'.")
    year: str = Field(description="Class year or program year. 'unknown' if not stated.")
    interests: list[str] = Field(description="3-8 short lowercase interest tags, e.g. 'rock climbing', 'jazz'.")
    availability: list[str] = Field(description="When they are free, e.g. 'weekday evenings'. Empty if not stated.")
    looking_for: str = Field(description="What kind of connection they want, in a few words.")
    bio: str = Field(description="A friendly 1-2 sentence bio in their voice, with NO contact info.")


class Match(BaseModel):
    student_id: str = Field(description="ID exactly as returned by search_students.")
    shared_interests: list[str] = Field(description="Interests both people actually listed.")
    why: str = Field(description="One or two sentences on why they should meet, citing real profile facts.")
    suggested_club_id: str | None = Field(description="ID from search_clubs that fits both, or null.")


class MatchList(BaseModel):
    matches: list[Match] = Field(description="Best 3-5 matches, best first.")
    clubs_for_me: list[str] = Field(description="1-3 club IDs from search_clubs that fit this student.")


class PhotoCheck(BaseModel):
    ok: bool = Field(description="True if the photo can be used as a profile photo.")
    reason: str = Field(description="Short reason shown to the user.")


class SelfieCheck(BaseModel):
    pose_ok: bool
    same_person: bool
    looks_live: bool
    reason: str = Field(description="One short sentence shown to the user.")


class MessageCheck(BaseModel):
    ok: bool = Field(description="True if the message is fine to send as is.")
    nudge: str = Field(description="If not ok: a short friendly suggestion. Empty if ok.")


@dataclass
class MatchDeps:
    me: dict
    pool: list[dict]                      # already filtered by the guard
    seen_ids: set[str] = field(default_factory=set)


# ---------- agents ----------
intake_agent = Agent(output_type=Profile, instructions=_prompt("intake.md"))
icebreaker_agent = Agent(output_type=str, instructions=_prompt("icebreaker.md"))
photo_agent = Agent(output_type=PhotoCheck, instructions=_prompt("photo_check.md"))
selfie_agent = Agent(output_type=SelfieCheck, instructions=_prompt("selfie_check.md"))
message_agent = Agent(output_type=MessageCheck, instructions=_prompt("message_check.md"))
# The club catalog sits in the fixed instructions so the long prefix is identical on every run (prompt caching).
matcher_agent = Agent(
    deps_type=MatchDeps, output_type=MatchList,
    instructions=_prompt("matcher.md") + "\n\n## Club catalog\n" + json.dumps(CLUBS, indent=1),
)


@matcher_agent.tool
def search_students(ctx: RunContext[MatchDeps], interests: list[str], limit: int = 12) -> list[dict]:
    """Search students who may appear for this user, ranked by overlapping interests and free time.

    Args:
        interests: interest words to look for, e.g. ["climbing", "jazz"].
        limit: max results (up to 15).
    """
    words = [w.lower() for w in interests]
    my_times = set(ctx.deps.me.get("availability", []))

    def score(s: dict) -> float:
        tags = " | ".join(s.get("interests", [])).lower()
        overlap = sum(1 for w in words if w in tags or any(t in w for t in tags.split(" | ")))
        return overlap + 0.3 * len(my_times & set(s.get("availability", [])))

    ranked = sorted(ctx.deps.pool, key=score, reverse=True)[: min(limit, 15)]
    ctx.deps.seen_ids.update(s["id"] for s in ranked)
    return [{**guard.public_view(s), "going_to": going_to(s)} for s in ranked]


@matcher_agent.tool_plain
def search_clubs(tags: list[str]) -> list[dict]:
    """Find Yale clubs and centers whose tags match any of the given words."""
    words = [t.lower() for t in tags]
    return [c for c in CLUBS if any(w in " ".join(c["tags"]) or w in c["name"].lower() for w in words)][:8]


# ---------- logging ----------
def log_usage(agent_name: str, usage, seconds: float) -> dict:
    uncached = usage.input_tokens - usage.cache_read_tokens - usage.cache_write_tokens
    cost = (uncached * PRICES["input_per_1m"] + usage.cache_read_tokens * PRICES["cached_input_per_1m"]
            + usage.cache_write_tokens * PRICES["cache_write_per_1m"]
            + usage.output_tokens * PRICES["output_per_1m"]) / 1_000_000
    row = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "agent": agent_name, "model": MODEL_NAME,
           "requests": usage.requests, "input_tokens": usage.input_tokens,
           "cached_input_tokens": usage.cache_read_tokens, "output_tokens": usage.output_tokens,
           "cost_usd": round(cost, 6), "seconds": round(seconds, 2)}
    LOG_DIR.mkdir(exist_ok=True)
    path = LOG_DIR / "usage.csv"
    new = not path.exists()
    with path.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(row))
        if new:
            w.writeheader()
        w.writerow(row)
    return row


# ---------- pipeline steps called by the app ----------
def build_profile(text: str) -> Profile:
    t = time.time()
    result = intake_agent.run_sync(f"<student_text>\n{text}\n</student_text>", model=_model(), usage_limits=LIMITS)
    log_usage("intake", result.usage, time.time() - t)
    p = result.output
    p.bio = guard.CONTACT.sub("[contact hidden]", p.bio)
    return p


def find_matches(me: dict, mode: Literal["friends", "dating"], level: str = "all", event_id: str | None = None) -> dict:
    """Returns {'matches': [...], 'clubs': [...], 'dropped': n, 'usage': {...}}.

    level: 'all', 'undergrad' or 'grad'. event_id: only people going to that event.
    Both are filtered here in code, before the agent sees anyone.
    """
    blocked = store.blocked_pairs(me["email"])
    # Sample people attached to a demo meetup count as going too, same as the "See who's going" list.
    demo_going = next((set(m.get("demo_going", [])) for m in store.demo_meetups() if m["id"] == event_id), set())
    pool = [s for s in store.all_students()
            if guard.eligible(me, s, mode, blocked) and guard.level_ok(s, level)
            and (not event_id or event_id in s.get("events", []) or s["email"] in demo_going)]
    if not pool:
        return {"matches": [], "clubs": [], "dropped": 0, "usage": None, "pool": []}
    deps = MatchDeps(me=me, pool=pool)
    me_public = {k: me.get(k) for k in ("school", "year", "interests", "availability", "looking_for")}
    me_public["going_to"] = going_to(me)
    level_note = {"all": "everyone", "undergrad": "undergraduates only", "grad": "graduate and professional students only"}
    t = time.time()
    result = matcher_agent.run_sync(
        f"Mode: {mode}\nShowing: {level_note[level]}\nStudent looking for matches:\n{json.dumps(me_public)}",
        deps=deps, model=_model(),
        usage_limits=LIMITS,
    )
    usage = log_usage(f"matcher-{mode}", result.usage, time.time() - t)

    # Hallucination check in code: only keep students the tool really returned and the guard allowed.
    by_id = {s["id"]: s for s in pool}
    kept, dropped = [], 0
    for m in result.output.matches:
        if m.student_id in by_id and m.student_id in deps.seen_ids:
            if m.suggested_club_id not in CLUB_IDS:
                m.suggested_club_id = None
            kept.append({**m.model_dump(), "student": by_id[m.student_id]})
        else:
            dropped += 1
            guard.log_event("invented_match_dropped", m.student_id)
    clubs = [c for c in CLUBS if c["id"] in result.output.clubs_for_me and guard.club_level_ok(c, level)]
    if not kept:  # never say "no one" when eligible people exist: simple ranking, honest reason, no AI text
        kept = fallback_matches(me, pool)
        guard.log_event("matcher_fallback", f"{mode}: agent returned no matches for a pool of {len(pool)}")
    return {"matches": kept, "clubs": clubs, "dropped": dropped, "usage": usage, "pool": pool}


def fallback_matches(me: dict, pool: list[dict], n: int = 3) -> list[dict]:
    mine = {i.lower() for i in me.get("interests", [])}
    my_times = set(me.get("availability", []))

    def score(s):
        return len(mine & {i.lower() for i in s.get("interests", [])}) + 0.5 * len(my_times & set(s.get("availability", [])))

    out = []
    for s in sorted(pool, key=score, reverse=True)[:n]:
        shared = sorted(mine & {i.lower() for i in s.get("interests", [])})
        why = (f"You both like {', '.join(shared)}." if shared else
               f"{s['name'].split()[0]} is into {', '.join(s.get('interests', [])[:2])}. Add more interests to your "
               "profile for better matches.")
        out.append({"student_id": s["id"], "shared_interests": shared, "why": why, "suggested_club_id": None,
                    "student": s})
    return out


def write_icebreaker(me: dict, them: dict, mode: str, why: str) -> str:
    t = time.time()
    prompt = (f"Mode: {mode}\nSender interests: {me.get('interests')}\n"
              f"Recipient: {json.dumps(guard.public_view(them))}\nWhy they matched: {why}")
    result = icebreaker_agent.run_sync(prompt, model=_model(), usage_limits=LIMITS)
    log_usage("icebreaker", result.usage, time.time() - t)
    return guard.CONTACT.sub("[contact hidden]", result.output)


# ---------- safety checks (Bumble-style) ----------
def check_photo(data: bytes, mime: str) -> PhotoCheck:
    t = time.time()
    result = photo_agent.run_sync(["Screen this profile photo.", BinaryContent(data, media_type=mime)],
                                  model=_model(), usage_limits=LIMITS)
    log_usage("photo-check", result.usage, time.time() - t)
    if not result.output.ok:
        guard.log_event("photo_rejected", result.output.reason)
    return result.output


def check_selfie(profile: tuple[str, bytes], selfie: tuple[str, bytes], pose: str) -> SelfieCheck:
    t = time.time()
    result = selfie_agent.run_sync([
        f"POSE they were asked to copy: {pose}", "PROFILE photo:", BinaryContent(profile[1], media_type=profile[0]),
        "SELFIE:", BinaryContent(selfie[1], media_type=selfie[0]),
    ], model=_model(), usage_limits=LIMITS)
    log_usage("selfie-check", result.usage, time.time() - t)
    o = result.output
    guard.log_event("selfie_check", f"pose_ok={o.pose_ok} same={o.same_person} live={o.looks_live}")
    return o


def review_message(text: str) -> MessageCheck:
    t = time.time()
    result = message_agent.run_sync(f"<message>\n{text}\n</message>", model=_model(), usage_limits=LIMITS)
    log_usage("message-check", result.usage, time.time() - t)
    if not result.output.ok:
        guard.log_event("message_nudged", text)
    return result.output


def check_event_post(text: str) -> MessageCheck:
    """Reuses the message reviewer on a student-posted event before anyone sees it."""
    return review_message(text)
