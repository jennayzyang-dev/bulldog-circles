# User testing — October 26, 2026

Two real Yale students tried Bulldog Circles on the designer's laptop (http://127.0.0.1:8050), one at a time, with no help.
The designer (Jenna) also ran the same tasks; her run is not timed or counted, because she built the app.
Screenshots and notes are in `docs/user_test/`.

| Tester | School · year | Notes |
|---|---|---|
| Abi | Law School · 3rd year | Dating turned on during the test |
| William | School of Medicine · 2nd year | Friends only |

## Before: how they find people today

| Tester | How | How long |
|---|---|---|
| Abi | Friends' friends, Facebook groups, signing up for Eventbrite events and hoping to meet someone there, joining random group chats | A few days, "difficult to coordinate" |
| William | Close circles, friends' friends | A few days (5–10 days) |

## In the app: time per task (stopwatch laps)

Tasks, given one at a time:
1. Sign in and set up your profile.
2. Find something to do this week.
3. Find people going to an event you'd like.
4. Find friends who share your interests and chat.

| Task | Abi | William | Average |
|---|---|---|---|
| 1. Sign in + profile | 1:54.69 | 1:24.17 | 1:39 |
| 2. Find something this week | 0:45.72 | 0:27.42 | 0:37 |
| 3. Find people going to an event | 1:50.00 | 0:45.95 | 1:18 |
| 4. Find friends + chat | 3:27.23 | 0:39.56 | 2:03 |
| **Total** | **7:57.64** | **3:17.10** | **5:37** |

**Headline:** finding people to go to an event took **about 1–2 minutes in the app (average 1:18)**, versus
**a few days (5–10 days for William)** the usual way. Both testers went from a brand-new account to chatting with a
match in **under 8 minutes**.

## After

| Question | Abi | William |
|---|---|---|
| Rating (would you use it?) | **5/5** | **3/5** |
| What would you tell a friend? | "Multi use app" | "Easy to set up events and to find friends" |
| What annoyed you / what would you change? | "Don't know how to turn on dating part, not obvious" | "Events connect with calendar for easier scheduling" |

Observations while watching:
- **Abi:** "Gender part is not showing; layout is nice; turn on dating function under profile part should be clearer."
- **William:** "Home page doesn't look like home page, a little confusing."

## Issues found and what we did

| # | Issue | Source | Status |
|---|---|---|---|
| 1 | Gender not shown on dating profiles | Abi | Fixed the same day: gender now shows on dating cards, Double Date cards and the profile summary |
| 2 | Turning on dating isn't obvious | Abi (twice) | Fixed: a separate pink Dating box in Profile with 3 numbered steps; the Dating tab's "Turn on dating" button jumps straight to it and ticks it |
| 3 | Home page doesn't feel like a home page | William | Fixed: signed-in users now get a personal banner ("Hi William, here's your week", next plan, match count, quick buttons for Find friends · Calendar · Chats). The site also moved to Yale blue as its one brand colour |
| 4 | Add events to your own calendar | William | Fixed: every dated event has "Add to calendar", which downloads a standard .ics file for Apple Calendar, Google Calendar or Outlook |
| 5 | Same chat message appeared several times ("Yo waddap" ×7, "okay who are you" ×3, "heyaa" ×2) | All three screenshots | Fixed: the AI checks each message before sending, and pressing Enter/Send again during that wait sent it again. Now the box and button lock ("Checking…") during the check, and an identical message within 10 s is ignored. Tested: 3 presses → 1 message |
| 6 | Demo reply said "Hi unknown!" | Jenna's run | Fixed by the new required Name field (the AI used to guess the name) |
