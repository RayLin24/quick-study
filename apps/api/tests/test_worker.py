from app.worker import celery_app, health


def test_worker_exposes_reusable_health_task() -> None:
    assert health.name == "app.worker.health"
    assert health.run() == {"service": "worker", "status": "ok"}
    assert celery_app.conf.broker_url.startswith("redis://")
