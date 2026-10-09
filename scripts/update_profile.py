#!/usr/bin/env python3
"""Render a profile card using the owner's avatar and public GitHub data."""

import argparse
import calendar
import io
import json
import os
from pathlib import Path
import subprocess
import time
from datetime import date, datetime
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

from PIL import Image, ImageEnhance, ImageOps

ROOT = Path(__file__).resolve().parents[1]
WIDTH, HEIGHT = 1040, 512
TEXT_X, TEXT_RIGHT = 419, 1018
FONT_SIZE, CELL = 16, 9.6
HEADERS = {"Accept": "application/vnd.github+json", "User-Agent": "EstebanPdN-profile"}


def api(endpoint, payload=None):
    """Use the Actions token, or the local authenticated gh CLI without exporting it."""
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        args = ["gh", "api", endpoint]
        if payload is not None:
            args += ["--input", "-"]
        result = subprocess.run(args, input=json.dumps(payload) if payload else None,
                                text=True, capture_output=True, check=True)
        data = json.loads(result.stdout)
    else:
        data = None
        body = json.dumps(payload).encode() if payload is not None else None
        headers = {**HEADERS, "Authorization": f"Bearer {token}"}
        if body:
            headers["Content-Type"] = "application/json"
        for attempt in range(3):
            try:
                with urlopen(Request(f"https://api.github.com/{endpoint}",
                                     data=body, headers=headers), timeout=60) as response:
                    data = json.load(response)
                break
            except HTTPError as error:
                if error.code < 500 or attempt == 2:
                    raise
                time.sleep(2 ** attempt)
            except URLError:
                if attempt == 2:
                    raise
                time.sleep(2 ** attempt)
    if isinstance(data, dict) and data.get("errors"):
        raise RuntimeError(json.dumps(data["errors"]))
    return data


def graphql(query, **variables):
    return api("graphql", {"query": query, "variables": variables})["data"]


def collect_stats(config, today):
    username = config["username"]
    user = api(f"users/{username}")
    repositories, cursor = [], None
    query = """query($login: String!, $after: String) {
      user(login: $login) {
        id repositories(first: 100, after: $after, privacy: PUBLIC,
                        ownerAffiliations: [OWNER]) {
          pageInfo { hasNextPage endCursor }
          nodes { nameWithOwner stargazerCount isFork
                  defaultBranchRef { name target { oid } } }
        }
      }
    }"""
    while True:
        response = graphql(query, login=username, after=cursor)["user"]
        connection = response["repositories"]
        repositories.extend(connection["nodes"])
        if not connection["pageInfo"]["hasNextPage"]:
            break
        cursor = connection["pageInfo"]["endCursor"]

    # Rebuild totals from the current public default branches, including rebases.
    # Match the account's author ID; never count inherited upstream authors.
    seen, commits, additions, deletions = set(), 0, 0, 0
    excluded = {name.lower() for name in config["exclude_commit_repositories"]}
    history_query = """query($owner: String!, $name: String!, $author: ID!,
                              $after: String, $head: String!) {
      repository(owner: $owner, name: $name) {
        object(expression: $head) { ... on Commit {
          history(first: 100, after: $after, author: {id: $author}) {
            pageInfo { hasNextPage endCursor }
            nodes { oid additions deletions }
          }
        } }
      }
    }"""
    for repo in repositories:
        if repo["nameWithOwner"].lower() in excluded or not repo["defaultBranchRef"]:
            continue
        owner, name = repo["nameWithOwner"].split("/", 1)
        cursor = None
        while True:
            result = graphql(history_query, owner=owner, name=name,
                             author=response["id"], after=cursor,
                             head=repo["defaultBranchRef"]["target"]["oid"])
            history = result["repository"]["object"]["history"]
            for commit in history["nodes"]:
                if commit["oid"] not in seen:
                    seen.add(commit["oid"])
                    commits += 1
                    additions += commit["additions"]
                    deletions += commit["deletions"]
            if not history["pageInfo"]["hasNextPage"]:
                break
            cursor = history["pageInfo"]["endCursor"]
    return {
        "updated_on": today.isoformat(), "avatar_url": user["avatar_url"],
        "repositories": len(repositories),
        "stars": sum(repo["stargazerCount"] for repo in repositories if not repo["isFork"]),
        "followers": user["followers"], "commits": commits,
        "additions": additions, "deletions": deletions,
        "scope": "Owned public repositories; unique authored commits reachable from current default branches; profile repository excluded from commit and line totals. Stars exclude forks. Line totals measure additions/deletions, including non-code text and merge diffs, not the size of the current codebase."
    }


