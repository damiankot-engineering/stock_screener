"""
test_backtesting.py
Testy backtestingu — używają syntetycznych danych, bez dostępu do sieci.

Pokryte przypadki:
  - Metryki (CAGR, Sharpe, Sortino, max drawdown, Calmar, alpha/beta)
  - Symulacja portfela (NAV, rebalansowanie, koszty transakcji)
  - Ładowanie buildów z DB (mock repozytorium)
  - Edge cases: pusty portfel, brakujące dane cenowe, jeden build
  - Miesięczne zwroty
  - Zapis i odczyt BacktestRun z prawdziwej in-memory SQLite
"""
from __future__ import annotations

import sys
import math
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))


# ══════════════════════════════════════════════════════════════
# Helpers — generowanie syntetycznych danych
# ══════════════════════════════════════════════════════════════

def make_price_series(
    start: str = "2022-01-03",
    periods: int = 504,          # ~2 lata sesji
    annual_return: float = 0.10,
    annual_vol: float = 0.20,
    seed: int = 42,
) -> pd.Series:
    """Generuj syntetyczną serię cen metodą GBM (geometric Brownian motion)."""
    rng = np.random.default_rng(seed)
    dt = 1 / 252
    mu = annual_return
    sigma = annual_vol
    daily_returns = rng.normal(
        loc=(mu - 0.5 * sigma ** 2) * dt,
        scale=sigma * np.sqrt(dt),
        size=periods,
    )
    prices = 100.0 * np.exp(np.cumsum(daily_returns))
    dates = pd.bdate_range(start=start, periods=periods)
    return pd.Series(prices, index=dates)


def make_portfolio_builds_df(
    tickers: list[str],
    build_dates: list[str],
    weights: list[list[float]] | None = None,
) -> pd.DataFrame:
    """Stwórz DataFrame z historią buildów portfela (format get_portfolio_builds_history)."""
    rows = []
    for i, (date_str, run_id) in enumerate(zip(build_dates, range(1, len(build_dates) + 1))):
        ts = datetime.fromisoformat(date_str)
        w = weights[i] if weights else [1.0 / len(tickers)] * len(tickers)
        for ticker, weight in zip(tickers, w):
            rows.append({
                "run_id":          run_id,
                "ticker":          ticker,
                "weight":          weight,
                "score":           0.5,
                "stability_score": 0.5,
                "timestamp":       ts,
            })
    return pd.DataFrame(rows)


def make_mock_repository(builds_df: pd.DataFrame) -> MagicMock:
    """Stwórz mock repozytorium zwracający podany DataFrame buildów."""
    repo = MagicMock()
    repo.get_portfolio_builds_history.return_value = builds_df
    repo.get_portfolio_evolution.return_value = builds_df
    repo.save_backtest_run.return_value = 1
    return repo


# ══════════════════════════════════════════════════════════════
# Testy metryk
# ══════════════════════════════════════════════════════════════

