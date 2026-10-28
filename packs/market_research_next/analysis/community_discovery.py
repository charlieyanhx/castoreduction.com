"""Where the customers talk: subreddits found by Reddit's public search, ranked locally.

The ranking rules are adapted from reddit-research-mcp (MIT). Their confidence comes from
embeddings we do not have, so confidence here is token overlap between the query and the
subreddit's name, title and description; the penalties, boosts, cut and tiers are theirs.
The query is built from category and place words only, never from the founder's brief:
a search box on a public site is not where a founder's description should go.
"""
from __future__ import annotations

import os
import re

from . import base
from .competitor_count import category_phrase, location_phrase

KEY = "community_discovery"
LABEL = "Online communities"
#: Reddit closed its public JSON API to anonymous clients (measured in
#: tools/sources/forums.py: 403 with any user-agent), so search needs app-only OAuth:
#: a "script" app's client id and secret exchanged for a bearer token.
TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
URL = "https://oauth.reddit.com/subreddits/search"
NO_CREDENTIALS = ("Reddit's API needs credentials (REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET), "
                  "which are not configured, so communities were not searched.")
USER_AGENT = "castor-research/1.0 (market research; contact via castor-advisory.com)"
KEEP = 10
MAX_TERMS = 4
GENERIC = frozenset({"funny", "pics", "videos", "gifs", "memes", "aww", "news", "science"})
TIERS = (("exact", 0.8), ("semantic", 0.6), ("adjacent", 0.35))
STOP = frozenset("""a an the and or of for to in on at by with from into near our my your their
their this that these those who which where what is are be it its as per via based small large
local new first then launching neighbourhood neighbourhoods neighborhood neighborhoods area areas
people customers customer clients client business businesses owners owner company companies
service services app tool platform product avenue ave street st road rd nw ne sw se north south
east west downtown city county state only mostly initially starting""".split())


# --------------------------------------------------------------------------- terms
def _words(text: str | None) -> list[str]:
    seen: list[str] = []
    for word in re.findall(r"[a-z]+", (text or "").lower().replace("'s", " ")):
        if len(word) > 2 and word not in STOP and word not in seen:
            seen.append(word)
    return seen


def _city(location: str | None) -> list[str]:
    """The town in a location: the part before the region, or after a final "of"/"in"."""
    if not location:
        return []
    parts = [p.strip() for p in location.split(",") if p.strip()]
    if len(parts) >= 3:
        place = parts[-2]
    elif len(parts) == 2:
        place = parts[0]
    else:
        place = re.split(r"\s(?:of|in)\s", parts[0] if parts else "")[-1]
    return _words(place)[:2]


def build_queries(context: dict) -> list[str]:
    """Up to two queries: the category's words, then the category with the town.

    The target customer's words stand in only when the product names no category: that
    attribute is prose, and its words dilute a search the category already makes.
    """
    words = _words(category_phrase(context))
    if not words:
        target = base.attribute(context, "target_customer").get("value")
        words = _words(target if isinstance(target, str) else None)
    words = words[:MAX_TERMS]
    if not words:
        return []
    queries = [" ".join(words)]
    city = [w for w in _city(location_phrase(context)) if w not in words]
    if city:
        queries.append(" ".join(words[:MAX_TERMS - len(city)] + city))
    return queries


# --------------------------------------------------------------------------- ranking
def confidence(terms: list[str], name: str, title: str, description: str,
               subscribers: int) -> float:
    """Token overlap, then reddit-research-mcp's generic penalty and size adjustments."""
    if not terms:
        return 0.0
    tokens = set(re.findall(r"[a-z0-9]+", f"{title} {description}".lower()))
    lowered = name.lower()
    hits = sum(1 for t in terms if t in tokens or (len(t) >= 3 and t in lowered))
    score = hits / len(terms)
    if lowered in GENERIC and lowered not in terms:
        score *= 0.3
    if subscribers >= 1_000_000:
        score = min(1.0, score * 1.1)
    elif subscribers < 10_000:
        score *= 0.9
    return round(score, 3)


def tier(score: float) -> str:
    return next((name for name, floor in TIERS if score >= floor), "peripheral")


def quality(subreddits: list[dict]) -> str:
    if sum(1 for s in subreddits if s["confidence"] > 0.7) >= 5:
        return "good"
    if sum(1 for s in subreddits if s["confidence"] >= 0.5) >= 3:
        return "moderate"
    return "poor"


def rank(results: dict[str, list[dict]]) -> list[dict]:
    """Merge each query's children by name, keep the best score, sort and cut to ten."""
    best: dict[str, dict] = {}
    for query, children in results.items():
        terms = query.split()
        for child in children:
            sub = child.get("data") if isinstance(child, dict) else None
            if not isinstance(sub, dict) or sub.get("over18") is True:
                continue
            name = sub.get("display_name")
            if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_]{2,32}", name):
                continue
            subs = base.finite_number(sub.get("subscribers"))
            subscribers = int(subs) if subs is not None and subs > 0 else 0
            title = sub.get("title") if isinstance(sub.get("title"), str) else ""
            desc = sub.get("public_description")
            score = confidence(terms, name, title, desc if isinstance(desc, str) else "",
                               subscribers)
            held = best.get(name.lower())
            if held is None or score > held["confidence"]:
                best[name.lower()] = {"name": name, "subscribers": subscribers,
                                      "confidence": score, "match_tier": tier(score),
                                      "url": f"https://www.reddit.com/r/{name}/",
                                      "title": title.strip()}
    ordered = sorted(best.values(), key=lambda s: (-s["confidence"], -s["subscribers"]))
    return ordered[:KEEP]


