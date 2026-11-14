import unittest

from puzzles.families import generate, generate_futoshiki
from puzzles.render import prompt_sha256, render


def build_zebra_csp_from_spec(spec):
    """Rebuild the CSP from the published spec, independently of the
    generator's internal state."""
    from puzzles.csp import CSP, alldiff, table

    cats = spec["categories"]
    names = [f"pos:{c}:{v}" for c, vs in cats for v in vs]
    csp = CSP(names, [list(range(1, spec["houses"] + 1))] * len(names))
    for cat, values in cats:
        csp.add_rule(alldiff([csp.index[f"pos:{cat}:{v}"] for v in values]))

    def vidx(m, cat_key, val_key):
        return csp.index[f"pos:{m[cat_key]}:{m[val_key]}"]

    for m in spec["clues"]:
        if m["type"] == "same":
            csp.add_clue(
                table([vidx(m, "cat1", "a"), vidx(m, "cat2", "b")], lambda p, q: p == q)
            )
        elif m["type"] == "left_k":
            csp.add_clue(
                table(
                    [vidx(m, "cat1", "a"), vidx(m, "cat2", "b")],
                    lambda p, q, k=m["k"]: q - p == k,
                )
            )
        elif m["type"] == "adjacent":
            csp.add_clue(
                table(
                    [vidx(m, "cat1", "a"), vidx(m, "cat2", "b")],
                    lambda p, q: abs(p - q) == 1,
                )
            )
        else:
            raise ValueError(f"unknown clue type {m['type']}")
    return csp


class TestZebra(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.puzzle, _ = generate("probe-test-1")
        cls.spec = cls.puzzle["spec"]

    def test_generated_puzzle_is_certified_and_minimal(self):
        self.assertIsNotNone(self.puzzle)
        self.assertTrue(self.puzzle["minimal"])
        self.assertGreaterEqual(self.puzzle["clue_count"], 4)
        csp = build_zebra_csp_from_spec(self.spec)
        self.assertTrue(csp.certify_unique())
        sols, _, _ = csp.enumerate_solutions(cap=2)
        cats = self.spec["categories"]
        flat = {
            cat: {v: sols[0][csp.index[f"pos:{cat}:{v}"]] for v in values}
            for cat, values in cats
        }
        self.assertEqual(flat, self.spec["solution"])
        # clue-minimality: removing any single clue breaks uniqueness
        for i in range(len(self.spec["clues"])):
            csp = build_zebra_csp_from_spec(self.spec)
            csp.clues = [c for j, c in enumerate(csp.clues) if j != i]
            self.assertFalse(
                csp.certify_unique(),
                f"clue {i} is redundant: puzzle is not clue-minimal",
            )

    def test_deterministic_regeneration(self):
        a, _ = generate("seed-x")
        b, _ = generate("seed-x")
        self.assertEqual(a["spec"], b["spec"])
        self.assertEqual(
            prompt_sha256(render(a["spec"])), prompt_sha256(render(b["spec"]))
        )

    def test_prompt_text_stable_and_renderable(self):
        text = render(self.spec)
        self.assertIn("Clues:", text)
        self.assertIn("exactly one JSON object", text)
        self.assertTrue(text.startswith("Grid logic puzzle."))
        self.assertEqual(text, render(self.spec))


class TestFutoshiki(unittest.TestCase):
    def test_generated_puzzle_is_certified(self):
        puzzle, _ = generate_futoshiki("f-test-1", 4)
        self.assertIsNotNone(puzzle)
        self.assertEqual(puzzle["tier"], "f4")
        spec = puzzle["spec"]
        sol = spec["solution"]
        for r in range(1, 5):
            self.assertEqual(sorted(sol[f"r{r}c{c}"] for c in range(1, 5)), [1, 2, 3, 4])
        for c in range(1, 5):
            self.assertEqual(sorted(sol[f"r{r}c{c}"] for r in range(1, 5)), [1, 2, 3, 4])
        text = render(spec)
        self.assertIn("4x4 grid", text)
        self.assertIn("row 1:", text)

    def test_futoshiki_5(self):
        puzzle, _ = generate_futoshiki("f-test-5", 5)
        self.assertIsNotNone(puzzle)
        self.assertEqual(puzzle["tier"], "f5")


if __name__ == "__main__":
    unittest.main()
