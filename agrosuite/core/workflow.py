"""The guided workflow.

Work on field data follows an order, and the order is not a preference: each
step depends on the one before it having been done. Cleaning before knowing
the units bakes the wrong units into the clean copy. Analysing before
cleaning fits a curve to overlap and headland turns. Exporting before
analysing sends a prescription nobody checked.

This module holds that order as data, so the app can always answer three
questions without being asked: where am I, what is missing, and what should I
do next.

A *project* is the set of files that describe one field-and-season. A DIFM
analysis needs more than one: the plan that was made, the as-applied log that
records what the machine actually did, the yield map that records what came
of it, and the prices that turn yield into money. The workflow tracks which
of those are present and names the ones that are not.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: The stages, in the order they have to happen.
STAGES = ("load", "review", "clean", "analyse", "export")

STAGE_LABELS = {
    "load": "Load the files",
    "review": "Review what came in",
    "clean": "Clean the data",
    "analyse": "Analyse",
    "export": "Send it to the monitor",
}

STAGE_DESCRIPTIONS = {
    "load": "Open what came off the monitor. The app identifies the platform, "
            "normalizes the columns and runs a first look at the file.",
    "review": "Confirm the units and the role of each file before anything "
              "downstream inherits a wrong assumption.",
    "clean": "Remove overlap, headland turns and sensor faults, and read the "
             "report to judge whether the cleaning was sound.",
    "analyse": "Fit the response and find the economic optimum, or cross "
               "vigour against rate for an Augmenta session.",
    "export": "Build the package for the target monitor and check it before "
              "the stick leaves the computer.",
}


@dataclass
class Requirement:
    """One thing an analysis needs before it can run."""

    key: str
    label: str
    detail: str
    satisfied: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "detail": self.detail,
            "satisfied": self.satisfied,
        }


#: What each kind of analysis needs. Roles come from
#: :mod:`agrosuite.core.preflight`.
GOALS = {
    "difm": {
        "label": "DIFM analysis",
        "description": "Yield response to rate, and the economic optimum.",
        "requires": [
            ("yield", "Yield map",
             "The harvest file. Without it there is no response to measure."),
            ("rate", "Applied rate",
             "The as-applied log, or the plan if that is all there is. The "
             "as-applied is better: the response follows what actually went out."),
            ("prices", "Crop price and input cost",
             "The optimum is an economic question. Without prices the app can "
             "only report the agronomic maximum, which is a different number."),
        ],
        "optional": [
            ("plan", "Plan / prescription",
             "Lets the app show how far the machine drifted from the map."),
            ("zone", "Management zones",
             "Turns one curve into one per zone, which is what justifies "
             "variable rate."),
        ],
    },
    "augmenta": {
        "label": "Augmenta analysis",
        "description": "Whether the vision system actually modulated the rate.",
        "requires": [
            ("vigor", "Augmenta session",
             "A file carrying the vigour index and the applied rate together."),
        ],
        "optional": [],
    },
    "export": {
        "label": "Send to a monitor",
        "description": "A package laid out the way the target platform expects.",
        "requires": [
            ("geometry", "Something with geometry",
             "A prescription, a trial layout or a field boundary."),
        ],
        "optional": [
            ("guidance", "AB lines",
             "Carried in the same ISOXML as the boundary and the prescription."),
        ],
    },
}


def evaluate(goal: str, roles: set[str], has_prices: bool = False,
             has_geometry: bool = False, has_zone: bool = False) -> dict[str, Any]:
    """Check a goal against what the project actually holds."""
    spec = GOALS.get(goal)
    if spec is None:
        raise ValueError(f"Unknown goal: '{goal}'.")

    def satisfied(key: str) -> bool:
        if key == "rate":
            return bool(roles & {"as_applied", "plan"})
        if key == "prices":
            return has_prices
        if key == "geometry":
            return has_geometry or bool(roles & {"plan", "boundary"})
        if key == "zone":
            return has_zone
        return key in roles

    requirements = [
        Requirement(key, label, detail, satisfied(key))
        for key, label, detail in spec["requires"]
    ]
    optional = [
        Requirement(key, label, detail, satisfied(key))
        for key, label, detail in spec["optional"]
    ]
    missing = [r for r in requirements if not r.satisfied]

    return {
        "goal": goal,
        "label": spec["label"],
        "description": spec["description"],
        "ready": not missing,
        "requirements": [r.to_dict() for r in requirements],
        "optional": [r.to_dict() for r in optional],
        "missing": [r.to_dict() for r in missing],
        "summary": (
            spec["description"] if not missing
            else "Still missing: " + ", ".join(r.label.lower() for r in missing) + "."
        ),
    }


def stage_of(
    layer_count: int,
    reviewed: bool,
    cleaned: bool,
    analysed: bool,
    exported: bool,
) -> str:
    """Where the project currently stands.

    The stage is derived from what has happened, not stored, so it can never
    disagree with the actual state of the session.
    """
    if not layer_count:
        return "load"
    if not reviewed:
        return "review"
    if not cleaned:
        return "clean"
    if not analysed:
        return "analyse"
    return "export" if not exported else "export"


def next_action(stage: str, goal: str, evaluation: dict[str, Any]) -> dict[str, Any]:
    """The single next thing to do, and why."""
    if stage == "load":
        return {
            "step": "load",
            "label": "Load a file",
            "why": "Nothing has been opened yet.",
        }
    if stage == "review":
        return {
            "step": "review",
            "label": "Review the files that came in",
            "why": "Units and roles have to be right before anything downstream "
                   "inherits them.",
        }
    if not evaluation["ready"]:
        first = evaluation["missing"][0]
        return {
            "step": "load",
            "label": f"Add: {first['label']}",
            "why": first["detail"],
        }
    if stage == "clean":
        return {
            "step": "clean",
            "label": "Clean the data",
            "why": "Raw monitor data carries overlap, turns and sensor faults that "
                   "distort every statistic that follows.",
        }
    if stage == "analyse":
        return {
            "step": "analyse",
            "label": f"Run the {evaluation['label'].lower()}",
            "why": evaluation["description"],
        }
    return {
        "step": "export",
        "label": "Build the package for the monitor",
        "why": "The analysis is done; what is left is getting it onto the machine.",
    }


def describe() -> dict[str, Any]:
    """The whole workflow, for the interface to draw."""
    return {
        "stages": [
            {
                "key": key,
                "label": STAGE_LABELS[key],
                "description": STAGE_DESCRIPTIONS[key],
            }
            for key in STAGES
        ],
        "goals": {
            key: {"label": spec["label"], "description": spec["description"]}
            for key, spec in GOALS.items()
        },
    }
