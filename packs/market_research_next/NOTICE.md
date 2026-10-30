# Third-party methods adapted in this pack

The analysis sections in `analysis/` adapt methods from the open-source projects below.
None of their code is imported or vendored; each method was re-implemented in Python
against this pack's section contract. Changes from each original are listed.

| Section | Source | License | Copyright | What changed |
|---|---|---|---|---|
| `sizing` | [milesc-bot/market-recon](https://github.com/milesc-bot/market-recon) | MIT | market-recon contributors | Same three methods, SOM share heuristic and 180-day confidence rule. Domains are registered domains, not raw hosts; the founder's own input never counts as a domain; without a competitor count SOM is not computed rather than assuming none. |
| `competitor_count` | [AgriciDaniel/local-competitor-map](https://github.com/AgriciDaniel/local-competitor-map) | Apache-2.0 | Daniel Agrici (builds on Google AI Studio's mcp_maps_3d sample) | Ported from the TypeScript Maps JavaScript library to the Places API (New) REST endpoint; adds `priceLevel`; no map or Gemini wrapper. |
| `community_discovery` | [dialog-tools/reddit-research-mcp](https://github.com/dialog-tools/reddit-research-mcp) | MIT | King of the Grackles | Their hosted vector search is not used. Reddit's subreddit search, through app-only OAuth, is ranked locally with their generic-subreddit penalty, subscriber adjustments and quality bands; confidence comes from term overlap, not embedding distance. Without Reddit credentials the same ranking runs over two open archives' public APIs: [Arctic Shift](https://github.com/ArthurHeitmann/arctic_shift) (subreddit metadata) and [PullPush](https://pullpush.io) (post search). No code of theirs is copied. |
| `pricing` | [sandbaseai/sandbase-skills](https://github.com/sandbaseai/sandbase-skills) (`multi-source-search`) | Apache-2.0 | SandBase | Their 1 / 2 / 3+ independent-source confidence levels and "a conflict is never high" rule, applied to pricing tiers found with this pack's own search tools; a competitor's own pricing page counts as at least medium. |
| `consistency` (review) | [damionrashford/RivalSearchMCP](https://github.com/damionrashford/RivalSearchMCP) (`core/conflict/detect.py`) | MIT | damionrashford | The numeric conflict rule, applied between this report's own sections instead of fetched web pages; money is its own unit class. |
| `five_forces`, `generic_strategy` | [gnurio/porter-strategy-skills](https://github.com/gnurio/porter-strategy-skills) | CC BY 4.0 ([license](https://creativecommons.org/licenses/by/4.0/)) | George Nurijanian | The 32 sub-factors, requirements table, risks and stuck-in-the-middle checklist, turned into a validated JSON schema; the flag count is computed in code. |
| `analog_markets` | [dungnotnull/market-analysis-hd-skill](https://github.com/dungnotnull/market-analysis-hd-skill) (stage 5) | MIT | dungnotnull | The seven structural characteristics and the three-shared-characteristics rule as a validated schema; analogs are model analysis, labelled as unverified. |

The MIT and Apache-2.0 license texts apply to the respective adapted methods. The Apache-2.0
projects' NOTICE terms are carried by this file.
