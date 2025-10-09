from web_scraper import process_urls, process_and_parse_commands, process_all_pages
import os, json, math, re

# soups 0 1 16 18 20 21 31 36 38
def parse_all_baits(soups_baits):  # this parses all 8 types of baits as their Wiki pages are similar
    # this ALSO parses the individual 'Bait' that will be a superclass eventually
    def norm_effects(effects):  # this is used to normalize bait features for better json
        out = {
            "extra_fish": 0,
            "fish_catch_percent": 0,
            "fish_quality_percent": 0,
            "treasure_chance_percent": 0,
            "treasure_quality_percent": 0,
            "xp_percent": 0,
            "pet_catch_chance_percent": 0,
            "pet_effectiveness_percent": 0,
            "pet_xp_percent": 0,
        }
        for s in effects:
            s = s.lower().replace(":", "").strip()
            # % values
            if "%" in s:
                n = int(s.split("%")[0].replace("+", ""))
                if "fish catch" in s:
                    out["fish_catch_percent"] += n
                elif "fish quality" in s:
                    out["fish_quality_percent"] += n
                elif "treasure chance" in s:
                    out["treasure_chance_percent"] += n
                elif "treasure quality" in s:
                    out["treasure_quality_percent"] += n
                elif "pet catch chance" in s:
                    out["pet_catch_chance_percent"] += n
                elif "pet effectiveness" in s:
                    out["pet_effectiveness_percent"] += n
                elif "pet xp" in s:
                    out["pet_xp_percent"] += n
                elif "xp" in s:
                    out["xp_percent"] += n
            # extra fish (no %)
            elif "extra fish" in s:
                out["extra_fish"] += int(s.split()[0].replace("+", ""))
        return out

    file_names = ["artifact_magnet", "fish_(bait)", "leeches", "magic_bait",
                  "magnet", "support_bait", "wise_bait", "worms"]
    soups_baits = [soups_baits[i] for i in [0, 16, 18, 20, 21, 31, 36, 38]]
    bait_cost = [4, 25, 25, 35, 70, 75, 250, 500]
    num_to_drop = [1, 2, 1, 3, 1, 1, 2, 1]

    for i, soup in enumerate(soups_baits):  # creating each bait's json
        paragraphs = []
        if i == 5:
            paragraphs = soup.select("p")[num_to_drop[i]:4]
        else:
            paragraphs = soup.select("p")[num_to_drop[i]:]
        para_text = []
        for paragraph in paragraphs:
            para = paragraph.get_text(strip=True)
            if i == 5:
                para = para[-4:] + " " + para[:-6]
            para_text.append(para)
        para_text = {**{"cost": bait_cost[i]}, **norm_effects(para_text)}   # dict unpacking magic woah
        if not os.path.exists("src/data/json/bait"): os.makedirs("src/data/json/bait")
        with open("src/data/json/bait/" + file_names[i] + ".json", "w") as f:
            json.dump({f"{file_names[i]}" :para_text}, f, ensure_ascii=False, indent=2)

    # creating the overall bait json
    bait = {"limit": 1000000, "consumed_on_use": 1, "not_consumed_chance": 5, "not_consumed_chance_limit":45}
    with open("src/data/json/bait/bait.json", "w") as f:
        json.dump({"bait": bait}, f, ensure_ascii=False, indent=2)
# soup 15
def parse_fish(soups_fish): # this parses information about all fish types
    soup = soups_fish[15]
    table_headers = ["Type", "River", "Volcanic", "Ocean", "Sky", "Space", "Alien", "Base XP", "Base Sell Price"]
    fish = {}
    for i, tr in enumerate(soup.select("tr")):
        if i == 0: continue     # skip first entry, is just table headers
        row, row_dict = [], {}
        current_fish = ""
        for td in tr.select("td"):
            if td.get_text(strip=True) == "": row.append(0)
            elif td.get_text(strip=True) == "+": row.append(1)
            else: row.append(td.get_text(strip=True))
        for j, r in enumerate(row):
            if j == 0: current_fish = r
            if j in [7, 8]: row_dict[table_headers[j]] = int(r.replace(",", ""))
            else: row_dict[table_headers[j]] = r
        fish[current_fish] = row_dict
    with open("src/data/json/fish.json", "w") as f:
        json.dump({"fish": fish}, f, ensure_ascii=False, indent=2)
