import anyio
import httpx

from app.main import app


def test_health_endpoint_reports_api_is_alive() -> None:
    async def get_health() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            return await client.get("/health/live")

    response = anyio.run(get_health)

    assert response.status_code == 200
    assert response.json() == {"service": "api", "status": "ok"}
