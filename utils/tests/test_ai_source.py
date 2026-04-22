"""
test_ai_source.py
Kompleksowe testy dla nowego AI ticker source i całego pipeline'u.
Uruchom: python test_ai_source.py

Nie wymaga prawdziwego klucza API – używa MockBackend.
"""
import sys
import json
import time
import os
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

# Wymuś backend mock dla wszystkich testów
os.environ["AI_BACKEND"] = "mock"

SEP = "=" * 65

def header(title):
    print(f"\n{SEP}")
    print(f"  {title}")
    print(SEP)

def ok(msg): print(f"  ✓ {msg}")
def fail(msg): print(f"  ✗ {msg}"); raise AssertionError(msg)

# ──────────────────────────────────────────────────────────────
# TEST 1: PromptLibrary – jakość i różnorodność promptów
# ──────────────────────────────────────────────────────────────
header("TEST 1: PromptLibrary – wszystkie strategie")
from data.ai_ticker_source import PromptLibrary

strategies = [
    ("growth_quality",    {"n": 50}),
    ("deep_value",        {"n": 40}),
    ("compounders",       {"n": 30}),
    ("sector_leaders",    {"n": 40, "sector": "healthcare"}),
    ("thematic",          {"n": 35, "theme": "clean energy"}),
    ("global_diversified",{"n": 60}),
]

for strategy, kwargs in strategies:
    n = kwargs["n"]
    prompt = PromptLibrary.get_prompt(strategy, **kwargs)

    # Sprawdź, że prompt zawiera kluczowe elementy
    assert str(n) in prompt, f"Brak liczby {n} w promptcie {strategy}"
    assert len(prompt) > 300, f"Prompt {strategy} za krótki ({len(prompt)} znaków)"
    assert "JSON" in prompt or "json" in prompt, f"Brak formatu JSON w {strategy}"
    assert "ticker" in prompt.lower() or "symbol" in prompt.lower()

    ok(f"{strategy}: {len(prompt)} znaków, n={n}")

# Test systemu prompt
sys_prompt = PromptLibrary.SYSTEM_PROMPT
assert "analyst" in sys_prompt.lower()
assert "JSON" in sys_prompt
ok(f"System prompt: {len(sys_prompt)} znaków")

# Test nieznana strategia → fallback do growth_quality
fallback = PromptLibrary.get_prompt("nieznana_strategia", n=50)
assert len(fallback) > 200
ok("Nieznana strategia → fallback do growth_quality")

# ──────────────────────────────────────────────────────────────
# TEST 2: TickerParser – parsowanie różnych formatów odpowiedzi
# ──────────────────────────────────────────────────────────────
header("TEST 2: TickerParser – parsowanie formatów")
from data.ai_ticker_source import TickerParser

# Format 1: JSON object z listą (Groq JSON mode)
r1 = TickerParser.parse('{"tickers": ["AAPL", "MSFT", "ASML.AS", "7203.T"]}', 4)
assert "AAPL" in r1 and "MSFT" in r1
assert "ASML.AS" in r1  # sufiks giełdowy
assert "7203.T" in r1   # ticker azjatycki numeryczny
ok(f"JSON object z listą: {r1}")

# Format 2: czysta JSON array
r2 = TickerParser.parse('["NVDA", "META", "NOVO-B.CO", "2330.TW"]', 4)
assert "NVDA" in r2
assert "NOVO-B.CO" in r2  # duński ticker z myślnikiem
assert "2330.TW" in r2    # tajwański
ok(f"Czysta JSON array: {r2}")

# Format 3: JSON zagnieżdżony różnie
r3 = TickerParser.parse('{"stocks": ["GOOGL", "AMZN", "700.HK", "RELIANCE.NS"]}', 4)
assert "GOOGL" in r3 and "700.HK" in r3
ok(f"JSON z kluczem 'stocks': {r3}")

# Test deduplikacji
r4 = TickerParser.parse('["AAPL", "MSFT", "AAPL", "MSFT", "NVDA"]', 5)
assert len(r4) == 3  # deduplikacja
assert r4.count("AAPL") == 1
ok(f"Deduplikacja: {len(r4)} unikalnych z 5 wejściowych")

# Test walidacji formatu
valid_tickers = ["AAPL", "BRK-B", "ASML.AS", "7203.T", "NOVO-B.CO",
                 "9988.HK", "2330.TW", "RELIANCE.NS", "SAP.DE"]
