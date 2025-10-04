# these are for later when I move the main from web_scraper into here to do it all at once
from importlib.metadata import files

from bs4 import BeautifulSoup

from web_scraper import process_urls, process_and_parse_commands, process_all_pages
import os, json


def parse_all_baits(soups_baits):  # this parses all 8 types of baits as their Wiki pages are similar
    # this ALSO parses the individual 'Bait' that will be a superclass eventually
    # all bait soups are numbers 0, 16, 18, 20, 21, 31, 36, 38
    # individual bait soup # doesn't matter, but it is 1

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
        if not os.path.exists("src/data/pages/bait"):
            os.makedirs("src/data/pages/bait")
        with open("src/data/pages/bait/" + file_names[i] + ".json", "w") as f:
            json.dump({f"{file_names[i]}" :para_text}, f, ensure_ascii=False, indent=2)

    # creating the overall bait json
    bait = {"limit": 1000000, "consumed_on_use": 1, "not_consumed_chance": 5, "not_consumed_chance_limit":45}
    with open("src/data/pages/bait/bait.json", "w") as f:
        json.dump({"bait": bait}, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    urls = ["https://virtualfisher.com/commands", "https://virtual-fisher.fandom.com/wiki/Special:AllPages"]
    soups_v1 = process_urls(urls, 0)
    print("Commands processed!") if process_and_parse_commands(soups_v1) else print("Commands not processed!")
    soups_v2 = process_all_pages(soups_v1)
    print("All pages processed!")
    parse_all_baits(soups_v2)           # done
    print("All baits parsed!")






    # todo from here
    # - separate out the pages that i actually want (somehow) todo DONE
    # - process data on pages in a generic way when possible (will need special cases)
    # - export all into json in a new folder
    #
    # #'s processed - 0 1 16 18 20 21 31 36 38