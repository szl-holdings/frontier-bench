"""Certified puzzle families: zebra and futoshiki.

Generate -> encode -> certify loop, seeded and regenerable:

  1. sample a ground-truth solution uniformly at random (seeded);
  2. build a candidate clue pool of clues TRUE under that solution;
  3. add clues in seeded random order until the puzzle is certified to have
     exactly one solution (dual encodings + declarative check must agree);
  4. minimize: drop every clue whose removal keeps the solution unique.

Every shipped puzzle is clue-minimal: removing any single clue creates a
second solution. The certificate therefore also encodes difficulty honestly:
clue-minimal puzzles with fewer clues are harder in the sense that less
information determines the answer. No further difficulty claim is made.
"""
from __future__ import annotations

import hashlib
import json
import random

from .csp import CSP, alldiff, table


def canonical_json(obj):
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, allow_nan=False)


def puzzle_id(spec):
    return hashlib.sha256(canonical_json(spec).encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------- zebra

ZEBRA_CATEGORIES_4 = [
    ("color", ["red", "blue", "green", "white"]),
    ("pet", ["dog", "cat", "bird", "fish"]),
    ("drink", ["coffee", "tea", "milk", "water"]),
    ("job", ["doctor", "teacher", "artist", "engineer"]),
]

ZEBRA_CATEGORIES_5 = [
    ("color", ["red", "blue", "green", "white", "purple"]),
    ("pet", ["dog", "cat", "bird", "fish", "horse"]),
    ("drink", ["coffee", "tea", "milk", "water", "cocoa"]),
    ("job", ["doctor", "teacher", "artist", "engineer", "baker"]),
    ("flower", ["rose", "tulip", "daisy", "lily", "orchid"]),
]


def _zebra_csp(n, categories):
    names = []
    domains = []
    for cat, values in categories:
        for v in values:
            names.append(f"pos:{cat}:{v}")
            domains.append(list(range(1, n + 1)))
    csp = CSP(names, domains)
    for cat, values in categories:
        idxs = [csp.index[f"pos:{cat}:{v}"] for v in values]
        csp.add_rule(alldiff(idxs, meta={"category": cat}))
    return csp


def _zebra_clue_pool(rng, csp, categories, truth, n):
    """Candidate clues, all TRUE under `truth` (index -> house)."""
    pool = []
    var = {}
    for cat, values in categories:
        for v in values:
            var[(cat, v)] = csp.index[f"pos:{cat}:{v}"]

    def t(cat, v):
        return truth[var[(cat, v)]]

    for cat1, v1s in categories:
        for cat2, v2s in categories:
            if cat1 >= cat2:
                continue
            for a in v1s:
                for b in v2s:
                    if t(cat1, a) == t(cat2, b):
                        pool.append(
                            table(
                                [var[(cat1, a)], var[(cat2, b)]],
                                lambda p, q: p == q,
                                meta={"type": "same", "cat1": cat1, "a": a, "cat2": cat2, "b": b},
                            )
                        )
                    for k in (1, 2):
                        if t(cat2, b) - t(cat1, a) == k:
                            pool.append(
                                table(
                                    [var[(cat1, a)], var[(cat2, b)]],
                                    lambda p, q, k=k: q - p == k,
                                    meta={"type": "left_k", "k": k, "cat1": cat1, "a": a, "cat2": cat2, "b": b},
                                )
                            )
                    if abs(t(cat1, a) - t(cat2, b)) == 1:
                        pool.append(
                            table(
                                [var[(cat1, a)], var[(cat2, b)]],
                                lambda p, q: abs(p - q) == 1,
                                meta={"type": "adjacent", "cat1": cat1, "a": a, "cat2": cat2, "b": b},
                            )
                        )
    rng.shuffle(pool)
    return pool


def generate_zebra(seed, categories=None):
    categories = categories or ZEBRA_CATEGORIES_4
    n = len(categories[0][1])
    rng = random.Random(f"zebra:{seed}")
    csp = _zebra_csp(n, categories)
    # ground truth: one seeded permutation per category. perm[i] occupies
    # house i+1, so value v sits at house perm.index(v)+1.
    truth = {}
    for cat, values in categories:
        perm = list(values)
        rng.shuffle(perm)
        for v in values:
            truth[csp.index[f"pos:{cat}:{v}"]] = perm.index(v) + 1
    pool = _zebra_clue_pool(rng, csp, categories, truth, n)
    clues = []
    for clue in pool:
        clues.append(clue)
        csp.clues = clues
        sols, saturated, consistent = csp.enumerate_solutions_direct(cap=2)
        if saturated and consistent and len(sols) == 1:
            break
    if not clues:
        return None, 1
    # minimize in seeded random order
    rng.shuffle(clues)  # vary which redundant clues get dropped first
    i = 0
    while i < len(clues):
        trial = clues[:i] + clues[i + 1 :]
        csp.clues = trial
        sols, saturated, consistent = csp.enumerate_solutions_direct(cap=2)
        if saturated and consistent and len(sols) == 1:
            clues = trial  # clue i removed; do not advance
        else:
            i += 1
    csp.clues = clues
    if not csp.certify_unique():  # final gate: dual encodings + check()
        return None, 1
    sols, _, _ = csp.enumerate_solutions(cap=2)
    solution = sols[0]
    if not csp.check(solution):
        return None, 1
    spec = {
        "family": "zebra",
        "houses": n,
        "categories": [[c, vs] for c, vs in categories],
        "clues": [c.meta for c in clues],
        "solution": {
            cat: {v: solution[csp.index[f"pos:{cat}:{v}"]] for v in values}
            for cat, values in categories
        },
    }
    spec["id"] = puzzle_id({k: v for k, v in spec.items() if k != "id"})
    return {
        "spec": spec,
        "tier": f"z{n}",
        "clue_count": len(clues),
        "minimal": True,
        "certified_solution": spec["solution"],
    }, 0


# ------------------------------------------------------------- futoshiki

def _futoshiki_csp(n):
    names = [f"cell:r{r}c{c}" for r in range(1, n + 1) for c in range(1, n + 1)]
    csp = CSP(names, [list(range(1, n + 1))] * (n * n))
    for r in range(1, n + 1):
        csp.add_rule(alldiff([csp.index[f"cell:r{r}c{c}"] for c in range(1, n + 1)], meta={"row": r}))
    for c in range(1, n + 1):
        csp.add_rule(alldiff([csp.index[f"cell:r{r}c{c}"] for r in range(1, n + 1)], meta={"col": c}))
    return csp


def _latin_square(rng, n):
    """Seeded Latin square: cyclic base + seeded row/col/value permutations.

    Honest limitation (see DATASHEET.md): this samples from a structured
    family of Latin squares, not uniformly from all Latin squares. The
    certification loop does not depend on uniformity.
    """
    row_perm = list(range(n))
    col_perm = list(range(n))
    val_perm = list(range(1, n + 1))
    rng.shuffle(row_perm)
    rng.shuffle(col_perm)
    rng.shuffle(val_perm)
    square = [[0] * n for _ in range(n)]
    for r in range(n):
        for c in range(n):
            cyclic = (row_perm[r] + col_perm[c]) % n  # cyclic Latin square
            square[r][c] = val_perm[cyclic]
    return square


def generate_futoshiki(seed, n=4):
    rng = random.Random(f"futoshiki:{seed}")
    square = _latin_square(rng, n)
    if square is None:
        return None, 1
    csp = _futoshiki_csp(n)
    pool = []
    for r in range(1, n + 1):
        for c in range(1, n):
            a, b = square[r - 1][c - 1], square[r - 1][c]
            op = "<" if a < b else ">"
            pool.append(
                table(
                    [csp.index[f"cell:r{r}c{c}"], csp.index[f"cell:r{r}c{c + 1}"]],
                    (lambda p, q, op=op: (p < q) if op == "<" else (p > q)),
                    meta={"type": "h", "r": r, "c": c, "op": op},
                )
            )
    for r in range(1, n):
        for c in range(1, n + 1):
            a, b = square[r - 1][c - 1], square[r][c - 1]
            op = "<" if a < b else ">"
            pool.append(
                table(
                    [csp.index[f"cell:r{r}c{c}"], csp.index[f"cell:r{r + 1}c{c}"]],
                    (lambda p, q, op=op: (p < q) if op == "<" else (p > q)),
                    meta={"type": "v", "r": r, "c": c, "op": op},
                )
            )
    rng.shuffle(pool)
    clues = []
    while pool:
        clue = pool.pop(0)
        clues.append(clue)
        csp.clues = clues
        sols, saturated, consistent = csp.enumerate_solutions_direct(cap=2)
        if saturated and consistent and len(sols) == 1:
            break
    rng.shuffle(clues)  # vary which redundant clues get dropped first
    i = 0
    while i < len(clues):
        trial = clues[:i] + clues[i + 1 :]
        csp.clues = trial
        sols, saturated, consistent = csp.enumerate_solutions_direct(cap=2)
        if saturated and consistent and len(sols) == 1:
            clues = trial  # clue i removed; do not advance
        else:
            i += 1
    csp.clues = clues
    if not csp.certify_unique():  # final gate: dual encodings + check()
        return None, 1
    sols, _, _ = csp.enumerate_solutions(cap=2)
    solution = sols[0]
    if not csp.check(solution):
        return None, 1
    sol_grid = {
        f"r{r}c{c}": solution[csp.index[f"cell:r{r}c{c}"]]
        for r in range(1, n + 1)
        for c in range(1, n + 1)
    }
    spec = {
        "family": "futoshiki",
        "size": n,
        "clues": [c.meta for c in clues],
        "solution": sol_grid,
    }
    spec["id"] = puzzle_id({k: v for k, v in spec.items() if k != "id"})
    return {
        "spec": spec,
        "tier": f"f{n}",
        "clue_count": len(clues),
        "minimal": True,
        "certified_solution": sol_grid,
    }, 0


def generate(seed, family="zebra", n=None):
    """Dispatch by family name: 'zebra' or 'futoshiki' (with size n)."""
    if family == "zebra":
        cats = ZEBRA_CATEGORIES_4 if (n or 4) == 4 else ZEBRA_CATEGORIES_5
        return generate_zebra(seed, cats)
    if family == "futoshiki":
        return generate_futoshiki(seed, n or 4)
    raise ValueError(f"unknown family: {family}")
