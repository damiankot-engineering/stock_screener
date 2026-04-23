"""
backtesting/report.py
Formatowanie i zapis wyników backtestingu.

Outputs:
  - Rich console: tabela metryk, log rebalansowań, podsumowanie
  - CSV: portfolio_values, monthly_returns, rebalance_log
"""
from __future__ import annotations

import csv
import logging
from pathlib import Path
from datetime import datetime

import pandas as pd

from backtesting.engine import BacktestResult

logger = logging.getLogger(__name__)

# Bezpieczny import rich (opcjonalny)
try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich import box
    _RICH = True
except ImportError:
    _RICH = False


class BacktestReporter:
    """Wyświetla i zapisuje wyniki backtestingu."""

    def __init__(self, reports_dir: str = "reports"):
        self.reports_dir = Path(reports_dir)
        self.reports_dir.mkdir(parents=True, exist_ok=True)

    # ──────────────────────────────────────────────────────────
    # Konsola
    # ──────────────────────────────────────────────────────────

    def print_results(self, result: BacktestResult, benchmark_ticker: str = "SPY") -> None:
        """Wyświetl pełne wyniki backtestingu na konsoli."""
        if not _RICH:
            self._print_plain(result)
            return

        c = Console()

        if not result.is_valid:
            c.print(f"\n[red]❌ Backtest nieudany:[/red] {result.warnings}")
            return

        m  = result.metrics
        bm = result.benchmark_metrics or {}

        # ── Nagłówek ──────────────────────────────────────────
        c.print()
        c.print(Panel.fit(
            f"[bold cyan]📊 WYNIKI BACKTESTINGU[/bold cyan]\n"
            f"[dim]Buildów portfela:[/dim] [white]{result.n_builds_used}[/white]   "
            f"[dim]Okres:[/dim] [white]{self._date_range(result.portfolio_values)}[/white]   "
            f"[dim]Benchmark:[/dim] [white]{benchmark_ticker}[/white]",
            border_style="cyan",
        ))

        # ── Główna tabela metryk ───────────────────────────────
        t = Table(box=box.SIMPLE_HEAD, show_header=True)
        t.add_column("Metryka",         style="bold", min_width=22)
        t.add_column("Portfel",         justify="right", min_width=12)
        t.add_column(benchmark_ticker,  justify="right", min_width=12, style="dim")
        t.add_column("Przewaga",        justify="right", min_width=12)

        def fmt_pct(v):
            if v is None: return "N/A"
            color = "green" if v >= 0 else "red"
            return f"[{color}]{v:+.2%}[/{color}]"

        def fmt_val(v, fmt=".4f"):
            if v is None: return "N/A"
            return f"{v:{fmt}}"

        def diff(port, bench, higher_better=True, pct=False):
            if port is None or bench is None: return "N/A"
            d = port - bench
            color = "green" if (d >= 0) == higher_better else "red"
            if pct:
                return f"[{color}]{d * 100:+.2f}pp[/{color}]"
            return f"[{color}]{d:+.4f}[/{color}]"

        rows = [
            ("Total Return",       fmt_pct(m.get("total_return")),  fmt_pct(bm.get("total_return")),  diff(m.get("total_return"), bm.get("total_return"), pct=True)),
            ("CAGR",               fmt_pct(m.get("cagr")),          fmt_pct(bm.get("cagr")),          diff(m.get("cagr"), bm.get("cagr"), pct=True)),
            ("Volatility (ann.)",  fmt_pct(m.get("volatility_ann")),fmt_pct(bm.get("volatility_ann")),diff(m.get("volatility_ann"), bm.get("volatility_ann"), higher_better=False, pct=True)),
            ("Sharpe Ratio",       fmt_val(m.get("sharpe_ratio")),  fmt_val(bm.get("sharpe_ratio")),  diff(m.get("sharpe_ratio"), bm.get("sharpe_ratio"))),
            ("Sortino Ratio",      fmt_val(m.get("sortino_ratio")), fmt_val(bm.get("sortino_ratio")), diff(m.get("sortino_ratio"), bm.get("sortino_ratio"))),
            ("Max Drawdown",       fmt_pct(m.get("max_drawdown")),  fmt_pct(bm.get("max_drawdown")),  diff(m.get("max_drawdown"), bm.get("max_drawdown"), higher_better=True, pct=True)),
            ("Calmar Ratio",       fmt_val(m.get("calmar_ratio")),  fmt_val(bm.get("calmar_ratio")),  diff(m.get("calmar_ratio"), bm.get("calmar_ratio"))),
            ("Win Rate",           fmt_pct(m.get("win_rate")),      fmt_pct(bm.get("win_rate")),      diff(m.get("win_rate"), bm.get("win_rate"), pct=True)),
            ("Best Period",        fmt_pct(m.get("best_period_return")),  "—", "—"),
            ("Worst Period",       fmt_pct(m.get("worst_period_return")), "—", "—"),
            ("Alpha",              fmt_val(m.get("alpha"), "+.4f") if m.get("alpha") is not None else "N/A", "—", "—"),
            ("Beta",               fmt_val(m.get("beta")) if m.get("beta") is not None else "N/A", "—", "—"),
            ("Max DD Duration",    f"{m.get('max_dd_duration_days', 'N/A')} dni", "—", "—"),
        ]
        for row in rows:
            t.add_row(*row)
        c.print(t)

        # ── NAV podsumowanie ──────────────────────────────────
        start_v = m.get("start_value", 0) or 0
        end_v   = m.get("end_value",   0) or 0
        c.print(
            f"\n  Kapitał startowy: [cyan]{start_v:,.0f}[/cyan]  →  "
            f"Końcowy: [{'green' if end_v >= start_v else 'red'}]{end_v:,.0f}[/]"
            f"  ([{'green' if end_v >= start_v else 'red'}]{end_v - start_v:+,.0f}[/])"
        )

        # ── Ostrzeżenia ───────────────────────────────────────
        if result.warnings:
            c.print()
            for w in result.warnings:
                c.print(f"  [yellow]⚠  {w}[/yellow]")

        # ── Log rebalansowań ──────────────────────────────────
        if result.rebalance_log:
            c.print()
            rt = Table(box=box.SIMPLE, title="Log rebalansowań", show_header=True)
            rt.add_column("Data",       min_width=12)
            rt.add_column("Pozycji",    justify="right", min_width=8)
            rt.add_column("Weszły",     min_width=20)
            rt.add_column("Wyszły",     min_width=20)
            rt.add_column("Koszt (tx)", justify="right", min_width=10)
            for log in result.rebalance_log:
                rt.add_row(
                    str(log.get("date", ""))[:10],
                    str(log.get("n_positions", "")),
                    ", ".join(log.get("tickers_in", [])[:5]),
                    ", ".join(log.get("tickers_out", [])[:5]),
                    f"{log.get('transaction_cost', 0):.2f}",
                )
            c.print(rt)

        # ── Miesięczne zwroty ─────────────────────────────────
        if not result.monthly_returns.empty:
            self._print_monthly_returns(c, result.monthly_returns)

        c.print()

    def _print_monthly_returns(self, c, monthly_df: pd.DataFrame) -> None:
        """Wyświetl heat-map miesięcznych zwrotów w konsoli."""
        MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

        mt = Table(box=box.SIMPLE, title="Miesięczne zwroty portfela")
        mt.add_column("Rok", min_width=6)
        for m in MONTHS:
            mt.add_column(m, justify="right", min_width=7)
        mt.add_column("Rok total", justify="right", min_width=10)

        for year, grp in monthly_df.groupby("year"):
            row = [str(year)]
            year_ret = 1.0
            for mo in range(1, 13):
                val = grp.loc[grp["month"] == mo, "return"]
                if len(val) > 0:
                    v = float(val.iloc[0])
                    year_ret *= (1 + v)
                    color = "green" if v >= 0 else "red"
                    row.append(f"[{color}]{v:+.1%}[/{color}]")
                else:
                    row.append("—")
            yr = year_ret - 1
            color = "green" if yr >= 0 else "red"
            row.append(f"[{color}]{yr:+.1%}[/{color}]")
            mt.add_row(*row)

        c.print()
        c.print(mt)

    def _print_plain(self, result: BacktestResult) -> None:
        """Fallback bez rich."""
        m = result.metrics
        print("\n=== BACKTEST RESULTS ===")
        for k, v in m.items():
            print(f"  {k}: {v}")

    # ──────────────────────────────────────────────────────────
    # Zapis CSV
    # ──────────────────────────────────────────────────────────

    def save_csv(self, result: BacktestResult) -> dict[str, str]:
        """
        Zapisz wyniki backtestingu do CSV.
        Zwraca słownik {nazwa: ścieżka} dla każdego pliku.
        """
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        paths = {}

        # 1. Wartości portfela (NAV)
        if result.portfolio_values is not None and len(result.portfolio_values) > 0:
            nav_path = self.reports_dir / f"backtest_nav_{ts}.csv"
            nav_df = pd.DataFrame({
                "date":            result.portfolio_values.index,
                "portfolio_value": result.portfolio_values.values,
            })
            if result.benchmark_values is not None:
                bm_aligned = result.benchmark_values.reindex(result.portfolio_values.index)
                nav_df["benchmark_value"] = bm_aligned.values
            nav_df.to_csv(nav_path, index=False)
            paths["nav"] = str(nav_path)
            logger.info(f"NAV zapisany: {nav_path}")

        # 2. Metryki
        metrics_path = self.reports_dir / f"backtest_metrics_{ts}.csv"
        metrics_rows = []
        for k, v in result.metrics.items():
            bm_v = result.benchmark_metrics.get(k) if result.benchmark_metrics else None
            metrics_rows.append({"metric": k, "portfolio": v, "benchmark": bm_v})
        pd.DataFrame(metrics_rows).to_csv(metrics_path, index=False)
        paths["metrics"] = str(metrics_path)

        # 3. Miesięczne zwroty
        if not result.monthly_returns.empty:
            monthly_path = self.reports_dir / f"backtest_monthly_{ts}.csv"
            result.monthly_returns.to_csv(monthly_path, index=False)
            paths["monthly"] = str(monthly_path)

        # 4. Log rebalansowań
        if result.rebalance_log:
            rebal_path = self.reports_dir / f"backtest_rebalance_{ts}.csv"
            pd.DataFrame(result.rebalance_log).to_csv(rebal_path, index=False)
            paths["rebalance"] = str(rebal_path)

        return paths

    # ──────────────────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────────────────

    @staticmethod
    def _date_range(pv: pd.Series) -> str:
        if pv is None or len(pv) == 0:
            return "N/A"
        s = str(pv.index[0])[:10]
        e = str(pv.index[-1])[:10]
        return f"{s} → {e}"
