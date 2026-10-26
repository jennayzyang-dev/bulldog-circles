"""Bulldog Circles: find Yale people who share your interests. Run: python app.py, then open http://127.0.0.1:8050"""

import base64
import datetime as dt
import hashlib
import json
import random
import re
import time
from collections import Counter

from dash import ALL, Dash, Input, Output, State, ctx, dcc, html, no_update
from flask import Response

import agents
import guard
import mailer
import store
from config import ROOT, SMTP_PASSWORD, SMTP_USER

FONTS = "https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Playfair+Display:wght@500;600&display=swap"
app = Dash(__name__, title="Bulldog Circles", external_stylesheets=[FONTS])  # falls back to system fonts offline
EMAIL_ON = bool(SMTP_USER and SMTP_PASSWORD)  # real emailed codes; otherwise one-step demo sign-in
EVENTS = json.loads((ROOT / "data" / "events.json").read_text())
EVENT_CATS = list(dict.fromkeys(e["category"] for e in EVENTS))  # Sports, Music festivals, Nightlife & DJ shows, ...


def all_events() -> list[dict]:
    return EVENTS + store.demo_meetups() + store.user_events()


def going_emails(e: dict, students: list[dict]) -> set[str]:
    """Everyone going: students who marked it, plus the sample people attached to a demo meetup."""
    return {s["email"] for s in students if e["id"] in s.get("events", [])} | set(e.get("demo_going", []))


def date_label(e: dict) -> str:
    """'Tonight · 7:00 PM', 'Tomorrow · 8:00 PM', 'Sat, Nov 1 · 10:00 AM', or the event's free-text timing."""
    if not e.get("date"):
        return e.get("when", "")
    d = dt.date.fromisoformat(e["date"])
    days = (d - dt.date.today()).days
    day = "Tonight" if days == 0 else "Tomorrow" if days == 1 else d.strftime("%a, %b %-d")
    t = dt.datetime.strptime(e["time"], "%H:%M").strftime("%-I:%M %p") if e.get("time") else ""
    return f"{day} · {t}" if t else day


def upcoming(e: dict, horizon_days: int = 14) -> bool:
    return bool(e.get("date")) and 0 <= (dt.date.fromisoformat(e["date"]) - dt.date.today()).days <= horizon_days
VIEWS = ["discover", "groups", "crew", "dating", "chats", "profile", "about"]
NAV = [("discover", "Discover"), ("groups", "Events"), ("crew", "Friends"), ("dating", "Dating"), ("chats", "Chats"),
       ("profile", "Profile")]
LEVELS = [{"label": "Everyone", "value": "all"}, {"label": "Undergrads", "value": "undergrad"},
          {"label": "Grad & professional", "value": "grad"}]
POSES = ["a thumbs up", "a peace sign", "your hand on your head", "a wave", "pointing at your ear"]
SCHOOLS = ["Yale College", "School of Management", "Law School", "School of Medicine",
           "Graduate School of Arts and Sciences", "School of Engineering & Applied Science", "School of the Environment",
           "School of Public Health", "School of Architecture", "School of Art", "School of Music", "School of Drama",
           "Divinity School", "Jackson School of Global Affairs", "School of Nursing"]
UNDERGRAD_YEARS = ["first-year", "sophomore", "junior", "senior"]
GRAD_YEARS = ["1st year", "2nd year", "3rd year", "4th year", "5th year+"]


def year_options(school):
    return UNDERGRAD_YEARS if school == "Yale College" else GRAD_YEARS


PROMPTS = ["My ideal Saturday in New Haven", "I'm looking for", "A trip I'm planning", "Green flags I look for",
           "Two truths and a lie", "My simple pleasures", "The way to win me over is", "I geek out on",
           "Best spot on campus", "Together we could", "My most irrational fear", "Typical Sunday"]
TIME_OPTIONS = [{"label": dt.time(h, m).strftime("%-I:%M %p"), "value": f"{h:02d}:{m:02d}"}
                for h in range(6, 24) for m in (0, 30)]
MAX_PHOTO_BYTES = 4_000_000
PHOTO_TYPES = {"image/jpeg", "image/png", "image/webp"}


def icon(name):
    return html.Span(className=f"ic i-{name}", **{"aria-hidden": "true"})


def meta(name, text, cls=""):
    """A small line of event info with a leading icon (date, place, price, people)."""
    return html.P([icon(name), html.Span(text)], className=f"meta {cls}".strip())


def section(view, *children):
    return html.Section(list(children), id=f"view-{view}", className="view", hidden=view != "discover")


def event_picker(picker_id, title):
    """Small clickable event tiles instead of a dropdown. The first row shows; 'Show all' reveals the rest."""
    return html.Div([
        html.Div([html.Span(title, className="pick-title"),
                  html.Button("Show all", id=f"{picker_id}-more", className="linkish small", n_clicks=0)],
                 className="pick-head"),
        html.Div([icon("search"),
                  dcc.Input(id=f"{picker_id}-q", type="search", debounce=0.3, className="input",
                            placeholder="Search events: f1, edm, museum, hiking…")], className="search-wrap small"),
        dcc.RadioItems(id=picker_id, options=[], value="", className="ev-picks",
                       inputClassName="chip-input", labelClassName="ev-pick"),
    ], className="pick-wrap")


def chips(levels_id):
    return dcc.RadioItems(id=levels_id, options=LEVELS, value="all", inline=True, className="chips",
                          inputClassName="chip-input", labelClassName="chip")


