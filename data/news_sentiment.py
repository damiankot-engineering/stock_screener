"""
data/news_sentiment.py
Analiza sentymentu wiadomości finansowych.

ŹRÓDŁA (od darmowych do płatnych):
  1. RSS feeds finansowe (DARMOWE, bez klucza) — Yahoo Finance, Reuters, Bloomberg
  2. Alpha Vantage News Sentiment (DARMOWE, 25 req/dzień z darmowym kluczem)
     Klucz: https://www.alphavantage.co/support/#api-key
  3. NewsAPI (DARMOWE, 100 req/dzień z darmowym kluczem)
     Klucz: https://newsapi.org/register

ALGORYTM SENTYMENTU:
  Prosta analiza leksykalna (VADER-style) na bazie listy słów kluczowych.
  Nie wymaga bibliotek ML ani danych treningowych — działa na czystym Pythonie.
  Wynik: float od -1.0 (bardzo negatywny) do +1.0 (bardzo pozytywny).

  Dla spółek EM dodatkowe słowa kluczowe: regulatory risk, currency risk,
  political risk, governance issue, sanctions, capital controls.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import quote

import requests

logger = logging.getLogger(__name__)

# ── Leksykon sentymentu ───────────────────────────────────────

POSITIVE_WORDS = {
    "beat", "beats", "exceed", "exceeded", "record", "growth", "profit",
    "surge", "rally", "upgrade", "outperform", "bullish", "strong",
    "expansion", "innovation", "breakthrough", "partnership", "acquisition",
    "revenue", "earnings", "dividend", "buyback", "raised", "guidance",
    "momentum", "opportunity", "recovery", "accelerat", "robust", "solid",
    "win", "won", "award", "contract", "deal", "invest", "launch",
}

NEGATIVE_WORDS = {
    "miss", "missed", "loss", "losses", "decline", "fall", "fell", "drop",
    "downgrade", "underperform", "bearish", "weak", "cut", "cuts", "reduce",
    "warning", "concern", "risk", "fraud", "investigation", "lawsuit", "fine",
    "sanction", "ban", "default", "debt", "bankruptcy", "layoff", "resign",
    "scandal", "hack", "breach", "recall", "halt", "suspend", "delisted",
    # Ryzyka specyficzne dla rynków wschodzących:
    "corruption", "coup", "capital controls", "currency crisis", "hyperinflation",
    "nationali", "expropriat", "political instability", "governance",
}

STRONG_NEGATIVE = {"fraud", "bankruptcy", "scandal", "default", "investigation",
                   "nationali", "expropriat", "sanctions"}
STRONG_POSITIVE = {"record", "breakthrough", "beat", "exceed"}


@dataclass
class SentimentResult:
    """Wynik analizy sentymentu dla jednej spółki."""
    ticker:          str
    score:           float | None = None   # -1.0 do +1.0
    articles_count:  int   = 0
    positive_signals: int  = 0
    negative_signals: int  = 0
    source:          str   = "rss"
    error:           str   = ""
    sample_headlines: list[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        if self.score is None:    return "unknown"
        if self.score > 0.3:     return "positive"
        if self.score < -0.3:    return "negative"
        return "neutral"


class NewsSentimentFetcher:
    """
    Analizuje sentyment wiadomości finansowych dla listy spółek.
    Domyślnie RSS feeds (brak klucza) → Alpha Vantage → NewsAPI.
    """

    RSS_FEEDS = {
        "yahoo":   "https://finance.yahoo.com/rss/headline?s={ticker}",
        "seeking": "https://seekingalpha.com/api/sa/combined/{ticker}.xml",
    }

    def __init__(
        self,
        alpha_vantage_key: str | None = None,
        newsapi_key: str | None = None,
        timeout: int = 10,
        api_delay: float = 0.3,
        lookback_days: int = 30,
    ):
        self.av_key        = (alpha_vantage_key or "").strip()
        self.newsapi_key   = (newsapi_key or "").strip()
        self.timeout       = timeout
        self.api_delay     = api_delay
        self.lookback_days = lookback_days

    # ── Główna metoda ─────────────────────────────────────────

    def fetch_batch(
        self, tickers: list[str]
    ) -> dict[str, SentimentResult]:
        """Pobierz sentyment dla listy tickerów."""
        results: dict[str, SentimentResult] = {}

        for ticker in tickers:
            try:
                if self.av_key:
                    result = self._fetch_alpha_vantage(ticker)
                else:
                    result = self._fetch_rss(ticker)
                results[ticker] = result
                time.sleep(self.api_delay)
            except Exception as exc:
                logger.debug(f"Sentiment {ticker}: {exc}")
                results[ticker] = SentimentResult(ticker=ticker, error=str(exc)[:80])

        scored = sum(1 for r in results.values() if r.score is not None)
        logger.info(f"Sentyment: {scored}/{len(tickers)} spółek z wynikiem")
        return results

    # ── Alpha Vantage (25 req/dzień darmowo) ──────────────────

    def _fetch_alpha_vantage(self, ticker: str) -> SentimentResult:
        """Alpha Vantage News Sentiment API."""
        resp = requests.get(
            "https://www.alphavantage.co/query",
            params={
                "function":  "NEWS_SENTIMENT",
                "tickers":   ticker,
                "apikey":    self.av_key,
                "limit":     20,
                "time_from": (datetime.utcnow() - timedelta(days=self.lookback_days))
                              .strftime("%Y%m%dT0000"),
            },
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()

        if "feed" not in data:
            return SentimentResult(ticker=ticker, error="no feed in AV response",
                                   source="alpha_vantage")

        articles = data["feed"]
        scores:    list[float] = []
        pos = neg  = 0
        headlines: list[str]  = []

        for art in articles:
            # AV zwraca per-ticker sentiment
            for ts in art.get("ticker_sentiment", []):
                if ts.get("ticker", "").upper() == ticker.upper():
                    raw = float(ts.get("ticker_sentiment_score", 0))
                    scores.append(raw)
                    if raw > 0.15:  pos += 1
                    elif raw < -0.15: neg += 1
            if len(headlines) < 3:
                headlines.append(art.get("title", "")[:80])

        if not scores:
            return SentimentResult(ticker=ticker, articles_count=len(articles),
                                   error="no sentiment for ticker", source="alpha_vantage")

        avg_score = round(sum(scores) / len(scores), 4)
        return SentimentResult(
            ticker=ticker, score=avg_score,
            articles_count=len(articles),
            positive_signals=pos, negative_signals=neg,
            source="alpha_vantage",
            sample_headlines=headlines,
        )

    # ── RSS feeds (bez klucza) ────────────────────────────────

    def _fetch_rss(self, ticker: str) -> SentimentResult:
        """Pobierz i przeanalizuj RSS feed Yahoo Finance dla tickera."""
        url = f"https://finance.yahoo.com/rss/headline?s={quote(ticker)}"
        try:
            resp = requests.get(url, timeout=self.timeout,
                                headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()
        except Exception as exc:
            return SentimentResult(ticker=ticker, error=str(exc)[:80], source="rss")

        # Wyodrębnij tytuły i opisy z XML RSS
        import xml.etree.ElementTree as ET
        texts: list[str] = []
        try:
            root = ET.fromstring(resp.text)
            for item in root.findall(".//item"):
                title = item.findtext("title") or ""
                desc  = item.findtext("description") or ""
                pub   = item.findtext("pubDate") or ""
                # Filtruj po dacie jeśli dostępna
                if pub:
                    try:
                        from email.utils import parsedate_to_datetime
                        pub_dt = parsedate_to_datetime(pub).replace(tzinfo=None)
                        if pub_dt < datetime.utcnow() - timedelta(days=self.lookback_days):
                            continue
                    except Exception:
                        pass
                texts.append(f"{title} {desc}")
        except ET.ParseError:
            # Fallback: wyodrębnij tekstem
            texts = re.findall(r'<title>(.*?)</title>', resp.text)

        if not texts:
            return SentimentResult(ticker=ticker, error="empty RSS feed", source="rss")

        return self._score_texts(ticker, texts, source="rss")

    # ── Leksykalna analiza sentymentu ─────────────────────────

    def _score_texts(
        self, ticker: str, texts: list[str], source: str = "rss"
    ) -> SentimentResult:
        """Prosta analiza leksykalna bez zewnętrznych modeli ML."""
        pos_hits = neg_hits = 0
        headlines: list[str] = []

        for text in texts[:20]:
            low = text.lower()
            words = set(re.findall(r'\b\w+\b', low))

            p = sum(2 if w in STRONG_POSITIVE else 1
                    for w in POSITIVE_WORDS if w in low)
            n = sum(2 if any(w in low for w in STRONG_NEGATIVE) else 1
                    for w in NEGATIVE_WORDS if w in low)
            pos_hits += p
            neg_hits += n

            if len(headlines) < 3 and text.strip():
                headlines.append(text[:80].strip())

        total = pos_hits + neg_hits
        if total == 0:
            return SentimentResult(ticker=ticker, articles_count=len(texts),
                                   error="no signal words found", source=source)

        score = round((pos_hits - neg_hits) / total, 4)
        return SentimentResult(
            ticker=ticker,
            score=score,
            articles_count=len(texts),
            positive_signals=pos_hits,
            negative_signals=neg_hits,
            source=source,
            sample_headlines=headlines,
        )
