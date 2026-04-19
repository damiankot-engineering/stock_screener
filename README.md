# 📊 Stock Screener – AI-Powered Global Equity Research System

Kompletny, modularny system do automatycznej selekcji spółek giełdowych z całego świata.
Listy tickerów generowane są przez modele językowe (LLM) na podstawie precyzyjnych,
wielokryterialnych promptów strategicznych. Dane finansowe z Yahoo Finance (darmowe).

---

## 🆕 AI Ticker Source – jak to działa?

Zamiast pobierać statyczne indeksy (S&P500, WIG20), system **pyta model językowy**:

```
[Strategia: growth_quality, n=50]

"You are a senior equity research analyst with 20+ years of experience...
 Identify exactly 50 publicly traded companies combining quality fundamentals
 with growth potential. [ROIC >15%, moat, rev.growth >10%, diversity criteria]
 Return ONLY a valid JSON array of ticker symbols."

→ ["AAPL", "MSFT", "ASML.AS", "NOVO-B.CO", "HDFC.NS", "6098.T", ...]
```

Efekt: każde uruchomienie bada **inną, globalną grupę spółek** — AI wie o firmach,
o których statyczne indeksy nie wiedzą.

---

## 🏗️ Architektura

```
stock_screener/
├── main.py                         ← Orchestrator pipeline + CLI
│
├── config/
│   ├── user_config.yaml            ← EDYTUJ: strategia, progi, backend
│   └── settings.py                 ← Ładowanie i walidacja konfiguracji
│
├── data/
│   ├── ai_ticker_source.py         ← ★ NOWY: AI Ticker Source
│   │   ├── PromptLibrary           ← 6 strategii inwestycyjnych
│   │   ├── AITickerSource          ← Fasada (single/multi-shot)
│   │   ├── TickerParser            ← Parsowanie odpowiedzi AI
│   │   └── BackendFactory          ← Groq | Anthropic | OpenAI | Mock
│   ├── ticker_source.py            ← Router → AITickerSource
│   └── fetcher.py                  ← Yahoo Finance (równoległe, retry)
│
├── db/
│   ├── models.py                   ← 4 tabele SQLite (EAV, historia)
│   └── repository.py               ← Repository Pattern (inkrementacyjny)
│
├── screening/
│   ├── filter_engine.py            ← Filtry AND z progami użytkownika
│   └── scorer.py                   ← Min-max + wagi → ranking
│
├── portfolio/
│   └── builder.py                  ← equal/score/rank weighted + stabilność
│
├── reports/
│   └── reporter.py                 ← Rich console + eksport CSV
│
├── scheduler/
│   └── runner.py                   ← APScheduler (daily/weekly/monthly)
│
├── test_ai_source.py               ← Testy jednostkowe (bez API key)
└── requirements.txt
```

---

## 🚀 Instalacja i uruchomienie

```bash
# 1. Instalacja zależności
pip install -r requirements.txt

# 2. Rejestracja Groq (darmowa, bez karty kredytowej)
#    → https://console.groq.com → API Keys → Create API Key

# 3. Ustaw klucz API
export GROQ_API_KEY=gsk_twój_klucz

# 4. Uruchomienie
python main.py                                       # domyślna strategia (growth_quality, 50 spółek)
python main.py --strategy deep_value                 # niedowartościowane spółki
python main.py --strategy compounders                # najwyższa jakość, długi horyzont
python main.py --strategy sector_leaders --sector healthcare
python main.py --strategy thematic --theme "quantum computing"
python main.py --strategy global_diversified         # szeroki globalny portfel badawczy
python main.py --n 80                                # więcej tickerów
python main.py --multi-shot                          # 3× więcej spółek (3 zapytania AI)
python main.py --backend mock                        # test bez API key
python main.py --analyze                             # historia uruchomień
python main.py --schedule                            # harmonogram (wg config)

# 5. Testy (bez internetu i API key)
python test_ai_source.py
```

---

## 🧠 Strategie AI

| Strategia | Opis | Domyślne n |
|-----------|------|-----------|
| `growth_quality` | Wzrost + jakość fundamentalna, globalna diversyfikacja | 50 |
| `deep_value` | Niedowartościowane z marżą bezpieczeństwa | 40 |
| `compounders` | Tylko szeroki moat, ROIC >20%, długi horyzont | 30 |
| `sector_leaders` | Liderzy i challengers w wybranym sektorze | 40 |
| `thematic` | Ekspozycja na wybrany megatrend | 35 |
| `global_diversified` | Precyzyjna alokacja geogr. i sektorowa | 60 |

### Przykłady tematycznych portfeli

