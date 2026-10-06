from pathlib import Path
import unittest

from src.catch_check import check_batch, load_fish_reference
from src.fishing_log import parse_fishing

LIVE = parse_fishing((Path(__file__).parent / 'fixtures' / 'live_fishing.txt').read_text(encoding='utf-8'))


class FishReferenceTests(unittest.TestCase):
    def test_reference_is_keyed_case_insensitively(self):
        reference = load_fish_reference()
        self.assertEqual(reference['tropical fish']['base_xp'], 10)
        self.assertEqual(reference['cod']['base_sell'], 10)
        self.assertIn('River', reference['salmon']['biomes'])
        self.assertNotIn('Ocean', reference['salmon']['biomes'])


class CheckBatchTests(unittest.TestCase):
    def setUp(self):
        self.result = check_batch(LIVE['trip_details'], load_fish_reference(), biome='River')

    def test_ratio_per_trip(self):
        trips = self.result['trips']
        self.assertEqual([trip['base_xp'] for trip in trips], [76, 55, 53, 44, 47, 62])
        self.assertEqual([round(trip['ratio'], 2) for trip in trips], [1.84, 1.85, 1.83, 1.84, 1.94, 1.94])

    def test_only_the_charm_jump_is_flagged(self):
        self.assertEqual([trip['jump'] for trip in self.result['trips']], [False, False, False, False, True, False])

    def test_base_value(self):
        self.assertEqual(self.result['trips'][0]['base_value'], 143)
        self.assertEqual(self.result['base_value'], 620)

    def test_no_flags_in_the_right_biome(self):
        self.assertTrue(all(not trip['unknown'] and not trip['off_biome'] for trip in self.result['trips']))

    def test_off_biome_catches_are_flagged(self):
        result = check_batch(LIVE['trip_details'], load_fish_reference(), biome='Ocean')
        self.assertEqual(result['trips'][2]['off_biome'], ['Salmon', 'Cod'])

    def test_unknown_fish_are_flagged_and_skip_the_ratio(self):
        trips = parse_fishing('You caught:\n2 Cod\n1 Moon Fish\n+30 XP')['trip_details']
        trip = check_batch(trips, load_fish_reference())['trips'][0]
        self.assertEqual(trip['unknown'], ['Moon Fish'])
        self.assertIsNone(trip['ratio'])
        self.assertEqual(trip['base_value'], 20)

    def test_chest_xp_is_left_out_of_the_ratio(self):
        trips = parse_fishing('You caught:\n1 Fish\n8 Salmon\n13 Cod\nYou got 217 XP from the chest.\n+382 XP')
        trip = check_batch(trips['trip_details'], load_fish_reference())['trips'][0]
        self.assertEqual(trip['base_xp'], 82)
        self.assertEqual(trip['xp'], 165)
        self.assertAlmostEqual(trip['ratio'], 165 / 82)

    def test_missing_xp_or_reference(self):
        trips = parse_fishing('You caught:\n2 Cod')['trip_details']
        self.assertIsNone(check_batch(trips, load_fish_reference())['trips'][0]['ratio'])
        self.assertIsNone(check_batch(trips, None))


if __name__ == '__main__':
    unittest.main()
