# 04 — Test Cases

Six benchmark cases live in `benchmarks/cases/`, each a JSON file with the venture description + reference data + expected pipeline outputs. Three originals plus three OOS (out-of-sample) cases added 2026-04-30.

## Current cases (6)

| File | Venture | Category | Buyer | Scope | Why this case |
|---|---|---|---|---|---|
| `sleep_loop.json` | Sleep Loop | Employer-sponsored sleep coaching | Head of Benefits | 200-2000 emp | Narrow-vertical / consumer-adjacent / benefits-buyer ICP |
| `devtools_apm.json` | TraceFlow | B2B SaaS observability/APM | VP Engineering | 50-1000 emp | Technical / dev-tool buyer / strong tech-community sources |
| `hr_smb.json` | Workhive | B2B SaaS HRIS/payroll for SMB | Founder-CEO | 10-200 emp | SMB / multi-feature bundle / bottom-up sizing |
| `cyber_soc.json` *(OOS)* | SentryOps | SOC / SIEM / SOAR | CISO | 500-5000 emp | High-stakes enterprise buyer, mature category w/ Magic Quadrant data |
| `restaurant_pos.json` *(OOS)* | Sliceline | Restaurant POS + back-of-house | Owner-Operator | 5-50 location SMB chain | Vertical SaaS, multi-location SMB, public Toast comp |
| `sales_engagement.json` *(OOS)* | Cadenz | Sales engagement / RevOps | VP Sales / RevOps | 50-1000 emp | Horizontal RevOps SaaS, named-leader Magic Quadrant |

The 6 cases collectively span:

- **Buyer roles**: Head of Benefits, VP Engineering, Founder-CEO, CISO, Owner-Operator, VP Sales / RevOps
- **Verticals**: corporate wellness, observability, HR/payroll, cybersecurity, restaurant tech, sales engagement
- **Scopes**: enterprise, SMB-multilocation, B2B SaaS midmarket, vertical SaaS, horizontal SaaS
- **Reference data**: KFF, Grand View, Rock Health, Gartner MQs (3 different categories), IDC/Statista, public SEC filings (Toast, CrowdStrike, Splunk, HubSpot), Census SUSB, National Restaurant Association

## OOS rationale

The first 3 cases were partially co-designed with the rubric — the venture descriptions and reference bands were tuned to exercise specific dimensions. The 3 OOS cases were added in a separate session **without any pipeline changes between case-write and bench-run**. They test whether the rubric and pipeline generalize to ventures the system hasn't seen before.

Specifically the OOS cases:
- Use buyer roles (CISO, Owner-Operator) the heuristic backstop hadn't been tuned for
- Use TAM scoping (enterprise security, restaurant SMB) at very different scales than the original cases
- Pull customer voice from communities (cybersecurity Twitter/Reddit, restaurant subreddits) the existing source whitelist may not optimally cover

## Case file structure

```jsonc
{
  "_doc": "Reference data for benchmarking the Castor pipeline against ...",
  "venture_under_test": {
    "name": "Sleep Loop",
    "description": "<full venture description, ≥30 chars; this is fed verbatim to /plan>",
    "category": "<short category label for human readers>",
    "geography": "US"
  },
  "references": [
    {
      "id": "kff_ehbs_2024",
      "source": "Kaiser Family Foundation",
      "title": "Employer Health Benefits Survey 2024",
      "url": "https://www.kff.org/...",
      "fetched_date": "2026-04-29",
      "publicly_accessible": true,
      "domain": "employer benefits / wellness adoption",
      "claims": [
        {
          "key": "pct_large_firms_increased_mh_via_third_party",
          "value": 48,
          "unit": "%",
          "source_quote": "48% of large employers have increased the number of mental health counseling resources..."
        },
        // ...more claims
      ]
    }
    // ...more references
  ],
  "expected_pipeline_outputs": {
    "tam_us_corporate_wellness_usd_low":  500000000,
    "tam_us_corporate_wellness_usd_mid":  5000000000,
    "tam_us_corporate_wellness_usd_high": 25000000000,
    "_tam_note": "Defensible scoping range...",
    "growth_cagr_pct_low": 3,
    "growth_cagr_pct_high": 25,
    "_cagr_note": "...",
    "competitor_must_include": ["Calm Business", "Headspace for Work", "Lyra Health", "Big Health", "BetterUp"],
    "_competitor_note": "KFF explicitly names Headspace and Lyra...",
    "icp_employee_band": "200-2000",
    "buyer_role_keywords": ["HR", "benefits", "people operations", "CHRO", "head of"],
    "minimum_pipeline_steps": 14,
    "minimum_customer_voice_sources": 5
  }
}
```