app.layout = html.Div([
    dcc.Store(id="token", storage_type="session"),
    # "See who" pop-up lives outside every page so Discover and Events can both open it.
    html.Div([
        html.Div([
            html.Div([
                html.Div([html.H3(id="members-title"), html.P(id="members-sub", className="muted small")]),
                html.Button(icon("x"), id="members-close", className="round", title="Close",
                            **{"aria-label": "Close"}),
            ], className="modal-head"),
            html.Div([html.P(id="members-note"),
                      html.Button("Sign in", id="members-signin", className="btn primary")],
                     id="members-gate", hidden=True),
            html.Div(id="members-panel", className="modal-body", **{"aria-live": "polite"}),
        ], className="modal-card", role="dialog", **{"aria-modal": "true", "aria-labelledby": "members-title"}),
    ], id="members-modal", className="modal", hidden=True),
    dcc.Store(id="view", data="discover"),
    dcc.Store(id="matches", data={}),
    dcc.Store(id="chat-with"),
    dcc.Store(id="pending-msg"),
    dcc.Store(id="pose"),
    dcc.Store(id="going-tick", data=0),
    dcc.Store(id="photo-tick", data=0),
    dcc.Store(id="chat-refresh", data=0),
    dcc.Interval(id="chat-tick", interval=3000, disabled=True),
    dcc.Interval(id="toast-timer", interval=4000, disabled=True, max_intervals=1),
    html.Div(id="toast", className="toast", role="status", hidden=True),

    html.Header([
        html.Button([html.Span("", className="logo-mark"), html.Span("Bulldog Circles", className="logo-text")],
                    id="logo", className="logo", title="Home", **{"aria-label": "Bulldog Circles home"}),
        html.Button("Sign in", id="auth-btn", className="pill"),
    ], className="topbar"),

    html.Main([
        # ---------- Discover ----------
        section("discover",
            html.Div([
                html.Div([
                    html.H1(id="hero-hi"),
                    html.P(id="hero-next", className="hero-next"),
                    html.P(id="hero-matches", className="muted"),
                    html.Div([
                        html.Button([icon("crew"), "Find friends"], id="home-friends", className="btn primary"),
                        html.Button([icon("cal"), "Calendar"], id="home-cal", className="btn ghost"),
                        html.Button([icon("chats"), "Chats"], id="home-chats", className="btn ghost"),
                    ], className="row quick", id="home-quick"),
                    html.Button("Set up my profile →", id="home-setup", className="btn primary", hidden=True),
                ], id="hero-me", hidden=True),
                html.Div([
                    html.Span("For every Yale school · undergrad & grad", className="eyebrow navy"),
                    html.H1("Find something to do — and people to do it with."),
                    html.P("Games, festivals, trips and interest circles across every Yale school. "
                           "See who's going, join in, make friends."),
                    html.Div([
                        html.Button("Get started", id="cta-signin", className="btn primary"),
                        html.Button("Browse events →", id="cta-groups", className="btn ghost"),
                    ], className="row", id="cta-row"),
                    html.Div([html.Span("Popular:", className="muted small")] + [
                        html.Button(label, id={"type": "goto-ev", "index": f"{cat}|hero-{i}"}, className="hero-chip")
                        for i, (label, cat) in enumerate([("Formula 1", "Sports"), ("EDM festivals", "Music festivals"),
                                                          ("DJ nights", "Nightlife & DJ shows"), ("Museums", "Art & culture"),
                                                          ("Day trips", "Day trips & fun")])
                    ], className="hero-chips"),
                ], id="hero-guest"),
                html.Div(id="hero-stats", className="hero-stats"),
            ], className="hero"),
            html.Div(id="discover-events"),
            html.Div(id="discover-preview"),
            html.Div([
                html.Div([html.H2("Find your friends"),
                          html.P("Yale students who share your interests, across every school, picked by AI.",
                                 className="muted")]),
                html.Button("Go to Friends →", id="goto-crew", className="btn primary"),
            ], className="panel crew-teaser"),
        ),

        # ---------- Friends (friend matching; internal view id is still 'crew') ----------
        section("crew",
            html.H2("Find your friends"),
            html.P("Meet Yale students who share your interests. Our AI picks your best matches and tells you why.",
                   className="muted"),
            html.Div([
                html.Div(icon("crew"), className="ce-icon"),
                html.H3("Your AI friend-finder"),
                html.P(id="discover-hint", className="muted"),
                html.Div([
                    html.Div([html.Span(n, className="ce-n"), html.Strong(t), html.Span(d, className="muted small")],
                             className="ce-step")
                    for n, t, d in [("1", "Tell us about you", "A few sentences: interests, free time, what you want."),
                                    ("2", "AI searches every school", "It reads every Yale student's profile for you."),
                                    ("3", "Matches with reasons", "Each pick says why, like “you both love jazz”.")]
                ], className="ce-steps"),
                html.Div(html.Button("Get started", id="crew-signin", className="btn primary"), className="row ce-actions"),
            ], id="crew-gate", className="panel chat-empty"),
            html.Div([
                chips("level"),
                event_picker("crew-event", "Going to something? Find people for it"),
                html.Button("Find my friends", id="search", className="btn primary wide"),
                dcc.Loading(html.Div(id="results", **{"aria-live": "polite"}), color="#00356B"),
            ], id="discover-main", hidden=True),
        ),

        # ---------- Groups ----------
        section("groups",
            html.H2("Events, circles & trips"),
            dcc.Tabs(id="browse-tab", value="calendar", className="seg", children=[
                dcc.Tab(label="Calendar", value="calendar"),
                dcc.Tab(label="Events & trips", value="events"),
                dcc.Tab(label="Interest circles", value="circles"),
                dcc.Tab(label="My events", value="mine"),
            ]),
            html.Div(dcc.RadioItems(id="event-cat", value="All", inline=True, className="chips",
                                    inputClassName="chip-input", labelClassName="chip",
                                    options=["All", *EVENT_CATS]), id="event-cat-box"),
            html.Div([
                html.Button("+ Post an event", id="ev-open", className="btn tiny primary"),
                html.Div([
                    html.H3("Post an event or trip"),
                    html.P("Going somewhere and want company? Post it for other Yale students. "
                           "Every post is checked by AI before it appears.", className="muted"),
                    html.Label("Event name", htmlFor="ev-name"),
                    dcc.Input(id="ev-name", maxLength=80, placeholder="e.g. Knicks vs Celtics", className="input"),
                    html.Label("Category"),
                    dcc.RadioItems(id="ev-cat", options=EVENT_CATS, value="Sports", inline=True, className="chips",
                                   inputClassName="chip-input", labelClassName="chip"),
                    html.Label("Type", htmlFor="ev-sub"),
                    dcc.Input(id="ev-sub", maxLength=40, placeholder="e.g. Basketball, EDM, Zoo", className="input"),
                    html.Div([
                        html.Div([html.Label("Location", htmlFor="ev-where"),
                                  dcc.Input(id="ev-where", maxLength=80, placeholder="e.g. Madison Square Garden, NYC",
                                            className="input")]),
                        html.Div([html.Label("Date", htmlFor="ev-date"),
                                  dcc.Dropdown(id="ev-date", placeholder="Pick a date", className="dd",
                                               clearable=False)]),
                        html.Div([html.Label("Time (optional)", htmlFor="ev-time"),
                                  dcc.Dropdown(id="ev-time", options=TIME_OPTIONS, placeholder="Time",
                                               className="dd")]),
                        html.Div([html.Label("Price", htmlFor="ev-price"),
                                  dcc.Input(id="ev-price", maxLength=30, placeholder="e.g. $45, Free",
                                            className="input")]),
                    ], className="form-grid"),
                    html.Label("Link (optional)", htmlFor="ev-link"),
                    dcc.Input(id="ev-link", type="url", maxLength=300, placeholder="https://…", className="input"),
                    html.Label("Details", htmlFor="ev-details"),
                    dcc.Textarea(id="ev-details", maxLength=500, className="input",
                                 placeholder="e.g. Taking the Metro-North at 4pm, have 2 extra tickets at face value."),
                    html.Button("Post event", id="ev-submit", className="btn primary"),
                    dcc.Loading(html.P(id="ev-msg", role="status", className="msg"), color="#00356B"),
                ], id="ev-form", className="panel", hidden=True),
            ], id="ev-box"),
            chips("club-level"),
            html.Div([icon("search"),
                      dcc.Input(id="club-q", type="search", placeholder="Search: formula 1, edc, jazz, hiking…",
                                className="input")], className="search-wrap"),
            html.P(id="browse-msg", role="status", className="msg"),
            html.Div(id="club-list"),
        ),

        # ---------- Dating ----------
        section("dating",
            html.Div([
                html.Div([
                    html.Div([
                        html.Span("Opt-in dating", className="eyebrow"),
                        html.H2("Dating, the Yale way"),
                        html.P("Opt-in only. Match on shared interests and plans — “also going to Coachella” — "
                               "not just photos.", className="dl-sub"),
                        html.Div([icon("people"), html.Span(id="dating-count")], className="dl-count"),
                        html.P(id="dating-lock-msg", className="lock"),
                        html.Button("Sign in to unlock", id="dating-unlock", className="btn pink"),
                    ], className="dl-copy"),
                    html.Div([  # decorative preview: blurred sample cards, nothing readable until you opt in
                        *[html.Div(html.Img(src=f"/photo/S{n:03d}", alt=""), className=f"dl-card c{i}")
                          for i, n in enumerate((4, 11, 23))],
                        html.Div([icon("heart"), html.Span("Profiles unlock when you turn on dating")],
                                 className="dl-lock"),
                    ], className="dl-preview", **{"aria-hidden": "true"}),
                ], className="dl-hero"),
                html.Div([
                    html.Div([icon(ic), html.Strong(t), html.P(txt)], className="dl-feature")
                    for ic, t, txt in [
                        ("profile", "Private by default", "Hidden until you sign in and turn on dating mode."),
                        ("heart", "Mutual fit only",
                         "Only people who opted in, and whose preferences match yours both ways."),
                        ("check", "Photo-verified", "Photo-verified badges, so you know people are real."),
                        ("chats", "Chat after a yes",
                         "Chat opens only when you both say yes. No emails or numbers shared."),
                        ("block", "Safety built in",
                         "Block, report or unmatch anytime. AI flags hurtful messages before they're sent."),
                        ("crew", "Double Date", "Double Date: pair up with a friend and meet another pair."),
                    ]
                ], className="dl-features"),
            ], id="dating-locked", className="panel dating-panel"),
            html.Div([
                html.H2("Dating"),
                chips("dating-level"),
                event_picker("dating-event", "Only people going to…"),
                dcc.Checklist(id="dating-verified", options=[{"label": " Verified profiles only ✓", "value": "v"}],
                              value=[], className="check"),
                html.Button("Find dates", id="dating-search", className="btn pink wide"),
                dcc.Loading(html.Div(id="dating-results", **{"aria-live": "polite"}), color="#A3456A"),
                html.Div([
                    html.H2("Double Date"),
                    html.P("Pair up with a friend and match with another pair. When both "
                           "pairs like each other, a group chat opens for all four.", className="muted"),
                    html.P(id="dd-status", className="strong"),
                    html.Div([
                        html.Label("Invite a friend to be your duo (people you've connected with)"),
                        dcc.RadioItems(id="dd-friend", options=[], className="chips", inputClassName="chip-input",
                                       labelClassName="chip"),
                        html.Button("Invite to Double Date", id="dd-invite", className="btn tiny primary"),
                    ], id="dd-invite-box"),
                    html.Div([
                        html.Button("Accept invite", id="dd-accept", className="btn tiny primary"),
                        html.Button("Leave duo", id="dd-leave", className="btn tiny ghost"),
                    ], className="row"),
                    html.Button("Find duos", id="dd-search", className="btn pink wide"),
                    html.P(id="dd-msg", role="status", className="msg"),
                    dcc.Loading(html.Div(id="dd-results"), color="#A3456A"),
                    dcc.Store(id="dd-tick", data=0),
                ], className="panel dd-panel"),
            ], id="dating-unlocked", hidden=True),
        ),

        # ---------- Chats ----------
        section("chats",
            html.H2("Chats"),
            html.Div([
                html.Div(icon("chats"), className="ce-icon"),
                html.H3(id="chat-empty-title"),
                html.P(id="chat-empty-sub", className="muted"),
                html.Div([
                    html.Div([html.Span(n, className="ce-n"), html.Strong(t), html.Span(d, className="muted small")],
                             className="ce-step")
                    for n, t, d in [("1", "Find people", "On Friends, Dating or an event's “Who's going”."),
                                    ("2", "Both say yes", "Tap Connect or Interested. A chat opens only if they do too."),
                                    ("3", "Chat safely", "No emails or numbers shared. AI flags hurtful messages.")]
                ], className="ce-steps"),
                html.Div([
                    html.Button("Sign in to start chatting", id="chat-empty-signin", className="btn primary"),
                    html.Button([icon("crew"), "Find friends"], id="chat-empty-friends", className="btn primary"),
                    html.Button([icon("cal"), "Browse events"], id="chat-empty-events", className="btn ghost"),
                ], className="row ce-actions"),
            ], id="chat-empty", className="panel chat-empty", hidden=True),
            html.Div([
                html.Div(id="chat-list", className="chat-list"),
                html.Div([
                    html.Div([
                        html.Div(id="chat-title", className="chat-title"),
                        html.Div([
                            html.Button("Unmatch", id="chat-unmatch", className="btn tiny"),
                            html.Button("Block", id="chat-block", className="btn tiny"),
                            html.Button("Report", id="chat-report", className="btn tiny danger"),
                        ], className="row"),
                    ], id="chat-head", className="chat-head", hidden=True),
                    html.Div(id="chat-messages", className="messages", **{"aria-live": "polite"}),
                    html.Div([
                        html.P(id="chat-nudge-text"),
                        html.Button("Edit message", id="chat-edit", className="btn tiny"),
                        html.Button("Send anyway", id="chat-send-anyway", className="btn tiny ghost"),
                    ], id="chat-nudge", className="nudge", hidden=True),
                    html.Div([
                        dcc.Input(id="chat-input", placeholder="Type a message…", className="input",
                                  maxLength=1000, debounce=False, n_submit=0),
                        html.Button("Send", id="chat-send", className="btn primary"),
                    ], id="chat-compose", className="compose", hidden=True),
                ], className="chat-pane"),
            ], id="chat-wrap", className="chat-wrap"),
        ),

        # ---------- Profile ----------
        section("profile",
            html.Div([
                html.Div([
                    html.Div([
                        html.Span("Welcome back", className="eyebrow navy"),
                        html.H2("Sign in"),
                        html.P("Pick up where you left off: your plans, matches and chats.", className="muted"),
                        html.Label("Yale email", htmlFor="email"),
                        dcc.Input(id="email", type="email", placeholder="netid@yale.edu", className="input"),
                        html.Button("Send code" if EMAIL_ON else "Sign in", id="send-code", className="btn primary wide"),
                        html.Div([
                            html.Label("6-digit code", htmlFor="code"),
                            dcc.Input(id="code", type="text", inputMode="numeric", maxLength=6, className="input"),
                            html.Button("Verify", id="verify", className="btn primary wide"),
                        ], hidden=not EMAIL_ON),
                        html.P(id="login-msg", role="status", className="msg"),
                        html.Div([icon("check"), "Your email is never shown to other students."], className="auth-note"),
                    ], className="auth-card"),
                    html.Div([
                        html.Span("New here?", className="eyebrow light"),
                        html.H2("Create your account"),
                        html.P("Free for anyone with a @yale.edu email. Takes about two minutes.", className="auth-sub"),
                        html.Ul([html.Li([icon(ic), t]) for ic, t in [
                            ("crew", "Meet Yale students who share your interests"),
                            ("cal", "See who's going to tonight's plans and big trips"),
                            ("chats", "Chat once you both say yes. No numbers shared"),
                        ]], className="auth-perks"),
                        html.Label("Yale email", htmlFor="reg-email"),
                        dcc.Input(id="reg-email", type="email", placeholder="netid@yale.edu", className="input"),
                        html.Button("Create my account", id="reg-go", className="btn primary wide"),
                        html.P(id="reg-msg", role="status", className="msg"),
                    ], className="auth-card join"),
                ], className="auth-grid"),
                html.P("Demo mode: no email code needed. A real launch would use Yale NetID login.",
                       className="muted small center", hidden=EMAIL_ON),
            ], id="login-card"),

            html.Div([
                html.Div([
                    html.Img(id="my-photo", className="my-photo", alt="Your profile photo"),
                    html.Div([
                        html.Div(id="my-name", className="my-name"),
                        html.Div(id="my-badge"),
                    ]),
                ], className="me-row"),
                html.Div([
                    html.H3("Welcome to Bulldog Circles"),
                    html.P("Start with a few sentences about you below. Our AI turns them into your profile, then you "
                           "can find friends, join tonight's plans and (if you want) date.", className="muted"),
                ], id="onboard-welcome", className="panel welcome", hidden=True),
                html.Div([
                    html.H3("Next steps"),
                    html.Div([
                        html.Button([html.Strong("Find your friends"), html.Span("People like you across every school",
                                                                              className="muted small")],
                                    id="next-crew", className="next-step"),
                        html.Button([html.Strong("Tonight & this week"), html.Span("Meetups and events to join",
                                                                                   className="muted small")],
                                    id="next-tonight", className="next-step"),
                        html.Button([html.Strong("My events"), html.Span("Plans you're going to or posted",
                                                                         className="muted small")],
                                    id="next-mine", className="next-step"),
                        html.Button([html.Strong(id="next-dating-title"), html.Span(id="next-dating-sub",
                                                                                    className="muted small")],
                                    id="next-dating", className="next-step"),
                    ], className="next-steps"),
                ], id="onboard-next", className="panel", hidden=True),

                html.Div([
                    html.H3("About you"),
                    html.Div([
                        html.Div([html.Label(["Name ", html.Span("Required", className="req")], htmlFor="p-name"),
                                  dcc.Input(id="p-name", maxLength=40, placeholder="e.g. Maya C.", className="input")]),
                        html.Div([html.Label(["School ", html.Span("Required", className="req")], htmlFor="p-school"),
                                  dcc.Dropdown(id="p-school", options=SCHOOLS, placeholder="Choose your school",
                                               searchable=False, className="dd")]),
                        html.Div([html.Label(["Year ", html.Span("Required", className="req")], htmlFor="p-year"),
                                  dcc.Dropdown(id="p-year", options=[], placeholder="Choose school first",
                                               searchable=False, className="dd")]),
                    ], className="field-row"),
                    html.P("Shown on your card. First name and last initial is enough.", className="muted small"),
                    html.Label(["Describe yourself ", html.Span("Required", className="req")], htmlFor="about"),
                    html.P("A few sentences: your interests, when you're free, and what you're looking for.",
                           className="muted small"),
                    dcc.Textarea(id="about", className="input tall",
                                 placeholder="e.g. Into F1, climbing, jazz and ramen. Free weekday evenings. Want a small "
                                             "friend group outside my program."),
                    html.Fieldset([
                        html.Legend("Your prompts"),
                        html.P("Pick up to 3 and answer in your own words. They show on your card.",
                               className="muted small"),
                        *[html.Div([
                            dcc.Dropdown(id=f"prompt-q-{i}", options=PROMPTS, placeholder="Choose a prompt",
                                         className="dd", clearable=True),
                            dcc.Textarea(id=f"prompt-a-{i}", maxLength=150, className="input",
                                         placeholder="Your answer (max 150 characters)"),
                        ], className="prompt-edit") for i in range(3)],
                    ]),
                    html.Div([
                        html.Div([icon("heart"), html.H3("Dating"), html.Span("Optional · off by default",
                                                                              className="muted small")],
                                 className="dating-box-head"),
                        html.P("To use the Dating tab: 1. tick the box below, 2. choose who you are and who you're "
                               "interested in, 3. press Save profile.", className="muted small"),
                        dcc.Checklist(id="dating-open", value=[], className="check big",
                                      options=[{"label": " Turn on dating: show me in Dating", "value": "yes"}]),
                        html.Div([
                            html.Label("I am"),
                            dcc.RadioItems(id="gender", options=["woman", "man", "non-binary"], inline=True,
                                           className="chips", inputClassName="chip-input", labelClassName="chip"),
                            html.Label("Interested in"),
                            dcc.Checklist(id="interested-in", options=["woman", "man", "non-binary"], value=[],
                                          inline=True, className="chips", inputClassName="chip-input",
                                          labelClassName="chip"),
                        ], id="dating-fields", hidden=True),
                    ], id="dating-box", className="dating-box"),
                    html.Button("Save profile", id="save-profile", className="btn primary wide"),
                    dcc.Loading(html.Div(id="profile-view", role="status"), color="#00356B"),
                ], className="panel"),

                html.Div([
                    html.H3("Photo & verification"),
                    html.P("Add a clear photo of yourself. It's screened by AI before anyone can see it.",
                           className="muted"),
                    dcc.Upload(html.Button("Upload photo", className="btn tiny"), id="photo-upload",
                               accept="image/jpeg,image/png,image/webp", max_size=MAX_PHOTO_BYTES),
                    dcc.Loading(html.P(id="photo-msg", role="status", className="msg"), color="#00356B"),
                    html.H3("Get verified ✓", className="sub-h"),
                    html.P("Take a selfie copying a random pose. Our AI checks it's really you and matches your "
                           "profile photo. We delete the selfie right after.", className="muted"),
                    html.Button("Start verification", id="verify-start", className="btn tiny"),
                    html.Div([
                        html.P(id="pose-text", className="pose"),
                        dcc.Upload(html.Button("Upload selfie", className="btn primary"), id="selfie-upload",
                                   accept="image/jpeg,image/png,image/webp", max_size=MAX_PHOTO_BYTES),
                    ], id="pose-box", hidden=True),
                    dcc.Loading(html.P(id="verify-msg", role="status", className="msg"), color="#00356B"),
                ], className="panel soft"),

                html.Div([
                    html.Button("Sign out", id="sign-out", className="btn ghost"),
                    html.Button("Delete my data", id="delete-me", className="btn danger"),
                ], className="row"),
                html.P("We store your profile, photo, matches and chats on this server only. Emails are never "
                       "shown to other users. Delete everything anytime.", className="muted small"),
            ], id="profile-area", hidden=True),
        ),

        # ---------- About & safety (footer link) ----------
        section("about",
            html.H2("How Bulldog Circles works"),
            html.Div([
                html.Div([html.Div("1", className="step-n"), html.H3("Tell us about you"),
                          html.P("Write a few sentences. Our AI turns them into a profile with interests, free time "
                                 "and what you're looking for.", className="muted")], className="step"),
                html.Div([html.Div("2", className="step-n"), html.H3("Find people and plans"),
                          html.P("See tonight's meetups and big events, and who from Yale is going. Our AI matches you "
                                 "with people across every school and explains why.", className="muted")],
                         className="step"),
                html.Div([html.Div("3", className="step-n"), html.H3("Say hi when it's mutual"),
                          html.P("Connect, go to events together, or (if you opt in) date. A chat opens only when "
                                 "both people say yes.", className="muted")], className="step"),
            ], className="steps"),
            html.H2("Safety", className="about-h"),
            html.Div([html.Div([html.H3(t), html.P(d, className="muted")], className="tile") for t, d in [
                ("Yale students only", "Sign-in requires a @yale.edu email."),
                ("Opt-in dating", "Dating is off by default. Only people who opted in, and whose preferences match "
                                  "yours both ways, can see each other."),
                ("Verified profiles", "Photo verification: a selfie copying a random pose is checked by AI against "
                                      "your profile photo. Verified profiles get a ✓."),
                ("Photo screening", "Every photo is checked by AI before anyone can see it. Explicit photos, photos "
                                    "without a person, and photos showing contact info are rejected."),
                ("Review before you send", "Messages that look hurtful or pressuring get a gentle nudge before "
                                           "they're sent."),
                ("Protected AI", "Bios, prompts and event posts that try to give instructions to our AI are blocked, "
                                 "and the AI can only recommend real people and clubs from our data."),
                ("No contact info until you match", "Phone numbers, emails and handles are hidden on profiles and "
                                                    "posts. Talk in the in-app chat first."),
                ("Block, report, unmatch", "Available on every profile and in every chat, including Double Date "
                                           "group chats. Blocked people never see you again."),
            ]], className="tiles"),
            html.H2("Privacy", className="about-h"),
            html.Ul([
                html.Li("We store your profile, photo, plans, matches and chats on this server only."),
                html.Li("Other students never see your email. Selfies used for verification are deleted right away."),
                html.Li("Our AI reads your profile text, never your sign-in email."),
                html.Li("Delete my data (Profile tab) removes your profile, photo, plans, matches, chats and posts."),
            ], className="plain-list"),
            html.H2("Good to know", className="about-h"),
            html.Ul([
                html.Li("Profiles marked “demo” and meetups marked “Demo meetup” are sample data so you can try "
                        "every feature."),
                html.Li("Demo mode signs you in with any @yale.edu address without an email code. A real launch "
                        "would use Yale NetID sign-in."),
            ], className="plain-list"),
        ),
    ], className="content"),

    html.Footer([
        html.Div([html.Span("Bulldog Circles", className="logo-text"),
                  html.P("Find something to do, and people to do it with.", className="muted small")]),
        html.Div([
            html.Button("How it works", id="foot-about", className="linkish"),
            html.Button("Safety & privacy", id="foot-safety", className="linkish"),
        ], className="foot-links"),
    ], className="site-footer"),

    html.Nav([html.Button([html.Span(className=f"nav-icon i-{v}", **{"aria-hidden": "true"}), html.Span(label)], id=f"nav-{v}",
                          className="nav-btn") for v, label in NAV], className="bottomnav"),
], className="app")


