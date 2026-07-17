"""
Iter 38: structured-data extraction. Pulls JSON-LD, OpenGraph, microdata,
prices, and social-link signals from arbitrary HTML in one call.

Free leverage — companies routinely advertise founding year, headcount, prices,
LinkedIn URLs in `<script type="application/ld+json">` and `<meta property="og:*">`,
and we ship `extruct` + `price-parser` already. Calling them returns a normalized
dict the caller can merge with whatever else they have.

Output:
  {
    "json_ld":   list of dicts (raw),
    "opengraph": dict (flat: {og_type, og_title, og_description, og_image, ...}),
    "microdata": list of dicts (raw),
    # Convenience extractions for common pipeline needs:
    "founded_year":     int | None,   # from JSON-LD foundingDate
    "employee_count":   int | None,   # from JSON-LD numberOfEmployees
    "company_name":     str | None,   # from JSON-LD Organization.name or og:site_name
    "description":      str | None,   # from JSON-LD or og:description
    "logo_url":         str | None,   # from og:image or JSON-LD logo
    "social_links":     list[str],    # LinkedIn/Twitter/etc from sameAs
    "prices":           list[dict],   # {amount, currency, raw} from Offer.price
    "products":         list[dict],   # {name, sku, price, currency, in_stock}
  }
"""
from __future__ import annotations
import json
import re
from typing import Any
from urllib.parse import urljoin, urlparse


# ---------- content-validity gate (W2/D13) ----------
# Page-level parked/thin markers. Complements sources.is_parked_domain (which reasons
# about the DOMAIN/host); this gate reasons about the PAGE CONTENT in hand.
_PARKED_TEXT_RE = re.compile(
    r"(domain (?:is )?for sale|buy this domain|domain parking|parking lander|"
    r"parked (?:free )?(?:courtesy|by)|this (?:web ?page|site) is parked|"
    r"sedo|hugedomains|dan\.com|afternic|bodis|parkingcrew)", re.I)
# Below this much extracted text a "page" is a shell or lander, not content. Real
# pricing pages carry plan descriptions well past this.
MIN_CONTENT_CHARS = 400


# trafilatura drives lxml.html, whose class-lookup/parser state is process-global C
# state — two threads extracting at once intermittently corrupt the heap and SIGABRT
# the whole process (observed live: two _fetch_one workers both inside
# trafilatura.extract at the crash). Every trafilatura call in this codebase must hold
# this lock. Extraction is ~ms against network fetches, so serializing costs nothing.
import threading

TRAFILATURA_LOCK = threading.Lock()


def _trafilatura_text(html: str) -> str:
    """Main-content text via trafilatura (isolated so the gate can degrade if it breaks)."""
    import trafilatura
    with TRAFILATURA_LOCK:
        return trafilatura.extract(html, include_comments=False) or ""


def main_text(html: str) -> str:
    """Readable main-content text of a page (trafilatura, tag-strip fallback).
    Whitespace-normalized. Do NOT use for price/structured extraction — that is
    extract/extract_prices; this is for relevance judgments and summaries."""
    if not html:
        return ""
    try:
        text = _trafilatura_text(html)
    except Exception:
        text = re.sub(r"<[^>]+>", " ", html)
    return " ".join(text.split())


def page_is_substantive(html: str) -> tuple[bool, str]:
    """Content-validity gate: True only when the page carries real readable content.

    A parked lander, registrar shell, or JS-only stub yields almost no main-content
    text — reject it BEFORE price extraction can fabricate a benchmark from it (the
    audit's "$2,495 domain-sale price became the category median" failure). Never
    raises; if trafilatura breaks, falls back to tag-stripped text length.
    """
    if not html or len(html) < 50:
        return False, "empty html"
    try:
        text = _trafilatura_text(html)
    except Exception:
        text = re.sub(r"<[^>]+>", " ", html)
    text = " ".join(text.split())
    if _PARKED_TEXT_RE.search(text) or _PARKED_TEXT_RE.search(html[:5000]):
        return False, "parked-domain markers on page"
    if len(text) < MIN_CONTENT_CHARS:
        return False, f"thin content ({len(text)} chars extracted)"
    return True, f"substantive ({len(text)} chars)"