class TestMetrics(unittest.TestCase):

    def _make_pv(self, annual_return=0.10, periods=504, seed=42):
        return make_price_series(annual_return=annual_return, periods=periods, seed=seed)

    def test_total_return_positive(self):
        from backtesting.metrics import compute_metrics
        # Use deterministic monotone series to guarantee positive return
        dates = pd.bdate_range("2022-01-03", periods=252)
        pv = pd.Series([100 * (1.20 ** (i / 252)) for i in range(252)], index=dates)
        m = compute_metrics(pv)
        self.assertGreater(m["total_return"], 0)

    def test_total_return_negative(self):
        from backtesting.metrics import compute_metrics
        pv = make_price_series(annual_return=-0.20, periods=252, seed=2)
        m = compute_metrics(pv)
        self.assertLess(m["total_return"], 0)

    def test_cagr_approximately_correct(self):
        from backtesting.metrics import compute_metrics
        # Deterministyczny portfel bez losowości: stały wzrost 10% rocznie
        dates = pd.bdate_range("2020-01-02", periods=504)
        prices = pd.Series(
            [100 * (1.10 ** (i / 252)) for i in range(504)],
            index=dates,
        )
        m = compute_metrics(prices)
        self.assertAlmostEqual(m["cagr"], 0.10, delta=0.005)

    def test_max_drawdown_negative(self):
        from backtesting.metrics import compute_metrics
        pv = make_price_series(annual_vol=0.30, seed=3)
        m = compute_metrics(pv)
        self.assertLess(m["max_drawdown"], 0)
        self.assertGreaterEqual(m["max_drawdown"], -1.0)

    def test_max_drawdown_flat_series(self):
        from backtesting.metrics import compute_metrics
        dates = pd.bdate_range("2022-01-03", periods=100)
        pv = pd.Series([100.0] * 100, index=dates)
        m = compute_metrics(pv)
        self.assertEqual(m["max_drawdown"], 0.0)

    def test_sharpe_higher_return_higher_sharpe(self):
        from backtesting.metrics import compute_metrics
        pv_low  = make_price_series(annual_return=0.05, annual_vol=0.15, seed=10)
        pv_high = make_price_series(annual_return=0.20, annual_vol=0.15, seed=11)
        m_low   = compute_metrics(pv_low)
        m_high  = compute_metrics(pv_high)
        self.assertGreater(m_high["sharpe_ratio"], m_low["sharpe_ratio"])

    def test_win_rate_between_0_and_1(self):
        from backtesting.metrics import compute_metrics
        pv = make_price_series(seed=5)
        m  = compute_metrics(pv)
        self.assertGreaterEqual(m["win_rate"], 0.0)
        self.assertLessEqual(m["win_rate"], 1.0)

    def test_calmar_ratio_positive_for_positive_cagr(self):
        from backtesting.metrics import compute_metrics
        pv = make_price_series(annual_return=0.25, annual_vol=0.15, seed=6)
        m  = compute_metrics(pv)
        if m["cagr"] > 0 and m["max_drawdown"] < 0:
            self.assertGreater(m["calmar_ratio"], 0)

    def test_alpha_beta_computed_when_benchmark_provided(self):
        from backtesting.metrics import compute_metrics
        pv = make_price_series(annual_return=0.15, seed=7)
        bm = make_price_series(annual_return=0.10, seed=8)
        m  = compute_metrics(pv, benchmark_values=bm)
        self.assertIsNotNone(m["alpha"])
        self.assertIsNotNone(m["beta"])

    def test_alpha_beta_none_without_benchmark(self):
        from backtesting.metrics import compute_metrics
        pv = make_price_series(seed=9)
        m  = compute_metrics(pv)
        self.assertIsNone(m["alpha"])
        self.assertIsNone(m["beta"])

    def test_empty_series_returns_empty_metrics(self):
        from backtesting.metrics import compute_metrics
        m = compute_metrics(pd.Series(dtype=float))
        self.assertIsNone(m["total_return"])
        self.assertIsNone(m["cagr"])

    def test_single_value_series_returns_empty_metrics(self):
        from backtesting.metrics import compute_metrics
        pv = pd.Series([100.0], index=pd.DatetimeIndex(["2022-01-03"]))
        m = compute_metrics(pv)
        self.assertIsNone(m["total_return"])

    def test_all_metric_keys_present(self):
        from backtesting.metrics import compute_metrics
        pv = make_price_series(seed=12)
        m  = compute_metrics(pv)
        required = [
            "total_return", "cagr", "volatility_ann", "sharpe_ratio",
            "sortino_ratio", "max_drawdown", "calmar_ratio", "win_rate",
            "best_period_return", "worst_period_return",
            "alpha", "beta", "max_dd_duration_days", "n_periods",
        ]
        for key in required:
            self.assertIn(key, m, f"Missing metric: {key}")

    def test_monthly_returns_shape(self):
        from backtesting.metrics import compute_monthly_returns
        pv  = make_price_series(periods=504)
        mdf = compute_monthly_returns(pv)
        self.assertIn("year",   mdf.columns)
        self.assertIn("month",  mdf.columns)
        self.assertIn("return", mdf.columns)
        self.assertGreater(len(mdf), 0)

    def test_monthly_returns_empty_for_short_series(self):
        from backtesting.metrics import compute_monthly_returns
        pv  = pd.Series([100.0], index=pd.DatetimeIndex(["2022-01-03"]))
        mdf = compute_monthly_returns(pv)
        self.assertEqual(len(mdf), 0)

    def test_max_dd_duration_zero_for_monotone_series(self):
        from backtesting.metrics import _max_drawdown_duration
        dates = pd.bdate_range("2022-01-03", periods=100)
        pv    = pd.Series(range(100, 200), index=dates, dtype=float)
        dur   = _max_drawdown_duration(pv)
        self.assertEqual(dur, 0)

    def test_max_dd_duration_positive_for_volatile_series(self):
        from backtesting.metrics import _max_drawdown_duration
        dates = pd.bdate_range("2022-01-03", periods=100)
        vals  = [100.0, 110.0, 90.0, 95.0, 105.0] * 20
        pv    = pd.Series(vals, index=dates)
        dur   = _max_drawdown_duration(pv)
        self.assertGreater(dur, 0)


