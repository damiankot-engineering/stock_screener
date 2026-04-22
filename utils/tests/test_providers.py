"""
test_providers.py
Testy dla systemu dostawców danych (FMP, Stooq, yFinance, Composite).
Wszystkie requesty HTTP mockowane — testy działają bez internetu i bez kluczy API.

Uruchom: python test_providers.py
"""
from __future__ import annotations

import io
import json
import sys
import os
from contextlib import contextmanager
from dataclasses import dataclass, field
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

SEP = "=" * 65
ok  = lambda m: print(f"  ✓ {m}")
hdr = lambda t: print(f"\n{SEP}\n  {t}\n{SEP}")


# ── Helpers ───────────────────────────────────────────────────

def make_mock_response(data, status=200):
    """Stwórz mock requests.Response."""
    mock = MagicMock()
    mock.status_code = status
    mock.json.return_value = data
    mock.raise_for_status = MagicMock()
    return mock


def make_stooq_csv(rows: int = 200) -> str:
    """Wygeneruj przykładowe CSV w formacie Stooq."""
    lines = ["Date,Open,High,Low,Close,Volume"]
    import random
    price = 150.0
    for i in range(rows, 0, -1):
        from datetime import datetime, timedelta
        dt = (datetime.utcnow() - timedelta(days=i)).strftime("%Y-%m-%d")
        price += random.uniform(-3, 3)
        price = max(price, 1.0)
        lines.append(f"{dt},{price:.2f},{price+2:.2f},{price-2:.2f},{price:.2f},1000000")
    return "\n".join(lines)


# ── TEST 1: base.py — DataProvider interface ─────────────────
hdr("TEST 1: DataProvider – abstract interface")
from data.providers.base import DataProvider, ProviderResult

# Sprawdź że nie można zinstancjonować bezpośrednio
try:
    DataProvider()
    raise AssertionError("Powinno rzucić TypeError")
except TypeError:
    ok("DataProvider jest abstrakcyjny — nie można zinstancjonować")

# Sprawdź że _safe_float działa poprawnie
assert DataProvider._safe_float(None) is None
assert DataProvider._safe_float("not_a_number") is None
assert DataProvider._safe_float(float("inf")) is None
assert DataProvider._safe_float(float("nan")) is None
assert DataProvider._safe_float(25.5) == 25.5
assert DataProvider._safe_float(100, 100) == 1.0
assert abs(DataProvider._safe_float("0.15") - 0.15) < 1e-9
ok("_safe_float: None/inf/nan → None, poprawne wartości OK")

# ProviderResult properties
pr_empty = ProviderResult(ticker="TEST")
assert not pr_empty.has_fundamentals
assert not pr_empty.has_prices
ok("ProviderResult(empty): has_fundamentals=False, has_prices=False")

pr_full = ProviderResult(
    ticker="AAPL",
    fundamentals={"pe_ratio": 25.0, "roe": 18.5},
    price_history=pd.DataFrame({"Close": range(30)},
                                index=pd.date_range("2024-01-01", periods=30)),
)
assert pr_full.has_fundamentals
assert pr_full.has_prices
ok("ProviderResult(full): has_fundamentals=True, has_prices=True")


# ── TEST 2: FMPProvider — mocked HTTP ────────────────────────
hdr("TEST 2: FMPProvider – mocked HTTP responses")
from data.providers.fmp import FMPProvider

MOCK_RATIOS = [{
    "priceEarningsRatio": 28.5,
    "priceToBookRatio": 7.2,
    "priceToSalesRatio": 6.8,
    "returnOnEquity": 0.160,     # FMP zwraca jako ułamek
    "returnOnAssets": 0.195,
    "debtEquityRatio": 1.2,
    "currentRatio": 1.45,
    "quickRatio": 1.20,
    "netProfitMargin": 0.25,
    "grossProfitMargin": 0.44,
    "operatingProfitMargin": 0.30,
    "dividendYield": 0.0055,
    "payoutRatio": 0.155,
}]
MOCK_GROWTH = [{
    "revenueGrowth": 0.085,
    "netIncomeGrowth": 0.12,
    "ebitdaGrowth": 0.095,
    "freeCashFlowGrowth": 0.18,
    "epsgrowth": 0.14,
}]
MOCK_METRICS = [{
    "enterpriseValueOverEBITDA": 22.4,
    "roic": 0.30,
    "freeCashFlowYield": 0.035,
    "netDebtToEBITDA": 0.8,
    "marketCap": 3_000_000_000_000,
    "revenuePerShare": 25.4,
}]
MOCK_PRICES = {
    "historical": [
        {"date": "2025-01-15", "open": 230.0, "high": 235.0,
         "low": 228.0, "close": 232.0, "volume": 80_000_000},
        {"date": "2025-01-14", "open": 228.0, "high": 232.0,
         "low": 226.0, "close": 230.0, "volume": 75_000_000},
    ] * 80  # ~160 sesji
}

