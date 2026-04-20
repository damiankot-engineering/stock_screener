# 📊 Stock Screener – AI-Powered Global Equity Research System

Kompletny, modularny system do automatycznej selekcji spółek giełdowych z całego świata.
Tickery generowane przez LLM → walidowane przez yfinance → filtrowane i scorowane →
portfel budowany z historii wielu uruchomień.

---

## Jak to działa (przepływ danych)

```
  ┌─────────────────────────────────────────────────────────────────┐
  │  TRYB 1: python main.py  (screening)                            │
  │                                                                 │
  │  [1]  AI (Groq/Claude/OpenAI)                                   │
  │       Prompt strategiczny → lista ~50 tickerów globalnie        │
  │       Feedback loop: znane złe tickery wstrzykiwane do promptu  │
  │                                                                 │
  │  [1b] Walidacja yfinance (NOWE)                                 │
  │       yf.Ticker(t).fast_info równolegle → odrzuć niedostępne    │
  │       Cache 30 dni w DB → nie sprawdzaj dwa razy tego samego    │
  │                                                                 │
  │  [2]  Data Fetcher                                              │
  │       Yahoo Finance: P/E, ROE, RSI, momentum, marże…            │
  │                                                                 │
  │  [3–7] Filter → Score → Zapisz do DB (inkrementacyjnie)         │
  └─────────────────────────────────────────────────────────────────┘

  ┌─────────────────────────────────────────────────────────────────┐
  │  TRYB 2: python main.py --build-portfolio  (po ≥3 runach)       │
  │                                                                 │
  │  Analiza historii DB → metryki per ticker:                      │
  │    appearance_rate, avg_score, score_consistency,               │
  │    trend_score, avg_rank                                        │
  │  Composite score → top N pozycji → wagi → CSV + DB             │
  └─────────────────────────────────────────────────────────────────┘
```

---

## Instalacja

```bash
pip install -r requirements.txt
```

### Darmowy klucz API (Groq)

1. Zarejestruj się na **https://console.groq.com** (bez karty kredytowej)
2. Wygeneruj klucz w zakładce *API Keys*
3. Ustaw zmienną środowiskową:

```bash
export GROQ_API_KEY=gsk_twój_klucz
```

---

## Użycie

```bash
# Screening (zbiera dane do historii DB)
python main.py                                        # growth_quality, 50 spółek
python main.py --strategy deep_value
python main.py --strategy compounders
python main.py --strategy sector_leaders --sector healthcare
python main.py --strategy thematic --theme "clean energy"
python main.py --strategy global_diversified
python main.py --n 80                                 # więcej tickerów
python main.py --multi-shot                           # 3× zapytania AI → ~150 spółek
python main.py --backend mock                         # test bez API key

# Portfel (dopiero po ≥3 uruchomieniach screenera)
python main.py --build-portfolio
python main.py --build-portfolio --runs 10            # użyj ostatnich 10 runów

# Analiza i harmonogram
python main.py --analyze                              # historia uruchomień
python main.py --schedule                             # wg user_config.yaml
```

---

## Architektura

```
stock_screener/
├── main.py                          Orchestrator + CLI
│
├── config/
│   ├── user_config.yaml             ← EDYTUJ: strategia, filtry, backend
│   └── settings.py                  Ładowanie i walidacja konfiguracji
│
├── data/
│   ├── ai_ticker_source.py          LLM backends (Groq/Anthropic/OpenAI/Mock)
│   │                                PromptLibrary – 6 strategii inwestycyjnych
│   │                                Feedback loop (avoid_tickers w prompcie)
│   ├── ticker_source.py             Router → AITickerSource
│   ├── ticker_validator.py          ★ Walidacja yfinance + cache DB
│   └── fetcher.py                   Yahoo Finance (równoległe, retry)
│
├── db/
│   ├── models.py                    5 tabel SQLite (EAV, cache, historia)
│   └── repository.py                Repository Pattern (wszystkie zapytania DB)
│
├── screening/
│   ├── filter_engine.py             Filtry AND z progami użytkownika
│   └── scorer.py                    Min-max normalizacja + wagi → ranking
│
├── portfolio/
│   └── builder.py                   build_from_history() – portfel z historii DB
│
├── reports/
│   └── reporter.py                  Wyjście konsolowe (Rich lub plain) + CSV
│
├── scheduler/
│   └── runner.py                    APScheduler (daily/weekly/monthly)
│
├── test_ai_source.py                Testy (bez API key, bez internetu)
└── test_core_logic.py               Testy logiki wewnętrznej
```

---

## Schemat bazy danych

```
screening_runs              metadane każdego uruchomienia
├── metric_snapshots        wartości metryk (EAV: ticker × run × metric = value)
├── screening_results       wyniki filtrowania i scoringu
├── portfolio_snapshots     składy portfela historycznego
└── ticker_validation_cache ★ cache walidacji yfinance (TTL 30 dni)
                              + feedback loop dla AI (lista złych tickerów)
```

