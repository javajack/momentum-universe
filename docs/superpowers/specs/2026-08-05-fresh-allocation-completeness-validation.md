# Fresh-Allocation Model — Completeness Re-Validation

**Branch:** `feature/fresh-allocation`  ·  **Status:** validation (go/no-go), no code yet.

## The constraint that changes everything

The user will **never input existing holdings** and **never track a live
portfolio**. Every interaction is stateless:

> "Here is ₹X (and/or ₹Y for swing). Tell me what to buy and approximate
> quantities."

The system is an **allocation advisor**, not a portfolio manager. It must be a
**new, separate menu option** — existing options (10 rebalance-from-holdings,
11 swing plan) stay so the user chooses per use.

This constraint is actually *more* aligned with the repo's self-contained
research identity than the parent's live-rebalance model — it removes all
state, holdings, and reconciliation from scope.

## Re-scoring the four gaps under this constraint

| # | Gap (original framing) | Under fresh-allocation-only | Verdict |
|---|---|---|---|
| 1 | Unified/combined **portfolio** (cross-sleeve overlap, combined DD, live risk) | Collapses to a **stateless combined allocation planner** (momentum ₹X + swing ₹Y → one breakup, overlap flagged). The stateful "portfolio manager" half is moot — no state is ever held. | **PARTIAL — build the stateless planner; DROP the stateful manager** |
| 2 | Walk-forward / **OOS validation** | Unchanged, and *more* important: repeated fresh deployments rely on the picks, so the edge must be robust out-of-sample. Regime-switcher + swing partition were both in-sample selected (flagged twice this session). | **BUILD — high priority, pure research** |
| 3 | Systematic **meta-allocation** (momentum vs swing split) | The user *enters* the amounts, so a systematic allocator is unnecessary. At most a one-line **regime hint** in the allocation output. | **DROP as a system; keep a cheap regime hint** |
| 4 | Liquidity-aware **sizing** + attribution | Sizing-vs-ADV still matters (a "buy 576 shares" answer must be fillable) → fold an ADV check into the planner. Attribution is a research nicety. | **BUILD the ADV check (small); DEFER attribution** |
| — | **Data-integrity fetch fix** (corp-action refresh + rename + health-check) | Unchanged — needed for self-maintenance regardless of allocation model. | **BUILD — independent** |

## The concrete new feature (the useful core of Gap 1)

**New menu option: "Fresh Allocation Plan"** (stateless; separate from 10/11)

- Input: momentum ₹ (0 to skip) and/or swing ₹ (0 to skip).
- Momentum leg: reuse `plan_rebalance(holdings={})` → target weights + approx qty.
- Swing leg: reuse `swing_allocation_plan` (the 3+2 slot planner).
- **Overlap netting:** if a ticker appears in *both* legs, flag it and show
  combined exposure (the one genuinely useful bit of "portfolio" thinking for
  fresh money — prevents unknowing double-buys).
- **ADV-aware qty note:** flag any slot whose ₹ value is a large % of the
  stock's 20-day ADV (fill-risk warning).
- **Regime hint (cheap):** one line from `market_state` — e.g. "regime
  DEFENSIVE → momentum sleeve auto-throttled; consider a smaller momentum ₹."
- Output: unified table — sleeve, ticker, weight, approx qty, ₹ value, stop,
  rotation days (swing), overlap flag, cash residue per sleeve.
- Also expose as an MCP tool `fresh_allocation` so Codex/Claude can call it.

## Recommended scope (what's actually worth doing)

**BUILD now (this branch):**
1. **Fresh Allocation Plan** menu option + MCP tool (stateless combined
   planner, overlap netting, ADV flag, regime hint). — *the thing the user
   asked for; high value, low effort (wraps existing actions).*
2. **Data-integrity fetch fix** (corp-action refresh + rename detection +
   post-fetch health-check; menu-2 verbose window). — *self-maintenance.*

**BUILD next (separate branch, research):**
3. **Tuning + Walk-forward / OOS harness** (one thing, not two). Tuning the
   strategy well means sweeping parameters and reading CAGR/returns — but done
   on IN-SAMPLE data that is the overfitting trap (config already carries
   ad-hoc scars: `rebalance_days: 30 # sweep-optimised`, `target_positions: 15
   # 15 best Sharpe`). Do it right: sweep on a TRAIN window, report
   CAGR / Sharpe / MaxDD / **Calmar** for train AND an untouched TEST window,
   keep only parameters that hold on test.
   - Knobs to sweep: `rank_range`, `rebalance_days`, `target_positions`,
     regime bull/caution thresholds, stop levels, the emerging-scan
     thresholds, and the swing slot split (3+2 was chosen on one window).
   - Deliverable: a `tune`/`sweep` action + report that ranks parameter sets
     by test-window Calmar (not in-sample CAGR), so tuning cannot overfit by
     construction. Re-validates the regime-switcher default and swing
     partition out-of-sample as its first outputs.

**DROP / DEFER (not worth it under this model):**
- Stateful portfolio manager (Gap 1 heavy) — never holds state.
- Systematic meta-allocator (Gap 3) — user sets the split.
- Return attribution (Gap 4b) — research nicety.

## Effort / value snapshot

| Item | Effort | Value (under this model) | Decision |
|---|---|---|---|
| Fresh Allocation Plan (opt + MCP) | Low (wraps existing) | **High** — the daily-use tool | BUILD now |
| Data-integrity fetch fix | Medium | High — correctness over time | BUILD now |
| Tuning + walk-forward/OOS harness | Medium-High | High — tune it well WITHOUT overfitting; trust the picks | BUILD next |
| Stateful portfolio manager | High | ~Zero | DROP |
| Systematic meta-allocator | Medium | Low | DROP |
| Attribution | Medium | Low | DEFER |

## Bottom line

Under the fresh-allocation-only model, **most of the "portfolio layer" work I
proposed is NOT worth doing** — it assumed live state the user will never
hold. What *is* worth doing shrinks to a clean, high-value set: the stateless
**Fresh Allocation Plan** option, the **data-integrity** fix, and (next) the
**OOS validation** harness. The rest is dropped, honestly.
