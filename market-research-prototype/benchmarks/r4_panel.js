export const meta = {
  name: 'r4-audit-panel',
  description: 'R4 ring: rubric-scored audit of the fresh corpus, adversarially verified (trend vs 26%/6)',
  phases: [{ title: 'Score' }, { title: 'Verify' }],
}

const CELLS = {
  type: 'object',
  properties: {
    cells: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          cell: { type: 'string' },
          verdict: { type: 'string', enum: ['PASS', 'WARN', 'FAIL'] },
          note: { type: 'string' },
          quote: { type: 'string' },
          severity: { type: 'string', enum: ['CRITICAL', 'HIGH', 'MED'] },
        },
        required: ['cell', 'verdict', 'note'],
      },
    },
  },
  required: ['cells'],
}
const VERDICT = {
  type: 'object',
  properties: { refuted: { type: 'boolean' }, reason: { type: 'string' } },
  required: ['refuted', 'reason'],
}

const REPO = '/Users/charlieyan/Downloads/castor-advisories/market-research-prototype'
const ARGS = typeof args === 'string' ? JSON.parse(args) : args
const DIR = ARGS.corpusDir
const GROUPS = [
  { key: 'sizing', rows: `
R1 Market-scale routing: classified to the right scale; sizing method matches (trade-area vs national vs regional). Client-services/agencies must NOT be hyperlocal.
R2 TAM: right method for the scale; magnitude plausible; sourced or labeled unsourced; calculation shown.
R3 SAM/SOM: funnel ordered SOM<=SAM<=TAM; SOM plausible for ONE unit; capacity-anchored where physical.`,
    hint: 'JSON: market_scale, market_sizing (tam/sam/som, validation, sources_to_validate). HTML: the market-sizing section.' },
  { key: 'pricing-econ', rows: `
R4 Business-model routing: classified correctly; NOT defaulted to subscription wrongly.
R5 Pricing: the right UNIT (per drink / per seat / per project / take-rate); coherent with stated price and WTP; benchmark labels match the model (no "/month per" on per-unit ventures).
R6 Unit economics: right FRAMEWORK (retail margin/covers vs CLV:CAC vs take-rate); no SaaS framing on non-SaaS; at-SOM profitability claim coherent with the scenario table.
R7 Financials: revenue basis matches the model (covers x check vs subscribers x ARPU vs GMV x take); scenario table coherent with the economics claims.`,
    hint: 'JSON: business_model_kind, pricing (psm, benchmark), economics (incl. at_som_volume), financials.scenarios. HTML: pricing + economics + financials sections.' },
  { key: 'comp-consumer', rows: `
R8 Competitors: real companies, relevant to the category, geographically appropriate (hyperlocal ventures need NEARBY venues); no near-duplicate entries counting one company twice; count consistent everywhere it appears.
R9 Consumer/WTP: right unit for the model; coherent band (low<=median<=high, no fake single-point band); connects to the recommended price.
R10 Differentiators: grounded in actual competitor data from THIS report, not generic filler.`,
    hint: 'JSON: discover (ranked_opportunities, geo_sourced), consumer_research.synthesis.willingness_to_pay, differentiators. HTML: competitor + consumer sections.' },
  { key: 'coherence', rows: `
R11 Viability: the score's reasoning is consistent with the report's own numbers; cites the right business model; no contradiction with sizing/economics.
R12 Integrity: validation gate state honest (failed validation => numbers withheld/flagged); provenance/trace present; "sourced N/N" claims accurate; any single number (SOM, price, competitor count) has ONE value everywhere it appears.`,
    hint: 'JSON: viability, validation, market_sizing.validation/publishable, _trace presence. HTML: exec summary vs body consistency.' },
]

const scorePrompt = (slug, g) => `You are an independent auditor (NOT the builder) scoring ONE
market-research report against a rubric group. Venture artifacts:
- Result JSON: ${DIR}/${slug}.json   (the pipeline's full result under key "result")
- Rendered report: ${DIR}/${slug}.html
Repo (for context only, do not fix anything): ${REPO}

Score EXACTLY these rubric cells:
${g.rows}
Where to look: ${g.hint}

Rules of evidence: read the actual artifacts (use Bash/python to slice the JSON; grep the HTML for
rendered claims). Verdicts: PASS = a paying human would trust it; WARN = plausible but weak;
FAIL = a paying human would distrust the report. Every FAIL needs severity — CRITICAL only for
fabrication, a wrong-model/wrong-scale headline, or numbers that contradict each other in the same
report; HIGH for a real defect a buyer would catch; MED otherwise. Every WARN/FAIL needs the
offending quote (short). Return one entry per rubric cell listed above — no more, no fewer.`

