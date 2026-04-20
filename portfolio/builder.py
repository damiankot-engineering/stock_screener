"""
portfolio/builder.py
Konstruktor portfela inwestycyjnego oparty WYŁĄCZNIE na danych historycznych.

FILOZOFIA PROJEKTOWA:
══════════════════════════════════════════════════════════════════════
Portfel NIE jest budowany po każdym uruchomieniu screenera.
Screener gromadzi dane do bazy (metric_snapshots, screening_results).
Portfel powstaje jako synteza tej historii — po zgromadzeniu min.
N uruchomień — metodą `portfolio build` (oddzielna komenda CLI).

Daje to:
  • Odporność na jednorazowe szumy rynkowe i anomalie AI
  • Premiowanie spółek KONSEKWENTNIE spełniających kryteria
  • Stabilność — portfel zmienia się ewolucyjnie, nie skokowo
  • Pełną audytowalność decyzji (każda oparta na danych z DB)
══════════════════════════════════════════════════════════════════════

ALGORYTM BUDOWY PORTFELA Z HISTORII:
  1. Pobierz N ostatnich uruchomień screenera z DB
  2. Dla każdego tickera oblicz:
       appearance_rate   = liczba_pojawień / N_runów          (0–1)
       avg_score         = średni score z runów, w których był
       score_consistency = 1 - std(score) / max(std, ε)       (stabilność)
       avg_rank          = średnia pozycja w rankingu
       trend_score       = slope regresji liniowej score w czasie
  3. Composite score = ważona suma powyższych metryk
  4. Wybierz top K spółek (max_positions z config)
  5. Oblicz wagi (equal | score_weighted | rank_weighted)
  6. Zapisz portfel do DB z run_id = None (osobna tabela portfolio_builds)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

if TYPE_CHECKING:
    from db.repository import ScreenerRepository

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════
# DATA CLASSES
# ══════════════════════════════════════════════════════════════

@dataclass
class PortfolioPosition:
    """Jedna pozycja w portfelu historycznym."""
    ticker: str
    weight: float                   # 0.0 – 1.0, suma = 1.0
    composite_score: float          # finalny score historyczny
    rank: int                       # pozycja w portfelu
    appearance_rate: float          # % uruchomień, w których wystąpił
    avg_score: float                # średni score ze screeningu
    score_consistency: float        # stabilność score (0=niestabilny, 1=stały)
    avg_rank: float                 # średnia pozycja w rankingu
    trend_score: float              # trend score w czasie (>0 = rosnący)
    n_appearances: int              # bezwzględna liczba pojawień
    is_new_entry: bool = True       # nowy w stosunku do poprzedniego portfela

    def to_dict(self) -> dict:
        return {
            "ticker":            self.ticker,
            "weight":            self.weight,
            "composite_score":   self.composite_score,
            "rank":              self.rank,
            "appearance_rate":   self.appearance_rate,
            "avg_score":         self.avg_score,
            "score_consistency": self.score_consistency,
            "avg_rank":          self.avg_rank,
            "trend_score":       self.trend_score,
            "n_appearances":     self.n_appearances,
            "is_new_entry":      self.is_new_entry,
            # backward compat z repository.save_portfolio()
            "score":             self.composite_score,
            "stability_score":   self.appearance_rate,
        }


@dataclass
class PortfolioBuildResult:
    """Wynik budowy portfela z historii."""
    positions: list[PortfolioPosition]
    n_runs_used: int
    n_candidates_evaluated: int
    build_timestamp: datetime = field(default_factory=datetime.utcnow)
    strategy_used: str = "historical_composite"
    weighting_used: str = "score_weighted"
    config_snapshot: dict = field(default_factory=dict)

    @property
    def is_valid(self) -> bool:
        return len(self.positions) > 0

    def summary(self) -> str:
        if not self.is_valid:
            return "Portfel pusty."
        weights = [p.weight for p in self.positions]
        top3 = ", ".join(
            f"{p.ticker}({p.appearance_rate:.0%})" for p in self.positions[:3]
        )
        return (
            f"Portfel historyczny: {len(self.positions)} pozycji | "
            f"Bazy: {self.n_runs_used} runów | "
            f"Kandydatów: {self.n_candidates_evaluated} | "
            f"Top 3: {top3}"
        )


# ══════════════════════════════════════════════════════════════
# GŁÓWNA KLASA
# ══════════════════════════════════════════════════════════════

class PortfolioBuilder:
    """
    Buduje portfel inwestycyjny wyłącznie z danych historycznych w bazie.

    Nie przyjmuje scored_tickers z bieżącego run'u — zamiast tego
    odpytuje DB o całą historię screening_results i buduje portfel
    na jej podstawie.
    """

    # Wagi składowych composite_score (konfigurowalnie przez użytkownika)
    DEFAULT_COMPOSITE_WEIGHTS = {
        "appearance_rate":   3.0,   # Najważniejsza: jak często spółka przechodzi filtry
        "avg_score":         2.0,   # Średnia jakość fundamentalna/techniczna
        "score_consistency": 1.5,   # Stabilność oceny (mniej wahań = lepiej)
        "trend_score":       1.0,   # Rosnący trend score = lepsza trajektoria
        "avg_rank_inv":      0.5,   # Odwrotność avg_rank (wyższy rank = lepiej)
    }

    def __init__(self, config: dict, repository: "ScreenerRepository | None" = None):
        portfolio_cfg = config.get("portfolio", {})
        self.max_positions      = portfolio_cfg.get("max_positions", 20)
        self.min_results        = portfolio_cfg.get("min_results", 5)
        self.weighting          = portfolio_cfg.get("weighting", "score_weighted")
        self.min_history_runs   = portfolio_cfg.get("min_history_runs", 3)
        self.composite_weights  = portfolio_cfg.get(
            "composite_weights", self.DEFAULT_COMPOSITE_WEIGHTS
        )
        self.repository = repository

    # ──────────────────────────────────────────────────────────
    # Główna metoda: buduj portfel z historii DB
    # ──────────────────────────────────────────────────────────

    def build_from_history(
        self,
        n_last_runs: int | None = None,
        previous_portfolio: set[str] | None = None,
    ) -> PortfolioBuildResult:
        """
        Zbuduj portfel na podstawie historycznych danych ze screenera.

        Args:
            n_last_runs: ile ostatnich runów uwzględnić (None = wszystkie)
            previous_portfolio: tickers z poprzedniego portfela (dla is_new_entry)

        Returns:
            PortfolioBuildResult z listą pozycji i metadanymi
        """
        if not self.repository:
            raise RuntimeError(
                "PortfolioBuilder wymaga repozytorium DB do budowy portfela z historii. "
                "Inicjalizuj z repository=ScreenerRepository(...)."
            )

        # Sprawdź, czy mamy wystarczającą historię
        total_runs = self.repository.get_run_count()
        if total_runs < self.min_history_runs:
            logger.warning(
                f"Zbyt mało danych historycznych: {total_runs} runów "
                f"(wymagane minimum: {self.min_history_runs}). "
                f"Uruchom screener co najmniej {self.min_history_runs - total_runs} razy więcej."
            )
            return PortfolioBuildResult(
                positions=[], n_runs_used=total_runs,
                n_candidates_evaluated=0,
            )

        effective_runs = n_last_runs or min(total_runs, 20)
        logger.info(
            f"Budowanie portfela historycznego: {effective_runs} ostatnich runów "
            f"(dostępnych: {total_runs})"
        )

        # Pobierz szczegółowe dane historyczne
        history_df = self._fetch_detailed_history(effective_runs)
        if history_df.empty:
            logger.warning("Brak danych w screening_results. Uruchom screener przynajmniej raz.")
            return PortfolioBuildResult(
                positions=[], n_runs_used=effective_runs, n_candidates_evaluated=0,
            )

        n_unique_tickers = history_df["ticker"].nunique()
        logger.info(f"Przeanalizowano {n_unique_tickers} unikalnych tickerów z historii")

        # Oblicz metryki historyczne per ticker
        metrics_df = self._compute_historical_metrics(history_df, effective_runs)

        # Filtruj kandydatów (muszą pojawić się w min. 2 runach lub >20% runów)
        min_appearances = max(2, int(effective_runs * 0.15))
        candidates = metrics_df[metrics_df["n_appearances"] >= min_appearances].copy()

        if candidates.empty:
            logger.warning(
                f"Żaden ticker nie pojawił się wystarczająco często "
                f"(min. {min_appearances} z {effective_runs} runów). "
                f"Najczęstszy ticker: {metrics_df.iloc[0]['ticker'] if not metrics_df.empty else 'N/A'} "
                f"({metrics_df.iloc[0]['n_appearances'] if not metrics_df.empty else 0}×)"
            )
            return PortfolioBuildResult(
                positions=[], n_runs_used=effective_runs,
                n_candidates_evaluated=n_unique_tickers,
            )

        logger.info(
            f"Kandydaci po filtrze minimalnej częstości "
            f"(≥{min_appearances}×): {len(candidates)} spółek"
        )

        # Oblicz composite score i wybierz top N
        candidates = self._compute_composite_score(candidates)
        selected = candidates.head(self.max_positions)

        # Oblicz wagi
        positions = self._assign_weights(selected, previous_portfolio or set())

        result = PortfolioBuildResult(
            positions=positions,
            n_runs_used=effective_runs,
            n_candidates_evaluated=n_unique_tickers,
            weighting_used=self.weighting,
            config_snapshot={
                "min_history_runs": self.min_history_runs,
                "max_positions": self.max_positions,
                "weighting": self.weighting,
                "composite_weights": self.composite_weights,
            },
        )

        logger.info(result.summary())
        return result

    # ──────────────────────────────────────────────────────────
    # Pobieranie danych z DB
    # ──────────────────────────────────────────────────────────

    def _fetch_detailed_history(self, n_last_runs: int) -> pd.DataFrame:
        """
        Pobierz historię screening_results z N ostatnich runów.
        Zwraca DataFrame z kolumnami: ticker, score, rank, run_id, run_timestamp
        """
        try:
            return self.repository.get_screening_history(n_last_runs=n_last_runs)
        except AttributeError:
            # Fallback: użyj starszej metody get_ticker_appearances
            logger.debug("Używam fallback metody pobierania historii")
            appearances = self.repository.get_ticker_appearances(n_last_runs=n_last_runs)
            if appearances.empty:
                return pd.DataFrame()
            # Emuluj minimalny format
            appearances["run_id"] = 0
            appearances["run_timestamp"] = datetime.utcnow()
            return appearances.rename(columns={"avg_score": "score"})

    # ──────────────────────────────────────────────────────────
    # Obliczanie metryk historycznych
    # ──────────────────────────────────────────────────────────

    def _compute_historical_metrics(
        self, history_df: pd.DataFrame, n_total_runs: int
    ) -> pd.DataFrame:
        """
        Dla każdego tickera oblicz pełen zestaw metryk historycznych.

        Kolumny wynikowe:
          ticker, n_appearances, appearance_rate, avg_score, std_score,
          score_consistency, avg_rank, trend_score
        """
        rows = []

        for ticker, grp in history_df.groupby("ticker"):
            grp = grp.sort_values("run_timestamp") if "run_timestamp" in grp.columns else grp
            scores = grp["score"].dropna().values
            ranks = grp["rank"].dropna().values if "rank" in grp.columns else np.array([])

            n = len(grp)
            appearance_rate = n / n_total_runs

            avg_score = float(np.mean(scores)) if len(scores) > 0 else 0.0
            std_score  = float(np.std(scores))  if len(scores) > 1 else 0.0

            # Consistency: 1 = idealna stabilność, 0 = bardzo zmienny score
            # Normalizujemy przez zakres możliwych wartości
            score_range = float(np.max(scores) - np.min(scores)) if len(scores) > 1 else 0.0
            score_consistency = 1.0 - (score_range / (abs(avg_score) + 1e-6))
            score_consistency = float(np.clip(score_consistency, 0.0, 1.0))

            avg_rank = float(np.mean(ranks)) if len(ranks) > 0 else 999.0

            # Trend score: slope regresji liniowej score w czasie
            # Dodatni = score rośnie = spółka coraz lepsza
            trend_score = 0.0
            if len(scores) >= 3:
                x = np.arange(len(scores), dtype=float)
                # Prosta regresja liniowa (slope)
                x_mean = x.mean()
                y_mean = scores.mean()
                num = np.sum((x - x_mean) * (scores - y_mean))
                den = np.sum((x - x_mean) ** 2)
                trend_score = float(num / den) if den > 0 else 0.0

            rows.append({
                "ticker":            ticker,
                "n_appearances":     n,
                "appearance_rate":   round(appearance_rate, 4),
                "avg_score":         round(avg_score, 6),
                "std_score":         round(std_score, 6),
                "score_consistency": round(score_consistency, 4),
                "avg_rank":          round(avg_rank, 2),
                "trend_score":       round(trend_score, 6),
            })

        df = pd.DataFrame(rows)
        if df.empty:
            return df
        return df.sort_values("appearance_rate", ascending=False).reset_index(drop=True)

    # ──────────────────────────────────────────────────────────
    # Composite score
    # ──────────────────────────────────────────────────────────

    def _compute_composite_score(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Oblicz composite_score jako ważoną sumę znormalizowanych metryk.

        Metoda normalizacji: min-max → [0, 1] per kolumna.
        avg_rank_inv = 1 / (avg_rank + 1) — wyższy rank = wyższe avg_rank_inv.
        """
        df = df.copy()

        # Przygotuj avg_rank_inv
        df["avg_rank_inv"] = 1.0 / (df["avg_rank"] + 1.0)

        component_cols = {
            "appearance_rate":   "appearance_rate",
            "avg_score":         "avg_score",
            "score_consistency": "score_consistency",
            "trend_score":       "trend_score",
            "avg_rank_inv":      "avg_rank_inv",
        }

        # Normalizacja min-max każdej składowej
        normalized: dict[str, pd.Series] = {}
        for key, col in component_cols.items():
            if col not in df.columns:
                continue
            s = df[col].fillna(0.0)
            mn, mx = s.min(), s.max()
            if mx > mn:
                normalized[key] = (s - mn) / (mx - mn)
            else:
                normalized[key] = pd.Series(0.5, index=df.index)

        # Ważona suma
        composite = pd.Series(0.0, index=df.index)
        for key, norm_series in normalized.items():
            w = self.composite_weights.get(key, 0.0)
            composite += norm_series * w

        df["composite_score"] = composite.round(6)
        return df.sort_values("composite_score", ascending=False).reset_index(drop=True)

    # ──────────────────────────────────────────────────────────
    # Ważenie portfela
    # ──────────────────────────────────────────────────────────

    def _assign_weights(
        self, selected: pd.DataFrame, previous_portfolio: set[str]
    ) -> list[PortfolioPosition]:
        """Przypisz wagi i utwórz obiekty PortfolioPosition."""
        n = len(selected)
        if n == 0:
            return []

        scores = selected["composite_score"].values.astype(float)

        if self.weighting == "equal":
            raw_weights = np.ones(n)

        elif self.weighting == "score_weighted":
            # Przesuń do >0 i dodaj bazę 50% równej wagi
            shifted = scores - scores.min() + 1e-6
            base = 0.5 / n
            raw_weights = shifted + base * shifted.sum()

        elif self.weighting == "rank_weighted":
            raw_weights = np.array([1.0 / (i + 1) for i in range(n)])

        else:
            logger.warning(f"Nieznana strategia ważenia '{self.weighting}'. Używam equal.")
            raw_weights = np.ones(n)

        total = raw_weights.sum()
        weights = raw_weights / total if total > 0 else np.ones(n) / n

        positions = []
        for i, (_, row) in enumerate(selected.iterrows()):
            positions.append(PortfolioPosition(
                ticker=str(row["ticker"]),
                weight=round(float(weights[i]), 6),
                composite_score=round(float(row["composite_score"]), 6),
                rank=i + 1,
                appearance_rate=round(float(row["appearance_rate"]), 4),
                avg_score=round(float(row["avg_score"]), 6),
                score_consistency=round(float(row["score_consistency"]), 4),
                avg_rank=round(float(row["avg_rank"]), 2),
                trend_score=round(float(row["trend_score"]), 6),
                n_appearances=int(row["n_appearances"]),
                is_new_entry=str(row["ticker"]) not in previous_portfolio,
            ))

        return positions

    # ──────────────────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────────────────

    def to_dict_list(self, positions: list[PortfolioPosition]) -> list[dict]:
        return [p.to_dict() for p in positions]

    @staticmethod
    def print_portfolio_report(positions: list[PortfolioPosition]) -> str:
        """Zwróć sformatowany raport tekstowy portfela."""
        if not positions:
            return "Brak pozycji w portfelu."

        lines = [
            "═" * 85,
            f"{'PORTFEL HISTORYCZNY':^85}",
            "═" * 85,
            f"{'#':<4} {'Ticker':<10} {'Waga':>7} {'CompScore':>10} "
            f"{'Freq':>6} {'AvgScore':>9} {'Stab':>6} {'Trend':>8} {'Nowy?':>6}",
            "─" * 85,
        ]
        for p in positions:
            new_flag = "✓" if p.is_new_entry else ""
            trend_str = f"+{p.trend_score:.3f}" if p.trend_score >= 0 else f"{p.trend_score:.3f}"
            lines.append(
                f"{p.rank:<4} {p.ticker:<10} {p.weight:>6.1%} "
                f"{p.composite_score:>10.4f} {p.appearance_rate:>5.0%} "
                f"{p.avg_score:>9.4f} {p.score_consistency:>5.0%} "
                f"{trend_str:>8} {new_flag:>6}"
            )
        lines.append("─" * 85)
        total_w = sum(p.weight for p in positions)
        lines.append(f"{'Suma wag:':>50} {total_w:>6.1%}")
        lines.append("═" * 85)
        return "\n".join(lines)