def extract(html: str, base_url: str = "") -> dict:
    """
    Extract everything useful from a chunk of HTML. Returns the dict above.
    Never raises — degrades gracefully to empty fields.
    """
    if not html or len(html) < 50:
        return _empty()

    try:
        import extruct
        data = extruct.extract(
            html,
            base_url=base_url,
            syntaxes=["json-ld", "opengraph", "microdata"],
            uniform=True,
        )
    except Exception:
        return _empty()

    json_ld = data.get("json-ld") or []
    opengraph = _flatten_opengraph(data.get("opengraph") or [])
    microdata = data.get("microdata") or []

    out = _empty()
    out["json_ld"] = json_ld
    out["opengraph"] = opengraph
    out["microdata"] = microdata

    # Walk JSON-LD looking for Organization / Product / Offer signals
    for item in _flatten_json_ld(json_ld):
        if not isinstance(item, dict):
            continue
        t = _type_of(item)
        if "Organization" in t or "Corporation" in t:
            out["company_name"] = out["company_name"] or item.get("name")
            out["description"] = out["description"] or item.get("description")
            out["logo_url"] = out["logo_url"] or _to_str(item.get("logo"))
            yr = _founding_year(item.get("foundingDate"))
            if yr and not out["founded_year"]:
                out["founded_year"] = yr
            ec = _to_int(item.get("numberOfEmployees"))
            if ec and not out["employee_count"]:
                out["employee_count"] = ec
            same = item.get("sameAs")
            if isinstance(same, str):
                out["social_links"].append(same)
            elif isinstance(same, list):
                out["social_links"].extend(s for s in same if isinstance(s, str))
        if "Product" in t:
            out["products"].append(_extract_product(item))
            # Products often inline a price field directly
            p = _extract_price(item)
            if p:
                out["prices"].append(p)
        if "Offer" in t or "AggregateOffer" in t:
            p = _extract_price(item)
            if p:
                out["prices"].append(p)
        # Service / SoftwareApplication / etc may have offers nested
        offers = item.get("offers")
        if offers:
            for off in (offers if isinstance(offers, list) else [offers]):
                if isinstance(off, dict):
                    p = _extract_price(off)
                    if p:
                        out["prices"].append(p)

    # Walk microdata for prices (e.g. <meta itemprop="price" content="29.95">)
    for md in microdata or []:
        if not isinstance(md, dict):
            continue
        props = md.get("properties") if isinstance(md.get("properties"), dict) else md
        for key in ("price", "lowPrice", "highPrice", "Price"):
            v = props.get(key)
            if v is not None:
                try:
                    amt = float(str(v).replace(",", "").replace("$", "").strip())
                    if 0 < amt < 1000000:
                        out["prices"].append({"amount": amt, "currency": "USD", "raw": str(v), "_source": "microdata"})
                except (ValueError, TypeError):
                    pass

    # OpenGraph product price (og:product:price:amount  →  og_product_price_amount or og_og_product_price_amount)
    for k, v in (opengraph or {}).items():
        if "product_price_amount" in k or k == "og_price_amount" or k == "og_og_price_amount":
            try:
                amt = float(str(v).replace(",", "").replace("$", "").strip())
                if 0 < amt < 1000000:
                    out["prices"].append({"amount": amt, "currency": "USD", "raw": str(v), "_source": "opengraph"})
            except (ValueError, TypeError):
                pass

    # Backfill from OpenGraph (extruct prepends "og_" + key already prefixed with "og:")
    if opengraph:
        def _og(*keys):
            for k in keys:
                if opengraph.get(k):
                    return opengraph[k]
            return None
        out["company_name"] = out["company_name"] or _og("og_site_name", "og_og_site_name", "og_title", "og_og_title")
        out["description"] = out["description"] or _og("og_description", "og_og_description")
        out["logo_url"] = out["logo_url"] or _og("og_image", "og_og_image")
        if base_url and out["logo_url"] and not out["logo_url"].startswith("http"):
            out["logo_url"] = urljoin(base_url, out["logo_url"])

    # Dedupe socials
    out["social_links"] = list(dict.fromkeys(out["social_links"]))
    return out


