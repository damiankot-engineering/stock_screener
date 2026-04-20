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
  │       ├─ yfinance:     P/E, ROE, RSI, momentum…                  │
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
| **World Bank** (PKB EM) | **Darmowy** | brak | — |
| **SEC EDGAR** (insiderzy) | **Darmowy** | brak | — |
| Anthropic (LLM) | Płatny | `ANTHROPIC_API_KEY` | https://console.anthropic.com |
| OpenAI (LLM) | Płatny | `OPENAI_API_KEY` | https://platform.openai.com |

```bash
export GROQ_API_KEY=gsk_...         # wymagany
export FRED_API_KEY=...             # opcjonalny (bez niego: proxy z Yahoo Finance)
export ALPHA_VANTAGE_KEY=...        # opcjonalny (bez niego: darmowe RSS feeds)
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
│   ├── user_config.yaml             ← EDYTUJ: strategia, filtry, enrichment
│   └── settings.py
├── data/
│   ├── ai_ticker_source.py          LLM backends + 8 strategii inwestycyjnych
│   ├── ticker_source.py             Router → AITickerSource
│   ├── ticker_validator.py          Walidacja yfinance + DB cache
│   ├── fetcher.py                   Yahoo Finance (fundamenty + techniczne)
│   ├── enriched_fetcher.py          ★ Orchestrator zewnętrznych źródeł
│   ├── macro_data.py                ★ FRED + World Bank (makro, PKB EM)
│   ├── insider_data.py              ★ SEC EDGAR Form 4 (transakcje insiderów)
│   └── news_sentiment.py            ★ RSS / Alpha Vantage (sentyment)
├── db/
│   ├── models.py                    7 tabel SQLite
│   └── repository.py
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
└── insider_signal_cache    ★ sygnały insiderów SEC EDGAR
```

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
# Rynki wschodzące z kontekstem makro (FRED + World Bank)
python main.py --strategy emerging_growth --n 60 --multi-shot

# Okazje z dużym upside przy wyważonym ryzyku
python main.py --strategy asymmetric_risk

# Tematyczne z kontekstem makro
python main.py --strategy thematic --theme "India digital economy"
python main.py --strategy thematic --theme "defense and cybersecurity"
python main.py --strategy thematic --theme "longevity and biotech"
```

---

## Zewnętrzne źródła danych

### FRED + World Bank (makro)

Pobierane raz na run, wstrzykiwane do:
- **Promptu AI** — model dobiera spółki świadomie środowiska makro
- **Danych screenera** — `macro_regime_score`, `vix`, `yield_curve_10y2y`, `em_gdp_XX`

Działa bez klucza FRED (Yahoo Finance jako proxy dla VIX i krzywej).

### SEC EDGAR Form 4 (insiderzy)

Tylko spółki USA. Nowe metryki:
- `insider_buy_ratio` — % transakcji insiderów = kupno (0–1, >0.7 = silny sygnał)
- `insider_net_shares` — netto akcji kupionych przez insiderów (90 dni)

Bez klucza API. Wymaga połączenia z `www.sec.gov`.

### RSS / Alpha Vantage (sentyment)

Domyślnie darmowe RSS feeds (Yahoo Finance). Z kluczem Alpha Vantage: per-ticker
sentiment score z 25 req/dzień. Nowa metryka:
- `news_sentiment` — od -1.0 (bardzo negatywny) do +1.0 (bardzo pozytywny)

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
python test_ai_source.py     # testy AI source, walidatora, pipeline
python test_core_logic.py    # testy filtrów, scoringu, portfela
```

---

## Planowany dalszy rozwój

- **Backtesting** — symulacja zwrotów portfela na danych historycznych yfinance
- **Feedback loop wyników** — wyniki backtestingu wracają do promptu AI
- **Dashboard webowy** — Flask + Plotly, historia portfela i scorów
- **Multi-strategia z konsensusem** — portfel ze spółek powtarzających się w ≥2 strategiach
- **Alerty** — email/Slack po każdym runie (nowe pozycje, wypadnięte spółki)
- **Eksport do brokera** — Alpaca/IBKR paper trading API

---

## Disclaimer

System służy wyłącznie celom edukacyjnym i informacyjnym. Nie stanowi doradztwa
inwestycyjnego. Rekomendacje AI mogą być niedokładne. Dane Yahoo Finance i zewnętrzne
źródła mogą być opóźnione lub niekompletne. Zawsze przeprowadź własne due diligence.
