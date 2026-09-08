"""Illustrative fixed review ladder for Virtuoso's scheduler module contract."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta


def main() -> None:
    request = json.load(sys.stdin)
    projection = request["projections"]["scheduler.request"]
    configuration = projection["configuration"]
    intervals = configuration.get("intervals", [1, 3, 7, 14, 30])
    previous = projection["previous_state"]
    attempt = projection["attempt"]
    if attempt["result"] == "not-demonstrated":
        step = 0
    elif previous is None:
        step = 0
    else:
        step = min(previous["step"] + 1, len(intervals) - 1)
    occurred_at = datetime.fromisoformat(attempt["occurred_at"])
    due_at = occurred_at + timedelta(days=intervals[step])
    due = due_at.isoformat()
    response = {
        "schema": "virtuoso/module-result@0.1",
        "module_id": "fixed-ladder",
        "kind": "scheduler-proposal",
        "payload": {
            "due_at": due,
            "algorithm": "fixed-ladder",
            "algorithm_version": "1.0.0",
            "learning_context": projection["learning_context"],
            "configuration": configuration,
            "proposed_state": {"step": step, "due": due},
            "rationale": (
                f"Illustrative fixed ladder step {step}: "
                f"next review in {intervals[step]} day(s)."
            ),
        },
    }
    json.dump(response, sys.stdout, allow_nan=False, sort_keys=True)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
