# WellWatch: 10-minute finalist presentation script

**Target length: about 9:45**, leaving a little room before questions. It uses all 13 slides and a
2:30 demo. The same words are in each slide's speaker notes in the deck.

| Time | Slide | Length | Main point |
|---|---|---|---|
| 0:00 | 1 Cover | 0:15 | What WellWatch is |
| 0:15 | 2 The problem | 0:45 | 1,700 failures, C$34M a year, crews arrive late |
| 1:00 | 3 What drives failures | 0:45 | Failures repeat; pump and tubing failures cost most |
| 1:45 | 4 The solution | 0:45 | Canary → risk → alerts → Gurobi plan → Canary |
| 2:30 | 5 How we tested it | 0:30 | Months it never saw, replayed through Canary |
| 3:00 | 6 Demo | 2:30 | The app, live or recorded |
| 5:30 | 7 Results | 0:45 | More value caught, 7 days ahead |
| 6:15 | 8 Business case | 0:45 | Up to C$1.6M a year; break-even 37% |
| 7:00 | 9 Implementation plan | 1:00 | Pilot → tune → scale, with go/no-go gates |
| 8:00 | 10 How the team would use it | 0:30 | Daily and weekly rhythm |
| 8:30 | 11 Risks | 0:30 | What could go wrong and how we handle it |
| 9:00 | 12 Recommendations | 0:30 | Four steps |
| 9:30 | 13 Thank you | – | Questions |

**Splitting it between presenters** (suggested): presenter A slides 1–5, presenter B the demo and
slide 7, presenter C slides 8–13. Hand over at slide 6 and slide 8.

