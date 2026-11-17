"""Bounded exact nonorientable surface-group orbit counts on finite groups.

Research fixture: N_g = #^g RP^2, pi_1=<a_1,...,a_g|a_1^2...a_g^2=1>.
Classifies discrete finite-G principal bundles via conjugacy orbits of
homomorphisms. Does NOT calculate block Picard groups or validate a vendor
benchmark reference answer. Python stdlib only; no provider calls.
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import permutations, product
import hashlib
import json
import re


class InvalidFiniteGroup(ValueError):
    pass


class CostCeilingExceeded(ValueError):
    pass


@dataclass(frozen=True)
class FiniteGroup:
    name: str
    multiplication: tuple[tuple[int, ...], ...]
    identity: int = 0

    def __post_init__(self) -> None:
        n = len(self.multiplication)
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,40}", self.name):
            raise InvalidFiniteGroup("GROUP_NAME_INVALID")
        if not 1 <= n <= 12:
            raise InvalidFiniteGroup("GROUP_ORDER_OUT_OF_BOUNDS")
        if type(self.identity) is not int or not 0 <= self.identity < n:
            raise InvalidFiniteGroup("IDENTITY_INVALID")
        for row in self.multiplication:
            if not isinstance(row, tuple) or len(row) != n:
                raise InvalidFiniteGroup("TABLE_SHAPE_INVALID")
            if any(type(v) is not int or not 0 <= v < n for v in row):
                raise InvalidFiniteGroup("TABLE_ENTRY_INVALID")
        op = self.multiplication
        e = self.identity
        for i in range(n):
            if op[i][e] != i or op[e][i] != i:
                raise InvalidFiniteGroup("NOT_AN_IDENTITY")
            if not any(op[i][j] == e and op[j][i] == e for j in range(n)):
                raise InvalidFiniteGroup("INVERSE_MISSING")
        for a, b, c in product(range(n), repeat=3):
            if op[op[a][b]][c] != op[a][op[b][c]]:
                raise InvalidFiniteGroup("NONASSOCIATIVE_TABLE")

    @property
    def order(self) -> int:
        return len(self.multiplication)

    def inverse(self, element: int) -> int:
        op = self.multiplication
        return next(i for i in range(self.order) if op[element][i] == self.identity
                    and op[i][element] == self.identity)

    def is_abelian(self) -> bool:
        op = self.multiplication
        return all(op[i][j] == op[j][i]
                   for i in range(self.order) for j in range(self.order))

    def source_sha256(self) -> str:
        return hashlib.sha256(json.dumps({
            "identity": self.identity, "multiplication": self.multiplication,
            "name": self.name,
        }, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def cyclic_group(n: int) -> FiniteGroup:
    if type(n) is not int or not 1 <= n <= 12:
        raise InvalidFiniteGroup("CYCLIC_ORDER_INVALID")
    return FiniteGroup("C" + str(n), tuple(
        tuple((i + j) % n for j in range(n)) for i in range(n)))


def symmetric_group_three() -> FiniteGroup:
    perms = tuple(permutations(range(3)))
    indices = {p: i for i, p in enumerate(perms)}
    # (a*b)(k) = a(b(k)) preserves the identity at permutation index 0.
    return FiniteGroup("S3", tuple(tuple(indices[tuple(a[b[k]] for k in range(3))]
        for b in perms) for a in perms))


def quaternion_group() -> FiniteGroup:
    """The standard eight-element Q8, generated locally without vendor input.

    Index 2*basis + sign (0 positive, 1 negative); basis is 1,i,j,k.
    """
    basis = {
        (0, 0): (1, 0), (0, 1): (1, 1), (0, 2): (1, 2), (0, 3): (1, 3),
        (1, 0): (1, 1), (2, 0): (1, 2), (3, 0): (1, 3),
        (1, 1): (-1, 0), (2, 2): (-1, 0), (3, 3): (-1, 0),
        (1, 2): (1, 3), (2, 1): (-1, 3),
        (2, 3): (1, 1), (3, 2): (-1, 1),
        (3, 1): (1, 2), (1, 3): (-1, 2),
    }

    def multiply(a: int, b: int) -> int:
        sign, unit = basis[(a // 2, b // 2)]
        total_sign = sign * (-1 if a % 2 else 1) * (-1 if b % 2 else 1)
        return 2 * unit + int(total_sign == -1)

    return FiniteGroup("Q8", tuple(tuple(multiply(a, b) for b in range(8))
                                    for a in range(8)))


def direct_product_group(left: FiniteGroup, right: FiniteGroup) -> FiniteGroup:
    n, m = left.order, right.order
    if n * m > 12:
        raise CostCeilingExceeded("DIRECT_PRODUCT_ORDER_OVER_LIMIT")
    pairs = list(product(range(n), range(m)))
    table = tuple(tuple(
        left.multiplication[a][c] * m + right.multiplication[b][d]
        for c, d in pairs) for a, b in pairs)
    e = left.identity * m + right.identity
    return FiniteGroup(left.name + "_" + right.name, table, e)


def _genus(genus: int) -> None:
    if type(genus) is not int or not 1 <= genus <= 5:
        raise CostCeilingExceeded("NONORIENTABLE_GENUS_OUT_OF_BOUNDS")


def count_orbits_burnside(group: FiniteGroup, genus: int = 5) -> int:
    """Burnside's lemma with independent per-centralizer group-word DP."""
    _genus(genus)
    n = group.order
    op = group.multiplication
    square = [op[x][x] for x in range(n)]
    total_fixed = 0
    for h in range(n):
        centralizer = [x for x in range(n) if op[h][x] == op[x][h]]
        dp = {group.identity: 1}
        for _ in range(genus):
            upcoming: dict[int, int] = {}
            for prefix, count in dp.items():
                for x in centralizer:
                    dest = op[prefix][square[x]]
                    upcoming[dest] = upcoming.get(dest, 0) + count
            dp = upcoming
        total_fixed += dp.get(group.identity, 0)
    if total_fixed % n:
        raise AssertionError("BURNSIDE_DIVISIBILITY_FAILURE")
    return total_fixed // n


