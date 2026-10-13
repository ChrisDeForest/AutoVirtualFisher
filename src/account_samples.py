"""Account replies as Virtual Fisher sent them on 2026-10-09 (P4, level ~32), transcribed from screenshots. The
simulator answers with these, and the parser tests start from them."""

UPGRADES_PAGE_1 = '\n'.join((
    'Upgrades in this category are permanent and can be leveled up multiple times.',
    'Use /buy <upgrade name> to level up your upgrades.',
    'Your balance: $266,418',
    'Better Fish (7/21)', '$25,000', 'Causes slightly better fish to bite.',
    'Salesman (7/20)', '$25,000', 'Sell fish for 5% more.',
    'Bait Efficiency (5/9)', '$20,000', 'Adds a 5% chance to not consume bait per cast.',
    'More Chests (3/11)', '$20,000', 'Increases number of chests found by 5%.',
    'Worker Motivation (1/12)', '$50,000', 'Increases amount of fish caught by workers by 10%.',
    'Page 1/2',
))
UPGRADES_PAGE_2 = '\n'.join((
    'Upgrades in this category are permanent and can be leveled up multiple times.',
    'Use /buy <upgrade name> to level up your upgrades.',
    'Your balance: $266,418',
    'Artifact Specialist (3/7)', '$25,000', 'Improves amount of items found per chest slightly.',
    'Experienced (0/5)', '$50,000', 'Increases XP gain by 10%.',
    'Better Chests (0/5)', '$100,000', 'Improves quality of chests found.',
    'Better Dailies (2/10)', '$25,000', 'Increases items in daily rewards by 10%.',
    'Page 2/2',
))
RODS_PAGE_1 = '\n'.join((
    'Fishing Rod Shop', 'Your balance: $266,418',
    'Supporter Rod', 'Select', 'Rod given as thanks for supporting Virtual Fisher.',
    'Plastic Rod', 'Select', 'Basic starter rod.',
    'Improved Rod', '$500', 'Attracts slightly better fish.',
    'Steel Rod', 'Select', 'Allows you to catch better fish, but slightly less of them.',
    'Fiberglass Rod', 'Selected', 'Catches large amounts of quality fish.',
    'Page 1/2',
))
RODS_PAGE_2 = '\n'.join((
    'Fishing Rod Shop', 'Your balance: $266,418',
    'Heavy Rod', '$100,000', "Doesn't catch as many fish, but gets you far more treasure.",
    'Alloy Rod', '$250,000', 'Catch rare fish at an inconsistent rate.',
    'Lava Rod', '$1,000,000', 'Catches many rare fish consistently.',
    'Magma Rod', '$10,000,000', 'Empowered lava rod to attract even more fish.',
    'More rods unlocked at level 100',
    'Page 2/2',
))
ROD = '\n'.join(('/rod <rod name>', 'Currently using: Fiberglass Rod', 'Supporter Rod', 'Plastic Rod', 'Steel Rod',
                 'Fiberglass Rod'))
BAIT = '\n'.join(('/bait <bait type> to use bait.', '/bait none is valid.', 'Current bait: NONE'))
# The miraheze wiki's /bait screenshot (an account owning every bait); each line starts with the bait's icon.
BAIT_OWNED = '\n'.join(('/bait <bait type> to use bait.', '/bait none is valid.', 'Current bait: Support Bait',
                        'Worms - 68', 'Leeches - 1,006', 'Magnet - 212', 'Wise Bait - 1,479', 'Fish - 909',
                        'Artifact Magnet - 1,727', 'Magic Bait - 1,485', 'Support Bait - 982'))
BIOME = '\n'.join(('Current Biome: River', 'Your unlocked biomes:', 'River', 'Next Biome: Volcanic',
                   'UNLOCKED AT LEVEL 50!'))
BOATS = '\n'.join((
    'All boats are permanent, decrease fishing cooldown and increase fish count slightly.',
    '/buy <boat name> (e.g. /buy rowboat).', 'Your balance: $266,418',
    'Rowboat - OWNED', 'Fishing Boat - $25,000', 'Speedboat - $100,000', 'Pontoon - $250,000',
    'Sailboat - $1,000,000', 'Yacht - $20,000,000', 'Unlock more boats at Level 50.',
))
PETS = '\n'.join((
    'Pets are permanent, they are not lost when you prestige. They increase in effectiveness as you level them up.',
    '+14 pet XP per fishing trip (average)', 'Current pet: Dolphin.',
    'Puffer(Lvl 30)', 'Increases your fish catch.', '1,493/2,600 XP to next level.',
    'Dolphin(Lvl 52)', 'Gives you bonus XP and leads you to more treasure.', '11,331/20,000 XP to next level.',
    'Zebrafish(Lvl 61)', 'Increases fish quality.', '13,875/45,000 XP to next level.',
    'Axolotl(Lvl 60)', 'Increases the quality of treasure you find.', '25,197/40,000 XP to next level.',
    'Tuna(Lvl 14)', 'Buffs every stat slightly. Wicked!', '93/725 XP to next level.',
))
PRESTIGE_SHOP = '\n'.join((
    'Prestige Shop', 'Spend your Azure Fish here. (/prestige buy)', 'You have: 0 .',
    'International Ties - 10% boost to every multiplier. - MAXED',
    'Business Education - 40% sell boost. - MAXED',
    'Fish Whisperer - 25% more fish caught. - MAXED',
    'Ancient One - 35% more experience from all sources - MAXED',
    'UNLOCKED AT PRESTIGE 5: Virtual Fisher - 40% more high quality fish, and a 20% sell price and XP buff',
))
LEAGUE_PAGE_1 = '\n'.join((
    'League Shop',
    'Hooks are obtained through weekly fishing leagues. In addition you can earn them through special quests '
    'each day.',
    'Balance: 11',
    'Pet Helper (0/5)', '50', 'Raises pet max level by 5. Increases low level pet XP gain.',
    'Bait Helper (1/5)', '100', 'Increases bait effectiveness by 10%.',
    'Super Crates (2/5)', '150', 'Find super crates. Upgrades increase your chances of finding a super crate.',
    'You will receive +17 hooks at the end of the week. Continue fishing for a greater reward.',
    'Page 1/2',
))
LEAGUE_PAGE_2 = '\n'.join((
    'League Shop',
    'Hooks are obtained through weekly fishing leagues. In addition you can earn them through special quests '
    'each day.',
    'Balance: 11',
    'Worker Crates (2/5)', '150', 'Allows workers to find crates. Upgrades increase quality and number of crates found.',
    'Fishing Frenzy (0/5)', '50', 'Increases effectiveness of temporary boosts.',
    'Duplicator 2.0 (0/10)', '25', '3% Chance to double fish catch.',
    'You will receive +17 hooks at the end of the week. Continue fishing for a greater reward.',
    'Page 2/2',
))
BOOSTS = '\n'.join(('Active Boosts', 'Global Boost Duration: 3h 3m 30s', 'Current Booster: Colby'))

# /shop special at level 75, as seen on 2026-10-13 (Fish Ovens' price, 50 Lava Fish, sat on a dimmed button: not read).
SPECIAL = '\n'.join((
    'Special Shop', 'Use /buy <upgrade name> to level up your upgrades.', 'Inventory: 49', 'Fish Ovens (2/20)',
    'Cook the fish you sellfor a 5% increase in selling price.', 'More upgrades unlocked at level 100.'))
