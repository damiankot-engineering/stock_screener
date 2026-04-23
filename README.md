# 📊 Stock Screener – AI-Powered Global Equity Research System

Kompletny system do automatycznej selekcji spółek z całego świata, z naciskiem
na rynki wschodzące i okazje inwestycyjne o asymetrycznym profilu ryzyko/zwrot.

---

## Jak to działa

```
  ┌──────────────────────────────────────────────────────────────────┐
  │  TRYB 1: python main.py  (screening)                             │
  │                                                                  │
  │  [1]  AI Ticker Source                                           │
  │       Prompt strategiczny → ~50 tickerów globalnie               │
  │       Kontekst makro (VIX, krzywa, PKB EM) wstrzyknięty do AI   │
  │       Feedback loop: znane złe tickery blokowane w prompcie      │
  │                                                                  │
  │  [1b] Walidacja yfinance                                         │
  │       yf.Ticker(t).fast_info równolegle → odrzuć niedostępne     │
  │       Cache 30 dni w DB (nie sprawdzaj dwukrotnie)               │
  │                                                                  │
  │  [2]  EnrichedFetcher (Yahoo Finance + zewnętrzne źródła)        │
  │       ├─ yfinance/FMP/Stooq: P/E, ROE, RSI, momentum…           │
  │       ├─ FRED/World Bank: VIX, krzywa rentowności, PKB EM        │
  │       ├─ SEC EDGAR:    transakcje insiderów (Form 4)             │
  │       └─ RSS/Alpha Vantage: sentyment wiadomości                 │
  │                                                                  │
  │  [3–7] Filter → Score → Zapisz do DB (inkrementacyjnie)          │
  └──────────────────────────────────────────────────────────────────┘

  ┌──────────────────────────────────────────────────────────────────┐
  │  TRYB 2: python main.py --build-portfolio  (po ≥3 runach)        │
  │                                                                  │
  │  Analiza historii DB → composite score per ticker:               │
  │    appearance_rate, avg_score, score_consistency,                │
  │    trend_score, avg_rank                                         │
  │  Top N pozycji → wagi → CSV + DB                                 │
  └──────────────────────────────────────────────────────────────────┘

  ┌──────────────────────────────────────────────────────────────────┐
  │  TRYB 3: python main.py --backtest  (po ≥1 build-portfolio)      │
  │                                                                  │
  │  Symulacja historyczna portfela:                                 │
  │    [1] Wczytaj historię buildów z DB                             │
  │    [2] Pobierz ceny historyczne (yfinance)                       │
  │    [3] Symuluj rebalansowanie przy każdym buildzie               │
  │    [4] Oblicz metryki vs benchmark (SPY)                         │
  │    [5] Zapisz wyniki do DB + CSV                                 │
  └──────────────────────────────────────────────────────────────────┘
```

---

## Instalacja

```bash
pip install -r requirements.txt
```

---

## Klucze API

| Źródło | Koszt | Klucz | Rejestracja |
|--------|-------|-------|-------------|
| **Groq** (LLM) | **Darmowy** | `GROQ_API_KEY` | https://console.groq.com |
| **FRED** (makro) | **Darmowy** | `FRED_API_KEY` | https://fred.stlouisfed.org/docs/api/api_key.html |
| **Alpha Vantage** (sentiment) | **Darmowy** | `ALPHA_VANTAGE_KEY` | https://www.alphavantage.co/support/#api-key |
| **FMP** (fundamenty) | **Darmowy** | `FMP_API_KEY` | https://financialmodelingprep.com/register |
| **World Bank** (PKB EM) | **Darmowy** | brak | — |
| **SEC EDGAR** (insiderzy) | **Darmowy** | brak | — |
| Anthropic (LLM) | Płatny | `ANTHROPIC_API_KEY` | https://console.anthropic.com |
| OpenAI (LLM) | Płatny | `OPENAI_API_KEY` | https://platform.openai.com |