**Demo: live or recorded?** A recorded 2:30 clip is safer: no loading, no surprises. If you go live,
warm the app up first (see the video script's "Before recording") and keep the clip ready as a backup.

---

## Script

### 1. Cover (0:00 – 0:15)
> “Hi, we’re [team]. This is WellWatch: it warns a week before an oil well fails, plans where the
> service crews should go, and it all runs on Canary.”

### 2. The problem (0:15 – 1:00)
> “Strathcona’s heavy-oil wells have about 1,700 failures a year that need a service crew, a Rod Truck
> or a Workover. Each one shuts a well in for around five days, and together they cost about 34
> million dollars a year in repairs and lost oil. These numbers come from five years of daily
> production, job and failure records for about two thousand oil wells. Today, a crew is sent when a
> well goes down, and every day it waits, it loses oil.
> But there’s a pattern: a well that failed three or more times last year fails again 85% of the time.
> So this is something we can predict.”

### 3. What drives failures (1:00 – 1:45)
> “Two things drive the cost. First, failures repeat. On the left: a well with no failure last year
> averages a third of a failure the next year; a well with three or more averages three and a half.
> Past service and repair history is the strongest signal, together with the last job report and pump
> sensors like speed and efficiency.
> Second, not all failures are equal. Rod failures are the most common but cost about 15 thousand
> dollars each; pump and tubing failures are rarer but cost 40 to 50 thousand. So the question isn’t
> just ‘which well will fail’, but ‘where would a failure cost the most’. That’s how WellWatch ranks
> wells.”

### 4. The solution (1:45 – 2:30)
> “Here’s how it works, left to right. The data lives in the Canary historian: daily rates, pressures,
> pump efficiency and downtime for every well. Every day our engine reads it from Canary and scores
> each running well: the chance of a crew-needing failure in the next seven days, times what a failure
> at that well usually costs. That’s the expected loss. Wells at the top become warning or critical
> alerts, each with a reason. Every Monday a Gurobi optimisation plans which wells each crew visits.
> And everything goes back into Canary as tags, with Canary raising its own alarm, so anyone in Axiom
> sees it. On top sits a desktop app for the team, which also works offline.”

### 5. How we tested it (2:30 – 3:00)
> “We were strict about testing. The model learned from 2021 to 2024, was calibrated on early 2025 and
> tested on the following year. There, the fifteen highest-risk wells each day failed within a week four
> times more often than average.
> Then we held back July to October 2026 entirely and replayed it through Canary, one day at a time,
> exactly as if it were live. And we compared against a rule crews already use, the wells with the
> shortest time since their last repair, given the same number of warnings.”

### 6. Demo (3:00 – 5:30)
Switch to the app (or play the clip).

**Canary (3:00 – 3:20):** Calcs & Events → "WellWatch warning".
> “First, Canary itself: this event job raises an alarm whenever a well’s WellWatch alert level
> reaches warning. It ran over the whole replay.”

**Fleet overview (3:20 – 3:50):**
> “This is the morning view for October 26th: 920 wells scored, 8 critical, 12 warnings, about 810
> thousand dollars of expected failure cost next week.”

Click a red well on the map.
> “Each well on the satellite map: colour is the alert level, size the money at stake. Click it for the
> risk, expected loss and the reason.”

**Alert inbox (3:50 – 4:20):** select the top critical alert, Acknowledge with your name.
> “Each alert says why it was flagged. Acknowledging records my name in the audit trail and writes the
> state back to Canary. Of the 207 alerts in the replay, 101 ended with the well failing and 86
> cleared on their own; every one records how it ended.”

**Crew planner (4:20 – 5:10):** day 2026-08-03, Solve plan.
> “For the week of August 3rd, Gurobi plans 18 visits. Expected loss falls from 289,762 dollars to
> 278,234: 11,528 dollars saved after paying for the visits, solved in under a second, within crew
> hours, travel, budget, and critical wells first. Here’s the schedule; one click prints each crew’s
> work orders.”

**Scoreboard (5:10 – 5:30):** Replay & engine.
> “And the scoreboard compares our warnings with the simple rule over the four months. Let’s go back
> to the slides for the numbers.”

### 7. Results (5:30 – 6:15)
> “With the same twelve warnings a day as the simple rule, WellWatch’s warnings came before 1.44
> million dollars of failures against 1.28 million, a median of seven days ahead instead of five. Over
> 17 weekly plans, the wells our plan sent crews to went on to have 1.8 million dollars of failures,
> against 0.6 million for the rule with the same number of visits.
> One honest trade-off: the rule warns about more failures in total, mostly cheap rod failures, because
> we rank by expected cost. That’s a setting an operator can choose; we chose money at stake.”

### 8. Business case (6:15 – 7:00)
> “What is it worth? It depends on one number the data doesn’t contain: how much of a failure a
> proactive visit actually prevents. So we show the range. If a visit prevents half a failure, the plan
> saves about half a million dollars a year after paying for the visits; at 75% it’s 1.6 million; at
> 25% it loses money. Break-even is 37%.
> We also stress-tested it: one week in the replay carried a big share of the savings; without it,
> break-even rises to about 50%. So the plan should be judged over months, and that’s why our rollout
> starts by measuring that number.”

### 9. Implementation plan (7:00 – 8:00)
> “We’d roll it out in three phases, each with a decision point.
> Phase one, months one to three: pilot on Druid Main, which had the highest expected failure cost in
> our replay. Connect WellWatch to the live Canary data, run daily alerts and weekly crew plans, and
> record the outcome of every visit. Go/no-go: do visits prevent at least 37% of a failure?
> Phase two, months four to six: add Winter and Cosine. Add a minimum number of warnings per field, so
> fields like Cactus Lake aren’t missed, use the run-life rule for rod failures where it does better,
> and send work orders straight into the maintenance system. Go/no-go: is the net saving positive over
> the quarter?
> Phase three, months seven to twelve: the whole fleet, quarterly retraining and oil-price updates, and
> a root-cause program for repeat failers, measured against the 34-million-dollar baseline.”

### 10. How the team would use it (8:00 – 8:30)
> “This is how it fits into a team’s week. The engine runs by itself every day from Canary. The
> production engineer spends ten minutes each morning on the map and the alert inbox. Every Monday the
> crew planner solves the week’s plan and prints work orders. The foreman reports what they found,
> which closes the alert with its outcome, and that’s the data that measures visit effectiveness. Once a
> month the manager reviews the scoreboard and the repeat failers.”

### 11. Risks (8:30 – 9:00)
> “We want to be upfront about the risks. The biggest: if visits prevent less than about 37% of a
> failure, the plan doesn’t pay, so the pilot measures it before anything is scaled. Because we rank by
> cost, some fields got no warnings in the replay; a per-field minimum fixes that. Savings are lumpy,
> so judge over months. Three quarters of failures have no recorded type, and sensors can get stuck;
> the app flags stuck sensors. And to avoid alarm fatigue the list stays short, about twelve warnings
> a day.”

### 12. Recommendations (9:00 – 9:30)
> “So, four recommendations. One: pilot WellWatch on Druid Main for three months on live Canary data.
> Two: record the outcome of every proactive visit, which turns our one big assumption into a measured
> number. Three: send repeat failers to root-cause review instead of repairing them again and again.
> And four: close the gaps, with a minimum of warnings per field and a failure type on every record.”

### 13. Thank you (9:30)
> “Thank you. We’re happy to take questions.”

---

## Likely questions

| Question | Short answer |
|---|---|
| How accurate is it? | Test year: the top 15 wells a day failed within a week 4.2× more often than average (ROC-AUC 0.745). Replay: C$1.44M of failures warned about vs C$1.28M for the rule, 7 days ahead vs 5. |
| Why does the rule catch more failures? | We rank by expected cost, so we focus on expensive wells. Ranked by risk alone, the model catches 66 failures (rule: 69) with 15% of warnings correct; it’s a setting. |
| Where does 50% effectiveness come from? | It’s an assumption, which is why we show 25 / 50 / 75%, the 37% break-even, and why the pilot measures it. |
| Why Gurobi? | It solves each weekly plan with crew hours, travel, budget and critical-first rules in under 1.5 s, and falls back to the free HiGHS solver. |
| How deep is the Canary integration? | Data in and out through Canary’s APIs, results stored as Canary tags, an asset model joining each well’s sensors and risk, and a Canary Calcs & Events alarm. |
| How would it run on live data? | Live mode already picks up each new day from Canary; it needs a mapping to the company’s tag names and a daily export of job and failure records. See `docs/STRATHCONA_WORKFLOW.md`. |
| What does a lost barrel cost in your numbers? | The cash margin per quarter (price minus royalties, transport and energy cost) from Strathcona’s and Cenovus’s public reports; we also show the low and high cases. |
