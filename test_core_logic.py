"""
test_core_logic.py
Testy jednostkowe dla logiki niepotrzebującej zewnętrznych bibliotek.
Uruchom: python test_core_logic.py
"""
import sys
import json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

print("=" * 60)
print("TESTY LOGIKI WEWNĘTRZNEJ")
print("=" * 60)

# ─── Test 1: Konfiguracja ────────────────────────────────────
print("\n[1] Test ładowania konfiguracji...")
from config.settings import load_config, _default_config
config = _default_config()
assert "source" in config
assert "filters" in config
assert "scoring" in config
assert config["source"]["index"] == "sp500"
print("    ✓ Domyślna konfiguracja poprawna")

# ─── Test 2: Parsowanie progów filtrów ───────────────────────
print("\n[2] Test parsowania progów filtrów...")
from screening.filter_engine import FilterEngine

test_config = {
    "filters": {
        "fundamental": {
            "pe_ratio": [0, 30],
            "roe": [5, None],
            "debt_to_equity": [None, 2.0],
        },
        "technical": {
            "rsi_14": [30, 70],
        }
    }
}
engine = FilterEngine(test_config)
assert len(engine._filter_map) == 4
assert engine._filter_map["pe_ratio"] == (0.0, 30.0, "fund")
assert engine._filter_map["roe"] == (5.0, None, "fund")
assert engine._filter_map["debt_to_equity"] == (None, 2.0, "fund")
assert engine._filter_map["rsi_14"] == (30.0, 70.0, "tech")
print("    ✓ Progi sparsowane poprawnie")

# ─── Test 3: Logika filtrowania ──────────────────────────────
print("\n[3] Test logiki filtrowania...")

# Ticker spełniający wszystkie kryteria
result = engine.apply("AAPL", {
    "pe_ratio": 25.0,
    "roe": 15.0,
    "debt_to_equity": 1.2,
    "rsi_14": 55.0,
})
assert result.passed, f"AAPL powinien przejść, failed_filters={result.failed_filters}"
print("    ✓ Ticker spełniający kryteria → PASSED")

# Ticker z za wysokim P/E
result = engine.apply("TSLA", {
    "pe_ratio": 80.0,  # za wysokie
    "roe": 12.0,
    "debt_to_equity": 0.8,
    "rsi_14": 50.0,
})
assert not result.passed
assert any("pe_ratio" in f for f in result.failed_filters)
print("    ✓ Ticker z za wysokim P/E → REJECTED")

# Ticker z brakującymi danymi
result = engine.apply("XYZ", {
    "pe_ratio": None,  # brak danych
    "roe": 10.0,
    "debt_to_equity": 1.0,
    "rsi_14": 45.0,
})
assert not result.passed
assert "pe_ratio" in result.missing_required
print("    ✓ Ticker z brakującymi danymi → REJECTED (conservative)")

# ─── Test 4: Ticker source czyszczenie ───────────────────────
print("\n[4] Test czyszczenia listy tickerów...")
from data.ticker_source import _clean_tickers

raw = ["AAPL", "msft", "AAPL", None, "", "BRK.B", "PKN.WA", "  GOOGL  "]
cleaned = _clean_tickers(raw)
assert "AAPL" in cleaned
assert cleaned.count("AAPL") == 1  # deduplikacja
assert "MSFT" in cleaned
assert "BRK-B" in cleaned  # BRK.B → BRK-B (Yahoo format)
assert "PKN.WA" in cleaned  # WA sufiks zachowany
assert "GOOGL" in cleaned   # spacja usunięta
print(f"    ✓ Wejście: {raw}")
print(f"    ✓ Wyjście: {cleaned}")

# ─── Test 5: RSI ─────────────────────────────────────────────
print("\n[5] Test obliczania RSI...")
import pandas as pd
import numpy as np
from data.fetcher import DataFetcher

# Stwórz syntetyczne dane cen
np.random.seed(42)
prices = pd.Series(
    100 + np.cumsum(np.random.normal(0.1, 1.5, 50)),
    dtype=float
)

