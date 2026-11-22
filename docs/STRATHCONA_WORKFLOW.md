# How a Strathcona team would use WellWatch

This is a **proposed** way of working for a heavy-oil production team, built on what the app does today
and what the 2021–2026 data shows. It is not a description of Strathcona's current internal processes.
The numbers come from [RESULTS.md](RESULTS.md).

## The problem WellWatch is built for

- About **1,700 crew-needing failures a year** (a Rod Truck or Workover job after an equipment
  failure), costing about **C$34M a year** in repairs and lost oil.
- An average failure keeps a well down **5 days** and loses **15 m³ of oil**, on top of a C$14.6k repair.
- Failures cluster: a well with 3+ failures last year fails again **85%** of the time.
- Crews are limited: each field has only the crews seen working at once in the records.
- Without a forecast, crews go to wells **after** they fail. WellWatch's job is to move some of that work
  to **before** the failure, on the wells where it pays most.

## Who uses it

| Role | What they need | Where in WellWatch |
|---|---|---|
| **Production / optimization engineer** | Which wells are at risk this week, and why | Fleet overview, Alert inbox |
| **Field foreman / lease operator** | Which wells to look at today, with a reason | Work orders, tray notifications, Canary events |
| **Maintenance / crew planner** | Where each Rod Truck crew should go this week, within budget | Crew planner |
| **Production or asset manager** | Is it working? What would another crew be worth? | Scoreboard, what-if analyses, RESULTS.md |
| **Instrumentation / SCADA technician** | Which sensors have stopped reporting real values | Settings & health → data quality |

## The working rhythm

### Every day, automatically

1. The day's production and sensor data lands in **Canary** (`Wells.<Field>.<UWI>.<Signal>` tags).
2. The **WellWatch engine** (live mode) sees the new day on Canary's data clock and reads the last 100
   days for every running well.
3. It scores each well's chance of a crew-needing failure **in the next 7 days**, multiplies that by
   what a failure at that well usually costs, and ranks the fleet by **expected loss**.
4. It writes the results back to Canary for every well: `FailureRisk7d`, `ExpectedLoss7d`, `RiskRank`,
   `AlertLevel`, `TopDriver`, `AlertState`. It also writes fleet totals.
5. Wells that reach **warning** (top 15 and ≥8% risk) or **critical** (top 5 and ≥15% risk) open an
   alert. Canary's own **"WellWatch warning"** event fires at the same time, so anyone in Axiom sees
   it too.
6. A **tray notification** pops up for each new or escalated warning or critical alert.

### Every morning: engineer, about 10 minutes

1. **Fleet overview:**
   - The tiles give the day at a glance: critical, warning and watch counts, and the expected failure cost
     for the next 7 days.
   - The **satellite map** shows where the risk sits.
   - **Top 15 wells by expected loss** is the shortlist.
2. **Alert inbox**, for each new alert:
   - Read **Why flagged**. Typical reasons: "11 service jobs in the past year", "last job report
     mentions broken rods", "pump efficiency level".
   - **Acknowledge** it (with your name) if it needs attention.
   - **Shelve** it with a reason and a date if there's a good reason to wait, for example "workover already
     booked for Thursday".
   - Everything goes into the alert's **audit trail** and is mirrored to Canary as `AlertState`.
3. For the history behind an alert, open the well in **Canary Axiom**. The `WellWatchAssets` view puts the
   well's sensors and its WellWatch risk tags side by side.

### Every Monday: crew planner, about 15 minutes

1. **Crew planner**: pick the Monday and set the **weekly budget** and **visit effectiveness**.
2. **Solve plan.** Gurobi picks which at-risk wells each field crew visits over the next 7 days, to
   minimise expected failure loss plus visit cost. It respects:
   - crew hours (a 5.5-hour job plus travel);
   - the budget;
   - crew zones;
   - critical wells first (visited by day 2).

   It solves in about a second.
3. Check the tiles: expected loss with no action, expected loss with the plan, the **saving**, and visits
   with their cost. Then check the **crew × day schedule**.
4. Adjust under **Advanced settings** if reality differs, for example one crew tied up on another job.
   Then solve again.
