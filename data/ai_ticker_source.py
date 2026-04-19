"""
data/ai_ticker_source.py
Pobieranie list tickerów za pomocą modeli językowych (LLM).

ARCHITEKTURA:
─────────────────────────────────────────────────────────────────
  PromptLibrary          ← zestaw gotowych promptów strategicznych
       │
  AITickerSource         ← fasada (główny interfejs modułu)
       │
       └── BackendFactory
                ├── GroqBackend        (domyślny – darmowy, Llama 3.3 70B)
                ├── AnthropicBackend   (Claude – do rozbudowy w przyszłości)
                ├── OpenAIBackend      (GPT-4o – opcjonalny)
                └── MockBackend        (testy bez API key)

DARMOWE API:
  Groq (https://console.groq.com) – darmowy tier:
    • 14 400 req/dzień, 500 000 tokenów/min
    • Modele: llama-3.3-70b-versatile, llama-3.1-8b-instant, mixtral-8x7b
    • Brak karty kredytowej przy rejestracji
─────────────────────────────────────────────────────────────────
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
import requests
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════
# BIBLIOTEKA PROMPTÓW
# ══════════════════════════════════════════════════════════════

class PromptLibrary:
    """
    Zestaw starannie zaprojektowanych promptów do różnych strategii inwestycyjnych.
    Każdy prompt jest zoptymalizowany pod kątem precyzji zwracanych danych
    i jakości doboru spółek.
    """

    # Szablon systemowy wspólny dla wszystkich strategii
    SYSTEM_PROMPT = """You are a senior equity research analyst with 20+ years of experience 
covering global stock markets across all sectors and geographies. You have deep expertise in 
fundamental analysis, competitive dynamics, and identifying companies with durable competitive 
advantages. Your recommendations are used by professional investors and must be actionable and 
grounded in verifiable financial metrics.

CRITICAL OUTPUT RULES:
- Return ONLY a valid JSON array of ticker symbols — no prose, no markdown, no explanation
- Use Yahoo Finance ticker format exactly: US stocks use plain symbols (AAPL, MSFT), 
  European stocks use exchange suffix (.AS=Amsterdam, .PA=Paris, .DE=Frankfurt, .L=London, 
  .MI=Milan, .SW=Zurich), Asian stocks use (.T=Tokyo, .HK=HongKong, .SS=Shanghai, .KS=Korea)
- Every ticker must be currently traded on a major exchange
- Do not include delisted, OTC-only, or unverifiable symbols
- If a company has multiple share classes, return only the most liquid class"""

    @staticmethod
    def growth_quality(n: int = 50) -> str:
        """Strategia wzrostu z naciskiem na jakość fundamentalną (domyślna)."""
        return f"""Identify exactly {n} publicly traded companies that represent the highest 
conviction opportunities combining quality fundamentals with growth potential. 

SELECTION FRAMEWORK — evaluate each company across these dimensions:

1. BUSINESS QUALITY (40% weight)
   — Return on Invested Capital (ROIC) consistently above 15%
   — Durable competitive moat: network effects, switching costs, cost leadership, 
     intangible assets (brands, patents, regulatory licenses)
   — Proven pricing power: ability to raise prices without losing customers
   — Recurring or highly predictable revenue streams

2. GROWTH TRAJECTORY (35% weight)  
   — Revenue growing at minimum 10% CAGR over past 3 years, OR accelerating recently
   — Expanding addressable market (TAM growth) with identifiable catalysts
   — Reinvestment opportunities at high returns on capital
   — Margin expansion potential or structurally high margins (>20% operating)

3. FINANCIAL HEALTH (15% weight)
   — Net debt/EBITDA below 3x (prefer net cash for smaller companies)
   — Strong free cash flow conversion (FCF/Net Income > 70%)
   — Minimal equity dilution history

4. VALUATION DISCIPLINE (10% weight)
   — PEG ratio below 2.5, OR EV/FCF below 40x for high-quality compounder
   — Not in bubble territory relative to sector peers