```bash
export GROQ_API_KEY=gsk_...         # wymagany
export FRED_API_KEY=...             # opcjonalny (bez niego: proxy z Yahoo Finance)
export ALPHA_VANTAGE_KEY=...        # opcjonalny (bez niego: darmowe RSS feeds)
export FMP_API_KEY=...              # opcjonalny (bez niego: fallback na yfinance)
```

---

## Użycie

```bash
# Screening – zbieranie danych do historii
python main.py                                        # growth_quality, 50 spółek
python main.py --strategy emerging_growth             # ★ rynki wschodzące, wysoki potencjał
python main.py --strategy asymmetric_risk             # ★ okazje z asymetrycznym R/R
python main.py --strategy deep_value
python main.py --strategy compounders
python main.py --strategy sector_leaders --sector healthcare
python main.py --strategy thematic --theme "clean energy"
python main.py --strategy global_diversified
python main.py --n 80 --multi-shot                    # ~240 spółek z 3 zapytań AI
python main.py --backend mock                         # test bez API key

# Portfel (dopiero po ≥3 runach)
python main.py --build-portfolio
python main.py --build-portfolio --runs 10

# Backtest (po ≥1 build-portfolio)
python main.py --backtest                             # SPY benchmark, 100k kapitału
python main.py --backtest --benchmark QQQ             # inny benchmark
python main.py --backtest --capital 50000             # inny kapitał startowy
python main.py --backtest --tx-cost 0                 # bez kosztów transakcji
python main.py --backtest --lookback 730              # inny zakres czasowy

# Analiza i harmonogram
python main.py --analyze
python main.py --schedule
```

---

## Architektura

```
stock_screener/
├── main.py                          Orchestrator + CLI
├── config/
│   ├── user_config.yaml             ← EDYTUJ: strategia, filtry, enrichment, backtest
│   └── settings.py
├── data/
│   ├── ai_ticker_source.py          LLM backends + 8 strategii inwestycyjnych
│   ├── ticker_source.py             Router → AITickerSource
│   ├── ticker_validator.py          Walidacja yfinance + DB cache
│   ├── fetcher.py                   Orchestrator dostawców danych
│   ├── enriched_fetcher.py          ★ Orchestrator zewnętrznych źródeł
│   ├── macro_data.py                ★ FRED + World Bank (makro, PKB EM)
│   ├── insider_data.py              ★ SEC EDGAR Form 4 (transakcje insiderów)
│   ├── news_sentiment.py            ★ RSS / Alpha Vantage (sentyment)
│   └── providers/
│       ├── base.py                  Interfejs DataProvider
│       ├── composite.py             FMP → Stooq → yFinance fallback chain
│       ├── fmp.py                   Financial Modeling Prep
│       ├── stooq.py                 Stooq (ceny historyczne, darmowy)
│       └── yfinance_provider.py     Yahoo Finance (fallback)
├── backtesting/                     ★ NOWY MODUŁ
│   ├── engine.py                    Silnik symulacji (rebalansowanie, NAV)
│   ├── metrics.py                   CAGR, Sharpe, Sortino, max DD, alpha/beta…
│   └── report.py                    Rich console output + CSV export
├── db/
│   ├── models.py                    8 tabel SQLite (+ BacktestRun)
│   └── repository.py                + get_portfolio_builds_history, save_backtest_run
├── screening/
│   ├── filter_engine.py
│   └── scorer.py
├── portfolio/
│   └── builder.py                   Portfel wyłącznie z historii DB
├── reports/
│   └── reporter.py
└── scheduler/
    └── runner.py
```

---

## Schemat bazy danych

```
screening_runs              metadane uruchomień
├── metric_snapshots        metryki yfinance + zewnętrzne (EAV)
├── screening_results       wyniki filtrowania i scoringu
├── portfolio_snapshots     składy portfela historycznego
├── ticker_validation_cache walidacja yfinance (TTL 30 dni)
├── macro_snapshots         ★ dane FRED/World Bank per run
├── insider_signal_cache    ★ sygnały insiderów SEC EDGAR
└── backtest_runs           ★ wyniki backtestingu (CAGR, Sharpe, MaxDD…)
```

---

## Backtesting

