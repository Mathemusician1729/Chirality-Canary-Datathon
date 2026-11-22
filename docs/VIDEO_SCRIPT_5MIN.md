# WellWatch: 5-minute project video script

**Target length: about 4:40.** It uses 6 of the deck's slides plus a 3-minute screen recording of the
app. Slides marked ★ in the deck's speaker notes are the video slides.

| Time | What's on screen | Length |
|---|---|---|
| 0:00 | Slide 1: Cover | 0:10 |
| 0:10 | Slide 2: The problem | 0:25 |
| 0:35 | Slide 4: The solution | 0:25 |
| 1:00 | **App demo** (screen recording) | 3:00 |
| 4:00 | Slide 7: Results | 0:20 |
| 4:20 | Slide 12: Recommendations | 0:20 |
| 4:40 | end | |

Skip slides 3, 5, 6 and 8–11 and 13 in the video; they are for the 10-minute presentation.

---

## Before recording (10 minutes)

- **Privacy:** close Discord, browsers and anything personal. Never show the Canary **Licenses** page or
  **Identity → API tokens**.
- **App:**
  1. Replay & engine → **Back up now** (so you can undo the demo's acknowledge).
  2. Click through every screen once (the planner takes ~15 s the first time).
  3. Crew planner: set day **2026-08-03 (Mon)** and press Solve plan once.
  4. Go back to Fleet overview, day **2026-10-26**, and zoom the satellite map onto the wells near
     Lloydminster.
- **Canary Admin** open on the **Calcs & Events** tab (for the 20-second Canary moment).
- Record at 1080p with OBS (display capture), or Windows Game Bar (Win+Alt+R) one window at a time,
  then join the clips.
- **Offline version?** See "If recording on the offline version" at the end.

---

## Script

### Slide 1: Cover (0:00 – 0:10)
> “Hi, we’re [team]. This is WellWatch: it warns a week before an oil well fails, plans where the
> service crews should go, and it all runs on Canary.”

### Slide 2: The problem (0:10 – 0:35)
> “Strathcona’s heavy-oil wells have about 1,700 failures a year that need a service crew. Each one
> shuts a well in for around five days, and together they cost about 34 million dollars a year in
> repairs and lost oil. Today, crews arrive after the failure.”

### Slide 4: The solution (0:35 – 1:00)
> “WellWatch reads each day’s well data from Canary, predicts which wells are likely to need a crew in
> the next seven days and what that would cost, raises alerts, and uses Gurobi to plan where each crew
> should go. Everything is written back into Canary.”

### App demo (1:00 – 4:00)

**a) Canary (1:00 – 1:20).** Show Canary Admin → Calcs & Events → the "WellWatch warning" job (Live
Mode, events count).
> “Everything runs on Canary. We held back July to October 2026 and replayed it into Canary one day at
> a time, as if live. Each day our engine reads the data from Canary, scores every well and writes the
> results back as tags. And Canary raises its own alarm whenever a well reaches warning level.”

**b) Fleet overview (1:20 – 2:00).** Switch to the app.
> “This is the morning view for the 26th of October: 920 wells scored, 8 critical alerts, 12 warnings,
> and about 810 thousand dollars of expected failure cost next week.”

Click a big red well on the satellite map; the popup opens.
> “Colour is the alert level, size is the money at stake. Click a well and you get its seven-day risk,
> expected loss, and the reason in plain English. The list on the right ranks wells by expected loss,
> so crews go where a failure would cost the most.”

**c) Alert inbox (2:00 – 2:35).** Open Alert inbox, select the top Critical alert.
> “Alerts follow the lifecycle used in control rooms: open, acknowledged, shelved, closed. Each one
> says why the well was flagged.”

Click **Acknowledge**, type your name, OK.
> “When I acknowledge it, it’s recorded with my name in the audit trail and written back to Canary,
> so the whole team sees someone’s on it.”

**d) Crew planner (2:35 – 3:25).** Open Crew planner (day 2026-08-03 already set) and press **Solve plan**.
> “Every Monday the planner decides where each crew goes. It’s a Gurobi optimisation that minimises
> expected failure loss plus visit cost, within crew hours, travel, the budget, and reaching critical
> wells within two days.”

Point at the tiles, then the schedule.
> “For the week of August 3rd, expected loss drops from 289,762 dollars to 278,234 with 18 visits:
> 11,528 dollars saved after paying for the visits, solved in under a second. Here’s the crew-by-day
> schedule, and one click prints work orders for each crew.”

**e) Scoreboard (3:25 – 4:00).** Open Replay & engine.
> “Did the warnings come true? On the four held-back months, compared with a simple rule crews already
> use, giving both the same twelve warnings a day, WellWatch warned about more of the costly failures,
> and most warnings arrived a full week early, enough time to book a crew.”

### Slide 7: Results (4:00 – 4:20)
> “Overall, WellWatch’s warnings came before 1.44 million dollars of failures against 1.28 million for
> the rule, a median of seven days ahead instead of five. And the wells Gurobi sent crews to had nearly
> three times the failure value of the rule’s picks.”

### Slide 12: Recommendations (4:20 – 4:40)
> “Our recommendation: pilot WellWatch on one field for three months on live Canary data, record what
> every proactive visit achieves, and send the repeat failers to root-cause review. Thanks for
> watching.”

---

## Tips

- Pause a second after each click, and speak slower than feels natural.
- Never press Start replay, Start live engine or Start fresh on camera.
- If a screen stalls, keep talking ("while that loads…") or cut it in editing.
- Running long? Shorten (a) Canary to one sentence over the solution slide, and drop the work-order
  sentence in (d).

## If recording on the offline version

- **Part (a) Canary:** record it on the laptop that has Canary, or say it over the solution slide.
- **Acknowledge:** say “…recorded in the audit trail, and on the Canary-connected version written back
  to Canary too.”
- **Planner:** if the grey line under the tiles says **highs**, say “the optimiser solved it” instead of
  “Gurobi … in under a second”. The numbers are the same.
- **Bottom bar** says “Canary: offline”: fine to leave, or crop it.
- Run `python -m wellwatch.scoreboard --no-canary` once beforehand, or the scoreboard is empty.
