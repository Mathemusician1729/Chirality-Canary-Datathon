"""Downtime-code classification shared by the analysis notebook and the app.

The daily file mixes two coding systems (descriptive codes such as "Downhole Equipment / Rods" and
legacy short codes such as "RODSPART / DH - Rodstring Failure"). Ordered keyword rules map every
(downtime_code_2, downtime_code_3) pair to one problem and one class. First match wins; the text
searched is "code_2 | code_3" in lower case.
"""

from __future__ import annotations

import pandas as pd

DOWNTIME_RULES = [
    # (keywords, problem, class)
    (["uneco", "long term shut", "long-term shut", "ltsi", "high opex", "high watercut", "low inflow"],
     "Uneconomic / long-term shut-in", "Economic"),
    (["rod"], "Rod string failure", "Equipment failure"),
    (["pump"], "Pump failure / problems", "Equipment failure"),
    (["tubing"], "Tubing failure", "Equipment failure"),
    (["torqued"], "Well torqued", "Equipment failure"),
    (["wellhead drive", "stuffing"], "Wellhead drive / stuffing box", "Equipment failure"),
    (["engine/motor"], "Engine / motor down", "Equipment failure"),
    (["electrical", "genset", "elfail"], "Electrical failure", "Equipment failure"),
    (["belt"], "Belt problems", "Equipment failure"),
    (["compressor"], "Compressor problems", "Equipment failure"),
    (["packer", "gas-locking", "loaded", "waxed", "plunger", "gas lift failure", "downhole obstruction",
      "disposal well repairs", "down hole failure", "downhole equipment | unspecified", "service rig on hole"],
     "Other downhole problem", "Equipment failure"),
    (["flowline failure", "flowline maintenance", "flowline hydrate", "hydrate", "obstruction",
      "pipeline/equipment failure"], "Flowline / pipeline problem", "Equipment failure"),
    (["esd", "high pressure/level", "process upset", "process shutdown"],
     "Facility upset / emergency shutdown", "Equipment failure"),
    (["equipment failure", "equipment malfunction"], "Other equipment failure", "Equipment failure"),
    (["limited capacity", "capacity constraint", "high pipeline pressure", "restrictions",
      "water source supply", "lift gas supply", "trucking", "third party"],
     "Facility capacity / supply constraint", "Facility constraint"),
    (["grid", "weather", "planned outage", "scrl", "power"], "Power outage / weather", "External"),
    (["regulatory", "spill", "lease", "road", "non-operated", "non - op", "surface access"],
     "Regulatory / environment / access", "External"),
    (["drill", "frac", "polymer", "eor", "voidage", "offset", "proximity", "completions",
      "stimulation", "optimization"], "Development / drilling / stimulation", "Development"),
    (["turn around", "maintenance", "workover", "construction", "install new", "tie-in", "tie in",
      "artificial lift", "pressure acquisition", "facility downtime"],
     "Planned maintenance / turnaround", "Planned maintenance"),
]


def classify_code(code_2, code_3):
    if pd.isna(code_2) and pd.isna(code_3):
        return "Unknown (no code)", "Unknown"
    text = f"{code_2} | {code_3}".lower()
    for keywords, problem, cls in DOWNTIME_RULES:
        if any(k in text for k in keywords):
            return problem, cls
    return "Unmatched", "Unknown"
