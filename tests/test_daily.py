import unittest

from src.actions import Action, check, command_line, outcome, reads_after

DAILY = Action('daily')
SUCCESS = 'Daily reward\nMedium Exotic crate!\nYou gained:\n+38 :diamond_fish:\n\nStreak: 1\nBest streak: 8'
COOLDOWN = ':x: Daily reward on cooldown! (21h 14m 11s)'


class DailyAction(unittest.TestCase):
    def test_command_and_label(self):
        self.assertEqual(command_line(DAILY), '/daily')
        self.assertEqual(DAILY.label(), 'Claim daily')
        self.assertIsNone(check(DAILY, {}, {}))
        self.assertEqual(reads_after(DAILY), ('refresh_profile',))

    def test_replies(self):
        self.assertEqual(outcome(DAILY, SUCCESS), ('done', 'streak 1'))
        self.assertEqual(outcome(DAILY, COOLDOWN), ('done', 'on cooldown, 21h 14m 11s left'))
        self.assertEqual(outcome(DAILY, 'You caught:\n3 Cod'), ('unclear', 'no daily reply'))

    def test_cooldown_ocr_ones_are_read_as_ones(self):
        # Final review: "(2lh 14m 11s)" read as 14m 11s would retry 21 hours early.
        self.assertEqual(outcome(DAILY, ':x: Daily reward on cooldown! (2lh 14m 1Is)'),
                         ('done', 'on cooldown, 21h 14m 11s left'))


class DailySimulation(unittest.TestCase):
    def test_success_then_cooldown(self):
        from types import SimpleNamespace
        from src.automation_transport import SimulationTransport
        clock = [0.0]
        sim = SimulationTransport(lambda: clock[0], None)
        intent = SimpleNamespace(arguments=[DAILY.to_dict()])
        self.assertEqual(outcome(DAILY, sim._act(intent)), ('done', 'streak 1'))
        clock[0] = 100
        self.assertEqual(outcome(DAILY, sim._act(intent)), ('done', 'on cooldown, 23h 58m 20s left'))
        clock[0] = 86500
        self.assertEqual(outcome(DAILY, sim._act(intent)), ('done', 'streak 2'))
