"""Independent Frobenius–Schur character checks for *declared small groups*.

Reference: M. R. Klug (2024), DOI 10.4153/S0008439524000420,
the Frobenius–Schur closed-nonorientable-surface homomorphism count:
 Hom(N_g -> G) = |G|^(g-1) * sum_chi nu(chi)^g * dim(chi)^(2-g).

The character fixtures are caller-declared mathematical inputs, not derived
by this module and not independently authenticated. This is a third method
for bounded research tests only — not a general character table solver,
formal proof, numerical approximation or published benchmark score.
"""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction

from .surface_groups import (
    CostCeilingExceeded,
    FiniteGroup,
    quaternion_group,
    symmetric_group_three,
    count_orbits_burnside,
    count_orbits_direct,
)

@dataclass(frozen=True)
class Irrep:
    dimension: int
    frobenius_schur_indicator: int

    def __post_init__(self) -> None:
        if type(self.dimension) is not int or self.dimension < 1:
            raise ValueError("INVALID_IRREP_DIMENSION")
        if type(self.frobenius_schur_indicator) is not int or self.frobenius_schur_indicator not in (-1, 0, 1):
            raise ValueError("INVALID_FROBENIUS_SCHUR_INDICATOR")


def hom_count_character_formula(group_order: int, genus: int,
                                characters: tuple[Irrep, ...]) -> int:
    """Exact rationals. Sum(dim_i^2)=|G| is necessary, not a proof of a table."""
    if type(group_order) is not int or not 1 <= group_order <= 12:
        raise CostCeilingExceeded("GROUP_ORDER_OUT_OF_SCOPE")
    if type(genus) is not int or not 1 <= genus <= 5:
        raise CostCeilingExceeded("GENUS_OUT_OF_SCOPE")
    if not isinstance(characters, tuple) or not characters or len(characters) > group_order:
        raise ValueError("MISSING_OR_OVERSIZED_CHARACTERS")
    if not all(isinstance(c, Irrep) for c in characters):
        raise ValueError("INVALID_CHARACTER_FIXTURE")
    if sum(c.dimension**2 for c in characters) != group_order:
        raise ValueError("CHARACTER_DIMENSION_SUM_INCOMPLETE")
    # Explicit Fraction exponent handles negative powers exactly, never floats.
    weighted = sum((
        Fraction(c.frobenius_schur_indicator**genus, 1)
        * Fraction(c.dimension, 1)**(2-genus)
        for c in characters
    ), Fraction())
    count = weighted * group_order**(genus-1)
    if count.denominator != 1 or count < 0:
        raise ValueError("NONINTEGER_HOMOMORPHISM_COUNT")
    return count.numerator


def trusted_fixture_character_data() -> dict[str, tuple[int, tuple[Irrep, ...]]]:
    """Conventional known representation theory, NOT a computed character table."""
    return {
        "C2": (2, (Irrep(1, 1), Irrep(1, 1))),
        "C3": (3, (Irrep(1, 1), Irrep(1, 0), Irrep(1, 0))),
        "C4": (4, (Irrep(1, 1), Irrep(1, 1), Irrep(1, 0), Irrep(1, 0))),
        "S3": (6, (Irrep(1, 1), Irrep(1, 1), Irrep(2, 1))),
        "Q8": (8, (Irrep(1, 1), Irrep(1, 1), Irrep(1, 1), Irrep(1, 1), Irrep(2, -1))),
    }


def third_method_orbits(name: str, genus: int = 5) -> dict:
    """Known centralizer-class multiplicities feed independent character counts.

    This is NOT a generic method for discovering group centralizers or irreps.
    The class-centralizer assignments below are human-specified small-group
    mathematical fixtures. Burnside is recomputed and compared to explicit
    orbit enumeration from a distinct algorithm.
    """
    groups: dict[str, FiniteGroup] = {
        "S3": symmetric_group_three(),
        "Q8": quaternion_group(),
    }
    if name not in groups:
        raise ValueError("CHARACTER_CROSSCHECK_GROUP_UNAVAILABLE")
    fixtures = trusted_fixture_character_data()
    # Group-theoretic centralizers by conjugacy class:
    # S3: {1} -> S3 (1), transpositions -> C2 (3), 3-cycles -> C3 (2).
    # Q8: {+1,-1} -> Q8 (2), +/-i,j,k -> C4 (6).
    centralizer_counts = {
        "S3": ((1, "S3"), (3, "C2"), (2, "C3")),
        "Q8": ((2, "Q8"), (6, "C4")),
    }
    group = groups[name]
    fixed = 0
    witnesses = []
    for multiplicity, centralizer in centralizer_counts[name]:
        size, characters = fixtures[centralizer]
        homs = hom_count_character_formula(size, genus, characters)
        fixed += multiplicity * homs
        witnesses.append({
            "conjugating_elements": multiplicity,
            "centralizer_fixture": centralizer,
            "homomorphisms_from_character_data": homs,
        })
    if fixed % group.order:
        raise AssertionError("CHARACTER_BURNSIDE_DIVISIBILITY_FAILURE")
    character_orbits = fixed // group.order
    burnside = count_orbits_burnside(group, genus)
    direct = count_orbits_direct(group, genus)
    if not character_orbits == burnside == direct:
        raise AssertionError("INDEPENDENT_METHODS_DISAGREE")
    return {
        "group": name,
        "genus": genus,
        "orbits_by_character_fixtures": character_orbits,
        "orbits_by_burnside_dp": burnside,
        "orbits_by_explicit_conjugacy": direct,
        "centralizer_character_inputs": witnesses,
        "provenance": "KNOWN_FINITE_GROUP_CHARACTER_FIXTURES_NOT_KERNEL_CHECKED",
        "character_table_completeness": "DECLARED_NOT_INDEPENDENTLY_PROVED",
        "benchmark_solution_verified": False,
        "model_training_executed": False,
        "production_eligible": False,
    }