# soup 19
def parse_level(): # this parses all the information related to leveling (maybe complicated)
    # parsing isn't necessary here as I wanted to expand the information present in the data fully
    def xp_for_level(x):
        if 1 <= x <= 5: return 100
        elif 6 <= x <= 13: return 150
        elif 14 <= x <= 17: return 200
        elif 18 <= x <= 31: return 250
        elif 32 <= x <= 39: return 500
        elif 40 <= x <= 59: return 1000
        elif 60 <= x <= 79: return 1500
        elif 80 <= x <= 99: return 2000
        elif 100 <= x <= 499: return math.ceil((x - 94) / 5) * 2500
        elif x == 500: return 900_000
        elif 501 <= x <= 1115: return 500_000
        elif 1116 <= x <= 1266: return 1_000_000
        elif 1267 <= x <= 1408: return 1_750_000
        elif 1409 <= x <= 4999: return 2_500_000
        elif x == 5000: return 273_750_000
        elif 5001 <= x <= 6000: return 10_000_000
        elif 6001 <= x <= 6500: return 20_000_000
        elif 6501 <= x <= 6700: return 50_000_000
        elif 6701 <= x <= 9999: return 100_000_000
        elif x == 10000: return 9223372036854775807
        else: return 0

    def money_award_for_level(x):
        if 2 <= x <= 14: return 50
        elif 15 <= x <= 29: return 100
        elif 30 <= x <= 39: return 200
        elif 40 <= x <= 49: return 400
        elif 50 <= x <= 99: return 1600
        elif 100 <= x <= 149: return 6400
        elif 150 <= x <= 199: return 25600
        elif 200 <= x <= 249: return 102400
        elif x >= 250: return 204800
        else: return 0

    def build_levels(max_level=10000):
        levels = {}
        for lvl in range(1, max_level + 1):
            levels[lvl] = {
                "xp_required": xp_for_level(lvl),
                "money_awarded": money_award_for_level(lvl),
            }
        return {"Levels": levels}

    data = build_levels(10000)
    with open("src/data/json/level.json", "w") as f:
        json.dump(data, f, separators=(",", ":"), ensure_ascii=False, indent=2)
# soup 23
def parse_pet(soups_pet):
    soup = soups_pet[23]
    pet_data = {}   # overarching dict for all pet data
    def pct_to_float(s):
        s = s.strip()
        if not s or s == "0": return 0.0
        return float(s.replace("%", "").replace("+", ""))
    pets = {}
    for tr in soup.select("tbody")[0].select("tr"):
        if tr.find("th"): continue
        cells = [td.get_text(strip=True) for td in tr.select("td")]
        if not cells or len(cells) < 2: continue
        name, desc = cells[0], cells[1]
        row = {"description": desc, "fish_catch_pct": pct_to_float(cells[2]) if len(cells) > 2 else 0.0,
               "fish_quality_pct": pct_to_float(cells[3]) if len(cells) > 3 else 0.0,
               "treasure_quality_pct": pct_to_float(cells[4]) if len(cells) > 4 else 0.0,
               "treasure_chance_pct": pct_to_float(cells[5]) if len(cells) > 5 else 0.0,
               "xp_pct": pct_to_float(cells[6]) if len(cells) > 6 else 0.0}
        pets[name] = row
    pet_data["pets"] = pets
    pet_buffs = soup.select("tbody")[1].select("tr")
    pb_headers, pb_vals, pb = [], [], {}
    for i, th in enumerate(pet_buffs[0]):
        pb_headers.append(th.get_text(strip=True))
    for th in pet_buffs[1]:
        pb_vals.append(th.get_text(strip=True))
    pb_headers, pb_vals = [p for p in pb_headers if p != ""][1:], [p for p in pb_vals if p != ""][1:]
    for i, x in enumerate(pb_headers):
        pb[pb_headers[i]] = pb_vals[i]
    pet_data["pet_buffs"] = pb          # adding pet_buffs (pb) to overall json
    pet_required_xp = soup.select("p")[-2:-1]
    pet_required_xp = re.sub(r" \(\d+\)", "", pet_required_xp[0].get_text(strip=True)[40:]).split(",")
    pet_required_xp = [int(p) for p in pet_required_xp]
    xp_dict = {1: 0}
    for i, xp in enumerate(pet_required_xp):
        xp_dict[i + 2] = xp
    pet_data["xp_required"] = xp_dict   # adding xp_dict to overall json
    with open("src/data/json/pet.json", "w") as f:
        json.dump(pet_data, f, ensure_ascii=False, indent=2)