# ---------- helpers ----------
def me_from(token):
    email = store.session_email(token)  # the server decides who you are; the browser only holds a random token
    return (email, store.get_profile(email)) if email else (None, None)


AI_BUSY = "Our AI is busy right now. Please try again in a moment."


class AIUnavailable(Exception):
    """An AI call failed (network, rate limit, usage cap). Callbacks show AI_BUSY instead of stalling."""


def ai(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except Exception as exc:  # any provider/network/limit error
        guard.log_event("ai_error", f"{fn.__name__}: {type(exc).__name__}: {exc}")
        raise AIUnavailable from exc


def student_by_id(sid):
    return next((s for s in store.all_students() if s["id"] == sid), None)


def people(level, me_email=None):
    """Everyone browsable at this level, minus me and anyone in a block with me."""
    blocked = store.blocked_pairs(me_email) if me_email else set()
    return [s for s in store.all_students()
            if guard.level_ok(s, level) and s.get("email") != me_email and s.get("email") not in blocked]


def nice(interest: str) -> str:
    """Display label for a lowercase interest tag: 'rock climbing' -> 'Rock Climbing', 'ai' -> 'AI'."""
    return {"ai": "AI", "k-pop": "K-pop", "reading sci-fi": "Reading Sci-Fi"}.get(interest, interest.title())


def hits(words, *texts):
    blob = " ".join(texts).lower()
    return not words or any(w in blob for w in words)


def photo_url(sid, tick=0):
    return f"/photo/{sid}?v={tick}"


def decode_upload(contents):
    """'data:image/png;base64,...' -> (mime, bytes) or an error string."""
    try:
        head, b64 = contents.split(",", 1)
        mime = head.split(":")[1].split(";")[0]
        data = base64.b64decode(b64)
    except (ValueError, IndexError):
        return "Couldn't read that file."
    if mime not in PHOTO_TYPES:
        return "Please upload a JPG, PNG or WEBP photo."
    if len(data) > MAX_PHOTO_BYTES:
        return "That photo is too big (max 4 MB)."
    return mime, data


def ics_text(s: str) -> str:
    return re.sub(r"([,;\\])", r"\\\1", s or "").replace("\n", "\\n")


@app.server.route("/ics/<event_id>")
def event_ics(event_id):
    """'Add to calendar': a standard .ics file that Apple Calendar, Google Calendar and Outlook all open."""
    e = next((e for e in all_events() if e["id"] == event_id and e.get("date")), None)
    if not e:
        return Response("Event not found", status=404)
    start = dt.datetime.fromisoformat(f"{e['date']}T{e.get('time') or '19:00'}")
    fmt = lambda d: d.strftime("%Y%m%dT%H%M%S")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Bulldog Circles//EN", "BEGIN:VEVENT",
             f"UID:{e['id']}-{e['date']}@bulldog-circles", f"DTSTAMP:{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
             f"DTSTART;TZID=America/New_York:{fmt(start)}", f"DTEND;TZID=America/New_York:{fmt(start + dt.timedelta(hours=2))}",
             f"SUMMARY:{ics_text(e['name'])}", f"LOCATION:{ics_text(e['where'])}",
             f"DESCRIPTION:{ics_text(' · '.join(x for x in (e.get('details'), e.get('price')) if x) + ' (from Bulldog Circles)')}",
             "END:VEVENT", "END:VCALENDAR"]
    name = re.sub(r"[^A-Za-z0-9]+", "-", e["name"]).strip("-")[:40] or "event"
    return Response("\r\n".join(lines) + "\r\n", mimetype="text/calendar",
                    headers={"Content-Disposition": f'attachment; filename="{name}.ics"'})


@app.server.route("/photo/<sid>")
def photo(sid):
    """Real uploaded photo if there is one, otherwise a colored initials avatar."""
    found = store.get_photo(sid)
    if found:
        return Response(found[1], mimetype=found[0])
    s = student_by_id(sid)
    name = s["name"] if s else "?"
    initials = "".join(w[0] for w in name.replace(".", "").split()[:2]).upper()
    hue = int(hashlib.md5(sid.encode()).hexdigest(), 16) % 360
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 300 300">'
           f'<rect width="300" height="300" fill="hsl({hue},22%,90%)"/>'
           f'<text x="150" y="178" font-family="Georgia,serif" font-size="104" fill="hsl({hue},25%,38%)" '
           f'text-anchor="middle">{initials}</text></svg>')
    return Response(svg, mimetype="image/svg+xml")


# ---------- navigation ----------
@app.callback(Output("view", "data"),
              *[Input(f"nav-{v}", "n_clicks") for v, _label in NAV],
              Input("cta-signin", "n_clicks"), Input("cta-groups", "n_clicks"), Input("auth-btn", "n_clicks"),
              Input("logo", "n_clicks"), prevent_initial_call=True)
def navigate(*_):
    trig = ctx.triggered_id
    if trig == "logo":
        return "discover"
    if trig in ("cta-signin", "auth-btn"):
        return "profile"
    if trig == "cta-groups":
        return "groups"
    return trig.removeprefix("nav-")


@app.callback(*[Output(f"view-{v}", "hidden") for v in VIEWS], *[Output(f"nav-{v}", "className") for v, _l in NAV],
              Output("chat-tick", "disabled"), Input("view", "data"))
def show_view(view):
    return (*[v != view for v in VIEWS], *["nav-btn active" if v == view else "nav-btn" for v, _l in NAV],
            view != "chats")


@app.callback(Output("view", "data", allow_duplicate=True), Input("foot-about", "n_clicks"),
              Input("foot-safety", "n_clicks"), prevent_initial_call=True)
def footer_nav(*_):
    return "about"


@app.callback(Output("hero-stats", "children"), Input("going-tick", "data"), Input("token", "data"))
def hero_stats(_tick, _token):
    """Live numbers for the landing page, straight from the data."""
    students = store.all_students()
    events = all_events()
    today = dt.date.today().isoformat()
    stats = [(sum(e.get("date") == today for e in events), "meetups tonight"),
             (sum(upcoming(e, 7) for e in events), "plans this week"),
             (len(EVENTS), "big events & trips"),
             (len(students), "Yale students")]
    return [html.Div("This week at Yale", className="prompt-q"),
            *[html.Div([html.Span(str(n), className="stat-n"), html.Span(label, className="muted")], className="stat")
              for n, label in stats]]


@app.callback(Output("hero-guest", "hidden"), Output("hero-me", "hidden"), Output("hero-hi", "children"),
              Output("hero-next", "children"), Output("hero-matches", "children"), Output("home-quick", "hidden"),
              Output("home-setup", "hidden"),
              Input("token", "data"), Input("going-tick", "data"), Input("view", "data"))
def hero_personal(token, _tick, _view):
    """Guests see the welcome banner; signed-in users get 'Hi <name>, here's your week'. Plain Python, no AI."""
    email, me = me_from(token)
    if not email:
        return False, True, None, None, None, True, True
    if not me:
        return (True, False, "Welcome! Let's set up your profile.",
                "Two minutes: your name, school and a few sentences. Then our AI finds your friends.", None, True, False)
    first = me.get("name", "").split()[0] if me.get("name") else "there"
    mine = sorted((e for e in all_events() if e["id"] in set(me.get("events", [])) and upcoming(e, 7)),
                  key=lambda e: (e["date"], e.get("time") or ""))
    nxt = ([html.Span("Your next plan: ", className="muted"), html.Strong(mine[0]["name"]), f" · {date_label(mine[0])}"]
           if mine else [html.Span("No plans this week yet. ", className="muted"),
                         "Pick one from the calendar and see who's going."])
    n = len(store.matches_of(email))
    matches = f"You have {n} match{'' if n == 1 else 'es'}." if n else "No matches yet. Find friends to get started."
    return True, False, f"Hi {first}, here's your week", nxt, matches, False, True


@app.callback(Output("view", "data", allow_duplicate=True), Output("browse-tab", "value", allow_duplicate=True),
              Input("home-friends", "n_clicks"), Input("home-cal", "n_clicks"), Input("home-chats", "n_clicks"),
              Input("home-setup", "n_clicks"), prevent_initial_call=True)
def home_quick(*clicks):
    if not ctx.triggered_id or not any(clicks):
        return no_update, no_update
    return {"home-friends": ("crew", no_update), "home-cal": ("groups", "calendar"),
            "home-chats": ("chats", no_update), "home-setup": ("profile", no_update)}[ctx.triggered_id]


# ---------- sign in / out ----------
@app.callback(Output("login-msg", "children"), Output("token", "data"), Output("reg-msg", "children"),
              Input("send-code", "n_clicks"), Input("verify", "n_clicks"), Input("email", "n_submit"),
              Input("reg-go", "n_clicks"), Input("reg-email", "n_submit"),
              State("email", "value"), State("code", "value"), State("reg-email", "value"), prevent_initial_call=True)
def login(_send, _verify, _enter, _reg, _reg_enter, email, code, reg_email):
    """Sign in (left) is for existing accounts; Create account (right) starts a new one. Both need a Yale email."""
    joining = ctx.triggered_id in ("reg-go", "reg-email")
    email = ((reg_email if joining else email) or "").strip().lower()
    say = (lambda m: (no_update, no_update, m)) if joining else (lambda m: (m, no_update, None))
    if not guard.is_yale_email(email):
        return say("Please use a @yale.edu email address.")
    if not joining and ctx.triggered_id != "verify" and not store.get_profile(email):
        return say("We don't have an account for that email yet. Create one on the right →")
    if EMAIL_ON and ctx.triggered_id != "verify":
        mailer.send_code(email, store.new_code(email))
        return (no_update, no_update, f"We emailed a code to {email}. Enter it under Sign in.") if joining else \
            (f"We emailed a code to {email}. It expires in 10 minutes.", no_update, None)
    if EMAIL_ON and not store.check_code(email, code or ""):
        return "That code is wrong or expired. Send a new one.", no_update, None
    return None, store.create_session(email), None


