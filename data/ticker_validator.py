"""
data/ticker_validator.py
Walidacja tickerów zwróconych przez AI przed uruchomieniem głównego screenera.

DZIAŁANIE:
  1. Sprawdź cache w DB — jeśli ticker był walidowany w ciągu TTL dni, użyj wyniku.
  2. Dla pozostałych: odpytaj yf.Ticker(t).fast_info równolegle (ThreadPoolExecutor).
  3. Ticker jest VALID jeśli fast_info zwraca last_price > 0 lub market_cap > 0.
  4. Zapisz wyniki do cache (tabela ticker_validation_cache).
  5. Zwróć (valid_tickers, invalid_tickers).

FEEDBACK LOOP:
  Wszystkie invalid tickery są trwale zapisane w DB.
  AITickerSource może je odczytać i wstawić do promptu jako:
  "AVOID these symbols — they are not available on Yahoo Finance: [...]"
  Dzięki temu AI stopniowo uczy się nie proponować niedziałających tickerów.
"""
from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


@dataclass
class ValidationResult:
    ticker: str
    is_valid: bool
    reason: str          # "ok" | "no_price" | "fetch_error" | "cached_invalid"
    from_cache: bool = False
    last_price: float | None = None
    market_cap: float | None = None


class TickerValidator:
    """
    Waliduje tickery przez szybkie odpytanie yfinance.fast_info.
    Wyniki cache'uje w DB przez TTL dni, żeby nie hammować Yahoo Finance.
    """

    def __init__(self, repository=None, workers: int = 10,
                 api_delay: float = 0.1, cache_ttl_days: int = 30):
        self.repository  = repository
        self.workers     = workers
        self.api_delay   = api_delay
        self.cache_ttl   = timedelta(days=cache_ttl_days)

    def validate_batch(
        self, tickers: list[str]
    ) -> tuple[list[str], list[str]]:
        """
        Podziel listę tickerów na valid i invalid.

        Returns:
            (valid_tickers, invalid_tickers)
        """
        if not tickers:
            return [], []

        logger.info(f"Walidacja {len(tickers)} tickerów (workers={self.workers})")

        # 1. Sprawdź cache
        cached   = self._load_cache(tickers) if self.repository else {}
        to_check = [t for t in tickers if t not in cached]

        logger.info(
            f"  Z cache: {len(cached)} "
            f"(valid={sum(1 for r in cached.values() if r.is_valid)}, "
            f"invalid={sum(1 for r in cached.values() if not r.is_valid)})  "
            f"Do sprawdzenia: {len(to_check)}"
        )

        # 2. Sprawdź live
        live_results: dict[str, ValidationResult] = {}
        if to_check:
            live_results = self._validate_live(to_check)
            if self.repository:
                self._save_cache(live_results)

        # 3. Scal wyniki
        all_results = {**cached, **live_results}

        valid   = [t for t in tickers if all_results.get(t, ValidationResult(t, False, "missing")).is_valid]
        invalid = [t for t in tickers if not all_results.get(t, ValidationResult(t, False, "missing")).is_valid]

        # Loguj nieudane
        if invalid:
            reasons = {t: all_results[t].reason for t in invalid if t in all_results}
            logger.warning(
                f"Odrzucone tickery ({len(invalid)}): "
                + ", ".join(f"{t}({r})" for t, r in list(reasons.items())[:10])
                + ("..." if len(invalid) > 10 else "")
            )

        logger.info(f"Walidacja zakończona: {len(valid)} valid / {len(invalid)} invalid")
        return valid, invalid

    # ── Live validation ───────────────────────────────────────

    def _validate_live(
        self, tickers: list[str]
    ) -> dict[str, ValidationResult]:
        results: dict[str, ValidationResult] = {}

        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            futures = {
                pool.submit(self._check_one, ticker): ticker
                for ticker in tickers
            }
            for future in as_completed(futures):
                result = future.result()
                results[result.ticker] = result

        return results

    def _check_one(self, ticker: str) -> ValidationResult:
        """Sprawdź jeden ticker przez yf.fast_info."""
        time.sleep(self.api_delay)
        try:
            import yfinance as yf
            info = yf.Ticker(ticker).fast_info

            last_price = getattr(info, "last_price", None)
            market_cap = getattr(info, "market_cap", None)

            # Ticker jest valid jeśli ma cenę lub market cap
            if last_price and last_price > 0:
                return ValidationResult(
                    ticker=ticker, is_valid=True, reason="ok",
                    last_price=float(last_price),
                    market_cap=float(market_cap) if market_cap else None,
                )
            if market_cap and market_cap > 0:
                return ValidationResult(
                    ticker=ticker, is_valid=True, reason="ok",
                    market_cap=float(market_cap),
                )

            return ValidationResult(ticker=ticker, is_valid=False, reason="no_price")

        except Exception as exc:
            short = str(exc)[:120]
            logger.debug(f"Błąd walidacji {ticker}: {short}")
            return ValidationResult(ticker=ticker, is_valid=False, reason="fetch_error")

    # ── Cache DB ──────────────────────────────────────────────

    def _load_cache(self, tickers: list[str]) -> dict[str, ValidationResult]:
        """Wczytaj świeże wpisy z cache (w ramach TTL)."""
        try:
            cutoff = datetime.utcnow() - self.cache_ttl
            rows = self.repository.get_validation_cache(tickers, since=cutoff)
            result = {}
            for row in rows:
                result[row["ticker"]] = ValidationResult(
                    ticker=row["ticker"],
                    is_valid=row["is_valid"],
                    reason=row["reason"] + " (cached)" if not row["is_valid"] else "ok (cached)",
                    from_cache=True,
                )
            return result
        except Exception as exc:
            logger.debug(f"Błąd odczytu cache walidacji: {exc}")
            return {}

    def _save_cache(self, results: dict[str, ValidationResult]) -> None:
        """Zapisz wyniki walidacji do cache."""
        try:
            rows = [
                {
                    "ticker":   r.ticker,
                    "is_valid": r.is_valid,
                    "reason":   r.reason,
                    "last_price": r.last_price,
                    "market_cap": r.market_cap,
                    "checked_at": datetime.utcnow(),
                }
                for r in results.values()
            ]
            self.repository.upsert_validation_cache(rows)
        except Exception as exc:
            logger.debug(f"Błąd zapisu cache walidacji: {exc}")
