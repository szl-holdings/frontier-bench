"""Exact-text rendering of certified puzzles.

Doctrine (Behnamian et al., Sec. E1): the evaluator certifies THE EXACT TEXT
the solver reads. The prompt is rendered deterministically from the clue
predicates (never hand-edited), and its sha256 is recorded in the receipt,
so a score is bound to the bytes that were actually sent.
"""
from __future__ import annotations

import hashlib

from .families import canonical_json


def render_zebra(spec):
    n = spec["houses"]
    cats = spec["categories"]
    lines = [
        f"Grid logic puzzle. {n} houses in a row, numbered 1 to {n} from "
        "left to right. Each house has one occupant. Every occupant has "
        "exactly one value from each category below, and no two occupants "
        "share a value within a category."
    ]
    for cat, values in cats:
        lines.append(f"- {cat}: " + ", ".join(values))
    lines.append("")
    lines.append("Clues:")
    for i, m in enumerate(spec["clues"], start=1):
        if m["type"] == "same":
            text = (f"The occupant with the {m['a']} {m['cat1']} has the "
                    f"{m['b']} {m['cat2']}.")
        elif m["type"] == "left_k":
            if m["k"] == 1:
                text = (f"The occupant with the {m['a']} {m['cat1']} lives "
                        f"directly to the left of the occupant with the "
                        f"{m['b']} {m['cat2']}.")
            else:
                text = (f"The occupant with the {m['a']} {m['cat1']} lives "
                        f"exactly {m['k']} houses to the left of the occupant "
                        f"with the {m['b']} {m['cat2']}.")
        elif m["type"] == "adjacent":
            text = (f"The occupant with the {m['a']} {m['cat1']} lives "
                    f"next to the occupant with the {m['b']} {m['cat2']}.")
        else:  # pragma: no cover - generator only emits the types above
            raise ValueError(f"unrenderable clue: {m}")
        lines.append(f"{i}. {text}")
    answer = {
        "solution": {cat: {v: 0 for v in values} for cat, values in cats}
    }
    lines.append("")
    lines.append(
        "Deduce the house number of every value of every category. Answer "
        "with exactly one JSON object of the form "
        + canonical_json(answer).replace(": 0", ": <house number 1-" + str(n) + ">")
        + " and nothing else."
    )
    return "\n".join(lines)


def render_futoshiki(spec):
    n = spec["size"]
    clue_map = {}
    for m in spec["clues"]:
        if m["type"] == "h":
            clue_map[("h", m["r"], m["c"])] = m["op"]
        else:
            clue_map[("v", m["r"], m["c"])] = m["op"]
    lines = [
        f"Fill the {n}x{n} grid with the numbers 1 to {n} so that every row "
        "and every column contains each number exactly once, and every "
        "inequality holds."
    ]
    for r in range(1, n + 1):
        cells = []
        for c in range(1, n + 1):
            cells.append("_")
            if c < n:
                cells.append(clue_map.get(("h", r, c), "."))
        lines.append(f"row {r}: " + " ".join(cells))
    for c in range(1, n + 1):
        marks = []
        for r in range(1, n):
            marks.append(clue_map.get(("v", r, c), "."))
        lines.append(f"col {c} vertical: " + " ".join(marks))
    answer = {"solution": {f"r{r}c{c}": 0 for r in range(1, n + 1) for c in range(1, n + 1)}}
    lines.append("")
    lines.append(
        "In the vertical row, the k-th mark compares the cell in row k "
        "with the cell directly below it in the same column. Deduce the "
        "full grid. Answer with exactly one JSON object of the form "
        + canonical_json(answer).replace(": 0", ": <number 1-" + str(n) + ">")
        + " and nothing else."
    )
    return "\n".join(lines)


def render(spec):
    if spec["family"] == "zebra":
        return render_zebra(spec)
    return render_futoshiki(spec)


def prompt_sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
