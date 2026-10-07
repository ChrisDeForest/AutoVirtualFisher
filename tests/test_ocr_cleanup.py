from pathlib import Path
import unittest

from src.fishing_log import parse_fishing
from src.ocr_cleanup import META, clean_ocr
from src.paste_detect import detect, split_messages
from src.player_state import parse_responses

FIXTURES = Path(__file__).parent / 'fixtures'


def sorted_screenshot(name):
    lines = (FIXTURES / f'ocr_{name}.txt').read_text(encoding='utf-8').splitlines()
    return detect(clean_ocr(lines, f'{name}.png'))


class OcrScreenshotTests(unittest.TestCase):
    def test_two_fishing_results(self):
        result = sorted_screenshot('2x_fish_msgs')
        self.assertEqual(result['counts'], {'fishing': 2})
        tally = parse_fishing(result['fishing'])
        self.assertEqual(tally['trips'], 2)
        self.assertEqual(tally['catches'], {'Salmon': 15, 'Cod': 10})
        self.assertEqual(tally['fishing_xp'], 170)
        self.assertEqual(tally['warnings'], [], 'buttons, tips and command lines are dropped')

    def test_profile(self):
        result = sorted_screenshot('profile_msg')
        state = parse_responses(profile=result['responses']['profile'])
        self.assertEqual(state['warnings'], [])
        profile = state['profile']
        self.assertEqual((profile['account_name'], profile['balance'], profile['prestige'], profile['level']),
                         ('epicmouse', 47210, 4, 30))
        self.assertEqual((profile['xp_current'], profile['xp_required']), (1743, 5750))
        self.assertEqual(profile['biome'], 'River')
        self.assertEqual(state['inventory']['fish'], {'Fish': 291, 'Salmon': 3642, 'Cod': 4623})
        self.assertEqual(state['inventory']['special'], {'Hooks': 160})
        self.assertEqual(result['times']['profile'], 'screenshot profile_msg.png')

    def test_buffs_period_misread_is_fixed(self):
        state = parse_responses(buffs=sorted_screenshot('buff_msg')['responses']['buffs'])
        self.assertEqual(state['warnings'], [])
        self.assertEqual(state['buffs']['xp_multiplier'], {'value': 2.12, 'unit': 'multiplier'})

    def test_pet_levels_are_corrected_and_lost_xp_stays_unknown(self):
        state = parse_responses(pet=sorted_screenshot('pet_msg')['responses']['pet'])
        owned = state['pets']['owned']
        self.assertEqual({name: pet['level'] for name, pet in owned.items()},
                         {'Puffer': 30, 'Dolphin': 51, 'Zebrafish': 61, 'Axolotl': 60, 'Tuna': 14})
        self.assertTrue(all(pet['xp_current'] is None for pet in owned.values()))
        self.assertEqual(state['pets']['current_pet'], 'Dolphin')


class CleanupRuleTests(unittest.TestCase):
    def clean(self, *lines):
        return clean_ocr(list(lines), 'shot.png').splitlines()

    def test_header_becomes_a_discord_header(self):
        self.assertEqual(self.clean('Virtual Fisher V APP', 'Balance: $1.'),
                         ['Virtual Fisher', 'APP', ' — screenshot shot.png', 'Balance: $1.'])

    def test_emoji_leftovers_between_count_and_name(self):
        self.assertEqual(self.clean('160 to Hooks', 'Current biome: e River', '4,623 cod'),
                         ['160 Hooks', 'Current biome: River', '4,623 Cod'])

    def test_level_misreads(self):
        self.assertEqual(self.clean('Tuna(Lv114)', 'Dolphin(Lvl 51)', 'Puffer(LvI 30)'),
                         ['Tuna(Lvl 14)', 'Dolphin(Lvl 51)', 'Puffer(Lvl 30)'])

    def test_capture_keeps_who_used_the_command_and_the_time(self):
        lines = ['epicmouse used', '% fish', 'Virtual Fisher 11:40 AM', 'You caught:']
        self.assertEqual(self.clean(*lines), ['Virtual Fisher', 'APP', ' — screenshot shot.png', 'You caught:'],
                         'pasted text stays free of markers')
        self.assertEqual(clean_ocr(lines, 'shot.png', capture=True).splitlines(),
                         ['Virtual Fisher', 'APP', ' — screenshot shot.png', f'{META}used by epicmouse',
                          f'{META}sent 11:40 AM', 'You caught:'])

    def test_other_authors_get_their_own_header(self):
        lines = self.clean('Virtual Fisher 11:40 AM', 'You caught:', 'Fake Fisher APP 11:41 AM', 'You caught:',
                           'epicmouse Today at 11:42 AM', 'hello')
        messages = split_messages('\n'.join(lines))
        self.assertEqual([(message['author'], message['bot']) for message in messages],
                         [('Virtual Fisher', True), ('Fake Fisher', True), ('epicmouse', False)])

    def test_live_review_misreads(self):
        # From the user's first live-capture review queue.
        self.assertEqual(self.clean("5 'cod", 'Message@Virtual Fisher', 'Message @Virtual Fisher', '9 Salmon'),
                         ['5 Cod', '9 Salmon'])


if __name__ == '__main__':
    unittest.main()