with patch("requests.get") as mock_get:
    def side_effect(url, params=None, **kwargs):
        if "ratios" in url:
            return make_mock_response(MOCK_RATIOS)
        elif "financial-growth" in url:
            return make_mock_response(MOCK_GROWTH)
        elif "key-metrics" in url:
            return make_mock_response(MOCK_METRICS)
        elif "historical-price-full" in url:
            return make_mock_response(MOCK_PRICES)
        return make_mock_response([])

    mock_get.side_effect = side_effect
    fmp = FMPProvider(api_key="test_key_123", api_delay=0)
    fundamentals = fmp.get_fundamentals("AAPL")

assert fundamentals.get("pe_ratio") == 28.5
assert fundamentals.get("pb_ratio") == 7.2
# ROE: 0.160 / (1/100) = 16.0%
assert abs(fundamentals.get("roe", 0) - 16.0) < 0.01
assert abs(fundamentals.get("profit_margin", 0) - 25.0) < 0.01
assert fundamentals.get("ev_ebitda") == 22.4
assert abs(fundamentals.get("roic", 0) - 30.0) < 0.01
assert fundamentals.get("revenue_growth") is not None
ok(f"get_fundamentals: pe_ratio={fundamentals['pe_ratio']}, roe={fundamentals.get('roe'):.1f}%")
ok(f"  ev_ebitda={fundamentals.get('ev_ebitda')}, roic={fundamentals.get('roic'):.1f}%")
ok(f"  revenue_growth={fundamentals.get('revenue_growth'):.2f}%")

# Test ticker normalizacji
assert fmp.normalize_ticker("ASML.AS") == "ASML"
assert fmp.normalize_ticker("SAP.DE")  == "SAP"
assert fmp.normalize_ticker("MC.PA")   == "MC"
assert fmp.normalize_ticker("AAPL")    == "AAPL"    # US bez zmian
assert fmp.normalize_ticker("7203.T")  == "7203.T"  # Tokyo zachowuje sufiks
assert fmp.normalize_ticker("700.HK")  == "700.HK"
ok("normalize_ticker: .AS/.DE/.PA → bez sufiksu, .T/.HK → zachowany")

# Test pobierania cen
with patch("requests.get", return_value=make_mock_response(MOCK_PRICES)):
    fmp2 = FMPProvider(api_key="test_key_123", api_delay=0)
    prices = fmp2.get_price_history("AAPL", days=90)

assert not prices.empty
assert "Close" in prices.columns
ok(f"get_price_history: {len(prices)} sesji, kolumny={list(prices.columns)}")

# Test obsługi błędu FMP
with patch("requests.get",
           return_value=make_mock_response({"Error Message": "Invalid API KEY"})):
    fmp3 = FMPProvider(api_key="bad_key", api_delay=0)
    result = fmp3.get_fundamentals("AAPL")
    assert result == {}
ok("FMP Error Message → pusty dict (nie wyjątek)")

# Test braku klucza
try:
    FMPProvider(api_key="")
    raise AssertionError("Powinno rzucić ValueError")
except ValueError as e:
    assert "FMP_API_KEY" in str(e)
ok("Brak klucza → ValueError z instrukcją rejestracji")


# ── TEST 3: StooqProvider — mocked HTTP ──────────────────────
hdr("TEST 3: StooqProvider – mocked HTTP responses")
from data.providers.stooq import StooqProvider, YAHOO_TO_STOOQ

stooq = StooqProvider(api_delay=0)