const refutePrompt = (slug, f) => `Adversarial verification (skeptic). A rubric auditor flagged this
finding on report ${slug} (artifacts: ${DIR}/${slug}.json + .html):
CELL ${f.cell} [${f.severity}]: ${f.note}
QUOTE: ${f.quote || '(none)'}
Read the artifacts yourself and try to REFUTE it. Default: refuted=true (not-a-bug) unless a paying
human reading this report would genuinely distrust it because of this specific issue. A stylistic
nit, a defensible methodological choice, or a correctly-disclosed limitation is NOT a bug.`

phase('Score')
const scored = await pipeline(
  ARGS.ventures,
  (slug) => parallel(GROUPS.map(g => () =>
    agent(scorePrompt(slug, g), { label: `score:${slug}:${g.key}`, phase: 'Score', schema: CELLS })))
    .then(rs => ({ slug, cells: rs.filter(Boolean).flatMap(r => r.cells) })),
  (sc, slug) => {
    const serious = sc.cells.filter(c => c.verdict === 'FAIL'
      && (c.severity === 'CRITICAL' || c.severity === 'HIGH'))
    if (!serious.length) return { ...sc, verified: [] }
    return parallel(serious.map(f => () =>
      agent(refutePrompt(sc.slug, f), { label: `verify:${sc.slug}:${f.cell}`, phase: 'Verify', schema: VERDICT })
        // A dead verifier (v == null) is NOT the same as "not refuted". Track it
        // explicitly so a truncated run can never masquerade as a confirmed finding.
        .then(v => ({ ...f, verifier_ran: !!v,
                      refuted: v ? v.refuted === true : false,
                      refute_reason: v ? v.reason : 'VERIFIER DIED — finding UNVERIFIED' }))))
      .then(vs => ({ ...sc, verified: vs }))
  },
)

const rows = scored.filter(Boolean)
let pass = 0, warn = 0, fail = 0, total = 0
let criticalsConfirmed = 0, highConfirmed = 0, verificationGaps = 0
const gaps = []
const perVenture = {}
for (const v of rows) {
  const vByKey = new Map((v.verified || []).map(x => [x.cell + '|' + x.note, x]))
  const cells = v.cells.map(c => {
    const ver = vByKey.get(c.cell + '|' + c.note)   // undefined = wasn't a serious finding (PASS/WARN/MED)
    const wasRefuted = ver ? ver.refuted : false
    const verifierDied = ver ? !ver.verifier_ran : false
    const verdict = (c.verdict === 'FAIL' && wasRefuted) ? 'WARN' : c.verdict
    return { ...c, verdict, refuted: wasRefuted, verifier_died: verifierDied }
  })
  for (const c of cells) {
    total += 1
    if (c.verifier_died) { verificationGaps += 1; gaps.push(`${v.slug}:${c.cell}`) }
    if (c.verdict === 'PASS') pass += 1
    else if (c.verdict === 'WARN') warn += 1
    else {
      fail += 1
      if (c.severity === 'CRITICAL') criticalsConfirmed += 1
      if (c.severity === 'HIGH') highConfirmed += 1
    }
  }
  perVenture[v.slug] = cells
}
// A FAIL whose verifier died is NOT adversarially confirmed — the run is only VALID
// with zero verification gaps. Surface loudly rather than silently counting it.
const valid = verificationGaps === 0
log(`R4 panel: ${pass}/${total} PASS (${Math.round(1000 * pass / total) / 10}%), ` +
    `${criticalsConfirmed} CRIT / ${highConfirmed} HIGH confirmed, ` +
    `${verificationGaps} VERIFICATION GAPS ${valid ? '(VALID)' : '(INVALID — re-run to close gaps)'}`)
return {
  valid, verification_gaps: verificationGaps, gap_cells: gaps,
  pass_pct: Math.round(1000 * pass / total) / 10,
  cells: { pass, warn, fail, total },
  criticals_confirmed: criticalsConfirmed,
  highs_confirmed: highConfirmed,
  per_venture: perVenture,
}
