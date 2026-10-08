"""Replies that live automation sent to Needs review on 2026-10-07, replayed through today's text pipeline.

Pixel-level recoveries (a balance native OCR dropped) are in test_reply_ocr.NativeCaptureTests."""
import json
from pathlib import Path
import unittest

from src.live_capture import fishing_problems
from src.ocr_cleanup import clean_ocr
from src.startup_fields import reply_fields

REPLIES = {reply['observed']: reply for reply in json.loads(
    (Path(__file__).parent / 'fixtures' / 'live_review_replies.json').read_text(encoding='utf-8'))}


class LiveReviewRegressionTests(unittest.TestCase):
    def test_optional_profile_noise_no_longer_blocks_startup(self):
        # Failed then on "Fish Invent?y", "a933 Salmon" and a level line without its XP numbers.
        for observed in ('20261007T143334.', '20261007T143353.', '20261007T152900.', '20261007T152919.'):
            with self.subTest(observed=observed):
                self.assertEqual(reply_fields('profile', REPLIES[observed]['text']),
                                 {'account_name': 'epicmouse', 'balance': 47210, 'level': 30})

    def test_a_dropped_balance_is_still_missing_from_text_alone(self):
        for observed in ('20261007T165553.', '20261007T165629.', '20261007T170853.'):
            with self.subTest(observed=observed):
                self.assertNotIn('balance', reply_fields('profile', REPLIES[observed]['text']))

    def test_chest_money_trip_reads_cleanly(self):
        self.assertEqual(fishing_problems(REPLIES['20261007T154227.']['text']), [])

    def test_lost_level_number_is_the_only_remaining_fishing_problem(self):
        text = clean_ocr(REPLIES['20261007T161758.']['text'].splitlines(), 'regression')
        self.assertEqual(fishing_problems(text), ['Unrecognized line: LEVEL UP! You are'])

    def test_buffs_glyphs_from_2026_10_08_read_cleanly(self):
        self.assertEqual(reply_fields('buffs', REPLIES['20261008T004634.']['text']),
                         {'username': 'epicmouse9925', 'cooldown_seconds': 3.5})

    def test_header_dash_without_space_still_gives_a_baseline(self):
        from src.automation_discord import stable_frame
        frame = stable_frame(REPLIES['20261008T164024.']['text'].splitlines())
        self.assertEqual([message['kind'] for message in frame['messages']], ['buffs', 'buffs'])

    def test_garbled_fish_again_button_is_not_part_of_the_reply(self):
        from src.automation_discord import stable_frame
        reply = stable_frame(REPLIES['20261008T170125.']['text'].splitlines())['messages'][-1]
        self.assertEqual(fishing_problems('\n'.join(reply['lines'])), [])
