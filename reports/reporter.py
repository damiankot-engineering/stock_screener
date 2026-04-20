"""
reports/reporter.py
Generowanie raportów i czytelne wyjście konsolowe.
Rich jest importowane leniwie (lazy import) wewnątrz metod – moduł działa
nawet bez zainstalowanego rich (degraduje do zwykłego print()).
"""
from __future__ import annotations

import csv
import logging
import sys
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


def _rich_available() -> bool:
    try:
        import rich  # noqa: F401
        return True
    except ImportError:
        return False


def _console():
    """Zwróć Rich Console lub None jeśli rich niedostępne."""
    try:
        from rich.console import Console
        return Console()
    except ImportError:
        return None


def _plain(*parts: str) -> None:
    """Fallback: wydrukuj zwykłym print() bez formatowania Rich."""
    text = " ".join(str(p) for p in parts)
    # Usuń Rich markup tags [bold], [cyan] itp.
    import re
    text = re.sub(r'\[/?[a-z_ ]+\]', '', text)
    print(text)


class Reporter:
    """Generuje raporty CSV i wizualizacje konsolowe."""

    def __init__(self, reports_dir: str = "reports"):
        self.reports_dir = Path(reports_dir)
        self.reports_dir.mkdir(exist_ok=True)
        self._use_rich = _rich_available()
        if not self._use_rich:
            logger.warning(
                "Biblioteka 'rich' nie jest zainstalowana. "
                "Wyjście konsolowe będzie uproszczone. "
                "Zainstaluj: pip install rich"
            )

    # ── Wyjście konsolowe ─────────────────────────────────────

    def print_header(self, source: str, n_tickers: int) -> None:
        msg = (
            f"\n{'='*60}\n"
            f"  STOCK SCREENER | Źródło: {source.upper()} | "
            f"Tickerów: {n_tickers} | {datetime.now():%Y-%m-%d %H:%M:%S}\n"
            f"{'='*60}"
        )
        if self._use_rich:
            from rich.console import Console
            from rich.panel import Panel
            Console().print(Panel.fit(
                f"[bold cyan]STOCK SCREENER[/bold cyan]\n"
                f"[dim]Źródło:[/dim] [yellow]{source.upper()}[/yellow]   "
                f"[dim]Tickerów:[/dim] [white]{n_tickers}[/white]   "
                f"[dim]Start:[/dim] [white]{datetime.now():%Y-%m-%d %H:%M:%S}[/white]",
                border_style="cyan",
            ))
        else:
            print(msg)

    def print_filter_summary(self, passed: int, rejected: int, total: int) -> None:
        msg = f"\nFiltrowanie: ✓ {passed} przeszło / ✗ {rejected} odrzucono / {total} łącznie"
        if self._use_rich:
            from rich.console import Console
            Console().print(
                f"\n[bold]Wyniki filtrowania:[/bold] "
                f"[green]✓ {passed} przeszło[/green] / "
                f"[red]✗ {rejected} odrzucono[/red] / [dim]{total} łącznie[/dim]"
            )
        else:
            print(msg)

    def print_screening_results(self, scored_tickers: list, top_n: int = 20) -> None:
        """Drukuj tabelę z wynikami screeningu."""
        if not scored_tickers:
            print("Brak wyników do wyświetlenia.")
            return

        show = scored_tickers[:top_n]

        if self._use_rich:
            from rich.console import Console
            from rich.table import Table
            from rich import box
            table = Table(
                title=f"Top {len(show)} wyniki screeningu",
                box=box.ROUNDED, show_lines=True,
            )
            table.add_column("#", style="dim", width=4)
            table.add_column("Ticker", style="bold cyan", width=10)
            table.add_column("Score", justify="right", style="green", width=9)
            sample = show[0].metrics if show else {}
            metric_cols = list(sample.keys())[:8]
            for m in metric_cols:
                table.add_column(m, justify="right", width=11)
            for st in show:
                row = [str(st.rank), st.ticker, f"{st.score:.4f}"]
                for m in metric_cols:
                    v = st.metrics.get(m)
                    row.append("[dim]N/A[/dim]" if v is None else f"{v:.2f}")
                table.add_row(*row)
            Console().print()
            Console().print(table)
        else:
            print(f"\n{'─'*70}")
            print(f"{'#':<4} {'Ticker':<10} {'Score':>9}")
            print(f"{'─'*70}")
            for st in show:
                print(f"{st.rank:<4} {st.ticker:<10} {st.score:>9.4f}")
            print(f"{'─'*70}")

    def print_portfolio(self, positions: list) -> None:
        """Drukuj tabelę portfela historycznego."""
        if not positions:
            print("Portfel jest pusty.")
            return

        if self._use_rich:
            from rich.console import Console
            from rich.table import Table
            from rich import box
            table = Table(
                title="Portfel inwestycyjny (oparty na historii DB)",
                box=box.DOUBLE_EDGE, show_lines=False,
            )
            table.add_column("#",      style="dim",  width=4)
            table.add_column("Ticker", style="bold", width=10)
            table.add_column("Waga",   justify="right", style="green",  width=8)
            table.add_column("Score",  justify="right", width=10)
            table.add_column("Freq%",  justify="right", width=7, style="cyan")
            table.add_column("Stab",   justify="right", width=7, style="yellow")
            table.add_column("Trend",  justify="right", width=9)
            table.add_column("Status", width=7)
            for p in positions:
                ts = getattr(p, "trend_score", 0)
                trend_str = (f"[green]+{ts:.3f}[/green]" if ts >= 0
                             else f"[red]{ts:.3f}[/red]")
                status = "[green]NOWY[/green]" if p.is_new_entry else "[dim]---[/dim]"
                cs = getattr(p, "composite_score", getattr(p, "score", 0))
                ar = getattr(p, "appearance_rate", getattr(p, "stability_score", 0))
                sc = getattr(p, "score_consistency", 0)
                table.add_row(
                    str(p.rank), p.ticker, f"{p.weight:.1%}",
                    f"{cs:.4f}", f"{ar:.0%}", f"{sc:.0%}", trend_str, status,
                )
            console = Console()
            console.print()
            console.print(table)
            console.print(f"[dim]Suma wag: {sum(p.weight for p in positions):.4f}[/dim]")
        else:
            print(f"\n{'─'*70}")
            print(f"{'#':<4} {'Ticker':<10} {'Waga':>7} {'Score':>10} "
                  f"{'Freq%':>6} {'Stab':>6} {'Trend':>8} {'Nowy?':>6}")
            print(f"{'─'*70}")
            for p in positions:
                cs = getattr(p, "composite_score", getattr(p, "score", 0))
                ar = getattr(p, "appearance_rate", getattr(p, "stability_score", 0))
                sc = getattr(p, "score_consistency", 0)
                ts = getattr(p, "trend_score", 0)
                new_flag = "✓" if p.is_new_entry else ""
                print(f"{p.rank:<4} {p.ticker:<10} {p.weight:>6.1%} "
                      f"{cs:>10.4f} {ar:>5.0%} {sc:>5.0%} "
                      f"{ts:>+8.3f} {new_flag:>6}")
            print(f"{'─'*70}")
            print(f"Suma wag: {sum(p.weight for p in positions):.4f}")

    def print_screen_summary(
        self,
        run_id: int,
        duration: float,
        passed: int,
        total_runs: int,
        min_runs: int,
    ) -> None:
        """Podsumowanie po uruchomieniu screenera — podpowiada kiedy budować portfel."""
        runs_left = max(0, min_runs - total_runs)
        if runs_left > 0:
            hint = (
                f"Portfel: potrzebujesz jeszcze {runs_left} run(ów)  →  "
                f"python main.py --build-portfolio"
            )
        else:
            hint = "Portfel gotowy!  →  python main.py --build-portfolio"

        if self._use_rich:
            from rich.console import Console
            from rich.panel import Panel
            rich_hint = (
                f"[yellow]Portfel:[/yellow] potrzebujesz jeszcze "
                f"[bold]{runs_left}[/bold] run(ów) → "
                f"[dim]python main.py --build-portfolio[/dim]"
                if runs_left > 0 else
                f"[green]Portfel gotowy do budowania![/green] "
                f"→ [dim]python main.py --build-portfolio[/dim]"
            )
            Console().print(Panel(
                f"[bold green]✅ Screening #{run_id} zakończony[/bold green]\n"
                f"Czas: [cyan]{duration:.1f}s[/cyan]   "
                f"Przeszło filtry: [cyan]{passed}[/cyan]   "
                f"Runów w DB: [cyan]{total_runs}/{min_runs}[/cyan]\n"
                f"{rich_hint}",
                border_style="green",
            ))
        else:
            print(
                f"\n{'='*60}\n"
                f"✅ Screening #{run_id} zakończony\n"
                f"   Czas: {duration:.1f}s   Przeszło: {passed}   "
                f"Runy: {total_runs}/{min_runs}\n"
                f"   {hint}\n"
                f"{'='*60}"
            )

    def create_progress(self):
        """Zwróć Rich Progress lub None."""
        if not self._use_rich:
            return None
        from rich.progress import (
            BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
        )
        from rich.console import Console
        return Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TimeElapsedColumn(),
            console=Console(),
        )

    # ── CSV reports ───────────────────────────────────────────

    def save_screening_csv(self, scored_tickers: list, run_id: int, source: str) -> str:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = self.reports_dir / f"screening_{source}_{timestamp}_run{run_id}.csv"
        if not scored_tickers:
            return str(filename)

        all_metric_keys = set()
        for st in scored_tickers:
            all_metric_keys.update(st.metrics.keys())
        fieldnames = ["rank", "ticker", "score"] + sorted(all_metric_keys)

        with open(filename, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            for st in scored_tickers:
                writer.writerow({"rank": st.rank, "ticker": st.ticker,
                                  "score": st.score, **st.metrics})
        logger.info(f"Screening CSV: {filename}")
        return str(filename)

    def save_portfolio_csv(self, positions: list, run_id: int, source: str) -> str:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = self.reports_dir / f"portfolio_{source}_{timestamp}_run{run_id}.csv"
        if not positions:
            return str(filename)

        fieldnames = [
            "rank", "ticker", "weight_pct", "composite_score",
            "appearance_rate", "avg_score", "score_consistency",
            "trend_score", "n_appearances", "is_new_entry",
        ]
        with open(filename, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            for p in positions:
                writer.writerow({
                    "rank":              getattr(p, "rank", ""),
                    "ticker":           p.ticker,
                    "weight_pct":       f"{p.weight:.4%}",
                    "composite_score":  getattr(p, "composite_score", getattr(p, "score", "")),
                    "appearance_rate":  getattr(p, "appearance_rate", getattr(p, "stability_score", "")),
                    "avg_score":        getattr(p, "avg_score", ""),
                    "score_consistency": getattr(p, "score_consistency", ""),
                    "trend_score":      getattr(p, "trend_score", ""),
                    "n_appearances":    getattr(p, "n_appearances", ""),
                    "is_new_entry":     p.is_new_entry,
                })
        logger.info(f"Portfolio CSV: {filename}")
        return str(filename)

    def save_historical_analysis_csv(self, appearances_df, run_id: int) -> str:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = self.reports_dir / f"historical_analysis_{timestamp}_run{run_id}.csv"
        if appearances_df is None or appearances_df.empty:
            return str(filename)
        appearances_df.to_csv(filename, index=False, encoding="utf-8")
        logger.info(f"Historical CSV: {filename}")
        return str(filename)