Moduł backtestingu symuluje co by się stało, gdybyś inwestował zgodnie
z historycznymi sygnałami portfela zgromadzonymi w bazie.

### Jak działa

1. **Pobiera historię buildów** z tabeli `portfolio_snapshots` (każdy `--build-portfolio` = jeden punkt rebalansowania)
2. **Pobiera ceny historyczne** dla wszystkich tickerów z yfinance
3. **Symuluje inwestycję**: kupuje portfel przy pierwszym buildzie, rebalansuje przy każdym kolejnym
4. **Oblicza metryki** vs benchmark (domyślnie SPY)
5. **Zapisuje wyniki** do `backtest_runs` + pliki CSV w `reports/`

### Metryki

| Metryka | Opis |
|---------|------|
| Total Return | Łączny zwrot za cały okres |
| CAGR | Roczna stopa wzrostu (Compound Annual Growth Rate) |
| Volatility | Roczna zmienność zwrotów |
| Sharpe Ratio | Zwrot ponad RF / odchylenie std |
| Sortino Ratio | Jak Sharpe, ale tylko downside volatility |
| Max Drawdown | Maksymalne obsunięcie od szczytu |
| Calmar Ratio | CAGR / Max Drawdown |
| Win Rate | % sesji z dodatnim zwrotem |
| Alpha | Zwrot ponad oczekiwany (CAPM) vs benchmark |
| Beta | Wrażliwość na ruchy benchmarku |
| Max DD Duration | Najdłuższy czas w obsunięciu (dni) |

### Przykładowe wyniki (output konsoli)

```
┌─ WYNIKI BACKTESTINGU ──────────────────────────────────────┐
│ Buildów portfela: 8   Okres: 2023-01-03 → 2024-12-31       │
│ Benchmark: SPY                                              │
└─────────────────────────────────────────────────────────────┘

  Metryka              Portfel       SPY       Przewaga
  ─────────────────────────────────────────────────────
  Total Return         +34.2%      +21.1%      +13.1pp
  CAGR                 +15.8%      +10.1%       +5.7pp
  Volatility (ann.)    +18.2%      +14.3%       -3.9pp
  Sharpe Ratio          0.8234      0.6541      +0.169
  Sortino Ratio         0.5871      0.9241      -0.337
  Max Drawdown         -12.4%      -10.2%       -2.2pp
  Calmar Ratio          1.2742      0.9902      +0.284
  Win Rate             +53.2%      +54.1%       -0.9pp
  Alpha                +0.0412        —            —
  Beta                  1.1200        —            —
```

### Pliki wyjściowe CSV

Po każdym `--backtest` w katalogu `reports/` tworzonych jest do 4 plików:

| Plik | Zawartość |
|------|-----------|
| `backtest_nav_YYYYMMDD_HHMMSS.csv` | Dzienna wartość portfela + benchmarku |
| `backtest_metrics_YYYYMMDD_HHMMSS.csv` | Wszystkie metryki (portfel vs benchmark) |
| `backtest_monthly_YYYYMMDD_HHMMSS.csv` | Miesięczne zwroty (rok, miesiąc, zwrot) |
| `backtest_rebalance_YYYYMMDD_HHMMSS.csv` | Log rebalansowań z kosztami transakcji |

### Konfiguracja

```yaml
backtesting:
  benchmark_ticker: "SPY"       # SPY, QQQ, ^GSPC, ^STOXX50E, …
  initial_capital: 100000.0     # kapitał startowy w USD
  transaction_cost_bps: 10.0    # 10 bps = 0.1% per strona transakcji
```

### Ograniczenia (świadome uproszczenia)

- Brak podatków
- Rebalansowanie po cenie zamknięcia dnia buildu
- Dywidendy wliczone (total return z yfinance)
- Brak modelu płynności (zakłada pełną realizację)

---

## Strategie AI

