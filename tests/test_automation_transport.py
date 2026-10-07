from datetime import datetime, timedelta, timezone
import unittest

from src.automation_models import ActionIntent, CapabilityError, TransportError
from src.automation_transport import SimulationTransport


class Clock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def utc(self):
        return datetime(2026, 10, 6, 12, tzinfo=timezone.utc) + timedelta(seconds=self.now)


def intent(number, kind='fish'):
    reply = {'fish': 'fishing', 'refresh_profile': 'profile', 'refresh_buffs': 'buffs'}[kind]
    return ActionIntent(f'a{number}', kind, 'sim:dm', '', reply)


class SimulationTransportTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()

    def transport(self, **options):
        return SimulationTransport(self.clock.monotonic, self.clock.utc, **options)

    def test_successful_trip_arrives_after_latency_with_correlation(self):
        sim = self.transport(latency=0.5)
        sim.send(intent(1))
        self.assertEqual(sim.poll(), [], 'nothing before the reply latency')
        self.clock.now = 0.5
        [reply] = sim.poll()
        self.assertEqual((reply.kind, reply.correlation, reply.identity_status), ('fishing', 'a1', 'verified'))
        self.assertIn('You caught:', reply.text)
        self.assertEqual(sim.totals['trips'], 1)

    def test_fishing_during_cooldown_gets_a_cooldown_reply(self):
        sim = self.transport(cooldown=3.5, latency=0)
        sim.send(intent(1))
        self.clock.now = 1.0
        sim.send(intent(2))
        replies = sim.poll()
        self.assertRegex(replies[-1].text, r'You must wait 2\.5 seconds before fishing again')
        self.assertEqual(sim.totals['trips'], 1)

    def test_scripted_outcomes_are_reproducible(self):
        def run():
            clock = Clock()
            sim = SimulationTransport(clock.monotonic, clock.utc, latency=0, seed=7,
                                      script={1: 'unreadable', 2: 'missing', 3: 'duplicate'})
            for number in range(4):
                clock.now = number * 4.0
                sim.send(intent(number))
            return [(reply.observation_id, reply.text) for reply in sim.poll()], sim.totals
        first, second = run(), run()
        self.assertEqual(first, second)
        replies, totals = first
        self.assertEqual(len(replies), 4, 'a0, a1 (unreadable), no a2, a3 delivered twice')
        self.assertEqual(replies[-1], replies[-2], 'the duplicate is the same observation twice')
        self.assertEqual(totals['trips'], 4, 'the game fished for the missing reply too')

    def test_send_failure_and_capabilities(self):
        sim = self.transport(script={0: 'send_error'})
        with self.assertRaises(TransportError):
            sim.send(intent(0))
        with self.assertRaises(CapabilityError):
            sim.send(ActionIntent('x', 'sell', 'sim:dm', '', 'sell'))
        self.assertEqual(len(sim.sent), 1, 'an unsupported action is rejected before dispatch')

    def test_profile_and_buffs_replies(self):
        sim = self.transport(latency=0, cooldown=2.5)
        sim.send(intent(1, 'refresh_profile'))
        sim.send(intent(2, 'refresh_buffs'))
        profile, buffs = sim.poll()
        self.assertEqual((profile.kind, buffs.kind), ('profile', 'buffs'))
        self.assertIn('Fishing cooldown: 2.5s', buffs.text)


if __name__ == '__main__':
    unittest.main()