@app.callback(Output("token", "data", allow_duplicate=True), Input("view", "data"), State("token", "data"),
              prevent_initial_call=True)
def drop_stale_token(_view, token):
    """If the browser holds a sign-in the server no longer accepts (expired, deleted), switch the whole page to
    signed-out instead of showing a mix of signed-in and signed-out parts."""
    if token and not store.session_email(token):
        return None
    return no_update


@app.callback(Output("token", "data", allow_duplicate=True), Output("view", "data", allow_duplicate=True),
              Input("sign-out", "n_clicks"), State("token", "data"), prevent_initial_call=True)
def sign_out(_, token):
    store.end_session(token)
    return None, "discover"


@app.callback(Output("token", "data", allow_duplicate=True), Output("login-msg", "children", allow_duplicate=True),
              Input("delete-me", "n_clicks"), State("token", "data"), prevent_initial_call=True)
def delete_me(_, token):
    email, _p = me_from(token)
    if email:
        store.delete_user(email)  # also removes their sessions
    return None, "Your profile, photo, matches and chats were deleted."


@app.callback(Output("crew-gate", "hidden"), Output("crew-signin", "children"),
              Output("auth-btn", "children"), Output("cta-row", "hidden"), Output("discover-main", "hidden"),
              Output("discover-hint", "children"), Output("login-card", "hidden"), Output("profile-area", "hidden"),
              Output("my-photo", "src"), Output("my-name", "children"), Output("my-badge", "children"),
              Output("profile-view", "children", allow_duplicate=True),
              Input("token", "data"), Input("photo-tick", "data"), prevent_initial_call="initial_duplicate")
def auth_state(token, tick):
    email, me = me_from(token)
    if not email:
        return (False, "Sign in to get started", "Sign in", False, True,
                "Sign in with your Yale email and write a few sentences about you. Then our AI finds your friends.",
                False, True, None, None, None, no_update)
    sid = store.student_id(email)
    badge = (html.Span("✓ Verified", className="badge ok") if me and me.get("verified")
             else html.Span("Not verified yet", className="badge"))
    name = me["name"] if me else email
    hint = None if me else "Write a few sentences about you in your Profile first. Then our AI can find your friends."
    pv = profile_view(me) if ctx.triggered_id == "token" else no_update
    return (me is not None, "Set up my profile", [html.Img(src=photo_url(sid, tick), className="mini-avatar"), "Me"],
            True, me is None, hint,
            True, False, photo_url(sid, tick), name, badge, pv)


@app.callback(Output("about", "value"), Output("dating-open", "value"), Output("gender", "value"),
              Output("interested-in", "value"), *[Output(f"prompt-{k}-{i}", "value") for i in range(3) for k in ("q", "a")],
              Output("p-name", "value"), Output("p-school", "value"), Output("p-year", "value", allow_duplicate=True),
              Input("token", "data"), prevent_initial_call="initial_duplicate")
def prefill_profile(token):
    """Fills the profile form with what the user saved before, so editing doesn't start from scratch."""
    _email, me = me_from(token)
    if not me:  # new account or signed out: start empty, never show the previous person's answers
        return "", [], None, [], *[None, ""] * 3, "", None, None
    prompts = (me.get("prompts") or []) + [{"q": None, "a": None}] * 3
    flat = [v for pr in prompts[:3] for v in (pr["q"], pr["a"] or "")]
    return (me.get("about", ""), ["yes"] if me.get("dating_open") else [], me.get("gender"),
            me.get("dating_interested_in", []), *flat, me.get("name", ""),
            me.get("school") if me.get("school") in SCHOOLS else None,
            me.get("year") if me.get("year") in year_options(me.get("school")) else None)


@app.callback(Output("p-year", "options"), Output("p-year", "placeholder"), Output("p-year", "value"),
              Input("p-school", "value"), State("p-year", "value"), State("token", "data"))
def year_choices(school, year, token):
    """Yale College uses first-year…senior; grad and professional schools use 1st year, 2nd year…
    Keeps the saved year when the page loads (the school is filled in first, then this runs)."""
    if not school:
        return [], "Choose school first", None
    opts = year_options(school)
    if year not in opts:
        _email, me = me_from(token)
        year = (me or {}).get("year") if (me or {}).get("school") == school else None
    return opts, "Choose your year", year if year in opts else None


@app.callback(Output("results", "children", allow_duplicate=True),
              Output("dating-results", "children", allow_duplicate=True),
              Output("dd-results", "children", allow_duplicate=True),
              Output("matches", "data", allow_duplicate=True), Output("chat-with", "data", allow_duplicate=True),
              Output("pending-msg", "data", allow_duplicate=True), Output("chat-input", "value", allow_duplicate=True),
              Output("ev-name", "value"), Output("ev-sub", "value"), Output("ev-where", "value"),
              Output("ev-price", "value"), Output("ev-link", "value"), Output("ev-details", "value"),
              Output("crew-event", "value"), Output("dating-event", "value"),
              Output("crew-event-q", "value"), Output("dating-event-q", "value"),
              Input("token", "data"), prevent_initial_call=True)
def reset_on_account_change(_token):
    """Signing in, out or as someone else wipes the last person's search results, chat and half-typed forms."""
    return [None, None, None, {}, None, None, ""] + [""] * 6 + ["", "", "", ""]


@app.callback(Output("onboard-welcome", "hidden"), Output("onboard-next", "hidden"),
              Output("next-dating-title", "children"), Output("next-dating-sub", "children"),
              Input("token", "data"), Input("photo-tick", "data"))
def onboard(token, _tick):
    """New users get told where to start; once the profile exists, show next steps."""
    email, me = me_from(token)
    if not email:
        return True, True, no_update, no_update
    if not me:
        return False, True, no_update, no_update
    if me.get("dating_open"):
        return True, False, "Dating", "1:1 matches and Double Date"
    return True, False, "Dating (optional)", "Tap to turn it on"


@app.callback(Output("view", "data", allow_duplicate=True), Output("browse-tab", "value", allow_duplicate=True),
              Input("next-crew", "n_clicks"), Input("next-tonight", "n_clicks"), Input("next-dating", "n_clicks"),
              Input("next-mine", "n_clicks"), State("token", "data"), prevent_initial_call=True)
def next_step(*args):
    *clicks, token = args
    if not ctx.triggered_id or not any(clicks):
        return no_update, no_update
    if ctx.triggered_id == "next-tonight":
        return "groups", "calendar"
    if ctx.triggered_id == "next-mine":
        return "groups", "mine"
    if ctx.triggered_id == "next-dating":
        _email, me = me_from(token)
        return ("dating" if me and me.get("dating_open") else no_update), no_update  # off: scroll to the box below
    return "crew", no_update


@app.callback(Output("view", "data", allow_duplicate=True), Input("goto-crew", "n_clicks"),
              Input("crew-signin", "n_clicks"), prevent_initial_call=True)
def goto_crew(*_):
    return "crew" if ctx.triggered_id == "goto-crew" else "profile"


# ---------- profile ----------
def profile_view(p):
    if not p:
        return None
    return html.Div([
        html.P([html.Strong(p["name"]), f" · {p['school']} · {p['year']}"]),
        html.P([html.Span(i, className="tag") for i in p["interests"]]),
        html.P("Free: " + (", ".join(p["availability"]) or "not set"), className="muted"),
        html.P(f"Looking for: {p['looking_for']}", className="muted"),
        html.P("Dating: " + (f"on · {(p.get('gender') or '').capitalize()}, interested in "
                             f"{', '.join(p.get('dating_interested_in') or [])}" if p.get("dating_open") else "off"),
               className="muted"),
        *[html.Div([html.Div(pr["q"], className="prompt-q"), html.Div(pr["a"], className="prompt-a")],
                   className="prompt") for pr in p.get("prompts", [])],
    ], className="profile-summary")


@app.callback(Output("profile-view", "children"), Output("photo-tick", "data", allow_duplicate=True),
              Output("toast", "children"), Output("toast", "className"), Output("toast", "hidden"),
              Output("toast-timer", "disabled"), Output("toast-timer", "n_intervals"),
              Input("save-profile", "n_clicks"),
              State("token", "data"), State("about", "value"), State("dating-open", "value"),
              State("gender", "value"), State("interested-in", "value"), State("photo-tick", "data"),
              *[State(f"prompt-{k}-{i}", "value") for i in range(3) for k in ("q", "a")],
              State("p-name", "value"), State("p-school", "value"), State("p-year", "value"),
              prevent_initial_call=True)
def save_profile(_, token, about, dating_open, gender, interested_in, tick, *rest):
    *prompt_values, name, school, year = rest
    def fail(msg):  # nothing saved: red pop-up, and the message also stays under the button
        return html.P(msg, className="msg err"), no_update, msg, "toast err", False, False, 0

    email, old = me_from(token)
    if not email:
        return fail("Please sign in again.")
    name = " ".join((name or "").split())
    if len(name) < 2:
        return fail("Not saved: please add your name (first name and last initial is enough).")
    if guard.INJECTION.search(name) or guard.CONTACT.search(name):
        return fail("Not saved: please use just your name in the Name field.")
    if school not in SCHOOLS or year not in year_options(school):
        return fail("Not saved: please choose your school and year.")
    if not about or len(about.strip()) < 20:
        return fail("Not saved: write at least one sentence in “Describe yourself” (20+ characters).")
    if len(about) > 1500:
        return fail("Not saved: please keep “Describe yourself” under 1500 characters.")
    if dating_open and not (gender and interested_in):
        return fail("Not saved: to turn on dating, choose “I am” and “Interested in” (or untick dating).")
    prompts = [{"q": q, "a": a.strip()[:150]} for q, a in zip(prompt_values[::2], prompt_values[1::2])
               if q in PROMPTS and a and a.strip()]
    prompt_text = " | ".join(p["a"] for p in prompts)
    if prompt_text and guard.INJECTION.search(prompt_text):
        guard.log_event("injection_blocked", f"prompt by {email}: {prompt_text}")
        return fail("Not saved: one of your prompt answers can't be used. Please rewrite it.")
    try:
        check = ai(agents.review_message, prompt_text) if prompt_text else None
        built = ai(agents.build_profile, about)
    except AIUnavailable:
        return fail(AI_BUSY + " Your profile was not saved.")  # the safety check can't be skipped
    if check and not check.ok:
        return fail(f"Not saved: please rewrite a prompt answer. {check.nudge}")
    if not built.interests:
        return fail("Not saved: we couldn't find any interests in your description. Mention a few things you like "
                    "(e.g. hiking, jazz, startups).")
    for pr in prompts:
        pr["a"] = guard.CONTACT.sub("[contact hidden]", pr["a"])  # contact details go in chat, after a match
    dating = bool(dating_open)
    p = built.model_dump()
    old = old or {}
    p.update(name=name, school=school, year=year,  # the form wins over what the AI guessed from the text
             about=about.strip(), email=email, gender=gender, dating_open=dating,
             dating_interested_in=interested_in if dating else [],
             events=old.get("events", []), verified=old.get("verified", False), prompts=prompts)  # keep earlier state
    store.save_profile(email, p)
    msg = "Profile saved ✓" + (" Dating is unlocked." if dating and not old.get("dating_open") else "")
    return profile_view(p), tick + 1, msg, "toast ok", False, False, 0  # tick refreshes name, badge and Discover


@app.callback(Output("toast", "hidden", allow_duplicate=True), Output("toast-timer", "disabled", allow_duplicate=True),
              Input("toast-timer", "n_intervals"), prevent_initial_call=True)
def hide_toast(n):
    return (True, True) if n else (no_update, no_update)


@app.callback(Output("photo-msg", "children"), Output("photo-tick", "data"),
              Input("photo-upload", "contents"), State("token", "data"), State("photo-tick", "data"),
              prevent_initial_call=True)
def upload_photo(contents, token, tick):
    email, me = me_from(token)
    if not email or not contents:
        return "Please sign in first.", no_update
    if not me:
        return "Save your profile first, then add a photo.", no_update
    up = decode_upload(contents)
    if isinstance(up, str):
        return up, no_update
    try:
        check = ai(agents.check_photo, up[1], up[0])  # screened before anyone can see it
    except AIUnavailable:
        return AI_BUSY + " (Your photo was not saved.)", no_update
    if not check.ok:
        return f"Photo not accepted: {check.reason}", no_update
    store.save_photo(store.student_id(email), *up)
    if me.get("verified"):
        me["verified"] = False  # new photo means verifying again
        store.save_profile(email, me)
    return "Photo approved. It's now on your profile.", tick + 1


@app.callback(Output("pose", "data"), Output("pose-text", "children"), Output("pose-box", "hidden"),
              Output("verify-msg", "children"), Input("verify-start", "n_clicks"), State("token", "data"),
              prevent_initial_call=True)
def start_verify(_, token):
    email, _me = me_from(token)
    if not email or not store.get_photo(store.student_id(email)):
        return no_update, no_update, True, "Upload a profile photo first."
    pose = random.choice(POSES)
    return pose, f"Take a selfie showing your face and {pose}.", False, None


@app.callback(Output("verify-msg", "children", allow_duplicate=True), Output("pose-box", "hidden", allow_duplicate=True),
              Output("photo-tick", "data", allow_duplicate=True),
              Input("selfie-upload", "contents"), State("token", "data"), State("pose", "data"),
              State("photo-tick", "data"), prevent_initial_call=True)
