"""
Single CLI entry point tying discover + taste + match together.

Examples:
  python cli.py discover skincare
  python cli.py taste "Glossier" glossier.com
  python cli.py match "AI-powered routine tool" out/taste_glossier.json
  python cli.py full skincare                      # runs discover → picks top 1 → taste → ready for match

All outputs go to ./out/ as timestamped JSON.
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import time
from pathlib import Path

OUT = Path(__file__).parent / "out"
OUT.mkdir(exist_ok=True)


def _save(name: str, data: dict) -> Path:
    ts = time.strftime("%Y%m%d_%H%M%S")
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
    path = OUT / f"{safe}_{ts}.json"
    path.write_text(json.dumps(data, indent=2, default=str))
    print(f"[cli] saved → {path}")
    return path


def cmd_discover(args):
    from discover import discover

    result = discover(args.category, geo=args.geo)
    _save(f"discover_{args.category}", result)
    if "synthesis" in result and isinstance(result["synthesis"], dict):
        opps = result["synthesis"].get("ranked_opportunities", [])
        print(f"\n=== Top opportunities for '{args.category}' ===")
        for i, o in enumerate(opps[:10], 1):
            print(f"{i}. {o.get('brand')} (score={o.get('opportunity_score')})")
            print(f"   {o.get('thesis', '')[:200]}")


def cmd_taste(args):
    from taste import decode_taste

    result = decode_taste(args.brand, args.domain)
    _save(f"taste_{args.brand}", result)
    print("\n=== Taste profile ===")
    print(json.dumps(result, indent=2, default=str)[:3000])


def cmd_match(args):
    from match import score_match

    with open(args.profile) as f:
        profile = json.load(f)
    result = score_match(args.idea, profile)
    _save(f"match", result)
    print("\n=== Match result ===")
    print(json.dumps(result, indent=2, default=str)[:3000])


def cmd_full(args):
    """discover → decode taste for top 3 → print."""
    from discover import discover
    from taste import decode_taste

    disc = discover(args.category, geo=args.geo)
    _save(f"discover_{args.category}", disc)

    opps = (disc.get("synthesis") or {}).get("ranked_opportunities", [])
    if not opps:
        print("[full] no opportunities found — stopping")
        return

    print(f"\n[full] {len(opps)} opportunities found; decoding taste for top 3")
    for o in opps[:3]:
        brand = o.get("brand")
        domain = o.get("domain")
        if not brand or not domain:
            continue
        print(f"\n[full] → {brand} / {domain}")
        try:
            profile = decode_taste(brand, domain)
            _save(f"taste_{brand}", profile)
        except Exception as e:
            print(f"  ! taste decode failed: {e}")


def main():
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass  # deps not yet installed — --help still works
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)

    pd = sub.add_parser("discover", help="find opportunities in a category")
    pd.add_argument("category")
    pd.add_argument("--geo", default="US")
    pd.set_defaults(func=cmd_discover)

    pt = sub.add_parser("taste", help="decode audience taste for a brand")
    pt.add_argument("brand")
    pt.add_argument("domain")
    pt.set_defaults(func=cmd_taste)

    pm = sub.add_parser("match", help="score a product idea against a taste profile")
    pm.add_argument("idea")
    pm.add_argument("profile", help="path to taste_*.json file")
    pm.set_defaults(func=cmd_match)

    pf = sub.add_parser("full", help="discover + decode top opportunities end-to-end")
    pf.add_argument("category")
    pf.add_argument("--geo", default="US")
    pf.set_defaults(func=cmd_full)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
