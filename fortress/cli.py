"""momentum-universe — unified interactive CLI (thin shell over the actions layer).

This menu only gathers inputs, calls one function from `fortress.actions`, and
renders the result. All logic lives in the actions layer (pure, testable).
Analysis features need no credentials; the live features prompt to configure
Zerodha keys first.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table

from fortress.config import Config, load_config
from fortress import actions as A

console = Console()

MENU = [
    ("1", "Configure Zerodha credentials", "optional — only for live features"),
    ("2", "Universe update", "rebuild / fetch latest NSE data"),
    ("3", "Universe query", "PIT members / rank / snapshot / coverage"),
    ("4", "Select strategy", "dual_momentum / emerging_momentum"),
    ("5", "Select universe + rank range", "v1/v2, e.g. ranks 201-600"),
    ("6", "Backtest", "historical simulation"),
    ("7", "Market phases", "per-phase returns vs NIFTY, 2013→date"),
    ("8", "Market / trigger check", "current regime from latest data"),
    ("9", "Momentum scan", "top-N momentum-ranked stocks + metrics"),
    ("10", "Momentum allocation / rebalance", "capital + N stocks -> picks + orders"),
    ("11", "Swing stock suggestions", "run ryner / high_base scanners, show stocks"),
    ("0", "Exit", ""),
]


class App:
    """Holds the current in-memory Config; each handler is thin."""

    def __init__(self, config_path: str = "config.yaml") -> None:
        self.config: Config = load_config(config_path)

    # ---- rendering helpers -------------------------------------------------
    def _menu(self) -> None:
        console.print(Panel(
            "[bold bright_cyan]MOMENTUM UNIVERSE[/bold bright_cyan]\n"
            f"[white]strategy: {self.config.active_strategy}  |  universe: "
            f"v{self.config.universe.version[-1]} ranks {self.config.universe.rank_range}[/white]",
            style="bright_blue",
        ))
        t = Table(show_header=False, box=None, padding=(0, 2))
        t.add_column("Key", style="cyan bold", width=3)
        t.add_column("Option", style="white", width=32)
        t.add_column("Description", style="dim")
        for k, opt, desc in MENU:
            t.add_row(k, opt, desc)
        console.print(t)

    # ---- handlers (thin: gather input -> action -> render) -----------------
    def configure_credentials(self) -> None:
        console.print("[dim]Keys are written to a gitignored .env and never committed.[/dim]")
        key = Prompt.ask("ZERODHA_API_KEY").strip()
        secret = Prompt.ask("ZERODHA_API_SECRET", password=True).strip()
        try:
            path = A.save_credentials(key, secret)
            console.print(f"[green]Saved to {path} (chmod 600).[/green]")
        except ValueError as e:
            console.print(f"[red]{e}[/red]")

    def universe_update(self) -> None:
        fetch = Prompt.ask("Fetch latest from NSE? (needs network) [y/N]", default="n").lower() == "y"
        with console.status("[green]updating universe..."):
            res = A.update_universe(fetch=fetch)
        console.print(f"[green]Universe rebuilt: {res.symbols} symbols, {res.rows:,} rows.[/green]")
        if res.fetched:
            console.print(f"[dim]steps: {res.steps}[/dim]")

    def universe_query(self) -> None:
        UQ = A.universe_query
        v = Prompt.ask("Universe version", choices=["v1", "v2"],
                       default=self.config.universe.version)
        kind = Prompt.ask("Query", choices=["members", "rank", "snapshot", "indices", "health"],
                          default="members")
        if kind == "indices":
            console.print("Named indices: " + ", ".join(UQ.list_indices(v)))
            return
        if kind == "health":
            console.print(UQ.coverage(v))
            return
        d = _ask_date("As-of date", date.today())
        if kind == "members":
            idx = Prompt.ask("Index", choices=UQ.list_indices(v), default="nifty_500")
            m = UQ.members_on(d, idx, v)
            more = f"  ... (+{len(m) - 40})" if len(m) > 40 else ""
            console.print(f"[green]{len(m)} members of {idx} on {d}:[/green]\n" + ", ".join(m[:40]) + more)
        elif kind == "rank":
            sym = Prompt.ask("Symbol").upper()
            r = UQ.rank_of(sym, d, v)
            console.print(f"rank({sym}, {d}) = [bold]{r if r is not None else 'not ranked'}[/bold]")
        elif kind == "snapshot":
            df = UQ.snapshot_on(d, v, top=20)
            t = Table("Rank", "Symbol", "Metric (₹ turnover)", box=None)
            for _, row in df.iterrows():
                t.add_row(str(int(row["rank"])), row["symbol"], f"{row['metric_value']:,.0f}")
            console.print(t)

    def select_strategy(self) -> None:
        s = Prompt.ask("Strategy", choices=list(A.selection.VALID_STRATEGIES),
                       default=self.config.active_strategy)
        self.config = A.apply_selection(self.config, strategy=s)
        console.print(f"[green]Active strategy: {self.config.active_strategy}[/green]")

    def select_universe(self) -> None:
        v = Prompt.ask("Universe version", choices=["v1", "v2"], default=self.config.universe.version)
        lo = int(Prompt.ask("Rank low", default=str(self.config.universe.rank_range[0])))
        hi = int(Prompt.ask("Rank high", default=str(self.config.universe.rank_range[1])))
        try:
            self.config = A.apply_selection(self.config, version=v, rank_range=[lo, hi])
            console.print(f"[green]Universe: v{v[-1]} ranks {self.config.universe.rank_range}[/green]")
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

    def rebalance(self) -> None:
        capital = float(Prompt.ask("Capital to deploy (₹)", default="1000000"))
        top_n = int(Prompt.ask("Number of momentum stocks (custom allocation)",
                               default=str(self.config.position_sizing.target_positions)))
        holdings = _ask_holdings()
        with console.status("[green]planning momentum allocation..."):
            plan = A.plan_rebalance(self.config, capital, holdings=holdings, top_n=top_n)
        console.print(f"[bold]Target portfolio[/bold]  as of {plan.as_of}  regime {plan.regime}")
        tt = Table("Symbol", "Wt%", "Qty", "Value ₹", box=None)
        for t in plan.targets:
            tt.add_row(t.symbol, f"{t.weight:.1%}", str(t.quantity), f"{t.target_value:,.0f}")
        console.print(tt)
        if plan.orders:
            ot = Table("Action", "Symbol", "Qty", "≈Value ₹", box=None, title="Orders")
            for o in plan.orders:
                ot.add_row(o.action, o.symbol, str(o.quantity), f"{o.value:,.0f}")
            console.print(ot)

    def swing(self) -> None:
        kind = Prompt.ask("Swing scanner", choices=["high_base", "ryner"], default="high_base")
        top = int(Prompt.ask("Show top N", default="15"))
        d = _ask_date("As-of date", date.today())
        # The scanners print their own candidate table (symbols + stops + returns)
        # and return the list; run them live on the vendored data.
        if kind == "ryner":
            A.swing.run_ryner_scan(as_of=d, top=top)
        else:
            A.swing.run_high_base_scan(as_of=d, top=top)

    # ---- loop --------------------------------------------------------------
    def run(self) -> None:
        while True:
            console.print()
            self._menu()
            choice = Prompt.ask("\nSelect option", default="0")
            handlers = {
                "1": self.configure_credentials, "2": self.universe_update,
                "3": self.universe_query,
                "4": self.select_strategy, "5": self.select_universe,
                "6": self.backtest, "7": self.market_phases,
                "8": self.market_check, "9": self.momentum_scan,
                "10": self.rebalance, "11": self.swing,
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


def _ask_holdings() -> Optional[dict]:
    raw = Prompt.ask("Current holdings as SYMBOL:QTY,... (blank = none)", default="").strip()
    if not raw:
        return None
    out = {}
    for part in raw.split(","):
        sym, _, qty = part.partition(":")
        if sym.strip() and qty.strip():
            out[sym.strip().upper()] = int(qty)
    return out


def main() -> None:
    App().run()


if __name__ == "__main__":
    main()
