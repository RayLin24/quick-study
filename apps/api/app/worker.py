from celery import Celery

from app.settings import get_settings

settings = get_settings()
celery_app = Celery(
    "quickstudy",
    broker=settings.redis_url,
    backend=settings.redis_url,
)
celery_app.conf.update(
    accept_content=["json"],
    task_serializer="json",
    result_serializer="json",
    timezone="UTC",
)


@celery_app.task(name="app.worker.health")
def health() -> dict[str, str]:
    return {"service": "worker", "status": "ok"}


from app.workflows import tasks as _tutorial_tasks  # noqa: E402, F401
