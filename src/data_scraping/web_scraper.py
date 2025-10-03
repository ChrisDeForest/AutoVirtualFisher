import requests, os, json
from bs4 import BeautifulSoup

def process_urls(links: list, path: int) -> list:   # downloading or loading html files
    soup_objs, responses = [], []
    if not os.path.exists('src/data/html/commands.html') and not os.path.exists('src/data/html/all_pages.html'):
        print("Files not found. Downloading...")
        for url in links:
            response = requests.get(url, timeout=30)
            link_soup = BeautifulSoup(response.text, "lxml")
            responses.append(response)
            soup_objs.append(link_soup)
        print("Downloaded!")
        if len(responses) == 2:
            if not os.path.exists("src/data/html"):
                os.makedirs("src/data/html")
            with open("src/data/html/commands.html", "w", encoding="utf-8") as f:
                f.write(responses[0].text)
            with open("src/data/html/all_pages.html", "w", encoding="utf-8") as f:
                f.write(responses[1].text)
        print("Saved!")
    else:
        if path == 0:
            with open("src/data/html/commands.html", "r", encoding="utf-8") as f:
                soup_objs.append(BeautifulSoup(f.read(), "lxml"))
            with open("src/data/html/all_pages.html", "r", encoding="utf-8") as f:
                soup_objs.append(BeautifulSoup(f.read(), "lxml"))
    if path == 1:
        if not os.path.exists("src/data/html/pages"):
            os.makedirs("src/data/html/pages")
            for url in links:
                response = requests.get(url, timeout=30)
                link_soup = BeautifulSoup(response.text, "lxml")
                responses.append(response)
                soup_objs.append(link_soup)
            if len(responses) == 60:
                for i, response in enumerate(responses):
                    with open(f"src/data/html/pages/{links[i].replace("https://virtual-fisher.fandom.com/wiki/", "").lower()}.html", "w", encoding="utf-8") as f:
                        f.write(response.text)
        else:
            for i in range(len(links)):
                with open(f"src/data/html/pages/{links[i].replace("https://virtual-fisher.fandom.com/wiki/", "").lower()}.html", "r", encoding="utf-8") as f:
                    soup_objs.append(BeautifulSoup(f.read(), "lxml"))
    return soup_objs

def process_commands(soups_cmds: list) -> bool: # processing commands.html
    soup = soups_cmds[0]
    subtitles = []
    all_commands = []
    for i, lst in enumerate(soup.select(".command_list")):
        subtitle = lst.select_one(".subtitle").get_text(strip=True).lower().replace(" ", "_")
        subtitles.append(subtitle)
        cmds = []
        for strong in lst.select("strong"):
            cmd = strong.get_text(strip=True)
            if cmd == "/buy": # lil exception for an extra strong tag
                cmds.append(("/buy", "Directly buy upgrades from the shop."))
                continue
            if cmd == "/shop":  # had to do in this order to fix accidental extra cmds
                cmds.append(("/shop", "Shop for upgrades to buy using the /buy command."))
                continue
            parts = []
            for sib in strong.next_siblings:
                if getattr(sib, "name", None) == "br":
                    break
                parts.append(sib.get_text(strip=True) if hasattr(sib, "get_text") else str(sib))
            desc = "".join(parts).lstrip(" -–—").strip()
            cmds.append((cmd, desc))
        # print("Subtitle", i, "is", subtitles[i])  # just some helpful debug print statements
        # print(cmds)
        all_commands.append(cmds)
        data = {"commands": []} # processing each individual command list
        for cmd, desc in cmds:
            data["commands"].append({"cmd": cmd, "description": desc})
        if not os.path.exists("src/data/commands"):
            os.makedirs("src/data/commands")
        with open(f"src/data/commands/{subtitles[i]}.json", "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    data = {"commands": []}     # combining every command list
    for cmd_group in all_commands:
        for cmd, desc in cmd_group:
            data["commands"].append({"cmd": cmd, "description": desc})
    with open("src/data/commands/all_commands.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return True # function succeeded

def process_all_pages(soups_all: list) -> bool: # processing all_pages.html
    soup = soups_all[1]
    lst = soup.select_one(".mw-allpages-chunk")
    page_urls = []
    for element in lst.select("li a"):
        page_urls.append("https://virtual-fisher.fandom.com/wiki/" + element.get_text(strip=True).replace(" ", "_"))
    soups_v2 = process_urls(page_urls, 1)
    # todo from here
    # - separate out the pages that i actually want (somehow)
    # - process data on pages in a generic way when possible (will need special cases)
    # - export all into json in a new folder

    return False

if __name__ == "__main__":
    urls = ["https://virtualfisher.com/commands", "https://virtual-fisher.fandom.com/wiki/Special:AllPages"]
    soups_v1 = process_urls(urls, 0)
    # print("Commands processed!") if process_commands(soups_v1) else print("Commands not processed!")
    # print("All pages processed!") if process_all_pages(soups_v1) else print("All pages not processed!")
    process_all_pages(soups_v1)