def check_selfie(contents, token, pose, tick):
    email, me = me_from(token)
    profile_photo = store.get_photo(store.student_id(email)) if email else None
    if not me or not profile_photo or not pose or not contents:
        return "Start verification first.", no_update, no_update
    up = decode_upload(contents)
    if isinstance(up, str):
        return up, no_update, no_update
    try:
        r = ai(agents.check_selfie, profile_photo, up, pose)  # the selfie itself is never saved
    except AIUnavailable:
        return AI_BUSY, no_update, no_update
    if r.pose_ok and r.same_person and r.looks_live:
        me["verified"] = True
        store.save_profile(email, me)
        return "You're verified! Your profile now shows a ✓ badge.", True, tick + 1
    return f"Not verified: {r.reason} Try again with a new pose.", True, no_update


# ---------- profile cards (Hinge-style) ----------
def person_card(m, mode, where):
    s, sid = m["student"], m["student_id"]
    key = f"{where}|{sid}"
    club = next((c["name"] for c in agents.CLUBS if c["id"] == m.get("suggested_club_id")), None)
    going = agents.going_to(s)
    return html.Article([
        html.Div([
            html.Div([
                html.Div([html.Span(s["name"], className="pc-name"),
                          html.Span("✓", className="vbadge", title="Photo verified") if s.get("verified") else None]),
                html.Div(" · ".join(x for x in ((s.get("gender") or "").capitalize(),
                                                s["school"], s.get("year", "")) if x), className="pc-sub"),
            ], className="pc-overlay"),
            html.Span("demo profile", className="demo-tag") if s.get("demo") else None,
        ], className="pc-photo", style={"backgroundImage": f"url({photo_url(sid)})"}),
        html.Div([
            html.Div([html.Div("Why you match", className="prompt-q"), html.Div(m["why"], className="prompt-a")],
                     className="prompt"),
            html.Div([html.Span(t, className="tag") for t in m["shared_interests"]], className="tags"),
            *[html.Div([html.Div(pr["q"], className="prompt-q"), html.Div(pr["a"], className="prompt-a")],
                       className="prompt") for pr in guard.public_view(s)["prompts"][:2]],
            html.Div([html.Div("Going to", className="prompt-q"), html.Div(", ".join(going), className="prompt-a")],
                     className="prompt") if going else None,
            html.Div([html.Div("Try together", className="prompt-q"), html.Div(club, className="prompt-a")],
                     className="prompt") if club else None,
        ], className="pc-body"),
        html.Div([
            html.Button(icon("send"), id={"type": "hi", "index": key}, className="round", title="Draft a hello",
                        **{"aria-label": f"Draft a hello to {s['name']}"}),
            html.Button(icon("heart" if mode == "dating" else "connect"), id={"type": "like", "index": key},
                        className="round big pink" if mode == "dating" else "round big",
                        title="I'm interested" if mode == "dating" else "Connect",
                        **{"aria-label": f"{'Interested in' if mode == 'dating' else 'Connect with'} {s['name']}"}),
            html.Button(icon("block"), id={"type": "block", "index": key}, className="round", title="Block",
                        **{"aria-label": f"Block {s['name']}"}),
            html.Button(icon("flag"), id={"type": "report", "index": key}, className="round danger", title="Report",
                        **{"aria-label": f"Report {s['name']}"}),
        ], className="pc-actions"),
        html.Div(id={"type": "card-msg", "index": key}, role="status", className="card-msg"),
    ], className="pcard dating-card" if mode == "dating" else "pcard")


def with_matches(store_data, where, mode, items):
    data = dict(store_data or {})
    data[where] = {"mode": mode, "by_id": {m["student_id"]: m for m in items}}
    return data


# ---------- discover ----------
@app.callback(Output("discover-preview", "children"), Input("token", "data"), Input("going-tick", "data"))
def discover_preview(token, _tick):
    """Popular interest circles (counts only, no profiles), shown after events."""
    email, me = me_from(token)
    counts = Counter(i.lower() for s in people("all", email) for i in s.get("interests", []))
    mine = {i.lower() for i in (me or {}).get("interests", [])}
    tiles = [html.Button([html.H3(nice(i)), html.P(f"{n} Yale students · See who →", className="muted"),
                          html.Span("You're into this too", className="tag yellow") if i in mine else None],
                         id={"type": "members", "index": f"i:{i}|home"}, className="tile tile-btn")
             for i, n in counts.most_common(8)]
    return [html.Div([html.H2("Popular circles"),
                      html.Button("All circles →", id={"type": "goto-ev", "index": "circles|all"}, className="linkish")],
                     className="section-head"),
            html.Div(tiles, className="tiles")]


@app.callback(Output("results", "children"), Output("matches", "data"),
              Input("search", "n_clicks"), State("token", "data"), State("level", "value"), State("crew-event", "value"),
              State("matches", "data"), prevent_initial_call=True)
def search(_, token, level, event_id, mdata):
    email, me = me_from(token)
    if not me:
        return "Set up your profile first (Profile tab).", no_update
    try:
        r = ai(agents.find_matches, me, "friends", level, event_id or None)
    except AIUnavailable:
        return AI_BUSY, no_update
    found = r["matches"]
    picked = {m["student_id"] for m in found}
    # More people beyond the AI's picks, ranked by shared interests and free time. No AI call.
    rest = agents.fallback_matches(me, [s for s in r["pool"] if s["id"] not in picked], n=12)
    if not found and not rest:
        return ("No one else is going to that event yet. Clear the event filter or try 'Everyone'." if event_id
                else "No one yet for this filter. Try 'Everyone'."), no_update
    out = []
    if found:
        out += [html.H3("Top picks for you", className="cat-head"),
                html.P("Chosen and explained by our AI.", className="muted small"),
                html.Div([person_card(m, "friends", "discover") for m in found], className="deck")]
    if r["clubs"]:
        out += [html.H3("Groups for you", className="cat-head"),
                html.Div([html.Div([html.Strong(c["name"]), html.Div(c["about"], className="muted")],
                                   className="mini-tile") for c in r["clubs"]], className="tiles")]
    if rest:
        out += [html.H3("More people you might like", className="cat-head"),
                html.Div([person_card(m, "friends", "discover") for m in rest], className="deck")]
    u = r["usage"]
    if u:
        out.append(html.P(f"AI search: {u['seconds']}s · {u['input_tokens']} input ({u['cached_input_tokens']} cached)"
                          f" / {u['output_tokens']} output tokens · ${u['cost_usd']:.4f}", className="muted small"))
    return out, with_matches(mdata, "discover", "friends", found + rest)


def event_tile(e, n, target, note=None):
    """Compact event tile for Discover. `target` says where the button goes: 'calendar' or an events category."""
    return html.Article([
        html.Span(e["sub"], className="tag"),
        html.H3(e["name"]),
        meta("cal", date_label(e), "strong when") if e.get("date") else None,
        meta("pin", e["where"] + ("" if e.get("date") else f" · {e['when']}"), "muted"),
        html.P(note or f"{n} Yale student{'' if n == 1 else 's'} going", className="strong"),
        html.Button("See who's going →", id={"type": "goto-ev", "index": f"{target}|{e['id']}"}, className="btn tiny"),
    ], className="tile event")


@app.callback(Output("discover-events", "children"), Input("token", "data"), Input("going-tick", "data"))
def discover_events(token, _tick):
    """Events first: tonight, your plans, this week, then what's popular. Plain Python, no AI cost."""
    email, me = me_from(token)
    students = store.all_students()
    pool_emails = {s["email"] for s in people("all", email)}
    events = all_events()
    count = {e["id"]: len(going_emails(e, students) & pool_emails) for e in events}
    my_ids = set((me or {}).get("events", []))
    tonight = sorted((e for e in events if e.get("date") == dt.date.today().isoformat()), key=lambda e: e.get("time") or "")
    mine = [e for e in events if e["id"] in my_ids and (not e.get("date") or upcoming(e))]
    shown = {e["id"] for e in tonight + mine}
    week = sorted((e for e in events if e["id"] not in shown and e.get("date") != dt.date.today().isoformat()
                   and upcoming(e, 7)), key=lambda e: (e["date"], e.get("time") or ""))
    shown |= {e["id"] for e in week}
    popular = sorted((e for e in events if e["id"] not in shown and not e.get("date")), key=lambda e: -count[e["id"]])[:4]
    out = []
    if tonight:
        out += [html.Div([html.H2("Happening tonight"),
                          html.Button("Full calendar →", id={"type": "goto-ev", "index": "calendar|all"}, className="linkish")],
                         className="section-head"),
                html.Div([event_tile(e, count[e["id"]], "calendar") for e in tonight[:4]], className="tiles")]
    if mine:
        out += [html.Div([html.H2("Your plans"),
                          html.Button("My events →", id={"type": "goto-ev", "index": "mine|all"}, className="linkish")],
                         className="section-head"),
                html.Div([event_tile(e, count[e["id"]], "mine", f"You + {count[e['id']]} other Yale students")
                          for e in mine[:4]], className="tiles")]
    if week:
        out += [html.Div([html.H2("Happening this week"),
                          html.Button("Full calendar →", id={"type": "goto-ev", "index": "calendar|week"},
                                      className="linkish")],
                         className="section-head"),
                html.Div([event_tile(e, count[e["id"]], "calendar") for e in week[:4]], className="tiles")]
    out += [html.Div([html.H2("Popular with Yale students"),
                      html.Button("All events →", id={"type": "goto-ev", "index": "events|All"}, className="linkish")],
                     className="section-head"),
            html.Div([event_tile(e, count[e["id"]], e["category"]) for e in popular], className="tiles")]
    return out


@app.callback(Output("view", "data", allow_duplicate=True), Output("browse-tab", "value"),
              Output("event-cat", "value"), Input({"type": "goto-ev", "index": ALL}, "n_clicks"),
              prevent_initial_call=True)
def goto_events(_clicks):
    """Every Discover button has a unique ID 'target|event'; target is a tab name or an events category."""
    if not ctx.triggered_id or not ctx.triggered[0]["value"]:
        return no_update, no_update, no_update
    target = ctx.triggered_id["index"].split("|", 1)[0]
    if target in ("calendar", "mine", "circles"):
        return "groups", target, no_update
    return "groups", "events", "All" if target == "events" else target


# ---------- groups ----------
@app.callback(Output("event-cat-box", "hidden"), Input("browse-tab", "value"))
def show_event_cats(tab):
    return tab != "events"


@app.callback(Output("ev-box", "hidden"), Input("browse-tab", "value"))
def show_event_box(tab):
    return tab == "circles"


@app.callback(Output("members-modal", "hidden", allow_duplicate=True),
              Output("browse-msg", "children", allow_duplicate=True),
              Input("browse-tab", "value"), Input("event-cat", "value"), Input("club-level", "value"),
              Input("view", "data"), prevent_initial_call=True)
def clear_members(*_):
    """The 'See who' pop-up closes when you change tab, filter or page (e.g. after tapping Open chat in it)."""
    return True, None


def event_card(e, going, email, my_events):
    """One event tile for the Groups tabs: when/where, price, who's going, and actions."""
    n = len(going - {email})
    mine = e["id"] in my_events
    return html.Article([
        html.Div([html.Span(e["sub"], className="tag"),
                  html.Span("Demo meetup", className="tag outline") if e.get("demo") else
                  html.Span("Posted by a Yale student", className="tag yellow") if e.get("posted") else None],
                 className="tags"),
        html.H3(e["name"]),
        meta("cal", date_label(e), "strong when") if e.get("date") else None,
        meta("pin", e["where"] + ("" if e.get("date") else f" · {e['when']}"), "muted"),
        meta("price", e["price"], "muted") if e.get("price") else None,
        html.P(e["details"], className="small") if e.get("details") else None,
        html.A("More info ↗", href=e["link"], target="_blank", rel="noopener noreferrer nofollow",
               className="small") if e.get("link") else None,
        html.P(f"{'You + ' if mine else ''}{n} other Yale student{'' if n == 1 else 's'} going", className="strong"),
        html.Div([
            html.Button("Going ✓" if mine else "I'm going", id={"type": "go", "index": e["id"]},
                        className="btn tiny primary" if mine else "btn tiny"),
            html.Button("See who's going", id={"type": "members", "index": f"e:{e['id']}"}, className="btn tiny ghost"),
            html.A([icon("cal"), "Add to calendar"], href=f"/ics/{e['id']}", className="cal-add")
            if e.get("date") else None,
            html.Button("Delete", id={"type": "del-ev", "index": e["id"]}, className="btn tiny danger")
            if email and e.get("creator") == email and not e.get("demo") else None,
        ], className="row"),
    ], className="tile event")


CAT_CLASS = {"Sports": "c-sports", "Music festivals": "c-music", "Concerts & shows": "c-music",
             "Nightlife & DJ shows": "c-night", "Art & culture": "c-art", "Day trips & fun": "c-fun"}