MANDATORY DIVERSITY REQUIREMENTS:
   — At least 8 companies from outside the United States
   — At least 6 different GICS sectors represented
   — At least 5 mid-cap companies ($2–15B market cap) alongside large-caps
   — At least 3 companies from Emerging Markets (India, Southeast Asia, Latin America)
   — No single sector exceeding 30% of total selections

GEOGRAPHIC COVERAGE TARGET:
   North America 40–50%, Europe 20–25%, Asia-Pacific 15–20%, Rest of World 5–10%

Return a JSON array of exactly {n} ticker symbols.
Example format: ["AAPL", "MSFT", "ASML.AS", "NOVO-B.CO", "HDFC.NS", "6098.T"]"""

    @staticmethod
    def deep_value(n: int = 40) -> str:
        """Strategia wartościowa – spółki niedowartościowane z marżą bezpieczeństwa."""
        return f"""Identify exactly {n} publicly traded companies that are significantly 
undervalued relative to their intrinsic value, with a clear margin of safety for long-term investors.

DEEP VALUE CRITERIA:

1. VALUATION METRICS (primary screen)
   — P/E below 15x trailing or forward earnings, OR
   — EV/EBITDA below 8x, OR  
   — Price-to-Book below 1.5x with positive ROE, OR
   — Price-to-FCF below 12x
   — Trading below estimated intrinsic value by at least 30%

2. BUSINESS QUALITY FLOOR (companies must clear these minimums)
   — Profitable for at least 3 consecutive years
   — Positive free cash flow in most recent 12 months
   — Current ratio above 1.0 (solvent)
   — Not in secular decline (addressable market must be stable or growing)

3. CATALYST FOR REVALUATION (at least one required)
   — Improving business fundamentals not yet reflected in price
   — Asset monetization opportunity (spin-off, real estate, intellectual property)
   — Industry cyclical recovery
   — Management change or restructuring
   — Market misunderstanding of business quality

4. AVOID VALUE TRAPS
   — Exclude companies with rapidly declining revenues
   — Exclude companies with debt/equity exceeding 3x in cyclical industries
   — Exclude companies disrupted by technology with no credible response

DIVERSITY: Include companies from at least 5 countries and 5 sectors.
Preference for companies with insider buying or significant share buybacks.

Return a JSON array of exactly {n} ticker symbols."""

    @staticmethod
    def compounders(n: int = 30) -> str:
        """Długoterminowe compoundersy – najwyższa jakość bez kompromisów."""
        return f"""Identify exactly {n} exceptional compounder businesses — companies capable 
of compounding shareholder value at 15%+ annually over the next decade with very high probability.

THE COMPOUNDER CHECKLIST (all criteria must be met):

✓ MOAT: Possesses a wide, durable competitive moat rated as "wide" by analysts
  — Examples of qualifying moats: Visa's network effect, LVMH's brand portfolio, 
    MSFT's switching costs, Booking's two-sided marketplace, ASML's monopoly on EUV

✓ REINVESTMENT: Large, high-return reinvestment opportunities available
  — Must be able to deploy incremental capital at >20% returns
  — Organic growth preferred over acquisition-driven growth

✓ MANAGEMENT: Proven capital allocation track record spanning 7+ years
  — Founder-led or owner-operated preferred
  — Low dilution history, history of profitable M&A if acquisitive

✓ FINANCIALS: Pristine balance sheet, minimal debt
  — Gross margins above 40% (for scalability)
  — Operating leverage: revenue growing faster than costs
  — FCF yield to enterprise value above 3%

✓ LONGEVITY: Business model resilient to technological disruption
  — Operating in industry with rising barriers to entry
  — Not dependent on single product/customer/geography

Include both well-known large-caps (proven compounder track record) and smaller 
emerging compounder candidates with all the hallmarks but earlier in their journey.

At least 10 companies must be non-US.