# --------------------------------------------------------------------------- produce
def produce(context: dict, *, model=None, session=None, env=None, now=None) -> dict:
    """Search, rank and report; a failed search is partial because customer voice goes on."""
    env = os.environ if env is None else env
    queries = build_queries(context)
    results: dict[str, list[dict]] = {}
    failures: list[str] = []
    client = ((env.get("REDDIT_CLIENT_ID") or "").strip(),
              (env.get("REDDIT_CLIENT_SECRET") or "").strip())
    token = None
    if queries and not all(client):
        failures.append(NO_CREDENTIALS)
        queries_to_run: list[str] = []
    else:
        queries_to_run = queries
    if queries_to_run:
        try:
            grant = base.http_json("POST", TOKEN_URL, session=session,
                                   headers={"User-Agent": USER_AGENT},
                                   form={"grant_type": "client_credentials"}, auth=client,
                                   timeout=10.0)
            token = grant.get("access_token") if isinstance(grant, dict) else None
            if not isinstance(token, str) or not token:
                raise base.AnalysisError("Reddit returned no access token")
        except base.AnalysisError as exc:
            failures.append(f"Reddit sign-in failed: {exc}.")
            queries_to_run = []
    for query in queries_to_run:
        try:
            payload = base.http_json(
                "GET", URL, session=session,
                headers={"User-Agent": USER_AGENT, "Authorization": f"bearer {token}"},
                params={"q": query, "limit": 25, "include_over_18": "false"}, timeout=10.0)
        except base.AnalysisError as exc:
            failures.append(f'Reddit search for "{query}" failed: {exc}.')
            continue
        children = ((payload.get("data") or {}).get("children")
                    if isinstance(payload, dict) and isinstance(payload.get("data"), dict) else None)
        results[query] = [c for c in children if isinstance(c, dict)] if isinstance(
            children, list) else []
    subreddits = rank(results)
    data = {"queries": queries, "subreddits": subreddits,
            "tier_distribution": {name: sum(1 for s in subreddits if s["match_tier"] == name)
                                  for name in ("exact", "semantic", "adjacent", "peripheral")},
            "quality": quality(subreddits)}
    uncertainties = list(failures)
    if not queries:
        uncertainties.append("The venture's category is not known, so Reddit was not searched.")
    elif not subreddits and not failures:
        uncertainties.append("No subreddit matched the category words.")
    if subreddits and data["quality"] == "poor":
        uncertainties.append("Few subreddits match the category closely; treat these as leads.")
    status = "partial" if failures or not subreddits else "completed"
    return base.envelope(
        LABEL, _narrative(data, failures), data=data, status=status,
        findings=[f'r/{s["name"]}: {s["subscribers"]:,} members, {s["match_tier"]} match'
                  for s in subreddits[:5]],
        assumptions=["Confidence is word overlap between the search and each subreddit's name, "
                     "title and description, not a measure of how active buyers are there."],
        uncertainties=uncertainties,
        metrics={"subreddits": [s["name"] for s in subreddits], "quality": data["quality"]})


def _narrative(data: dict, failures: list[str]) -> str:
    if not data["queries"]:
        return "No community search was made: the venture's category is not known."
    searched = " and ".join(f'"{q}"' for q in data["queries"])
    if not data["subreddits"]:
        why = "the search failed" if failures else "nothing matched"
        return f"Reddit was searched for {searched}; {why}, so no community is listed."
    top = ", ".join(f'r/{s["name"]}' for s in data["subreddits"][:3])
    return (f"Reddit was searched for {searched}. {len(data['subreddits'])} communities are "
            f"listed, led by {top}. Match quality is {data['quality']}.")


# --------------------------------------------------------------------------- data
def validate_data(data: dict) -> None:
    if not isinstance(data, dict) or not isinstance(data.get("queries"), list):
        raise ValueError("community_discovery data needs its queries")
    if data.get("quality") not in ("good", "moderate", "poor"):
        raise ValueError("quality must be good, moderate or poor")
    subs = data.get("subreddits")
    if not isinstance(subs, list) or len(subs) > KEEP:
        raise ValueError(f"subreddits must be a list of at most {KEEP}")
    for item in subs:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            raise ValueError("every subreddit needs a name")
        score = base.finite_number(item.get("confidence"))
        if score is None or not 0 <= score <= 1 or item.get("match_tier") != tier(score):
            raise ValueError("confidence must be in [0, 1] and match its tier")
        if not isinstance(item.get("subscribers"), int) or item["subscribers"] < 0:
            raise ValueError("subscribers must be a count")
    dist = data.get("tier_distribution")
    if not isinstance(dist, dict) or sum(dist.values()) != len(subs):
        raise ValueError("tier_distribution must count the listed subreddits")


def render_data(data: dict) -> str:
    rows = [(f'r/{s["name"]}', s["title"], f'{s["subscribers"]:,}', f'{s["confidence"]:.2f}',
             s["match_tier"]) for s in data.get("subreddits", [])]
    head = (f'<p>Searched: {base.esc("; ".join(data.get("queries", [])))}. '
            f'Match quality: {base.esc(data.get("quality"))}.</p>')
    if not rows:
        return head + "<p>No community matched.</p>"
    return head + base.render_table(("Subreddit", "Title", "Members", "Confidence", "Tier"), rows)
