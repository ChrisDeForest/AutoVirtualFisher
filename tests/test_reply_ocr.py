import importlib.util
import json
from pathlib import Path
import shutil
import unittest


class FishingRereadTests(unittest.TestCase):
    def test_balance_recovery_does_not_require_readable_inventory(self):
        from src.reply_ocr import repair_balance_field
        original = ['Inventory of epicmouse', 'Balance:', 'Level 31', 'Fish Invent«y']
        primary = ['Inventory of epicmouse', 'Balance: $54,056.', 'unreadable inventory']
        peer = ['Inventory of epicmouse', 'Balance: $54,056.', 'different inventory noise']
        repaired = repair_balance_field(original, primary, [peer])
        self.assertEqual(repaired, ['Inventory of epicmouse', 'Balance: $54,056.', 'Level 31', 'Fish Invent«y'])
        self.assertIsNone(repair_balance_field(original, primary, [[line.replace('54,056', '54,058') for line in peer]]))
        self.assertIsNone(repair_balance_field(original, primary, [[line.replace('epicmouse', 'other') for line in peer]]))

    def test_long_reply_requires_independent_agreement_on_missing_values(self):
        from src.reply_ocr import agreed_reply
        reply = ['You caught:', '2 Fish', '4 Salmon', '9 Cod', '+117 XP',
                 'Global boost by HIYA BLAKE', 'LEVEL UP! You are now level 31.',
                 '$6,200 received for leveling up.', 'QUEST COMPLETE!',
                 'Daily Level-ups Tier 1', 'Reward:', '+$350', '1x uncommon chest',
                 'You got 150 XP from the chest.']
        self.assertEqual(agreed_reply('fishing', 'You caught:', reply, [reply]), reply)
        wrong = [line.replace('+$350', '+$850') for line in reply]
        self.assertIsNone(agreed_reply('fishing', 'You caught:', reply, [wrong]))
        broken = [line.replace('now level 31.', '') for line in reply]
        self.assertIsNone(agreed_reply('fishing', 'You caught:', broken, [reply]))
from unittest.mock import patch

from src.reply_ocr import agreed_reply


PRIMARY = ['Inventory of epicmouse =', 'Balance: $47,210.',
           'P4 Level 30, 3,657/5,750 XP to next level.', 'Currently using “ Steel Rod.',
           'Current biome: id River', 'Pet: * Dolphin (Level 51)', 'Fish Inventory',
           '296 #° Fish', '3,764 © Salmon', '4,754 #° Cod', 'Fish Value: $130,211',
           'Exotic Fish', '2 #° Gold Fish', '1 #° Emerald Fish', 'Special', '10 ~, Hooks']
PEER = ['Inventory of epicmouse', 'Balance: $47,210-',
        'P4 Level 30, 3,657/5,750 XP to next level-', 'Currently using Z Steel Rod-',
        'Current biome:@River', 'Pet: Dolphin (Level 51)', 'Fish Inventory',
        '296 Fish', '3,764 Salmon', '4,754 cod', 'Fish Value: $130,211',
        'Exotic Fish', '2 Gold Fish', '1 Emerald Fish', 'Special', '10 Hooks']