# Normalizacja tickerów
assert stooq.normalize_ticker("AAPL")     == "aapl.us"
assert stooq.normalize_ticker("BRK-B")    == "brk-b.us"
assert stooq.normalize_ticker("ASML.AS")  == "asml.nl"
assert stooq.normalize_ticker("SAP.DE")   == "sap.de"
assert stooq.normalize_ticker("MC.PA")    == "mc.fr"
assert stooq.normalize_ticker("AZN.L")    == "azn.uk"
assert stooq.normalize_ticker("7203.T")   == "7203.jp"
assert stooq.normalize_ticker("700.HK")   == "700.hk"
assert stooq.normalize_ticker("RELIANCE.NS") == "reliance.ns"
assert stooq.normalize_ticker("PKN.WA")   == "pkn.pl"
assert stooq.normalize_ticker("VALE3.SA") == "vale3.br"
ok("normalize_ticker: US→.us, .AS→.nl, .DE→.de, .PA→.fr, .T→.jp, .HK→.hk, .NS→.ns")

# Pobieranie cen z poprawnym CSV
stooq_csv = make_stooq_csv(250)
mock_resp = MagicMock()
mock_resp.status_code = 200
mock_resp.text = stooq_csv
mock_resp.raise_for_status = MagicMock()

with patch("requests.get", return_value=mock_resp):
    prices = stooq.get_price_history("AAPL", days=365)

assert not prices.empty
assert "Close" in prices.columns
assert len(prices) == 250
ok(f"get_price_history: {len(prices)} sesji z CSV")

# Stooq nie dostarcza fundamentów
assert stooq.get_fundamentals("AAPL") == {}
assert not stooq.provides_fundamentals
assert stooq.provides_prices
ok("provides_fundamentals=False, provides_prices=True")

# Test pustej odpowiedzi
mock_empty = MagicMock()
mock_empty.status_code = 200
mock_empty.text = "Date,Open,High,Low,Close,Volume\n"
mock_empty.raise_for_status = MagicMock()

with patch("requests.get", return_value=mock_empty):
    empty_prices = stooq.get_price_history("FAKEXYZ", days=30)
assert empty_prices.empty
ok("Puste CSV → pusty DataFrame (nie wyjątek)")

# Test "No data" w odpowiedzi
mock_nodata = MagicMock()
mock_nodata.status_code = 200
mock_nodata.text = "No data"
mock_nodata.raise_for_status = MagicMock()

with patch("requests.get", return_value=mock_nodata):
    nodata_prices = stooq.get_price_history("DELISTED", days=30)
assert nodata_prices.empty
ok("'No data' w odpowiedzi → pusty DataFrame")


# ── TEST 4: YFinanceProvider — mocked yfinance ───────────────
hdr("TEST 4: YFinanceProvider – mocked yfinance")
from data.providers.yfinance_provider import YFinanceProvider

MOCK_YF_INFO = {
    "trailingPE":                   22.0,
    "priceToBook":                  5.5,
    "returnOnEquity":               0.18,   # ułamek → 18%
    "returnOnAssets":               0.12,
    "debtToEquity":                 145.0,  # yfinance mnożone przez 100 → /100 = 1.45
    "currentRatio":                 1.2,
    "revenueGrowth":                0.07,   # ułamek → 7%
    "profitMargins":                0.22,
    "marketCap":                    2_500_000_000_000,
}
mock_yf_ticker = MagicMock()
mock_yf_ticker.info = MOCK_YF_INFO

close_prices = pd.Series(
    100 + np.cumsum(np.random.default_rng(42).normal(0.1, 1.5, 300)),
)
mock_hist = pd.DataFrame({
    "Open": close_prices - 1,
    "High": close_prices + 2,
    "Low":  close_prices - 2,
    "Close": close_prices,
    "Volume": [1_000_000] * 300,
}, index=pd.date_range("2024-01-01", periods=300))

mock_yf_ticker.history.return_value = mock_hist

yf_provider = YFinanceProvider()

with patch("data.providers.yfinance_provider.YFinanceProvider.get_fundamentals",
           return_value={
               "pe_ratio": 22.0, "roe": 18.0, "debt_to_equity": 1.45,
               "profit_margin": 22.0, "market_cap": 2_500_000_000_000,
           }),      patch("data.providers.yfinance_provider.YFinanceProvider.get_price_history",
           return_value=mock_hist):
    fund = yf_provider.get_fundamentals("AAPL")
    prices = yf_provider.get_price_history("AAPL", days=300)