# ══════════════════════════════════════════════════════════════
# Testy silnika backtestingu
# ══════════════════════════════════════════════════════════════

class TestBacktestEngine(unittest.TestCase):

    def _make_price_fetcher(self, price_series_map: dict[str, pd.Series]):
        """Fabryka mock price fetchera."""
        def fetcher(ticker: str, start, end) -> pd.Series:
            s = price_series_map.get(ticker, pd.Series(dtype=float))
            if len(s) == 0:
                return s
            mask = (s.index >= pd.Timestamp(start)) & (s.index <= pd.Timestamp(end))
            return s[mask]
        return fetcher

    def _make_engine(self, builds_df, price_map, config=None):
        from backtesting.engine import BacktestEngine, BacktestConfig
        repo   = make_mock_repository(builds_df)
        cfg    = config or BacktestConfig(initial_capital=10_000.0, transaction_cost_bps=0.0)
        engine = BacktestEngine(
            repository=repo,
            config=cfg,
            price_fetcher=self._make_price_fetcher(price_map),
        )
        return engine

    # ── Normalny przypadek: 2 tickery, 2 buildy ───────────────

    def test_basic_simulation_returns_valid_result(self):
        tickers = ["AAPL", "MSFT"]
        builds_df = make_portfolio_builds_df(
            tickers=tickers,
            build_dates=["2022-01-03", "2022-07-01"],
            weights=[[0.6, 0.4], [0.5, 0.5]],
        )
        price_map = {
            "AAPL": make_price_series(seed=1),
            "MSFT": make_price_series(seed=2, annual_return=0.12),
        }
        engine = self._make_engine(builds_df, price_map)
        result = engine.run()

        self.assertTrue(result.is_valid)
        self.assertGreater(len(result.portfolio_values), 0)
        self.assertIsNotNone(result.metrics["total_return"])
        self.assertGreater(result.n_builds_used, 0)

    def test_nav_starts_at_initial_capital(self):
        tickers = ["AAPL"]
        builds_df = make_portfolio_builds_df(
            tickers=tickers,
            build_dates=["2022-01-03"],
            weights=[[1.0]],
        )
        price_map = {"AAPL": make_price_series(seed=3)}
        engine = self._make_engine(builds_df, price_map)
        result = engine.run()

        self.assertTrue(result.is_valid)
        # NAV pierwszego dnia powinien być bliski initial_capital
        first_nav = result.portfolio_values.iloc[0]
        self.assertAlmostEqual(first_nav, 10_000.0, delta=500.0)

    def test_transaction_costs_reduce_nav(self):
        from backtesting.engine import BacktestConfig
        tickers = ["AAPL", "MSFT"]
        builds_df = make_portfolio_builds_df(
            tickers=tickers,
            build_dates=["2022-01-03", "2022-07-01"],
            weights=[[0.7, 0.3], [0.3, 0.7]],
        )
        price_map = {
            "AAPL": make_price_series(seed=10),
            "MSFT": make_price_series(seed=11),
        }
        # Bez kosztów
        engine_free = self._make_engine(
            builds_df, price_map,
            config=BacktestConfig(initial_capital=10_000.0, transaction_cost_bps=0.0)
        )
        result_free = engine_free.run()

        # Z kosztami 50 bps (wysoki celowo by test był wyraźny)
        engine_cost = self._make_engine(
            builds_df, price_map,
            config=BacktestConfig(initial_capital=10_000.0, transaction_cost_bps=50.0)
        )
        result_cost = engine_cost.run()

        if result_free.is_valid and result_cost.is_valid:
            self.assertGreater(
                result_free.portfolio_values.iloc[-1],
                result_cost.portfolio_values.iloc[-1],
                "Koszty transakcji powinny obniżać końcową wartość portfela"
            )

    def test_rebalance_log_has_entries(self):
        tickers = ["AAPL", "MSFT"]
        builds_df = make_portfolio_builds_df(
            tickers=tickers,
            build_dates=["2022-01-03", "2022-07-01"],
        )
        price_map = {
            "AAPL": make_price_series(seed=20),
            "MSFT": make_price_series(seed=21),
        }
        engine = self._make_engine(builds_df, price_map)
        result = engine.run()

        self.assertGreater(len(result.rebalance_log), 0)

    def test_missing_ticker_data_tracked(self):
        tickers = ["AAPL", "GHOST"]   # GHOST nie ma danych
        builds_df = make_portfolio_builds_df(
            tickers=tickers,
            build_dates=["2022-01-03"],
        )
        price_map = {"AAPL": make_price_series(seed=30)}
        engine = self._make_engine(builds_df, price_map)
        result = engine.run()

        self.assertIn("GHOST", result.tickers_missing_data)

    # ── Edge cases ─────────────────────────────────────────────

    def test_empty_builds_returns_invalid_result(self):
        from backtesting.engine import BacktestEngine, BacktestConfig
        repo   = MagicMock()
        repo.get_portfolio_builds_history.return_value = pd.DataFrame()
        repo.get_portfolio_evolution.return_value      = pd.DataFrame()
        engine = BacktestEngine(repository=repo, config=BacktestConfig())
        result = engine.run()
        self.assertFalse(result.is_valid)

    def test_single_build_single_ticker(self):
        tickers = ["AAPL"]
        builds_df = make_portfolio_builds_df(
            tickers=tickers,
            build_dates=["2022-01-03"],
            weights=[[1.0]],
        )
        price_map = {"AAPL": make_price_series(seed=40)}
        engine = self._make_engine(builds_df, price_map)
        result = engine.run()

        self.assertTrue(result.is_valid)
        self.assertEqual(result.n_builds_used, 1)

    def test_weights_sum_to_one_after_normalization(self):
        from backtesting.engine import BacktestEngine, BacktestConfig
        # Wagi nie sumują się do 1 — engine powinien znormalizować
        tickers = ["AAPL", "MSFT", "GOOG"]
        rows = []
        ts   = datetime(2022, 1, 3)
        for ticker, w in zip(tickers, [0.3, 0.3, 0.3]):  # suma = 0.9
            rows.append({"run_id": 1, "ticker": ticker, "weight": w,
                         "score": 0.5, "stability_score": 0.5, "timestamp": ts})
        builds_df = pd.DataFrame(rows)
        price_map = {t: make_price_series(seed=i) for i, t in enumerate(tickers)}

        repo   = make_mock_repository(builds_df)
        engine = BacktestEngine(
            repository=repo,
            config=BacktestConfig(initial_capital=10_000.0, transaction_cost_bps=0.0),
            price_fetcher=lambda t, s, e: price_map.get(t, pd.Series(dtype=float)),
        )
        result = engine.run()
        self.assertTrue(result.is_valid)

    def test_all_prices_missing_returns_invalid(self):
        tickers = ["GHOST1", "GHOST2"]
        builds_df = make_portfolio_builds_df(
            tickers=tickers,
            build_dates=["2022-01-03"],
        )
        price_map = {}  # brak danych dla wszystkich
        engine = self._make_engine(builds_df, price_map)
        result = engine.run()
        self.assertFalse(result.is_valid)

    def test_benchmark_present_when_fetcher_returns_data(self):
        tickers = ["AAPL"]
        builds_df = make_portfolio_builds_df(tickers=tickers, build_dates=["2022-01-03"])
        price_map = {
            "AAPL": make_price_series(seed=50),
            "SPY":  make_price_series(seed=51, annual_return=0.08),
        }
        engine = self._make_engine(builds_df, price_map)
        result = engine.run()

        if result.is_valid:
            self.assertIsNotNone(result.benchmark_values)
            self.assertIsNotNone(result.benchmark_metrics)

    def test_three_builds_two_rebalances(self):
        tickers = ["AAPL", "MSFT"]
        builds_df = make_portfolio_builds_df(
            tickers=tickers,
            build_dates=["2022-01-03", "2022-05-02", "2022-10-03"],
            weights=[[0.6, 0.4], [0.4, 0.6], [0.5, 0.5]],
        )
        price_map = {
            "AAPL": make_price_series(seed=60),
            "MSFT": make_price_series(seed=61),
        }
        engine = self._make_engine(builds_df, price_map)
        result = engine.run()

        self.assertTrue(result.is_valid)
        # Powinniśmy mieć co najmniej 2 rebalansowania (initial + 2 buildy)
        self.assertGreaterEqual(len(result.rebalance_log), 2)


