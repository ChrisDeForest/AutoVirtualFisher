from pathlib import Path
import unittest

from src.fishing_log import parse_fishing
from src.paste_detect import detect, split_messages
from src.player_state import parse_responses

FIXTURES = Path(__file__).parent / 'fixtures'
COPY = (FIXTURES / 'live_discord_copy.txt').read_text(encoding='utf-8')


def fixture(name):
    return (FIXTURES / name).read_text(encoding='utf-8')


class SplitMessagesTests(unittest.TestCase):
    def test_discord_headers_split_messages_and_mark_authors(self):
        messages = split_messages(COPY)
        self.assertEqual(messages[0]['author'], 'Virtual Fisher')
        self.assertTrue(messages[0]['bot'])
        self.assertEqual(messages[0]['when'], '10/3/2025 11:02 AM')
        self.assertEqual(messages[0]['lines'], [':x: You don\'t have enough :emerald_fish:.'])
        users = [message for message in messages if not message['bot']]
        self.assertEqual(len(users), 1)
        self.assertEqual(users[0]['author'], 'epicmouse')
        self.assertNotIn('APP', [line.strip() for message in messages for line in message['lines']])

    def test_headerless_text_is_one_message(self):
        messages = split_messages('Balance: $1\nLevel: 2')
        self.assertEqual(len(messages), 1)
        self.assertIsNone(messages[0]['author'])


class DetectTests(unittest.TestCase):
    def setUp(self):
        self.result = detect(COPY)

    def test_counts_every_kind_and_skipped_message(self):
        self.assertEqual(self.result['counts'], {'profile': 4, 'buffs': 2, 'prestige_shop': 3, 'pet': 1,
                                                 'quests': 1, 'fishing': 26})
        self.assertEqual(self.result['skipped_bot'], 15)
        self.assertEqual(self.result['skipped_user'], 1)

    def test_uses_the_newest_of_each_response(self):
        responses = self.result['responses']
        self.assertIn('Bait: :bait_worms: Worms (10)', responses['profile'])
        self.assertIn('XP Multiplier: 1.84x', responses['buffs'])
        self.assertEqual(self.result['times']['profile'], 'Yesterday at 5:35 PM')
        state = parse_responses(**{kind: responses[kind] for kind in
                                   ('profile', 'buffs', 'prestige_shop', 'pet', 'quests')})
        self.assertEqual(state['profile']['account_name'], 'epicmouse')
        self.assertEqual(state['profile']['level'], 29)
        self.assertEqual(state['profile']['bait'], 'Worms')
        self.assertEqual(state['buffs']['xp_multiplier'], {'value': 1.84, 'unit': 'multiplier'})
        self.assertEqual(state['prestige']['inferred_prestige_minimum'], 4)
        self.assertEqual(len(state['pets']['owned']), 5)
        self.assertEqual(state['quests']['daily']['Daily Fishing']['progress'], 101)
        self.assertEqual(state['warnings'], [])

    def test_all_fishing_results_form_one_batch(self):
        tally = parse_fishing(self.result['fishing'])
        self.assertEqual(tally['trips'], 26)
        self.assertEqual(tally['warnings'], [])
        self.assertEqual(tally['chest_xp'], 217)
        self.assertEqual(len(tally['quests_completed']), 3)

    def test_headerless_responses_are_split_by_their_first_lines(self):
        text = '\n'.join(fixture(name) for name in ('live_profile.txt', 'live_quests.txt', 'live_fishing.txt',
                                                     'live_buffs.txt'))
        result = detect(text)
        self.assertEqual(result['counts'], {'profile': 1, 'buffs': 1, 'quests': 1, 'fishing': 1})
        self.assertEqual(parse_responses(profile=result['responses']['profile'])['warnings'], [])
        self.assertEqual(parse_fishing(result['fishing'])['trips'], 6)
        self.assertEqual(parse_fishing(result['fishing'])['warnings'], [])
        self.assertIn('Quest List', result['responses']['quests'])

    def test_unrecognized_text_is_reported(self):
        result = detect('hello there\nnothing to see')
        self.assertEqual(result['counts'], {})
        self.assertEqual(result['skipped_bot'], 1)


if __name__ == '__main__':
    unittest.main()
