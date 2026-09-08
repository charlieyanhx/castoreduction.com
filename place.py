"""
Step 11 of spec: Place analysis — detect go-to-market channels from competitor
homepages, then recommend primary + secondary channels for the product.

Channel signals we detect from homepage HTML:
  - "Book a demo" / "Talk to sales" → sales-led
  - "Sign up free" / "Start free trial" → product-led self-serve
  - "Download on App Store" / "Google Play" → mobile app
  - "Add to Chrome" / "Install extension" → browser extension
  - "API" / "Integrate with" → developer-led / integration
  - "Find a reseller" / "Partner with us" → channel sales
  - G2 / Capterra links → review-site discovery
  - "Subscribe" / "Join our mailing list" → content-led
"""
from __future__ import annotations
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

import net as mrp_http
from llm import call_json
from logger import get

log = get("place")


CHANNEL_PATTERNS = {
    "sales_led":        [r"book\s*a\s*demo", r"talk\s*to\s*sales", r"contact\s*sales", r"schedule\s*a\s*call"],
    "product_led":      [r"sign\s*up\s*free", r"start\s*free\s*trial", r"get\s*started\s*free", r"try\s*for\s*free"],
    "mobile_app":       [r"app\s*store", r"google\s*play", r"download\s*(the|on)\s*(ios|android)"],
    "browser_ext":      [r"chrome\s*(web\s*)?store", r"add\s*to\s*chrome", r"firefox\s*add-?on"],
    "developer":        [r"\bapi\b", r"developer\s*docs", r"integrate\s*with", r"github\.com"],
    "channel_partners": [r"partner\s*with\s*us", r"find\s*a\s*reseller", r"become\s*a\s*partner"],
    "review_sites":     [r"g2\.com", r"capterra\.com", r"trustradius", r"softwareadvice"],
    "content_led":      [r"subscribe\s*to\s*(our|the)\s*newsletter", r"join\s*our\s*mailing", r"read\s*the\s*blog"],
    "retail":           [r"find\s*in\s*store", r"shop\s*at\s*amazon", r"walmart\.com", r"target\.com"],
    "ecomm_direct":     [r"add\s*to\s*cart", r"buy\s*now", r"shop\s*all", r"free\s*shipping"],
}

PLACE_SYNTHESIS_PROMPT = """You are recommending a go-to-market channel strategy.

OUR PRODUCT: {product_summary}
OUR TARGET SEGMENT: {segment_summary}

COMPETITOR CHANNEL SIGNALS (top competitors in this category):
{competitor_channels}

DOMINANT CHANNELS (most common across competitors): {dominant}
OUTLIER CHANNELS (used by few competitors — potential whitespace): {outliers}

Return JSON:
{{
  "primary_channel": "the one channel we should focus on first",
  "primary_reasoning": "one sentence why",
  "secondary_channels": ["2-3 supporting channels"],
  "gtm_motion": "one paragraph describing the go-to-market motion",
  "whitespace_opportunity": "if any outlier channel is a good fit, which one and why",
  "avoid": "channels to skip and why"
}}

Keep everything concise — each list item ≤2 short sentences."""


def detect_channels(html: str) -> list[str]:
    """Return list of channel tags found in a homepage's HTML."""
    text = html.lower()
    found = []
    for channel, patterns in CHANNEL_PATTERNS.items():
        for pat in patterns:
            if re.search(pat, text):
                found.append(channel)
                break
    return found


def analyze_competitor_channels(domains: list[str]) -> dict:
    """
    Fetch each competitor homepage in parallel, detect channel signals, rank them.
    Returns: {per_domain: {d: [channels]}, channel_counts, channel_share}
    """
    def _fetch_one(d):
        try:
            r = mrp_http.get(f"https://{d}", timeout=8, max_retries=0, allow_redirects=True)
            if r.status_code >= 400:
                return (d, [])
            return (d, detect_channels(r.text[:30000]))
        except Exception as e:
            log.debug(f"  skipped {d}: {e}")
            return (d, [])

    per_domain: dict[str, list[str]] = {}
    aggregate: Counter = Counter()

    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(_fetch_one, d) for d in domains[:10]]
        for fut in as_completed(futures, timeout=30):
            try:
                d, channels = fut.result()
                per_domain[d] = channels
                aggregate.update(channels)
            except Exception:
                continue

    total = sum(aggregate.values()) or 1
    return {
        "per_domain": per_domain,
        "channel_counts": dict(aggregate),
        "channel_share": {k: round(v / total * 100, 1) for k, v in aggregate.items()},
    }


def recommend_place(
    product_summary: str,
    segment_summary: str,
    competitor_analysis: dict,
) -> dict:
    """Use LLM to recommend primary + secondary channels based on competitor analysis."""
    channel_counts = competitor_analysis.get("channel_counts", {})
    if not channel_counts:
        return {
            "error": "No competitor channel data available",
            "primary_channel": "unknown",
        }

    # Rank channels
    sorted_channels = sorted(channel_counts.items(), key=lambda x: -x[1])
    dominant = [c for c, n in sorted_channels if n >= max(channel_counts.values()) * 0.6]
    outliers = [c for c, n in sorted_channels if n == 1 and len(channel_counts) > 3]

    comp_blob = "\n".join(
        f"  {d}: {', '.join(ch) if ch else '(no signals)'}"
        for d, ch in list(competitor_analysis.get("per_domain", {}).items())[:10]
    )

    result = call_json(
        system="You recommend go-to-market channels. Return only JSON.",
        user=PLACE_SYNTHESIS_PROMPT.format(
            product_summary=product_summary[:800],
            segment_summary=segment_summary[:800],
            competitor_channels=comp_blob,
            dominant=", ".join(dominant) or "none",
            outliers=", ".join(outliers) or "none",
        ),
        max_tokens=1500,
    )
    result["_evidence"] = {
        "competitors_analyzed": len(competitor_analysis.get("per_domain", {})),
        "channel_distribution": competitor_analysis.get("channel_share", {}),
    }
    return result