Return a JSON array of exactly {n} ticker symbols."""

    @staticmethod
    def sector_leaders(n: int = 40, sector: str = "technology") -> str:
        """Liderzy wybranego sektora – najlepsze spółki w danej branży."""
        sector_context = {
            "technology": "software, semiconductors, cloud infrastructure, cybersecurity, AI infrastructure",
            "healthcare": "pharmaceuticals, biotechnology, medical devices, healthcare services, genomics",
            "consumer": "consumer discretionary and staples, e-commerce, luxury goods, food & beverage",
            "financials": "banks, insurance, asset management, fintech, payment processors",
            "industrials": "aerospace, automation, logistics, clean energy equipment, defense",
            "energy": "oil & gas majors, renewable energy, energy transition, utilities",
            "materials": "mining, specialty chemicals, agricultural commodities",
        }.get(sector.lower(), sector)

        return f"""Identify exactly {n} publicly traded companies that are leaders or 
emerging leaders in the {sector} sector, specifically covering: {sector_context}.

SELECTION APPROACH for {sector.upper()} sector:

TIER 1 — Established Sector Champions (40% of selections)
   Clear market leadership with defensible positions, proven profitability,
   and demonstrated ability to maintain or grow market share.

TIER 2 — High-Growth Challengers (40% of selections)  
   Companies with innovative business models disrupting incumbents,
   growing revenue at >25% annually with a credible path to profitability
   or already profitable at scale.

TIER 3 — Value Opportunities Within Sector (20% of selections)
   Quality businesses in the sector trading below fair value due to 
   temporary headwinds, with strong recovery potential.

QUALITY FILTERS:
   — Minimum $500M market cap (for liquidity)
   — Traded on NYSE, NASDAQ, LSE, Euronext, TSE, or ASX
   — Exclude companies under SEC investigation or with accounting irregularities

GLOBAL SCOPE: Include sector leaders from the US, Europe, Asia, and rest of world.
Do NOT limit to US-listed companies only.

Return a JSON array of exactly {n} ticker symbols."""

    @staticmethod
    def thematic(n: int = 35, theme: str = "artificial intelligence") -> str:
        """Portfel tematyczny – spółki eksponowane na wybrany megatrend."""
        return f"""Identify exactly {n} publicly traded companies best positioned to 
benefit from the megatrend: "{theme}".

EXPOSURE FRAMEWORK — rank companies by directness of thematic exposure:

PURE PLAYS (50% of selections — direct, high-conviction exposure)
   Companies where >50% of revenue or future value is directly tied to {theme}.
   These must be genuine beneficiaries, not companies that merely mention the theme.

ENABLERS (30% of selections — critical infrastructure)
   Companies providing essential components, infrastructure, data, or services 
   that make {theme} possible at scale. Less obvious but often more durable.

ADOPTERS (20% of selections — accelerated by theme)
   Traditional companies whose business model is being meaningfully enhanced or 
   transformed by {theme}, creating competitive advantages vs. peers who adapt slower.

QUALITY STANDARDS:
   — All companies must have credible, revenue-generating businesses today
   — Exclude pre-revenue companies and development-stage businesses
   — Favor companies with proprietary data, technology IP, or unique positioning
   — Include global leaders: US, Europe, Japan, South Korea, Taiwan, China (if applicable)

AVOID: Marketing gimmicks — companies that rebranded for the theme with no 
fundamental business change, or companies with only superficial exposure.

Return a JSON array of exactly {n} ticker symbols."""

    @staticmethod
    def global_diversified(n: int = 60) -> str:
        """Szeroki, globalnie zdywersyfikowany portfel badawczy."""
        return f"""Identify exactly {n} publicly traded companies for a well-diversified 
global equity research universe — representing the best risk-adjusted opportunities 
across all sectors, geographies, and market caps worldwide.

CONSTRUCTION MANDATE:

GEOGRAPHIC ALLOCATION (enforce strictly):
   — United States: 20–25 companies (35–40%)
   — Europe (including UK): 12–15 companies (20–25%)
   — Asia-Pacific Developed (Japan, Australia, Singapore, South Korea): 8–10 companies (13–17%)
   — Emerging Asia (India, China, Taiwan, Southeast Asia): 7–9 companies (12–15%)
   — Rest of World (Canada, Brazil, Middle East, Africa): 4–6 companies (7–10%)

SECTOR ALLOCATION (no sector above 20%):
   Technology, Healthcare, Financials, Consumer Discretionary, Consumer Staples,
   Industrials, Energy, Materials, Real Estate, Utilities, Communication Services