def cal_box(e, going, email, my_events):
    """One small event box inside a day column: time, name, place, who's going and two quick buttons."""
    n = len(going - {email})
    mine = e["id"] in my_events
    t = dt.datetime.strptime(e["time"], "%H:%M").strftime("%-I:%M %p") if e.get("time") else "All day"
    return html.Div([
        html.Div([html.Span(t, className="cb-time"), html.Span(e["sub"], className="cb-sub")], className="cb-top"),
        html.Strong(e["name"], className="cb-name"),
        html.Div(e["where"], className="cb-meta"),
        html.Div(" · ".join(x for x in (e.get("price"), f"{'You + ' if mine else ''}{n} going") if x), className="cb-meta"),
        html.Div([
            html.Button("Going ✓" if mine else "I'm going", id={"type": "go", "index": e["id"]},
                        className="btn tiny primary" if mine else "btn tiny"),
            html.Button("Who's going", id={"type": "members", "index": f"e:{e['id']}"}, className="btn tiny ghost"),
            html.A([icon("cal"), "Add to calendar"], href=f"/ics/{e['id']}", className="cal-add", title="Download for "
                   "Apple Calendar, Google Calendar or Outlook"),
            html.Button("Delete", id={"type": "del-ev", "index": e["id"]}, className="btn tiny danger")
            if email and e.get("creator") == email and not e.get("demo") else None,
        ], className="cb-actions"),
    ], className=f"cal-box {CAT_CLASS.get(e['category'], '')}" + (" mine" if mine else ""), title=e.get("details") or None)


def day_column(d, evs, box, today):
    days = (d - today).days
    label = "Today" if days == 0 else "Tomorrow" if days == 1 else d.strftime("%a")
    return html.Div([
        html.Div([html.Span(label, className="dc-dow"), html.Span(d.strftime("%b %-d"), className="dc-date")],
                 className="day-col-head" + (" today" if days == 0 else "")),
        *([box(e) for e in evs] or [html.P("Nothing planned yet.", className="muted small dc-empty")]),
    ], className="day-col")


def calendar_view(events, card, box, count):
    """Like a paper planner: the next 3 days as columns, later days as more columns, then popular big events."""
    today = dt.date.today()
    by_day = {}
    for e in sorted((e for e in events if e.get("date")), key=lambda e: (e["date"], e.get("time") or "")):
        by_day.setdefault(e["date"], []).append(e)
    soon = [today + dt.timedelta(days=i) for i in range(3)]
    out = [html.H2("Upcoming days", className="cal-section first"),
           html.Div([day_column(d, by_day.get(d.isoformat(), []), box, today) for d in soon], className="day-cols")]
    later = [dt.date.fromisoformat(d) for d in by_day if d > soon[-1].isoformat()]
    if later:
        out += [html.H2("Later", className="cal-section"),
                html.Div([day_column(d, by_day[d.isoformat()], box, today) for d in later], className="day-cols later")]
    popular = sorted((e for e in events if not e.get("date")), key=lambda e: -count(e))[:6]
    if popular:
        out += [html.H2("Popular big events", className="cal-section"),
                html.P("No fixed date yet. Tap “I'm going” to find people for them.", className="muted small"),
                html.Div([card(e) for e in popular], className="tiles")]
    return out


@app.callback(Output("club-list", "children"),
              Input("club-q", "value"), Input("club-level", "value"), Input("browse-tab", "value"),
              Input("event-cat", "value"), Input("going-tick", "data"), Input("token", "data"))
def browse(q, level, tab, cat, _tick, token):
    """Browsing is plain Python over the data files. No AI call, so it costs nothing and works for guests."""
    words = [w.strip() for w in (q or "").lower().split(",") if w.strip()]
    email, me = me_from(token)
    pool = people(level, email)
    students = store.all_students()
    pool_emails = {s["email"] for s in pool} | ({email} if email else set())
    my_events = set((me or {}).get("events", []))
    match = lambda e: hits(words, e["name"], e["sub"], e["where"], e.get("details", ""), *e["tags"])
    card = lambda e: event_card(e, going_emails(e, students) & pool_emails, email, my_events)

    if tab == "circles":
        counts = Counter(i.lower() for s in pool for i in s.get("interests", []))
        mine = {i.lower() for i in (me or {}).get("interests", [])}
        items = [html.Article([
            html.H3(nice(interest)), html.P(f"{n} Yale student{'s' if n > 1 else ''}", className="muted"),
            html.Span("You're into this too", className="tag yellow") if interest in mine else None,
            html.Button("See who", id={"type": "members", "index": f"i:{interest}"}, className="btn tiny"),
        ], className="tile") for interest, n in counts.most_common() if hits(words, interest)][:30]
        return html.Div(items, className="tiles") if items else html.P("Nothing found.", className="muted")

    if tab == "calendar":
        events = [e for e in all_events() if (upcoming(e) or not e.get("date")) and match(e)]
        if not events:
            return html.P("Nothing found. Try another word, or post a meetup with “+ Post an event”.", className="muted")
        box = lambda e: cal_box(e, going_emails(e, students) & pool_emails, email, my_events)
        return calendar_view(events, card, box, lambda e: len(going_emails(e, students) & pool_emails))

    if tab == "mine":
        if not email:
            return html.P("Sign in (top right) to see the events you're going to.", className="muted")
        going = [e for e in all_events() if (e["id"] in my_events or e.get("creator") == email) and match(e)]
        if not going:
            return html.P("No plans yet. Tap “I'm going” on any event, or post your own.", className="muted")
        going.sort(key=lambda e: (not e.get("date"), e.get("date") or "", e.get("time") or "", e["name"]))
        return html.Div([card(e) for e in going], className="tiles")

    sections = []
    for category in EVENT_CATS:
        if cat not in (None, "All", category):
            continue
        tiles = [card(e) for e in all_events() if e["category"] == category and match(e)
                 and (not e.get("date") or upcoming(e))]
        if tiles:
            sections.append(html.Div([html.H3(category, className="cat-head"),
                                      html.Div(tiles, className="tiles")]))
    return sections or html.P("Nothing found. Try another word.", className="muted")


@app.callback(Output("going-tick", "data"), Output("browse-msg", "children"),
              Input({"type": "go", "index": ALL}, "n_clicks"), State("token", "data"), State("going-tick", "data"),
              prevent_initial_call=True)
def toggle_going(_clicks, token, tick):
    if not ctx.triggered_id or not ctx.triggered[0]["value"]:
        return no_update, no_update
    email, _p = me_from(token)
    if not email:
        return no_update, "Sign in (top right) to say you're going."
    going = store.toggle_going(email, ctx.triggered_id["index"])
    if going is None:
        return no_update, "Set up your profile first (Profile tab)."
    name = next(e["name"] for e in all_events() if e["id"] == ctx.triggered_id["index"])
    return tick + 1, f"You're going to {name}." if going else f"Removed {name}."


@app.callback(Output("members-panel", "children"), Output("matches", "data", allow_duplicate=True),
              Output("members-modal", "hidden"), Output("members-title", "children"), Output("members-sub", "children"),
              Output("members-gate", "hidden"), Output("members-note", "children"), Output("members-signin", "children"),
              Input({"type": "members", "index": ALL}, "n_clicks"), State("token", "data"),
              State("club-level", "value"), State("matches", "data"), prevent_initial_call=True)
def show_members(_clicks, token, level, mdata):
    """Opens a pop-up with the people in an interest circle or going to an event, as profile cards. No AI call."""
    if not ctx.triggered_id or not ctx.triggered[0]["value"]:
        return (no_update,) * 8
    kind, key = ctx.triggered_id["index"].split("|")[0].split(":", 1)  # "|home" etc. only keeps ids unique
    email, me = me_from(token)
    if kind == "e":
        ev = next((e for e in all_events() if e["id"] == key), None)
        title = ev["name"] if ev else "This event"
        going = going_emails(ev, store.all_students()) if ev else set()
        members = [s for s in people(level, email) if s["email"] in going]
        why, sub = f"Also going to {title}.", "Yale students who said they're going."
    else:
        title = nice(key)
        members = [s for s in people(level, email) if key in (i.lower() for i in s.get("interests", []))]
        why, sub = f"Also into {key}.", f"Yale students who listed {key} as an interest."
    heading = f"{title} · {len(members)} {'person' if len(members) == 1 else 'people'}"
    if not email:  # guests see the count, never the profiles
        return None, no_update, False, heading, sub, False, "Sign in with your Yale email to see who's here.", "Sign in"
    if not me:
        return None, no_update, False, heading, sub, False, "Set up your profile first, then you can see who's here.", \
            "Set up profile"
    mine = {i.lower() for i in me.get("interests", [])}
    cards = [{"student_id": s["id"], "student": s, "why": why, "suggested_club_id": None,
              "shared_interests": sorted(mine & {i.lower() for i in s.get("interests", [])}) or [key]}
             for s in members[:12]]
    if not cards:
        return (html.P(f"No one else yet. Be the first!", className="muted"), no_update, False, heading, sub, True,
                None, no_update)
    faces = [html.Button([html.Img(src=photo_url(m["student_id"]), alt=""),
                          html.Span(m["student"]["name"], className="face-name")],
                         id={"type": "member-face", "index": m["student_id"]}, className="face", n_clicks=0)
             for m in cards]
    details = [html.Div(person_card(m, "friends", "groups"), id={"type": "member-detail", "index": m["student_id"]},
                        className="member-detail", hidden=True) for m in cards]
    body = [html.P("Tap a photo to see their profile. Connect, and when you both say yes, a chat opens.",
                   className="muted small"),
            html.Div(faces, className="faces"), html.Div(details)]
    return body, with_matches(mdata, "groups", "friends", cards), False, heading, sub, True, None, no_update


@app.callback(Output({"type": "member-detail", "index": ALL}, "hidden"),
              Output({"type": "member-face", "index": ALL}, "className"),
              Input({"type": "member-face", "index": ALL}, "n_clicks"), prevent_initial_call=True)
def pick_member(_clicks):
    """Shows one person's full profile under the row of photos; tapping the same photo again closes it."""
    ids = [i["id"]["index"] for i in ctx.outputs_list[0]]
    pick = ctx.triggered_id["index"] if ctx.triggered_id and ctx.triggered[0]["value"] else None
    return [sid != pick for sid in ids], ["face on" if sid == pick else "face" for sid in ids]


@app.callback(Output("members-modal", "hidden", allow_duplicate=True), Input("members-close", "n_clicks"),
              prevent_initial_call=True)
def close_members(_):
    return True


@app.callback(Output("members-modal", "hidden", allow_duplicate=True), Output("view", "data", allow_duplicate=True),
              Input("members-signin", "n_clicks"), prevent_initial_call=True)
def members_signin(_):
    return True, "profile"


def date_options(days: int = 60) -> list[dict]:
    """The next 60 days, built fresh each time the form opens: 'Today · Sun, Oct 25', 'Tomorrow · …', 'Tue, Oct 27'."""
    today = dt.date.today()
    out = []
    for i in range(days):
        d = today + dt.timedelta(days=i)
        label = d.strftime("%a, %b %-d") if i > 1 else f"{'Today' if i == 0 else 'Tomorrow'} · {d.strftime('%a, %b %-d')}"
        out.append({"label": label, "value": d.isoformat()})
    return out


@app.callback(Output("ev-form", "hidden"), Output("ev-date", "options"), Input("ev-open", "n_clicks"),
              State("ev-form", "hidden"), prevent_initial_call=True)
def toggle_event_form(_, hidden):
    return not hidden, date_options()


@app.callback(Output("ev-msg", "children"), Output("going-tick", "data", allow_duplicate=True),
              Output("ev-name", "value", allow_duplicate=True), Output("ev-details", "value", allow_duplicate=True),
              Input("ev-submit", "n_clicks"),
              State("token", "data"), State("ev-name", "value"), State("ev-cat", "value"), State("ev-sub", "value"),
              State("ev-where", "value"), State("ev-date", "value"), State("ev-time", "value"), State("ev-price", "value"),
              State("ev-link", "value"), State("ev-details", "value"), State("going-tick", "data"),
              prevent_initial_call=True)
def post_event(_, token, name, cat, sub, where, date, time_, price, link, details, tick):
    email, me = me_from(token)
    if not email:
        return "Sign in (top right) to post an event.", no_update, no_update, no_update
    if not me:
        return "Set up your profile first (Profile tab).", no_update, no_update, no_update
    e = {"name": (name or "").strip(), "category": cat, "sub": (sub or "").strip() or cat, "where": (where or "").strip(),
         "when": "", "price": (price or "").strip(), "link": (link or "").strip(), "details": (details or "").strip()}
    if not (e["name"] and e["where"] and date):
        return "Please add at least a name, location and date.", no_update, no_update, no_update
    try:
        days_out = (dt.date.fromisoformat(date) - dt.date.today()).days
    except ValueError:
        return "Please pick a valid date.", no_update, no_update, no_update
    if not 0 <= days_out <= 365:
        return "Pick a date between today and one year from now.", no_update, no_update, no_update
    if time_ and not re.fullmatch(r"\d{2}:\d{2}", time_):
        return "Please pick a valid time.", no_update, no_update, no_update
    if cat not in EVENT_CATS:
        return "Pick a category.", no_update, no_update, no_update
    if e["link"] and not e["link"].lower().startswith(("https://", "http://")):
        return "Links must start with https://", no_update, no_update, no_update
    text = " | ".join(v for v in e.values() if v)
    if guard.INJECTION.search(text):  # check before scrubbing so nothing hides the attack
        guard.log_event("injection_blocked", f"event post by {email}: {text}")
        return "This post can't be published.", no_update, no_update, no_update
    e["details"] = guard.CONTACT.sub("[contact hidden]", e["details"])  # share contact info in chat instead
    try:
        check = ai(agents.check_event_post, text)
    except AIUnavailable:
        return AI_BUSY + " (Not posted yet.)", no_update, no_update, no_update
    if not check.ok:
        return f"Not posted: {check.nudge}", no_update, no_update, no_update
    e["date"], e["time"] = date, time_ or None
    event_id = store.add_event(email, e)
    store.toggle_going(email, event_id)  # the poster is going
    return f"Posted “{e['name']}” for {date_label(e)}. It's on the calendar and in My events.", tick + 1, "", ""


