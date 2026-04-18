"""
db/models.py
Definicja schematu bazy danych SQLAlchemy.

ARCHITEKTURA BAZY DANYCH:
═══════════════════════════════════════════════════════════════
  screening_runs           ← metadane każdego uruchomienia
       │
       ├── metric_snapshots  ← surowe wartości metryk (fundamentalne + techniczne)
       │       (ticker × run × metric_name = wartość)
       │
       ├── screening_results ← które tickery przeszły przez filtry
       │
       └── portfolio_snapshots ← skład portfela (po scoringu i selekcji)

Zalety tego schematu:
  • Pełna historia każdego uruchomienia bez nadpisywania danych
  • Możliwość dodawania nowych metryk bez zmiany schematu (EAV dla metryk)
  • Wydajne zapytania o historię spółki w czasie
  • Analiza stabilności portfela na przestrzeni czasu
═══════════════════════════════════════════════════════════════
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean, Column, DateTime, Float, ForeignKey,
    Index, Integer, String, Text, UniqueConstraint, create_engine,
)
from sqlalchemy.orm import DeclarativeBase, relationship, sessionmaker


class Base(DeclarativeBase):
    pass


# ─────────────────────────────────────────────────────────────
# Tabela 1: Metadane uruchomień
# ─────────────────────────────────────────────────────────────

class ScreeningRun(Base):
    """Każde uruchomienie screenera tworzy jeden rekord w tej tabeli."""
    __tablename__ = "screening_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_timestamp = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    source_index = Column(String(50), nullable=False)          # sp500, wig20, itp.
    total_tickers_fetched = Column(Integer, default=0)
    total_tickers_passed = Column(Integer, default=0)
    fetch_errors_count = Column(Integer, default=0)
    config_snapshot = Column(Text, nullable=True)              # JSON konfiguracji (dla audytu)
    duration_seconds = Column(Float, nullable=True)
    notes = Column(Text, nullable=True)

    # Relacje
    metric_snapshots = relationship("MetricSnapshot", back_populates="run",
                                    cascade="all, delete-orphan")
    screening_results = relationship("ScreeningResult", back_populates="run",
                                     cascade="all, delete-orphan")
    portfolio_snapshots = relationship("PortfolioSnapshot", back_populates="run",
                                       cascade="all, delete-orphan")

    def __repr__(self):
        return (f"<ScreeningRun id={self.id} "
                f"ts={self.run_timestamp:%Y-%m-%d %H:%M} "
                f"source={self.source_index} "
                f"passed={self.total_tickers_passed}>")


# ─────────────────────────────────────────────────────────────
# Tabela 2: Snapshoty metryk (EAV – Entity-Attribute-Value)
# ─────────────────────────────────────────────────────────────

class MetricSnapshot(Base):
    """
    Przechowuje wartość jednej metryki dla jednego tickera w jednym uruchomieniu.
    Model EAV pozwala na dodawanie nowych metryk bez zmiany schematu.
    Indeks (run_id, ticker, metric_name) zapewnia szybkie zapytania.
    """
    __tablename__ = "metric_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(Integer, ForeignKey("screening_runs.id"), nullable=False)
    ticker = Column(String(20), nullable=False)
    metric_name = Column(String(50), nullable=False)
    metric_value = Column(Float, nullable=True)         # NULL = brak danych
    metric_type = Column(String(20), nullable=False)    # 'fundamental' lub 'technical'

    run = relationship("ScreeningRun", back_populates="metric_snapshots")

    __table_args__ = (
        UniqueConstraint("run_id", "ticker", "metric_name",
                         name="uq_run_ticker_metric"),
        Index("ix_metric_ticker_name", "ticker", "metric_name"),
        Index("ix_metric_run_ticker", "run_id", "ticker"),
    )

    def __repr__(self):
        return f"<MetricSnapshot {self.ticker}.{self.metric_name}={self.metric_value}>"


# ─────────────────────────────────────────────────────────────
# Tabela 3: Wyniki screeningu (kto przeszedł filtry)
# ─────────────────────────────────────────────────────────────

class ScreeningResult(Base):
    """
    Ticker, który przeszedł wszystkie filtry w danym uruchomieniu.
    Przechowuje score (ranking) oraz informację o przejściu/odrzuceniu każdego filtra.
    """
    __tablename__ = "screening_results"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(Integer, ForeignKey("screening_runs.id"), nullable=False)
    ticker = Column(String(20), nullable=False)
    score = Column(Float, nullable=True)               # Wynik scoringu
    rank = Column(Integer, nullable=True)              # Pozycja w rankingu
    passed_fundamental = Column(Boolean, default=True)
    passed_technical = Column(Boolean, default=True)
    failed_filters = Column(Text, nullable=True)       # JSON: które filtry nie przeszły
    metric_values_json = Column(Text, nullable=True)   # Kopia kluczowych metryk w JSON

    run = relationship("ScreeningRun", back_populates="screening_results")

    __table_args__ = (
        UniqueConstraint("run_id", "ticker", name="uq_result_run_ticker"),
        Index("ix_result_run_id", "run_id"),
        Index("ix_result_ticker", "ticker"),
        Index("ix_result_score", "score"),
    )

    def __repr__(self):
        return f"<ScreeningResult {self.ticker} score={self.score:.3f} rank=#{self.rank}>"


# ─────────────────────────────────────────────────────────────
# Tabela 4: Skład portfela
# ─────────────────────────────────────────────────────────────

class PortfolioSnapshot(Base):
    """
    Skład portfela inwestycyjnego wygenerowanego po danym uruchomieniu.
    Waga = udział procentowy w portfelu.
    """
    __tablename__ = "portfolio_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(Integer, ForeignKey("screening_runs.id"), nullable=False)
    ticker = Column(String(20), nullable=False)
    weight = Column(Float, nullable=False)             # Udział w portfelu (0.0 – 1.0)
    score = Column(Float, nullable=True)
    rank = Column(Integer, nullable=True)
    stability_score = Column(Float, nullable=True)     # Jak często pojawiał się wcześniej
    is_new_entry = Column(Boolean, default=True)       # Czy nowy vs poprzedni portfel

    run = relationship("ScreeningRun", back_populates="portfolio_snapshots")

    __table_args__ = (
        UniqueConstraint("run_id", "ticker", name="uq_portfolio_run_ticker"),
        Index("ix_portfolio_run_id", "run_id"),
        Index("ix_portfolio_ticker", "ticker"),
    )

    def __repr__(self):
        return f"<Portfolio {self.ticker} w={self.weight:.1%} rank=#{self.rank}>"


# ─────────────────────────────────────────────────────────────
# Fabryka silnika i sesji
# ─────────────────────────────────────────────────────────────

def create_db_engine(db_path: str):
    """
    Utwórz silnik SQLAlchemy dla bazy SQLite.
    WAL mode dla lepszej wydajności przy równoległych zapisach.
    """
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={
            "check_same_thread": False,
            "timeout": 30,
        },
        echo=False,
    )
    # Włącz WAL mode dla SQLite (lepsza wydajność przy równoległych zapisach)
    with engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA journal_mode=WAL")
        conn.exec_driver_sql("PRAGMA synchronous=NORMAL")
        conn.exec_driver_sql("PRAGMA cache_size=10000")
        conn.exec_driver_sql("PRAGMA temp_store=MEMORY")

    Base.metadata.create_all(engine)
    return engine


def get_session_factory(engine):
    """Zwróć fabrykę sesji SQLAlchemy."""
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)