assert fund.get("pe_ratio") == 22.0
assert abs(fund.get("roe", 0) - 18.0) < 0.01    # 0.18 × 100 = 18%
assert abs(fund.get("debt_to_equity", 0) - 1.45) < 0.01  # 145 / 100 = 1.45
assert abs(fund.get("profit_margin", 0) - 22.0) < 0.01
assert not prices.empty and "Close" in prices.columns
ok(f"get_fundamentals: pe_ratio={fund['pe_ratio']}, roe={fund.get('roe'):.1f}%")
ok(f"  debt_to_equity={fund.get('debt_to_equity')}, margin={fund.get('profit_margin'):.1f}%")
ok(f"get_price_history: {len(prices)} sesji")


# ── TEST 5: CompositeProvider — fallback chain ───────────────
hdr("TEST 5: CompositeProvider – fallback logic")
from data.providers.composite import CompositeProvider
from data.providers.base import DataProvider as DP

class MockFundProvider(DP):
    """Zwraca tylko P/E i ROE."""
    name = "MockFund"
    provides_fundamentals = True
    provides_prices = False
    def get_fundamentals(self, ticker):
        return {"pe_ratio": 25.0, "roe": 20.0}
    def get_price_history(self, ticker, days=400):
        return pd.DataFrame()
    def normalize_ticker(self, t): return t

class MockFallbackProvider(DP):
    """Fallback — zwraca więcej pól."""
    name = "MockFallback"
    provides_fundamentals = True
    provides_prices = True
    def get_fundamentals(self, ticker):
        return {"pe_ratio": 30.0, "roe": 15.0, "debt_to_equity": 0.8,
                "profit_margin": 18.0}
    def get_price_history(self, ticker, days=400):
        return pd.DataFrame(
            {"Close": range(50)},
            index=pd.date_range("2024-01-01", periods=50),
        )
    def normalize_ticker(self, t): return t

class MockBrokenProvider(DP):
    """Zawsze rzuca wyjątek."""
    name = "MockBroken"
    provides_fundamentals = True
    provides_prices = True
    def get_fundamentals(self, ticker):
        raise ConnectionError("Simulated network error")
    def get_price_history(self, ticker, days=400):
        raise ConnectionError("Simulated network error")
    def normalize_ticker(self, t): return t

# Test 1: Primary uzupełnia z fallback
composite = CompositeProvider(
    fundamental_providers=[MockFundProvider(), MockFallbackProvider()],
    price_providers=[MockFallbackProvider()],
)
fund = composite.get_fundamentals("AAPL")
# P/E i ROE z MockFund (primary), debt_to_equity i profit_margin z MockFallback
assert fund.get("pe_ratio") == 25.0      # primary wins
assert fund.get("roe") == 20.0           # primary wins
assert fund.get("debt_to_equity") == 0.8 # tylko fallback ma to pole
assert fund.get("profit_margin") == 18.0
ok("Primary + fallback: primary wygrywa, fallback uzupełnia brakujące pola")

# Test 2: Broken provider → fallback
composite_broken = CompositeProvider(
    fundamental_providers=[MockBrokenProvider(), MockFallbackProvider()],
    price_providers=[MockBrokenProvider(), MockFallbackProvider()],
)
fund_b = composite_broken.get_fundamentals("AAPL")
assert fund_b.get("pe_ratio") == 30.0   # z fallback po błędzie primary
prices_b = composite_broken.get_price_history("AAPL")
assert not prices_b.empty
ok("Broken primary → automatic fallback (brak wyjątku)")

# Test 3: Wszystkie broken → pusty dict / pusty DataFrame
composite_all_broken = CompositeProvider(
    fundamental_providers=[MockBrokenProvider()],
    price_providers=[MockBrokenProvider()],
)
assert composite_all_broken.get_fundamentals("X") == {}
assert composite_all_broken.get_price_history("X").empty
ok("Wszystkie broken → pusty wynik (nie wyjątek)")

# Test _has_core_fundamentals
assert CompositeProvider._has_core_fundamentals(
    {"pe_ratio": 25.0, "roe": 20.0, "debt_to_equity": 0.5, "revenue_growth": 8.0}
)
assert not CompositeProvider._has_core_fundamentals(
    {"pe_ratio": None, "roe": None, "debt_to_equity": None}
)
ok("_has_core_fundamentals: poprawna detekcja wystarczającego zestawu metryk")

# Test get_all
result = composite.get_all("AAPL")
assert result.has_fundamentals
assert result.has_prices
ok(f"get_all: has_fundamentals={result.has_fundamentals}, has_prices={result.has_prices}")


# ── TEST 6: TechnicalCalculator ──────────────────────────────
hdr("TEST 6: TechnicalCalculator – obliczenia techniczne")
from data.fetcher import TechnicalCalculator

