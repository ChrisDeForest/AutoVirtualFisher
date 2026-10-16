from dataclasses import replace
import unittest
from src.automation_state import AutomationState
from src.observation import Observation


class StartupFieldsTests(unittest.TestCase):
    def seen(self, text, kind='profile', ident='one'):
        return Observation(ident, ident, '2026-10-07T15:00:00+00:00', 'dm', kind, 'sim', text, 'verified')

    def test_complementary_reads_retain_confirmed_fields(self):
        from src.startup_fields import apply_startup, startup_problems
        state = AutomationState('run', 'sim', 'sim0001', 'dm')
        state = apply_startup(state, self.seen('Inventory of sim\nBalance: $50.\nLevel: 1O'))
        self.assertEqual(state.profile['balance'], 50)
        self.assertEqual(startup_problems(state, 'profile'), ['Level has not been read.'])
        state = apply_startup(state, self.seen('Inventory of sim\nBalance: $5O.\nP4 Level 30, xp to next leveL\nFish Invent«y', ident='two'))
        self.assertEqual(state.profile['balance'], 50)
        self.assertEqual(state.profile['level'], 30)
        self.assertEqual(startup_problems(state, 'profile'), [])

    def test_optional_buffs_text_does_not_hide_valid_cooldown(self):
        from src.startup_fields import apply_startup, startup_problems
        state = AutomationState('run', 'sim', 'sim0001', 'dm')
        state = apply_startup(state, self.seen("sim0001's current multipliers (including bait):\nFish quality: ???\nFishing cooldown: 3.5s.", 'buffs'))
        self.assertEqual(state.cooldown_seconds, 3.5)
        self.assertEqual(startup_problems(state, 'buffs'), [])

    def test_wrong_identity_and_conflicting_required_lines_do_not_establish_values(self):
        from src.startup_fields import apply_startup
        state = AutomationState('run', 'sim', 'sim0001', 'dm')
        self.assertEqual(apply_startup(state, self.seen('Inventory of other\nBalance: $50.\nLevel: 30')), state)
        partial = apply_startup(state, self.seen('Inventory of sim\nBalance: $50.\nBalance: $60.\nLevel: 30'))
        self.assertIsNone(partial.profile.get('balance'))

    def test_unverified_and_other_channel_are_ignored(self):
        from src.startup_fields import apply_startup
        state = AutomationState('run', 'sim', 'sim0001', 'dm')
        observation = self.seen('Inventory of sim\nBalance: $50.\nLevel: 30')
        for bad in (replace(observation, identity_status='unknown'), replace(observation, context_id='other')):
            self.assertEqual(apply_startup(state, bad), state)


class ObservedGlyphTests(unittest.TestCase):
    def test_buffs_bullet_apostrophe_and_underscore_period(self):
        # Native OCR, 2026-10-08: "'s" read as "•s" and the closing period as "_".
        from src.startup_fields import reply_fields
        text = 'epicmouse9925•s current multipliers (including bait):\nFishing cooldown: 3.5s_'
        self.assertEqual(reply_fields('buffs', text), {'username': 'epicmouse9925', 'cooldown_seconds': 3.5})
        self.assertNotIn('cooldown_seconds', reply_fields('buffs', text.replace('3.5s_', '3_5s')))


class DroppedDecimalTests(unittest.TestCase):
    # Live, 2026-10-16: Windows OCR read "Fishing cooldown: 2.0s." as "20s." at x1 and x2 (x3 read it right), and the
    # run waited 20 s a trip. The game always writes the cooldown with one decimal.
    TEXT = "epicmouse9925's current multipliers (including bait):\nFishing cooldown: {}"

    def test_cooldown_without_a_decimal_point_is_not_read(self):
        from src.startup_fields import reply_fields, reply_problems
        self.assertNotIn('cooldown_seconds', reply_fields('buffs', self.TEXT.format('20s.')))
        self.assertEqual(reply_problems('buffs', self.TEXT.format('20s.')), ['Fishing cooldown has not been read.'])
        self.assertEqual(reply_fields('buffs', self.TEXT.format('2.0s.'))['cooldown_seconds'], 2.0)

    def test_dropped_decimal_never_sets_the_cooldown(self):
        from src.automation_state import apply_observation, refresh_problems
        state = AutomationState('run', 'sim', 'epicmouse9925', 'dm', cooldown_seconds=2.1)
        seen = Observation('o1', 'o1', '2026-10-16T00:11:46+00:00', 'dm', 'buffs', 'sim', self.TEXT.format('20s.'),
                           'verified')
        self.assertTrue(refresh_problems('buffs', seen.text))
        self.assertEqual(apply_observation(state, seen).cooldown_seconds, 2.1)
