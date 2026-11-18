import unittest

from bettingbot.models import pointmodel as pm


class TestGame(unittest.TestCase):
    def test_symmetric_game_is_fifty_fifty(self):
        self.assertAlmostEqual(pm.game_win_prob(0.5), 0.5, places=10)

    def test_strong_server_wins_almost_always(self):
        self.assertGreater(pm.game_win_prob(0.75), 0.90)

    def test_monotonic_in_p(self):
        probs = [pm.game_win_prob(p) for p in (0.2, 0.4, 0.5, 0.6, 0.8)]
        self.assertEqual(probs, sorted(probs))

    def test_extremes(self):
        self.assertEqual(pm.game_win_prob(0.0), 0.0)
        self.assertEqual(pm.game_win_prob(1.0), 1.0)

    def test_bad_input(self):
        with self.assertRaises(ValueError):
            pm.game_win_prob(1.2)


class TestTiebreak(unittest.TestCase):
    def test_even_matchup_is_even(self):
        self.assertAlmostEqual(pm.tiebreak_win_prob(0.6, 0.6), 0.5, places=10)

    def test_advantage_for_better_returner(self):
        # A saca igual que B, pero B resta peor: A debe ganar más.
        p = pm.tiebreak_win_prob(0.65, 0.55)
        self.assertGreater(p, 0.5)


class TestSetAndMatch(unittest.TestCase):
    def test_symmetric_players(self):
        self.assertAlmostEqual(pm.set_win_prob(0.6, 0.6), 0.5, places=10)
        self.assertAlmostEqual(pm.match_win_prob(0.6, 0.6, best_of=3), 0.5, places=10)

    def test_bo5_rewards_favourite_more(self):
        p_bo3 = pm.match_win_prob(0.65, 0.60, best_of=3)
        p_bo5 = pm.match_win_prob(0.65, 0.60, best_of=5)
        self.assertGreater(p_bo3, 0.5)
        self.assertGreater(p_bo5, p_bo3)

    def test_probabilities_in_range(self):
        for a in (0.3, 0.5, 0.7):
            for b in (0.3, 0.5, 0.7):
                p = pm.match_win_prob(a, b, best_of=3)
                self.assertTrue(0.0 <= p <= 1.0)

    def test_bad_best_of(self):
        with self.assertRaises(ValueError):
            pm.match_win_prob(0.6, 0.6, best_of=4)


class TestTableTennis(unittest.TestCase):
    def test_symmetric(self):
        self.assertAlmostEqual(pm.table_tennis_match_prob(0.6, 0.6), 0.5, places=10)

    def test_shorter_match_is_more_volatile(self):
        # A un partido al mejor de 3, el favorito gana menos que al mejor de 7.
        p3 = pm.table_tennis_match_prob(0.6, 0.55, best_of=3)
        p7 = pm.table_tennis_match_prob(0.6, 0.55, best_of=7)
        self.assertLess(p3, p7)

    def test_race_win_prob_symmetric(self):
        self.assertAlmostEqual(
            pm.race_win_prob(0.5, 0.5, target=11, first_block=2), 0.5, places=10
        )


if __name__ == "__main__":
    unittest.main()
