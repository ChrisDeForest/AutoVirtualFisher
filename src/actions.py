"""Part 4: the actions a run takes on request, how each is typed, and what its reply means. Pure: no I/O.

Formats: docs/progression/notes.md, "Action command formats" and "Action replies" (2026-10-13). Typing the whole
line makes Discord pick the command and fill its first option; a buy's amount is reached with three Tabs.
"""
from dataclasses import asdict, dataclass, fields
import re

COMMAND = {'buy': '/buy', 'sell': '/sell', 'rod': '/rod', 'biome': '/biome', 'bait': '/bait', 'pet': '/pet'}
# Tabs from the first option to a buy's amount (user, 2026-10-13), each after a pause.
AMOUNT_TABS = 3
SHOP_READ = {'upgrades': 'refresh_upgrades', 'special': 'refresh_special', 'league': 'refresh_league',
             'rods': 'refresh_rod', 'boats': 'refresh_boats', 'bait': 'refresh_bait'}
SWITCH_READ = {'rod': 'refresh_rod', 'biome': 'refresh_biome', 'bait': 'refresh_bait', 'pet': 'refresh_pets'}
# Shops paid in money: the balance is checked before buying. League buys check the Hooks from /shop league;
# special (exotic fish) buys are left to the game's reply.
MONEY_SHOPS = ('upgrades', 'rods', 'boats', 'bait')


@dataclass(frozen=True)
class Action:
    act: str                   # 'buy', 'sell', 'rod', 'biome', 'bait' or 'pet'
    name: str = ''             # the item, rod, biome, bait or pet; '' for sell, and for bait: none
    amount: int | None = None  # a buy's amount (bait)
    level: int | None = None   # the level an upgrade reaches
    shop: str = ''             # a buy's shop: 'upgrades', 'special', 'league', 'rods', 'boats' or 'bait'
    price: float = 0           # a buy's price (0: not checked)

    @classmethod
    def from_dict(cls, data):
        return cls(**{field.name: data[field.name] for field in fields(cls) if field.name in data})

    def to_dict(self):
        return asdict(self)

    def label(self):
        if self.act == 'buy':
            if self.amount:
                return f'Buy {self.amount:,} {self.name}'
            return f'Buy {self.name}' + (f' {self.level}' if self.level else '')
        if self.act == 'sell':
            return 'Sell all fish'
        return f'Switch to {self.name or "no bait"}'


def command_line(action):
    """The line typed for the action: the command, then its first option's value."""
    if action.act == 'sell':
        return '/sell all'
    value = action.name or ('none' if action.act == 'bait' else '')
    return f'{COMMAND[action.act]} {value.lower()}'.rstrip()


def outcome(action, text):
    """('done', note), ('failed', why) or ('unclear', why) for the reply to `action`."""
    text = re.sub(r'\s+', ' ', text.replace('’', "'")).strip()
    if re.search(r"can'?t afford", text, re.I):
        return 'failed', "can't afford it"
    if re.search(r'already have that upgrade maxed', text, re.I):
        return 'failed', 'already maxed'
    if re.search(r"don'?t own that rod", text, re.I):
        return 'failed', 'the rod is not owned'
    if re.search(r'Invalid biome name', text, re.I):
        return 'failed', 'invalid biome'
    name = action.name.casefold()
    if action.act == 'buy':
        # Bait (user, 2026-10-14): "Bait purchase / You bought 200 artifact magnet :bait_artifact: for $15,000."
        batch = re.search(r'You bought ([\d,]+) (.+?)\s+for \$([\d,]+)', text, re.I)
        if batch:
            name = re.sub(r'\s*:\w+:\s*$|\s+\S$', '', batch[2]).strip()
            return 'done', f'bought {batch[1]} {name} for ${batch[3]}'
        bought = re.search(r'You just bought (.+?)(?: level (\d+))?[.!]', text, re.I)
        if not bought:
            return 'unclear', 'no purchase reply'
        if action.level and bought[2] and int(bought[2]) != action.level:
            return 'unclear', f'bought level {bought[2]}, expected {action.level}'
        return 'done', f'bought {bought[1]}' + (f' level {bought[2]}' if bought[2] else '')
    if action.act == 'sell':
        sold = re.search(r'You sold your entire inventory for \$([\d,]+)', text, re.I)
        return ('done', f'sold for ${sold[1]}') if sold else ('unclear', 'no sale reply')
    if action.act == 'rod':
        switched = re.search(r'You switched to the (.+? Rod)', text, re.I)
        if switched:
            return ('done', f'using {switched[1]}') if switched[1].casefold().endswith(name) else (
                'failed', f'switched to {switched[1]}')
    if action.act == 'biome':
        moved = re.search(r'You are now fishing in the (.+?) biome', text, re.I)
        if moved:
            return ('done', f'fishing in {moved[1]}') if moved[1].casefold().endswith(name) else (
                'failed', f'moved to {moved[1]}')
    if action.act == 'bait':
        if not name and re.search(r'no longer using any bait', text, re.I):
            return 'done', 'no bait'
        using = re.search(r'You are now using (.+?) \(', text, re.I)
        if using:
            return ('done', f'using {using[1]}') if name and using[1].casefold().endswith(name) else (
                'failed', f'using {using[1]}')
    if action.act == 'pet':
        chosen = re.search(r'active pet to your level (\d+) (\w+)', text, re.I)
        if chosen:
            return ('done', f'pet {chosen[2]}') if chosen[2].casefold() == name else ('failed', f'pet {chosen[2]}')
        if re.search(r'Pets are permanent|Current pet:', text, re.I):
            return 'failed', 'no pet was selected'
    return 'unclear', 'reply not recognised'


def reads_after(action):
    """The reads to redo once the action has a reply, whatever it said."""
    if action.act == 'buy':
        return 'refresh_profile', SHOP_READ[action.shop]
    if action.act == 'sell':
        return ('refresh_profile',)
    return 'refresh_profile', 'refresh_buffs', SWITCH_READ[action.act]


def _values(sections, kind):
    return ((sections or {}).get(kind) or {}).get('values') or {}


def check(action, sections, profile):
    """Why the action should be skipped now, from the latest reads; None when it should run. Anything uncertain
    is left to the game's reply."""
    if action.act == 'buy':
        shop = {'upgrades': 'shop_upgrades', 'special': 'shop_special', 'league': 'shop_league'}.get(action.shop)
        level = ((_values(sections, shop).get('items') or {}).get(action.name) or {}).get('level') if shop else None
        if action.level and level is not None and level >= action.level:
            return f'already level {level}'
        hooks = _values(sections, 'shop_league').get('hooks')
        if action.shop == 'league' and action.price and hooks is not None and hooks < action.price:
            return f'not enough Hooks ({hooks:,} of {action.price:,.0f})'
        balance = (profile or {}).get('balance')
        if action.shop in MONEY_SHOPS and action.price and balance is not None and balance < action.price:
            return f'not enough money (${balance:,.0f} of ${action.price:,.0f})'
        return None
    if action.act in SWITCH_READ:
        # Only a switch to what is already in use is skipped. Whether a rod, biome, bait or pet is owned is left to
        # the game's reply: live reads miss lines ('/rod' once listed no Alloy Rod), and a wrong switch only fails.
        # /profile names the setup in use and is read most often; the command's own read stands in for it.
        current = ((profile or {}).get(action.act) or _values(sections, action.act).get('current') or '').casefold()
        wanted = action.name.casefold()
        if current and wanted and (current == wanted or wanted.removesuffix(' rod') == current.removesuffix(' rod')):
            return f'already using {action.name}'
    return None