| Strategia | Opis | n |
|-----------|------|---|
| `growth_quality` | Wzrost + jakość, globalna dywersyfikacja | 50 |
| `deep_value` | Niedowartościowane z marżą bezpieczeństwa | 40 |
| `compounders` | Szeroki moat, ROIC >20%, długi horyzont | 30 |
| `sector_leaders` | Liderzy i challengers w wybranym sektorze | 40 |
| `thematic` | Ekspozycja na wybrany megatrend | 35 |
| `global_diversified` | Precyzyjna alokacja geograficzna i sektorowa | 60 |
| **`emerging_growth`** | **★ Indie, SEA, LatAm, EM champions — wysoki potencjał** | 45 |
| **`asymmetric_risk`** | **★ Okazje z asymetrycznym R/R (4 typy katalysatorów)** | 35 |

### Przykłady

```bash
python main.py --strategy emerging_growth --n 60 --multi-shot
python main.py --strategy asymmetric_risk
python main.py --strategy thematic --theme "India digital economy"
python main.py --strategy thematic --theme "defense and cybersecurity"
python main.py --strategy thematic --theme "longevity and biotech"
```

---

## Zewnętrzne źródła danych

### FRED + World Bank (makro)

Pobierane raz na run, wstrzykiwane do promptu AI i danych screenera:
`macro_regime_score`, `vix`, `yield_curve_10y2y`, `em_gdp_XX`

### SEC EDGAR Form 4 (insiderzy)

Tylko spółki USA.
- `insider_buy_ratio` — % transakcji insiderów = kupno (0–1, >0.7 = silny sygnał)
- `insider_net_shares` — netto akcji kupionych przez insiderów (90 dni)

### RSS / Alpha Vantage (sentyment)

`news_sentiment` — od -1.0 (bardzo negatywny) do +1.0 (bardzo pozytywny)

---

## Feedback Loop

System zapamiętuje tickery które nie przechodzą walidacji yfinance i wstrzykuje je
do promptu AI przy kolejnych runach — AI nie generuje ich ponownie.

```yaml
settings:
  feedback_loop_limit: 500    # ile niedziałających tickerów przechowywać w DB
                              # (max 200 trafia do prompta AI)
```

---

## Konfiguracja zewnętrznych źródeł

```yaml
enrichment:
  macro:
    enabled: true
    fred_api_key_env: "FRED_API_KEY"    # opcjonalny

  insider:
    enabled: true
    lookback_days: 90
    min_insider_buy_ratio: null          # null = brak filtra, 0.5 = min 50% kupna

  sentiment:
    enabled: true
    lookback_days: 30
    alpha_vantage_key_env: "ALPHA_VANTAGE_KEY"   # opcjonalny
```

### Nowe metryki dostępne w filtrach i scoringu

```yaml
filters:
  fundamental:
    insider_buy_ratio: [0.5, null]    # min 50% transakcji insiderów = kupno
    news_sentiment:    [0.0, null]    # neutralny lub pozytywny sentyment
    macro_regime_score: [0.4, null]   # min risk-neutral środowisko

scoring:
  weights:
    insider_buy_ratio:  1.5    # silny bonus za kupno insiderów
    news_sentiment:     0.8
    macro_regime_score: 0.5
```

---

## Testy

```bash
python test_ai_source.py      # testy AI source, walidatora, pipeline
python test_core_logic.py     # testy filtrów, scoringu, portfela
python test_backtesting.py    # ★ testy backtestingu (44 testów, bez sieci)
python test_providers.py      # testy dostawców danych
```

---

## Planowany dalszy rozwój

- **Dashboard webowy** — Streamlit, historia portfela i scorów, wykresy NAV
- **Feedback loop wyników** — wyniki backtestingu wracają do promptu AI
- **Alerty** — powiadomienia po każdym runie (nowe pozycje, wypadnięte spółki)
- **Eksport do brokera** — Alpaca paper trading API

---

## Disclaimer

System służy wyłącznie celom edukacyjnym i informacyjnym. Nie stanowi doradztwa
inwestycyjnego. Rekomendacje AI mogą być niedokładne. Dane Yahoo Finance i zewnętrzne
źródła mogą być opóźnione lub niekompletne. Zawsze przeprowadź własne due diligence.