MARKET CAP MIX:
   — Mega-cap (>$200B): 30–35%
   — Large-cap ($15B–$200B): 35–40%  
   — Mid-cap ($2B–$15B): 20–25%
   — Small-cap ($300M–$2B): 5–10%

QUALITY FLOOR (every company must meet):
   — Profitable on operating basis
   — Listed for minimum 3 years on major exchange
   — Adequate liquidity (daily volume >$1M USD equivalent)

SELECTION PHILOSOPHY:
   Prioritize companies with enduring business models, strong management teams,
   and identifiable competitive advantages. Seek to represent the global economy's 
   best opportunities across cycles.

Return a JSON array of exactly {n} ticker symbols."""

    @classmethod
    def get_prompt(cls, strategy: str, n: int, **kwargs) -> str:
        """Pobierz prompt dla wybranej strategii."""
        strategies = {
            "growth_quality":    lambda: cls.growth_quality(n),
            "deep_value":        lambda: cls.deep_value(n),
            "compounders":       lambda: cls.compounders(n),
            "sector_leaders":    lambda: cls.sector_leaders(n, kwargs.get("sector", "technology")),
            "thematic":          lambda: cls.thematic(n, kwargs.get("theme", "artificial intelligence")),
            "global_diversified": lambda: cls.global_diversified(n),
        }
        fn = strategies.get(strategy)
        if not fn:
            logger.warning(f"Nieznana strategia '{strategy}', używam 'growth_quality'")
            fn = strategies["growth_quality"]
        return fn()


# ══════════════════════════════════════════════════════════════
# BACKENDY LLM
# ══════════════════════════════════════════════════════════════

class LLMBackend(ABC):
    """Abstrakcyjny backend LLM – interfejs dla wszystkich dostawców."""

    @abstractmethod
    def call(self, system_prompt: str, user_prompt: str, temperature: float) -> str:
        """Wywołaj API i zwróć surowy tekst odpowiedzi."""
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """Nazwa backendu (do logów)."""
        ...


class GroqBackend(LLMBackend):
    """
    Groq – darmowy, bardzo szybki backend LLM.

    Rejestracja: https://console.groq.com (brak karty kredytowej)
    Free tier: 14 400 req/dzień, modele Llama 3.3 70B / Mixtral 8x7B / Gemma 2
    API: kompatybilne z OpenAI SDK

    Ustaw zmienną środowiskową: GROQ_API_KEY=gsk_...
    """

    DEFAULT_MODEL = "llama-3.3-70b-versatile"
    API_URL = "https://api.groq.com/openai/v1/chat/completions"

    def __init__(self, api_key: str | None = None, model: str | None = None):
        self._api_key = api_key or os.getenv("GROQ_API_KEY", "")
        self._model = model or self.DEFAULT_MODEL

        if not self._api_key:
            raise ValueError(
                "Brak GROQ_API_KEY.\n"
                "1. Zarejestruj się na https://console.groq.com (darmowe, bez karty)\n"
                "2. Wygeneruj API key w zakładce 'API Keys'\n"
                "3. Ustaw zmienną: export GROQ_API_KEY=gsk_twój_klucz"
            )

    @property
    def name(self) -> str:
        return f"Groq/{self._model}"

    def call(self, system_prompt: str, user_prompt: str, temperature: float = 0.3) -> str:
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_prompt},
            ],
            "temperature": temperature,
            "max_tokens": 2048,
            "response_format": {"type": "json_object"},  # Groq JSON mode
        }

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        
        resp = requests.post(self.API_URL, json=payload, headers=headers, timeout=60)
        resp.raise_for_status()
        data = resp.json()

        return data["choices"][0]["message"]["content"]


class AnthropicBackend(LLMBackend):
    """
    Anthropic Claude – backend do przyszłej rozbudowy.
    Wymaga klucza API (płatne): https://console.anthropic.com

    Ustaw: ANTHROPIC_API_KEY=sk-ant-...
    Instalacja SDK: pip install anthropic
    """

    DEFAULT_MODEL = "claude-sonnet-4-20250514"
    API_URL = "https://api.anthropic.com/v1/messages"

    def __init__(self, api_key: str | None = None, model: str | None = None):
        self._api_key = api_key or os.getenv("ANTHROPIC_API_KEY", "")
        self._model = model or self.DEFAULT_MODEL

        if not self._api_key:
            raise ValueError(
                "Brak ANTHROPIC_API_KEY. Ustaw: export ANTHROPIC_API_KEY=sk-ant-..."
            )

    @property
    def name(self) -> str:
        return f"Anthropic/{self._model}"

    def call(self, system_prompt: str, user_prompt: str, temperature: float = 0.3) -> str:
        payload = {
            "model": self._model,
            "max_tokens": 2048,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_prompt}],
            "temperature": temperature,
        }

        headers = {
            "x-api-key": self._api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        
        resp = requests.post(self.API_URL, json=payload, headers=headers, timeout=60)
        resp.raise_for_status()
        data = resp.json()

        return data["content"][0]["text"]


class OpenAIBackend(LLMBackend):
    """
    OpenAI GPT – opcjonalny backend.
    Ustaw: OPENAI_API_KEY=sk-...
    """

    DEFAULT_MODEL = "gpt-4o-mini"
    API_URL = "https://api.openai.com/v1/chat/completions"

    def __init__(self, api_key: str | None = None, model: str | None = None):
        self._api_key = api_key or os.getenv("OPENAI_API_KEY", "")
        self._model = model or self.DEFAULT_MODEL
        if not self._api_key:
            raise ValueError("Brak OPENAI_API_KEY.")

    @property
    def name(self) -> str:
        return f"OpenAI/{self._model}"

    def call(self, system_prompt: str, user_prompt: str, temperature: float = 0.3) -> str:
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_prompt},
            ],
            "temperature": temperature,
            "max_tokens": 2048,
            "response_format": {"type": "json_object"},
        }

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        
        resp = requests.post(self.API_URL, json=payload, headers=headers, timeout=60)
        resp.raise_for_status()
        data = resp.json()

        return data["choices"][0]["message"]["content"]


class MockBackend(LLMBackend):
    """
    Backend testowy – nie wymaga klucza API.
    Zwraca predefiniowaną listę tickerów do celów testowych.
    Aktywuj: backend: "mock" w konfiguracji, lub AI_BACKEND=mock
    """

    @property
    def name(self) -> str:
        return "Mock/Test"

    def call(self, system_prompt: str, user_prompt: str, temperature: float = 0.3) -> str:
        logger.info("[MOCK] Symulacja odpowiedzi AI (brak prawdziwego API)")
        # Realistyczna lista dla testów – różnorodna geograficznie i sektorowo
        mock_tickers = [
            # US Tech
            "AAPL", "MSFT", "NVDA", "GOOGL", "META", "AMZN", "CRM", "ADBE",
            # US Healthcare
            "LLY", "UNH", "ABBV", "TMO", "ISRG", "DXCM",
            # US Financials
            "V", "MA", "JPM", "BRK-B",
            # US Consumer & Industrials
            "COST", "HD", "ODFL", "FAST",
            # Europe
            "ASML.AS", "NOVO-B.CO", "SAP.DE", "MC.PA", "NESN.SW", "AZN.L",
            "LVMH.PA", "RMS.PA", "SIE.DE",
            # Asia-Pacific
            "7203.T", "6758.T", "035420.KS", "2330.TW", "RELIANCE.NS",
            "INFY.NS", "9988.HK", "700.HK",
            # Emerging Markets
            "NU", "MELI", "GRAB", "SEA",
        ]
        return json.dumps({"tickers": mock_tickers})


# ══════════════════════════════════════════════════════════════
# FABRYKA BACKENDÓW
# ══════════════════════════════════════════════════════════════

class BackendFactory:
    """Tworzy odpowiedni backend na podstawie konfiguracji."""

    _registry: dict[str, type[LLMBackend]] = {
        "groq":       GroqBackend,
        "anthropic":  AnthropicBackend,
        "openai":     OpenAIBackend,
        "mock":       MockBackend,
    }

    @classmethod
    def create(cls, ai_config: dict) -> LLMBackend:
        backend_name = os.getenv("AI_BACKEND") or ai_config.get("backend", "groq")
        api_key = os.getenv(ai_config.get("api_key_env", "GROQ_API_KEY")) or ai_config.get("api_key")
        model = ai_config.get("model")

        backend_cls = cls._registry.get(backend_name.lower())
        if not backend_cls:
            raise ValueError(
                f"Nieznany backend: '{backend_name}'. "
                f"Dostępne: {list(cls._registry.keys())}"
            )

        if backend_name == "mock":
            return MockBackend()

        kwargs: dict[str, Any] = {}
        if api_key:
            kwargs["api_key"] = api_key
        if model:
            kwargs["model"] = model

        return backend_cls(**kwargs)

    @classmethod
    def register(cls, name: str, backend_cls: type[LLMBackend]) -> None:
        """Zarejestruj własny backend (rozszerzalność)."""
        cls._registry[name.lower()] = backend_cls


# ══════════════════════════════════════════════════════════════
# PARSER I WALIDATOR ODPOWIEDZI
# ══════════════════════════════════════════════════════════════

class TickerParser:
    """
    Parsuje odpowiedź LLM i wyodrębnia listę tickerów.
    Obsługuje różne formaty: JSON array, JSON object z listą, tekst z tickerami.
    """

    # Dozwolone sufiksy giełdowe (Yahoo Finance format)
    VALID_SUFFIXES = {
        ".AS", ".PA", ".DE", ".L", ".MI", ".SW", ".MC", ".BR", ".CO",
        ".ST", ".OL", ".HE", ".IS", ".WA", ".PR",       # Europa
        ".T", ".HK", ".SS", ".SZ", ".KS", ".KQ", ".TW", ".SI",  # Azja
        ".NS", ".BO",                                    # Indie
        ".AX", ".NZ",                                    # Oceania
        ".SA", ".MX", ".BA",                             # Ameryka Łacińska
        ".TA",                                           # Izrael
    }

    # Blacklista: nieakceptowalne tokeny
    BLACKLIST = {"ETF", "INDEX", "FUND", "CASH", "NULL", "NONE", "NA", "N/A"}

    @classmethod
    def parse(cls, raw_text: str, expected_n: int) -> list[str]:
        """
        Wyodrębnij tickery z surowej odpowiedzi modelu.
        Próbuje kilku strategii parsowania.
        """
        tickers: list[str] = []

        # Strategia 1: JSON object z listą (Groq JSON mode zwraca obiekt)
        try:
            data = json.loads(raw_text)
            if isinstance(data, list):
                tickers = data
            elif isinstance(data, dict):
                # Szukaj pierwszej wartości będącej listą
                for v in data.values():
                    if isinstance(v, list):
                        tickers = v
                        break
        except json.JSONDecodeError:
            pass

        # Strategia 2: Szukaj JSON array w tekście
        if not tickers:
            match = re.search(r'\[([^\[\]]*)\]', raw_text, re.DOTALL)
            if match:
                try:
                    tickers = json.loads(f"[{match.group(1)}]")
                except json.JSONDecodeError:
                    # Wyodrębnij cytowane stringi
                    tickers = re.findall(r'"([A-Z0-9.\-]+)"', match.group(0))

        # Strategia 3: Wyodrębnij wszystkie tokeny wyglądające jak tickery
        if not tickers:
            tickers = re.findall(r'\b([A-Z]{1,5}(?:\.[A-Z]{1,2})?|[A-Z]{1,4}\d{1,4}(?:\.[A-Z]{2})?)\b',
                                  raw_text)

        cleaned = cls._clean(tickers)

        if len(cleaned) < expected_n * 0.5:
            logger.warning(
                f"Parser wyodrębnił tylko {len(cleaned)}/{expected_n} tickerów. "
                f"Odpowiedź modelu mogła mieć niepoprawny format."
            )

        return cleaned

    @classmethod
    def _clean(cls, raw: list) -> list[str]:
        """Filtruj, normalizuj i deduplikuj tickery."""
        seen: set[str] = set()
        result: list[str] = []

        for item in raw:
            if not isinstance(item, str):
                continue

            t = str(item).strip().upper()

            # Podstawowe filtry
            if not t:
                continue
            if t in cls.BLACKLIST:
                continue
            if len(t) < 1 or len(t) > 12:
                continue
            if not re.match(r'^[A-Z0-9]', t):
                continue

            # Waliduj format tickera
            if not cls._is_valid_ticker(t):
                continue

            # Deduplikacja
            if t in seen:
                continue

            seen.add(t)
            result.append(t)

        return result

    @classmethod
    def _is_valid_ticker(cls, ticker: str) -> bool:
        """Sprawdź, czy ticker ma poprawny format."""
        # US ticker: 1–5 liter/cyfr (BRK-B też OK)
        if re.match(r'^[A-Z]{1,5}(-[A-Z])?$', ticker):
            return True

        # Ticker z sufiksem giełdowym
        for suffix in cls.VALID_SUFFIXES:
            if ticker.endswith(suffix):
                base = ticker[: -len(suffix)]
                if re.match(r'^[A-Z0-9]{1,10}(-[A-Z0-9])?$', base):
                    return True

        # Azjatyckie tickery numeryczne (np. 7203.T, 9988.HK)
        if re.match(r'^\d{4,6}\.[A-Z]{1,2}$', ticker):
            return True

        return False


# ══════════════════════════════════════════════════════════════
# GŁÓWNA KLASA – AI TICKER SOURCE
# ══════════════════════════════════════════════════════════════

@dataclass
class AIRunResult:
    """Wynik jednego uruchomienia AI ticker source."""
    tickers: list[str]
    strategy: str
    backend: str
    n_requested: int
    n_returned: int
    attempts: int
    prompt_used: str = field(repr=False)
    raw_response: str = field(repr=False)


class AITickerSource:
    """
    Pobiera listy tickerów używając modeli językowych.

    Cechy:
    - Adapter Pattern: łatwa podmiana backendu (Groq → Claude → OpenAI)
    - Retry z różnymi temperaturami (wyższa temp = większa różnorodność)
    - Walidacja i parsowanie odpowiedzi
    - Deduplication z poprzednimi wywołaniami (dla trybu ekspansji)
    - Łączenie wyników z wielu prompts (tryb multi-shot)
    """

    def __init__(self, ai_config: dict):
        self.config = ai_config
        self.n_tickers = ai_config.get("n_tickers", 50)
        self.strategy = ai_config.get("strategy", "growth_quality")
        self.temperature = ai_config.get("temperature", 0.3)
        self.max_retries = ai_config.get("max_retries", 3)
        self.multi_shot = ai_config.get("multi_shot", False)
        self.multi_shot_runs = ai_config.get("multi_shot_runs", 3)

        self._extra_kwargs = {
            k: v for k, v in ai_config.items()
            if k in ("sector", "theme")
        }

        self._backend: LLMBackend | None = None

    @property
    def backend(self) -> LLMBackend:
        """Leniwa inicjalizacja backendu."""
        if self._backend is None:
            self._backend = BackendFactory.create(self.config)
            logger.info(f"AI backend: {self._backend.name}")
        return self._backend

    def fetch(self) -> list[str]:
        """
        Pobierz listę tickerów od AI.

        Tryby:
        - Standardowy: jedno zapytanie, retry przy błędzie
        - Multi-shot: N zapytań z różnymi temperaturami, unia wyników
        """
        if self.multi_shot:
            return self._fetch_multi_shot()
        else:
            result = self._fetch_single()
            return result.tickers

    def _fetch_single(self, temperature_override: float | None = None) -> AIRunResult:
        """Jedno zapytanie z obsługą retry."""
        system_prompt = PromptLibrary.SYSTEM_PROMPT
        user_prompt = PromptLibrary.get_prompt(
            self.strategy,
            self.n_tickers,
            **self._extra_kwargs,
        )
        temperature = temperature_override or self.temperature

        logger.info(
            f"AI query: backend={self.backend.name}, "
            f"strategy={self.strategy}, n={self.n_tickers}, temp={temperature}"
        )

        last_error: Exception | None = None
        tickers: list[str] = []

        for attempt in range(1, self.max_retries + 1):
            try:
                # Zwiększ temperaturę przy retry (więcej różnorodności)
                actual_temp = min(temperature + (attempt - 1) * 0.1, 0.9)

                raw = self.backend.call(system_prompt, user_prompt, actual_temp)
                logger.debug(f"Odpowiedź AI (próba {attempt}): {raw[:200]}...")

                tickers = TickerParser.parse(raw, self.n_tickers)

                if len(tickers) >= self.n_tickers * 0.5:
                    logger.info(f"AI zwróciło {len(tickers)} tickerów (próba {attempt})")
                    return AIRunResult(
                        tickers=tickers,
                        strategy=self.strategy,
                        backend=self.backend.name,
                        n_requested=self.n_tickers,
                        n_returned=len(tickers),
                        attempts=attempt,
                        prompt_used=user_prompt,
                        raw_response=raw,
                    )
                else:
                    logger.warning(
                        f"Próba {attempt}: za mało tickerów ({len(tickers)}). "
                        f"Ponawiam z wyższą temperaturą."
                    )
                    time.sleep(1.5 * attempt)

            except Exception as exc:
                last_error = exc
                logger.warning(f"Błąd AI (próba {attempt}/{self.max_retries}): {exc}")
                if attempt < self.max_retries:
                    time.sleep(2 ** attempt)  # exponential backoff

        # Po wyczerpaniu prób – zwróć co mamy (lub pusty wynik)
        if tickers:
            logger.warning(f"Zwracam niepełny wynik: {len(tickers)} tickerów")
        elif last_error:
            raise RuntimeError(
                f"AI ticker source nie powiodło się po {self.max_retries} próbach. "
                f"Ostatni błąd: {last_error}"
            )

        return AIRunResult(
            tickers=tickers, strategy=self.strategy, backend=self.backend.name,
            n_requested=self.n_tickers, n_returned=len(tickers),
            attempts=self.max_retries, prompt_used=user_prompt, raw_response="",
        )

    def _fetch_multi_shot(self) -> list[str]:
        """
        Tryb ekspansji: N zapytań z rosnącą temperaturą → unia tickerów.
        Efekt: szerszy, bardziej zróżnicowany zbiór spółek do zbadania.
        """
        logger.info(
            f"Multi-shot mode: {self.multi_shot_runs} zapytań AI, "
            f"cel: ~{self.n_tickers * self.multi_shot_runs} unikalnych tickerów"
        )
        all_tickers: list[str] = []
        seen: set[str] = set()

        temperatures = [0.2, 0.5, 0.7, 0.85, 1.0][: self.multi_shot_runs]

        for i, temp in enumerate(temperatures, 1):
            logger.info(f"Multi-shot {i}/{self.multi_shot_runs} (temp={temp})")
            try:
                result = self._fetch_single(temperature_override=temp)
                new_tickers = [t for t in result.tickers if t not in seen]
                seen.update(new_tickers)
                all_tickers.extend(new_tickers)
                logger.info(
                    f"  → {len(result.tickers)} zwrócone, "
                    f"{len(new_tickers)} nowe, "
                    f"{len(all_tickers)} łącznie"
                )
                # Krótka przerwa między requestami
                if i < self.multi_shot_runs:
                    time.sleep(1.0)
            except Exception as exc:
                logger.warning(f"Multi-shot {i} zakończony błędem: {exc}")

        logger.info(f"Multi-shot zakończony: {len(all_tickers)} unikalnych tickerów")
        return all_tickers


# ══════════════════════════════════════════════════════════════
# FUNKCJA POMOCNICZA (dla backward compat z ticker_source.py)
# ══════════════════════════════════════════════════════════════

def fetch_ai_tickers(ai_config: dict) -> list[str]:
    """Główna funkcja API modułu – pobierz tickery od AI."""
    source = AITickerSource(ai_config)
    tickers = source.fetch()
    logger.info(f"AI ticker source: {len(tickers)} tickerów z backendu {source.backend.name}")
    return tickers
