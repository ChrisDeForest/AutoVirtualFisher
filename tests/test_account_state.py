from pathlib import Path
import tempfile
import unittest

from src import account_samples as sample
from src.account_replies import PARSERS
from src.account_state import apply_read, empty_state, load_state, save_state, section_unread


def read(state, kind, text, at='2026-10-10T00:00:00+00:00'):
    return apply_read(state, kind, PARSERS[kind](text), at)


class AccountStateTests(unittest.TestCase):
    def test_both_pages_of_a_shop_make_one_complete_section(self):
        state = read(empty_state(), 'shop_upgrades', sample.UPGRADES_PAGE_1)
        section = state['sections']['shop_upgrades']
        self.assertEqual(sorted(section['unread']), ['Artifact Specialist', 'Better Chests', 'Better Dailies',
                                                     'Experienced'], 'page 2 not read yet')
        state = read(state, 'shop_upgrades', sample.UPGRADES_PAGE_2, at='2026-10-10T00:00:05+00:00')
        section = state['sections']['shop_upgrades']
        self.assertEqual(len(section['values']['items']), 9)
        self.assertEqual(section['unread'], [])
        self.assertEqual(section['pages'], {'1': '2026-10-10T00:00:00+00:00', '2': '2026-10-10T00:00:05+00:00'})
        self.assertEqual(section['read_at'], '2026-10-10T00:00:05+00:00')

    def test_a_later_read_replaces_values_and_keeps_what_it_lacked(self):
        state = read(empty_state(), 'shop_upgrades', sample.UPGRADES_PAGE_1)
        bought = sample.UPGRADES_PAGE_1.replace('Better Fish (7/21)', 'Better Fish (8/21)').replace(
            'Salesman (7/20)', 'Sa1esrnan (7/2O)')
        state = read(state, 'shop_upgrades', bought)
        items = state['sections']['shop_upgrades']['values']['items']
        self.assertEqual(items['Better Fish']['level'], 8)
        self.assertEqual(items['Salesman']['level'], 7, 'the garbled line keeps the earlier read')

    def test_league_needs_every_upgrade_and_the_hooks(self):
        state = read(read(empty_state(), 'shop_league', sample.LEAGUE_PAGE_1), 'shop_league', sample.LEAGUE_PAGE_2)
        self.assertEqual(state['sections']['shop_league']['unread'], [])
        self.assertEqual(section_unread('shop_league', {'items': {}, 'hooks': None})[-1], 'Hooks')

    def test_single_page_replies_use_their_own_rule(self):
        state = read(empty_state(), 'rod', '/rod <rod name>')
        self.assertEqual(state['sections']['rod']['unread'], ['Rod in use'])

    def test_save_and_load(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'account_state.json'
            self.assertEqual(load_state(path), empty_state(), 'a missing file loads empty')
            state = read(empty_state(), 'biome', sample.BIOME)
            save_state(path, state)
            self.assertEqual(load_state(path), state)


if __name__ == '__main__':
    unittest.main()
