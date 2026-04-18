"""
screening/scorer.py
System scoringu i rankingu spółek, które przeszły filtry.

Algorytm:
1. Normalizacja metryk do zakresu [0, 1] (min-max scaling)
2. Aplikacja wag użytkownika
3. Suma ważona → final_score
4. Sortowanie malejąco

Cechy:
- Obsługuje metryki "odwrócone" (np. P/E – niższe jest lepsze → waga ujemna)
- Odporna na outliery (robust scaling opcjonalnie)
- Przejrzyste logowanie wkładów poszczególnych metryk
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd

from screening.filter_engine import FilterResult

logger = logging.getLogger(__name__)


@dataclass
class ScoredTicker:
    """Ticker po scoringu z pełnymi danymi."""
    ticker: str
    score: float
    rank: int
    metrics: dict[str, float | None]
    score_contributions: dict[str, float]  # wkład każdej metryki w score
    passed_fundamental: bool = True
    passed_technical: bool = True
    failed_filters: list[str] = None

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "score": self.score,
            "rank": self.rank,
            "passed_fundamental": self.passed_fundamental,
            "passed_technical": self.passed_technical,
            "failed_filters": self.failed_filters or [],
            "metrics": self.metrics,
        }


class Scorer:
    """
    Oblicza score dla każdego tickera na podstawie ważonych znormalizowanych metryk.
    """

    def __init__(self, config: dict):
        self.weights: dict[str, float] = config.get("scoring", {}).get("weights", {})
        if not self.weights:
            logger.warning("Brak wag scoringu w konfiguracji. Ranking będzie alfabetyczny.")

    def score_and_rank(self, passed_results: list[FilterResult]) -> list[ScoredTicker]:
        """
        Oblicz score dla wszystkich tickerów, które przeszły filtry.

        Args:
            passed_results: lista FilterResult z passed=True

        Returns:
            Lista ScoredTicker posortowana wg score malejąco
        """
        if not passed_results:
            logger.warning("Brak tickerów do scoringu")
            return []

        if not self.weights:
            # Bez wag – równy score dla wszystkich, sortowanie alfabetyczne
            scored = [
                ScoredTicker(
                    ticker=r.ticker,
                    score=0.0,
                    rank=i + 1,
                    metrics=r.metrics,
                    score_contributions={},
                    passed_fundamental=r.passed_fundamental,
                    passed_technical=r.passed_technical,
                    failed_filters=r.failed_filters,
                )
                for i, r in enumerate(sorted(passed_results, key=lambda x: x.ticker))
            ]
            return scored

        # Buduj DataFrame z metrykami
        records = []
        for r in passed_results:
            row = {"ticker": r.ticker, **r.metrics}
            row["_passed_fundamental"] = r.passed_fundamental
            row["_passed_technical"] = r.passed_technical
            row["_failed_filters"] = str(r.failed_filters)
            records.append(row)

        df = pd.DataFrame(records).set_index("ticker")

        # Metryki uwzględniane w scoringu
        scored_metrics = [m for m in self.weights if m in df.columns]
        if not scored_metrics:
            logger.warning(f"Żadna z metryk wagowych {list(self.weights)} "
                           f"nie jest dostępna w danych. Sprawdź konfigurację.")

        contributions: dict[str, pd.Series] = {}
        df["_score"] = 0.0

        for metric in scored_metrics:
            weight = self.weights[metric]
            col = df[metric].copy()

            # Uzupełnij NaN medianą (nie wykluczamy spółki z powodu brakującej metryki scoringowej)
            col_filled = col.fillna(col.median())

            # Min-max normalizacja → [0, 1]
            col_norm = self._normalize(col_filled)

            # Dla metryk z ujemną wagą: wyższa wartość = niższy score
            contribution = col_norm * weight
            contributions[metric] = contribution
            df["_score"] += contribution

        # Finalne ranking
        df = df.sort_values("_score", ascending=False)
        df["_rank"] = range(1, len(df) + 1)

        # Buduj listę ScoredTicker
        scored = []
        for ticker, row in df.iterrows():
            contribs = {m: float(contributions[m].get(ticker, 0.0)) for m in scored_metrics}
            metrics_dict = {
                col: row[col]
                for col in df.columns
                if not col.startswith("_")
            }
            # Konwertuj NaN na None
            metrics_dict = {
                k: (None if isinstance(v, float) and np.isnan(v) else v)
                for k, v in metrics_dict.items()
            }

            scored.append(ScoredTicker(
                ticker=str(ticker),
                score=round(float(row["_score"]), 6),
                rank=int(row["_rank"]),
                metrics=metrics_dict,
                score_contributions=contribs,
                passed_fundamental=bool(row["_passed_fundamental"]),
                passed_technical=bool(row["_passed_technical"]),
                failed_filters=eval(row["_failed_filters"]) if row["_failed_filters"] != "[]" else [],
            ))

        logger.info(
            f"Scoring zakończony: {len(scored)} spółek. "
            f"Top 3: {[s.ticker for s in scored[:3]]}"
        )
        return scored

    @staticmethod
    def _normalize(series: pd.Series) -> pd.Series:
        """
        Min-max normalizacja do [0, 1].
        Jeśli wszystkie wartości są identyczne → zwróć 0.5.
        """
        min_val = series.min()
        max_val = series.max()
        if max_val == min_val:
            return pd.Series(0.5, index=series.index)
        return (series - min_val) / (max_val - min_val)
