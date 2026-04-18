# 📊 Stock Screener – System selekcji akcji z analizą historyczną

Kompletny, modularny system do automatycznej selekcji spółek giełdowych,
budowania portfela inwestycyjnego i analizy historycznej wyników.
Wszystkie dane pochodzą z darmowych źródeł (Yahoo Finance, Wikipedia).

---

## 🏗️ Architektura systemu

```
stock_screener/
├── main.py                    # Punkt wejścia + orchestrator pipeline
│
├── config/
│   ├── user_config.yaml       # ← EDYTUJ TUTAJ swoje progi i ustawienia
│   └── settings.py            # Ładowanie i walidacja konfiguracji
│
├── data/
│   ├── ticker_source.py       # Pobieranie list tickerów (S&P500, WIG20, DAX40...)
│   └── fetcher.py             # Pobieranie danych z Yahoo Finance (równoległe)
│
├── db/
│   ├── models.py              # Schema SQLAlchemy (4 tabele)
│   └── repository.py          # Warstwa dostępu do danych (Repository Pattern)
│
├── screening/
│   ├── filter_engine.py       # Filtrowanie wg progów użytkownika
│   └── scorer.py              # Scoring i ranking (ważona normalizacja)
│
├── portfolio/
│   └── builder.py             # Budowa portfela + bonus stabilności historycznej
│
├── reports/
│   └── reporter.py            # Rich console output + eksport CSV
│
├── scheduler/
│   └── runner.py              # APScheduler (daily/weekly/monthly)
│
└── requirements.txt
```

---

## 🗄️ Schema bazy danych

```
screening_runs           ← metadane każdego uruchomienia
    │
    ├── metric_snapshots     ← surowe wartości metryk (EAV: ticker × run × metric = value)
    │                           nigdy nie nadpisywane – pełna historia
    │
    ├── screening_results    ← kto przeszedł filtry, z jakim score i rankiem
    │
    └── portfolio_snapshots  ← skład portfela po każdym uruchomieniu
```

**Kluczowe decyzje projektowe bazy:**
- **Nigdy nie nadpisujemy** – każde uruchomienie = nowe rekordy
- **Model EAV** dla metryk → nowe metryki bez zmiany schematu
- **WAL mode SQLite** → lepsza wydajność przy równoległych operacjach
- **Indeksy** na (ticker, metric_name) i (run_id) → szybkie zapytania historyczne

---

## 🚀 Instalacja i uruchomienie

```bash
# 1. Instalacja zależności
pip install -r requirements.txt

# 2. Jedno uruchomienie (S&P500, domyślna konfiguracja)
python main.py

# 3. Własna konfiguracja
python main.py --config my_config.yaml

# 4. Inne źródło danych
python main.py --source wig20
python main.py --source nasdaq100
python main.py --source dax40

# 5. Analiza historyczna (po kilku uruchomieniach)
python main.py --analyze

# 6. Automatyczny harmonogram (wg ustawień w config)
python main.py --schedule
```

---

## ⚙️ Konfiguracja (user_config.yaml)

### Źródło danych
```yaml
source:
  index: "sp500"    # sp500 | nasdaq100 | wig20 | dax40 | custom
  custom_tickers:   # używane tylko przy index: "custom"
    - "AAPL"
    - "MSFT"
```

### Filtry (progi)
```yaml
filters:
  fundamental:
    pe_ratio:       [0, 35]       # min, max (null = brak ograniczenia)
    roe:            [8, null]     # ROE minimum 8%
    debt_to_equity: [null, 1.5]   # D/E maksimum 1.5

  technical:
    rsi_14:         [30, 70]      # Unikamy wyprzedanych i wykupionych
    momentum_3m:    [0, null]     # Pozytywne momentum
```

### Scoring (wagi)
```yaml
scoring:
  weights:
    roe:            2.0    # wyższe ROE → wyższy score
    pe_ratio:      -0.5   # wyższe P/E → niższy score (ujemna waga)
    debt_to_equity: -1.0
    momentum_3m:    1.0
```

### Portfel
```yaml
portfolio:
  max_positions: 20
  weighting: "score_weighted"   # equal | score_weighted | rank_weighted
  stability_bonus_weight: 0.5   # bonus za historyczną stabilność
```

---

## 📐 Algorytm scoringu

1. Dla każdej ważonej metryki: `norm = (value - min) / (max - min)` → [0, 1]
2. `contribution = norm × weight`
3. `score = Σ contributions`
4. Sortowanie malejąco po score
5. Ranking 1..N

**Bonus stabilności:**
- Spółki, które regularnie pojawiają się w screeningu historycznym, otrzymują bonus:
  `combined_score = score + frequency × stability_bonus_weight`
- Redukuje rotację portfela i preferuje firmy o stabilnych fundamentach