for t in valid_tickers:
    assert TickerParser._is_valid_ticker(t), f"Powinien być valid: {t}"
ok(f"Walidacja poprawnych tickerów: {valid_tickers}")

invalid_tickers = ["", "123456789012", "a", "TOOLONGNAME123"]
for t in invalid_tickers:
    # Albo jest invalid_ticker albo przeszedłby przez clean i byłby pusty
    cleaned = TickerParser._clean([t])
    assert len(cleaned) == 0 or not TickerParser._is_valid_ticker(t)
ok(f"Walidacja niepoprawnych tickerów odrzucona")

# Test blacklisty
r_bl = TickerParser.parse('["AAPL", "ETF", "NULL", "MSFT", "NONE", "NVDA"]', 6)
assert "ETF" not in r_bl
assert "NULL" not in r_bl
assert "NONE" not in r_bl
assert "AAPL" in r_bl
ok(f"Blacklista odfiltrowana, wynik: {r_bl}")

# ──────────────────────────────────────────────────────────────
# TEST 3: MockBackend – symulacja odpowiedzi AI
# ──────────────────────────────────────────────────────────────
header("TEST 3: MockBackend")
from data.ai_ticker_source import MockBackend

backend = MockBackend()
assert backend.name == "Mock/Test"
raw = backend.call("system", "user", temperature=0.3)
data = json.loads(raw)
assert "tickers" in data
tickers = data["tickers"]
assert len(tickers) >= 30, f"Za mało tickerów w MockBackend: {len(tickers)}"
ok(f"MockBackend zwrócił {len(tickers)} tickerów")

# Sprawdź różnorodność geograficzną w mock danych
suffixes = [t.split(".")[-1] if "." in t else "US" for t in tickers]
has_eu = any(s in {"AS", "CO", "DE", "PA", "L", "SW"} for s in suffixes)
has_asia = any(s in {"T", "HK", "KS", "TW", "NS"} for s in suffixes)
assert has_eu, "Brak europejskich tickerów w MockBackend"
assert has_asia, "Brak azjatyckich tickerów w MockBackend"
ok(f"Różnorodność geograficzna: EU={has_eu}, Asia={has_asia}")

# ──────────────────────────────────────────────────────────────
# TEST 4: BackendFactory – fabryka backendów
# ──────────────────────────────────────────────────────────────
header("TEST 4: BackendFactory")
from data.ai_ticker_source import BackendFactory

# Mock backend (env już ustawiony)
b = BackendFactory.create({"backend": "mock"})
assert isinstance(b, MockBackend)
ok("BackendFactory → MockBackend")

# Nieznany backend → wyjątek (tymczasowo zdejmij mock env)
_old_env = os.environ.pop("AI_BACKEND", None)
try:
    BackendFactory.create({"backend": "nieznany_backend"})
    fail("Powinien rzucić ValueError!")
except ValueError as e:
    ok(f"Nieznany backend → ValueError: {str(e)[:50]}")
finally:
    if _old_env: os.environ["AI_BACKEND"] = _old_env

# Test rejestracji własnego backendu (tymczasowo zdejmij AI_BACKEND env)
from data.ai_ticker_source import LLMBackend
class CustomTestBackend(LLMBackend):
    @property
    def name(self): return "Custom/Test"
    def call(self, s, u, t): return '{"tickers": ["TEST"]}'

BackendFactory.register("custom_test", CustomTestBackend)
_saved = os.environ.pop("AI_BACKEND", None)
try:
    b_custom = BackendFactory.create({"backend": "custom_test"})
    assert isinstance(b_custom, CustomTestBackend)
    ok("Własny backend zarejestrowany i utworzony poprawnie")
finally:
    if _saved: os.environ["AI_BACKEND"] = _saved

# ──────────────────────────────────────────────────────────────
# TEST 5: AITickerSource – integracja z MockBackend
# ──────────────────────────────────────────────────────────────
header("TEST 5: AITickerSource – pełna integracja")
from data.ai_ticker_source import AITickerSource

for strategy, kwargs in strategies:
    cfg = {
        "backend": "mock",
        "strategy": strategy,
        "n_tickers": kwargs["n"],
        "temperature": 0.3,
        "max_retries": 2,
        **{k: v for k, v in kwargs.items() if k != "n"},
    }
    source = AITickerSource(cfg)
    tickers = source.fetch()
    assert len(tickers) > 0, f"Brak tickerów dla {strategy}"
    assert all(isinstance(t, str) for t in tickers)
    ok(f"Strategia '{strategy}': {len(tickers)} tickerów")

