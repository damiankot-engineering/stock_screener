"""
portfolio/builder.py
Konstruktor portfela inwestycyjnego.

Strategie ważenia:
  • equal           – równe wagi (1/N)
  • score_weighted  – wagi proporcjonalne do score
  • rank_weighted   – wagi proporcjonalne do odwrotności ranku (1/rank)

Bonus stabilności:
  Spółki, które regularnie pojawiały się w poprzednich iteracjach screenera,
  otrzymują bonus do score końcowego. Redukuje to rotację portfela i preferuje
  spółki o stabilnych fundamentach.

Architektura portfela:
  1. Pobierz historyczne dane o częstości pojawiania się spółek
  2. Oblicz stability_score = frequency × stability_bonus_weight
  3. Połącz ze score z bieżącego screeningu
  4. Wybierz top N spółek
  5. Oblicz wagi wg wybranej strategii
  6. Normalizuj do sumy = 1.0
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from screening.scorer import ScoredTicker

logger = logging.getLogger(__name__)


@dataclass
class PortfolioPosition:
    """Jedna pozycja w portfelu."""
    ticker: str
    weight: float           # 0.0 – 1.0
    score: float
    rank: int
    stability_score: float  # Jak często pojawiał się historycznie (0.0 – 1.0)
    combined_score: float   # score + stability_bonus
    is_new_entry: bool = True

    def __repr__(self):
        return (f"PortfolioPosition({self.ticker}, "
                f"weight={self.weight:.1%}, rank=#{self.rank}, "
                f"score={self.score:.4f})")


class PortfolioBuilder:
    """
    Buduje portfel inwestycyjny na podstawie wyników screenera.
    Uwzględnia dane historyczne dla stabilności.
    """

    def __init__(self, config: dict, repository=None):
        portfolio_cfg = config.get("portfolio", {})
        self.max_positions = portfolio_cfg.get("max_positions", 20)
        self.min_results = portfolio_cfg.get("min_results", 5)
        self.weighting = portfolio_cfg.get("weighting", "score_weighted")
        self.min_history_runs = portfolio_cfg.get("min_history_runs", 3)
        self.stability_bonus_weight = portfolio_cfg.get("stability_bonus_weight", 0.5)
        self.repository = repository

    def build(
        self,
        scored_tickers: list[ScoredTicker],
        previous_portfolio: set[str] | None = None,
    ) -> list[PortfolioPosition]:
        """
        Zbuduj portfel na podstawie rankingu i danych historycznych.

        Args:
            scored_tickers: Lista ScoredTicker (posortowana wg score malejąco)
            previous_portfolio: Tickers z poprzedniego portfela

        Returns:
            Lista PortfolioPosition z wagami sumującymi się do 1.0
        """
        if len(scored_tickers) < self.min_results:
            logger.warning(
                f"Za mało spółek ({len(scored_tickers)}) do budowy portfela. "
                f"Wymagane minimum: {self.min_results}"
            )
            return []

        # Pobierz dane historyczne
        stability_map = self._get_stability_map()
        run_count = self.repository.get_run_count() if self.repository else 0

        # Oblicz combined_score
        candidates = self._compute_combined_scores(scored_tickers, stability_map, run_count)

        # Wybierz top N
        selected = candidates[:self.max_positions]

        # Oblicz wagi
        positions = self._assign_weights(selected, previous_portfolio or set())

        # Loguj wynik
        total_w = sum(p.weight for p in positions)
        logger.info(
            f"Portfel zbudowany: {len(positions)} pozycji, "
            f"suma wag={total_w:.4f}, "
            f"strategia='{self.weighting}'"
        )
        for p in positions[:5]:
            logger.info(f"  #{p.rank:3d} {p.ticker:<10s} w={p.weight:.1%} "
                        f"score={p.score:.4f} stab={p.stability_score:.2f}")
        if len(positions) > 5:
            logger.info(f"  ... i {len(positions) - 5} kolejnych pozycji")

        return positions

    def _get_stability_map(self) -> dict[str, float]:
        """
        Pobierz mapę ticker → frequency z historii screenera.
        Jeśli brak repozytorium lub za mało historii → zwróć pusty słownik.
        """
        if not self.repository:
            return {}

        try:
            run_count = self.repository.get_run_count()
            if run_count < self.min_history_runs:
                logger.info(
                    f"Za mało historii ({run_count} < {self.min_history_runs}). "
                    f"Stabilność nie będzie uwzględniona."
                )
                return {}

            appearances = self.repository.get_ticker_appearances(
                n_last_runs=max(self.min_history_runs, 10)
            )
            if appearances.empty:
                return {}

            return dict(zip(appearances["ticker"], appearances["frequency"]))
        except Exception as exc:
            logger.warning(f"Błąd pobierania danych historycznych: {exc}")
            return {}

    def _compute_combined_scores(
        self,
        scored: list[ScoredTicker],
        stability_map: dict[str, float],
        run_count: int,
    ) -> list[dict]:
        """Połącz score z bieżącego screeningu ze stability bonus."""
        candidates = []
        for st in scored:
            stab = stability_map.get(st.ticker, 0.0)
            bonus = stab * self.stability_bonus_weight if run_count >= self.min_history_runs else 0.0
            combined = st.score + bonus

            candidates.append({
                "ticker": st.ticker,
                "score": st.score,
                "stability_score": stab,
                "combined_score": combined,
                "rank": st.rank,
                "metrics": st.metrics,
            })

        # Sortuj po combined_score
        candidates.sort(key=lambda x: x["combined_score"], reverse=True)
        # Przenumeruj ranki
        for i, c in enumerate(candidates, 1):
            c["rank"] = i

        return candidates

    def _assign_weights(
        self,
        candidates: list[dict],
        previous_portfolio: set[str],
    ) -> list[PortfolioPosition]:
        """Przypisz wagi i utwórz obiekty PortfolioPosition."""
        n = len(candidates)
        if n == 0:
            return []

        scores = np.array([c["combined_score"] for c in candidates])

        if self.weighting == "equal":
            raw_weights = np.ones(n)

        elif self.weighting == "score_weighted":
            # Przesuń do >0 i dodaj minimalną wagę bazową (50% równej wagi)
            # Zapobiega przyznaniu niemal zerowej wagi ostatnim pozycjom
            base = 0.5 / n
            shifted = scores - scores.min() + 1e-6
            raw_weights = shifted + base * shifted.sum()

        elif self.weighting == "rank_weighted":
            # 1/rank → liniowo malejące wagi
            raw_weights = np.array([1.0 / c["rank"] for c in candidates])

        else:
            logger.warning(f"Nieznana strategia ważenia: '{self.weighting}'. Używam equal.")
            raw_weights = np.ones(n)

        # Normalizacja do sumy = 1.0
        total = raw_weights.sum()
        weights = raw_weights / total if total > 0 else np.ones(n) / n

        positions = []
        for i, (candidate, weight) in enumerate(zip(candidates, weights)):
            positions.append(PortfolioPosition(
                ticker=candidate["ticker"],
                weight=round(float(weight), 6),
                score=round(candidate["score"], 6),
                rank=candidate["rank"],
                stability_score=round(candidate["stability_score"], 4),
                combined_score=round(candidate["combined_score"], 6),
                is_new_entry=candidate["ticker"] not in previous_portfolio,
            ))

        return positions

    def to_dict_list(self, positions: list[PortfolioPosition]) -> list[dict]:
        """Konwertuj do listy słowników (do zapisu w bazie)."""
        return [
            {
                "ticker": p.ticker,
                "weight": p.weight,
                "score": p.score,
                "rank": p.rank,
                "stability_score": p.stability_score,
                "is_new_entry": p.is_new_entry,
            }
            for p in positions
        ]

    @staticmethod
    def print_portfolio_report(positions: list[PortfolioPosition]) -> str:
        """Generuj czytelny raport tekstowy portfela."""
        if not positions:
            return "Brak pozycji w portfelu."

        lines = [
            "═" * 70,
            f"{'PORTFEL INWESTYCYJNY':^70}",
            f"{'Liczba pozycji: ' + str(len(positions)):^70}",
            "═" * 70,
            f"{'#':<4} {'Ticker':<10} {'Waga':>8} {'Score':>10} "
            f"{'Stabilność':>12} {'Nowy?':>6}",
            "─" * 70,
        ]
        for p in positions:
            new_flag = "✓ NOWY" if p.is_new_entry else ""
            lines.append(
                f"{p.rank:<4} {p.ticker:<10} {p.weight:>7.1%} "
                f"{p.score:>10.4f} {p.stability_score:>11.2%} {new_flag:>7}"
            )
        lines.append("─" * 70)
        total_w = sum(p.weight for p in positions)
        lines.append(f"{'Suma wag:':>38} {total_w:>7.1%}")
        lines.append("═" * 70)
        return "\n".join(lines)
