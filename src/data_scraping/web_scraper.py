import requests
import json
import re
from bs4 import BeautifulSoup
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit
import argparse

def process_urls(links: list, path: int, *, refresh=False) -> list:
    """Load each page independently; refresh bypasses existing cache files."""
    if path not in (0, 1):
        raise ValueError("path must be 0 (index pages) or 1 (wiki pages)")
    if path == 0 and len(links) != 2:
        raise ValueError("Expected the commands URL followed by the wiki index URL")
    soups = []
    for i, url in enumerate(links):
        if path == 0:
            cache = Path("src/data/html") / ("commands.html", "all_pages.html")[i]
        else:
            slug = urlsplit(url).path.removeprefix("/wiki/")
            filename = quote(unquote(slug).lower(), safe="_()-") + ".html"
            cache = Path("src/data/html/pages") / filename
        if cache.is_file() and cache.stat().st_size and not refresh:
            html = cache.read_text(encoding="utf-8")
        else:
            response = requests.get(url, timeout=30)
            response.raise_for_status()
            html = response.text
            if not html.strip():
                raise ValueError(f"Empty HTML response from {url}")
            cache.parent.mkdir(parents=True, exist_ok=True)
            temporary = cache.with_suffix(".html.tmp")
            temporary.write_text(html, encoding="utf-8")
            temporary.replace(cache)
            print(f"Downloaded {url}")
        soups.append(BeautifulSoup(html, "lxml"))
    return soups

def process_and_parse_commands(soups_cmds: list) -> bool:
    """Parse command lines without treating inline references as new commands."""
    soup = soups_cmds[0]
    categories = {}
    for title in soup.select(".subtitle"):
        container = title.parent
        if container.find("strong", recursive=False) is None:
            continue
        # A line ends at <br>; nested <strong> tags may reference another command.
        fragment = BeautifulSoup(str(container), "lxml")
        for heading in fragment.select(".subtitle"):
            heading.decompose()
        for br in fragment.select("br"):
            br.replace_with("\n")
        commands = []
        for line in fragment.get_text().splitlines():
            match = re.match(r"^\s*(/\S+)\s+[-–—]\s*(.+)$", line)
            if match:
                commands.append({"cmd": match[1], "description": match[2].strip()})
        if commands:
            name = re.sub(r"[^a-z0-9_-]+", "_", title.get_text(strip=True).lower()).strip("_")
            categories[name] = {"commands": commands}
    if not categories:
        raise ValueError("No command categories found; the command page layout may have changed")
    categories["all_commands"] = {"commands": [command for category in categories.values()
                                                for command in category["commands"]]}
    output = Path("src/data/commands")
    output.mkdir(parents=True, exist_ok=True)
    for name, data in categories.items():
        destination = output / (name + ".json")
        temporary = destination.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(destination)
    return True

def process_all_pages(soups_all: list, *, refresh=False) -> dict: # processing all_pages.html
    soup = soups_all[1]
    lst = soup.select_one(".mw-allpages-chunk")
    if lst is None:
        raise ValueError("Missing wiki page index (.mw-allpages-chunk)")
    page_names = []
    page_urls = []
    useful_urls = ["Artifact_Magnet", "Bait", "Biome", "Boats", "Booster", "Boosts", "Buff", "Charms", "Chest",
                   "Clan", "Color", "Commands", "Daily", "Event", "Exotic_Fish", "Fish", "Fish_(Bait)", "Leaderboard",
                   "Leeches", "Level", "Magic_Bait", "Magnet", "Money", "Pet", "Prestige", "Prestige_Shop", "Quests",
                   "Rods", "Settings", "Shop", "Special", "Support_Bait", "Tip", "Upgrades", "Verify", "Vote",
                   "Wise_Bait", "Worker", "Worms"]
    for element in soup.select(".mw-allpages-chunk li a"):
        if element.get_text(strip=True).replace(" ", "_") in useful_urls:   # lil redundant but it does its job
            name = element.get_text(strip=True).replace(" ", "_")
            if name in page_names:
                continue
            page_names.append(name)
            page_urls.append("https://virtual-fisher.fandom.com/wiki/" + element.get_text(strip=True).replace(" ", "_"))
    if not page_urls:
        raise ValueError("No supported game pages found in the wiki index")
    soups_v2 = process_urls(page_urls, 1, refresh=refresh)
    return dict(zip(page_names, soups_v2))

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Collect Virtual Fisher commands and wiki pages")
    parser.add_argument("--refresh", action="store_true", help="Download fresh HTML instead of using cached pages")
    args = parser.parse_args()
    urls = ["https://virtualfisher.com/commands", "https://virtual-fisher.fandom.com/wiki/Special:AllPages"]
    soups_v1 = process_urls(urls, 0, refresh=args.refresh)
    process_and_parse_commands(soups_v1)
    pages = process_all_pages(soups_v1, refresh=args.refresh)
    print(f"Commands exported; {len(pages)} wiki pages collected.")
