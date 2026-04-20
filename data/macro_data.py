"""
data/macro_data.py
Pobieranie danych makroekonomicznych z darmowych API.

ŹRÓDŁA:
  • FRED (Federal Reserve) — klucz API darmowy: https://fred.stlouisfed.org/docs/api/api_key.html
    Dane: krzywa rentowności, stopy procentowe, inflacja, VIX, spread kredytowy
  • World Bank Open Data — bez klucza API, dane dla krajów wschodzących
    Dane: wzrost PKB, inflacja, bezrobocie per kraj

TRYB BEZ KLUCZA FRED:
  Jeśli FRED_API_KEY nie jest ustawiony, moduł zwraca dane z World Bank i
  uproszczone dane makro z Yahoo Finance (^VIX, ^TNX itp.).
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import requests

logger = logging.getLogger(__name__)

# ── Kraje wschodzące – kody World Bank ───────────────────────
EM_COUNTRIES = {
    "IN": "India",
    "BR": "Brazil",
    "CN": "China",
    "ID": "Indonesia",
    "MX": "Mexico",
    "ZA": "South Africa",
    "TR": "Turkey",
    "KR": "South Korea",
    "TW": "Taiwan",
    "PH": "Philippines",
    "VN": "Vietnam",
    "NG": "Nigeria",
    "EG": "Egypt",
    "AR": "Argentina",
    "CL": "Chile",
    "CO": "Colombia",
    "PL": "Poland",
    "CZ": "Czech Republic",
    "HU": "Hungary",
    "RO": "Romania",
}


@dataclass
class MacroSnapshot:
    """Bieżący stan makroekonomiczny."""
    timestamp: datetime = field(default_factory=datetime.utcnow)

    # FRED / Yahoo Finance proxy
    yield_curve_10y2y: float | None = None   # spread 10Y-2Y (ujemny = inwersja)
    fed_funds_rate: float | None = None
    us_cpi_yoy: float | None = None           # inflacja USA YoY %
    vix: float | None = None                  # indeks strachu
    us_hy_spread: float | None = None         # spread high-yield (ryzyko kredytowe)
    dxy: float | None = None                  # indeks dolara

    # World Bank – kraje wschodzące
    em_gdp_growth: dict[str, float] = field(default_factory=dict)   # kraj → PKB YoY %
    em_inflation:  dict[str, float] = field(default_factory=dict)   # kraj → inflacja %

    # Syntetyczny wynik makro (0=risk-off, 1=risk-on)
    macro_regime_score: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "yield_curve_10y2y":   self.yield_curve_10y2y,
            "fed_funds_rate":      self.fed_funds_rate,
            "us_cpi_yoy":          self.us_cpi_yoy,
            "vix":                 self.vix,
            "us_hy_spread":        self.us_hy_spread,
            "dxy":                 self.dxy,
            "macro_regime_score":  self.macro_regime_score,
            **{f"gdp_{k}": v for k, v in self.em_gdp_growth.items()},
        }


class MacroDataFetcher:
    """
    Pobiera bieżące dane makroekonomiczne.
    Każde pole ma fallback — brak jednego źródła nie blokuje całości.
    """

    FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"
    WB_BASE   = "https://api.worldbank.org/v2"

    def __init__(self, fred_api_key: str | None = None, timeout: int = 15):
        self.fred_key = (fred_api_key or "").strip()
        self.timeout  = timeout

    # ── Główna metoda ─────────────────────────────────────────

    def fetch(self) -> MacroSnapshot:
        """Pobierz pełny snapshot makro."""
        snap = MacroSnapshot()

        if self.fred_key:
            self._fetch_fred(snap)
        else:
            logger.info("FRED_API_KEY nie ustawiony – używam Yahoo Finance jako proxy makro")
            self._fetch_yahoo_macro_proxy(snap)

        self._fetch_world_bank(snap)
        snap.macro_regime_score = self._compute_regime_score(snap)

        logger.info(
            f"Makro: VIX={snap.vix}, krzywa={snap.yield_curve_10y2y}, "
            f"regime={snap.macro_regime_score:.2f}, "
            f"EM PKB: {len(snap.em_gdp_growth)} krajów"
        )
        return snap

    # ── FRED ─────────────────────────────────────────────────

    def _fetch_fred(self, snap: MacroSnapshot) -> None:
        """Pobierz kluczowe serie z FRED."""
        series_map = {
            "T10Y2Y":   "yield_curve_10y2y",
            "FEDFUNDS": "fed_funds_rate",
            "CPIAUCSL": "us_cpi_yoy",
            "VIXCLS":   "vix",
        }
        for series_id, attr in series_map.items():
            try:
                val = self._fred_latest(series_id)
                setattr(snap, attr, val)
                time.sleep(0.2)
            except Exception as exc:
                logger.debug(f"FRED {series_id}: {exc}")

    def _fred_latest(self, series_id: str) -> float | None:
        """Pobierz ostatnią dostępną wartość serii FRED."""
        resp = requests.get(
            self.FRED_BASE,
            params={
                "series_id":  series_id,
                "api_key":    self.fred_key,
                "limit":      5,
                "sort_order": "desc",
                "file_type":  "json",
            },
            timeout=self.timeout,
        )
        resp.raise_for_status()
        observations = resp.json().get("observations", [])
        for obs in observations:
            val = obs.get("value", ".")
            if val != ".":
                return float(val)
        return None

    # ── Yahoo Finance proxy (bez klucza FRED) ────────────────

    def _fetch_yahoo_macro_proxy(self, snap: MacroSnapshot) -> None:
        """
        Pobierz dane makro z Yahoo Finance jako proxy.
        Symbole: ^VIX, ^TNX (10Y), ^IRX (3M), DX-Y.NYB (DXY).
        """
        try:
            import yfinance as yf
            symbols = {"^VIX": "vix", "DX-Y.NYB": "dxy"}
            for sym, attr in symbols.items():
                try:
                    hist = yf.Ticker(sym).history(period="5d")
                    if not hist.empty:
                        setattr(snap, attr, round(float(hist["Close"].iloc[-1]), 4))
                except Exception:
                    pass

            # Krzywa rentowności: 10Y - 3M
            try:
                t10 = yf.Ticker("^TNX").history(period="5d")
                t3m = yf.Ticker("^IRX").history(period="5d")
                if not t10.empty and not t3m.empty:
                    snap.yield_curve_10y2y = round(
                        float(t10["Close"].iloc[-1]) - float(t3m["Close"].iloc[-1]), 4
                    )
            except Exception:
                pass
        except ImportError:
            logger.debug("yfinance niedostępne dla proxy makro")

    # ── World Bank ────────────────────────────────────────────

    def _fetch_world_bank(self, snap: MacroSnapshot) -> None:
        """
        Pobierz PKB YoY i inflację dla krajów wschodzących.
        World Bank API — całkowicie darmowe, bez klucza.
        mrv=2 = dwie ostatnie dostępne obserwacje (dane roczne z opóźnieniem).
        """
        gdp_indicator = "NY.GDP.MKTP.KD.ZG"   # PKB wzrost realny %
        inf_indicator = "FP.CPI.TOTL.ZG"       # inflacja CPI %

        # Batch request: wszystkie kraje EM naraz
        countries_str = ";".join(EM_COUNTRIES.keys())
        for indicator, target_dict in [
            (gdp_indicator, snap.em_gdp_growth),
            (inf_indicator, snap.em_inflation),
        ]:
            try:
                resp = requests.get(
                    f"{self.WB_BASE}/country/{countries_str}/indicator/{indicator}",
                    params={"format": "json", "mrv": 2, "per_page": 100},
                    timeout=self.timeout,
                )
                resp.raise_for_status()
                data = resp.json()
                if len(data) < 2:
                    continue
                # Pierwsza pozycja to metadata, druga to dane
                for entry in data[1] or []:
                    code  = entry.get("countryiso3code", "")[:2]
                    value = entry.get("value")
                    if code and value is not None and code not in target_dict:
                        target_dict[code] = round(float(value), 2)
                time.sleep(0.3)
            except Exception as exc:
                logger.debug(f"World Bank {indicator}: {exc}")

    # ── Regime score ──────────────────────────────────────────

    def _compute_regime_score(self, snap: MacroSnapshot) -> float:
        """
        Syntetyczny wskaźnik risk-on / risk-off (0.0 – 1.0).
        Wyższy = bardziej sprzyjające środowisko dla ryzykownych aktywów.
        """
        score = 0.5  # domyślnie neutralnie
        votes = 0

        # VIX < 20 → risk-on, > 30 → risk-off
        if snap.vix is not None:
            if snap.vix < 15:   score += 0.15; votes += 1
            elif snap.vix < 20: score += 0.08; votes += 1
            elif snap.vix > 30: score -= 0.15; votes += 1
            elif snap.vix > 25: score -= 0.08; votes += 1

        # Krzywa rentowności > 0 → risk-on, < -0.5 → sygnał recesji
        if snap.yield_curve_10y2y is not None:
            if snap.yield_curve_10y2y > 0.5:   score += 0.10; votes += 1
            elif snap.yield_curve_10y2y > 0:   score += 0.05; votes += 1
            elif snap.yield_curve_10y2y < -0.5: score -= 0.10; votes += 1
            elif snap.yield_curve_10y2y < 0:   score -= 0.05; votes += 1

        # Wzrost PKB EM > 4% → pozytywny sygnał dla EM
        if snap.em_gdp_growth:
            avg_em_gdp = sum(snap.em_gdp_growth.values()) / len(snap.em_gdp_growth)
            if avg_em_gdp > 5:   score += 0.10; votes += 1
            elif avg_em_gdp > 3: score += 0.05; votes += 1
            elif avg_em_gdp < 1: score -= 0.05; votes += 1

        return round(max(0.0, min(1.0, score)), 3)

    def get_em_context_for_prompt(self, snap: MacroSnapshot) -> str:
        """
        Generuj opis makro dla promptu AI — kontekstualizuje wybór spółek.
        """
        lines = []

        if snap.macro_regime_score is not None:
            regime = "RISK-ON" if snap.macro_regime_score > 0.6 else \
                     "RISK-OFF" if snap.macro_regime_score < 0.4 else "NEUTRAL"
            lines.append(f"Current macro regime: {regime} (score={snap.macro_regime_score:.2f})")

        if snap.vix:
            lines.append(f"VIX: {snap.vix:.1f}")
        if snap.yield_curve_10y2y is not None:
            inv = "INVERTED" if snap.yield_curve_10y2y < 0 else "normal"
            lines.append(f"Yield curve (10Y-2Y): {snap.yield_curve_10y2y:+.2f}% ({inv})")

        # Top rosnące kraje EM
        if snap.em_gdp_growth:
            top_em = sorted(snap.em_gdp_growth.items(), key=lambda x: x[1], reverse=True)[:5]
            em_str = ", ".join(f"{EM_COUNTRIES.get(c, c)} {v:+.1f}%" for c, v in top_em)
            lines.append(f"Fastest-growing EM economies: {em_str}")

        return "\n".join(lines) if lines else "Macro data unavailable."
