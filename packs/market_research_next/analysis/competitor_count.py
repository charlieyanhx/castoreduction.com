"""Local competitor count from Google Places (method adapted from local-competitor-map, Apache-2.0).

One text search for "<category> in <location>" returns what a customer standing in that
place would find. Google's order is kept because it is the order a customer sees; closed
venues are dropped because they compete with nobody. The section applies only to a venture
that serves people in person somewhere; an online business has no local count.
"""
from __future__ import annotations

import os
import re
from typing import Any

from . import base

KEY = "competitor_count"
LABEL = "Local competitor count"
URL = "https://places.googleapis.com/v1/places:searchText"
FIELD_MASK = ",".join(f"places.{name}" for name in (
    "id", "displayName", "formattedAddress", "rating", "userRatingCount", "priceLevel",
    "businessStatus", "primaryType", "websiteUri"))
MAX_RESULTS = 20
PRICE_LEVELS = {"PRICE_LEVEL_FREE": 0, "PRICE_LEVEL_INEXPENSIVE": 1, "PRICE_LEVEL_MODERATE": 2,
                "PRICE_LEVEL_EXPENSIVE": 3, "PRICE_LEVEL_VERY_EXPENSIVE": 4}
CATEGORY_WORDS = 6
LOCATION_CHARS = 80

# "Online shop" names a channel, not a counter, so shop and store prove nothing either way.
_ONLINE = re.compile(r"\b(online|remote(ly)?|virtual(ly)?|web(site)?|internet|digital|app|saas|"
                     r"software|download|ship(s|ped|ping)?|mail(ed)?)\b", re.I)
_IN_PERSON = re.compile(r"\b(in[- ]person|on[- ]?site|face[- ]to[- ]face|walk(ed|ing)?|visit(s|ed)?|"
                        r"mix(ed)?|hybrid|both|physical(ly)?|venue|door)\b", re.I)
# The product phrase stops where it starts describing place, audience or mechanism.
_CLAUSE_END = re.compile(r"\s(?:on|in|at|near|for|where|that|which|who|with|to|serving|"
                         r"offering|located|based)\s", re.I)
_SENTENCE_END = re.compile(r"[.;:()]|\s-\s")
_ARTICLE = frozenset({"a", "an", "the", "our", "my"})
# How the venture is run, not what it is: dropped so the search names the business.
_QUALIFIERS = frozenset({"single-site", "multi-site", "in-person", "online", "mobile", "local",
                         "small", "new", "independent", "provisional"})
# Container nouns that say nothing a map search can use on their own.
_GENERIC = frozenset({"app", "platform", "tool", "service", "marketplace", "website", "site",
                      "business", "company", "startup", "product", "solution", "software"})
_FILLER = frozenset({"a", "an", "the", "and", "or", "of", "to", "in", "on", "at", "local",
                     "nearby", "their", "own", "more", "be", "is", "are", "can", "get"})


# --------------------------------------------------------------------------- inputs
def _text(context: dict, name: str) -> str | None:
    """An attribute's text; a typed slot renders as its value."""
    value = base.attribute(context, name).get("value")
    if isinstance(value, dict):
        value = value.get("value")
    return value.strip() if isinstance(value, str) and value.strip() else None


def category_phrase(context: dict) -> str | None:
    """The venture's category as a short noun phrase, from the product attribute.

    Deterministic on purpose: take the first sentence, stop at the first word that begins
    a place, audience or mechanism ("on", "for", "where"), drop articles and delivery
    qualifiers ("single-site", "in-person") and keep the last six words, where an English
    noun phrase keeps its noun. "Single-site, in-person specialty coffee shop on NW 23rd
    Avenue" -> "specialty coffee shop". When only a container noun is left ("An app where
    dog owners book local dog walkers"), the last two content words of the clause that
    follows name the business instead: "dog walkers". No model is asked, so the same
    brief always searches the same words.
    """
    product = _text(context, "product")
    if not product:
        return None
    sentence = _SENTENCE_END.split(product, maxsplit=1)[0].replace(",", " ")
    parts = _CLAUSE_END.split(f" {sentence} ", maxsplit=1)
    words = [w for w in parts[0].split()
             if w.lower() not in _ARTICLE and w.lower() not in _QUALIFIERS][-CATEGORY_WORDS:]
    if all(w.lower() in _GENERIC for w in words) and len(parts) > 1:
        content = [w for w in parts[1].split() if w.lower() not in _FILLER]
        words = content[-2:] or words
    return " ".join(words) or None