---

## 📊 Dostępne metryki

| Kategoria | Metryka | Opis |
|-----------|---------|------|
| Fundamentalne | `pe_ratio` | Cena / Zysk (trailing) |
| | `pb_ratio` | Cena / Wartość księgowa |
| | `ps_ratio` | Cena / Przychody |
| | `roe` | Zwrot z kapitału (%) |
| | `roa` | Zwrot z aktywów (%) |
| | `debt_to_equity` | Wskaźnik zadłużenia |
| | `current_ratio` | Płynność bieżąca |
| | `revenue_growth` | Wzrost przychodów YoY (%) |
| | `earnings_growth` | Wzrost zysku YoY (%) |
| | `profit_margin` | Marża netto (%) |
| | `dividend_yield` | Stopa dywidendy (%) |
| | `market_cap` | Kapitalizacja (USD) |
| Techniczne | `momentum_1m/3m/6m/12m` | Zmiana ceny (%) |
| | `rsi_14` | RSI 14 dni |
| | `volume_ratio` | Wolumen / Średnia 20d |
| | `above_ma50` | Powyżej MA50 (0/1) |
| | `above_ma200` | Powyżej MA200 (0/1) |
| | `volatility_30d` | Roczna zmienność 30d (%) |

---

## 🛡️ Obsługa błędów i skalowalność

- **Retry z backoff**: każdy ticker pobierany do 3 razy
- **Graceful degradation**: ticker z błędem → logowany, nie zatrzymuje procesu
- **Brakujące metryki**: ticker z NULL w filtrowanej metryce → odrzucony (conservative)
- **Rate limiting**: konfigurowalne opóźnienie między requestami (`api_delay_seconds`)
- **ThreadPoolExecutor**: równoległe pobieranie (`fetch_workers` wątków)
- **Bulk insert**: masowy zapis do SQLite przez `bulk_insert_mappings`
- **WAL SQLite**: odporność na awarie podczas zapisu

---

## 🔧 Technologie i uzasadnienie wyboru

| Technologia | Uzasadnienie |
|-------------|--------------|
| **Python** | Ekosystem bibliotek finansowych, prosta składnia |
| **yfinance** | Darmowe dane Yahoo Finance bez API key |
| **SQLite + SQLAlchemy** | Brak serwera, pełna historia, SQL queries, portable |
| **pandas / numpy** | Wydajne obliczenia na danych tabelarycznych |
| **APScheduler** | Prosty, niezawodny scheduler z persistencją |
| **Rich** | Czytelne wyjście konsolowe z tabelami i progress |
| **PyYAML** | Czytelna konfiguracja dla użytkownika nietechnicznego |

---

## 📈 Przykładowe wyniki (po kilku uruchomieniach)

```
python main.py --analyze

Historia uruchomień: 5 łącznie
┌──────────┬─────────────────────┬────────┬─────────┬────────┐
│ run_id   │ timestamp           │ source │ fetched │ passed │
├──────────┼─────────────────────┼────────┼─────────┼────────┤
│ 1        │ 2024-01-08 07:00:02 │ sp500  │ 493     │ 47     │
│ 2        │ 2024-01-15 07:00:01 │ sp500  │ 491     │ 52     │
│ ...      │ ...                 │ ...    │ ...     │ ...    │
└──────────┴─────────────────────┴────────┴─────────┴────────┘

Top 15 najczęściej pojawiających się spółek:
┌────────┬───────────┬──────┬───────────┐
│ Ticker │ Wystąpień │ Freq │ Avg Score │
├────────┼───────────┼──────┼───────────┤
│ MSFT   │ 5         │ 100% │ 0.8234    │
│ AAPL   │ 4         │  80% │ 0.7891    │
│ ...    │ ...       │ ...  │ ...       │
└────────┴───────────┴──────┴───────────┘
```

---

## 🔮 Możliwa rozbudowa

- **Więcej źródeł danych**: Alpha Vantage, EDGAR, Quandl, GPW API
- **Więcej metryk**: DCF, EV/EBITDA, Altman Z-Score, insider transactions
- **Backtesting**: symulacja zwrotów portfela na danych historycznych
- **Web UI**: Flask/FastAPI + React dashboard z wykresami historii portfela
- **Powiadomienia**: email/Slack po każdym uruchomieniu
- **Multi-indeks**: screener jednocześnie na kilku giełdach
- **ML ranking**: zamiast liniowego scoringu – model predykcyjny

---

## ⚠️ Disclaimer

System służy wyłącznie celom edukacyjnym i informacyjnym.
Nie stanowi doradztwa inwestycyjnego. Dane z Yahoo Finance mogą być
opóźnione lub niedokładne. Zawsze przeprowadź własną analizę due diligence.