@app.callback(Output("going-tick", "data", allow_duplicate=True), Input({"type": "del-ev", "index": ALL}, "n_clicks"),
              State("token", "data"), State("going-tick", "data"), prevent_initial_call=True)
def delete_event(_clicks, token, tick):
    if not ctx.triggered_id or not ctx.triggered[0]["value"]:
        return no_update
    email, _me = me_from(token)
    if email:
        store.delete_event(email, ctx.triggered_id["index"])  # only deletes if you posted it
    return tick + 1


@app.callback(Output("crew-event", "options"), Input("token", "data"), Input("view", "data"), Input("going-tick", "data"),
              Input("crew-event-q", "value"), State("crew-event", "value"))
def crew_event_options(token, _view, _tick, q=None, picked=None):
    """Events you're going to come first, so 'who else is going?' is one click. `q` filters by name, type or place."""
    _, me = me_from(token)
    mine = set((me or {}).get("events", []))
    words = (q or "").lower().split()
    events = [e for e in all_events() if (not e.get("date") or upcoming(e))
              and (e["id"] == picked or hits(words, e["name"], e.get("sub", ""), e.get("category", ""),
                                             e.get("where", ""), " ".join(e.get("tags", []))))]
    events.sort(key=lambda e: (e["id"] not in mine, not e.get("date"), e.get("date") or ""))  # yours, then soonest

    def label(e):
        when = date_label(e) if e.get("date") else e.get("when", "")
        return html.Span([html.Span(e["name"], className="opt-name"),
                          html.Span(("Going · " if e["id"] in mine else "") + " · ".join(x for x in (e.get("sub"), when) if x),
                                    className="opt-sub")], className="opt")
    anyone = {"label": html.Span([html.Span("Any event", className="opt-name"),
                                  html.Span("Everyone", className="opt-sub")], className="opt"), "value": ""}
    return [anyone] + [{"label": label(e), "value": e["id"]} for e in events]


@app.callback(Output("dating-event", "options", allow_duplicate=True), Input("dating-event-q", "value"),
              State("token", "data"), State("dating-event", "value"), prevent_initial_call=True)
def dating_event_search(q, token, picked):
    return crew_event_options(token, None, None, q, picked)


for _pid in ("crew-event", "dating-event"):
    app.clientside_callback(  # a search shows every match, not just the first row
        "function(n, q){ const all = n % 2 === 1 || !!q;"
        " return [all ? 'ev-picks all' : 'ev-picks', n % 2 === 1 ? 'Show less' : 'Show all']; }",
        Output(_pid, "className"), Output(f"{_pid}-more", "children"), Input(f"{_pid}-more", "n_clicks"),
        Input(f"{_pid}-q", "value"))


# ---------- dating ----------
@app.callback(Output("dating-locked", "hidden"), Output("dating-unlocked", "hidden"), Output("dating-count", "children"),
              Output("dating-lock-msg", "children"), Output("dating-event", "options"),
              Output("dating-unlock", "children"),
              Input("token", "data"), Input("profile-view", "children"), Input("view", "data"))
def dating_view(token, _profile, _view):
    """Guests and friends-only users get a locked preview with a count only, never profiles."""
    email, me = me_from(token)
    n = sum(1 for s in store.all_students() if s.get("dating_open") and s.get("email") != email)
    count = f"{n} Yale students have opted in so far."
    if me and me.get("dating_open"):
        return True, False, count, None, crew_event_options(token, None, None), no_update
    msg = ("Turn on dating mode in your Profile to unlock." if email
           else "Sign in and turn on dating mode in your Profile to unlock.")
    return False, True, count, msg, [], "Turn on dating" if email else "Sign in to unlock"


@app.callback(Output("view", "data", allow_duplicate=True), Output("dating-open", "value", allow_duplicate=True),
              Input("dating-unlock", "n_clicks"), State("token", "data"), prevent_initial_call=True)
def dating_unlock(_, token):
    """Guests go to sign in. Signed-in users land on the Dating box in Profile with the box already ticked."""
    email, _me = me_from(token)
    return "profile", (["yes"] if email else no_update)


app.clientside_callback(  # scroll to the Dating box after the Profile page opens
    "function(n){ if(n){ var tries=0; (function go(){ var b=document.getElementById('dating-box');"
    " if(b && b.offsetParent){ b.scrollIntoView({behavior:'smooth', block:'center'}); }"
    " else if(tries++ < 30){ setTimeout(go, 100); } })(); }"  # wait until the Profile page is showing
    " return window.dash_clientside.no_update; }",
    Output("dating-box", "className"), Input("dating-unlock", "n_clicks"), prevent_initial_call=True)

app.clientside_callback(  # Next steps → "Dating (optional)" scrolls to the box when dating is still off
    "function(n){ if(n){ var b=document.getElementById('dating-box'); var on=document.querySelector('#dating-open input');"
    " if(b && b.offsetParent && !(on && on.checked)){ b.scrollIntoView({behavior:'smooth', block:'center'}); } }"
    " return window.dash_clientside.no_update; }",
    Output("dating-box", "title"), Input("next-dating", "n_clicks"), prevent_initial_call=True)

app.clientside_callback(  # "I am" / "Interested in" only appear once dating is ticked
    "function(v){ return !(v && v.length); }", Output("dating-fields", "hidden"), Input("dating-open", "value"))


@app.callback(Output("dating-results", "children"), Output("matches", "data", allow_duplicate=True),
              Input("dating-search", "n_clicks"), State("token", "data"), State("dating-level", "value"),
              State("dating-event", "value"), State("dating-verified", "value"), State("matches", "data"),
              prevent_initial_call=True)
def dating_search(_, token, level, event_id, verified_only, mdata):
    email, me = me_from(token)
    if not me or not me.get("dating_open"):  # checked again on the server, not just hidden in the page
        return "Dating is locked for this account.", no_update
    try:
        r = ai(agents.find_matches, me, "dating", level, event_id or None)
    except AIUnavailable:
        return AI_BUSY, no_update
    ok = lambda s: not verified_only or s.get("verified")
    found = [m for m in r["matches"] if ok(m["student"])]
    picked = {m["student_id"] for m in found}
    # Everyone else who is eligible, so nobody is hidden just because the AI didn't pick them. No AI call.
    rest = agents.fallback_matches(me, [s for s in r["pool"] if ok(s) and s["id"] not in picked], n=50)
    if not found and not rest:
        return ("No one matches these filters yet. Try 'Everyone', any event, or turn off 'verified only'.",
                no_update)
    names = agents.event_names()
    for m in found + rest:
        shared = set(m["student"].get("events", [])) & set(me.get("events", []))
        if shared:
            m["why"] += " Also going to " + ", ".join(names.get(e, "an event") for e in shared) + "."
    out = []
    if found:
        out += [html.H3("Top picks for you", className="cat-head"),
                html.P("Chosen and explained by our AI.", className="muted small"),
                html.Div([person_card(m, "dating", "dating") for m in found], className="deck")]
    if rest:
        out += [html.H3("More people open to dating", className="cat-head"),
                html.Div([person_card(m, "dating", "dating") for m in rest], className="deck")]
    u = r["usage"]
    if u:
        out.append(html.P(f"AI search: {u['seconds']}s · ${u['cost_usd']:.4f}", className="muted small"))
    return out, with_matches(mdata, "dating", "dating", found + rest)


# ---------- card buttons ----------
@app.callback(Output({"type": "card-msg", "index": ALL}, "children"),
              Input({"type": "hi", "index": ALL}, "n_clicks"), Input({"type": "like", "index": ALL}, "n_clicks"),
              Input({"type": "block", "index": ALL}, "n_clicks"), Input({"type": "report", "index": ALL}, "n_clicks"),
              State({"type": "card-msg", "index": ALL}, "id"), State("token", "data"), State("matches", "data"),
              prevent_initial_call=True)
def card_action(_hi, _like, _block, _report, msg_ids, token, mdata):
    trig = ctx.triggered_id
    if not trig or not ctx.triggered[0]["value"] or not mdata:
        return [no_update] * len(msg_ids)
    where, sid = trig["index"].split("|", 1)
    email, me = me_from(token)
    m = (mdata.get(where) or {}).get("by_id", {}).get(sid)
    if not me or not m:
        return [no_update] * len(msg_ids)
    them, mode = m["student"], mdata[where]["mode"]
    if trig["type"] == "hi":
        try:
            msg = html.Blockquote(ai(agents.write_icebreaker, me, them, mode, m["why"]))
        except AIUnavailable:
            msg = AI_BUSY
    elif trig["type"] == "like":
        if them.get("demo"):  # fake sample profiles accept right away so the chat can be tried
            store.like(them["email"], email, mode)
        if store.like(email, them["email"], mode):
            msg = html.Div([html.Strong("It's a match! "),
                            html.Button("Open chat", id={"type": "open-chat", "index": sid}, className="btn tiny primary")])
        else:
            msg = "Sent! If they say yes too, a chat opens for you both."
    elif trig["type"] == "block":
        store.block(email, them["email"])
        msg = f"Blocked. You and {them['name']} won't see each other again."
    else:
        store.report(email, them["email"], "reported from profile card")
        guard.log_event("user_report", f"{email} reported {them['id']}")
        msg = "Reported and blocked. Thank you, we'll review it."
    return [msg if i["index"] == trig["index"] else no_update for i in msg_ids]


# ---------- Double Date ----------
def duo_by_id(duo_id):
    return next((d for d in store.all_duos() if d["id"] == duo_id), None)


def active_duo(email):
    d = store.my_duo(email)
    return d if d and d["status"] == "active" else None


@app.callback(Output("dd-status", "children"), Output("dd-friend", "options"), Output("dd-invite-box", "hidden"),
              Output("dd-accept", "hidden"), Output("dd-leave", "hidden"), Output("dd-search", "hidden"),
              Input("token", "data"), Input("dd-tick", "data"), Input("view", "data"))
def dd_state(token, _tick, view):
    email, me = me_from(token)
    if not email or view != "dating":
        return no_update, no_update, no_update, no_update, no_update, no_update
    by_email = {s.get("email"): s for s in store.all_students()}
    d = store.my_duo(email)
    if d:
        partner = by_email.get(next(m for m in d["members"] if m != email), {})
        if d["status"] == "pending" and d["members"][1] == email:
            return f"{partner.get('name', 'A friend')} invited you to be their Double Date duo.", [], True, False, False, True
        if d["status"] == "pending":
            return f"Invite sent to {partner.get('name', 'your friend')}. Waiting for them to accept.", [], True, True, False, True
        return f"Your duo: you + {partner.get('name', 'friend')}", [], True, True, False, False
    friends = [by_email[o] for o, _m in store.matches_of(email) if o in by_email]
    options = [{"label": f.get("name", "?"), "value": f["id"]} for f in friends]
    status = "Pick a friend to pair up with." if friends else (
        "Connect with someone first (Connect on a profile in Discover or Events), then invite them here.")
    return status, options, not friends, True, True, True


@app.callback(Output("dd-tick", "data"), Output("dd-msg", "children"),
              Input("dd-invite", "n_clicks"), Input("dd-accept", "n_clicks"), Input("dd-leave", "n_clicks"),
              State("dd-friend", "value"), State("token", "data"), State("dd-tick", "data"), prevent_initial_call=True)
def dd_actions(_i, _a, _l, friend_id, token, tick):
    email, me = me_from(token)
    if not me or not me.get("dating_open"):
        return no_update, "Dating is locked for this account."
    if ctx.triggered_id == "dd-invite":
        friend = student_by_id(friend_id) if friend_id else None
        if not friend or not store.is_match(email, friend["email"]):  # only people you're connected with
            return no_update, "Pick one of your connections first."
        if store.my_duo(email) or store.my_duo(friend["email"]):
            return no_update, "One of you is already in a duo."
        store.invite_duo(email, friend["email"], auto_accept=bool(friend.get("demo")))
        return tick + 1, ("Duo ready! Tap Find duos." if friend.get("demo") else f"Invite sent to {friend['name']}.")
    if ctx.triggered_id == "dd-accept":
        store.accept_duo(email)
        return tick + 1, "You're in a duo! Tap Find duos."
    store.leave_duo(email)
    return tick + 1, "You left your duo."


def duo_card(d, my_duo_members):
    by_email = {s.get("email"): s for s in store.all_students()}
    members = [by_email[m] for m in d["members"] if m in by_email]
    ours = [by_email[m] for m in my_duo_members if m in by_email]
    our_interests = {i.lower() for s in ours for i in s.get("interests", [])}
    shared = sorted(our_interests & {i.lower() for s in members for i in s.get("interests", [])})
    our_events = {e for s in ours for e in s.get("events", [])}
    names = agents.event_names()
    events = sorted({names[e] for s in members for e in s.get("events", []) if e in our_events and e in names})
    return html.Article([
        html.Div([html.Div([html.Div(className="duo-photo", style={"backgroundImage": f"url({photo_url(m['id'])})"}),
                            html.Div([m["name"], html.Span(" ✓", className="vbadge small") if m.get("verified") else None],
                                     className="duo-name"),
                            html.Div(" · ".join(x for x in ((m.get("gender") or "").capitalize(), m["school"]) if x),
                                     className="duo-sub")], className="duo-member") for m in members],
                 className="duo-photos"),
        html.Span("demo duo", className="demo-tag") if d.get("demo") else None,
        html.Div([
            html.Div([html.Div("Your four have in common", className="prompt-q"),
                      html.Div(", ".join(shared) if shared else "New things to try together", className="prompt-a")],
                     className="prompt"),
            html.Div([html.Div("All going to", className="prompt-q"), html.Div(", ".join(events), className="prompt-a")],
                     className="prompt") if events else None,
        ], className="pc-body"),
        html.Div([
            html.Button(icon("x"), id={"type": "duo-pass", "index": d["id"]}, className="round", title="Pass",
                        **{"aria-label": "Pass on this duo"}),
            html.Button(icon("heart"), id={"type": "duo-like", "index": d["id"]}, className="round big pink", title="Like duo",
                        **{"aria-label": "Like this duo"}),
        ], className="pc-actions"),
        html.Div(id={"type": "duo-msg", "index": d["id"]}, role="status", className="card-msg"),
    ], className="pcard dating-card duo-card")