def location_phrase(context: dict) -> str | None:
    """The first clause of the geography attribute, else of the survey's geography answer."""
    text = _text(context, "geography") or base.intake_facts(context).get("geography")
    if not text:
        return None
    first = re.split(r"[.;\n]", text.strip(), maxsplit=1)[0].strip(" ,")
    return first[:LOCATION_CHARS].strip() or None


def _delivery_mode(context: dict) -> str:
    return " ".join(filter(None, (base.intake_facts(context).get("delivery_mode"),
                                  _text(context, "delivery_mode"))))


def inapplicable(decided: dict) -> str | None:
    """A reason when a local count means nothing: online only, or no place at all."""
    mode = _delivery_mode(decided)
    if mode and _ONLINE.search(mode) and not _IN_PERSON.search(mode):
        return "The venture is delivered online only, so there is no local competitor count."
    if not location_phrase(decided):
        return "The venture names no location, so there is no local area to count."
    return None


# --------------------------------------------------------------------------- parsing
def parse_places(payload: Any) -> tuple[list[dict], int]:
    """Open competitors in Google's order, ranked 1..n, and how many places Google returned."""
    if not isinstance(payload, dict):
        raise base.AnalysisError("Google Places returned an unexpected shape")
    places = payload.get("places") or []
    if not isinstance(places, list):
        raise base.AnalysisError("Google Places returned an unexpected shape")
    competitors = []
    for place in places:
        if not isinstance(place, dict) or place.get("businessStatus") == "CLOSED_PERMANENTLY":
            continue
        display = place.get("displayName")
        name = display.get("text") if isinstance(display, dict) else None
        if not isinstance(name, str) or not name.strip():
            continue
        reviews = base.finite_number(place.get("userRatingCount"))
        competitors.append({
            "rank": len(competitors) + 1, "name": name.strip(),
            "address": _str(place.get("formattedAddress")),
            "rating": base.finite_number(place.get("rating")),
            "reviews": int(reviews) if reviews is not None else None,
            "price_level": PRICE_LEVELS.get(place.get("priceLevel")),
            "status": _str(place.get("businessStatus")),
            "category": _str(place.get("primaryType")),
            "website": _str(place.get("websiteUri")),
        })
    return competitors, len(places)


