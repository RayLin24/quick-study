"""robots.txt 获取与判定：尊重 Disallow 与 Crawl-delay（design.md 4.1 / §9 合规）。"""
from __future__ import annotations

import urllib.robotparser
from dataclasses import dataclass, field
from urllib.parse import urlparse


@dataclass
class RobotsPolicy:
    allowed: bool = True
    crawl_delay: float | None = None
    sitemaps: list[str] = field(default_factory=list)
    error: str = ""


@dataclass
class _OriginRules:
    parser: urllib.robotparser.RobotFileParser | None = None
    crawl_delay: float | None = None
    sitemaps: list[str] = field(default_factory=list)
    error: str = ""
    deny_all: bool = False


class RobotsCache:
    """按源站缓存 robots.txt 规则；can_fetch 按 URL 逐条判定。"""

    def __init__(self, user_agent: str):
        self.user_agent = user_agent
        self._origins: dict[str, _OriginRules] = {}

    async def _rules_for(self, url: str, fetch_text) -> _OriginRules:
        parts = urlparse(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin in self._origins:
            return self._origins[origin]

        rules = _OriginRules()
        try:
            status, text = await fetch_text(f"{origin}/robots.txt")
            if status == 200 and text:
                rp = urllib.robotparser.RobotFileParser()
                rp.parse(text.splitlines())
                rules.parser = rp
                delay = rp.crawl_delay(self.user_agent)
                rules.crawl_delay = float(delay) if delay else None
                # robotparser 不暴露 sitemap 指令，手工扫一遍
                rules.sitemaps = [line.split(":", 1)[1].strip()
                                  for line in text.splitlines()
                                  if line.lower().startswith("sitemap:") and ":" in line]
            elif status in (401, 403):
                rules.deny_all = True
                rules.error = f"robots.txt HTTP {status}，按拒绝抓取处理"
            # 404/5xx/其他：视为无限制
        except Exception as e:  # noqa: BLE001 - robots 获取失败不阻断主流程
            rules.error = f"robots.txt 获取失败: {e}"
        self._origins[origin] = rules
        return rules

    async def policy_for(self, url: str, fetch_text) -> RobotsPolicy:
        """fetch_text: async callable(url) -> (status, text)，由抓取层注入。"""
        rules = await self._rules_for(url, fetch_text)
        allowed = not rules.deny_all
        if allowed and rules.parser is not None:
            allowed = rules.parser.can_fetch(self.user_agent, url)
        return RobotsPolicy(allowed=allowed, crawl_delay=rules.crawl_delay,
                            sitemaps=rules.sitemaps, error=rules.error)
