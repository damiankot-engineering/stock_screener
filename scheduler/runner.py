"""
scheduler/runner.py
Moduł harmonogramowania – umożliwia automatyczne, cykliczne uruchamianie screenera.
Używa APScheduler z persistentem SQLite (jobstore) dla odporności na restarty.
"""
from __future__ import annotations

import logging
from typing import Callable

logger = logging.getLogger(__name__)


def start_scheduler(
    run_fn: Callable,
    scheduler_config: dict,
    db_path: str = "screener_jobs.db",
) -> None:
    """
    Uruchom harmonogram na podstawie konfiguracji.

    Args:
        run_fn: Funkcja do wywołania (np. ScreenerPipeline.run)
        scheduler_config: Słownik z kluczami: enabled, frequency, run_at_hour, weekday
        db_path: Ścieżka do bazy danych APScheduler
    """
    try:
        from apscheduler.schedulers.blocking import BlockingScheduler
        from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
        from apscheduler.triggers.cron import CronTrigger
    except ImportError:
        logger.error("APScheduler nie jest zainstalowany. Uruchom: pip install apscheduler")
        raise

    if not scheduler_config.get("enabled", False):
        logger.info("Harmonogram jest wyłączony (scheduler.enabled=false w konfiguracji).")
        return

    frequency = scheduler_config.get("frequency", "weekly")
    hour = scheduler_config.get("run_at_hour", 7)
    weekday = scheduler_config.get("weekday", 0)  # 0 = poniedziałek

    # Konfiguracja triggera
    if frequency == "daily":
        trigger = CronTrigger(hour=hour, minute=0)
        schedule_desc = f"codziennie o {hour:02d}:00"
    elif frequency == "weekly":
        # APScheduler: day_of_week: 0=poniedziałek, 6=niedziela
        day_names = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
        trigger = CronTrigger(day_of_week=day_names[weekday], hour=hour, minute=0)
        schedule_desc = f"w każdy {['pon', 'wt', 'śr', 'czw', 'pt', 'sob', 'nd'][weekday]} o {hour:02d}:00"
    elif frequency == "monthly":
        trigger = CronTrigger(day=1, hour=hour, minute=0)
        schedule_desc = f"1. dnia każdego miesiąca o {hour:02d}:00"
    else:
        logger.error(f"Nieznana częstotliwość: '{frequency}'. Dostępne: daily, weekly, monthly")
        return

    # JobStore w SQLite – persystentny (przeżyje restart)
    jobstores = {
        "default": SQLAlchemyJobStore(url=f"sqlite:///{db_path}")
    }

    scheduler = BlockingScheduler(jobstores=jobstores, timezone="Europe/Warsaw")
    scheduler.add_job(
        run_fn,
        trigger=trigger,
        id="stock_screener_run",
        name="Stock Screener Run",
        replace_existing=True,
        misfire_grace_time=3600,  # 1h okno na nadrobienie nieodebranego zadania
    )

    logger.info(f"Harmonogram uruchomiony: {schedule_desc}")
    logger.info("Wciśnij Ctrl+C, aby zatrzymać.")

    try:
        scheduler.start()
    except KeyboardInterrupt:
        logger.info("Harmonogram zatrzymany przez użytkownika.")
        scheduler.shutdown()


def run_once_and_schedule(run_fn: Callable, scheduler_config: dict) -> None:
    """
    Uruchom raz natychmiast, a następnie kontynuuj wg harmonogramu.
    Przydatne przy pierwszym uruchomieniu.
    """
    logger.info("Uruchamiam jednorazowo przed startem harmonogramu...")
    run_fn()
    start_scheduler(run_fn, scheduler_config)