5. **Send to Canary.** Each planned well gets `PlannedVisitDay` and `PlannedCrew`, visible to everyone
   in Axiom.
6. **Export work orders**: one printable HTML sheet and a CSV per crew, listing each well, the visit day,
   the risk and the reason, ready for the field.

Example from the replay: on Monday 3 August 2026 the plan scheduled **18 visits (C$27,000)** and cut the
week's expected loss from C$289,762 to C$278,234, a saving of **C$11,528**, already net of visit cost.

### In the field: foreman / operator

1. Work from the crew's **work order**: well, day, risk and the reason it was picked.
2. On site, check what the reason points to: rod load, pump fillage, tubing leak, sand.
3. Report back. The engineer **closes** the alert with an outcome: **Fixed**, **False alarm** or
   **Other**.

Those outcomes are the most valuable data the system collects. Over a few months they show how much of a
failure a visit really prevents. Today that is an assumption, and it decides whether the plan pays (break-even:
37% of a failure prevented).

### Every month: manager

1. **Replay & engine → scoreboard:** how many failures were warned about, how far ahead (median 7 days),
   how many warnings were followed by a failure, and the value caught, all against a **simple rule**
   (shortest run life since the last repair) given the same number of warnings.
2. **What if… → value of one more crew per field:** the drop in expected loss from adding a crew, to
   weigh against contractor cost.
3. **What if… → effectiveness 25% / 50% / 75%:** how the plan and its payoff change with that assumption.
4. Review the **repeat failers** (RESULTS.md, section 2) for root-cause work: rod design, corrosion
   treatment, pump sizing. Repairing them again is not enough.

### Every quarter: engineer or analyst

- Update the oil-price table from the latest public quarterly reports, so lost oil is valued at current
  prices.
- Retrain the model (`python -m wellwatch.train`) with the new quarter of data and check the model card.
- Use the closed-alert outcomes to update the visit-effectiveness setting.

## Why each feature helps

### In Canary (the system of record)

| Feature | Why it helps the workflow |
|---|---|
| **WellWatch tags on every well** (`FailureRisk7d`, `ExpectedLoss7d`, `AlertLevel`, `TopDriver`…) | The forecast lives where the operational data already lives. Anyone with Canary or Axiom access sees it without installing anything, and it is stored with history like any other tag. |
| **`WellWatchAssets` asset view** | Each well's sensors and its risk sit under one asset. Comparing "risk went up" with "pump efficiency dropped" is one click, not two systems. |
| **"WellWatch warning" Calcs & Events job** | Canary itself raises an event while a well is at warning or above. Teams that live in Axiom get the alarm natively, and it keeps working even if nobody has the app open. |
| **Fleet tags** (`OpenAlerts`, `ExpectedLoss7d`, `CatchRate`…) | Fleet KPIs can go on any Axiom display or report next to production KPIs. |
| **`PlannedVisitDay` / `PlannedCrew` tags** | The weekly plan is visible to the whole team in Canary, not only to the planner. |

### Fleet overview

| Feature | Why it helps |
|---|---|
| **KPI tiles** (wells scored, critical / warning / watch, expected cost next 7 days) | A 10-second read of whether today is a normal day or a bad one. |
| **Day picker** | Go back to any day to review what WellWatch said then; useful for post-failure reviews. |
| **Satellite / terrain / light map**, colour = alert level, size = expected loss | Shows where risk clusters, which crews it affects and how far apart the wells are. Click a well for its risk, cost and reason. |
| **Top 15 by expected loss** | The shortlist, ranked by money at stake rather than raw probability, so attention goes where a failure costs most. |
| **Field summary** | Running wells, expected loss and open alerts per field, which maps to how crews and foremen are organised. |

### Alert inbox

