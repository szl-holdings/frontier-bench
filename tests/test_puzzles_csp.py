import unittest

from puzzles.csp import CSP, alldiff, table
from puzzles.dpll import ClauseDB, Solver


def small_permutation_csp():
    csp = CSP(["x", "y", "z"], [[1, 2, 3]] * 3)
    csp.add_rule(alldiff([0, 1, 2]))
    csp.add_clue(table([0], lambda a: a == 1))
    return csp


class TestDualEncodings(unittest.TestCase):
    def test_encodings_agree_on_solution_set(self):
        csp = small_permutation_csp()
        sols, saturated, consistent = csp.enumerate_solutions(cap=6)
        self.assertTrue(saturated)
        self.assertTrue(consistent)
        self.assertEqual(len(sols), 2)  # x=1 leaves y,z in {2,3} both ways
        for s in sols:
            self.assertTrue(csp.check(s))

    def test_certify_unique_positive(self):
        csp = small_permutation_csp()
        csp.add_clue(table([1], lambda b: b == 2))
        self.assertTrue(csp.certify_unique())
        sols, _, _ = csp.enumerate_solutions(cap=2)
        self.assertEqual(sols[0], [1, 2, 3])

    def test_certify_unique_negative_when_two_solutions(self):
        csp = small_permutation_csp()
        self.assertFalse(csp.certify_unique())

    def test_direct_check_independent_of_cnf(self):
        csp = small_permutation_csp()
        # a labels list violating the clue must fail check() even though the
        # CNF machinery is not involved at all
        self.assertFalse(csp.check([2, 2, 3]))  # alldiff broken
        self.assertFalse(csp.check([3, 1, 2]))  # clue x=1 broken
        self.assertTrue(csp.check([1, 3, 2]))

    def test_support_encoding_conflict_detected(self):
        csp = small_permutation_csp()
        csp.add_clue(table([0], lambda a: a == 4))  # value outside domain
        sols, saturated, consistent = csp.enumerate_solutions(cap=2)
        # no solution can satisfy the clue: both encodings must agree on
        # zero solutions
        self.assertEqual(sols, [])
        self.assertTrue(saturated)
        self.assertTrue(consistent)
        self.assertFalse(csp.certify_unique())


if __name__ == "__main__":
    unittest.main()
