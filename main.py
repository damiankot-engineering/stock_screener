"""
main.py
Główny punkt wejścia systemu. Orkiestruje cały pipeline screenera.

ARCHITEKTURA PIPELINE:
═══════════════════════════════════════════════════════════════
  [1] KONFIGURACJA      → Wczytaj user_config.yaml
  [2] AI TICKER SOURCE  → LLM generuje listę tickerów wg strategii
  [3] DATA FETCHER      → Pobierz dane fundamentalne + techniczne
  [4] DB SAVE           → Zapisz snapshoty metryk (inkrementacyjnie)
  [5] FILTER ENGINE     → Odfiltruj wg progów użytkownika
  [6] SCORER            → Oblicz score i ranking
  [7] DB SAVE           → Zapisz wyniki screeningu
  [8] PORTFOLIO BUILDER → Zbuduj portfel (+ bonus stabilności)
  [9] DB SAVE           → Zapisz skład portfela
  [10] REPORTER         → Wydrukuj wyniki + zapisz CSV
═══════════════════════════════════════════════════════════════

UŻYCIE:
  python main.py                           # Jedno uruchomienie (domyślna konfiguracja)
  python main.py --config my_config.yaml   # Własna konfiguracja
  python main.py --strategy compounders    # Nadpisz strategię AI
  python main.py --strategy thematic --theme "clean energy"
  python main.py --strategy sector_leaders --sector healthcare
  python main.py --n 80                    # Nadpisz liczbę tickerów
  python main.py --backend mock            # Tryb testowy (bez API key)
  python main.py --multi-shot              # Szersze universum (3× więcej tickerów)
  python main.py --schedule                # Uruchom z harmonogramem
  python main.py --analyze                 # Pokaż analizę historyczną
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

# Dodaj katalog projektu do PYTHONPATH
sys.path.insert(0, str(Path(__file__).parent))

from config.settings import load_config, setup_logging
from data.ticker_source import get_tickers
from data.fetcher import DataFetcher
from db.models import create_db_engine, get_session_factory
from db.repository import ScreenerRepository
from screening.filter_engine import FilterEngine
from screening.scorer import Scorer
from portfolio.builder import PortfolioBuilder
from reports.reporter import Reporter

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# Klasa Pipeline
# ─────────────────────────────────────────────────────────────

class ScreenerPipeline:
    """
    Główny orchestrator całego systemu.
    Można uruchomić raz (run()) lub zharmonogramować (schedule()).
    """

    def __init__(self, config: dict):
        self.config = config
        settings = config.get("settings", {})

        # Konfiguracja logowania
        setup_logging(settings.get("log_level", "INFO"))

        # Inicjalizacja komponentów
        db_path = settings.get("db_path", "screener_data.db")
        engine = create_db_engine(db_path)
        session_factory = get_session_factory(engine)

        self.repository = ScreenerRepository(session_factory)
        self.fetcher = DataFetcher(config)
        self.filter_engine = FilterEngine(config)
        self.scorer = Scorer(config)
        self.portfolio_builder = PortfolioBuilder(config, repository=self.repository)
        self.reporter = Reporter(settings.get("reports_dir", "reports"))

        logger.info("Pipeline zainicjalizowany pomyślnie")

    def run(self) -> dict:
        """
        Uruchom pełny pipeline screenera.
        Zwraca słownik z wynikami (run_id, portfolio, stats).
        """
        start_time = time.time()
        source_config = self.config.get("source", {})
        strategy = source_config.get("strategy", "growth_quality")
        backend = source_config.get("ai", {}).get("backend", "groq")
        n_tickers = source_config.get("ai", {}).get("n_tickers", 50)
        source_name = f"ai_{strategy}"

        # ── Krok 1: AI generuje listę tickerów ──────────────
        logger.info("=" * 60)
        logger.info(f"KROK 1: AI Ticker Source [backend={backend}, strategia={strategy}, n={n_tickers}]")
        tickers = get_tickers(source_config)
        self.reporter.print_header(f"AI:{strategy}@{backend}", len(tickers))

        # ── Krok 2: Pobierz dane ─────────────────────────────
        logger.info("=" * 60)
        logger.info(f"KROK 2: Pobieranie danych dla {len(tickers)} tickerów")
        ticker_data_list = self.fetcher.fetch_all(tickers)

        fetch_errors = sum(1 for td in ticker_data_list if not td.success)
        successful_data = [td for td in ticker_data_list if td.success]
        logger.info(f"Dane pobrane: {len(successful_data)}/{len(tickers)} sukces")

        # ── Krok 3: Zapisz metadane uruchomienia ─────────────
        logger.info("=" * 60)
        logger.info("KROK 3: Zapis do bazy danych (metadane uruchomienia)")
        # Tymczasowy run_id (zaktualizujemy po filtracji)
        temp_run_id = self.repository.save_run(
            source_index=source_name,
            config=self.config,
            total_fetched=len(successful_data),
            total_passed=0,  # zaktualizujemy później
            fetch_errors=fetch_errors,
            duration=0.0,    # zaktualizujemy później
            notes="W trakcie wykonywania",
        )

        # ── Krok 4: Zapisz snapshoty metryk ──────────────────
        logger.info("KROK 4: Zapis snapshotów metryk")
        self.repository.save_metric_snapshots(temp_run_id, ticker_data_list)

        # ── Krok 5: Filtrowanie ───────────────────────────────
        logger.info("=" * 60)
        logger.info("KROK 5: Filtrowanie wg progów")
        logger.info(self.filter_engine.get_filter_summary())
        passed_results, rejected_results = self.filter_engine.apply_batch(successful_data)
        self.reporter.print_filter_summary(
            len(passed_results), len(rejected_results), len(successful_data)
        )

        if not passed_results:
            logger.warning("⚠️  Żaden ticker nie przeszedł filtrów! "
                           "Sprawdź ustawienia filtrów w konfiguracji.")
            # Zaktualizuj run jako ukończony z 0 wynikami
            self._finalize_run(temp_run_id, 0, time.time() - start_time)
            return {"run_id": temp_run_id, "passed": 0, "portfolio": []}

        # ── Krok 6: Scoring i ranking ─────────────────────────
        logger.info("=" * 60)
        logger.info("KROK 6: Scoring i ranking")
        scored_tickers = self.scorer.score_and_rank(passed_results)

        # ── Krok 7: Zapisz wyniki screeningu ─────────────────
        logger.info("KROK 7: Zapis wyników screeningu")
        results_to_save = [st.to_dict() for st in scored_tickers]
        self.repository.save_screening_results(temp_run_id, results_to_save)

        # ── Krok 8: Wyświetl wyniki ───────────────────────────
        self.reporter.print_screening_results(scored_tickers, top_n=20)

        # ── Krok 9: Budowa portfela ───────────────────────────
        logger.info("=" * 60)
        logger.info("KROK 9: Budowa portfela inwestycyjnego")
        previous_portfolio = self.repository.get_last_portfolio()
        portfolio_positions = self.portfolio_builder.build(scored_tickers, previous_portfolio)
        self.reporter.print_portfolio(portfolio_positions)

        # ── Krok 10: Zapisz portfel ───────────────────────────
        if portfolio_positions:
            portfolio_dicts = self.portfolio_builder.to_dict_list(portfolio_positions)
            self.repository.save_portfolio(temp_run_id, portfolio_dicts, previous_portfolio)

        # ── Krok 11: Raporty CSV ──────────────────────────────
        duration = time.time() - start_time
        screening_csv = self.reporter.save_screening_csv(scored_tickers, temp_run_id, source_name)
        portfolio_csv = self.reporter.save_portfolio_csv(portfolio_positions, temp_run_id, source_name)

        # Analiza historyczna
        appearances = self.repository.get_ticker_appearances()
        if not appearances.empty:
            self.reporter.save_historical_analysis_csv(appearances, temp_run_id)

        # ── Aktualizuj run z finalnymi statystykami ───────────
        self._finalize_run(temp_run_id, len(passed_results), duration)
        self.reporter.print_run_summary(temp_run_id, duration, len(portfolio_positions))

        logger.info(f"Pipeline zakończony w {duration:.1f}s")
        logger.info(f"Raporty: {screening_csv}, {portfolio_csv}")

        return {
            "run_id": temp_run_id,
            "source": source_name,
            "fetched": len(successful_data),
            "passed": len(passed_results),
            "portfolio_size": len(portfolio_positions),
            "portfolio": portfolio_positions,
            "duration": duration,
        }

    def _finalize_run(self, run_id: int, passed: int, duration: float) -> None:
        """Zaktualizuj metadane uruchomienia po jego zakończeniu."""
        # Bezpośrednia aktualizacja przez session
        from db.models import ScreeningRun
        with self.repository._session_factory() as session:
            run = session.get(ScreeningRun, run_id)
            if run:
                run.total_tickers_passed = passed
                run.duration_seconds = round(duration, 2)
                run.notes = "Zakończone pomyślnie"
                session.commit()

    def analyze_history(self) -> None:
        """Pokaż analizę historyczną wyników screenera."""
        from rich.console import Console
        from rich import box
        from rich.table import Table

        c = Console()

        runs_df = self.repository.get_all_runs()
        if runs_df.empty:
            c.print("[yellow]Brak historycznych uruchomień w bazie.[/yellow]")
            return

        c.print(f"\n[bold]Historia uruchomień:[/bold] {len(runs_df)} łącznie\n")

        # Tabela uruchomień
        table = Table(box=box.SIMPLE, title="Historia screenera")
        for col in runs_df.columns:
            table.add_column(str(col), justify="right" if col in ["fetched", "passed", "errors"] else "left")
        for _, row in runs_df.tail(10).iterrows():
            table.add_row(*[str(v) for v in row])
        c.print(table)

        # Częstość pojawiania się tickerów
        appearances = self.repository.get_ticker_appearances()
        if not appearances.empty:
            c.print(f"\n[bold]Top 15 najczęściej pojawiających się spółek:[/bold]\n")
            top15 = appearances.head(15)
            t2 = Table(box=box.SIMPLE)
            t2.add_column("Ticker", style="cyan")
            t2.add_column("Wystąpień", justify="right")
            t2.add_column("Freq", justify="right", style="green")
            t2.add_column("Avg Score", justify="right")
            for _, row in top15.iterrows():
                t2.add_row(
                    row["ticker"],
                    str(row["appearances"]),
                    f"{row['frequency']:.0%}",
                    f"{row['avg_score']:.4f}" if row['avg_score'] else "N/A",
                )
            c.print(t2)

    def schedule(self) -> None:
        """Uruchom pipeline według harmonogramu z konfiguracji."""
        from scheduler.runner import start_scheduler
        scheduler_config = self.config.get("scheduler", {})
        start_scheduler(self.run, scheduler_config)


# ─────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Stock Screener – selekcja akcji generowanych przez AI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--config", default=None,
        help="Ścieżka do pliku konfiguracyjnego YAML",
    )
    parser.add_argument(
        "--strategy",
        choices=["growth_quality", "deep_value", "compounders",
                 "sector_leaders", "thematic", "global_diversified"],
        help="Nadpisz strategię AI z linii komend",
    )
    parser.add_argument(
        "--backend",
        choices=["groq", "anthropic", "openai", "mock"],
        help="Nadpisz backend LLM (mock = bez API key, do testów)",
    )
    parser.add_argument(
        "--n", type=int, dest="n_tickers",
        help="Liczba tickerów do wygenerowania przez AI",
    )
    parser.add_argument(
        "--sector", default=None,
        help="Sektor dla strategii sector_leaders (np. healthcare)",
    )
    parser.add_argument(
        "--theme", default=None,
        help="Temat dla strategii thematic (np. 'clean energy')",
    )
    parser.add_argument(
        "--multi-shot", action="store_true",
        help="Tryb multi-shot: więcej zapytań AI = szersze universum spółek",
    )
    parser.add_argument(
        "--schedule", action="store_true",
        help="Uruchom z automatycznym harmonogramem",
    )
    parser.add_argument(
        "--analyze", action="store_true",
        help="Pokaż analizę historyczną (bez nowego screeningu)",
    )
    parser.add_argument(
        "--top", type=int, default=20,
        help="Liczba top wyników do wyświetlenia",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = load_config(args.config)

    # Nadpisz parametry AI z CLI
    ai_cfg = config.setdefault("source", {}).setdefault("ai", {})
    if args.strategy:
        config["source"]["strategy"] = args.strategy
        ai_cfg["strategy"] = args.strategy
        logger.info(f"Strategia AI nadpisana przez CLI: {args.strategy}")
    if args.backend:
        ai_cfg["backend"] = args.backend
        logger.info(f"Backend AI nadpisany przez CLI: {args.backend}")
    if args.n_tickers:
        ai_cfg["n_tickers"] = args.n_tickers
    if args.sector:
        ai_cfg["sector"] = args.sector
    if args.theme:
        ai_cfg["theme"] = args.theme
    if args.multi_shot:
        ai_cfg["multi_shot"] = True

    # Inicjalizacja pipeline
    pipeline = ScreenerPipeline(config)

    if args.analyze:
        pipeline.analyze_history()
    elif args.schedule:
        pipeline.schedule()
    else:
        pipeline.run()


if __name__ == "__main__":
    main()