def extract_prices(html: str) -> list[dict]:
    """Just the prices — uses both extruct (Offers) and price-parser (regex on text)."""
    if not html:
        return []
    out = []

    # 1. Structured (highest confidence)
    structured = extract(html)
    out.extend(structured.get("prices", []))

    # 1b. Bare itemprop fallback — extruct ignores microdata not inside an
    # itemscope, but lots of pages put `<meta itemprop="price" content="X">`
    # alone in <head>. Catch those with a tiny regex.
    try:
        import re
        for m in re.finditer(
            r'<meta[^>]+itemprop=["\'](price|lowPrice|highPrice)["\'][^>]+content=["\']([0-9.,]+)',
            html, re.I,
        ):
            try:
                amt = float(m.group(2).replace(",", ""))
                if 0 < amt < 1000000:
                    out.append({"amount": amt, "currency": "USD", "raw": m.group(2), "_source": "itemprop-bare"})
            except ValueError:
                pass
    except Exception:
        pass

    # PRECISION (Item 3 / scraper audit): a page that ships structured price markup
    # (schema.org Offer / microdata / bare itemprop) has already told us its REAL
    # prices with high confidence. Do NOT then run the regex-over-all-text pass, which
    # scoops up shipping thresholds, discounts, and "was $250" strikethroughs as if
    # they were prices (the audit's one-real-$68 → [5,20,25,50,68,500] failure). Only
    # fall through to regex when structured extraction found nothing.
    if out:
        return out

    # 2. price-parser regex over visible text — catches inline `$49/mo` etc
    try:
        from price_parser import Price
        # Cheap text extraction: strip tags via selectolax (5-25× faster than bs4)
        try:
            from selectolax.parser import HTMLParser
            text = HTMLParser(html).text(separator=" ")
        except Exception:
            import re
            text = re.sub(r"<[^>]+>", " ", html)

        # Look for $ or € or £ followed by a number — price-parser handles ranges,
        # decimals, currency words. Limit to ~80 candidates per page.
        import re
        candidates = re.findall(r"[\$€£][\s]?[0-9][0-9,\.]*", text)[:80]
        seen = set()
        for c in candidates:
            p = Price.fromstring(c)
            if p.amount is not None:
                key = (float(p.amount), p.currency or "")
                if key in seen:
                    continue
                seen.add(key)
                out.append({"amount": float(p.amount), "currency": p.currency or "", "raw": c, "_source": "price-parser"})
    except Exception:
        pass
    return out


# ---------- helpers ----------
def _empty() -> dict:
    return {
        "json_ld": [],
        "opengraph": {},
        "microdata": [],
        "founded_year": None,
        "employee_count": None,
        "company_name": None,
        "description": None,
        "logo_url": None,
        "social_links": [],
        "prices": [],
        "products": [],
    }


def _flatten_opengraph(og_list: list) -> dict:
    """OpenGraph from extruct comes back as list of dicts; flatten to {og_*: val}."""
    out = {}
    for entry in og_list:
        if not isinstance(entry, dict):
            continue
        for k, v in entry.items():
            if isinstance(v, (str, int, float)) and not k.startswith("_"):
                out[f"og_{k.replace(':', '_')}"] = v
    return out


def _flatten_json_ld(items: list):
    """JSON-LD `@graph` containers — yield each inner item recursively."""
    for x in items or []:
        if isinstance(x, dict):
            if "@graph" in x and isinstance(x["@graph"], list):
                yield from _flatten_json_ld(x["@graph"])
            else:
                yield x
        elif isinstance(x, list):
            yield from _flatten_json_ld(x)


def _type_of(item: dict) -> list[str]:
    t = item.get("@type") or item.get("type") or ""
    if isinstance(t, str):
        return [t]
    if isinstance(t, list):
        return [str(x) for x in t]
    return []


def _founding_year(d) -> int | None:
    if not d:
        return None
    s = str(d)
    if len(s) >= 4 and s[:4].isdigit():
        try:
            y = int(s[:4])
            if 1800 <= y <= 2100:
                return y
        except ValueError:
            pass
    return None


def _to_int(v) -> int | None:
    if v is None:
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, dict):
        v = v.get("value") or v.get("@value")
    if isinstance(v, str):
        # Handle "501-1000" → take midpoint
        if "-" in v and v.replace("-", "").replace(",", "").isdigit():
            parts = [int(p.replace(",", "")) for p in v.split("-")]
            return sum(parts) // len(parts)
        digits = "".join(c for c in v if c.isdigit())
        if digits:
            try:
                return int(digits)
            except ValueError:
                return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _to_str(v) -> str | None:
    if v is None:
        return None
    if isinstance(v, str):
        return v
    if isinstance(v, dict):
        return v.get("url") or v.get("@id") or v.get("name")
    return None


def _extract_product(item: dict) -> dict:
    return {
        "name": item.get("name"),
        "sku": item.get("sku"),
        "description": item.get("description"),
    }


def _extract_price(offer: dict) -> dict | None:
    price = offer.get("price") or offer.get("lowPrice") or offer.get("highPrice")
    currency = offer.get("priceCurrency") or "USD"
    if price is None:
        return None
    try:
        amt = float(str(price).replace(",", "").replace("$", "").strip())
        return {"amount": amt, "currency": currency, "raw": str(price), "_source": "json-ld"}
    except (ValueError, TypeError):
        return None
