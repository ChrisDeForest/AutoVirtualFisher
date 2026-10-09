from pathlib import Path
import unittest


class FishNameRegressionTests(unittest.TestCase):
    def test_emoji_fragment_before_fuzzy_fish_name(self):
        text = clean_ocr(['You caught:', "4 Y' Salrnon", '+83 XP', "5 Y' Imaginaryfish"], 'reply')
        self.assertIn('4 Salmon', text)
        self.assertIn("5 Y' Imaginaryfish", text)

    def test_fuzzy_names_preserve_quantities(self):
        text = clean_ocr(['You caught:', '4 Salmom', '12 Salrnon', '7 Salmn', '+83 XP'], 'reply')
        self.assertIn('4 Salmon', text)
        self.assertIn('12 Salmon', text)
        self.assertIn('7 Salmon', text)
        self.assertIn('+83 XP', text)

    def test_ambiguous_and_short_names_are_not_guessed(self):
        from src.ocr_cleanup import match_fish_name
        reference = {name.casefold(): {'name': name} for name in ('Salmon', 'Salman', 'Cod', 'Fish')}
        self.assertIsNone(match_fish_name('Salmen', reference))
        self.assertIsNone(match_fish_name('sn', reference))
        self.assertIsNone(match_fish_name('Co', reference))
        self.assertIsNone(match_fish_name('Imaginaryfish', reference))
        self.assertEqual(match_fish_name('Salmon', reference), 'Salmon')

    def test_missing_reference_leaves_ocr_unchanged(self):
        from src.ocr_cleanup import match_fish_name
        self.assertIsNone(match_fish_name('Salmom', {}))

    def test_known_salmon_rn_error_preserves_numbers_and_unknown_names(self):
        from src.ocr_cleanup import clean_ocr
        text = clean_ocr(['You caught:', '4 Salrnon', '6 Cod', '+83 XP', '5 Imaginaryfish'], 'reply')
        self.assertIn('4 Salmon', text)
        self.assertIn('+83 XP', text)
        self.assertIn('5 Imaginaryfish', text)

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
    def test_split_and_dated_headers_keep_time_and_invoker(self):
        from src.live_capture import capture_frame
        for header, following in [('Virtual Fisher', ['8:55 AM']),
                                  ('Virtual Fisher — Yesterday at 12:09 PM', [])]:
            message = capture_frame(['epicmouse used profile', header, *following,
                                     'Inventory of epicmouse', 'Balance: $1'])['messages'][0]
            self.assertEqual(message['invoked_by'], 'epicmouse')
            self.assertIsNotNone(message['sent'])

    def test_observed_fish_emoji_fragments_preserve_counts(self):
        self.assertEqual(self.clean('7 y cod', '2 t cod', "3,764 y' Salmon", '296 s° Fish'),
                         ['7 Cod', '2 Cod', '3,764 Salmon', '296 Fish'])
        self.assertEqual(self.clean('1° Emerald Fish'), ['1 Emerald Fish'])

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


class HeaderDashTests(unittest.TestCase):
    def test_header_dash_without_space_is_virtual_fisher(self):
        from src.ocr_cleanup import MESSAGE_HEADER
        self.assertTrue(MESSAGE_HEADER.fullmatch('Virtual Fisher— 8:46 PM'))
        self.assertFalse(MESSAGE_HEADER.fullmatch('Virtual Fisher [fish'))


class GarbledButtonTests(unittest.TestCase):
    def test_garbled_long_button_label_is_dropped(self):
        # Observed 2026-10-08: the "Fish Again" button under a fishing reply read as "ISh Agam".
        from src.ocr_cleanup import clean_ocr
        lines = ['You caught:', '6 Salmon', '6 Cod', '+89 xp', 'Global boost by Someone', 'ISh Agam']
        self.assertEqual(clean_ocr(lines, 't').splitlines()[-1], 'Global boost by Someone')

    def test_lines_that_are_not_close_to_a_button_are_kept(self):
        # A catch line that lost its count must still reach review, never be dropped as chrome.
        from src.ocr_cleanup import clean_ocr
        for line in ('Salmon', 'Fish', 'Reward:', 'Exotic Fish', 'Special'):
            with self.subTest(line=line):
                self.assertIn(line, clean_ocr(['You caught:', line], 't').splitlines())


class SpacedNumberTests(unittest.TestCase):
    def test_spaces_around_a_thousands_comma_are_removed(self):
        from src.ocr_cleanup import clean_ocr
        self.assertEqual(clean_ocr(['$6 , 600 received for leveling up.'], 't'), '$6,600 received for leveling up.')
        self.assertEqual(clean_ocr(['4 , 954 Cod'], 't'), '4,954 Cod')
        self.assertEqual(clean_ocr(['Pet: Dolphin, 51'], 't'), 'Pet: Dolphin, 51', 'only digit groups of three')
        level = 'P4 Level 32, 723/6,500 XP to next level.'
        self.assertEqual(clean_ocr([level], 't'), level, 'a normal comma and space is never joined')