# ══════════════════════════════════════════════════════════════
# Testy repozytorium (in-memory SQLite)
# ══════════════════════════════════════════════════════════════

try:
    import sqlalchemy as _sa
    _HAS_SQLALCHEMY = True
except ImportError:
    _HAS_SQLALCHEMY = False


@unittest.skipUnless(_HAS_SQLALCHEMY, "sqlalchemy not installed")
class TestBacktestRepository(unittest.TestCase):

    def setUp(self):
        """Stwórz in-memory SQLite z pełnym schematem."""
        import sys
        sys.path.insert(0, str(Path(__file__).parent))
        from db.models import create_db_engine, get_session_factory, Base
        from db.repository import ScreenerRepository

        self.engine  = create_db_engine(":memory:")
        session_factory = get_session_factory(self.engine)
        self.repo    = ScreenerRepository(session_factory)

    def _insert_portfolio_build(self, tickers_weights: dict, ts: datetime | None = None):
        """Pomocnik: wstaw screening run + portfolio snapshot do DB."""
        from db.models import ScreeningRun, PortfolioSnapshot
        from sqlalchemy.orm import sessionmaker
        Session = sessionmaker(bind=self.engine)
        with Session() as session:
            run = ScreeningRun(
                run_timestamp=ts or datetime.utcnow(),
                source_index="portfolio_build",
                total_tickers_fetched=len(tickers_weights),
                total_tickers_passed=len(tickers_weights),
                fetch_errors_count=0,
                duration_seconds=0.0,
            )
            session.add(run)
            session.flush()
            for ticker, weight in tickers_weights.items():
                snap = PortfolioSnapshot(
                    run_id=run.id,
                    ticker=ticker,
                    weight=weight,
                    score=0.5,
                    stability_score=0.5,
                    is_new_entry=True,
                )
                session.add(snap)
            session.commit()

    def test_get_portfolio_builds_history_empty(self):
        df = self.repo.get_portfolio_builds_history()
        self.assertIsInstance(df, pd.DataFrame)
        self.assertEqual(len(df), 0)

    def test_get_portfolio_builds_history_single_build(self):
        self._insert_portfolio_build({"AAPL": 0.6, "MSFT": 0.4})
        df = self.repo.get_portfolio_builds_history()
        self.assertEqual(len(df), 2)
        self.assertIn("ticker", df.columns)
        self.assertIn("weight", df.columns)
        self.assertIn("run_id", df.columns)
        self.assertIn("timestamp", df.columns)

    def test_get_portfolio_builds_history_multiple_builds(self):
        ts1 = datetime(2022, 1, 3)
        ts2 = datetime(2022, 7, 1)
        self._insert_portfolio_build({"AAPL": 0.5, "MSFT": 0.5}, ts=ts1)
        self._insert_portfolio_build({"GOOG": 0.4, "AMZN": 0.6}, ts=ts2)
        df = self.repo.get_portfolio_builds_history()
        self.assertEqual(len(df), 4)
        self.assertEqual(df["run_id"].nunique(), 2)

    def test_get_portfolio_builds_excludes_screening_runs(self):
        """Tylko portfolio_build runy powinny być zwrócone."""
        from db.models import ScreeningRun, PortfolioSnapshot, ScreeningResult
        from sqlalchemy.orm import sessionmaker
        Session = sessionmaker(bind=self.engine)
        with Session() as session:
            # Screening run (NIE portfolio_build)
            run = ScreeningRun(
                run_timestamp=datetime.utcnow(),
                source_index="ai_growth_quality",
                total_tickers_fetched=10,
                total_tickers_passed=5,
                fetch_errors_count=0,
            )
            session.add(run)
            session.commit()

        # Wstaw portfolio_build
        self._insert_portfolio_build({"AAPL": 1.0})
        df = self.repo.get_portfolio_builds_history()

        # Powinien być tylko 1 wiersz (z portfolio_build, nie ai_growth_quality)
        self.assertEqual(len(df), 1)

    def test_save_backtest_run_persists_to_db(self):
        from backtesting.engine import BacktestConfig, BacktestResult
        from backtesting.metrics import _empty_metrics

        config = BacktestConfig(initial_capital=50_000.0, benchmark_ticker="QQQ")
        pv = make_price_series(periods=252)
        from backtesting.metrics import compute_metrics
        metrics = compute_metrics(pv)

        result = BacktestResult(
            config=config,
            portfolio_values=pv,
            benchmark_values=None,
            metrics=metrics,
            benchmark_metrics=None,
            monthly_returns=pd.DataFrame(),
            rebalance_log=[],
            position_history=pd.DataFrame(),
            n_builds_used=3,
            tickers_missing_data=[],
        )
        bt_id = self.repo.save_backtest_run(result, csv_paths={"nav": "/tmp/nav.csv"})
        self.assertIsInstance(bt_id, int)
        self.assertGreater(bt_id, 0)

    def test_get_backtest_history_after_save(self):
        from backtesting.engine import BacktestConfig, BacktestResult
        from backtesting.metrics import compute_metrics

        config = BacktestConfig()
        pv     = make_price_series(periods=252)
        result = BacktestResult(
            config=config,
            portfolio_values=pv,
            benchmark_values=None,
            metrics=compute_metrics(pv),
            benchmark_metrics=None,
            monthly_returns=pd.DataFrame(),
            rebalance_log=[],
            position_history=pd.DataFrame(),
            n_builds_used=2,
            tickers_missing_data=[],
        )
        self.repo.save_backtest_run(result)
        history = self.repo.get_backtest_history()
        self.assertGreater(len(history), 0)
        self.assertIn("cagr", history.columns)
        self.assertIn("sharpe", history.columns)