def _str(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def summarize(competitors: list[dict]) -> dict:
    levels = [c["price_level"] for c in competitors if c["price_level"] is not None]
    return {"count": len(competitors),
            "median_rating": base.median(c["rating"] for c in competitors),
            "median_price_level": base.median(levels),
            "price_level_counts": {str(n): levels.count(n) for n in range(5)}}


# --------------------------------------------------------------------------- produce
def _unavailable(query: str | None, location: str | None, reason: str) -> dict:
    data = {"available": False, "query": query, "location": location, "competitors": [],
            "count": None, "median_rating": None, "median_price_level": None,
            "price_level_counts": {str(n): 0 for n in range(5)}}
    return base.envelope(LABEL, f"No local competitor count was made. {reason}",
                         data=data, uncertainties=[reason], status="partial")


def produce(context: dict, *, model=None, session=None, env=None, now=None) -> dict:
    """Search Places once. No key, no inputs, or a failed call is a partial section: the
    count is an optional enrichment, and failing it would send every section that reads
    it back through correction rounds for an outside fault they cannot repair."""
    env = os.environ if env is None else env
    category, location = category_phrase(context), location_phrase(context)
    query = f"{category} in {location}" if category and location else None
    if not query:
        missing = "category (product)" if not category else "location (geography)"
        return _unavailable(None, location, f"The venture's {missing} is not known.")
    key = (env.get("GOOGLE_MAPS_API_KEY") or "").strip()
    if not key:
        return _unavailable(query, location, "The Google Places key is not configured, "
                            "so no local search was made.")
    try:
        payload = base.http_json(
            "POST", URL, session=session,
            headers={"X-Goog-Api-Key": key, "X-Goog-FieldMask": FIELD_MASK,
                     "Content-Type": "application/json"},
            json_body={"textQuery": query, "maxResultCount": MAX_RESULTS, "languageCode": "en"})
        competitors, returned = parse_places(payload)
    except base.AnalysisError as exc:
        # The message names only the host and status, never the provider's body.
        return _unavailable(query, location, f"The Google Places search failed: {exc}.")
    stats = summarize(competitors)
    data = {"available": True, "query": query, "location": location,
            "competitors": competitors, **stats}
    return base.envelope(LABEL, _narrative(data), data=data, status="completed",
                         findings=_findings(data), assumptions=_assumptions(),
                         uncertainties=_uncertainties(data, returned),
                         metrics={"count": stats["count"], "median_rating": stats["median_rating"],
                                  "median_price_level": stats["median_price_level"]})


def _narrative(data: dict) -> str:
    text = (f'Google Places lists {data["count"]} open businesses for "{data["query"]}".')
    if data["median_rating"] is not None:
        text += f' Their median rating is {data["median_rating"]:.1f}.'
    if data["median_price_level"] is not None:
        text += f' The median price level is {data["median_price_level"]:g} on Google\'s 0 to 4 scale.'
    return text


def _findings(data: dict) -> list[str]:
    reviewed = sorted((c for c in data["competitors"] if c["reviews"]),
                      key=lambda c: -c["reviews"])[:3]
    return [f'{c["name"]}: {c["reviews"]} reviews, rating {c["rating"]}' for c in reviewed]


def _assumptions() -> list[str]:
    return ["Google's text search decides which places match the category; its order is kept.",
            "Permanently closed places are left out of the count."]


def _uncertainties(data: dict, returned: int) -> list[str]:
    notes = []
    if returned >= MAX_RESULTS:
        notes.append(f"Google returns at most {MAX_RESULTS} places per search, so the real "
                     "number of competitors is likely higher.")
    if not data["competitors"]:
        notes.append("No open place matched; the category wording may not match how Google "
                     "labels these businesses.")
    return notes


# --------------------------------------------------------------------------- data
def validate_data(data: dict) -> None:
    if not isinstance(data, dict) or not isinstance(data.get("available"), bool):
        raise ValueError("competitor_count data needs an available flag")
    if not isinstance(data.get("competitors"), list):
        raise ValueError("competitor_count data needs a competitors list")
    counts = data.get("price_level_counts")
    if not isinstance(counts, dict) or set(counts) != {str(n) for n in range(5)}:
        raise ValueError("price_level_counts must count levels 0 to 4")
    for rank, item in enumerate(data["competitors"], start=1):
        if not isinstance(item, dict) or item.get("rank") != rank:
            raise ValueError("competitors must be ranked 1..n in order")
        if not isinstance(item.get("name"), str) or not item["name"].strip():
            raise ValueError("every competitor needs a name")
        if item.get("price_level") not in (None, 0, 1, 2, 3, 4):
            raise ValueError("price_level must be 0 to 4 or null")
    if data["available"]:
        if not isinstance(data.get("query"), str) or data.get("count") != len(data["competitors"]):
            raise ValueError("an available count needs its query and a matching count")
    elif data["competitors"] or data.get("count") is not None:
        raise ValueError("an unavailable count cannot list competitors")


def render_data(data: dict) -> str:
    if not data.get("available"):
        return "<p>No local search was made.</p>"
    level = {None: "", 0: "Free", 1: "$", 2: "$$", 3: "$$$", 4: "$$$$"}
    rows = [(c["rank"], c["name"], c["address"] or "", c["rating"] if c["rating"] is not None else "",
             c["reviews"] if c["reviews"] is not None else "", level.get(c["price_level"], ""))
            for c in data["competitors"]]
    head = f'<p>Search: {base.esc(data["query"])}. Open places: {base.esc(data["count"])}.</p>'
    return head + base.render_table(("Rank", "Name", "Address", "Rating", "Reviews", "Price"), rows)