def uptime(birthday, today):
    birth = date.fromisoformat(birthday)
    if today < birth:
        raise ValueError("Birthday cannot be in the future")
    months = (today.year - birth.year) * 12 + today.month - birth.month
    def anchor(count):
        year, month = divmod(birth.year * 12 + birth.month - 1 + count, 12)
        return date(year, month + 1, min(birth.day, calendar.monthrange(year, month + 1)[1]))
    if anchor(months) > today:
        months -= 1
    years, months_in_year = divmod(months, 12)
    days = (today - anchor(months)).days
    def plural(value, unit):
        return f"{value} {unit}{'' if value == 1 else 's'}"
    return ", ".join([plural(years, "year"), plural(months_in_year, "month"), plural(days, "day")])


def make_avatar(url):
    # Render the same avatar as monochrome text, preserving its physical aspect.
    with urlopen(Request(url + "&s=512", headers={"User-Agent": HEADERS["User-Agent"]}),
                 timeout=30) as response:
        avatar = Image.open(io.BytesIO(response.read())).convert("RGB")
    avatar = ImageOps.fit(avatar, (365, 458), centering=(0.5, 0.5)).convert("L")
    avatar = ImageEnhance.Contrast(avatar).enhance(1.15)
    avatar = avatar.resize((46, 35), Image.Resampling.LANCZOS)
    ramp = " .,:;i1tfLCG08@"
    pixels = list(avatar.get_flattened_data())
    return ["".join(ramp[min(len(ramp) - 1, (255 - pixels[y * 46 + x]) * len(ramp) // 256)]
                    for x in range(46)).rstrip() for y in range(35)]


def render(config, stats, avatar, today, dark):
    theme = ({"bg": "#161b22", "text": "#c9d1d9", "key": "#ffa657",
              "value": "#a5d6ff", "muted": "#616e7f", "green": "#3fb950", "red": "#f85149"}
             if dark else
             {"bg": "#f6f8fa", "text": "#24292f", "key": "#953800",
              "value": "#0550ae", "muted": "#8c959f", "green": "#1a7f37", "red": "#cf222e"})
    elements = []
    def text(x, y, value, color="text", size=FONT_SIZE, extra=""):
        elements.append(f'<text x="{x}" y="{y}" fill="{theme[color]}" font-size="{size}" {extra}>{escape(str(value))}</text>')
    def section(y, title):
        text(TEXT_X, y, title)
        start = TEXT_X + (len(title) + 1) * CELL
        elements.append(f'<line x1="{start}" y1="{y - 4}" x2="{TEXT_RIGHT}" y2="{y - 4}" stroke="{theme["muted"]}" stroke-dasharray="4 3"/>')
    def row(y, key, value):
        label = key + ":" if key else ""
        text(TEXT_X, y, label, "key")
        end_label = TEXT_X + (len(label) + 1) * CELL
        start_value = TEXT_RIGHT - len(value) * CELL
        if start_value < end_label:
            raise ValueError(f"Profile row is too long: {key}")
        dots = max(0, int((start_value - end_label) / CELL) - 1)
        text(end_label, y, "." * dots, "muted")
        text(TEXT_RIGHT, y, value, "value", extra='text-anchor="end"')
    for index, line in enumerate(avatar):
        text(22, 32 + index * 13.2, line, size=13.2, extra='xml:space="preserve"')
    section(32, config["username"].lower() + "@github")
    row(56, "OS", config["os"])
    row(78, "Uptime", uptime(config["birthday"], today))
    row(100, "IDE", config["ide"])
    row(146, "Languages.Programming", config["programming"])
    row(168, "Languages.Computer", config["computer"])
    row(190, "Languages.Real", config["languages"])
    row(236, "Hobbies", config["hobbies"][0])
    row(258, "", config["hobbies"][1])
    section(304, "Contact")
    row(328, "Email", config["email"])
    row(350, "Discord", config["discord"])
    row(372, "Website", config["website"])
    section(418, "GitHub Stats")
    text(TEXT_X, 442, "Repos:", "key")
    text(TEXT_X + 65, 442, f'{stats["repositories"]:,}', "value")
    text(TEXT_X + 105, 442, "[public]", "muted")
    text(787, 442, "Stars:", "key")
    text(TEXT_RIGHT, 442, f'{stats["stars"]:,}', "value", extra='text-anchor="end"')
    text(TEXT_X, 464, "Commits:", "key")
    text(TEXT_X + 84, 464, f'{stats["commits"]:,}', "value")
    text(TEXT_X + 135, 464, "[owned public]", "muted")
    text(787, 464, "Followers:", "key")
    text(TEXT_RIGHT, 464, f'{stats["followers"]:,}', "value", extra='text-anchor="end"')
    text(TEXT_X, 486, "Lines changed:", "key")
    text(738, 486, f'+{stats["additions"]:,}', "green", extra='text-anchor="end"')
    text(756, 486, "/", "muted")
    text(862, 486, f'-{stats["deletions"]:,}', "red", extra='text-anchor="end"')
    text(TEXT_RIGHT, 486, stats["updated_on"], "muted", size=11, extra='text-anchor="end"')
    description = f'OS: {config["os"]}. Uptime: {uptime(config["birthday"], today)}. IDE: {config["ide"]}. Programming: {config["programming"]}. Computer languages: {config["computer"]}. Languages: {config["languages"]}. Hobbies: {", ".join(config["hobbies"])}. Email: {config["email"]}. Discord: {config["discord"]}. Website: {config["website"]}. Public repositories: {stats["repositories"]}. Stars: {stats["stars"]}. Authored commits in owned public repositories: {stats["commits"]}. Followers: {stats["followers"]}. Line additions: {stats["additions"]}. Line deletions: {stats["deletions"]}.'
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" viewBox="0 0 {WIDTH} {HEIGHT}" role="img" aria-labelledby="title description">
<title id="title">{escape(config["username"])} — GitHub profile</title>
<desc id="description">{escape(description)}</desc>
<style>text {{ font-family: "SFMono-Regular", Consolas, "Liberation Mono", monospace; white-space: pre; font-variant-ligatures: none; }}</style>
<rect width="{WIDTH}" height="{HEIGHT}" rx="15" fill="{theme["bg"]}"/>
{chr(10).join(elements)}
</svg>
'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--offline", action="store_true", help="Use previously fetched public data and ASCII avatar")
    parser.add_argument("--date", type=date.fromisoformat, help="Override the local calendar date for preview/testing")
    args = parser.parse_args()
    config = json.loads((ROOT / "profile.json").read_text())
    today = args.date or datetime.now(ZoneInfo(config["timezone"])).date()
    if args.offline:
        stats = json.loads((ROOT / "data/stats.json").read_text())
        avatar = [line.rstrip() for line in (ROOT / "assets/avatar.txt").read_text().splitlines()]
    else:
        stats = collect_stats(config, today)
        avatar = make_avatar(stats["avatar_url"])
    # Build everything before replacing the previous known-good output.
    cards = {name: render(config, stats, avatar, today, dark)
             for name, dark in [("profile-dark.svg", True), ("profile-light.svg", False)]}
    for name, svg in cards.items():
        (ROOT / "assets" / name).write_text(svg)
    (ROOT / "data/stats.json").write_text(json.dumps(stats, indent=2) + "\n")
    (ROOT / "assets/avatar.txt").write_text("\n".join(avatar) + "\n")
    print(f'Updated {config["username"]}: {uptime(config["birthday"], today)}; '
          f'{stats["repositories"]} public repos, {stats["stars"]} stars, '
          f'{stats["commits"]} authored commits, {stats["followers"]} followers.')


if __name__ == "__main__":
    main()
