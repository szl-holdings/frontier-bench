"""Exact character-count cross-checks with independent group algorithms."""
import unittest

from research.security_math_v0.character_crosscheck import (
    Irrep,
    hom_count_character_formula,
    third_method_orbits,
    trusted_fixture_character_data,
)


class CharacterCrosscheckTests(unittest.TestCase):
    def test_cyclic_two_and_three_word_counts(self):
        fixtures = trusted_fixture_character_data()
        self.assertEqual(hom_count_character_formula(*fixtures["C2"][:1], 5, fixtures["C2"][1]), 32)
        self.assertEqual(hom_count_character_formula(*fixtures["C3"][:1], 5, fixtures["C3"][1]), 81)

    def test_cyclic_four_character_count(self):
        n, reps = trusted_fixture_character_data()["C4"]
        self.assertEqual(hom_count_character_formula(n, 5, reps), 512)

    def test_s3_three_methods_and_known_character_fixtures(self):
        result = third_method_orbits("S3", 5)
        self.assertEqual(result["orbits_by_character_fixtures"], 502)
        self.assertEqual(result["orbits_by_burnside_dp"], 502)
        self.assertEqual(result["orbits_by_explicit_conjugacy"], 502)
        self.assertEqual(result["centralizer_character_inputs"], [
            {"conjugating_elements": 1, "centralizer_fixture": "S3",
             "homomorphisms_from_character_data": 2754},
            {"conjugating_elements": 3, "centralizer_fixture": "C2",
             "homomorphisms_from_character_data": 32},
            {"conjugating_elements": 2, "centralizer_fixture": "C3",
             "homomorphisms_from_character_data": 81},
        ])
        self.assertFalse(result["benchmark_solution_verified"])

    def test_q8_quaternionic_frobenius_schur_indicator_changes_odd_genus(self):
        n, reps = trusted_fixture_character_data()["Q8"]
        self.assertEqual(hom_count_character_formula(n, 5, reps), 15872)
        self.assertEqual(hom_count_character_formula(n, 4, reps), 2176)
        result = third_method_orbits("Q8", 5)
        self.assertEqual(result["orbits_by_character_fixtures"], 4352)
        self.assertEqual(result["orbits_by_burnside_dp"], 4352)
        self.assertEqual(result["orbits_by_explicit_conjugacy"], 4352)
        self.assertFalse(result["production_eligible"])

    def test_dimension_sum_must_equal_group_order(self):
        with self.assertRaisesRegex(ValueError, "INCOMPLETE"):
            hom_count_character_formula(8, 5, (Irrep(1, 1), Irrep(2, 1)))

    def test_bad_character_data_not_authorized(self):
        for bad in (-2, 3, 0.1):
            with self.assertRaises(ValueError):
                Irrep(2, bad)
        with self.assertRaises(ValueError):
            Irrep(0, 1)
        with self.assertRaises(ValueError):
            hom_count_character_formula(3, 5, (Irrep(2, 1),))

    def test_unknown_character_group_fails_closed(self):
        with self.assertRaises(ValueError):
            third_method_orbits("Picard-unknown-group", 5)

    def test_out_of_bound_genus_and_order_rejected(self):
        with self.assertRaises(ValueError):
            hom_count_character_formula(True, 5, (Irrep(1, 1),))
        with self.assertRaises(ValueError):
            hom_count_character_formula(3, 6, (Irrep(1, 1),))
        with self.assertRaises(ValueError):
            hom_count_character_formula(3, 5, "NOT_A_CHARACTER_TABLE")


if __name__ == "__main__":
    unittest.main()
