"""momentum-universe — unified interactive CLI (thin shell over the actions layer).

This menu only gathers inputs, calls one function from `fortress.actions`, and
renders the result. All logic lives in the actions layer (pure, testable).
Credential-free — every feature runs on the vendored data, no broker.
"""
from __future__ import annotations

from datetime import date, datetime

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table

from fortress.config import Config, load_config
from fortress import actions as A

console = Console()

# Grouped into four jobs: manage data → research → discover names → size a plan.
# A 2-tuple is a section header; a 3-tuple is a selectable option.
MENU = [
    ("§", "DATA"),
    ("1", "Universe update", "fetch + full sync + integrity check"),
    ("2", "Universe query", "search · screen · lists · climbers"),
    ("3", "Settings", "strategy · universe · rank band"),
    ("§", "RESEARCH"),
    ("4", "Backtest", "historical sim, custom window"),
    ("5", "Market phases", "per-phase returns vs NIFTY"),
    ("6", "Market / regime check", "current regime, VIX, allocation"),
    ("§", "DISCOVER"),
    ("7", "Momentum scan", "momentum leaders + metrics"),
    ("8", "Emerging momentum scan", "rank-climbing, pre-run names"),
    ("§", "ALLOCATE"),
    ("9", "Fresh allocation plan", "enter ₹ → momentum + swing buy plan"),
    ("§", ""),
    ("0", "Exit", ""),
]