# ══════════════════════════════════════════════════════════════
# Testy reportera (bez rich, bez plików)
# ══════════════════════════════════════════════════════════════

class TestBacktestReporter(unittest.TestCase):

    def _make_result(self, annual_return=0.12, n_builds=3):
        from backtesting.engine import BacktestConfig, BacktestResult
        from backtesting.metrics import compute_metrics, compute_monthly_returns

        pv     = make_price_series(annual_return=annual_return, periods=504)
        bm     = make_price_series(annual_return=0.08, periods=504, seed=99)
        config = BacktestConfig(initial_capital=10_000.0)
        return BacktestResult(
            config=config,
            portfolio_values=pv,
            benchmark_values=bm,
            metrics=compute_metrics(pv, bm),
            benchmark_metrics=compute_metrics(bm),
            monthly_returns=compute_monthly_returns(pv),
            rebalance_log=[{
                "date": "2022-07-01",
                "run_id": 2,
                "n_positions": 5,
                "transaction_cost": 12.50,
                "tickers_in":  ["AAPL"],
                "tickers_out": ["MSFT"],
            }],
            position_history=pd.DataFrame(),
            n_builds_used=n_builds,
            tickers_missing_data=[],
        )

    def test_save_csv_creates_files(self):
        import tempfile, os
        from backtesting.report import BacktestReporter

        result = self._make_result()
        with tempfile.TemporaryDirectory() as tmpdir:
            reporter = BacktestReporter(reports_dir=tmpdir)
            paths = reporter.save_csv(result)
            self.assertIn("nav", paths)
            self.assertIn("metrics", paths)
            self.assertTrue(os.path.exists(paths["nav"]))
            self.assertTrue(os.path.exists(paths["metrics"]))

    def test_nav_csv_has_correct_columns(self):
        import tempfile
        import pandas as pd
        from backtesting.report import BacktestReporter

        result = self._make_result()
        with tempfile.TemporaryDirectory() as tmpdir:
            reporter = BacktestReporter(reports_dir=tmpdir)
            paths    = reporter.save_csv(result)
            nav_df   = pd.read_csv(paths["nav"])
            self.assertIn("date",            nav_df.columns)
            self.assertIn("portfolio_value", nav_df.columns)
            self.assertIn("benchmark_value", nav_df.columns)

    def test_metrics_csv_has_portfolio_and_benchmark(self):
        import tempfile
        import pandas as pd
        from backtesting.report import BacktestReporter

        result   = self._make_result()
        with tempfile.TemporaryDirectory() as tmpdir:
            reporter = BacktestReporter(reports_dir=tmpdir)
            paths    = reporter.save_csv(result)
            mdf      = pd.read_csv(paths["metrics"])
            self.assertIn("metric",    mdf.columns)
            self.assertIn("portfolio", mdf.columns)
            self.assertIn("benchmark", mdf.columns)

    def test_monthly_csv_saved_when_data_exists(self):
        import tempfile, os
        from backtesting.report import BacktestReporter

        result = self._make_result()
        with tempfile.TemporaryDirectory() as tmpdir:
            reporter = BacktestReporter(reports_dir=tmpdir)
            paths    = reporter.save_csv(result)
            self.assertIn("monthly", paths)
            self.assertTrue(os.path.exists(paths["monthly"]))

    def test_rebalance_csv_saved(self):
        import tempfile, os
        from backtesting.report import BacktestReporter

        result = self._make_result()
        with tempfile.TemporaryDirectory() as tmpdir:
            reporter = BacktestReporter(reports_dir=tmpdir)
            paths    = reporter.save_csv(result)
            self.assertIn("rebalance", paths)
            self.assertTrue(os.path.exists(paths["rebalance"]))

    def test_invalid_result_print_does_not_crash(self):
        from backtesting.engine import BacktestConfig, BacktestResult
        from backtesting.metrics import _empty_metrics
        from backtesting.report import BacktestReporter
        import io

        result = BacktestResult(
            config=BacktestConfig(),
            portfolio_values=pd.Series(dtype=float),
            benchmark_values=None,
            metrics=_empty_metrics(),
            benchmark_metrics=None,
            monthly_returns=pd.DataFrame(),
            rebalance_log=[],
            position_history=pd.DataFrame(),
            n_builds_used=0,
            tickers_missing_data=[],
            warnings=["Test warning"],
        )
        reporter = BacktestReporter(reports_dir="/tmp")
        # Should not raise
        try:
            reporter.print_results(result)
        except Exception as e:
            self.fail(f"print_results raised unexpected exception: {e}")


