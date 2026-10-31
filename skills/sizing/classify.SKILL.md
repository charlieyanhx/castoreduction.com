---
name: classify_market_scale
produces: market_scale
summary: Decide WHICH sizing method a venture gets. An LLM extracts three signals from the brief; deterministic rules turn those signals into a scale and a named sizing method. Get this wrong and every number downstream is wrong, because a neighbourhood cafe sized like a SaaS company is off by orders of magnitude.
---

# Market-scale classification — the routing decision

## Why this is the most important method in the engine

Nothing downstream can recover from a wrong answer here. A cafe routed to the digital
method gets a national TAM divided by an ARPU, which is not a market it can serve; a SaaS
company routed to the trade-area method gets one city's households, which is not its
market either. Every other sizing method assumes this one was right.

## The split that keeps it auditable

The model does **extraction only**. It reads the brief and returns three facts:

| signal | question it answers | allowed values |
|---|---|---|
| `is_physical` | does serving a customer require a premise or in-person footfall? | true / false |
| `geo_scope` | how far does the market reach? | `single_site`, `local_metro`, `regional`, `national`, `global` |
| `delivery` | how does the product get to the customer? | `in_person`, `local_delivery`, `online`, `hybrid` |

The **routing is deterministic rules over those three signals**. No number, and no method
choice, is left to model judgment. That is what makes the decision auditable: given the
same three signals, the same method is chosen every time, and the rule that chose it can
be read.

An unrecognised value is not trusted. `geo_scope` falls back to `national` and `delivery`
to `online`, because a degraded call must not silently look like a confident answer.

## The rules

A venture counts as **physical** if `is_physical` is true **or** delivery is `in_person`
or `local_delivery`. Then:

| physical? | geo_scope | scale | method | sizing skill |
|---|---|---|---|---|
| yes | `single_site`, `local_metro` | hyperlocal | trade-area catchment | `size_hyperlocal` |
| yes | `regional` | regional | per-location rollout | `size_regional` |
| yes | `national`, `global` | national_physical | per-location rollout | `size_regional` |
| no | `national` (or anything not global) | national_digital | top-down / bottom-up | `size_national_digital` |
| no | `global` | global_digital | top-down / bottom-up | `size_national_digital` |

## The four deterministic overrides, and the failure that bought each one

These run on the raw text and beat the model, because each was written after a real
report shipped wrong.

1. **Multi-location detection.** An explicit count (`8 studios`, `5 stores`) or chain
   language (`chain`, `franchise`, `multi-location`, `nationwide`) forces at least
   regional. *"8 studios across Austin" was routed hyperlocal and sized as one studio.*
2. **Physical-local override.** An obvious venue word plus a real location and no digital
   framing forces the physical path, even when the model degrades. *A rate-limited call
   defaulted to online and sent "a pizzeria in Echo Park" to the national digital method.*
   A location must be more than country-level: "in the US" alone does not count.
3. **Venue vocabulary is explicit, and incomplete on purpose.** Bare `stand` and `stall`
   are excluded because "the deal could stall" is not a venue; the food-anchored forms
   (`taco stand`, `food cart`, `food hall`) are unambiguous. *"A chinese beef tripe taco
   stand" matched nothing and shipped as a hybrid business.*
4. **Client-services exclusion.** An agency, consultancy or design studio paid per project
   is not a footfall business, so the household trade-area path never applies even when a
   city is named. Requires **both** a provider noun and a client/engagement signal, so
   "yoga studio for local clients" stays hyperlocal. *A $20k-per-project brand-design
   studio was routed hyperlocal, where the geo-competitor check cannot be satisfied.*

## What to change if you disagree with the routing

- **A venture type is routed to the wrong method** → the override regexes at the top of
  `classify.py`. Add the vocabulary, and add the case to `test_market_scale_classifier`
  so the next person knows it was deliberate.
- **A scale should use a different method** → `METHOD_FOR_SCALE`, one line per scale.
- **A new scale is needed** → add it to `METHOD_FOR_SCALE`, write the method with
  `@skill(produces="market_sizing")`, register it in `skills/sizing/__init__.py`, and add
  the branch to `skills/sizing/routing.py`. Nothing outside `skills/sizing/` changes.

## How to check your change

```bash
.venv/bin/python -m bench call classify_market_scale description="a pizzeria in Echo Park" geo=US
```

Then sweep the corpus, which contains seven venture kinds, and compare the pass rate
before and after. A routing change that improves one venture and breaks another shows up
there and nowhere else.

## What this method must never do

Return a number. It chooses a method; the method it chooses produces the numbers. If this
file ever computes a market size, the audit trail that makes the choice defensible is gone.
