# Castor SIZING — the numbers-right methodology

> **Status:** Spec (cycle33). Implemented by `skills/sizing/` + `tools/geo.py`.
> Architecture context: Layer 3 in `ARCHITECTURE.md`. This is the moat.

The promise: **any scale, numbers right** — a restaurant in LA and a global SaaS
both get a defensible TAM/SAM/SOM, each computed by the method that actually fits,
every figure traceable to an authoritative source.

---

## Core principles (invariants)

1. **The LLM never invents a number.** Authoritative data (US Census ACS, BLS CEX,
   BEA, World Bank, OSM) is ground truth. The LLM extracts, assembles, narrates.
2. **Bottom-up beats top-down.** Build demand from real units (households × spend,
   seats × turns × check). Top-down (industry ÷ players) is only a sanity ceiling.
3. **Triangulate.** Every headline figure is computed ≥2 independent ways.
   Convergence → high confidence; divergence → a visible flag, never a silent pick.
4. **Provenance on everything.** Each number ships with `{value, source, formula}`.
5. **Confidence intervals, not false precision.** Report P10/P50/P90 with assumptions.
6. **Validate loud.** `validate_numbers` fails visibly on impossibilities
   (SOM > SAM, share > 100%, SOM > catchment spend).

---

## Method routing (from `classify_market_scale`)

| Scale | Method | Sizing skill |
|---|---|---|
| `hyperlocal` | trade-area catchment | `size_hyperlocal` |
| `regional` / `national_physical` | per-location × rollout | `size_regional` |
| `national_digital` / `global_digital` | top-down ÷ bottom-up | `size_national_digital` |

---

## Method 1 — Trade-area catchment (`size_hyperlocal`)

For a single physical premise serving a local catchment (restaurant, salon, gym,
clinic). **This is the method generic TAM tools get most wrong** — they size a
neighborhood cafe against a national restaurant-industry TAM.

### Inputs (all sourced, none invented)
- **Geocode** the address → lat/lng + Census tract/county. *Source: US Census Geocoder.*
- **Catchment demographics** — households + median household income within the
  trade area. *Source: US Census ACS 5-year.*
- **Category spend per household** — annual spend on the relevant category.
  *Source: BLS Consumer Expenditure Survey (e.g. "food away from home" ≈ $3,600/hh/yr).*
- **Competition density** — count of competing POIs in the catchment radius.
  *Source: OpenStreetMap Overpass.*

### Trade area
Default = a drive-time isochrone (OSRM) or a radius proxy (urban 1.5mi / suburban
3mi / rural 8mi) when isochrone is unavailable. The radius proxy is explicitly
flagged as lower-confidence.

### Formulas
```
TAM_local = households_in_catchment × annual_category_spend_per_household
SAM_local = TAM_local × addressable_fraction        # segment/positioning fit
fair_share = SAM_local / (n_competitors + 1)         # equal-share baseline
SOM_y1   = fair_share × capture_ramp                 # ramp 0.3–0.7 of fair share, yr 1
```

### Triangulation (independent second estimate)
```
SOM_capacity = seats × turns_per_day × avg_check × operating_days × utilization
```
Demand-side (`SOM_y1`) and supply-side (`SOM_capacity`) must land within a stated
band. Divergence > 2× raises a flag and widens the confidence interval.

### Worked example — farm-to-table restaurant, Echo Park, LA
```
catchment (2mi radius proxy, urban)   ≈ 38,000 households       [ACS]
food-away-from-home spend / hh / yr   ≈ $3,600                  [BLS CEX]
TAM_local = 38,000 × $3,600           ≈ $136.8M                 [formula]
addressable_fraction (upscale, ~12%)  = 0.12                    [positioning]
SAM_local = $136.8M × 0.12            ≈ $16.4M
competing sit-down restaurants in 2mi ≈ 140                     [OSM Overpass]
fair_share = $16.4M / 141             ≈ $116k
capture_ramp (strong concept, yr1)    = 0.5
SOM_y1 = $116k × 0.5                   ≈ $58k/yr  →  flag: low vs viable restaurant
  ↑ triangulate against capacity:
  40 seats × 1.5 turns × $55 × 310 days × 0.6 util ≈ $612k/yr
  → 10× divergence ⇒ fair-share model understates (catchment too tight /
    addressable_fraction too low) ⇒ widen CI, surface both, do NOT silently pick.
```
The point of the example: the engine **shows its work and flags the divergence**
instead of emitting one confident wrong number. That is the product.

---

## Method 2 — Per-location × rollout (`size_regional`) — spec

```
SOM_per_location  = (Method 1 applied to a representative site)
SOM_regional      = SOM_per_location × locations_year_n × maturity_curve
```
Rollout schedule and maturity curve are inputs with stated assumptions.

---

## Method 3 — Top-down ÷ bottom-up (`size_national_digital`) — refactor existing

Keep the current `market_sizing.py` engine but enforce the invariants:
```
TAM_topdown   = industry_revenue × segment_fraction          [analyst/industry source]
TAM_bottomup  = n_target_accounts × annual_contract_value     [Census CBP / firmographics]
TAM           = reconcile(topdown, bottomup)                  # must agree within band
SAM           = TAM × (geo_reachable × segment_fit)
SOM           = SAM × realistic_share(yr1..yr3)               # capped by GTM capacity
```
`n_target_accounts` comes from Census County Business Patterns (firm counts by
NAICS × size band), not an LLM guess.

---

## The validation gate (`validate_numbers`)

Mandatory before any sizing Evidence ships. Returns `{ok, flags[], checks[]}`.

Hard checks (failing → `ok=False`):
- `SOM ≤ SAM ≤ TAM`
- every headline figure has a non-empty `source`
- implied market share ≤ 100%
- `SOM ≤ catchment_spend` (hyperlocal) / `SOM ≤ SAM` (digital)

Soft checks (flag, don't fail):
- triangulation divergence > 2×
- radius proxy used instead of isochrone
- any input older than 3 years (ACS/BLS vintage)

No sizing skill returns a result that bypasses this gate.

---

## Data sources (all free / open)

| Source | Use | Key? |
|---|---|---|
| US Census Geocoder | address → lat/lng + tract | no |
| US Census ACS 5-yr | households, income, population | optional (higher limits) |
| US Census CBP | firm counts by NAICS × size (digital bottom-up) | optional |
| BLS Consumer Expenditure | category spend per household | no (published tables) |
| BEA Regional | regional income/GDP cross-check | no |
| OSM Overpass | competition density (POI counts) | no |
| OSRM (public/self-host) | drive-time isochrone | no |

License + rate-limit notes tracked in `STACK.md`. All calls go through
`scrape.http.request` (cached, throttled, stale-on-error).
