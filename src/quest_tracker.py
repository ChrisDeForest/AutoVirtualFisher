"""Part 7: the day's quests from /quests, when to read them again, and the Booster quest's minutes. Pure: no I/O.

Quests reset at 00:00 UTC; the reply's "Quests reset in 5h 29m" is taken as the truth. Only the Booster special quest
(activate 360 minutes of boosts) changes what the run does: the boost keeper runs all three boosts while it is open.
"""
from datetime import timedelta

READ_EVERY = 1800
BOOSTER = 'Booster'
BOOSTER_MINUTES = 360


class QuestTracker:
    def __init__(self):
        self.daily, self.special = {}, {}
        self.reset_at = None
        self.boost_minutes = 0.0
        self.rolled = False
        self.minutes_at_read = 0.0     # boost_minutes when /quests was last read

    def _roll(self, now):
        """At the reset: a new day, with nothing known of its quests and no boost minutes. The reset moves on a day,
        so minutes bought before the new day's quests are read still count."""
        if self.reset_at is not None and now >= self.reset_at:
            self.daily, self.special, self.boost_minutes, self.minutes_at_read = {}, {}, 0.0, 0.0
            while self.reset_at <= now:
                self.reset_at += timedelta(days=1)
            self.rolled = True      # read the new day's quests

    def apply_read(self, values, now):
        self._roll(now)
        self.rolled = False
        self.daily = dict(values.get('daily') or {})
        self.special = dict(values.get('special') or {})
        minutes = values.get('resets_in_minutes')
        if minutes is not None:
            new_reset = now + timedelta(minutes=minutes)
            if self.reset_at is not None and new_reset - self.reset_at > timedelta(hours=1):
                self.boost_minutes = 0.0     # a new day's quests: minutes from before the reset do not count
            self.reset_at = new_reset
        self.minutes_at_read = self.boost_minutes

    def add_boost_minutes(self, minutes, now):
        self._roll(now)
        self.boost_minutes += minutes

    def booster_open(self, now):
        self._roll(now)
        quest = self.special.get(BOOSTER)
        if not quest or quest.get('done'):
            return False
        # The quest's own progress counts too (after a restart the minutes bought here start from 0), with the
        # minutes bought since it was read.
        done = max(self.boost_minutes, (quest.get('progress') or 0) + self.boost_minutes - self.minutes_at_read)
        return done < (quest.get('goal') or BOOSTER_MINUTES)

    def _all_done(self):
        quests = list(self.daily.values()) + list(self.special.values())
        return bool(quests) and all(quest.get('done') for quest in quests)

    def read_due(self, now, last_read_at):
        self._roll(now)
        if last_read_at is None or self.rolled:
            return True
        if self.reset_at is not None and now >= self.reset_at and last_read_at < self.reset_at:
            return True
        return not self._all_done() and (now - last_read_at).total_seconds() >= READ_EVERY

    def view(self, now):
        self._roll(now)

        def rows(group):
            return [{'name': name, **{key: quest.get(key) for key in ('progress', 'goal', 'done', 'task')}}
                    for name, quest in group.items()]
        left = (self.reset_at - now).total_seconds() if self.reset_at is not None else None
        return {'daily': rows(self.daily), 'special': rows(self.special),
                'resets_in': max(0, int(left)) if left is not None else None,
                'boost_minutes': int(self.boost_minutes)}
