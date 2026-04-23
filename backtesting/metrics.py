"""
backtesting/metrics.py
Obliczanie metryk wydajności portfela.

Obsługiwane metryki:
  - Total Return          – łączny zwrot za cały okres
  - CAGR                  – roczna stopa wzrostu (Compound Annual Growth Rate)
  - Sharpe Ratio          – zwrot ponad stopę wolną od ryzyka / odchylenie std
  - Sortino Ratio         – jak Sharpe, ale tylko downside volatility
  - Max Drawdown          – maksymalne obsunięcie od szczytu
  - Calmar Ratio          – CAGR / Max Drawdown
  - Win Rate              – % miesięcy z dodatnim zwrotem
  - Volatility (ann.)     – roczna zmienność zwrotów
  - Alpha / Beta          – względem benchmarku (np. SPY)
  - Best/Worst Month      – najlepszy i najgorszy miesięczny zwrot
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def compute_metrics(
    portfolio_values: pd.Series,
    benchmark_values: pd.Series | None = None,
    risk_free_rate: float = 0.04,
    periods_per_year: int = 252,
) -> dict:
    """
    Oblicz pełny zestaw metryk wydajności dla szeregu wartości portfela.

    Args:
        portfolio_values: Series z wartościami portfela indeksowana datami
        benchmark_values: opcjonalny Series z wartościami benchmarku (te same daty)
        risk_free_rate:   roczna stopa wolna od ryzyka (domyślnie 4%)
        periods_per_year: liczba okresów w roku (252 = dziennie, 12 = miesięcznie)

    Returns:
        dict z metrykami (wszystkie wartości to floaty lub None)
    """
    if portfolio_values is None or len(portfolio_values) < 2:
        return _empty_metrics()

    pv = portfolio_values.dropna()
    if len(pv) < 2:
        return _empty_metrics()

    returns = pv.pct_change().dropna()

    # ── Podstawowe zwroty ────────────────────────────────────
    total_return = float(pv.iloc[-1] / pv.iloc[0] - 1)

    n_years = len(pv) / periods_per_year
    cagr = float((pv.iloc[-1] / pv.iloc[0]) ** (1 / n_years) - 1) if n_years > 0 else 0.0

    # ── Zmienność ────────────────────────────────────────────
    volatility = float(returns.std() * np.sqrt(periods_per_year))

    # ── Sharpe ───────────────────────────────────────────────
    daily_rf = risk_free_rate / periods_per_year
    excess = returns - daily_rf
    sharpe = float(excess.mean() / excess.std() * np.sqrt(periods_per_year)) \
        if excess.std() > 0 else 0.0

    # ── Sortino ──────────────────────────────────────────────
    downside = returns[returns < daily_rf] - daily_rf
    downside_std = float(np.sqrt((downside ** 2).mean()) * np.sqrt(periods_per_year)) \
        if len(downside) > 0 else 0.0
    sortino = float((cagr - risk_free_rate) / downside_std) if downside_std > 0 else 0.0

    # ── Max Drawdown ─────────────────────────────────────────
    rolling_max = pv.cummax()
    drawdown = (pv - rolling_max) / rolling_max
    max_drawdown = float(drawdown.min())

    # ── Calmar ───────────────────────────────────────────────
    calmar = float(cagr / abs(max_drawdown)) if max_drawdown < 0 else 0.0

    # ── Win Rate ─────────────────────────────────────────────
    win_rate = float((returns > 0).mean())

    # ── Best / Worst period ──────────────────────────────────
    best_period  = float(returns.max())
    worst_period = float(returns.min())

    # ── Alpha / Beta vs benchmark ────────────────────────────
    alpha, beta = None, None
    if benchmark_values is not None and len(benchmark_values) >= 2:
        bv = benchmark_values.dropna()
        # Wyrównaj indeksy
        common = pv.index.intersection(bv.index)
        if len(common) >= 2:
            p_ret = pv.loc[common].pct_change().dropna()
            b_ret = bv.loc[common].pct_change().dropna()
            common_ret = p_ret.index.intersection(b_ret.index)
            if len(common_ret) >= 2:
                p_aligned = p_ret.loc[common_ret].values
                b_aligned = b_ret.loc[common_ret].values
                cov_matrix = np.cov(p_aligned, b_aligned)
                beta = float(cov_matrix[0, 1] / cov_matrix[1, 1]) \
                    if cov_matrix[1, 1] > 0 else None
                if beta is not None:
                    b_cagr = float(
                        (bv.iloc[-1] / bv.iloc[0]) ** (1 / n_years) - 1
                    ) if n_years > 0 else 0.0
                    alpha = float(cagr - (risk_free_rate + beta * (b_cagr - risk_free_rate)))

    # ── Drawdown Duration ────────────────────────────────────
    max_dd_duration_days = _max_drawdown_duration(pv)

    return {
        "total_return":         round(total_return, 6),
        "cagr":                 round(cagr, 6),
        "volatility_ann":       round(volatility, 6),
        "sharpe_ratio":         round(sharpe, 4),
        "sortino_ratio":        round(sortino, 4),
        "max_drawdown":         round(max_drawdown, 6),
        "calmar_ratio":         round(calmar, 4),
        "win_rate":             round(win_rate, 4),
        "best_period_return":   round(best_period, 6),
        "worst_period_return":  round(worst_period, 6),
        "alpha":                round(alpha, 6) if alpha is not None else None,
        "beta":                 round(beta, 4)  if beta  is not None else None,
        "max_dd_duration_days": max_dd_duration_days,
        "n_periods":            len(returns),
        "start_value":          round(float(pv.iloc[0]), 4),
        "end_value":            round(float(pv.iloc[-1]), 4),
    }


def compute_monthly_returns(portfolio_values: pd.Series) -> pd.DataFrame:
    """
    Oblicz miesięczne zwroty portfela.
    Zwraca DataFrame z kolumnami: year, month, return.
    """
    if portfolio_values is None or len(portfolio_values) < 2:
        return pd.DataFrame(columns=["year", "month", "return"])

    monthly = portfolio_values.resample("ME").last()
    ret = monthly.pct_change().dropna()

    df = pd.DataFrame({
        "year":   ret.index.year,
        "month":  ret.index.month,
        "return": ret.values,
    })
    return df


def _max_drawdown_duration(pv: pd.Series) -> int:
    """Oblicz maksymalną liczbę dni trwania obsunięcia."""
    rolling_max = pv.cummax()
    in_drawdown = pv < rolling_max

    max_duration = 0
    current_duration = 0
    for v in in_drawdown:
        if v:
            current_duration += 1
            max_duration = max(max_duration, current_duration)
        else:
            current_duration = 0
    return max_duration


def _empty_metrics() -> dict:
    return {
        "total_return": None, "cagr": None, "volatility_ann": None,
        "sharpe_ratio": None, "sortino_ratio": None, "max_drawdown": None,
        "calmar_ratio": None, "win_rate": None,
        "best_period_return": None, "worst_period_return": None,
        "alpha": None, "beta": None, "max_dd_duration_days": None,
        "n_periods": 0, "start_value": None, "end_value": None,
    }
