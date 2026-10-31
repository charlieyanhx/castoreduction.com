---
name: hyperlocal_sizing
summary: Size a single physical location from real trade-area demand — households x category spend, capped by both fair-share competition and the site's own capacity. Use when the venture is one premise serving a walk/drive catchment, never for a national or online market.
---

# Hyperlocal sizing — trade-area catchment

## When this applies

One physical premise serving people who travel to it: a cafe, gym, clinic, salon,
restaurant. The signal that it does NOT apply: the venture ships, streams, or sells
online, or operates in more than one metro. Those are national/regional methods —
sizing them as a catchment understates the market by orders of magnitude.

## The method

Every input comes from an authoritative source. Nothing here is estimated by the LLM
unless a source is unavailable, and a fallback estimate is labelled as one.

1. **Trade area.** Geocode the address, then take the catchment radius from the trip
   type the category implies — walk-in convenience is a much tighter ring than a
   destination visit people plan a drive around. State the radius; it is the single
   assumption the whole estimate rests on.
2. **Households.** US Census ACS 5-year for the catchment. When ACS is unavailable,
   estimate residential DENSITY (households/km2) and multiply by catchment area —
   density is a stable per-place quantity, whereas guessing a household count
   directly compounds two unknowns.
3. **Category spend per household.** BLS Consumer Expenditure Survey line item,
   annual dollars per household.
4. **TAM_local** = households x annual spend per household.
5. **SAM_local** = TAM_local x serviceable fraction — the share of that spend the
   format can actually serve. State what the fraction excludes.
6. **SOM by demand** = SAM_local x 1/(competitors + 1) x ramp. Competing venues come
   from OpenStreetMap Overpass, not from a guess. The fair-share denominator is the
   competitor count PLUS the venture itself.
7. **SOM by supply** = seats x turns/day x average check x operating days. This is
   the capacity ceiling and is completely independent of demand.
8. **SOM_local = min(demand, supply)** — report which one binds. A site whose
   capacity binds has a different growth story from one whose demand binds, and the
   operator needs to know which lever moves the number.

## Capture-rate discipline

Fair share is the DEFAULT, not the floor. Claiming above fair share requires a stated
reason (an underserved catchment, a format with no local equivalent) and that reason
belongs in the report. A capture rate that quietly exceeds fair share is the most
common way a hyperlocal estimate becomes fiction.

## What invalidates the estimate

- The address does not geocode, so the catchment is imaginary.
- The competitor count came from a search snippet rather than a POI query.
- The multi-site case: SOM covers many locations while fixed costs describe one.
  Withhold the profit claim rather than scaling one store's rent to a chain's revenue.
- Every figure ships as {value_usd, label, source, formula} and runs through
  validate_numbers; a hard block sets Evidence.error and the report must not publish.
