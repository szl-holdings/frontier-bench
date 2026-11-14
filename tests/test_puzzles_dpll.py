import unittest

from puzzles.dpll import ClauseDB, Solver


class TestDpll(unittest.TestCase):
    def test_satisfiable(self):
        db = ClauseDB([[1, 2], [-1, 3], [-2, -3]])
        model = Solver(db).solve()
        self.assertIsNotNone(model)
        for clause in db.clauses:
            self.assertTrue(any((l > 0) == model.get(abs(l), False) for l in clause))

    def test_unsat(self):
        db = ClauseDB([[1], [-1]])
        self.assertIsNone(Solver(db).solve())

    def test_pigeonhole_unsat(self):
        # 4 pigeons into 3 holes: p_ij = pigeon i in hole j
        clauses = []
        for i in range(1, 5):
            clauses.append([i * 10 + j for j in (1, 2, 3)])
        for j in (1, 2, 3):
            for i1, i2 in [(1, 2), (1, 3), (1, 4), (2, 3), (2, 4), (3, 4)]:
                clauses.append([-(i1 * 10 + j), -(i2 * 10 + j)])
        self.assertIsNone(Solver(ClauseDB(clauses)).solve())

    def test_enumerate_cap_and_saturation(self):
        db = ClauseDB([[1, 2]])  # x2 free only when x1 False -> 3 models
        models, saturated = Solver(db).enumerate_models(cap=5)
        self.assertEqual(len(models), 3)
        self.assertTrue(saturated)
        models, saturated = Solver(db).enumerate_models(cap=2)
        self.assertEqual(len(models), 2)
        self.assertFalse(saturated)

    def test_forced_assignment_single_model(self):
        db = ClauseDB([[1, 2], [-1]])  # x1 False forces x2 True
        models, saturated = Solver(db).enumerate_models(cap=5)
        self.assertEqual(len(models), 1)
        self.assertTrue(saturated)
        self.assertEqual(models[0], {1: False, 2: True})

    def test_deterministic(self):
        db = ClauseDB([[1, 2], [2, 3], [-1, -2], [1, 3]])
        m1 = Solver(db).solve()
        m2 = Solver(db).solve()
        self.assertEqual(m1, m2)


if __name__ == "__main__":
    unittest.main()