```bash
python main.py --strategy thematic --theme "artificial intelligence"
python main.py --strategy thematic --theme "clean energy transition"
python main.py --strategy thematic --theme "longevity and biotech"
python main.py --strategy thematic --theme "defense and cybersecurity"
python main.py --strategy thematic --theme "emerging market consumer"
python main.py --strategy thematic --theme "space economy"
```

---

## 🤖 Dostępne backendy LLM

| Backend | Koszt | Model domyślny | Rejestracja |
|---------|-------|----------------|-------------|
| **Groq** (domyślny) | **DARMOWY** | `llama-3.3-70b-versatile` | https://console.groq.com |
| Anthropic | Płatne | `claude-sonnet-4-6` | https://console.anthropic.com |
| OpenAI | Płatne | `gpt-4o-mini` | https://platform.openai.com |
| Mock | Brak | — | Brak (testowy) |

### Przełączanie backendów

```yaml
# config/user_config.yaml
source:
  ai:
    backend: "groq"           # ← zmień na anthropic lub openai
    api_key_env: "GROQ_API_KEY"
    model: "llama-3.3-70b-versatile"
```

lub z CLI:
```bash
python main.py --backend anthropic    # wymaga ANTHROPIC_API_KEY
python main.py --backend openai       # wymaga OPENAI_API_KEY
```

### Rejestracja własnego backendu (rozszerzalność)

```python
from data.ai_ticker_source import BackendFactory, LLMBackend

class MyCustomLLM(LLMBackend):
    @property
    def name(self): return "MyLLM/v1"
    def call(self, system, user, temperature):
        # ... twoja implementacja ...
        return '{"tickers": ["AAPL", "MSFT"]}'

BackendFactory.register("my_llm", MyCustomLLM)
```

---

## 🎯 Tryb Multi-Shot (ekspansja universum)

```bash
python main.py --multi-shot --n 60
```

Wysyła 3 zapytania do AI z rosnącą temperaturą (0.2 → 0.5 → 0.7),
łącząc wyniki w unię. Efekt: ~150–180 unikalnych spółek zamiast 60.
Idealny do szerokiego research'u i odkrywania mniej oczywistych spółek.

```yaml
# user_config.yaml
source:
  ai:
    multi_shot: true
    multi_shot_runs: 3
    n_tickers: 60       # per shot → ~180 total
```

---

## ⚙️ Konfiguracja (user_config.yaml)

### Strategia i backend
```yaml
source:
  strategy: "growth_quality"    # lub: deep_value, compounders, sector_leaders, thematic
  ai:
    backend: "groq"
    api_key_env: "GROQ_API_KEY"
    model: "llama-3.3-70b-versatile"
    n_tickers: 50
    temperature: 0.35            # 0.2=deterministyczny, 0.8=różnorodny
    sector: "healthcare"         # dla sector_leaders
    theme: "clean energy"        # dla thematic
    multi_shot: false
    multi_shot_runs: 3
```

### Filtry (progi)
```yaml
filters:
  fundamental:
    pe_ratio:       [0, 60]      # [min, max], null = bez ograniczenia
    roe:            [10, null]   # ROE minimum 10%
    debt_to_equity: [null, 2.0]
    market_cap:     [5e8, null]  # min $500M
  technical:
    rsi_14:         [25, 80]
    volume_ratio:   [0.3, null]
```

### Scoring (wagi)
```yaml
scoring:
  weights:
    roe:            2.0     # wyższe = ważniejsze w rankingu
    revenue_growth: 1.8
    pe_ratio:      -0.4     # ujemna waga = kara za wysokie P/E
    debt_to_equity: -0.8
```

---

## 🗄️ Baza danych – historia badań

SQLite z 4 tabelami, bez nadpisywania (każdy run = nowe rekordy):

```
screening_runs       ← metadane: strategia AI, czas, statystyki
metric_snapshots     ← EAV: każda metryka każdej spółki z każdego runu
screening_results    ← kto przeszedł filtry i z jakim score
portfolio_snapshots  ← wagi portfela + stability_score
```

Po kilku uruchomieniach tej samej strategii:
```bash
python main.py --analyze
# Pokaże: które spółki najczęściej pojawiają się w wynikach,
# ich średni score, historię portfela
```

---

## 📁 Pliki wynikowe

Po każdym uruchomieniu w katalogu `reports/`:
```
reports/
├── screening_ai_growth_quality_20250420_070215_run1.csv  ← ranking ze scorami
└── portfolio_ai_growth_quality_20250420_070215_run1.csv  ← portfel z wagami
```

---

## ⚠️ Disclaimer

System służy wyłącznie celom edukacyjnym i informacyjnym. Nie stanowi doradztwa
inwestycyjnego. Rekomendacje AI mogą być niedokładne. Dane Yahoo Finance mogą być
opóźnione. Zawsze przeprowadź własne due diligence.