@app.callback(Output("dd-results", "children"), Input("dd-search", "n_clicks"), State("token", "data"),
              prevent_initial_call=True)
def dd_search(_, token):
    """Other duos, filtered in code: no blocked people, nobody already rated, and at least one person
    in the other duo whose dating preferences match yours both ways. No AI call needed."""
    email, me = me_from(token)
    mine = active_duo(email) if me and me.get("dating_open") else None
    if not mine:
        return html.P("Pair up with a friend first.", className="muted")
    by_email = {s.get("email"): s for s in store.all_students()}
    blocked = set().union(*(store.blocked_pairs(m) for m in mine["members"]))
    rated = store.rated_duos(mine["id"])
    found = []
    for d in store.all_duos():
        if d["id"] == mine["id"] or d["status"] != "active" or d["id"] in rated:
            continue
        if set(d["members"]) & (set(mine["members"]) | blocked):
            continue
        others = [by_email.get(m) for m in d["members"]]
        if all(others) and any(guard.eligible(me, o, "dating", blocked) for o in others):
            found.append(d)
    if not found:
        return html.P("No new duos right now. Check back later!", className="muted")
    return html.Div([duo_card(d, mine["members"]) for d in found[:6]], className="deck")


@app.callback(Output({"type": "duo-msg", "index": ALL}, "children"),
              Input({"type": "duo-like", "index": ALL}, "n_clicks"), Input({"type": "duo-pass", "index": ALL}, "n_clicks"),
              State({"type": "duo-msg", "index": ALL}, "id"), State("token", "data"), prevent_initial_call=True)
def dd_rate(_likes, _passes, msg_ids, token):
    trig = ctx.triggered_id
    if not trig or not ctx.triggered[0]["value"]:
        return [no_update] * len(msg_ids)
    email, _me = me_from(token)
    mine, other = active_duo(email), duo_by_id(trig["index"])
    if not mine or not other:
        return [no_update] * len(msg_ids)
    liked = trig["type"] == "duo-like"
    if liked and other.get("demo"):  # demo duos like you back so the group chat can be tried
        store.rate_duo(other["id"], mine["id"], True)
    if store.rate_duo(mine["id"], other["id"], liked):
        msg = html.Div([html.Strong("It's a double match! "),
                        html.Button("Open group chat", id={"type": "open-chat", "index": f"G|{mine['id']}|{other['id']}"},
                                    className="btn tiny primary")])
    else:
        msg = "Liked! If they like you back, a group chat opens." if liked else "Passed."
    return [msg if i["index"] == trig["index"] else no_update for i in msg_ids]


# ---------- chat ----------
@app.callback(Output("chat-with", "data"), Output("view", "data", allow_duplicate=True),
              Input({"type": "open-chat", "index": ALL}, "n_clicks"), prevent_initial_call=True)
def open_chat(_clicks):
    if not ctx.triggered_id or not ctx.triggered[0]["value"]:
        return no_update, no_update
    return ctx.triggered_id["index"], "chats"


def chat_ctx(token, sid):
    """Who I'm chatting with, checked on the server every time: still matched, not blocked.
    1:1 chats use the other student's ID; Double Date group chats use 'G|my_duo|their_duo'."""
    email, me = me_from(token)
    if not email or not sid:
        return None
    by_email = {s.get("email"): s for s in store.all_students()}
    if sid.startswith("G|"):
        _g, mine_id, other_id = sid.split("|")
        mine, other = active_duo(email), duo_by_id(other_id)
        if not mine or mine["id"] != mine_id or not other or other_id not in store.duo_matches(mine_id):
            return None
        if set(other["members"]) & store.blocked_pairs(email):  # blocked anyone in that duo: chat closed
            return None
        partner = by_email.get(next(m for m in mine["members"] if m != email), {})
        others = [by_email[m] for m in other["members"] if m in by_email]
        return {"email": email, "me": me, "key": (mine_id, other_id), "group": True, "others": others,
                "partner": partner, "other_duo": other,
                "title": f"You & {partner.get('name', 'friend')} · " + " & ".join(o["name"] for o in others)}
    them = student_by_id(sid)
    if not them or not store.is_match(email, them["email"]):
        return None
    return {"email": email, "me": me, "key": (email, them["email"]), "group": False, "others": [them],
            "title": them["name"]}


@app.callback(Output("chat-list", "children"),
              Input("view", "data"), Input("chat-tick", "n_intervals"), Input("chat-refresh", "data"),
              Input("chat-with", "data"), State("token", "data"))
def chat_list(view, _n, _r, current, token):
    if view != "chats":
        return no_update
    email, _me = me_from(token)
    if not email:
        return html.P("Sign in to see your chats.", className="muted")
    by_email = {s.get("email"): s for s in store.all_students()}
    rows = []
    mine = active_duo(email)
    blocked = store.blocked_pairs(email)
    for other_id in (store.duo_matches(mine["id"]) if mine else []):
        other = duo_by_id(other_id)
        if not other or set(other["members"]) & blocked:
            continue
        sid = f"G|{mine['id']}|{other_id}"
        msgs = store.get_messages(mine["id"], other_id, limit=1)
        names = " & ".join(by_email[m]["name"] for m in other["members"] if m in by_email)
        rows.append(html.Button([
            html.Div([html.Img(src=photo_url(by_email[m]["id"]), className="mini-avatar") for m in other["members"]
                      if m in by_email], className="avatar-pair"),
            html.Div([html.Div([names, html.Span("Double date", className="mini-tag pink")], className="strong"),
                      html.Div((msgs[-1]["text"] if msgs else "Double match! Say hi")[:48], className="muted small")],
                     className="cl-text"),
        ], id={"type": "open-chat", "index": sid}, className="chat-item active" if sid == current else "chat-item"))
    for other, mode in store.matches_of(email):
        s = by_email.get(other)
        if not s:
            continue
        msgs = store.get_messages(email, other, limit=1)
        last = msgs[-1]["text"] if msgs else "New match. Say hi!"
        rows.append(html.Button([
            html.Img(src=photo_url(s["id"]), className="mini-avatar"),
            html.Div([html.Div([s["name"], html.Span("Dating", className="mini-tag pink") if mode == "dating" else None], className="strong"),
                      html.Div(last[:48], className="muted small")], className="cl-text"),
        ], id={"type": "open-chat", "index": s["id"]},
            className="chat-item active" if s["id"] == current else "chat-item"))
    return rows or html.P("No chats yet.", className="muted")


@app.callback(Output("chat-empty", "hidden"), Output("chat-wrap", "hidden"), Output("chat-empty-title", "children"),
              Output("chat-empty-sub", "children"), Output("chat-empty-signin", "hidden"),
              Output("chat-empty-friends", "hidden"), Output("chat-empty-events", "hidden"),
              Input("view", "data"), Input("token", "data"), Input("chat-refresh", "data"))
def chat_gate(view, token, _r):
    """Guests and people with no matches yet get a friendly explainer instead of an empty chat window."""
    if view != "chats":
        return no_update, no_update, no_update, no_update, no_update, no_update, no_update
    email, _me = me_from(token)
    if not email:
        return (False, True, "Your conversations live here",
                "Sign in with your Yale email. When you and someone both say yes, a private chat opens here.",
                False, True, True)
    has_chats = bool(store.matches_of(email)) or bool((d := active_duo(email)) and store.duo_matches(d["id"]))
    if not has_chats:
        return (False, True, "No chats yet",
                "Connect with someone who shares your interests, or join an event and see who's going.",
                True, False, False)
    return True, False, no_update, no_update, True, True, True


@app.callback(Output("view", "data", allow_duplicate=True), Output("browse-tab", "value", allow_duplicate=True),
              Input("chat-empty-signin", "n_clicks"), Input("chat-empty-friends", "n_clicks"),
              Input("chat-empty-events", "n_clicks"), prevent_initial_call=True)
def chat_empty_go(*clicks):
    if not ctx.triggered_id or not any(clicks):
        return no_update, no_update
    return {"chat-empty-signin": ("profile", no_update), "chat-empty-friends": ("crew", no_update),
            "chat-empty-events": ("groups", "calendar")}[ctx.triggered_id]


@app.callback(Output("chat-head", "hidden"), Output("chat-title", "children"), Output("chat-messages", "children"),
              Output("chat-compose", "hidden"),
              Input("chat-with", "data"), Input("chat-tick", "n_intervals"), Input("chat-refresh", "data"),
              State("token", "data"), State("view", "data"))
def chat_pane(sid, _n, _r, token, view):
    if view != "chats" and ctx.triggered_id == "chat-tick":
        return no_update, no_update, no_update, no_update
    c = chat_ctx(token, sid)
    if not c:
        return True, None, (html.Div([icon("chats"), html.P("Pick a conversation on the left.")], className="pick-chat")
                            if store.session_email(token) else None), True
    title = [*[html.Img(src=photo_url(o["id"]), className="mini-avatar") for o in c["others"]],
             html.Span(c["title"], className="strong"),
             html.Span(" ✓", className="vbadge small") if not c["group"] and c["others"][0].get("verified") else None]
    by_email = {s.get("email"): s for s in store.all_students()}
    bubbles = []
    for m in store.get_messages(*c["key"]):
        if m["sender"] == c["email"]:
            bubbles.append(html.Div(m["text"], className="bubble me"))
        else:
            who = by_email.get(m["sender"], {}).get("name", "") if c["group"] else None
            bubbles.append(html.Div([html.Div(who, className="bubble-name") if who else None, m["text"]],
                                    className="bubble them"))
    if not bubbles:
        bubbles = [html.P(f"You matched with {c['title']}. Say hi!", className="muted center")]
    return False, title, bubbles, False


@app.callback(Output("chat-input", "value"), Output("chat-nudge", "hidden"), Output("chat-nudge-text", "children"),
              Output("pending-msg", "data"), Output("chat-refresh", "data"),
              Input("chat-send", "n_clicks"), Input("chat-input", "n_submit"), Input("chat-send-anyway", "n_clicks"),
              Input("chat-edit", "n_clicks"),
              State("chat-input", "value"), State("pending-msg", "data"), State("chat-with", "data"),
              State("token", "data"), State("chat-refresh", "data"), prevent_initial_call=True,
              running=[(Output("chat-send", "disabled"), True, False), (Output("chat-input", "disabled"), True, False),
                       (Output("chat-send", "children"), "Checking…", "Send")])
def send(_c, _s, _anyway, _edit, text, pending, sid, token, refresh):
    """The box and button lock while the AI reviews the message, so pressing Enter again can't send it twice."""
    c = chat_ctx(token, sid)
    if not c:
        return no_update, True, None, None, refresh + 1
    if ctx.triggered_id == "chat-edit":
        return pending if pending else no_update, True, None, None, no_update
    if ctx.triggered_id == "chat-send-anyway":
        if not pending:  # nothing waiting (e.g. the check itself failed)
            return no_update, True, None, None, no_update
        text = pending
    else:
        text = (text or "").strip()
        if not text:
            return no_update, no_update, no_update, no_update, no_update
        try:
            check = ai(agents.review_message, text)  # "review before you send"
        except AIUnavailable:
            return text, False, f"{AI_BUSY} Your message wasn't sent.", None, no_update  # text kept
        if not check.ok:
            return "", False, f"Before you send: {check.nudge}", text, no_update
    recent = store.get_messages(*c["key"], limit=5)
    if any(m["sender"] == c["email"] and m["text"] == text[:1000] and time.time() - m["time"] < 10 for m in recent):
        return "", True, None, None, refresh + 1  # same message again within 10 s: a double press, not a new message
    store.send_to(*c["key"], c["email"], text[:1000])
    demo = next((o for o in c["others"] if o.get("demo")), None)
    if demo and not any(m["sender"] in {o["email"] for o in c["others"]} for m in store.get_messages(*c["key"])):
        hello = "Hi all! 😊" if c["group"] else f"Hi {c['me']['name'].split()[0]}! 😊"
        store.send_to(*c["key"], demo["email"], f"{hello} (This is a demo profile — real students reply here in real time.)")
    return "", True, None, None, refresh + 1


@app.callback(Output("chat-with", "data", allow_duplicate=True), Output("chat-refresh", "data", allow_duplicate=True),
              Input("chat-unmatch", "n_clicks"), Input("chat-block", "n_clicks"), Input("chat-report", "n_clicks"),
              State("chat-with", "data"), State("token", "data"), State("chat-refresh", "data"),
              prevent_initial_call=True)
def chat_safety(_u, _b, _r, sid, token, refresh):
    c = chat_ctx(token, sid)
    if not c:
        return None, refresh + 1
    if ctx.triggered_id == "chat-unmatch":
        if c["group"]:
            store.unmatch_duos(*c["key"])
        else:
            store.unmatch(c["email"], c["others"][0]["email"])
    for o in c["others"] if ctx.triggered_id != "chat-unmatch" else []:
        if ctx.triggered_id == "chat-block":
            store.block(c["email"], o["email"])
        else:
            store.report(c["email"], o["email"], "reported from chat")
            guard.log_event("user_report", f"{c['email']} reported {o['id']} from chat")
    return None, refresh + 1


if __name__ == "__main__":
    app.run(debug=False)
