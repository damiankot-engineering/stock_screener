"""
backtesting/engine.py
Silnik backtestingu portfela historycznego.

FILOZOFIA:
══════════════════════════════════════════════════════════════════
  Backtesting używa WYŁĄCZNIE danych historycznych z DB —
  nie odpytuje AI, nie uruchamia screenerów. Symuluje, co by
  się stało, gdybyś inwestował zgodnie z sygnałami które
  screener generował w przeszłości.

ALGORYTM:
  1. Pobierz wszystkie portfolio_snapshots z DB (historia buildów)
  2. Dla każdego buildu: pobierz ceny historyczne tickerów
  3. Symuluj inwestycję:
       - Dzień T=0:    kup portfel wg wag z buildu
       - Dzień T=next: rebalansuj do wag z następnego buildu
       - Ostatni build: trzymaj do end_date lub dzisiaj
  4. Oblicz dzienną wartość portfela (NAV = Net Asset Value)
  5. Pobierz benchmark (SPY / ^GSPC) za ten sam okres
  6. Oblicz metryki vs benchmark
══════════════════════════════════════════════════════════════════

OGRANICZENIA (świadome uproszczenia):
  - Brak kosztów transakcji (możliwe do skonfigurowania)
  - Brak podatków
  - Ceny otwarcia dnia następnego po sygnale (T+1 execution)
  - Rebalansowanie w całości (nie uwzględnia płynności)
  - Dywidendy wliczone (total return ceny z yfinance)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Callable

import numpy as np
import pandas as pd

from backtesting.metrics import compute_metrics, compute_monthly_returns

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════
# DATA CLASSES
# ══════════════════════════════════════════════════════════════

@dataclass
class PortfolioBuild:
    """Jeden snapshot składu portfela (z portfolio_snapshots w DB)."""
    build_date: datetime
    run_id: int
    positions: dict[str, float]   # ticker → weight (suma = 1.0)


@dataclass
class BacktestConfig:
    """Konfiguracja backtestingu."""
    initial_capital: float = 100_000.0
    benchmark_ticker: str = "SPY"
    transaction_cost_bps: float = 10.0    # 10 bps = 0.1% per strona
    t_plus_execution: int = 1             # realizacja T+1 po sygnale
    start_date: datetime | None = None    # None = data pierwszego buildu
    end_date:   datetime | None = None    # None = dzisiaj
    min_price_history_days: int = 200     # min sesji ceny dla tickera (~10 mies.)
    lookback_days: int = 730              # okno śledzenia wstecz gdy brak start_date


@dataclass
class BacktestResult:
    """Wyniki pełnego backtestingu."""
    config: BacktestConfig
    portfolio_values: pd.Series           # dzienna wartość portfela (NAV)
    benchmark_values: pd.Series | None    # dzienna wartość benchmarku
    metrics: dict                         # obliczone metryki
    benchmark_metrics: dict | None        # metryki benchmarku
    monthly_returns: pd.DataFrame         # miesięczne zwroty portfela
    rebalance_log: list[dict]             # log każdego rebalansowania
    position_history: pd.DataFrame        # historia wag pozycji
    n_builds_used: int                    # ile buildów portfela użyto
    tickers_missing_data: list[str]       # tickery bez danych cenowych
    warnings: list[str] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return (
            self.portfolio_values is not None
            and len(self.portfolio_values) >= 2
            and self.metrics.get("total_return") is not None
        )

    def summary_line(self) -> str:
        if not self.is_valid:
            return "Backtest nieudany — brak danych"
        m = self.metrics
        bm = self.benchmark_metrics or {}
        return (
            f"Total: {m['total_return']:+.1%}  "
            f"CAGR: {m['cagr']:+.1%}  "
            f"Sharpe: {m['sharpe_ratio']:.2f}  "
            f"MaxDD: {m['max_drawdown']:.1%}  "
            f"vs {self.config.benchmark_ticker}: "
            f"{bm.get('total_return', 0):+.1%}"
        )


# ══════════════════════════════════════════════════════════════
# PRICE FETCHER ABSTRACTION
# ══════════════════════════════════════════════════════════════

PriceFetcherFn = Callable[[str, datetime, datetime], pd.Series]
"""
Sygnatura funkcji pobierającej ceny.
Argumenty: ticker, start_date, end_date
Zwraca: Series z cenami zamknięcia (Close), indeksowana datami.
"""


def default_price_fetcher(
    ticker: str,
    start: datetime,
    end: datetime,
) -> pd.Series:
    """
    Domyślny fetcher cen — używa yfinance.
    Zwraca Series cen zamknięcia (auto-adjusted = total return).
    """
    try:
        import yfinance as yf
        hist = yf.Ticker(ticker).history(
            start=start.strftime("%Y-%m-%d"),
            end=(end + timedelta(days=5)).strftime("%Y-%m-%d"),
            auto_adjust=True,
        )
        if hist.empty or "Close" not in hist.columns:
            return pd.Series(dtype=float)
        close = hist["Close"].dropna()
        close.index = pd.to_datetime(close.index).tz_localize(None)
        return close
    except Exception as exc:
        logger.debug(f"Błąd pobierania cen {ticker}: {exc}")
        return pd.Series(dtype=float)


# ══════════════════════════════════════════════════════════════
# GŁÓWNA KLASA BACKTESTERA
# ══════════════════════════════════════════════════════════════

class BacktestEngine:
    """
    Silnik backtestingu — symuluje inwestowanie zgodnie z
    historycznymi sygnałami portfela z bazy danych.
    """

    def __init__(
        self,
        repository,
        config: BacktestConfig | None = None,
        price_fetcher: PriceFetcherFn | None = None,
    ):
        self.repository    = repository
        self.config        = config or BacktestConfig()
        self.price_fetcher = price_fetcher or default_price_fetcher

    # ──────────────────────────────────────────────────────────
    # Główna metoda
    # ──────────────────────────────────────────────────────────

    def run(self) -> BacktestResult:
        """
        Uruchom pełny backtest.
        Pobiera historię portfela z DB, symuluje inwestycję, oblicza metryki.
        """
        logger.info("Backtest: pobieranie historii portfela z DB...")
        builds = self._load_portfolio_builds()

        if not builds:
            logger.warning("Backtest: brak buildów portfela w DB. Uruchom --build-portfolio.")
            return self._empty_result("Brak buildów portfela w DB")

        logger.info(f"Backtest: znaleziono {len(builds)} buildów portfela")

        # Wyznacz zakres dat
        start_date = datetime.utcnow() - timedelta(days=self.config.lookback_days)
        end_date = self.config.end_date or datetime.utcnow()

        # Zbierz wszystkie unikalne tickery
        all_tickers = set()
        for b in builds:
            all_tickers.update(b.positions.keys())

        logger.info(f"Backtest: pobieranie cen dla {len(all_tickers)} tickerów + benchmark...")

        # Pobierz ceny dla wszystkich tickerów
        price_cache: dict[str, pd.Series] = {}
        missing: list[str] = []

        fetch_start = start_date - timedelta(days=10)  # trochę zapasu
        for ticker in sorted(all_tickers):
            prices = self.price_fetcher(ticker, fetch_start, end_date)
            if len(prices) >= self.config.min_price_history_days:
                price_cache[ticker] = prices
            else:
                logger.warning(f"Backtest: brak wystarczających danych cenowych dla {ticker}")
                missing.append(ticker)

        # Pobierz benchmark
        benchmark_prices: pd.Series | None = None
        try:
            bp = self.price_fetcher(self.config.benchmark_ticker, fetch_start, end_date)
            if len(bp) >= 2:
                benchmark_prices = bp
                logger.info(f"Backtest: benchmark {self.config.benchmark_ticker} pobrany ({len(bp)} sesji)")
        except Exception as exc:
            logger.warning(f"Backtest: nie udało się pobrać benchmarku: {exc}")

        # Uruchom symulację
        logger.info("Backtest: symulacja portfela...")
        portfolio_values, rebalance_log, position_history = self._simulate(
            builds=builds,
            price_cache=price_cache,
            start_date=start_date,
            end_date=end_date,
        )

        if portfolio_values is None or len(portfolio_values) < 2:
            return self._empty_result(
                "Symulacja zwróciła za mało danych — sprawdź dostępność cen"
            )

        # Wyrównaj benchmark do zakresu portfela
        benchmark_aligned: pd.Series | None = None
        if benchmark_prices is not None:
            common = portfolio_values.index.intersection(benchmark_prices.index)
            if len(common) >= 2:
                bm_raw = benchmark_prices.loc[common]
                # Znormalizuj do initial_capital
                benchmark_aligned = bm_raw / bm_raw.iloc[0] * self.config.initial_capital

        # Oblicz metryki
        metrics = compute_metrics(
            portfolio_values,
            benchmark_values=benchmark_aligned,
        )
        benchmark_metrics = compute_metrics(benchmark_aligned) if benchmark_aligned is not None else None
        monthly_returns = compute_monthly_returns(portfolio_values)

        warnings = []
        if missing:
            warnings.append(f"Brak danych cenowych dla: {', '.join(missing[:10])}"
                            + (f" i {len(missing)-10} więcej" if len(missing) > 10 else ""))

        result = BacktestResult(
            config=self.config,
            portfolio_values=portfolio_values,
            benchmark_values=benchmark_aligned,
            metrics=metrics,
            benchmark_metrics=benchmark_metrics,
            monthly_returns=monthly_returns,
            rebalance_log=rebalance_log,
            position_history=position_history,
            n_builds_used=len(builds),
            tickers_missing_data=missing,
            warnings=warnings,
        )

        logger.info(f"Backtest zakończony: {result.summary_line()}")
        return result

    # ──────────────────────────────────────────────────────────
    # Ładowanie buildów portfela z DB
    # ──────────────────────────────────────────────────────────

    def _load_portfolio_builds(self) -> list[PortfolioBuild]:
        """
        Pobierz historię buildów portfela z DB.
        Każdy build = jeden snapshot portfolio_snapshots powiązany
        z rekordem ScreeningRun o source_index='portfolio_build'.
        """
        try:
            df = self.repository.get_portfolio_builds_history()
        except AttributeError:
            # Fallback: użyj get_portfolio_evolution jeśli nowa metoda nie istnieje
            df = self.repository.get_portfolio_evolution()

        if df is None or df.empty:
            return []

        builds = []
        # Grupuj po run_id / timestamp
        group_col = "run_id" if "run_id" in df.columns else "timestamp"

        for key, grp in df.groupby(group_col):
            # Pobierz timestamp buildu
            if "timestamp" in grp.columns:
                ts = pd.to_datetime(grp["timestamp"].iloc[0])
            else:
                ts = datetime.utcnow()

            # Zbuduj słownik ticker→weight
            if "weight" in grp.columns and "ticker" in grp.columns:
                positions = dict(zip(grp["ticker"], grp["weight"]))
                # Normalizuj wagi (powinny sumować się do 1)
                total_w = sum(positions.values())
                if total_w > 0:
                    positions = {t: w / total_w for t, w in positions.items()}
            else:
                continue

            builds.append(PortfolioBuild(
                build_date=ts,
                run_id=int(key) if str(key).isdigit() else 0,
                positions=positions,
            ))

        # Sortuj chronologicznie
        builds.sort(key=lambda b: b.build_date)
        return builds

    # ──────────────────────────────────────────────────────────
    # Symulacja portfela
    # ──────────────────────────────────────────────────────────

    def _simulate(
        self,
        builds: list[PortfolioBuild],
        price_cache: dict[str, pd.Series],
        start_date: datetime,
        end_date: datetime,
    ) -> tuple[pd.Series | None, list[dict], pd.DataFrame]:
        """
        Symulacja inwestycji zgodnie z sekwencją buildów portfela.

        Każdy build definiuje skład portfela do następnego buildu.
        Zwraca (portfolio_values, rebalance_log, position_history).
        """
        if not price_cache:
            return None, [], pd.DataFrame()

        # Stwórz wspólny indeks dat (dni handlowe)
        all_dates = pd.DatetimeIndex([])
        for prices in price_cache.values():
            all_dates = all_dates.union(prices.index)
        all_dates = all_dates.sort_values()

        # Ogranicz do zakresu dat
        start_ts = pd.Timestamp(start_date)
        end_ts   = pd.Timestamp(end_date)
        all_dates = all_dates[(all_dates >= start_ts) & (all_dates <= end_ts)]

        if len(all_dates) < 2:
            return None, [], pd.DataFrame()

        # Macierz cen (daty × tickery), wypełniona forward fill
        tickers = sorted(price_cache.keys())
        price_df = pd.DataFrame(
            {t: price_cache[t] for t in tickers},
            index=all_dates,
        ).ffill().bfill()

        # ── Symulacja ─────────────────────────────────────────
        capital = self.config.initial_capital
        holdings: dict[str, float] = {}  # ticker → liczba jednostek
        portfolio_nav: dict[pd.Timestamp, float] = {}
        rebalance_log: list[dict] = []
        pos_history: list[dict] = []

        # Wyznacz okresy rebalansowania
        # build[i] aktywny od build[i].date do build[i+1].date
        rebalance_dates = [pd.Timestamp(b.build_date) for b in builds]
        rebalance_dates.append(pd.Timestamp(end_date) + timedelta(days=1))  # sentinel

        current_build_idx = 0
        current_build = builds[0]

        for i, dt in enumerate(all_dates):
            # Sprawdź czy należy rebalansować (z T+1 uwzględnieniem)
            while (current_build_idx + 1 < len(builds) and
                   dt >= rebalance_dates[current_build_idx + 1]):
                current_build_idx += 1
                current_build = builds[current_build_idx]

                # Wykonaj rebalansowanie
                rebal_info = self._rebalance(
                    holdings=holdings,
                    target_weights=current_build.positions,
                    prices=price_df.loc[dt] if dt in price_df.index else None,
                    capital=capital,
                    dt=dt,
                )
                holdings = rebal_info["new_holdings"]
                cost = rebal_info["transaction_cost"]
                capital -= cost
                rebalance_log.append({
                    "date":             dt.isoformat(),
                    "run_id":           current_build.run_id,
                    "n_positions":      len(holdings),
                    "transaction_cost": round(cost, 2),
                    "tickers_in":       rebal_info["tickers_in"],
                    "tickers_out":      rebal_info["tickers_out"],
                })

            # Inicjalizacja przy pierwszej dacie
            if i == 0 and not holdings:
                if dt in price_df.index:
                    rebal_info = self._rebalance(
                        holdings={},
                        target_weights=current_build.positions,
                        prices=price_df.loc[dt],
                        capital=capital,
                        dt=dt,
                    )
                    holdings = rebal_info["new_holdings"]
                    capital -= rebal_info["transaction_cost"]
                    rebalance_log.append({
                        "date":             dt.isoformat(),
                        "run_id":           current_build.run_id,
                        "n_positions":      len(holdings),
                        "transaction_cost": round(rebal_info["transaction_cost"], 2),
                        "tickers_in":       rebal_info["tickers_in"],
                        "tickers_out":      [],
                        "note":             "initial",
                    })

            # Oblicz wartość portfela na koniec dnia
            if dt in price_df.index and holdings:
                nav = sum(
                    holdings.get(t, 0) * float(price_df.loc[dt, t])
                    for t in holdings
                    if t in price_df.columns
                )
                portfolio_nav[dt] = nav

                # Historia pozycji (co 5 sesji aby nie eksplodował rozmiar)
                if i % 5 == 0:
                    pos_row = {"date": dt}
                    total = nav if nav > 0 else 1
                    for t in tickers:
                        if t in holdings:
                            pos_row[t] = holdings[t] * float(price_df.loc[dt, t]) / total
                        else:
                            pos_row[t] = 0.0
                    pos_history.append(pos_row)

        if not portfolio_nav:
            return None, rebalance_log, pd.DataFrame()

        pv = pd.Series(portfolio_nav).sort_index()
        position_df = pd.DataFrame(pos_history).set_index("date") if pos_history else pd.DataFrame()

        return pv, rebalance_log, position_df

    def _rebalance(
        self,
        holdings: dict[str, float],
        target_weights: dict[str, float],
        prices: pd.Series | None,
        capital: float,
        dt,
    ) -> dict:
        """
        Wykonaj rebalansowanie — oblicz nowe holdings i koszty transakcji.
        """
        if prices is None:
            return {
                "new_holdings": holdings,
                "transaction_cost": 0.0,
                "tickers_in": [],
                "tickers_out": [],
            }

        # Oblicz bieżącą wartość portfela
        current_value = sum(
            holdings.get(t, 0) * float(prices.get(t, 0))
            for t in holdings
        )
        total_capital = max(current_value, capital)

        new_holdings: dict[str, float] = {}
        total_turnover = 0.0
        tickers_in  = []
        tickers_out = []

        # Nowe pozycje wg wag
        for ticker, weight in target_weights.items():
            price = float(prices.get(ticker, 0)) if ticker in prices.index else 0.0
            if price <= 0:
                continue
            target_value = total_capital * weight
            new_units = target_value / price
            new_holdings[ticker] = new_units

            old_value = holdings.get(ticker, 0) * price
            new_value = new_units * price
            total_turnover += abs(new_value - old_value)

            if ticker not in holdings or holdings[ticker] == 0:
                tickers_in.append(ticker)

        # Tickery które wypadają z portfela
        for ticker in holdings:
            if ticker not in target_weights:
                tickers_out.append(ticker)
                price = float(prices.get(ticker, 0)) if ticker in prices.index else 0.0
                total_turnover += holdings[ticker] * price

        # Koszty transakcji
        cost_rate = self.config.transaction_cost_bps / 10_000
        transaction_cost = total_turnover * cost_rate

        return {
            "new_holdings":     new_holdings,
            "transaction_cost": transaction_cost,
            "tickers_in":       tickers_in,
            "tickers_out":      tickers_out,
        }

    def _empty_result(self, reason: str) -> BacktestResult:
        from backtesting.metrics import _empty_metrics
        return BacktestResult(
            config=self.config,
            portfolio_values=pd.Series(dtype=float),
            benchmark_values=None,
            metrics=_empty_metrics(),
            benchmark_metrics=None,
            monthly_returns=pd.DataFrame(),
            rebalance_log=[],
            position_history=pd.DataFrame(),
            n_builds_used=0,
            tickers_missing_data=[],
            warnings=[reason],
        )