class App:
    """Holds the current in-memory Config; each handler is thin."""

    def __init__(self, config_path: str = "config.yaml") -> None:
        self.config: Config = load_config(config_path)

    # ---- rendering helpers -------------------------------------------------
    def _menu(self) -> None:
        cfg = self.config
        rr = cfg.universe.rank_range
        console.print()
        console.print(Panel.fit(
            f"[bold bright_cyan]MOMENTUM UNIVERSE[/]\n"
            f"[dim]{cfg.active_strategy} · v{cfg.universe.version[-1]} · "
            f"ranks {rr[0]}-{rr[1]}[/dim]",
            border_style="bright_blue", padding=(0, 3),
        ))

        def _new_table() -> Table:
            t = Table(show_header=False, box=None, padding=(0, 1))
            t.add_column("Key", style="bold cyan", justify="right", width=4)
            t.add_column("Option", style="white", width=24, no_wrap=True)
            t.add_column("Description", style="dim", no_wrap=True)
            return t

        t = None
        for row in MENU:
            if len(row) == 2:                       # section header / spacer -> flush
                if t is not None:
                    console.print(t)
                    t = None
                name = row[1]
                if name:
                    console.print(f"\n[bold yellow]  {name}[/]")
            else:
                k, opt, desc = row
                if t is None:
                    t = _new_table()
                t.add_row(k, opt, desc)
        if t is not None:
            console.print(t)

    # ---- handlers (thin: gather input -> action -> render) -----------------
    def universe_update(self) -> None:
        # Preview the pending window BEFORE the (possibly slow) network call.
        try:
            start, end, ndays = A.universe_update.pending_window(self.config.universe.version)
        except Exception:
            start, end, ndays = None, None, 0
        if start is not None:
            console.print(f"[dim]Pending sync window: [bold]{start} → {end}[/bold] "
                          f"(~{ndays} trading days behind).[/dim]")
        else:
            console.print("[dim]Prices are up to date — a fetch would sync nothing new.[/dim]")

        fetch = Prompt.ask("Fetch latest bhavcopy from NSE? (public, needs network) [y/N]",
                           default="n").lower() == "y"
        full = deep = False
        if fetch:
            console.print("[dim]Full sync refreshes corporate actions + detects renames, and "
                          "recomputes both v1 and v2 ranks — keeps adjusted prices correct.[/dim]")
            full = Prompt.ask("Run FULL sync (corporate actions + renames)? [Y/n]",
                              default="y").lower() == "y"
            if full:
                console.print("[dim]By default only symbols with a split/bonus footprint in the "
                              "new days are refreshed (fast, seconds). A DEEP sweep re-checks all "
                              "~2000 symbols (slow ~minutes) and also catches pure dividends — "
                              "run occasionally.[/dim]")
                deep = Prompt.ask("Also run a DEEP corporate-actions sweep (all symbols)? [y/N]",
                                  default="n").lower() == "y"

        with console.status("[green]updating universe (this can take a while for a full/deep sync)..."):
            res = A.update_universe(fetch=fetch, full=full, deep_actions=deep)

        console.print(f"[green]Universe rebuilt: {res.symbols:,} symbols, {res.rows:,} rows.[/green]")
        if res.window:
            console.print(f"[dim]synced window: {res.window[0]} → {res.window[1]} "
                          f"({res.pending_days} trading days)[/dim]")
        for k, v in res.steps.items():
            console.print(f"  [dim]{k:10}[/dim] {v}")

        # Data-integrity: unrecorded corporate actions polluting adjusted prices.
        if res.integrity_suspects:
            t = Table("Symbol", "Date", "close/prev", box=None,
                      title=f"⚠ {len(res.integrity_suspects)} unrecorded corporate-action suspect(s)")
            for s in res.integrity_suspects[:15]:
                t.add_row(s.symbol, s.date, f"{s.ratio:g}")
            console.print(t)
            tip = ("re-run with FULL sync (corporate actions) to record these"
                   if not res.full else
                   "these persist after a full refresh — likely genuine data gaps to inspect")
            console.print(f"[yellow]These are overnight moves beyond circuit limits with no "
                          f"adjustment record — {tip}.[/yellow]")
        else:
            console.print("[green]Data-integrity check: clean (adjustments in step with prices).[/green]")

    _TIER_LEGEND = ("[dim]tier = 6-month turnover-rank band (LARGE 1-100 · MID 101-250 · "
                    "SMALL 251-500 · MICRO 501-1000), NOT market cap. For true mcap, "
                    "cross-check StockEdge.[/dim]")

    def _rowtable(self, rows, title: str) -> None:
        t = Table("Rank", "Symbol", "₹Cr/day", "Tier", "Sector", box=None, title=title)
        for r in rows:
            t.add_row(str(r.rank), r.symbol, f"{r.turnover / 1e7:.1f}", r.tier,
                      (r.sector or "")[:18])
        console.print(t)

    def universe_query(self) -> None:
        UQ = A.universe_query
        v = self.config.universe.version
        kind = Prompt.ask("Query", choices=["search", "screen", "lists", "climbers", "coverage"],
                          default="search")

        if kind == "coverage":
            console.print(UQ.coverage(v))
            return

        d = _ask_date("As-of date", date.today())

        if kind == "search":
            term = Prompt.ask("Search term (part of a ticker, e.g. BAJAJ)").strip()
            rows, asof = UQ.search(term, d, v)
            if not rows:
                console.print(f"[yellow]No ranked ticker contains '{term}' as of {asof}.[/yellow]")
                return
            self._rowtable(rows, f"'{term}' — {len(rows)} match(es), as-of {asof}")
            console.print(self._TIER_LEGEND)

        elif kind == "screen":
            lo = int(Prompt.ask("Rank band — low", default="1"))
            hi = int(Prompt.ask("Rank band — high", default="1000"))
            tier = Prompt.ask("Tier filter", choices=["all"] + [t.lower() for t in UQ.TIER_NAMES],
                              default="all")
            mint = float(Prompt.ask("Min turnover (₹cr/day)", default="0"))
            top = int(Prompt.ask("Show top N", default="30"))
            rows, bd, asof, total = UQ.screen(d, v, rank_lo=lo, rank_hi=hi,
                                              tier=tier, min_turnover_cr=mint, top=top)
            if not rows:
                console.print("[yellow]No stocks match those criteria.[/yellow]")
                return
            self._rowtable(rows, f"Screen — showing {len(rows)} of {total} matches, as-of {asof}")
            crumb = " · ".join(f"{n} {bd[n]}" for n in UQ.TIER_NAMES if bd[n])
            console.print(f"[dim]{total} match · breakdown: {crumb}[/dim]")
            console.print(self._TIER_LEGEND)

        elif kind == "lists":
            tier = Prompt.ask("Which list", choices=[t.lower() for t in UQ.TIER_NAMES],
                              default="mid")
            top = int(Prompt.ask("Show top N (0 = all)", default="40"))
            rows, asof, total = UQ.tier_members(d, tier, v, top=top or 10_000)
            self._rowtable(rows[:top] if top else rows,
                           f"{tier.upper()} list — {total} names, as-of {asof}")
            console.print(self._TIER_LEGEND)

        elif kind == "climbers":
            console.print("[dim]Deep-tail turnover-rank climbers, enriched with price momentum — "
                          "hunting EARLY multibaggers (liquidity + price moving, runway ahead).[/dim]")
            lb = int(Prompt.ask("Lookback (months)", default="6"))
            # Which CURRENT-rank band to hunt climbers in — a cap tier or a custom range.
            focus = Prompt.ask("Focus band",
                               choices=["tail"] + [t.lower() for t in UQ.TIER_NAMES] + ["custom"],
                               default="tail")
            if focus == "tail":
                rank_lo, rank_hi = 251, 2000            # deep tail (outside LARGE/MID)
            elif focus == "custom":
                rank_lo = int(Prompt.ask("Rank band — low", default="1000"))
                rank_hi = int(Prompt.ask("Rank band — high", default="1500"))
            else:
                rank_lo, rank_hi = next((lo, hi) for n, lo, hi in UQ.TIER_BANDS
                                        if n == focus.upper())
            mint = float(Prompt.ask("Min turnover (₹cr/day, tradeable floor)", default="10"))
            top = int(Prompt.ask("Show top N", default="25"))
            excl = Prompt.ask("Exclude recent IPOs? (float onboarding ≠ markup) [Y/n]",
                              default="y").lower() == "y"
            console.print("[dim]Preset — accumulation: EARLY (still basing, above 200SMA, not yet "
                          "run) · runway: not-yet-parabolic · all: unfiltered (mostly already-run).[/dim]")
            preset = Prompt.ask("Preset", choices=["accumulation", "runway", "all"],
                                default="accumulation")
            kw = {}
            if preset == "accumulation":
                kw = dict(require_above_200sma=True, max_6m_return=30.0, max_12m_return=60.0)
            elif preset == "runway":
                kw = dict(max_12m_return=100.0)
            with console.status(f"[green]scanning rank velocity + price momentum "
                                f"(ranks {rank_lo}-{rank_hi}, {preset})..."):
                rows, now_asof, past_asof, _pm = UQ.rank_velocity(
                    d, lookback_months=lb, rank_lo=rank_lo, rank_hi=rank_hi,
                    min_turnover_cr=mint, top=top, exclude_ipos=excl, **kw)
            if not rows:
                console.print("[yellow]No climbers match those criteria.[/yellow]")
                return
            t = Table("Symbol", "then→now", "Δclimb", "IPO?", "6M", "12M", "OffHi", "200",
                      "₹Cr/day", "Tier", "Sector", box=None,
                      title=f"Rank climbers, ranks {rank_lo}-{rank_hi} [{preset}] — "
                            f"{past_asof} → {now_asof} ({len(rows)} names)")
            for r in rows:
                traj = f"new→{r.rank_now}" if r.new_entrant else f"{r.rank_past}→{r.rank_now}"
                delta = f"≥{r.velocity}" if r.new_entrant else f"+{r.velocity}"
                r6 = f"{r.ret_6m_pct:+.0f}%" if r.ret_6m_pct is not None else "—"
                r12 = f"{r.ret_12m_pct:+.0f}%" if r.ret_12m_pct is not None else "—"
                offhi = f"{r.off_high_pct:.0f}%" if r.off_high_pct is not None else "—"
                sma = "✓" if r.above_200sma else ("·" if r.above_200sma is not None else "—")
                t.add_row(r.symbol, traj, delta, "IPO" if r.is_ipo else "", r6, r12, offhi, sma,
                          f"{r.turnover / 1e7:.1f}", r.tier, (r.sector or "")[:16])
            console.print(t)
            console.print("[magenta]⚑ Research watchlist — NOT a buy list. EARLY sweet spot: rank "
                          "climbing + above 200SMA + price still BASING (low 6M) + near its high "
                          "(OffHi ~0). Confirm the catalyst via StockEdge / news.[/magenta]")
            console.print("[dim]Δclimb ≥ = new entrant · 6M/12M = price return · OffHi = % below "
                          "52w-high (0 = at high; −30 = faded from a peak) · 200 ✓ = above 200SMA "
                          "· IPO = recent listing (float onboarding, not a markup).[/dim]")

    def settings(self) -> None:
        """Session settings: strategy + universe version + rank band. These drive
        backtest, scans and allocation. Enter through the defaults to keep the
        validated setup (dual_momentum · v2 · ranks 201-600)."""
        rr = self.config.universe.rank_range
        console.print(f"[dim]Current: strategy [bold]{self.config.active_strategy}[/bold] · "
                      f"universe [bold]v{self.config.universe.version[-1]}[/bold] · "
                      f"ranks [bold]{rr[0]}-{rr[1]}[/bold]. Press Enter to keep each.[/dim]")
        s = Prompt.ask("Strategy", choices=list(A.selection.VALID_STRATEGIES),
                       default=self.config.active_strategy)
        v = Prompt.ask("Universe version  [dim](v2 = momentum-grade, recommended)[/dim]",
                       choices=["v1", "v2"], default=self.config.universe.version)
        lo = int(Prompt.ask("Rank band — low", default=str(rr[0])))
        hi = int(Prompt.ask("Rank band — high", default=str(rr[1])))
        try:
            self.config = A.apply_selection(self.config, strategy=s, version=v, rank_range=[lo, hi])
            nr = self.config.universe.rank_range
            console.print(f"[green]✓ {self.config.active_strategy} · "
                          f"v{self.config.universe.version[-1]} · ranks {nr[0]}-{nr[1]}[/green]")
        except ValueError as e:
            console.print(f"[red]{e}[/red]")

    def backtest(self) -> None:
        start = _ask_date("Start date", date(2013, 1, 1))
        end = _ask_date("End date", date.today())
        with console.status(f"[green]running backtest ({self.config.active_strategy})..."):
            r = A.run_backtest(self.config, start, end)
        console.print(Panel(
            f"Return [bold]{r.total_return:+.1%}[/bold]   CAGR [bold]{r.cagr:.1%}[/bold]   "
            f"Sharpe [bold]{r.sharpe_ratio:.2f}[/bold]   MaxDD [bold]{r.max_drawdown:.1%}[/bold]   "
            f"trades {len(r.trades)}",
            title=f"Backtest {start} → {end}", style="green",
        ))
        console.print("[dim]CAGR = annualised return · MaxDD = worst peak-to-trough drop · "
                      "Sharpe = return per unit of risk (higher is better). Survivorship-free, "
                      "modelled costs — research only, not a live result.[/dim]")

    def market_phases(self) -> None:
        with console.status(f"[green]running {len(A.MARKET_PHASES)}-phase analysis ({self.config.active_strategy})... (~minutes)"):
            rep = A.run_market_phases(self.config)
        console.print(Panel(
            f"Return [bold]{rep.overall_return:+.1%}[/bold]   CAGR [bold]{rep.cagr:.1%}[/bold]   "
            f"Sharpe [bold]{rep.sharpe:.2f}[/bold]   MaxDD [bold]{rep.max_dd:.1%}[/bold]   "
            f"₹{rep.initial_capital:,.0f} → ₹{rep.final_value:,.0f}",
            title=f"Market Phases ({self.config.active_strategy})", style="green",
        ))
        t = Table("Phase", "Type", "Strat", "MaxDD", "NIFTY", "α", box=None)
        for p in rep.phases:
            nifty = f"{p.nifty_return:+.1%}" if p.nifty_return is not None else "n/a"
            alpha = f"{p.alpha:+.1%}" if p.alpha is not None else "n/a"
            acolor = "green" if (p.alpha or 0) >= 0 else "red"
            t.add_row(p.name, p.phase_type, f"{p.strat_return:+.1%}",
                      f"{p.max_dd:.1%}", nifty, f"[{acolor}]{alpha}[/{acolor}]")
        console.print(t)

    def market_check(self) -> None:
        with console.status("[green]reading latest market state..."):
            ms = A.current_market_state(self.config)
        color = {"bullish": "green", "normal": "cyan", "caution": "yellow", "defensive": "red"}.get(ms.regime, "white")
        console.print(Panel(
            f"[{color}]REGIME: {ms.regime.upper()}[/{color}]   "
            f"52W pos {ms.nifty_52w_position:.0%}   VIX {ms.vix_level:.1f}   "
            f"3M {ms.nifty_3m_return:+.1%}\n"
            f"Allocation: Equity {ms.equity_weight:.0%} / Gold {ms.gold_weight:.0%}   "
            f"(stress {ms.stress_score:.2f})",
            title=f"Market state as of {ms.as_of}", style=color,
        ))

    def momentum_scan(self) -> None:
        top = int(Prompt.ask("Show top N", default="20"))
        with console.status(f"[green]ranking universe ({self.config.active_strategy})..."):
            res = A.momentum_scan(self.config, top_n=top)
        console.print(Panel(
            f"strategy [bold]{res.strategy}[/bold]   universe [bold]v{res.version[-1]} "
            f"ranks {list(res.rank_range)}[/bold]   as of [bold]{res.as_of}[/bold]   "
            f"[dim]{res.total_passing} names passed entry filters[/dim]",
            title="Momentum scan", style="cyan",
        ))
        t = Table("#", "Ticker", "Sector", "Score", "52W%", "6M", "12M", "₹Cr/day", "200SMA", box=None)
        for i, s in enumerate(res.stocks, 1):
            t.add_row(
                str(i), s.ticker, (s.sector or "")[:16], f"{s.score:.2f}",
                f"{s.high_52w_proximity:.0%}", f"{s.return_6m:+.0%}", f"{s.return_12m:+.0%}",
                f"{s.daily_turnover / 1e7:.1f}", "✓" if s.above_200sma else "·",
            )
        console.print(t)
        console.print("[dim]Score = strategy momentum score (higher = stronger) · 52W% = nearness "
                      "to 52-week high · ₹Cr/day = liquidity · 200SMA ✓ = uptrend. These have "
                      "already run — mind valuation, and diligence before buying.[/dim]")

    def emerging_scan(self) -> None:
        top = int(Prompt.ask("Show top N", default="15"))
        with console.status("[green]scanning rank trajectories + early momentum (~2-3 min)..."):
            res = A.emerging_scan(self.config, top_n=top)
        console.print(Panel(
            f"universe [bold]v{res.version[-1]}[/bold]   band [bold]ranks {res.band[0]}-{res.band[1]}[/bold]   "
            f"as of [bold]{res.as_of}[/bold]   [dim]{res.candidates_scanned} rank-climbers scanned, "
            f"{res.total_passing} passed early-momentum filters[/dim]\n"
            f"[dim]stocks EARLY in a move (liquidity rank climbing + breaking toward highs, "
            f"12m return capped) — the pre-run complement to menu 7. Diligence each pick before acting.[/dim]",
            title="Emerging momentum scan", style="cyan",
        ))
        t = Table("#", "Ticker", "Sector", "Rank 2y→now", "3M", "6M", "12M",
                  "52W%", "Accel", "Vol", "₹Cr/day", box=None)
        for i, s in enumerate(res.stocks, 1):
            climb = f"{s.rank_2y if s.rank_2y is not None else 'new'}→{s.rank_now}"
            t.add_row(
                str(i), s.symbol, (s.sector or "")[:14], climb,
                f"{s.ret_3m_pct:+.0f}%", f"{s.ret_6m_pct:+.0f}%", f"{s.ret_12m_pct:+.0f}%",
                f"{s.prox_52w_high:.2f}", f"{s.accel_pct:+.0f}%", f"{s.volatility:.2f}",
                f"{s.daily_turnover / 1e7:.0f}",
            )
        console.print(t)
        console.print("[dim]Rank 2y→now = liquidity-rank climb (falling number = getting more "
                      "liquid) · Accel = recent-leg strength · Vol = annualised volatility. Early "
                      "movers — weigh cash-flow quality & pledge heavily in diligence.[/dim]")

    def fresh_allocation(self) -> None:
        console.print("[dim]Stateless plan — enter fresh amounts to deploy (no existing "
                      "holdings needed). Type 0 for a sleeve to skip it.[/dim]")
        mom = float(Prompt.ask("Momentum ₹ to deploy", default="1000000"))
        mom_n = None
        if mom > 0:
            mom_n = int(Prompt.ask("  → how many momentum stocks to shortlist",
                                   default=str(self.config.position_sizing.target_positions)))
        sw = float(Prompt.ask("Swing ₹ to deploy", default="500000"))
        hb, rs = 3, 2
        if sw > 0:
            hb = int(Prompt.ask("  → swing: high-base (breakout) slots", default="3"))
            rs = int(Prompt.ask("  → swing: RSI-pullback slots", default="2"))
        console.print("[dim]Climbers = SATELLITE sleeve: accumulation rank-climbers (1-500 band, "
                      "still basing), ~5 concentrated names, QUARTERLY rotation. Higher-risk "
                      "alpha (validated ~+20%/yr vs the band) — size small.[/dim]")
        cl = float(Prompt.ask("Climbers ₹ to deploy (satellite)", default="0"))
        cl_n = 8
        if cl > 0:
            cl_n = int(Prompt.ask("  → max climbers to hold (cap; ~5 usually qualify)", default="8"))
        if mom <= 0 and sw <= 0 and cl <= 0:
            console.print("[yellow]Nothing to allocate — all amounts were 0.[/yellow]")
            return

        with console.status("[green]building fresh allocation..."):
            plan = A.fresh_allocation(self.config, momentum_capital=mom, swing_capital=sw,
                                      climbers_capital=cl, momentum_top_n=mom_n,
                                      hb_slots=hb, rsi_slots=rs, climbers_top_n=cl_n)

        style = {"defensive": "red", "caution": "yellow"}.get(plan.regime, "green")
        console.print(Panel(
            f"as of [bold]{plan.as_of}[/bold]    regime [bold]{plan.regime.upper()}[/bold]    "
            f"total [bold]{_inr(plan.momentum_capital + plan.swing_capital + plan.climbers_capital)}[/bold]\n"
            f"[dim]{plan.regime_hint}[/dim]",
            title="Fresh allocation plan", style=style,
        ))

        if plan.momentum_rows:
            deployed = sum(r.value for r in plan.momentum_rows)
            t = Table("Ticker", "Wt", "Qty", "Value ₹", "ADV%", "flag", box=None,
                      title=f"Momentum — {_inr(plan.momentum_capital)} across "
                            f"{len(plan.momentum_rows)} stocks")
            for r in plan.momentum_rows:
                advp = f"{r.adv_pct*100:.0f}%" if r.adv_pct is not None else "—"
                t.add_row(r.symbol, r.detail, f"{r.quantity:,}", f"{r.value:,.0f}", advp,
                          self._flag(r))
            console.print(t)
            if plan.defensive_rows:
                buf = "   ".join(f"{r.symbol} {r.detail} ({_inr(r.value)})"
                                 for r in plan.defensive_rows)
                console.print(f"  [dim]defensive buffer (regime overlay): {buf} — buy these "
                              f"ETFs separately for the gold/cash cushion[/dim]")
            console.print(f"  [dim]deployed {_inr(deployed)} · cash residue "
                          f"{_inr(plan.momentum_cash)}[/dim]")

        if plan.swing_rows:
            deployed = sum(r.value for r in plan.swing_rows)
            t = Table("Strategy", "Ticker", "Qty", "Value ₹", "Stop ₹", "Rotate≤", "ADV%", "flag",
                      box=None, title=f"Swing — {_inr(plan.swing_capital)} (high-base×{hb} + rsi2×{rs})")
            for r in plan.swing_rows:
                advp = f"{r.adv_pct*100:.0f}%" if r.adv_pct is not None else "—"
                t.add_row(r.detail, r.symbol, f"{r.quantity:,}", f"{r.value:,.0f}",
                          f"{r.stop:,.1f}" if r.stop else "—",
                          f"{r.rotation_days}d" if r.rotation_days else "—", advp, self._flag(r))
            console.print(t)
            console.print(f"  [dim]deployed {_inr(deployed)} · cash residue "
                          f"{_inr(plan.swing_cash)}[/dim]")

        if plan.climbers_rows:
            deployed = sum(r.value for r in plan.climbers_rows)
            t = Table("Ticker", "rank then→now", "Qty", "Value ₹", "Rotate≤", "ADV%", "flag",
                      box=None, title=f"Climbers (satellite) — {_inr(plan.climbers_capital)} across "
                                      f"{len(plan.climbers_rows)} names")
            for r in plan.climbers_rows:
                advp = f"{r.adv_pct*100:.0f}%" if r.adv_pct is not None else "—"
                t.add_row(r.symbol, r.detail, f"{r.quantity:,}", f"{r.value:,.0f}",
                          f"{r.rotation_days}d" if r.rotation_days else "—", advp, self._flag(r))
            console.print(t)
            console.print(f"  [dim]deployed {_inr(deployed)} · cash residue "
                          f"{_inr(plan.climbers_cash)} · quarterly rotation · higher-risk alpha, "
                          f"diligence the catalyst per name[/dim]")
        elif plan.climbers_capital > 0:
            console.print(f"  [yellow]Climbers: no accumulation candidates qualify right now "
                          f"(rare — the filter is strict). Hold the {_inr(plan.climbers_capital)} "
                          f"as cash or add to another sleeve.[/yellow]")

        if plan.overlaps:
            console.print(Panel(
                f"[bold]↔ {', '.join(plan.overlaps)}[/bold] appears in BOTH sleeves — you'd buy "
                "it twice. Consider keeping it in one sleeve only.", style="yellow"))
        console.print("[dim]Legend: ↔ = in both sleeves · ! = order is a large % of the stock's "
                      "daily volume (harder to fill). Quantities are approximate.\n"
                      "Next: diligence each name (options 7/8 give context; pair with the "
                      "StockEdge MCP) before placing orders. Re-run after any swing exit.[/dim]")

    @staticmethod
    def _flag(r) -> str:
        return ("↔" if r.overlap else "") + ("!" if r.adv_warn else "") or "·"

    # ---- loop --------------------------------------------------------------
    def run(self) -> None:
        while True:
            console.print()
            self._menu()
            choice = Prompt.ask("\nSelect option", default="0")
            handlers = {
                "1": self.universe_update, "2": self.universe_query,
                "3": self.settings,
                "4": self.backtest, "5": self.market_phases, "6": self.market_check,
                "7": self.momentum_scan, "8": self.emerging_scan,
                "9": self.fresh_allocation,
            }
            if choice == "0":
                console.print("[dim]bye[/dim]")
                return
            handler = handlers.get(choice)
            if handler is None:
                console.print("[red]invalid option[/red]")
                continue
            try:
                handler()
            except Exception as e:  # keep the menu alive on any action error
                console.print(f"[red]error: {e}[/red]")


def _ask_date(label: str, default: date) -> date:
    raw = Prompt.ask(f"{label} (YYYY-MM-DD)", default=default.isoformat())
    return datetime.fromisoformat(raw).date()


def _inr(x: float) -> str:
    """Indian-readable rupee amount: 1e5 -> ₹1.00L, 1e7 -> ₹1.00Cr."""
    x = float(x)
    if abs(x) >= 1e7:
        return f"₹{x / 1e7:.2f}Cr"
    if abs(x) >= 1e5:
        return f"₹{x / 1e5:.2f}L"
    return f"₹{x:,.0f}"


def main() -> None:
    App().run()


if __name__ == "__main__":
    main()
