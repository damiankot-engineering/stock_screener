"""
db/repository.py
Warstwa dostępu do danych (Repository Pattern).
Wszystkie operacje na bazie danych są tu zgrupowane.
Logika biznesowa (screener, portfolio) NIE powinna bezpośrednio używać SQLAlchemy.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

import pandas as pd
from sqlalchemy.orm import Session

from .models import (
    MetricSnapshot, PortfolioSnapshot,
    ScreeningResult, ScreeningRun,
)

logger = logging.getLogger(__name__)


class ScreenerRepository:
    """
    Centralne repozytorium operacji bazodanowych.
    Implementuje inkrementalne zapisywanie – nigdy nie nadpisuje historycznych danych.
    """

    def __init__(self, session_factory):
        self._session_factory = session_factory

    # ─────────────────────────────────────────────────────────
    # Operacje zapisu
    # ─────────────────────────────────────────────────────────

    def save_run(
        self,
        source_index: str,
        config: dict,
        total_fetched: int,
        total_passed: int,
        fetch_errors: int,
        duration: float,
        notes: str = "",
    ) -> int:
        """Zapisz metadane uruchomienia. Zwróć ID nowego rekordu."""
        with self._session_factory() as session:
            run = ScreeningRun(
                run_timestamp=datetime.utcnow(),
                source_index=source_index,
                total_tickers_fetched=total_fetched,
                total_tickers_passed=total_passed,
                fetch_errors_count=fetch_errors,
                config_snapshot=json.dumps(config, default=str),
                duration_seconds=round(duration, 2),
                notes=notes,
            )
            session.add(run)
            session.commit()
            session.refresh(run)
            run_id = run.id
            logger.info(f"Zapisano run ID={run_id} ({source_index}, passed={total_passed})")
            return run_id

    def save_metric_snapshots(
        self,
        run_id: int,
        ticker_data_list: list,  # lista TickerData
    ) -> int:
        """
        Zapisz snapshoty metryk dla wszystkich tickerów.
        Używa bulk insert dla wydajności.
        Zwróć liczbę zapisanych rekordów.
        """
        from data.fetcher import TickerData

        rows = []
        for td in ticker_data_list:
            if not td.success:
                continue
            for metric_name, value in td.fundamentals.items():
                rows.append({
                    "run_id": run_id,
                    "ticker": td.ticker,
                    "metric_name": metric_name,
                    "metric_value": value,
                    "metric_type": "fundamental",
                })
            for metric_name, value in td.technicals.items():
                rows.append({
                    "run_id": run_id,
                    "ticker": td.ticker,
                    "metric_name": metric_name,
                    "metric_value": value,
                    "metric_type": "technical",
                })

        if not rows:
            logger.warning("Brak danych do zapisania w metric_snapshots")
            return 0

        with self._session_factory() as session:
            session.bulk_insert_mappings(MetricSnapshot, rows)
            session.commit()

        logger.info(f"Zapisano {len(rows)} rekordów metryk dla run_id={run_id}")
        return len(rows)

    def save_screening_results(
        self,
        run_id: int,
        results: list[dict],
    ) -> int:
        """
        Zapisz wyniki screeningu (tickery, które przeszły filtry).

        Args:
            run_id: ID bieżącego uruchomienia
            results: lista słowników {ticker, score, rank, passed_fundamental,
                     passed_technical, failed_filters, metrics}
        """
        rows = []
        for r in results:
            rows.append({
                "run_id": run_id,
                "ticker": r["ticker"],
                "score": r.get("score"),
                "rank": r.get("rank"),
                "passed_fundamental": r.get("passed_fundamental", True),
                "passed_technical": r.get("passed_technical", True),
                "failed_filters": json.dumps(r.get("failed_filters", [])),
                "metric_values_json": json.dumps(r.get("metrics", {}), default=str),
            })

        with self._session_factory() as session:
            session.bulk_insert_mappings(ScreeningResult, rows)
            session.commit()

        logger.info(f"Zapisano {len(rows)} wyników screeningu dla run_id={run_id}")
        return len(rows)

    def save_portfolio(
        self,
        run_id: int,
        portfolio: list[dict],
        previous_tickers: set[str] | None = None,
    ) -> int:
        """
        Zapisz skład portfela inwestycyjnego.

        Args:
            run_id: ID bieżącego uruchomienia
            portfolio: lista {ticker, weight, score, rank, stability_score}
            previous_tickers: zbiór tickerów z poprzedniego portfela (dla is_new_entry)
        """
        prev = previous_tickers or set()
        rows = []
        for p in portfolio:
            rows.append({
                "run_id": run_id,
                "ticker": p["ticker"],
                "weight": p["weight"],
                "score": p.get("score"),
                "rank": p.get("rank"),
                "stability_score": p.get("stability_score"),
                "is_new_entry": p["ticker"] not in prev,
            })

        with self._session_factory() as session:
            session.bulk_insert_mappings(PortfolioSnapshot, rows)
            session.commit()

        logger.info(f"Zapisano portfel z {len(rows)} pozycjami dla run_id={run_id}")
        return len(rows)

    # ─────────────────────────────────────────────────────────
    # Operacje odczytu – analiza historyczna
    # ─────────────────────────────────────────────────────────

    def get_all_runs(self) -> pd.DataFrame:
        """Zwróć historię wszystkich uruchomień jako DataFrame."""
        with self._session_factory() as session:
            runs = session.query(ScreeningRun).order_by(ScreeningRun.run_timestamp).all()
            return pd.DataFrame([{
                "run_id": r.id,
                "timestamp": r.run_timestamp,
                "source": r.source_index,
                "fetched": r.total_tickers_fetched,
                "passed": r.total_tickers_passed,
                "errors": r.fetch_errors_count,
                "duration_s": r.duration_seconds,
            } for r in runs])

    def get_ticker_history(self, ticker: str, metric_name: str) -> pd.DataFrame:
        """
        Pobierz historię wartości danej metryki dla tickera na przestrzeni uruchomień.
        Użyteczne do analizy stabilności parametrów.
        """
        with self._session_factory() as session:
            rows = (
                session.query(MetricSnapshot, ScreeningRun.run_timestamp)
                .join(ScreeningRun)
                .filter(MetricSnapshot.ticker == ticker)
                .filter(MetricSnapshot.metric_name == metric_name)
                .order_by(ScreeningRun.run_timestamp)
                .all()
            )
            return pd.DataFrame([{
                "timestamp": ts,
                "value": row.metric_value,
            } for row, ts in rows])

    def get_ticker_appearances(self, n_last_runs: int | None = None) -> pd.DataFrame:
        """
        Zlicz, ile razy każdy ticker pojawił się w wynikach screeningu.
        Kluczowe dla analizy stabilności portfela.

        Args:
            n_last_runs: jeśli podane, analizuj tylko N ostatnich uruchomień

        Returns:
            DataFrame z kolumnami: ticker, appearances, total_runs, frequency
        """
        with self._session_factory() as session:
            if n_last_runs:
                # Pobierz ID N ostatnich uruchomień
                last_runs = (
                    session.query(ScreeningRun.id)
                    .order_by(ScreeningRun.run_timestamp.desc())
                    .limit(n_last_runs)
                    .all()
                )
                run_ids = [r[0] for r in last_runs]
                total = len(run_ids)
                results = (
                    session.query(ScreeningResult.ticker,
                                  ScreeningResult.score)
                    .filter(ScreeningResult.run_id.in_(run_ids))
                    .all()
                )
            else:
                total = session.query(ScreeningRun).count()
                results = session.query(ScreeningResult.ticker,
                                        ScreeningResult.score).all()

        if not results:
            return pd.DataFrame(columns=["ticker", "appearances", "total_runs", "frequency",
                                          "avg_score"])

        df = pd.DataFrame(results, columns=["ticker", "score"])
        agg = df.groupby("ticker").agg(
            appearances=("ticker", "count"),
            avg_score=("score", "mean"),
        ).reset_index()
        agg["total_runs"] = total
        agg["frequency"] = (agg["appearances"] / total).round(4)
        return agg.sort_values("frequency", ascending=False).reset_index(drop=True)

    def get_last_portfolio(self) -> set[str]:
        """Pobierz tickery z ostatniego portfela (dla is_new_entry)."""
        with self._session_factory() as session:
            last_run = (
                session.query(ScreeningRun)
                .order_by(ScreeningRun.run_timestamp.desc())
                .first()
            )
            if not last_run:
                return set()
            portfolio = (
                session.query(PortfolioSnapshot.ticker)
                .filter(PortfolioSnapshot.run_id == last_run.id)
                .all()
            )
            return {p[0] for p in portfolio}

    def get_latest_screening_results(self) -> pd.DataFrame:
        """Pobierz wyniki screeningu z ostatniego uruchomienia."""
        with self._session_factory() as session:
            last_run = (
                session.query(ScreeningRun)
                .order_by(ScreeningRun.run_timestamp.desc())
                .first()
            )
            if not last_run:
                return pd.DataFrame()

            results = (
                session.query(ScreeningResult)
                .filter(ScreeningResult.run_id == last_run.id)
                .order_by(ScreeningResult.rank)
                .all()
            )
            rows = []
            for r in results:
                metrics = json.loads(r.metric_values_json or "{}")
                rows.append({
                    "ticker": r.ticker,
                    "rank": r.rank,
                    "score": r.score,
                    **metrics,
                })
            return pd.DataFrame(rows)

    def get_portfolio_evolution(self) -> pd.DataFrame:
        """
        Śledź ewolucję składu portfela w czasie.
        Zwraca DataFrame z historią wag każdego tickera.
        """
        with self._session_factory() as session:
            data = (
                session.query(
                    PortfolioSnapshot.ticker,
                    PortfolioSnapshot.weight,
                    PortfolioSnapshot.score,
                    PortfolioSnapshot.stability_score,
                    ScreeningRun.run_timestamp,
                )
                .join(ScreeningRun)
                .order_by(ScreeningRun.run_timestamp)
                .all()
            )
        return pd.DataFrame(data, columns=["ticker", "weight", "score",
                                            "stability_score", "timestamp"])

    def get_run_count(self) -> int:
        """Ile uruchomień jest w bazie."""
        with self._session_factory() as session:
            return session.query(ScreeningRun).count()
