# Player response fixtures

`live_profile.txt` and `live_buffs.txt` are manual text transcriptions of the
user's October 5, 2026 Discord screenshots. Emoji are representative rather
than exact custom Discord emoji encodings. The fixtures contain the response
body, not Discord's timestamps, buttons, or message header.

The profile's 270,862/477,500 means current/required XP, leaving 206,638 XP.
Its missing pet field must remain unknown; `/pet` is a separate command.
Buffs include the selected bait and represent this account at that moment,
not base game constants or isolated bait bonuses.

`live_fishing.txt` is text the user copied from Discord on October 5, 2026: six
fishing results (each starting with the author line `epicmouse9925`), two quest
completions, and one cooldown reply, in the order they were pasted. Emoji appear
as Discord `:name:` codes. The author name differs from the profile heading
(`epicmouse`), so fishing output cannot identify an account on its own.

`live_quests.txt` is the user's `/quests` response copied from Discord on
October 5, 2026, with three daily quests, one special daily quest, and the reset
countdown.

`live_discord_copy.txt` is a multi-message range the user copied from Discord on
October 6, 2026, including Discord's message headers (`Virtual Fisher` / `APP` /
` — <time>`), one of the user's own messages, unrelated bot replies, repeated
`/profile` and `/buffs` responses, 26 fishing results, and `/quests`. Other
players' names in "Global boost by" lines are replaced with placeholders.

`ocr_*.txt` are the lines Windows OCR (`src/screenshot_ocr.py`, 1x scale)
returned on October 6, 2026 for the user's screenshots `2x_fish_msgs.png`,
`buff_msg.png`, `pet_msg.png`, and `profile_msg.png`, kept verbatim with their
misreads (e.g. `Lv130` for `Lvl 30`) except that another player's name in a
"Global boost by" line is replaced with a placeholder. The screenshots
themselves stay in the ignored `docs/screenshots/` folder.
