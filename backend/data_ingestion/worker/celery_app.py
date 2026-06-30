"""
Celery application factory.

Broker  → Redis DB 1  (task queue)
Backend → Redis DB 2  (result storage)

On Windows run workers with:
    celery -A data_ingestion.worker.celery_app worker --pool=solo --loglevel=info
"""
from __future__ import annotations

from celery import Celery
from celery.signals import worker_process_init

from data_ingestion.config.settings import get_settings


@worker_process_init.connect
def _init_worker_logging(**_kwargs) -> None:
    from data_ingestion.config.logging_setup import configure_service_file_loggers

    configure_service_file_loggers()


def _make_celery() -> Celery:
    s = get_settings()
    app = Celery(
        "ftth_worker",
        broker=s.celery_broker_url,
        backend=s.celery_result_backend,
        include=["data_ingestion.worker.tasks"],
    )
    app.conf.update(
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        timezone="UTC",
        enable_utc=True,
        # Retry on connection errors
        broker_connection_retry_on_startup=True,
        # Task time limits
        task_soft_time_limit=3600,   # 1h soft — raises SoftTimeLimitExceeded
        task_time_limit=7200,        # 2h hard kill
        # Windows requires solo pool (no fork())
        worker_pool="solo",
        # Keep results for 24 h then expire
        result_expires=86400,
    )
    return app


celery_app: Celery = _make_celery()
