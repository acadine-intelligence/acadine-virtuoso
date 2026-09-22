"""Illustrative rule-based annotator for Virtuoso's annotation contract.

This module answers the bundled ``item_quality_v1`` question set from the
item text alone. It calls no model and opens no network connection, so it
declares ``network: none``. Real annotators plug a model into the same shape.
The answers are calibration-free heuristics: they show the contract, they do
not judge items well.
"""

from __future__ import annotations

import json
import sys


def _clause_count(text: str) -> int:
    separators = (" and ", " or ", ";", "?")
    return 1 + sum(text.lower().count(token) for token in separators)


def main() -> None:
    request = json.load(sys.stdin)
    projection = request["projections"]["annotation.request"]
    subject = projection["subject"]
    prompt = subject.get("prompt", "")
    answer = subject.get("answer", "")
    hint = subject.get("hint", "")

    clauses = _clause_count(prompt)
    atomic = 0.9 if clauses == 1 else max(0.1, 0.9 - 0.3 * (clauses - 1))
    supports = 0.8 if len(answer.split()) <= 60 else 0.5
    words = len(prompt.split()) + len(answer.split())
    difficulty = "easy" if words < 25 else "moderate" if words < 80 else "hard"
    leak = 0.9 if hint and answer.lower() in hint.lower() else 0.1

    answers = {}
    for question in projection["questions"]:
        question_id = question["id"]
        if question_id == "atomic":
            value, confidence = atomic, 0.6
        elif question_id == "answer_supports_prompt":
            value, confidence = supports, 0.4
        elif question_id == "difficulty":
            value, confidence = difficulty, 0.5
        elif question_id == "hint_leaks_answer":
            value, confidence = leak, 0.7 if hint else 0.9
        else:
            raise SystemExit(f"rule-annotator does not know question {question_id}")
        answers[question_id] = {
            "primitive": question["primitive"],
            "value": value,
            "confidence": confidence,
        }

    response = {
        "schema": "virtuoso/module-result@0.1",
        "module_id": "rule-annotator",
        "kind": "annotation-answers",
        "payload": {"answers": answers},
    }
    json.dump(response, sys.stdout, allow_nan=False, sort_keys=True)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