# ══════════════════════════════════════════════════════════════
# Testy ładowania buildów z silnika
# ══════════════════════════════════════════════════════════════

class TestBuildLoading(unittest.TestCase):

    def test_load_builds_from_dataframe(self):
        from backtesting.engine import BacktestEngine, BacktestConfig

        builds_df = make_portfolio_builds_df(
            tickers=["AAPL", "MSFT"],
            build_dates=["2022-01-03", "2022-07-01"],
            weights=[[0.6, 0.4], [0.5, 0.5]],
        )
        repo = make_mock_repository(builds_df)
        engine = BacktestEngine(repository=repo, config=BacktestConfig())
        builds = engine._load_portfolio_builds()

        self.assertEqual(len(builds), 2)
        self.assertIn("AAPL", builds[0].positions)
        self.assertAlmostEqual(sum(builds[0].positions.values()), 1.0, places=5)

    def test_builds_sorted_chronologically(self):
        from backtesting.engine import BacktestEngine, BacktestConfig

        # Celowo odwrócona kolejność
        builds_df = make_portfolio_builds_df(
            tickers=["AAPL"],
            build_dates=["2022-07-01", "2022-01-03"],
        )
        repo   = make_mock_repository(builds_df)
        engine = BacktestEngine(repository=repo, config=BacktestConfig())
        builds = engine._load_portfolio_builds()

        self.assertLess(builds[0].build_date, builds[1].build_date)

    def test_empty_dataframe_returns_empty_list(self):
        from backtesting.engine import BacktestEngine, BacktestConfig

        repo = make_mock_repository(pd.DataFrame())
        engine = BacktestEngine(repository=repo, config=BacktestConfig())
        builds = engine._load_portfolio_builds()
        self.assertEqual(builds, [])

    def test_weights_normalized_in_build(self):
        from backtesting.engine import BacktestEngine, BacktestConfig

        # Wagi sumują się do 0.9 — powinny być znormalizowane do 1.0
        builds_df = make_portfolio_builds_df(
            tickers=["AAPL", "MSFT"],
            build_dates=["2022-01-03"],
            weights=[[0.45, 0.45]],
        )
        repo   = make_mock_repository(builds_df)
        engine = BacktestEngine(repository=repo, config=BacktestConfig())
        builds = engine._load_portfolio_builds()

        total_w = sum(builds[0].positions.values())
        self.assertAlmostEqual(total_w, 1.0, places=5)


# ══════════════════════════════════════════════════════════════
# Runner
# ══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    loader  = unittest.TestLoader()
    suite   = unittest.TestSuite()

    for cls in [
        TestMetrics,
        TestBacktestEngine,
        TestBacktestRepository,
        TestBacktestReporter,
        TestBuildLoading,
    ]:
        suite.addTests(loader.loadTestsFromTestCase(cls))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
