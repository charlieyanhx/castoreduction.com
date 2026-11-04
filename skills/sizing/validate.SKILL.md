---
name: validate_numbers
produces: validation
summary: The mandatory gate every sizing payload passes before it ships. Checks the figures against each other and against their own stated formulas. Hard violations set an error so the report refuses to publish; softer ones warn. Validate loud, never silent.
---

# The numbers gate

## What this is for

Every other sizing method produces figures. This one decides whether those figures are
allowed to reach a buyer. It is the difference between a report that is wrong and a report
that is wrong **and says so**.

It computes nothing new. It only asks whether what was computed can be true.

## The two severities, and why the line matters

- **Block** sets `Evidence.error`, and the renderer refuses to publish. Use it when the
  numbers cannot be true regardless of context: they contradict each other or their own
  arithmetic.
- **Warn** is advisory and ships with the report. Use it when the numbers might be true but
  deserve a reader's attention.

Moving a check across that line is a product decision, not a technical one. A block that
fires on a correct report withholds something a founder paid for; a warn that should have
been a block ships a number nobody can defend.

## The checks

| check | rule | severity |
|---|---|---|
| `no_numbers` | the payload has no figures at all | block |
| `ordering` | SOM ≤ SAM ≤ TAM | block |
| `share_ceiling` | SOM / TAM ≤ 40% without an explicit override | block |
| `provenance` | every `*_usd` figure names a source | block |
| `trade_area_cap` | SOM ≤ the catchment's total spend (hyperlocal only) | block |
| `formula_reconciliation` | a figure's stated formula computes to its stated value | block / warn |
| `segmentation_sum` | segment figures sum to their stated total | block |
| `triangulation` | two independent SOM estimates agree within tolerance | warn |

## Formula reconciliation, and why it is fussy

The check parses a figure's own words back into arithmetic: `"166k × $50"` must equal the
value printed beside it. It caught a report claiming `166k × $50 = $845M`, which is
actually $8.3M.

Parsing prose into arithmetic is where this method has been wrong, twice, and both fixes
are load-bearing:

- **A bare magnitude letter must stand alone.** `"300 mid-size"` used to read the `m` of
  "mid" as mega, inventing a million-fold blow-up that raised a false block and withheld
  correct sizings. Attached suffixes (`100k`, `130M`) and `%` still resolve.
- **A slash is only division when a number follows it.** `/hh/yr` and `/location` are
  per-unit prose; `/ 3%` is division.

If you widen this parser, widen it against the corpus first. Both bugs above passed every
unit test and were found by sweeping real reports.

## What to change

- **A threshold is wrong** → `DEFAULT_MAX_SHARE` (the 40% share ceiling), or the tolerance
  in the triangulation check.
- **A check should block instead of warn, or the reverse** → move it between the `blocks`
  and `warns` lists in `_check`. Sweep the corpus before and after: a check that changes
  severity changes which reports ship.
- **A new check** → add it to `_check` with a `check` name and a message that carries the
  actual numbers. A finding that says "ordering violated" is not actionable; one that says
  "SOM 4,200,000 > SAM 1,100,000" is.

## How to check your change

```bash
.venv/bin/python -m bench call validate_numbers sizing='{"tam_usd":100,"sam_usd":10,"som_usd":1}'
```

Then sweep the corpus. This method decides what ships, so a change here moves the pass
rate directly, and that number is the review.

## What this method must never do

Fix the numbers. It reports; it does not adjust. A gate that quietly corrects its input is
a gate nobody can audit, and the corrected value would carry the authority of a checked
one without ever having been checked.