class ReplyOcrTests(unittest.TestCase):
    @patch('src.reply_ocr.shutil.which', return_value='tesseract')
    @patch('src.screenshot_ocr.save_region')
    def test_required_fields_skip_optional_profile_rereads(self, save, executable):
        from src.reply_ocr import repair_reply_regions
        lines = ['Inventory of sim', 'Balance: $50.', 'P4 Level 30, xp to next leveL', 'Fish Invent«y']
        boxes = [dict(text=text, left=450, right=750, top=300+i*20, bottom=313+i*20)
                 for i, text in enumerate(lines)]
        self.assertEqual(repair_reply_regions(boxes, 'unused.bmp', 1408), lines)
        save.assert_not_called()

    def test_profile_field_rereads_require_independent_values(self):
        from src.reply_ocr import profile_field_candidates, profile_level_peers
        primary = [s.replace('4,754', '4,154') for s in PRIMARY]
        corrected = profile_field_candidates(primary, PRIMARY, [PEER])
        self.assertIsNotNone(agreed_reply('profile', PEER[0], corrected, [PEER]))
        wrong = [s.replace('4,754', '4,755') for s in PRIMARY]
        self.assertIsNone(agreed_reply('profile', PEER[0],
                                     profile_field_candidates(primary, wrong, [PEER]), [PEER]))
        missing = [s.replace('3,657/5,750 ', '') for s in PEER]
        restored = profile_level_peers(corrected, [missing], ['P4 Level 30.3657/5,750 XP to next level.'])
        self.assertIsNotNone(agreed_reply('profile', PEER[0], corrected, restored))
        rejected = profile_level_peers(corrected, [missing], ['P4 Level 30.3658/5,750 XP to next level.'])
        self.assertIsNone(agreed_reply('profile', PEER[0], corrected, rejected))

    def test_native_fishing_caption_and_button_row(self):
        from src.live_capture import join_boost_caption, fishing_problems
        from src.reply_ocr import chrome_rows
        from src.ocr_cleanup import clean_ocr
        def box(text, left, top, right):
            return dict(text=text, left=left, top=top, right=right, bottom=top + 13)
        boxes = [box('You caught:', 466, 1138, 560), box('6 Salmon', 466, 1165, 540),
                 box('6 y cod', 466, 1185, 540), box('+89 xp', 466, 1205, 508),
                 box('Global boost by', 466, 1225, 559), box('HIYA BLAKE', 581, 1225, 659),
                 box('ISh Agam', 509, 1268, 563)]
        joined = join_boost_caption(boxes)
        skipped = chrome_rows(joined, [box('Return', 660, 1268, 700)])
        text = clean_ocr([b['text'] for i, b in enumerate(joined) if i not in skipped], 'test')
        self.assertEqual(fishing_problems(text), [])
        boxes[5]['top'] += 20
        boxes[5]['bottom'] += 20
        self.assertIn('Global boost by', [b['text'] for b in join_boost_caption(boxes)])
        self.assertEqual(chrome_rows(joined, [box('Return', 660, 1300, 700)]), set())
        self.assertEqual(chrome_rows(joined, [box('Inventory', 100, 1165, 200)]), set())
        self.assertEqual(chrome_rows(joined, [box('Pets', 2270, 1185, 2350)]), set())

    def test_buff_decimal_repair_requires_exact_cross_engine_value(self):
        from src.reply_ocr import buff_field_candidates
        identity = "epicmouse9925's current multipliers (including bait):"
        primary = [identity, 'Fish quality: 121x', 'Fishing cooldown: 3.5s.']
        peers = [[identity, 'Fish quality: 1.211', 'Fishing cooldown: 3.5s-']]
        candidate = buff_field_candidates(primary, ['Fish quality: 1.21x'], peers)
        self.assertEqual(candidate[1], 'Fish quality: 1.21x')
        self.assertIsNotNone(agreed_reply('buffs', identity, candidate, peers))
        candidate = buff_field_candidates(primary, ['Fish quality: 1.22x'], peers)
        self.assertIsNone(agreed_reply('buffs', identity, candidate, peers))

    def test_buffs_identity_apostrophe_glyph_preserves_name(self):
        identity = "epicmouse9925's current multipliers (including bait):"
        original = identity.replace("'", '\ufffd')
        lines = [identity, 'Fishing cooldown: 3.5s']
        self.assertEqual(agreed_reply('buffs', original, lines, [lines]), lines)
        self.assertIsNone(agreed_reply('buffs', original,
                                      [identity.replace('9925', '9926'), lines[1]], [lines]))

    def test_chrome_rows_remove_garbled_button_only(self):
        from src.reply_ocr import chrome_rows
        def box(text, top):
            return dict(text=text, top=top, bottom=top+15, left=450, right=550)
        boxes = [box('unreadable body', 100), box('Inventory', 200), box('harms', 201)]
        self.assertEqual(chrome_rows(boxes), {1, 2})

    def test_level_line_missing_its_xp_is_completed_from_an_agreeing_reread(self):
        # Live: Windows OCR dropped "8,698/20,000" from the line at every scale; Tesseract read it each time.
        from src.reply_ocr import complete_level_line
        primary = 'P4 Level 49, XP to next level.'
        whole = 'P4 Level 49, 8,698/20,000 XP to next level.'
        self.assertEqual(complete_level_line(primary, iter([[whole], None, [whole]])), whole)
        self.assertEqual(complete_level_line(primary, iter([[whole], ['P4 Level 49, 8,690/20,000 XP to next level.']])),
                         primary, 'two rereads must agree on every digit')
        level_48 = 'P4 Level 48, 8,698/20,000 XP to next level.'
        self.assertEqual(complete_level_line(primary, iter([[level_48], [level_48]])), primary,
                         'rereads that disagree with the line on the level are not used')
        rereads = iter([[whole]])
        self.assertEqual(complete_level_line(whole, rereads), whole)
        self.assertEqual(list(rereads), [[whole]], 'a complete line is not reread')

    def test_a_button_beside_a_shop_item_keeps_the_item(self):
        # /shop rods: a Select button level with each rod name, far to its right, is part of the reply.
        from src.reply_ocr import chrome_rows
        def box(text, left, top, right):
            return dict(text=text, left=left, top=top, right=right, bottom=top + 20)
        boxes = [box('Supporter Rod', 620, 835, 775), box('Select', 1123, 845, 1181),
                 box('Rod given as thanks for supporting Virtual Fisher.', 583, 874, 1018),
                 box('Page 1/2', 583, 1276, 659), box('Back', 605, 1326, 649)]
        self.assertEqual(chrome_rows(boxes), {4})
        # Live: the button's text sat 9 px lower than the rod name's.
        boxes = [dict(box('Plastic Rod', 621, 915, 740), bottom=933), dict(box('Select', 1123, 925, 1181), bottom=940)]
        self.assertEqual(chrome_rows(boxes), set())

    def test_numeric_labels_and_inventory_categories_must_agree(self):
        peer = [line.replace('Balance:', 'TEMP:').replace('Fish Value:', 'Balance:')
                .replace('TEMP:', 'Fish Value:') for line in PEER]
        self.assertIsNone(agreed_reply('profile', PEER[0], PRIMARY, [peer]))
        peer = [line.replace('Exotic Fish', 'Fish Inventory') for line in PEER]
        self.assertIsNone(agreed_reply('profile', PEER[0], PRIMARY, [peer]))

    def test_missing_primary_equipment_is_not_agreement(self):
        primary = [line for line in PRIMARY if not line.startswith('Currently using')]
        self.assertIsNone(agreed_reply('profile', PEER[0], primary, [PEER]))

    @patch('src.reply_ocr.shutil.which', return_value=None)
    def test_missing_independent_engine_keeps_original(self, which):
        from src.reply_ocr import repair_reply_regions
        self.assertEqual(repair_reply_regions([{'text': 'unreadable'}], None, 1408), ['unreadable'])

    def test_buffs_units_must_agree(self):
        identity = "epicmouse9925's current multipliers (including bait):"
        primary = [identity, 'Fishing cooldown: 3.5s']
        self.assertEqual(agreed_reply('buffs', identity, primary, [primary]), primary)
        self.assertIsNone(agreed_reply('buffs', identity, primary,
                                      [[identity, 'Fishing cooldown: 3.5x']]))

    def test_real_independent_reads_recover_complete_profile(self):
        from src.player_state import parse_responses
        lines = agreed_reply('profile', 'Inventory of epicmouse', PRIMARY, [PEER])
        self.assertIsNotNone(lines)
        state = parse_responses(profile='\n'.join(lines))
        self.assertEqual(state['profile']['xp_current'], 3657)
        self.assertEqual(state['profile']['rod'], 'Steel Rod')
        self.assertEqual(state['inventory']['fish']['Salmon'], 3764)

    def test_disagreeing_or_missing_numbers_never_repair(self):
        for text in ('P4 Level 30, 3,658/5,750 XP to next level-',
                     'P4 Level 30, XP to next level-'):
            peer = [text if line.startswith('P4') else line for line in PEER]
            self.assertIsNone(agreed_reply('profile', PEER[0], PRIMARY, [peer]))
        peer = [line.replace('$47,210', '-$47,210') for line in PEER]
        self.assertIsNone(agreed_reply('profile', PEER[0], PRIMARY, [peer]))

    def test_changed_identity_or_equipment_never_repairs(self):
        for old, new in [('Inventory of epicmouse', 'Inventory of another'),
                         ('Dolphin', 'Tuna'), ('Steel Rod', 'Wooden Rod')]:
            peer = [line.replace(old, new) for line in PEER]
            self.assertIsNone(agreed_reply('profile', PEER[0], PRIMARY, [peer]))
        primary = ['Inventory of another' if line.startswith('Inventory of') else line for line in PRIMARY]
        self.assertIsNone(agreed_reply('profile', PEER[0], primary, [PEER]))

    def test_unparsed_primary_stays_rejected(self):
        self.assertIsNone(agreed_reply('profile', PEER[0], PRIMARY + ['unreadable text'], [PEER]))


