"""
reports/reporter.py
Generowanie raportów i czytelne wyjście konsolowe (Rich).
"""
from __future__ import annotations

import csv
import logging
import os
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Table
from rich import box

logger = logging.getLogger(__name__)
console = Console()


class Reporter:
    """Generuje raporty CSV i wizualizacje konsolowe."""

    def __init__(self, reports_dir: str = "reports"):
        self.reports_dir = Path(reports_dir)
        self.reports_dir.mkdir(exist_ok=True)

    # ─────────────────────────────────────────────────────────
    # Wyjście konsolowe
    # ─────────────────────────────────────────────────────────

    def print_header(self, source: str, n_tickers: int) -> None:
        console.print()
        console.print(Panel.fit(
            f"[bold cyan]📊 STOCK SCREENER[/bold cyan]\n"
            f"[dim]Źródło: [/dim][yellow]{source.upper()}[/yellow]   "
            f"[dim]Tickerów:[/dim] [white]{n_tickers}[/white]   "
            f"[dim]Start:[/dim] [white]{datetime.now():%Y-%m-%d %H:%M:%S}[/white]",
            border_style="cyan",
        ))

    def print_filter_summary(self, passed: int, rejected: int, total: int) -> None:
        console.print(
            f"\n[bold]Wyniki filtrowania:[/bold] "
            f"[green]✓ {passed} przeszło[/green] / "
            f"[red]✗ {rejected} odrzucono[/red] / "
            f"[dim]{total} łącznie[/dim]"
        )

    def print_screening_results(self, scored_tickers: list, top_n: int = 20) -> None:
        """Drukuj tabelę z wynikami screeningu."""
        if not scored_tickers:
            console.print("[yellow]Brak wyników do wyświetlenia.[/yellow]")
            return

        table = Table(
            title=f"Top {min(top_n, len(scored_tickers))} wyniki screeningu",
            box=box.ROUNDED,
            show_lines=True,
        )
        table.add_column("#", style="dim", width=4)
        table.add_column("Ticker", style="bold cyan", width=10)
        table.add_column("Score", justify="right", style="green", width=9)

        # Dynamiczne kolumny metryk (top metryki)
        sample = scored_tickers[0].metrics if scored_tickers else {}
        metric_cols = list(sample.keys())[:8]  # max 8 kolumn metryk

        for metric in metric_cols:
            table.add_column(metric, justify="right", width=11)

        for st in scored_tickers[:top_n]:
            row = [
                str(st.rank),
                st.ticker,
                f"{st.score:.4f}",
            ]
            for metric in metric_cols:
                val = st.metrics.get(metric)
                if val is None:
                    row.append("[dim]N/A[/dim]")
                elif isinstance(val, float):
                    row.append(f"{val:.2f}")
                else:
                    row.append(str(val))
            table.add_row(*row)

        console.print()
        console.print(table)

    def print_portfolio(self, positions: list) -> None:
        """Drukuj tabelę portfela."""
        if not positions:
            console.print("[yellow]Portfel jest pusty.[/yellow]")
            return

        table = Table(
            title="Portfel inwestycyjny",
            box=box.DOUBLE_EDGE,
            show_lines=False,
        )
        table.add_column("#", style="dim", width=4)
        table.add_column("Ticker", style="bold", width=10)
        table.add_column("Waga", justify="right", style="green", width=8)
        table.add_column("Score", justify="right", width=9)
        table.add_column("Stabilność", justify="right", width=11)
        table.add_column("Status", width=8)

        for p in positions:
            status = "[green]NOWY[/green]" if p.is_new_entry else "[dim]---[/dim]"
            table.add_row(
                str(p.rank),
                p.ticker,
                f"{p.weight:.1%}",
                f"{p.score:.4f}",
                f"{p.stability_score:.1%}",
                status,
            )

        console.print()
        console.print(table)
        total_w = sum(p.weight for p in positions)
        console.print(f"[dim]Suma wag: {total_w:.4f}[/dim]")

    def print_run_summary(self, run_id: int, duration: float, portfolio_size: int) -> None:
        console.print()
        console.print(Panel(
            f"[bold green]✅ Uruchomienie #{run_id} zakończone[/bold green]\n"
            f"Czas: [cyan]{duration:.1f}s[/cyan]   "
            f"Portfel: [cyan]{portfolio_size}[/cyan] pozycji\n"
            f"[dim]Wyniki zapisane do bazy danych i plików CSV[/dim]",
            border_style="green",
        ))

    def create_progress(self) -> Progress:
        """Zwróć obiekt Rich Progress do wyświetlania postępu."""
        return Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TimeElapsedColumn(),
            console=console,
        )

    # ─────────────────────────────────────────────────────────
    # Raporty CSV
    # ─────────────────────────────────────────────────────────

    def save_screening_csv(
        self, scored_tickers: list, run_id: int, source: str
    ) -> str:
        """Zapisz wyniki screeningu do CSV."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = self.reports_dir / f"screening_{source}_{timestamp}_run{run_id}.csv"

        if not scored_tickers:
            return str(filename)

        # Zbierz wszystkie klucze metryk
        all_metric_keys = set()
        for st in scored_tickers:
            all_metric_keys.update(st.metrics.keys())

        fieldnames = ["rank", "ticker", "score"] + sorted(all_metric_keys)

        with open(filename, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            for st in scored_tickers:
                row = {
                    "rank": st.rank,
                    "ticker": st.ticker,
                    "score": st.score,
                    **{k: v for k, v in st.metrics.items()},
                }
                writer.writerow(row)

        logger.info(f"Raport CSV zapisany: {filename}")
        return str(filename)

    def save_portfolio_csv(self, positions: list, run_id: int, source: str) -> str:
        """Zapisz skład portfela do CSV."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = self.reports_dir / f"portfolio_{source}_{timestamp}_run{run_id}.csv"

        fieldnames = ["rank", "ticker", "weight_pct", "score",
                      "stability_score", "combined_score", "is_new_entry"]

        with open(filename, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for p in positions:
                writer.writerow({
                    "rank": p.rank,
                    "ticker": p.ticker,
                    "weight_pct": f"{p.weight:.4%}",
                    "score": p.score,
                    "stability_score": p.stability_score,
                    "combined_score": p.combined_score,
                    "is_new_entry": p.is_new_entry,
                })

        logger.info(f"Portfel CSV zapisany: {filename}")
        return str(filename)

    def save_historical_analysis_csv(self, appearances_df, run_id: int) -> str:
        """Zapisz analizę historyczną częstości pojawiania się tickerów."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = self.reports_dir / f"historical_analysis_{timestamp}_run{run_id}.csv"

        if appearances_df is None or appearances_df.empty:
            return str(filename)

        appearances_df.to_csv(filename, index=False, encoding="utf-8")
        logger.info(f"Analiza historyczna zapisana: {filename}")
        return str(filename)
