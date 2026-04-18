"""
screening/filter_engine.py
Silnik filtrowania – sprawdza, czy metryki tickera spełniają zdefiniowane progi.
Obsługuje brakujące dane, logikę AND/OR oraz generuje raporty przyczyn odrzucenia.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class FilterResult:
    """Wynik filtrowania jednego tickera."""
    ticker: str
    passed: bool
    metrics: dict[str, float | None]
    failed_filters: list[str] = field(default_factory=list)
    missing_required: list[str] = field(default_factory=list)

    @property
    def passed_fundamental(self) -> bool:
        return not any(f.startswith("fund:") for f in self.failed_filters)

    @property
    def passed_technical(self) -> bool:
        return not any(f.startswith("tech:") for f in self.failed_filters)


class FilterEngine:
    """
    Aplikuje filtry (progi) z konfiguracji użytkownika do danych tickerów.

    Logika:
    - Ticker musi spełniać WSZYSTKIE aktywne filtry (AND)
    - Brak danych dla metryki z filtrem → ticker jest ODRZUCANY (conservative)
    - Metryki bez filtrów są pobierane tylko dla celów scoringu
    """

    # Metryki, których brak (None) powoduje automatyczne odrzucenie
    # (można wyłączyć w konfiguracji)
    STRICT_REQUIRED: frozenset[str] = frozenset()

    def __init__(self, config: dict):
        self.filter_config = config.get("filters", {})
        self.fundamental_filters = self.filter_config.get("fundamental", {})
        self.technical_filters = self.filter_config.get("technical", {})

        # Spłaszczona mapa: metric_name → (min, max, type)
        self._filter_map = self._build_filter_map()
        logger.debug(f"Załadowano {len(self._filter_map)} filtrów: "
                     f"{list(self._filter_map.keys())}")

    def _build_filter_map(self) -> dict[str, tuple[float | None, float | None, str]]:
        """Buduje słownik: metric → (min_val, max_val, type)."""
        result = {}
        for metric, bounds in self.fundamental_filters.items():
            min_val, max_val = self._parse_bounds(bounds)
            result[metric] = (min_val, max_val, "fund")
        for metric, bounds in self.technical_filters.items():
            min_val, max_val = self._parse_bounds(bounds)
            result[metric] = (min_val, max_val, "tech")
        return result

    @staticmethod
    def _parse_bounds(bounds: Any) -> tuple[float | None, float | None]:
        """Parsuj progi: [min, max], null = brak ograniczenia."""
        if not isinstance(bounds, (list, tuple)) or len(bounds) != 2:
            logger.warning(f"Niepoprawny format filtra: {bounds}. Oczekiwano [min, max].")
            return None, None
        min_val = float(bounds[0]) if bounds[0] is not None else None
        max_val = float(bounds[1]) if bounds[1] is not None else None
        return min_val, max_val

    def apply(self, ticker: str, metrics: dict[str, float | None]) -> FilterResult:
        """
        Sprawdź, czy ticker spełnia wszystkie filtry.

        Args:
            ticker: symbol tickera
            metrics: słownik {metric_name: wartość} (None = brak danych)

        Returns:
            FilterResult z wynikiem i szczegółami odrzucenia
        """
        failed: list[str] = []
        missing: list[str] = []

        for metric_name, (min_val, max_val, mtype) in self._filter_map.items():
            value = metrics.get(metric_name)

            # Brak danych → odrzuć (conservative approach)
            if value is None:
                missing.append(metric_name)
                failed.append(f"{mtype}:{metric_name}=NULL")
                continue

            # Sprawdzenie dolnego progu
            if min_val is not None and value < min_val:
                failed.append(f"{mtype}:{metric_name}={value:.4g}<{min_val}")
                continue

            # Sprawdzenie górnego progu
            if max_val is not None and value > max_val:
                failed.append(f"{mtype}:{metric_name}={value:.4g}>{max_val}")

        passed = len(failed) == 0
        return FilterResult(
            ticker=ticker,
            passed=passed,
            metrics=metrics,
            failed_filters=failed,
            missing_required=missing,
        )

    def apply_batch(
        self, ticker_data_list: list
    ) -> tuple[list[FilterResult], list[FilterResult]]:
        """
        Przefiltruj wszystkie tickery.

        Returns:
            Krotka (passed_list, rejected_list)
        """
        passed = []
        rejected = []

        for td in ticker_data_list:
            if not td.success:
                rejected.append(FilterResult(
                    ticker=td.ticker,
                    passed=False,
                    metrics={},
                    failed_filters=["FETCH_ERROR"],
                ))
                continue

            result = self.apply(td.ticker, td.all_metrics)
            if result.passed:
                passed.append(result)
            else:
                rejected.append(result)

        logger.info(
            f"Filtrowanie: {len(passed)} przeszło / "
            f"{len(rejected)} odrzucono / "
            f"łącznie {len(passed) + len(rejected)}"
        )

        # Statystyki odrzuceń (dla debugowania)
        if rejected:
            self._log_rejection_stats(rejected)

        return passed, rejected

    def _log_rejection_stats(self, rejected: list[FilterResult]) -> None:
        """Loguj, które filtry najczęściej powodują odrzucenia."""
        from collections import Counter
        reason_counts: Counter = Counter()
        for r in rejected:
            for f in r.failed_filters:
                # Wyciągnij tylko nazwę metryki (bez wartości)
                metric = f.split(":")[1].split("=")[0] if ":" in f else f
                reason_counts[metric] += 1

        top_reasons = reason_counts.most_common(5)
        logger.debug(f"Top powody odrzucenia: {top_reasons}")

    def get_filter_summary(self) -> str:
        """Zwróć czytelne podsumowanie aktywnych filtrów."""
        lines = ["Aktywne filtry:"]
        for metric, (min_v, max_v, mtype) in self._filter_map.items():
            bounds_str = f"[{min_v if min_v is not None else '−∞'}, {max_v if max_v is not None else '+∞'}]"
            lines.append(f"  {mtype.upper():4s} │ {metric:<20s} {bounds_str}")
        return "\n".join(lines)
