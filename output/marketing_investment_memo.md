# Marketing investment decision — MKT-ALLOC-2026-001

**Recommendation:** CONDITIONAL APPROVAL

**Budget:** $37,202 current → $37,202 recommended

**Expected incremental orders:** 796.0 → 819.7 (3.0% modelled uplift)

## Decision

Keep total paid media spend unchanged, apply the channel floors and caps in the
decision packet, and treat every increase outside paid search as **test-gated**.
The allocation is optimized against a downside-weighted response, not the centre
forecast alone. It is a planning recommendation, not a realized outcome.

## Evidence boundary

Paid search is calibrated to the synthetic geo holdout. The other paid-channel
response curves are synthetic planning priors with wider uncertainty. The current
12-month history is intentionally not used to fit an MMM; the project requires
2–3 years of weekly data, controls, diagnostics and experiment calibration first.

## Release conditions

1. Growth VP owns the investment decision; Finance reviews budget neutrality.
2. Experimentation Lead validates power, pre-period fit and spillover risk.
3. Display, affiliate and paid-social changes do not become scale decisions until
   their required tests are complete.
4. Re-read after eight weeks and compare realized lift with the registered interval.

## Audit evidence

The machine-readable packet at `output/marketing_investment_decision.json` contains
the per-channel guardrails, named authorities, evidence class, required next action
and a SHA-256 digest over the canonical payload.
