"""
main.py
Główny punkt wejścia systemu. Dwa niezależne tryby pracy:

  TRYB 1 — SCREEN (domyślny):
  ════════════════════════════
  Uruchamia pełny pipeline zbierania danych:
    [1] AI Ticker Source  → LLM generuje listę tickerów wg strategii
    [2] Data Fetcher      → Yahoo Finance: dane fundamentalne + techniczne
    [3] DB SAVE           → metric_snapshots (inkrementacyjnie, bez nadpisywania)
    [4] Filter Engine     → odfiltruj wg progów użytkownika
    [5] Scorer            → oblicz score i ranking
    [6] DB SAVE           → screening_results (inkrementacyjnie)
    [7] Reporter          → wydrukuj wyniki + zapisz CSV

  NIE buduje portfela — tylko zbiera dane do historii.

  TRYB 2 — BUILD-PORTFOLIO:
  ══════════════════════════
  Analizuje całą historię w DB i buduje portfel inwestycyjny:
    [1] Pobierz historię screening_results z N ostatnich runów
    [2] Oblicz metryki per ticker: appearance_rate, avg_score,
        score_consistency, trend_score, avg_rank
    [3] Composite score = ważona suma metryk historycznych
    [4] Wybierz top K spółek i oblicz wagi
    [5] Zapisz portfel do DB + CSV

  Portfel budowany TYLKO po zgromadzeniu min. N runów (konfig: min_history_runs).

UŻYCIE:
  python main.py                              # screen: domyślna strategia
  python main.py --strategy deep_value        # screen: strategia wartościowa
  python main.py --strategy thematic --theme "clean energy"
  python main.py --strategy sector_leaders --sector healthcare
  python main.py --n 80                       # screen: więcej tickerów
  python main.py --backend mock               # screen: tryb testowy
  python main.py --multi-shot                 # screen: 3× więcej tickerów
  python main.py --build-portfolio            # buduj portfel z historii DB
  python main.py --build-portfolio --runs 10  # użyj 10 ostatnich runów
  python main.py --analyze                    # pokaż analizę historyczną
  python main.py --schedule                   # harmonogram (wg config)
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from config.settings import load_config, setup_logging
from data.ticker_source import get_tickers
from data.fetcher import DataFetcher
from data.enriched_fetcher import EnrichedFetcher
from data.ticker_validator import TickerValidator
from db.models import create_db_engine, get_session_factory
from db.repository import ScreenerRepository
from screening.filter_engine import FilterEngine
from screening.scorer import Scorer
from portfolio.builder import PortfolioBuilder
from reports.reporter import Reporter

logger = logging.getLogger(__name__)


class ScreenerPipeline:
    """Orchestrator systemu — dwa niezależne tryby: screen i build-portfolio."""

    def __init__(self, config: dict):
        self.config = config
        settings = config.get("settings", {})
        setup_logging(settings.get("log_level", "INFO"))

        db_path = settings.get("db_path", "screener_data.db")
        engine = create_db_engine(db_path)
        session_factory = get_session_factory(engine)

        self.repository     = ScreenerRepository(session_factory)
        self.fetcher        = EnrichedFetcher(config)   # Yahoo + EDGAR + FRED + Sentiment
        self.validator      = TickerValidator(
            repository=self.repository,
            workers=settings.get("fetch_workers", 10),
            api_delay=settings.get("api_delay_seconds", 0.1),
            cache_ttl_days=settings.get("validation_cache_ttl_days", 30),
        )
        self.filter_engine  = FilterEngine(config)
        self.scorer         = Scorer(config)
        self.portfolio_builder = PortfolioBuilder(config, repository=self.repository)
        self.reporter       = Reporter(settings.get("reports_dir", "reports"))

        logger.info("Pipeline zainicjalizowany")

    # ══════════════════════════════════════════════════════════
    # TRYB 1: SCREEN — zbieranie danych do historii
    # ══════════════════════════════════════════════════════════

    def run(self) -> dict:
        """
        Uruchom screening i zapisz wyniki do historii DB.
        NIE buduje portfela — wyłącznie zbiera dane.
        """
        start = time.time()
        source_config = self.config.get("source", {})
        strategy   = source_config.get("strategy", "growth_quality")
        backend    = source_config.get("ai", {}).get("backend", "groq")
        n_tickers  = source_config.get("ai", {}).get("n_tickers", 50)
        source_name = f"ai_{strategy}"

        # ── 1: AI generuje tickery (z feedback loop z DB) ────
        logger.info("=" * 60)
        logger.info(f"KROK 1: AI Ticker Source [backend={backend}, strategia={strategy}, n={n_tickers}]")
        # Wstrzyknij do promptu tickery znane jako niedziałające w yfinance
        feedback_limit = self.config.get("settings", {}).get("feedback_loop_limit", 500)
        invalid_known = self.repository.get_invalid_tickers(limit=feedback_limit)
        if invalid_known:
            source_config.setdefault("ai", {})["avoid_tickers"] = invalid_known
            logger.info(f"Feedback loop: {len(invalid_known)} znanych złych tickerów dodanych do promptu AI")

        # Pobierz kontekst makro i wstrzyknij do promptu AI
        macro_context: str | None = None
        if self.config.get("enrichment", {}).get("macro", {}).get("enabled", True):
            try:
                from data.macro_data import MacroDataFetcher
                import os
                fred_key = os.getenv("FRED_API_KEY", "").strip()
                macro_snap = MacroDataFetcher(fred_api_key=fred_key or None).fetch()
                macro_context = MacroDataFetcher(fred_api_key=fred_key or None)\
                    .get_em_context_for_prompt(macro_snap)
                source_config.setdefault("ai", {})["macro_context"] = macro_context
                logger.info(f"Kontekst makro dla AI: {macro_context[:100]}...")
            except Exception as exc:
                logger.debug(f"Makro kontekst niedostępny: {exc}")

        tickers_raw = get_tickers(source_config)

        # ── 1b: Walidacja tickerów przez yfinance ─────────────
        logger.info("=" * 60)
        logger.info(f"KROK 1b: Walidacja {len(tickers_raw)} tickerów przez yfinance")
        tickers, invalid = self.validator.validate_batch(tickers_raw)
        self._print_validation_summary(tickers_raw, tickers, invalid)
        self.reporter.print_header(f"AI:{strategy}@{backend}", len(tickers))

        if not tickers:
            logger.error("Żaden ticker nie przeszedł walidacji yfinance!")
            return {"run_id": None, "passed": 0}

        # ── 2: Pobierz dane fundamentalne + techniczne ────────
        logger.info("=" * 60)
        logger.info(f"KROK 2: Pobieranie danych dla {len(tickers)} tickerów oraz wzbogacanie danych (FRED / SEC EDGAR / OpenFIGI")
        ticker_data_list = self.fetcher.fetch_all_with_enrichment(tickers)
        fetch_errors  = sum(1 for td in ticker_data_list if not td.success)
        successful    = [td for td in ticker_data_list if td.success]

        # ── 3: Zapis metadanych runu ──────────────────────────
        logger.info("KROK 3: Zapis metadanych uruchomienia")
        run_id = self.repository.save_run(
            source_index=source_name,
            config=self.config,
            total_fetched=len(successful),
            total_passed=0,
            fetch_errors=fetch_errors,
            duration=0.0,
            notes="W trakcie",
        )

        # ── 4: Zapis snapshotów metryk ────────────────────────
        logger.info("KROK 4: Zapis snapshotów metryk do DB")
        self.repository.save_metric_snapshots(run_id, ticker_data_list)

        # ── 5: Filtrowanie ────────────────────────────────────
        logger.info("=" * 60)
        logger.info("KROK 5: Filtrowanie")
        logger.info(self.filter_engine.get_filter_summary())
        passed, rejected = self.filter_engine.apply_batch(successful)
        self.reporter.print_filter_summary(len(passed), len(rejected), len(successful))

        if not passed:
            logger.warning("Żaden ticker nie przeszedł filtrów!")
            self._finalize_run(run_id, 0, time.time() - start)
            return {"run_id": run_id, "passed": 0}

        # ── 6: Scoring ────────────────────────────────────────
        logger.info("=" * 60)
        logger.info("KROK 6: Scoring i ranking")
        scored = self.scorer.score_and_rank(passed)

        # ── 7: Zapis wyników screeningu ───────────────────────
        logger.info("KROK 7: Zapis wyników screeningu")
        self.repository.save_screening_results(run_id, [s.to_dict() for s in scored])

        # ── 8: Wyświetl wyniki ────────────────────────────────
        self.reporter.print_screening_results(scored, top_n=20)

        # ── 9: CSV i finalizacja ──────────────────────────────
        duration = time.time() - start
        self.reporter.save_screening_csv(scored, run_id, source_name)
        self._finalize_run(run_id, len(passed), duration)

        # Pokaż status historii
        total_runs = self.repository.get_run_count()
        min_runs   = self.config.get("portfolio", {}).get("min_history_runs", 3)
        self.reporter.print_screen_summary(run_id, duration, len(scored), total_runs, min_runs)

        return {
            "run_id":     run_id,
            "source":     source_name,
            "fetched":    len(successful),
            "passed":     len(passed),
            "duration":   duration,
        }

    # ══════════════════════════════════════════════════════════
    # TRYB 2: BUILD-PORTFOLIO — buduj portfel z historii
    # ══════════════════════════════════════════════════════════

    def build_portfolio(self, n_last_runs: int | None = None) -> dict:
        """
        Zbuduj portfel inwestycyjny na podstawie historii screeningów w DB.
        Wymaga min. portfolio.min_history_runs uruchomień.
        """
        min_runs   = self.config.get("portfolio", {}).get("min_history_runs", 3)
        total_runs = self.repository.get_run_count()

        from rich.console import Console
        from rich.panel import Panel
        c = Console()

        c.print()
        c.print(Panel.fit(
            f"[bold cyan]📈 BUDOWANIE PORTFELA HISTORYCZNEGO[/bold cyan]\n"
            f"[dim]Uruchomień w DB:[/dim] [white]{total_runs}[/white]   "
            f"[dim]Wymagane minimum:[/dim] [white]{min_runs}[/white]   "
            f"[dim]Używam:[/dim] [white]{n_last_runs or 'wszystkich'}[/white] runów",
            border_style="cyan",
        ))

        if total_runs < min_runs:
            c.print(
                f"\n[yellow]⚠  Za mało danych historycznych.[/yellow]\n"
                f"   Masz [bold]{total_runs}[/bold] runów, "
                f"potrzebujesz [bold]{min_runs}[/bold].\n"
                f"   Uruchom screener jeszcze [bold]{min_runs - total_runs}[/bold] raz(y):\n"
                f"   [dim]python main.py[/dim]"
            )
            return {"status": "insufficient_history", "runs": total_runs, "required": min_runs}

        # Pobierz poprzedni portfel (dla is_new_entry)
        previous_portfolio = self.repository.get_last_portfolio()

        # Zbuduj portfel z historii
        result = self.portfolio_builder.build_from_history(
            n_last_runs=n_last_runs,
            previous_portfolio=previous_portfolio,
        )

        if not result.is_valid:
            c.print("[yellow]Portfel jest pusty – za mało spółek spełniających kryteria.[/yellow]")
            return {"status": "empty_portfolio", "runs_used": result.n_runs_used}

        # Wyświetl portfel
        self.reporter.print_portfolio(result.positions)

        # Zapisz do DB
        portfolio_dicts = self.portfolio_builder.to_dict_list(result.positions)
        self.repository.save_portfolio(
            run_id=None,
            portfolio=portfolio_dicts,
            previous_tickers=previous_portfolio,
        )

        # CSV
        source_name = self.config.get("source", {}).get("strategy", "historical")
        csv_path = self.reporter.save_portfolio_csv(
            result.positions, run_id=0, source=f"portfolio_{source_name}"
        )

        c.print()
        c.print(Panel(
            f"[bold green]✅ Portfel historyczny zbudowany[/bold green]\n"
            f"Pozycji: [cyan]{len(result.positions)}[/cyan]   "
            f"Runów użytych: [cyan]{result.n_runs_used}[/cyan]   "
            f"Kandydatów: [cyan]{result.n_candidates_evaluated}[/cyan]\n"
            f"CSV: [dim]{csv_path}[/dim]",
            border_style="green",
        ))

        c.print(f"\n[dim]{PortfolioBuilder.print_portfolio_report(result.positions)}[/dim]")

        return {
            "status":        "ok",
            "positions":     len(result.positions),
            "runs_used":     result.n_runs_used,
            "candidates":    result.n_candidates_evaluated,
        }

    # ══════════════════════════════════════════════════════════
    # TRYB 3: ANALYZE — historia uruchomień
    # ══════════════════════════════════════════════════════════

    def analyze_history(self) -> None:
        """Pokaż analizę historyczną uruchomień i częstości tickerów."""
        from rich.console import Console
        from rich.table import Table
        from rich import box

        c = Console()
        runs_df = self.repository.get_all_runs()
        if runs_df.empty:
            c.print("[yellow]Brak historycznych uruchomień.[/yellow]")
            return

        c.print(f"\n[bold]Historia uruchomień:[/bold] {len(runs_df)} łącznie\n")
        t = Table(box=box.SIMPLE, title="Ostatnie 10 uruchomień")
        for col in runs_df.columns:
            t.add_column(str(col), justify="right" if col in ["fetched", "passed", "errors"] else "left")
        for _, row in runs_df.tail(10).iterrows():
            t.add_row(*[str(v) for v in row])
        c.print(t)

        appearances = self.repository.get_ticker_appearances()
        if not appearances.empty:
            c.print(f"\n[bold]Top 20 najczęściej pojawiających się spółek:[/bold]\n")
            t2 = Table(box=box.SIMPLE)
            t2.add_column("Ticker", style="cyan")
            t2.add_column("Wystąpień", justify="right")
            t2.add_column("Freq", justify="right", style="green")
            t2.add_column("Avg Score", justify="right")
            for _, row in appearances.head(20).iterrows():
                t2.add_row(
                    row["ticker"], str(row["appearances"]),
                    f"{row['frequency']:.0%}",
                    f"{row['avg_score']:.4f}" if pd.notna(row.get("avg_score")) else "N/A",
                )
            c.print(t2)

    def schedule(self) -> None:
        from scheduler.runner import start_scheduler
        start_scheduler(self.run, self.config.get("scheduler", {}))

    def _print_validation_summary(
        self, raw: list[str], valid: list[str], invalid: list[str]
    ) -> None:
        pct = len(valid) / len(raw) * 100 if raw else 0
        logger.info(
            f"Walidacja: {len(valid)}/{len(raw)} valid ({pct:.0f}%)  "
            f"odrzucone: {len(invalid)}"
        )
        if invalid:
            sample = ", ".join(invalid[:8]) + ("…" if len(invalid) > 8 else "")
            logger.warning(f"Niedziałające tickery (zapisane w DB): {sample}")

    def _finalize_run(self, run_id: int, passed: int, duration: float) -> None:
        from db.models import ScreeningRun
        with self.repository._session_factory() as session:
            run = session.get(ScreeningRun, run_id)
            if run:
                run.total_tickers_passed = passed
                run.duration_seconds     = round(duration, 2)
                run.notes                = "Zakończone pomyślnie"
                session.commit()


# ══════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Stock Screener – AI-Powered, portfel z historii DB",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--config", default=None)

    # Screening options
    parser.add_argument("--strategy",
        choices=["growth_quality","deep_value","compounders",
                 "sector_leaders","thematic","global_diversified",
                 "emerging_growth","asymmetric_risk"])
    parser.add_argument("--backend", choices=["groq","anthropic","openai","mock"])
    parser.add_argument("--n", type=int, dest="n_tickers")
    parser.add_argument("--sector", default=None)
    parser.add_argument("--theme", default=None)
    parser.add_argument("--multi-shot", action="store_true")

    # Portfolio / analysis
    parser.add_argument("--build-portfolio", action="store_true",
        help="Zbuduj portfel inwestycyjny z historii DB (zamiast screeningu)")
    parser.add_argument("--runs", type=int, default=None,
        help="Liczba ostatnich runów do analizy przy --build-portfolio")
    parser.add_argument("--analyze", action="store_true")
    parser.add_argument("--schedule", action="store_true")
    parser.add_argument("--top", type=int, default=20)
    return parser.parse_args()


def main() -> None:
    import pandas as pd  # needed for analyze_history

    args = parse_args()
    config = load_config(args.config)

    # Nadpisz parametry z CLI
    ai_cfg = config.setdefault("source", {}).setdefault("ai", {})
    if args.strategy:
        config["source"]["strategy"] = args.strategy
        ai_cfg["strategy"] = args.strategy
    if args.backend:
        ai_cfg["backend"] = args.backend
    if args.n_tickers:
        ai_cfg["n_tickers"] = args.n_tickers
    if args.sector:
        ai_cfg["sector"] = args.sector
    if args.theme:
        ai_cfg["theme"] = args.theme
    if args.multi_shot:
        ai_cfg["multi_shot"] = True

    pipeline = ScreenerPipeline(config)

    if args.build_portfolio:
        pipeline.build_portfolio(n_last_runs=args.runs)
    elif args.analyze:
        pipeline.analyze_history()
    elif args.schedule:
        pipeline.schedule()
    else:
        pipeline.run()


if __name__ == "__main__":
    import pandas as pd
    main()
