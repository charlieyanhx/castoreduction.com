---
name: citation
summary: Attribute every checkable claim to a source the reader can follow, and audit the finished prose for claims that carry no live marker. Use when writing or reviewing narrative that asserts years, dollar figures, or percentages.
---

# Citation — attribution the reader can follow

## What counts as a claim

A sentence asserting something checkable: a year, a dollar figure, or a percentage.
Positioning prose ("buyers value trust") is not a claim and is deliberately not
policed — flagging it would train readers to ignore the checker entirely.

## Rules for writing

1. **One marker per claim.** Every checkable sentence carries a superscript marker
   resolving to an emitted citation. A run of superscripts is ONE id: the twelfth
   footnote is a two-glyph marker, not a first and a second.
2. **Cite the artifact, not the vibe.** Only sources actually in the evidence pool.
   Do not invent "HR Leader Interviews (N=20)" or a quarterly date stamp that no
   source carries.
3. **Dedupe on source + claim.** The same evidence cited twice keeps one id.
   A footnote list longer than the evidence base reads as rigour the work does not have.
4. **Say when data is thin.** "Data is thin on X — operator should validate via Y" is
   a stronger sentence than a confident number with no source. Faking conviction is
   the failure mode; hedging honestly is not.
5. **Assumptions are not citations.** "We assume an average job value of $200" is a
   legitimate sentence, but it is the author's input, not evidence. Do not dress it
   in a marker.

## Rules for auditing

The audit is deterministic and makes no judgement about whether a claim is TRUE —
only whether it is ATTRIBUTED. Two failures it exists to catch:

- **Uncited claim.** A checkable sentence with no marker, wearing the same authority
  as a sourced one.
- **Dangling marker.** A marker pointing at an id that was never emitted — a footnote
  that looks rigorous and resolves to nothing.

Resolve each section's markers against that SECTION's citation list when it has one.
Split synthesis numbers every section's footnotes from 1, so a pooled list holds
several colliding id spaces, and resolving against the pool would match some other
section's marker and call an unsourced claim sourced.

## Reporting

Emit the fact density — how many checkable claims the prose makes and how many are
attributed — and name unattributed claims as the author's inference rather than
sourced evidence. The number is advisory: it annotates the report, it does not block
it. A report at 68% attribution is not broken; a report that hides the figure is.