# ──────────────────────────────────────────────────────────────
# TEST 6: Multi-shot mode
# ──────────────────────────────────────────────────────────────
header("TEST 6: Multi-shot mode")
cfg_multi = {
    "backend": "mock",
    "strategy": "growth_quality",
    "n_tickers": 20,
    "temperature": 0.3,
    "max_retries": 1,
    "multi_shot": True,
    "multi_shot_runs": 3,
}
source_multi = AITickerSource(cfg_multi)
tickers_multi = source_multi.fetch()
assert len(tickers_multi) > 0
# Multi-shot powinien mieć tendencję do zwracania więcej unikalnych tickerów
ok(f"Multi-shot (3×): {len(tickers_multi)} unikalnych tickerów")

# ──────────────────────────────────────────────────────────────
# TEST 7: ticker_source.py router
# ──────────────────────────────────────────────────────────────
header("TEST 7: ticker_source.py router")
from data.ticker_source import get_tickers, get_tickers_multi_strategy

source_config = {
    "strategy": "growth_quality",
    "ai": {"backend": "mock", "n_tickers": 30, "temperature": 0.3, "max_retries": 1}
}
tickers = get_tickers(source_config)
assert len(tickers) > 0
ok(f"get_tickers(): {len(tickers)} tickerów")

# Multi-strategy
source_config_multi = {
    "multi_strategy": ["growth_quality", "deep_value"],
    "ai": {"backend": "mock", "n_tickers": 20, "temperature": 0.3, "max_retries": 1}
}
results = get_tickers_multi_strategy(source_config_multi)
assert "growth_quality" in results
assert "deep_value" in results
ok(f"get_tickers_multi_strategy(): {len(results)} strategii")

# ──────────────────────────────────────────────────────────────
# TEST 8: settings.py – konfiguracja
# ──────────────────────────────────────────────────────────────
header("TEST 8: Konfiguracja i walidacja")
from config.settings import load_config, _validate_config, _default_config

# Domyślna konfiguracja jest poprawna
default = _default_config()
_validate_config(default)
ok("Domyślna konfiguracja przechodzi walidację")

# Wczytaj plik YAML
config = load_config()
assert config["source"]["strategy"] == "growth_quality"
assert "ai" in config["source"]
assert config["source"]["ai"]["backend"] == "groq"
ok(f"Wczytano user_config.yaml: backend={config['source']['ai']['backend']}")

# Nieznana strategia → błąd
try:
    bad_cfg = _default_config()
    bad_cfg["source"]["strategy"] = "nieznana"
    _validate_config(bad_cfg)
    fail("Powinien rzucić ValueError!")
except ValueError as e:
    ok(f"Nieznana strategia → ValueError")

# Nieznany backend → błąd
try:
    bad_cfg2 = _default_config()
    bad_cfg2["source"]["ai"]["backend"] = "badziew"
    _validate_config(bad_cfg2)
    fail("Powinien rzucić ValueError!")
except ValueError:
    ok("Nieznany backend → ValueError")

# ──────────────────────────────────────────────────────────────
# TEST 9: FilterEngine z nowymi progami
# ──────────────────────────────────────────────────────────────
header("TEST 9: FilterEngine z nową konfiguracją")
from screening.filter_engine import FilterEngine

filter_config = {
    "filters": {
        "fundamental": {
            "pe_ratio": [0, 60],
            "roe": [10, None],
            "debt_to_equity": [None, 2.0],
            "market_cap": [5e8, None],
        },
        "technical": {"rsi_14": [25, 80], "volume_ratio": [0.3, None]},
    }
}
engine = FilterEngine(filter_config)

# Growth stock z wysokim P/E ale dobrym ROE
growth = {"pe_ratio": 45.0, "roe": 28.0, "debt_to_equity": 0.5,
          "market_cap": 5e9, "rsi_14": 60.0, "volume_ratio": 1.2}
r = engine.apply("HIGHGROWTH", growth)
assert r.passed, f"Growth stock powinien przejść: {r.failed_filters}"
ok(f"Growth stock (P/E=45, ROE=28%) → PASSED")

