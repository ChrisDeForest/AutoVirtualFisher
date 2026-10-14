"""The account reads automation makes: which command, which reply it expects, and how many pages it has.

A two-page read is two intents: the typed command (page 1), then a click on the → button (page 2), named
`<read>_page2`. Page 2 is never typed: NEXT_PAGE marks it for the transport.
"""
from dataclasses import dataclass

NEXT_PAGE = 'next page'


@dataclass(frozen=True)
class Read:
    command: str
    reply: str
    pages: int = 1
    # Page 2 only when page 1 says so ("Page 1/2"); otherwise a read of two pages always has one.
    paged_when_listed: bool = False


ACCOUNT_READS = {
    'refresh_upgrades': Read('/shop upgrades', 'shop_upgrades', 2),
    'refresh_rod': Read('/rod', 'rod'),
    'refresh_bait': Read('/bait', 'bait'),
    'refresh_biome': Read('/biome', 'biome'),
    'refresh_boats': Read('/shop boats', 'shop_boats'),
    'refresh_pets': Read('/pet', 'pet'),
    'refresh_prestige_shop': Read('/prestige shop', 'prestige_shop'),
    'refresh_league': Read('/shop league', 'shop_league', 2),
    # One page below level 100, two from there (seen 2026-10-13, level 100+).
    'refresh_special': Read('/shop special', 'shop_special', 2, paged_when_listed=True),
    'refresh_boosts': Read('/boosts', 'boosts'),
}
# Page-2 intent -> its read.
PAGE_TWO = {f'{kind}_page2': kind for kind, read in ACCOUNT_READS.items() if read.pages == 2}
# Every account intent, reads first.
ALL_READS = tuple(ACCOUNT_READS) + tuple(PAGE_TWO)


def read_of(kind: str) -> str:
    """The read an intent belongs to (a page-2 intent belongs to its read)."""
    return PAGE_TWO.get(kind, kind)


def reply_of(kind: str) -> str:
    """The reply kind an account intent expects."""
    return ACCOUNT_READS[read_of(kind)].reply
