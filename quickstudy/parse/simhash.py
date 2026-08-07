"""simhash 近重复检测（ADR-007：去重先用确定性方法，向量只做语义级重叠）。

文档站大量样板重复（每页的安装步骤、版权块），simhash 64 位指纹 + 海明距离 ≤3 判重。
"""
from __future__ import annotations

import re
from collections import Counter

_TOKEN_RE = re.compile(r"[a-zA-Z]{2,}|[一-鿿]")


def _tokens(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def _hash64(token: str) -> int:
    import hashlib

    return int.from_bytes(hashlib.blake2b(token.encode(), digest_size=8).digest(), "big")


def simhash(text: str) -> int:
    bits = [0] * 64
    for token, weight in Counter(_tokens(text)).items():
        h = _hash64(token)
        for i in range(64):
            bits[i] += weight if (h >> i) & 1 else -weight
    fp = 0
    for i, v in enumerate(bits):
        if v > 0:
            fp |= 1 << i
    return fp


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


class DedupIndex:
    """注册页面指纹，返回首个相似页 url（阈值内）。"""

    def __init__(self, threshold: int = 3):
        self.threshold = threshold
        self._fps: dict[int, str] = {}

    def check(self, url: str, text: str) -> str | None:
        fp = simhash(text)
        for other_fp, other_url in self._fps.items():
            if hamming(fp, other_fp) <= self.threshold:
                return other_url
        self._fps[fp] = url
        return None