Note: TAM key names are `tam_us_corporate_wellness_usd_*` for legacy reasons (historically the first case was Sleep Loop / corporate wellness). The score.py reader treats them as opaque labels — same names work for any vertical. See [`process/02-decisions.md`](../process/02-decisions.md) for why we didn't rename.

## How references were curated

Each reference must satisfy four criteria:

1. **Publicly accessible** (no paywall, no login required) — verified via WebFetch at the time of curation, with `fetched_date` recorded
2. **Authoritative** — KFF, Grand View Research, Rock Health, Gartner, IDC, US Census, etc. — not random blog posts
3. **Quotable** — the `source_quote` field captures the exact line that supports the claim; <15 words to stay within fair-use comfort zone
4. **Numeric where possible** — claims should be quantitative so the bench can check pipeline output against them programmatically

Reference data is **not** fed to the pipeline at runtime — only the venture description is. References are used purely by the benchmark scorer to define expected output bands.

## Defensible scoping bands

Each `_tam_note` field documents *why* the band is set the way it is. Example for Sleep Loop:

> Defensible scoping range: narrow ICP-fit slice (employer-sponsored CBT-I sleep coaching only, ~$500M-$2B), mid-narrow (digital MH employer point-solutions, $2-8B), or broad (US corporate wellness total $20-25B). Pipeline TAM mid acceptable in $0.5B-$25B range as long as 3 methods reconcile.

This intentionally allows multiple defensible scopings — the bench shouldn't fail a pipeline that picks "narrow" if the pipeline gives a coherent reason.

## Adding a new case

1. **Create** `benchmarks/cases/<your_case>.json` matching the schema above
2. **Curate ≥3 references** — at least one each for: TAM/CAGR, competitor scale, ICP/firmographics
3. **Pick wide bands** — give the pipeline room to defensibly disagree
4. **List 3-5 expected competitors** — these will be checked via substring match
5. **Specify employee band + buyer-role keywords** — used by `icp_alignment` scorer

Then:

```bash
# Score a single case
python -m benchmarks.score http://127.0.0.1:8765/jobs/<id> --case=<your_case>

# Run as part of the multi-case dashboard
python -m benchmarks.run_all --cases <your_case>,sleep_loop,devtools_apm,hr_smb
```

Cases auto-discover from the directory — `list_cases()` walks `cases/*.json`.

## Running all cases

```bash
# Sequential, no LLM judge (fast, deterministic-only)
python -m benchmarks.run_all

# Sequential, with LLM judge (~6-10 min/case × N cases)
python -m benchmarks.run_all --with-prose

# Parallel — faster wall-clock but contention on LLM rate-limits
python -m benchmarks.run_all --parallel 3 --with-prose

# Save full structured dashboard for later analysis
python -m benchmarks.run_all --with-prose --out /tmp/dashboard.json
```

## Why exactly 3 cases?

- 1 case → single point, no signal
- 2 cases → can spot one consistent vs inconsistent issue
- **3 cases → can spot a pattern across cases** (e.g. "place prose is the weakest dimension in ALL 3 cases" — that's a real bug, not noise)
- 4+ cases → diminishing returns; each adds ~5-10 min to a sequential run

When the bench was 1 case, the rubric saturated at 100/100 and the system looked perfect. Going to 3 cases + 16 dimensions exposed real bugs that were hidden in the single-case run. See [`process/03-bugs-surfaced.md`](../process/03-bugs-surfaced.md).
