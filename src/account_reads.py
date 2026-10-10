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


ACCOUNT_READS = {
    'refresh_upgrades': Read('/shop upgrades', 'shop_upgrades', 2),
    'refresh_shop_rods': Read('/shop rods', 'shop_rods', 2),
    'refresh_rod': Read('/rod', 'rod'),
    'refresh_bait': Read('/bait', 'bait'),
    'refresh_biome': Read('/biome', 'biome'),
    'refresh_boats': Read('/shop boats', 'shop_boats'),
    'refresh_pets': Read('/pet', 'pet'),
    'refresh_prestige_shop': Read('/prestige shop', 'prestige_shop'),
    'refresh_league': Read('/shop league', 'shop_league', 2),
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