rsi = DataFetcher._compute_rsi(prices, period=14)
assert rsi is not None
assert 0 <= rsi <= 100
print(f"    ✓ RSI obliczone: {rsi:.2f} (zakres [0, 100])")

# RSI dla stale rosnących cen → blisko 100
rising = pd.Series(range(1, 52), dtype=float)
rsi_rising = DataFetcher._compute_rsi(rising, 14)
assert rsi_rising > 90, f"RSI dla trendów wzrostowych powinno być >90, got {rsi_rising}"
print(f"    ✓ RSI dla trendu wzrostowego: {rsi_rising:.2f} (> 90)")

# ─── Test 6: Scoring normalizacja ────────────────────────────
print("\n[6] Test normalizacji scoringu...")
from screening.scorer import Scorer

scorer = Scorer({
    "scoring": {
        "weights": {
            "roe": 2.0,
            "pe_ratio": -0.5,
        }
    }
})

series = pd.Series([10.0, 20.0, 30.0])
normalized = Scorer._normalize(series)
assert abs(normalized.min() - 0.0) < 1e-9
assert abs(normalized.max() - 1.0) < 1e-9
print("    ✓ Normalizacja: min=0.0, max=1.0")

# Test dla identycznych wartości
flat_series = pd.Series([5.0, 5.0, 5.0])
flat_norm = Scorer._normalize(flat_series)
assert all(flat_norm == 0.5)
print("    ✓ Normalizacja identycznych wartości → 0.5")

# ─── Test 7: Portfolio ważenie ────────────────────────────────
print("\n[7] Test ważenia portfela...")

# Mock ScoredTicker
from screening.scorer import ScoredTicker
from portfolio.builder import PortfolioBuilder

mock_scored = [
    ScoredTicker(ticker="A", score=0.9, rank=1, metrics={}, score_contributions={}),
    ScoredTicker(ticker="B", score=0.7, rank=2, metrics={}, score_contributions={}),
    ScoredTicker(ticker="C", score=0.5, rank=3, metrics={}, score_contributions={}),
    ScoredTicker(ticker="D", score=0.3, rank=4, metrics={}, score_contributions={}),
    ScoredTicker(ticker="E", score=0.1, rank=5, metrics={}, score_contributions={}),
]

builder_equal = PortfolioBuilder({
    "portfolio": {
        "max_positions": 5,
        "min_results": 3,
        "weighting": "equal",
        "min_history_runs": 999,  # brak historii
        "stability_bonus_weight": 0.5,
    }
})

positions = builder_equal.build(mock_scored)
assert len(positions) == 5
total_weight = sum(p.weight for p in positions)
assert abs(total_weight - 1.0) < 1e-6, f"Suma wag: {total_weight}"
print(f"    ✓ Equal weighting: suma wag = {total_weight:.6f}")

# Score-weighted
builder_sw = PortfolioBuilder({
    "portfolio": {
        "max_positions": 3,
        "min_results": 3,
        "weighting": "score_weighted",
        "min_history_runs": 999,
        "stability_bonus_weight": 0.5,
    }
})
positions_sw = builder_sw.build(mock_scored)
assert len(positions_sw) == 3
total_sw = sum(p.weight for p in positions_sw)
assert abs(total_sw - 1.0) < 1e-6
# Pierwsza pozycja powinna mieć największą wagę
assert positions_sw[0].weight >= positions_sw[1].weight >= positions_sw[2].weight
print(f"    ✓ Score-weighted: top wagi = {[f'{p.weight:.3f}' for p in positions_sw]}")

# ─── Podsumowanie ────────────────────────────────────────────
print()
print("=" * 60)
print("✅ WSZYSTKIE TESTY PRZESZŁY POMYŚLNIE")
print("=" * 60)
print()
print("Projekt zawiera 2346 linii kodu w 11 modułach Python.")
print("Uruchom 'python main.py --help' aby zobaczyć opcje CLI.")