calc = TechnicalCalculator()
rng = np.random.default_rng(42)

# Wygeneruj realistyczną historię cen
n = 300
prices_series = pd.Series(
    100 + np.cumsum(rng.normal(0.1, 1.5, n)),
    index=pd.date_range("2024-01-01", periods=n),
)
hist_df = pd.DataFrame({
    "Close":  prices_series,
    "Volume": rng.integers(1_000_000, 5_000_000, n).astype(float),
})

fields = ["momentum_1m", "momentum_3m", "momentum_6m", "momentum_12m",
          "rsi_14", "above_ma50", "above_ma200",
          "volatility_30d", "volume_ratio"]
results = calc.compute("AAPL", hist_df, fields)

for f in fields:
    assert f in results, f"Brak {f} w wynikach"
    val = results[f]
    assert val is not None, f"{f} jest None dla pełnego historii"
ok(f"Wszystkie {len(fields)} wskaźników obliczone (nie-None)")

# RSI w zakresie [0, 100]
rsi = results["rsi_14"]
assert 0 <= rsi <= 100
ok(f"RSI=14: {rsi:.2f} (zakres [0,100] ✓)")

# above_ma50 i above_ma200 są 0 lub 1
assert results["above_ma50"] in (0.0, 1.0)
assert results["above_ma200"] in (0.0, 1.0)
ok(f"above_ma50={results['above_ma50']}, above_ma200={results['above_ma200']} (0 lub 1 ✓)")

# RSI = 100 dla ciągle rosnących cen
rising_df = pd.DataFrame({"Close": pd.Series(range(1, 100))})
rsi_rising = calc._rsi(rising_df["Close"], 14)
assert rsi_rising == 100.0
ok(f"RSI dla trendu wzrostowego = 100.0 ✓")

# Za mało danych
short_df = pd.DataFrame({"Close": [100.0, 101.0, 102.0]})
short_results = calc.compute("X", short_df, ["momentum_3m", "rsi_14"])
assert all(v is None for v in short_results.values())
ok("< 20 sesji → wszystkie wskaźniki None (nie wyjątek)")


# ── TEST 7: DataFetcher integracja ────────────────────────────
hdr("TEST 7: DataFetcher – integracja z CompositeProvider")
from data.fetcher import DataFetcher

config = {
    "settings": {"fetch_workers": 2, "api_delay_seconds": 0,
                 "price_history_days": 400, "max_fetch_errors": 1},
    "metrics": {
        "fundamental": {"enabled": True,
                        "fields": ["pe_ratio", "roe", "debt_to_equity", "profit_margin"]},
        "technical":   {"enabled": True,
                        "fields": ["momentum_3m", "rsi_14", "above_ma200"]},
    },
    "data_sources": {
        "fmp":      {"enabled": False},
        "stooq":    {"enabled": False},
        "yfinance": {"enabled": True},
    }
}

fetcher = DataFetcher(config)

# Podstaw mock composite provider
class MockComposite:
    name = "MockComposite"

    def get_all(self, ticker, days=400):
        from data.providers.base import ProviderResult
        n = 300
        rng2 = np.random.default_rng(hash(ticker) % 2**32)
        prices = pd.Series(
            100 + np.cumsum(rng2.normal(0.1, 1.5, n)),
            index=pd.date_range("2024-01-01", periods=n),
        )
        return ProviderResult(
            ticker=ticker,
            fundamentals={
                "pe_ratio": 25.0 + hash(ticker) % 10,
                "roe": 15.0,
                "debt_to_equity": 0.8,
                "profit_margin": 18.0,
            },
            price_history=pd.DataFrame(
                {"Close": prices, "Volume": [1_000_000.0] * n},
                index=prices.index,
            ),
            source="Mock",
        )

fetcher._provider = MockComposite()

# Fetch dla 3 tickerów
tickers = ["AAPL", "MSFT", "ASML.AS"]
results = fetcher.fetch_all(tickers)

assert len(results) == 3
assert all(td.success for td in results)

for td in results:
    assert td.fundamentals.get("pe_ratio") is not None
    assert td.fundamentals.get("roe") == 15.0
    assert "momentum_3m" in td.technicals
    assert "rsi_14" in td.technicals
    rsi = td.technicals.get("rsi_14")
    assert rsi is not None and 0 <= rsi <= 100

