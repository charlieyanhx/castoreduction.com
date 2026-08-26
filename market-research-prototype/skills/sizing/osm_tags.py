"""skills/sizing/osm_tags.py — business category to the OpenStreetMap tag that finds it.

Moved verbatim out of plan.py (#87, wave 2). Bodies byte-identical; plan.py re-exports.

The competitor census for a hyperlocal venture is an Overpass query, and that query needs two
things this module owns: WHICH tag identifies a competitor (amenity=cafe, shop=bicycle,
leisure=fitness_centre — the key differs per category, and guessing `amenity` for a bike shop
finds nothing), and HOW FAR the trade area reaches (a walk-in cafe draws from ~1.5km, not the
flat 3km that once made a neighbourhood cafe's catchment the size of a small city).

Deliberately a lookup with a no-match answer rather than a model call. An unmapped category
returns None and the caller falls back explicitly — inventing a plausible-looking tag would
return a confident census of the wrong kind of business, which is harder to notice than none.
"""
from __future__ import annotations


_OSM_TAG_BY_CATEGORY = {
    "restaurant": ("amenity", "restaurant"), "eatery": ("amenity", "restaurant"),
    "diner": ("amenity", "restaurant"), "bistro": ("amenity", "restaurant"),
    "food truck": ("amenity", "fast_food"), "food cart": ("amenity", "fast_food"),
    "fast food": ("amenity", "fast_food"), "fast-casual": ("amenity", "fast_food"),
    "fast casual": ("amenity", "fast_food"), "salad": ("amenity", "fast_food"),
    "food": ("amenity", "restaurant"),
    "cafe": ("amenity", "cafe"), "café": ("amenity", "cafe"), "coffee": ("amenity", "cafe"),
    "tea house": ("amenity", "cafe"), "teahouse": ("amenity", "cafe"),
    # A live Lisbon run classified its own category as "artisan sourdough and pastries" —
    # no word "bakery" in it — so this table returned None, size_by_scale fell back to
    # amenity=restaurant, and an artisan bakery was benchmarked against 1,603 restaurants
    # including a London burger place. The words a founder and an LLM actually use for this
    # trade, including the non-English ones a non-US venture will produce:
    "bakery": ("shop", "bakery"), "patisserie": ("shop", "bakery"),
    "pâtisserie": ("shop", "bakery"), "pastry": ("shop", "bakery"),
    "pastries": ("shop", "bakery"), "sourdough": ("shop", "bakery"),
    "bread": ("shop", "bakery"), "boulangerie": ("shop", "bakery"),
    "pastelaria": ("shop", "bakery"), "panaderia": ("shop", "bakery"),
    "panadería": ("shop", "bakery"), "cake": ("shop", "bakery"),
    "cupcake": ("shop", "bakery"), "doughnut": ("shop", "bakery"),
    "donut": ("shop", "bakery"), "bagel": ("shop", "bakery"),
    "croissant": ("shop", "bakery"), "confectioner": ("shop", "confectionery"),
    "chocolatier": ("shop", "chocolate"), "deli": ("shop", "deli"),
    "delicatessen": ("shop", "deli"), "butcher": ("shop", "butcher"),
    "greengrocer": ("shop", "greengrocer"), "cheesemonger": ("shop", "cheese"),
    "pub": ("amenity", "pub"), "brewery": ("amenity", "bar"), "bar": ("amenity", "bar"),
    "wine bar": ("amenity", "bar"), "cocktail": ("amenity", "bar"),
    "nightclub": ("amenity", "nightclub"), "ice cream": ("amenity", "ice_cream"),
    "gym": ("leisure", "fitness_centre"), "fitness": ("leisure", "fitness_centre"),
    "crossfit": ("leisure", "fitness_centre"), "yoga": ("leisure", "fitness_centre"),
    "pilates": ("leisure", "fitness_centre"),
    "barbershop": ("shop", "hairdresser"), "barber": ("shop", "hairdresser"),
    "hair salon": ("shop", "hairdresser"), "salon": ("shop", "hairdresser"),
    "nail": ("shop", "beauty"), "beauty": ("shop", "beauty"), "spa": ("leisure", "spa"),
    "clinic": ("amenity", "clinic"), "dental": ("amenity", "dentist"),
    "dentist": ("amenity", "dentist"), "veterinary": ("amenity", "veterinary"),
    "pharmacy": ("amenity", "pharmacy"), "bookstore": ("shop", "books"),
    "bookshop": ("shop", "books"), "florist": ("shop", "florist"),
    "library": ("amenity", "library"), "cinema": ("amenity", "cinema"),
    # Clothing / vintage / fashion retail — shop=clothes is the OSM tag for
    # any physical clothing store. Vintage, consignment, and boutique all map here.
    "clothing": ("shop", "clothes"), "clothes": ("shop", "clothes"),
    "vintage": ("shop", "clothes"), "thrift": ("shop", "second_hand"),
    "consignment": ("shop", "second_hand"), "second hand": ("shop", "second_hand"),
    "secondhand": ("shop", "second_hand"), "resale": ("shop", "second_hand"),
    "boutique": ("shop", "clothes"), "fashion": ("shop", "clothes"),
    "apparel": ("shop", "clothes"), "garment": ("shop", "clothes"),
    "luxury retail": ("shop", "clothes"),
}
_RADIUS_BY_OSM_VALUE = {
    "cafe": 1500, "fast_food": 1500, "bakery": 1500, "ice_cream": 1500,
    "bar": 2000, "pub": 2000, "hairdresser": 2500, "beauty": 2500, "spa": 3000,
    "restaurant": 3000, "nightclub": 3000, "pharmacy": 3000,
    # Clothing/vintage: destination retail — people travel further for boutique shopping
    # than for a haircut; 4km covers a tourist strip like Venice Beach comfortably.
    "clothes": 4000, "second_hand": 4000,
    "clinic": 4500, "dentist": 4500, "fitness_centre": 5000, "cinema": 6000, "library": 4000,
}
def _radius_for_osm_value(osm_value: str, default: int = 3000) -> int:
    """Deterministic catchment radius (m) for an OSM venue type — walk-in vs destination."""
    return _RADIUS_BY_OSM_VALUE.get((osm_value or "").lower(), default)
def _resolve_osm_tag(category: str) -> tuple[str, str] | None:
    """Deterministic category → (osm_key, osm_value) using real OSM taxonomy. Longest
    substring match wins (so 'barbershop' → shop/hairdresser, not 'bar' → amenity/bar).
    Returns None when no confident match, so callers SKIP geo-competitor fetch instead of
    guessing a wrong category."""
    catl = (category or "").lower()
    best, best_len = None, 0
    for k, v in _OSM_TAG_BY_CATEGORY.items():
        if k in catl and len(k) > best_len:
            best, best_len = v, len(k)
    return best
def _resolve_osm_amenity(category: str) -> str | None:
    """Back-compat shim: the OSM VALUE only (e.g. 'cafe', 'hairdresser'). Prefer
    _resolve_osm_tag, which also returns the correct OSM key (amenity/shop/leisure)."""
    t = _resolve_osm_tag(category)
    return t[1] if t else None
