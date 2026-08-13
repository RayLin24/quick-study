from fastapi import FastAPI

from app.api.routes import auth, chapters, exports, projects, runs, sources
from app.settings import get_settings

settings = get_settings()
app = FastAPI(title=settings.app_name)

app.include_router(auth.router)
app.include_router(projects.router)
app.include_router(sources.router)
app.include_router(runs.router)
app.include_router(chapters.router)
app.include_router(exports.router)


@app.get("/health/live", tags=["health"])
async def health_live() -> dict[str, str]:
    return {"service": "api", "status": "ok"}