# soup 2
def parse_biome(soups_biome):   # this parses biome related information
    soup = soups_biome[2]
    biomes = ["River", "Volcanic", "Ocean", "Sky", "Space", "Alien"]
    uls = soup.select(".page__main")[0].select("ul")[2:-1]
    def split_items(s):
        s = s.replace(" and ", ", ")
        return [x.strip() for x in s.split(",") if x.strip()]
    def strip_parens(s):
        return re.sub(r"\([^)]*\)", "", s).strip()
    biome = {}
    for i, ul in enumerate(uls):
        li = [x.get_text(" ", strip=True) for x in ul.select("li")]
        lvl = 0 if "Always available" in li[0] else int(re.search(r"\d+", li[0]).group())
        code = li[1].split()[-1]
        cd = 0.0 if "Base cooldown" in li[2] else float(li[2].split("s")[0])
        rods = split_items(li[3].replace("Used with", "").replace("rods", "").strip())
        can = [strip_parens(x).replace(" fish", "").strip() for x in
               split_items(li[4].replace("Can catch", "").strip())]
        if li[5].startswith("No treasure restrictions"): no_treasure_restrictions, cannot = True, []
        else: no_treasure_restrictions, cannot = False, [strip_parens(x).replace(" fish", "").strip() for x
                                                         in split_items(li[5].replace("Cannot catch", "")
                                                                        .replace("lava or","lava,").strip())]
        mult = float(li[6].split("x")[0])
        biome[biomes[i]] = {"level_req": lvl, "code": code, "cooldown_s": cd, "rods": rods, "can_catch": can,
                          "cannot_catch": cannot, "no_treasure_restrictions": no_treasure_restrictions,
                          "fish_mult": mult}
    with open("src/data/json/biome.json", "w") as f:
        json.dump({"biome": biome}, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    urls = ["https://virtualfisher.com/commands", "https://virtual-fisher.fandom.com/wiki/Special:AllPages"]
    soups_v1 = process_urls(urls, 0)
    print("Commands processed!") if process_and_parse_commands(soups_v1) else print("Commands not processed!")
    soups_v2 = process_all_pages(soups_v1)
    print("All pages processed!")
    parse_all_baits(soups_v2)           # done
    print("All baits parsed!")
    parse_fish(soups_v2)
    print("All fish parsed!")
    parse_level()
    print("All levels parsed!")
    parse_pet(soups_v2)
    print("All pets parsed!")
    parse_biome(soups_v2)
    print("All biomes parsed!")

    # todo from here
    # - separate out the pages that i actually want (somehow) todo DONE
    # - process data on pages in a generic way when possible (will need special cases) todo WIP
    # - export all into json in a new folder todo WIP
    #   todo next ones to parse: boats (3), daily (12), quests (26), rods (27), prestige (24),
    #   todo prestige shop (25), upgrades (33), boosts (5), special (30), clan (9)
    # #'s processed - 0 1 2 15 16 18 19 20 21 23 31 36 38