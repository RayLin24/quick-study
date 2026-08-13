from fastapi import FastAPI

from app.settings import get_settings

settings = get_settings()
app = FastAPI(title=settings.app_name)


@app.get("/health/live", tags=["health"])
async def health_live() -> dict[str, str]:
    return {"service": "api", "status": "ok"}
