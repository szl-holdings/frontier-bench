"""Deterministic DPLL SAT solver — Python standard library only.

Puzzle certification in this package must not depend on third-party solvers:
the harness, verifier and CI of this repository are stdlib-only by doctrine
(see README). This is a small, deliberately simple DPLL solver with unit
propagation, written for the tiny CNFs produced by the puzzle families
(tens of variables, hundreds of clauses), NOT as a general-purpose solver.

Determinism: literal order is fixed (smallest unassigned variable first,
True before False), so a certificate (solve -> block -> UNSAT) reproduces
byte-for-byte on any machine.
"""
from __future__ import annotations


class ClauseDB:
    """List of clauses; a clause is a non-empty list of nonzero ints."""

    def __init__(self, clauses=None):
        self.clauses = [list(c) for c in (clauses or [])]
        self._max_var = 0
        for c in self.clauses:
            for l in c:
                self._max_var = max(self._max_var, abs(l))

    def add(self, clause):
        if not clause:
            raise ValueError("empty clause: formula trivially UNSAT by construction")
        self.clauses.append(list(clause))
        for l in clause:
            self._max_var = max(self._max_var, abs(l))

    @property
    def n_vars(self):
        return self._max_var


def _unit_propagate(clauses, assignment):
    """Propagate units under `assignment` (dict var->bool), in place.

    Returns False on conflict, True otherwise.
    """
    changed = True
    while changed:
        changed = False
        for clause in clauses:
            satisfied = False
            unassigned = []
            for lit in clause:
                val = assignment.get(abs(lit))
                if val is None:
                    unassigned.append(lit)
                elif (lit > 0) == val:
                    satisfied = True
                    break
            if satisfied:
                continue
            if not unassigned:
                return False
            if len(unassigned) == 1:
                lit = unassigned[0]
                assignment[abs(lit)] = lit > 0
                changed = True
    return True


def _clause_satisfied(clause, model):
    for lit in clause:
        val = model.get(abs(lit))
        if val is not None and (lit > 0) == val:
            return True
    return False


def _pick_var(clauses, assignment):
    """Smallest variable mentioned by any clause that is unassigned."""
    best = None
    for clause in clauses:
        for lit in clause:
            v = abs(lit)
            if v not in assignment and (best is None or v < best):
                best = v
    return best


def _dpll(clauses, assignment):
    work = dict(assignment)
    if not _unit_propagate(clauses, work):
        return None
    var = _pick_var(clauses, work)
    if var is None:
        for clause in clauses:
            if not _clause_satisfied(clause, work):
                return None
        return work
    for value in (True, False):
        child = dict(work)
        child[var] = value
        result = _dpll(clauses, child)
        if result is not None:
            return result
    return None


class Solver:
    """Tiny deterministic DPLL solver over a ClauseDB."""

    def __init__(self, db: ClauseDB):
        self.db = db

    def solve(self, extra_clauses=()):
        clauses = self.db.clauses + [list(c) for c in extra_clauses]
        if any(len(c) == 0 for c in clauses):
            return None
        return _dpll(clauses, {})

    def enumerate_models(self, cap, extra_clauses=()):
        """Enumerate up to `cap` distinct models in deterministic order.

        Returns (models, saturated): saturated=True means fewer than `cap`
        models exist in total (exhaustion proven), False means the cap was
        hit and more may exist.
        """
        clauses = self.db.clauses + [list(c) for c in extra_clauses]
        if any(len(c) == 0 for c in clauses):
            return [], True
        models = []
        blocked = []
        while len(models) < cap:
            model = _dpll(clauses + blocked, {})
            if model is None:
                return models, True
            models.append(model)
            block = [-v if model[v] else v for v in sorted(model)]
            blocked.append(block)
        return models, False
