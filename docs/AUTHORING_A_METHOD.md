# Working on a method

For the person who owns the **methods** rather than the pipeline. Everything here lives in
`skills/`. You should never need to open `plan.py`, and if you do, that is a bug in the
boundary rather than something to work around.

## The shape of the system, in one paragraph

A **method** is a Python function decorated with `@skill(produces="...")`. `produces` is
what it yields: `market_sizing`, `market_scale`, `validation`, `competitor_landscape`. Two
methods that produce the same thing are **alternatives**, and something has to choose
between them. Sizing has five alternatives and the choice is made in
`skills/sizing/routing.py`. The orchestrator asks the registry for the method by name and
calls it; it does not know the names.

## Finding the method that owns a decision

```bash
.venv/bin/python -m bench list --kind skill
```

One line each: name, what it produces, and its first docstring line. Methods producing the
same thing are the competing alternatives for that decision.

## Running one on its own

```bash
.venv/bin/python -m bench call size_hyperlocal address="123 Main St, Boise" category=food_away_from_home
```

No pipeline, no report, no six-minute wait. This is the loop to work in.

## Checking a change

```bash
.venv/bin/python -m bench smoke --kind skill      # every method, one line each, diffed against the last run
```

`smoke` exits non-zero only when the **code** is wrong: an error, a refusal, a timeout, a
missing fixture. A method that ran correctly and found nothing exits zero and prints
`empty`, because that is what happened.

Then sweep the corpus. It holds 19 real reports across seven venture kinds, and it is where
a change that helps one venture and hurts another becomes visible. A method change is
reviewed by its effect on that pass rate, not by whether the tests still pass.

## Writing the method document

Every method should have a `NAME.SKILL.md` beside its source. `skills/sizing/classify.SKILL.md`,
`skills/sizing/validate.SKILL.md` and `skills/sizing/hyperlocal.SKILL.md` are the worked
examples. The shape:

```markdown
---
name: the_registered_name
produces: what_it_yields
summary: One sentence. What it does, and the one thing that goes wrong without it.
---

# Title

## When this applies          (and the signal that it does NOT)
## The method                 (numbered steps, each naming its source)
## What to change             (which knob, and what it costs)
## How to check your change   (the bench command, then the corpus)
## What this method must never do
```

Two rules that make these documents worth writing:

**Name the source for every input.** "Households from US Census ACS 5-year" is checkable;
"household estimate" is not. Where a value is estimated rather than fetched, say so and say
why the estimate is shaped the way it is. `hyperlocal.SKILL.md` estimates residential
*density* rather than a household count, because density is a stable per-place quantity
while guessing a count compounds two unknowns. That reasoning is the part a reader cannot
reconstruct from the code.

**Write down the failure that bought each rule.** A regex that excludes the bare word
"stand" looks arbitrary until you know that "a chinese beef tripe taco stand" shipped as a
hybrid business. The next person to widen that rule needs to know what it was defending.

## Adding a method

1. Write it in the right package with `@skill(produces="...")`. Return an `Evidence`
   envelope: it carries `count`, `payload`, `error` and `skeleton`, and the distinction
   between an error and a skeleton is load-bearing. An error means the call failed. A
   skeleton means it succeeded and returned something inferred rather than fetched.
   Collapsing them turns "we could not look" into "we looked and found nothing".
2. Register it in the package's `__init__.py`. A method that is not imported does not
   exist as far as the catalogue, the bench and the router are concerned. There is a test
   that fails if you forget, because this happened: `size_citywide` ran in production for
   months while the catalogue showed four sizing methods instead of five.
3. If it competes with an existing method, add the branch that routes to it. For sizing
   that is `skills/sizing/routing.py`, which returns a method name and its arguments.
4. Write the `.SKILL.md`.
5. Run `bench smoke`, then sweep the corpus.

## Things that are not yours to change

- `core/` is the frame: ordering, verification, and the three kinds of absence a section
  can have. It imports nothing from this project and knows nothing about market research.
- `plan.py` and `orchestrator/` are the pipeline. If a method change needs an edit there,
  say so rather than making it: it usually means the method needs an input the pipeline
  does not yet pass, which is a boundary change worth doing deliberately.

## The habit that matters most

Measure before you widen a rule. Two of the worst bugs in the sizing gate passed every unit
test and were found only by sweeping real reports. The corpus is free to run and it is the
only instrument that sees a change's whole effect.
