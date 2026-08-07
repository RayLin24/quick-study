"""Playwright 渲染后端（可选依赖）。仅在被 JS 壳站点触发时懒加载。"""
from __future__ import annotations


class PlaywrightRenderer:
    def __init__(self, user_agent: str):
        self.user_agent = user_agent
        self._pw = None
        self._browser = None

    async def start(self) -> None:
        from playwright.async_api import async_playwright

        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(headless=True)

    async def render(self, url: str, wait_ms: int = 3000) -> bytes | None:
        if self._browser is None:
            return None
        page = await self._browser.new_page(user_agent=self.user_agent)
        try:
            await page.goto(url, wait_until="networkidle", timeout=30000)
            await page.wait_for_timeout(wait_ms)
            return (await page.content()).encode()
        finally:
            await page.close()

    async def close(self) -> None:
        if self._browser:
            await self._browser.close()
        if self._pw:
            await self._pw.stop()