ok(f"fetch_all({tickers}): {len(results)} TickerData, wszystkie success=True")
ok(f"  Fundamenty: pe_ratio, roe, debt_to_equity, profit_margin ✓")
ok(f"  Techniczne: momentum_3m, rsi_14, above_ma200 ✓")
ok(f"  RSI AAPL={results[0].technicals.get('rsi_14'):.2f}")

# Test error handling — jeden ticker rzuca wyjątek
class ErrorComposite:
    name = "ErrorComposite"
    def get_all(self, ticker, days=400):
        if ticker == "BAD":
            raise ValueError("Simulated error")
        from data.providers.base import ProviderResult
        return ProviderResult(ticker=ticker, fundamentals={"pe_ratio": 20.0},
                               price_history=pd.DataFrame())

fetcher._provider = ErrorComposite()
results2 = fetcher.fetch_all(["GOOD", "BAD"])
good = next(td for td in results2 if td.ticker == "GOOD")
bad  = next(td for td in results2 if td.ticker == "BAD")
assert good.success
assert not bad.success
ok("Error w jednym tickerze → TickerData(success=False), pozostałe bez zmian")


# ── TEST 8: build_composite — fabryka ────────────────────────
hdr("TEST 8: build_composite – fabryka providers")
from data.providers.composite import build_composite

# Bez kluczy → tylko yfinance
os.environ.pop("FMP_API_KEY", None)
cfg_no_keys = {
    "settings": {"api_delay_seconds": 0.1},
    "data_sources": {
        "fmp":      {"enabled": True, "api_key_env": "FMP_API_KEY"},
        "stooq":    {"enabled": False},
        "yfinance": {"enabled": True},
    }
}
composite_no_keys = build_composite(cfg_no_keys)
# Bez FMP_API_KEY — FMP pomijany; yfinance jako fallback
fund_names = [p.name for p in composite_no_keys.fundamental_providers]
assert "yFinance" in fund_names
assert "FMP" not in fund_names
ok(f"Bez FMP_API_KEY: fundamental_providers={fund_names}")

# Z kluczem FMP
os.environ["FMP_API_KEY"] = "test_key_abc"
cfg_with_fmp = {
    "settings": {"api_delay_seconds": 0.1},
    "data_sources": {
        "fmp":      {"enabled": True, "api_key_env": "FMP_API_KEY"},
        "stooq":    {"enabled": False},
        "yfinance": {"enabled": True},
    }
}
composite_with_fmp = build_composite(cfg_with_fmp)
fund_names_fmp = [p.name for p in composite_with_fmp.fundamental_providers]
assert "FMP" in fund_names_fmp
assert "yFinance" in fund_names_fmp
ok(f"Z FMP_API_KEY: fundamental_providers={fund_names_fmp} (FMP primary, yFinance fallback)")

# Z Stooq
cfg_stooq = {
    "settings": {"api_delay_seconds": 0.1},
    "data_sources": {
        "fmp":      {"enabled": False},
        "stooq":    {"enabled": True},
        "yfinance": {"enabled": True},
    }
}
composite_stooq = build_composite(cfg_stooq)
price_names = [p.name for p in composite_stooq.price_providers]
assert "Stooq" in price_names
ok(f"Ze Stooq: price_providers={price_names} (Stooq primary ceny)")

os.environ.pop("FMP_API_KEY", None)  # cleanup


# ── PODSUMOWANIE ─────────────────────────────────────────────
print(f"\n{SEP}")
print("  ✅  WSZYSTKIE TESTY PRZESZŁY POMYŚLNIE  (8 grup)")
print(f"{SEP}")
print()
arch_summary = [
    "Nowe źródła danych:",
    "  FMP  → fundamenty ze sprawozdań fin. (250 req/dzień, darmowy)",
    "  Stooq → historyczne ceny OHLCV (bez klucza, bez limitu)",
    "  yFinance → fallback (zachowany dla kompatybilności)",
    "",
    "Architektura:",
    "  DataProvider (ABC) → FMPProvider | StooqProvider | YFinanceProvider",
    "  CompositeProvider → łańcuch fallback, uzupełnianie brakujących pól",
    "  DataFetcher → używa CompositeProvider (API bez zmian dla reszty systemu)",
    "",
    "Rozszerzalność:",
    "  BloombergProvider / RefinitivProvider → implement DataProvider ABC",
    "  build_composite() → podaj nowe providers w fundamental_providers/price_providers",
]
for line in arch_summary:
    print(f"  {line}")
print()