| Feature | Why it helps |
|---|---|
| **Alert lifecycle in the ISA-18.2 style** (Open → Acknowledged → Shelved → Closed) | The standard way control rooms manage alarms. Nothing gets lost and nothing is silently ignored. |
| **Only warning and critical open alerts**, about 12 warnings a day across 900+ wells | Limits alarm fatigue: the list stays short enough to act on. |
| **"Why flagged" on every alert** | Engineers act on reasons, not scores. "11 service jobs in the past year" tells them what to check. |
| **Shelve with a required reason and end date** | Silences known cases (already booked, shut in) without losing them; they come back on the date. |
| **Close with an outcome** (Fixed / False alarm / Other) | Builds the evidence for visit effectiveness, the number that decides the business case. |
| **Audit trail** (who, when, what) | Accountability and handover between shifts; useful for incident reviews. |
| **Mirrored to Canary (`AlertState`)** | Someone looking in Axiom sees that an alert is already acknowledged or shelved. |
| **Auto-close**: on failure ("Failure occurred") or after 7 quiet days ("Cleared") | The inbox cleans itself up, and every closed alert says how it ended. |

### Crew planner

| Feature | Why it helps |
|---|---|
| **Gurobi optimisation: minimise expected loss + visit cost** | Replaces gut feel and first-come-first-served with the plan that saves the most money for the crews and budget available. |
| **Constraints: crew hours, travel, budget, zones, critical by day 2** | The plan is something crews can actually do, not a wish list. |
| **Crews per field from the records (editable)** | Starts from how many crews each field really runs, and adapts when that changes. |
| **Tiles: no-action vs plan, saving, visits / cost** | One-glance justification for the week's proactive work. |
| **Crew × day schedule and visit list** | Directly usable to dispatch crews. |
| **Export work orders** (HTML + CSV per crew) | Printable or emailable sheets for the field; the CSV loads into other tools. |
| **Send to Canary** | Publishes the plan to everyone, and keeps a record of what was planned for later review. |
| **What-if: one more crew per field** | Puts a dollar value on adding capacity, for contractor and staffing decisions. |
| **What-if: effectiveness 25 / 50 / 75%** | Shows how sensitive the plan and its payoff are to the main assumption. |
| **HiGHS fallback** | Planning still works on any laptop without a Gurobi licence. |

### Replay & engine

| Feature | Why it helps |
|---|---|
| **Live engine start / stop** | Runs the daily scoring from the app; a clean stop never leaves Canary in a bad state. |
| **One-writer lock** | Two engines can never write the same Canary tags at once. |
| **Replay** of held-back days through Canary | Proves the system on days it never saw, through the real data path, before anyone relies on it. |
| **Scoreboard vs a simple rule** | Honest evidence: value caught, lead time, warnings that came true, failures caught. It shows where the model wins and where the simple rule does better. |
| **Lead-time chart** | Shows warnings typically arrive about 7 days ahead, which is enough time to book a crew. |
| **Backups and restore** | Alerts, audit trail and scores are backed up automatically before risky operations and can be restored in one click. |

### Settings & health

| Feature | Why it helps |
|---|---|
| **Model card** | What the model was trained on, how it was tested and its strongest inputs. That builds trust and supports review. |
| **Data-quality check** (efficiency, torque or current unchanged for 14 days) | Finds stuck sensors and stale manual entries. Garbage in means wrong warnings, so this sends the instrumentation team to the right wells. |
| **API token in Windows Credential Manager** | No passwords in files or code; follows normal IT security practice. |
| **Offline mode** | Review alerts, plans and results without a Canary connection, for example travelling or in a meeting. |
| **Tray notifications** (new or escalated warning / critical only; never shelved) | You hear about what matters without watching a screen, and aren't nagged about what you have parked. |
| **Light / dark theme, operator name** | Small comforts for an app that stays open all day; your name pre-fills the audit trail. |

## What it would take to run this for real

1. **Live data:** connect the engine to the same Canary datasets the field already writes to. The engine
   reads its daily values from Canary, so only the tag mapping changes.
2. **Feedback loop:** make closing alerts with an outcome part of the routine for 2–3 months, to measure
   visit effectiveness.
3. **Crew zones and costs:** confirm crews per field, travel times and the cost of an inspection visit
   with the operations team.
4. **Coverage:** add a per-field minimum, so fields with cheaper failures (for example Cactus Lake) still
   get warnings, and combine the model with the run-life rule for rod failures.
5. **Work-order hand-off:** send the exported CSV into the company's maintenance system instead of
   printing.
6. **Records:** record a failure type for every crew-needing failure. Today three quarters have none,
   which limits learning by failure type.