def count_orbits_direct(group: FiniteGroup, genus: int = 5,
                        max_assignments: int = 100_000) -> int:
    """Independent explicit-orbit enumeration, separate from Burnside DP."""
    _genus(genus)
    n = group.order
    if type(max_assignments) is not int or n**genus > max_assignments:
        raise CostCeilingExceeded("DIRECT_ENUMERATION_BUDGET")
    op = group.multiplication
    square = [op[x][x] for x in range(n)]
    inverses = [group.inverse(h) for h in range(n)]
    conjugate = [[op[op[h][x]][inverses[h]] for x in range(n)] for h in range(n)]
    representatives: set[tuple[int, ...]] = set()
    for values in product(range(n), repeat=genus):
        word = group.identity
        for value in values:
            word = op[word][square[value]]
        if word != group.identity:
            continue
        representatives.add(min(tuple(conjugate[h][v] for v in values)
                                for h in range(n)))
    return len(representatives)


def abelian_reference_formula(group: FiniteGroup, genus: int = 5) -> int:
    """For a finite abelian G only: |G|^(genus-1) times |G[2]|."""
    _genus(genus)
    if not group.is_abelian():
        raise InvalidFiniteGroup("ABELIAN_FORMULA_INAPPLICABLE")
    two_torsion = sum(group.multiplication[x][x] == group.identity
                      for x in range(group.order))
    return group.order ** (genus - 1) * two_torsion


def independently_checked_receipt(group: FiniteGroup, genus: int = 5) -> dict:
    by_fixed_points = count_orbits_burnside(group, genus)
    by_orbits = count_orbits_direct(group, genus)
    if by_fixed_points != by_orbits:
        raise AssertionError("ALGORITHMS_DISAGREE")
    if group.is_abelian() and by_fixed_points != abelian_reference_formula(group, genus):
        raise AssertionError("ABELIAN_FORMULA_DISAGREES")
    evidence = {
        "schema": "szl.synthetic.surface-orbit-check.v1",
        "group_name": group.name,
        "group_order": group.order,
        "group_table_sha256": group.source_sha256(),
        "nonorientable_genus": genus,
        "burnside_orbits": by_fixed_points,
        "independent_direct_orbits": by_orbits,
        "state": "REPRODUCED_SYNTHETIC_EXAMPLE",
        "training_origin": "SZL_ORIGINAL_SYNTHETIC",
        "benchmark_answer_verified": False,
        "block_picard_group_computation": "NOT_PERFORMED",
        "proof_assistant_kernel_checked": False,
        "external_actions_authorized": False,
        "production_eligible": False,
    }
    evidence["content_sha256"] = hashlib.sha256(json.dumps(
        evidence, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return evidence


def verified_product_count(factors: tuple[FiniteGroup, ...], genus: int = 5) -> dict:
    """Componentwise conjugation implies product of *verified* orbit counts.

    This arithmetic holds for the finite direct-product case; each input
    group must independently pass exact checks. Not a Picard group solver.
    """
    _genus(genus)
    if not 1 <= len(factors) <= 12:
        raise CostCeilingExceeded("FACTOR_COUNT_OUT_OF_BOUNDS")
    receipts = [independently_checked_receipt(g, genus) for g in factors]
    total = 1
    for item in receipts:
        total *= item["burnside_orbits"]
    return {
        "state": "REPRODUCED_SYNTHETIC_PRODUCT",
        "factor_receipt_sha256": [x["content_sha256"] for x in receipts],
        "orbit_count": total,
        "benchmark_answer_verified": False,
        "production_eligible": False,
    }