Dane są **tylko dopisywane, nigdy nadpisywane** (z wyjątkiem cache walidacji,
który jest z definicji mutowalny).

---

## Konfiguracja (user_config.yaml)

### Strategia i backend AI
```yaml
source:
  strategy: "growth_quality"     # growth_quality | deep_value | compounders
                                 # sector_leaders | thematic | global_diversified
  ai:
    backend: "groq"              # groq (darmowy) | anthropic | openai | mock
    api_key_env: "GROQ_API_KEY"
    model: "llama-3.3-70b-versatile"
    n_tickers: 50
    temperature: 0.35            # 0.2=deterministyczny, 0.8=różnorodny
    sector: "healthcare"         # dla sector_leaders
    theme: "clean energy"        # dla thematic
    multi_shot: false
    multi_shot_runs: 3
```

### Filtry
```yaml
filters:
  fundamental:
    pe_ratio:       [0, 60]      # [min, max], null = bez ograniczenia
    roe:            [10, null]
    debt_to_equity: [null, 2.0]
    market_cap:     [5e8, null]  # min $500M
  technical:
    rsi_14:         [25, 80]
    volume_ratio:   [0.3, null]
```

### Scoring
```yaml
scoring:
  weights:
    roe:            2.0          # wyższe = ważniejsze w rankingu
    revenue_growth: 1.8
    pe_ratio:      -0.4          # ujemna waga = kara
    debt_to_equity: -0.8
```

### Portfel historyczny
```yaml
portfolio:
  max_positions: 20
  min_history_runs: 3            # ile runów potrzeba przed build-portfolio
  weighting: "score_weighted"    # equal | score_weighted | rank_weighted
  stability_bonus_weight: 0.5
  composite_weights:             # wagi składowych composite_score
    appearance_rate:   3.0       # jak często ticker przechodzi filtry
    avg_score:         2.0
    score_consistency: 1.5
    trend_score:       1.0
    avg_rank_inv:      0.5
```

### Walidacja tickerów
```yaml
settings:
  validation_cache_ttl_days: 30  # jak długo cache jest świeży
  fetch_workers: 10              # równoległe sprawdzenia yfinance
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

### Przykłady tematyczne
```bash
python main.py --strategy thematic --theme "artificial intelligence"
python main.py --strategy thematic --theme "quantum computing"
python main.py --strategy thematic --theme "defense and cybersecurity"
python main.py --strategy thematic --theme "longevity and biotech"
python main.py --strategy thematic --theme "emerging market consumer"
```

---

## Backendy LLM

| Backend | Koszt | Model domyślny | Klucz |
|---------|-------|----------------|-------|
| **Groq** | **Darmowy** | `llama-3.3-70b-versatile` | `GROQ_API_KEY` |
| Anthropic | Płatny | `claude-sonnet-4-6` | `ANTHROPIC_API_KEY` |
| OpenAI | Płatny | `gpt-4o-mini` | `OPENAI_API_KEY` |
| Mock | Brak | — | Brak |

### Własny backend
```python
from data.ai_ticker_source import BackendFactory, LLMBackend

class MyLLM(LLMBackend):
    @property
    def name(self): return "MyLLM/v1"
    def call(self, system, user, temperature):
        # twoja implementacja HTTP
        return '{"tickers": ["AAPL", "MSFT"]}'

BackendFactory.register("my_llm", MyLLM)
```

---

## Pliki wynikowe

Po każdym uruchomieniu w katalogu `reports/`:
```
reports/
├── screening_ai_growth_quality_20250420_070215_run3.csv
└── portfolio_portfolio_growth_quality_20250420_073012_run0.csv
```

---

## Testy

```bash
python test_ai_source.py     # testy AI source, walidatora, pipeline (bez API key)
python test_core_logic.py    # testy filtrów, scoringu, portfela
```

---

## Planowany dalszy rozwój

- **Backtesting** — symulacja zwrotów portfela na danych historycznych yfinance
- **Feedback loop wyników** — wyniki backtestingu wracają do promptu AI
- **Dashboard webowy** — Flask + Plotly, podgląd historii portfela
- **Multi-strategia z konsensusem** — portfel tylko ze spółek powtarzających się w ≥2 strategiach
- **Alerty** — email/Slack po każdym runie (nowe pozycje, wypadnięte spółki)
- **Dane uzupełniające** — SEC EDGAR (insider transactions), FRED (makro)
- **Eksport do brokera** — Alpaca/IBKR paper trading API

---

## Disclaimer

System służy wyłącznie celom edukacyjnym i informacyjnym. Nie stanowi doradztwa
inwestycyjnego. Rekomendacje AI mogą być niedokładne. Dane Yahoo Finance mogą być
opóźnione. Zawsze przeprowadź własne due diligence przed podjęciem decyzji inwestycyjnych.