class FieldConsensusTests(unittest.TestCase):
    """One required field, reread from several preparations; values from real native captures."""
    PROFILE = 'Inventory of epicmouse'
    BUFFS = "epicmouse9925's current multipliers (including bait):"

    def consensus(self, kind, field, reads):
        from src.reply_ocr import field_consensus
        return field_consensus(kind, self.PROFILE if kind == 'profile' else self.BUFFS, field, reads)

    def test_inverted_tesseract_and_native_agreement_recovers_balance(self):
        reads = [('tesseract', '| Balance: $47,210.'), ('tesseract', 'Balance: $47,210.'),
                 ('windows', 'Balance: $47,210-')]
        self.assertEqual(self.consensus('profile', 'balance', reads), (47210, 'Balance: $47,210.'))

    def test_any_conflicting_read_rejects_the_field(self):
        # Observed: a 100-threshold crop read 47,210 as 47,710 at two scales.
        reads = [('tesseract', '| Balance: $47,210.'), ('windows', 'Balance: $47,210'),
                 ('tesseract', '| Balance: $47,710.')]
        self.assertIsNone(self.consensus('profile', 'balance', reads))

    def test_same_engine_needs_three_agreeing_preparations(self):
        two = [('tesseract', 'Balance: $47,210.'), ('tesseract', '| Balance: $47,210.')]
        self.assertIsNone(self.consensus('profile', 'balance', two))
        three = two + [('tesseract', 'Balance: $47,210')]
        self.assertEqual(self.consensus('profile', 'balance', three)[0], 47210)
        self.assertIsNone(self.consensus('profile', 'balance', [('windows', 'Balance: $47,210')] * 3))

    def test_unreadable_reads_neither_count_nor_conflict(self):
        reads = [('tesseract', ''), ('tesseract', '| P4 Lewel 30, 3,/46/5,750 XP'),
                 ('tesseract', '| P4 Level 30, 3,746/5,750 XP to next level.'),
                 ('windows', 'P4 Level 30, XP to next leveL')]
        self.assertEqual(self.consensus('profile', 'level', reads),
                         (30, 'P4 Level 30, 3,746/5,750 XP to next level.'))

    def test_buffs_cooldown_requires_seconds(self):
        reads = [('tesseract', 'Fishing cooldown: 3.5s.'), ('windows', 'Fishing cooldown: 3.5s-')]
        self.assertEqual(self.consensus('buffs', 'cooldown_seconds', reads)[0], 3.5)
        self.assertIsNone(self.consensus('buffs', 'cooldown_seconds',
                                         [('tesseract', 'Fishing cooldown: 3.5s.'), ('windows', 'Fishing cooldown: 35s')]))