# Value trap – duży dług
value_trap = {"pe_ratio": 8.0, "roe": 3.0, "debt_to_equity": 5.0,
              "market_cap": 1e9, "rsi_14": 45.0, "volume_ratio": 0.8}
r2 = engine.apply("VALUETRAP", value_trap)
assert not r2.passed
ok(f"Value trap (ROE=3%, D/E=5x) → REJECTED")

# Mikro-cap poniżej progu
microcap = {"pe_ratio": 15.0, "roe": 20.0, "debt_to_equity": 0.3,
            "market_cap": 1e8, "rsi_14": 50.0, "volume_ratio": 0.5}
r3 = engine.apply("MICROCAP", microcap)
assert not r3.passed
ok(f"Mikro-cap ($100M) → REJECTED (below $500M threshold)")

# ──────────────────────────────────────────────────────────────
# TEST 10: Pipeline filter+score+portfolio (bez zewnętrznych deps)
# ──────────────────────────────────────────────────────────────
header("TEST 10: Pipeline filter+score+portfolio (bez sieciowych deps)")

import logging, os, sqlite3
logging.disable(logging.CRITICAL)

try:
    from config.settings import _default_config
    from screening.filter_engine import FilterEngine, FilterResult
    from screening.scorer import Scorer, ScoredTicker
    from portfolio.builder import PortfolioBuilder
    import numpy as np

    # Symuluj dane wyjściowe z fetchera (bez yfinance)
    fake_ticker_data = [
        {"ticker": "AAPL",      "roe": 28.0, "pe_ratio": 30.0, "debt_to_equity": 0.5,
         "current_ratio": 1.5, "revenue_growth": 12.0, "profit_margin": 25.0,
         "market_cap": 3e12, "rsi_14": 58.0, "volume_ratio": 1.2,
         "momentum_3m": 8.0, "momentum_6m": 15.0, "earnings_growth": 10.0},
        {"ticker": "MSFT",      "roe": 35.0, "pe_ratio": 35.0, "debt_to_equity": 0.3,
         "current_ratio": 2.0, "revenue_growth": 17.0, "profit_margin": 35.0,
         "market_cap": 3.1e12, "rsi_14": 62.0, "volume_ratio": 1.1,
         "momentum_3m": 12.0, "momentum_6m": 22.0, "earnings_growth": 18.0},
        {"ticker": "ASML.AS",   "roe": 40.0, "pe_ratio": 45.0, "debt_to_equity": 0.4,
         "current_ratio": 1.8, "revenue_growth": 25.0, "profit_margin": 28.0,
         "market_cap": 3.5e11, "rsi_14": 55.0, "volume_ratio": 0.9,
         "momentum_3m": 5.0, "momentum_6m": 18.0, "earnings_growth": 22.0},
        {"ticker": "NVDA",      "roe": 70.0, "pe_ratio": 55.0, "debt_to_equity": 0.4,
         "current_ratio": 4.0, "revenue_growth": 120.0, "profit_margin": 55.0,
         "market_cap": 2.5e12, "rsi_14": 72.0, "volume_ratio": 2.0,
         "momentum_3m": 25.0, "momentum_6m": 60.0, "earnings_growth": 150.0},
        {"ticker": "JUNK_CO",   "roe": 1.0,  "pe_ratio": 5.0,  "debt_to_equity": 8.0,
         "current_ratio": 0.5, "revenue_growth": -5.0, "profit_margin": 0.5,
         "market_cap": 1e8,  "rsi_14": 20.0, "volume_ratio": 0.1,
         "momentum_3m": -30.0, "momentum_6m": -50.0, "earnings_growth": -80.0},
    ]

    cfg = _default_config()
    cfg["filters"] = {
        "fundamental": {"pe_ratio": [0, 60], "roe": [10, None],
                        "debt_to_equity": [None, 2.0], "market_cap": [5e8, None]},
        "technical": {"rsi_14": [25, 80], "volume_ratio": [0.3, None]},
    }
    cfg["scoring"]["weights"] = {
        "roe": 2.0, "revenue_growth": 1.5, "profit_margin": 1.2,
        "momentum_6m": 1.0, "pe_ratio": -0.4, "debt_to_equity": -0.8,
    }
    cfg["portfolio"]["max_positions"] = 5
    cfg["portfolio"]["min_results"] = 3
    # Krok 1: Filtrowanie
    engine_f = FilterEngine(cfg)
    passed, rejected = [], []
    for td in fake_ticker_data:
        r = engine_f.apply(td["ticker"], td)
        (passed if r.passed else rejected).append(r)
    assert len(passed) == 4, f"Oczekiwano 4 spółek, got {len(passed)}: {[r.ticker for r in passed]}"
    assert any(r.ticker == "JUNK_CO" for r in rejected)
    ok(f"Filtrowanie: {len(passed)} przeszło, {len(rejected)} odrzucono")

    # Krok 2: Scoring
    scorer = Scorer(cfg)
    scored = scorer.score_and_rank(passed)
    assert len(scored) == 4
    assert scored[0].score >= scored[1].score  # malejąco posortowane
    assert scored[0].ticker in ("NVDA", "ASML.AS", "MSFT")  # najwyższy ROE
    ok(f"Scoring: #1={scored[0].ticker}(score={scored[0].score:.4f}), #2={scored[1].ticker}, #3={scored[2].ticker}")

    # Krok 3: Portfolio
    builder = PortfolioBuilder(cfg, repository=None)
    portfolio = builder.build(scored)
    assert len(portfolio) >= 3
    total_w = sum(p.weight for p in portfolio)
    assert abs(total_w - 1.0) < 1e-5
    ok(f"Portfel: {len(portfolio)} pozycji, suma wag={total_w:.6f}")

    # Krok 4: DB test używając wbudowanego sqlite3 (bez sqlalchemy)
    db_path = "/tmp/test_screener_lite.db"
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""CREATE TABLE IF NOT EXISTS runs
                    (id INTEGER PRIMARY KEY, source TEXT, passed INTEGER, ts DATETIME DEFAULT CURRENT_TIMESTAMP)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS results
                    (run_id INTEGER, ticker TEXT, score REAL)""")

    # Run 1
    cur = conn.execute("INSERT INTO runs(source, passed) VALUES('ai_growth',4)")
    run_id1 = cur.lastrowid
    for s in scored:
        conn.execute("INSERT INTO results VALUES(?,?,?)", (run_id1, s.ticker, s.score))
    conn.commit()

    # Run 2 (inkrementacyjny – nowy rekord, brak nadpisania)
    cur2 = conn.execute("INSERT INTO runs(source, passed) VALUES('ai_growth',4)")
    run_id2 = cur2.lastrowid
    for s in scored:
        conn.execute("INSERT INTO results VALUES(?,?,?)", (run_id2, s.ticker, s.score))
    conn.commit()

    total_runs = conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
    total_results = conn.execute("SELECT COUNT(*) FROM results").fetchone()[0]
    assert total_runs == 2, f"Oczekiwano 2 runów, got {total_runs}"
    assert total_results == 8  # 4 spółki × 2 runy
    ok(f"DB inkrementacyjny: {total_runs} runów, {total_results} wyników (brak nadpisania)")

    # Analiza częstości
    freq = conn.execute("""
        SELECT ticker, COUNT(*) as cnt FROM results GROUP BY ticker ORDER BY cnt DESC
    """).fetchall()
    assert all(f[1] == 2 for f in freq), "Każda spółka powinna wystąpić w obu runach"
    ok(f"Częstość: {len(freq)} tickerów, każdy w 100% uruchomień")

    conn.close()
    for f in [db_path, db_path + "-wal", db_path + "-shm"]:
        if os.path.exists(f): os.unlink(f)

finally:
    logging.disable(logging.NOTSET)

# ──────────────────────────────────────────────────────────────
# PODSUMOWANIE
# ──────────────────────────────────────────────────────────────
print(f"\n{SEP}")
print(f"  ✅  WSZYSTKIE TESTY PRZESZŁY POMYŚLNIE")
print(f"{SEP}")
print()
print("  Projekt gotowy do uruchomienia z prawdziwym API:")
print()
print("  1. Rejestracja Groq (darmowa, bez karty):")
print("     https://console.groq.com")
print()
print("  2. Ustaw klucz API:")
print("     export GROQ_API_KEY=gsk_twoj_klucz")
print()
print("  3. Uruchom screener:")
print("     python main.py")
print("     python main.py --strategy deep_value")
print("     python main.py --strategy thematic --theme 'quantum computing'")
print("     python main.py --multi-shot --n 80")
print()
print("  4. Test bez klucza (mock):")
print("     python main.py --backend mock")
print(f"{SEP}")
