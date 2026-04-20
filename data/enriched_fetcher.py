"""
data/enriched_fetcher.py
Orchestrator pobierania danych — łączy Yahoo Finance z zewnętrznymi źródłami.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from data.macro_data import MacroDataFetcher, MacroSnapshot
from data.insider_data import InsiderDataFetcher
from data.news_sentiment import NewsSentimentFetcher

if TYPE_CHECKING:
    from data.fetcher import TickerData

logger = logging.getLogger(__name__)


class EnrichedFetcher:
    """
    Rozszerzony fetcher — wrappuje DataFetcher i wzbogaca dane zewnętrznymi źródłami.
    Każde źródło jest opcjonalne (disabled w config = pomijane bez błędu).
    """

    def __init__(self, config: dict):
        self.config   = config
        self.settings = config.get("settings", {})
        enrich_cfg    = config.get("enrichment", {})

        # DataFetcher imported lazily so yfinance not required at module load time
        from data.fetcher import DataFetcher
        self.base_fetcher = DataFetcher(config)

        # Opcjonalne zewnętrzne źródła
        macro_cfg    = enrich_cfg.get("macro", {})
        insider_cfg  = enrich_cfg.get("insider", {})
        sentiment_cfg = enrich_cfg.get("sentiment", {})

        self.macro_enabled    = macro_cfg.get("enabled", True)
        self.insider_enabled  = insider_cfg.get("enabled", True)
        self.sentiment_enabled = sentiment_cfg.get("enabled", True)

        self.macro_fetcher = MacroDataFetcher(
            fred_api_key=self._key(macro_cfg.get("fred_api_key_env", "FRED_API_KEY")),
        ) if self.macro_enabled else None

        self.insider_fetcher = InsiderDataFetcher(
            lookback_days=insider_cfg.get("lookback_days", 90),
            api_delay=self.settings.get("api_delay_seconds", 0.15),
        ) if self.insider_enabled else None

        self.sentiment_fetcher = NewsSentimentFetcher(
            alpha_vantage_key=self._key(
                sentiment_cfg.get("alpha_vantage_key_env", "ALPHA_VANTAGE_KEY")),
            newsapi_key=self._key(
                sentiment_cfg.get("newsapi_key_env", "NEWSAPI_KEY")),
            lookback_days=sentiment_cfg.get("lookback_days", 30),
            api_delay=self.settings.get("api_delay_seconds", 0.3),
        ) if self.sentiment_enabled else None

    @staticmethod
    def _key(env_var: str) -> str | None:
        import os
        val = os.getenv(env_var, "").strip()
        return val if val else None

    # ── Główna metoda ─────────────────────────────────────────

    def fetch_all_with_enrichment(self, tickers: list[str]) -> list:
        """Pobierz dane dla wszystkich tickerów z wszystkich źródeł."""
        ticker_data_list = self.base_fetcher.fetch_all(tickers)

        # 2. Dane makro (raz na run, nie per ticker)
        macro_snap = self._fetch_macro()

        # 3. Dane insiderów (tylko US stocks)
        insider_map = self._fetch_insider(tickers)

        # 4. Sentyment wiadomości
        sentiment_map = self._fetch_sentiment(tickers)

        # 5. Scal wszystko w TickerData
        for td in ticker_data_list:
            if not td.success:
                continue
            self._merge_macro(td, macro_snap)
            self._merge_insider(td, insider_map)
            self._merge_sentiment(td, sentiment_map)

        return ticker_data_list

    # ── Fetch helpers ─────────────────────────────────────────

    def _fetch_macro(self) -> MacroSnapshot | None:
        if not self.macro_fetcher:
            return None
        try:
            logger.info("Pobieranie danych makroekonomicznych...")
            return self.macro_fetcher.fetch()
        except Exception as exc:
            logger.warning(f"Dane makro niedostępne: {exc}")
            return None

    def _fetch_insider(self, tickers: list[str]) -> dict:
        if not self.insider_fetcher:
            return {}
        try:
            logger.info("Pobieranie transakcji insiderów (SEC EDGAR)...")
            return self.insider_fetcher.fetch_batch(tickers)
        except Exception as exc:
            logger.warning(f"Dane insiderów niedostępne: {exc}")
            return {}

    def _fetch_sentiment(self, tickers: list[str]) -> dict:
        if not self.sentiment_fetcher:
            return {}
        try:
            logger.info("Pobieranie sentymentu wiadomości...")
            return self.sentiment_fetcher.fetch_batch(tickers)
        except Exception as exc:
            logger.warning(f"Sentyment niedostępny: {exc}")
            return {}

    # ── Merge helpers ─────────────────────────────────────────

    def _merge_macro(self, td: TickerData, snap: MacroSnapshot | None) -> None:
        """Dodaj dane makro do każdego tickera (wspólny kontekst)."""
        if not snap:
            return
        extras: dict[str, float | None] = {
            "macro_regime_score":  snap.macro_regime_score,
            "vix":                 snap.vix,
            "yield_curve_10y2y":   snap.yield_curve_10y2y,
        }
        # PKB krajów EM — przydatne dla filtrów geograficznych
        for country, gdp in list(snap.em_gdp_growth.items())[:10]:
            extras[f"em_gdp_{country.lower()}"] = gdp
        td.fundamentals.update(extras)

    def _merge_insider(self, td: TickerData, insider_map: dict) -> None:
        """Dodaj sygnał insiderów do tickera US."""
        signal = insider_map.get(td.ticker)
        if signal and signal.data_available:
            td.fundamentals["insider_buy_ratio"] = signal.buy_ratio
            td.fundamentals["insider_net_shares"] = float(signal.net_shares)

    def _merge_sentiment(self, td: TickerData, sentiment_map: dict) -> None:
        """Dodaj wynik sentymentu do tickera."""
        result = sentiment_map.get(td.ticker)
        if result and result.score is not None:
            td.fundamentals["news_sentiment"] = result.score