class RequiredFieldRereadTests(unittest.TestCase):
    LINES = ['Inventory of epicmouse', 'Balance:', 'P4 Level 31, xp to next level.', 'Fish Invent«y']

    def boxes(self):
        return [dict(text=text, left=466, right=560, top=868 + i * 20, bottom=881 + i * 20)
                for i, text in enumerate(self.LINES)]

    def run_repair(self, tesseract, windows):
        from types import SimpleNamespace
        from src.reply_ocr import repair_reply_regions
        with patch('src.reply_ocr.shutil.which', return_value='tesseract'), \
                patch('src.ocr_image.contrast_region') as contrast, \
                patch('src.screenshot_ocr.save_region') as save, \
                patch('src.screenshot_ocr.read_image_boxes', return_value=[]), \
                patch('src.screenshot_ocr.read_region_lines', return_value=windows), \
                patch('src.reply_ocr.subprocess.run',
                      return_value=SimpleNamespace(returncode=0, stdout=tesseract)):
            return repair_reply_regions(self.boxes(), 'capture.bmp', 1408), contrast, save

    def test_missing_balance_is_reread_from_its_own_line(self):
        lines, contrast, save = self.run_repair('| Balance: $47,210.\n', ['Balance:', '$47,210-'])
        self.assertEqual(lines, ['Inventory of epicmouse', 'Balance: $47,210.', *self.LINES[2:]])
        save.assert_not_called()  # the expensive whole-reply reread was not needed

    def test_conflicting_field_rereads_keep_the_original(self):
        lines, _, _ = self.run_repair('| Balance: $47,210.\n', ['Balance:', '$47,710'])
        self.assertEqual(lines, self.LINES)

    def test_whole_reply_tesseract_read_uses_an_inverted_crop(self):
        _, contrast, save = self.run_repair('unreadable\n', ['unreadable'])
        save.assert_not_called()
        self.assertIn(None, [call.kwargs.get('threshold') for call in contrast.call_args_list])


SOURCE = Path(__file__).parent.parent / 'docs' / 'planning' / 'live-probe' / 'startup-source.bmp'


@unittest.skipUnless(SOURCE.exists() and shutil.which('tesseract') and importlib.util.find_spec('winrt'),
                     'needs the local native capture, Tesseract and Windows OCR')
class NativeCaptureTests(unittest.TestCase):
    def test_real_capture_recovers_a_balance_native_ocr_dropped(self):
        from src.reply_ocr import repair_reply_regions
        from src.startup_fields import reply_fields
        capture = json.loads(SOURCE.with_name('startup-failure.json').read_text(encoding='utf-8'))
        boxes = [dict(box) for box in capture['boxes'] if 860 <= box['top'] <= 1240 and box['left'] < 900]
        balance = next(box for box in boxes if box['text'].startswith('Balance'))
        # As in the 16:55 runs: native OCR kept the label but lost the amount.
        balance.update(text='Balance:', right=balance['left'] + 50)
        lines = repair_reply_regions(boxes, SOURCE, 1408)
        self.assertEqual(reply_fields('profile', '\n'.join(lines)),
                         {'account_name': 'epicmouse', 'balance': 47210, 'level': 30})
