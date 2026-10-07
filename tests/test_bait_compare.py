from pathlib import Path
import tempfile
import unittest

from src.bait_compare import NOT_RECORDED, assign_bait, compare_baits, load_bait_reference
from src.catch_check import load_fish_reference
from src.fishing_log import fingerprint, parse_fishing
from src.fishing_sessions import load_sessions, save_session

LIVE = (Path(__file__).parent / 'fixtures' / 'live_fishing.txt').read_text(encoding='utf-8')


class AssignBaitTests(unittest.TestCase):
    def test_bait_lasts_through_the_trip_where_it_runs_out(self):
        tally = parse_fishing('You caught:\n2 Cod\n+10 XP\n\nYou caught:\n2 Cod\n+10 XP\nYou ran out of :bait_worms: worms.'
                              '\n\nYou caught:\n2 Cod\n+12 XP')
        baited = assign_bait(tally, 'Worms')
        self.assertEqual([trip['bait'] for trip in baited['trip_details']], ['Worms', 'Worms', None])
        self.assertEqual(baited['bait'], 'Worms')
        self.assertNotIn('bait', tally['trip_details'][0], 'the parsed tally is not modified')

    def test_no_bait_and_not_recorded(self):
        tally = parse_fishing('You caught:\n2 Cod\n+10 XP')
        self.assertIsNone(assign_bait(tally, None)['trip_details'][0]['bait'])
        self.assertNotIn('bait', assign_bait(tally, NOT_RECORDED)['trip_details'][0])

    def test_live_batch_ran_out_on_the_last_trip(self):
        baited = assign_bait(parse_fishing(LIVE), 'Worms')
        self.assertEqual([trip['bait'] for trip in baited['trip_details']], ['Worms'] * 6)


class CompareBaitsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def save(self, text, bait):
        result = save_session(assign_bait(parse_fishing(text), bait), 'epicmouse', fingerprint(text), self.root)
        self.assertEqual(result['status'], 'saved')

    def compare(self):
        return compare_baits(load_sessions('epicmouse', self.root)[0], load_fish_reference(), load_bait_reference())

    def test_groups_trips_by_bait(self):
        self.save(LIVE, 'Worms')
        self.save('You caught:\n4 Cod\n+40 XP\n\nYou caught:\n2 Cod\n+20 XP\nYou found an artifact box', None)
        self.save('You caught:\n9 Cod\n+90 XP', NOT_RECORDED)
        result = self.compare()
        rows = {row['bait']: row for row in result['rows']}
        self.assertEqual(set(rows), {'Worms', 'No bait'})
        self.assertEqual(result['not_recorded_trips'], 1)
        worms = rows['Worms']
        self.assertEqual(worms['trips'], 6)
        self.assertEqual(worms['boosted_trips'], 6)
        self.assertAlmostEqual(worms['fish_per_trip'], 101 / 6)
        # Median of six trip ratios: the middle two are 140/76 (1.842) and 102/55 (1.855).
        self.assertAlmostEqual(worms['xp_ratio'], (140 / 76 + 102 / 55) / 2)
        self.assertAlmostEqual(worms['base_value_per_trip'], 620 / 6)
        self.assertAlmostEqual(worms['finds_per_100_trips'], 4 * 100 / 6)
        self.assertEqual(worms['wiki']['extra_fish'], 2)
        self.assertEqual(worms['wiki']['xp_percent'], -10)
        none = rows['No bait']
        self.assertEqual((none['trips'], none['fish_per_trip'], none['xp_ratio']), (2, 3, 2.0))
        self.assertIsNone(none['wiki'])
        self.assertTrue(none['few_trips'])

    def test_net_value_subtracts_bait_cost(self):
        self.save('You caught:\n4 Cod\n+40 XP', 'Pricey')
        self.save('You caught:\n3 Cod\n+30 XP', 'Cheap')
        self.save('You caught:\n3 Cod\n+33 XP', 'Mystery')
        self.save('You caught:\n2 Cod\n+20 XP', None)
        reference = {'Pricey': {'cost': 50}, 'Cheap': {'cost': 1}}
        sessions = load_sessions('epicmouse', self.root)[0]
        rows = {row['bait']: row for row in compare_baits(sessions, load_fish_reference(), reference, sell=2.0)['rows']}
        self.assertGreater(rows['Pricey']['gross_value_per_trip'], rows['Cheap']['gross_value_per_trip'])
        self.assertLess(rows['Pricey']['net_value_per_trip'], rows['Cheap']['net_value_per_trip'],
                        'more fish but dearer bait earns less')
        self.assertEqual((rows['Pricey']['bait_cost_per_trip'], rows['Pricey']['net_value_per_trip']), (50, 30))
        self.assertIsNone(rows['Mystery']['bait_cost_per_trip'], 'unknown cost is not zero')
        self.assertIsNone(rows['Mystery']['net_value_per_trip'])
        self.assertEqual(rows['No bait']['bait_cost_per_trip'], 0)
        self.assertEqual(rows['No bait']['net_value_per_trip'], 40)
        without_sell = compare_baits(sessions, load_fish_reference(), reference)['rows']
        self.assertTrue(all(row['net_value_per_trip'] is None for row in without_sell))

    def test_bait_names_match_the_wiki_ignoring_case(self):
        self.save('You caught:\n4 Cod\n+40 XP', 'magic bait')
        row = self.compare()['rows'][0]
        self.assertEqual(row['bait'], 'Magic Bait')
        self.assertEqual(row['wiki']['extra_fish'], 2)


if __name__ == '__main__':
    unittest.main()
