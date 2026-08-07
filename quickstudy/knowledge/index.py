"""Embedding 客户端与向量索引（design.md 4.3.2 / 5）。

- Embedder：阿里百炼（DashScope）OpenAI 兼容端点；FakeEmbedder 供离线测试
- 索引：Qdrant 本地嵌入模式（免 Docker，数据落 workspace），接口与远端 Qdrant 一致
"""
from __future__ import annotations

import hashlib
import logging
import os

import httpx

log = logging.getLogger(__name__)


class DashScopeEmbedder:
    """阿里百炼 text-embedding（OpenAI 兼容模式）。key 从 env 读，不落盘。"""

    def __init__(self, model: str = "", base_url: str = "", api_key: str = "",
                 batch_size: int = 20):
        self.model = model or os.environ.get("QUICKSTUDY_EMBED_MODEL", "text-embedding-v4")
        self.base_url = (base_url or os.environ.get(
            "QUICKSTUDY_EMBED_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")).rstrip("/")
        self.api_key = api_key or os.environ.get("DASHSCOPE_API_KEY", "")
        if not self.api_key:
            raise RuntimeError("缺少 DASHSCOPE_API_KEY（阿里百炼 embedding）")
        self.batch_size = batch_size
        self._client = httpx.Client(timeout=60.0)

    @property
    def dim(self) -> int:
        # text-embedding-v4 默认 1024 维（可调），以实际返回为准
        return int(os.environ.get("QUICKSTUDY_EMBED_DIM", "1024"))

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for i in range(0, len(texts), self.batch_size):
            batch = texts[i:i + self.batch_size]
            resp = self._client.post(
                f"{self.base_url}/embeddings",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"model": self.model, "input": batch})
            resp.raise_for_status()
            data = resp.json()["data"]
            vectors.extend(d["embedding"] for d in sorted(data, key=lambda d: d["index"]))
        return vectors


class FakeEmbedder:
    """确定性假向量（token 哈希袋），测试/无 key 环境用。维度小、无语义。"""

    def __init__(self, dim: int = 64):
        self._dim = dim

    @property
    def dim(self) -> int:
        return self._dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for t in texts:
            vec = [0.0] * self._dim
            for token in t.lower().split():
                h = int(hashlib.blake2b(token.encode(), digest_size=4).hexdigest(), 16)
                vec[h % self._dim] += 1.0
            norm = sum(v * v for v in vec) ** 0.5 or 1.0
            out.append([v / norm for v in vec])
        return out


class ChunkIndex:
    """Qdrant 本地索引：chunk 向量 + payload（page_id/url/section_path/version）。"""

    COLLECTION = "chunks"

    def __init__(self, path, dim: int):
        from qdrant_client import QdrantClient
        from qdrant_client.models import Distance, VectorParams

        self.client = QdrantClient(path=str(path))  # 本地嵌入模式，免服务
        self.dim = dim
        existing = {c.name for c in self.client.get_collections().collections}
        if self.COLLECTION not in existing:
            self.client.create_collection(
                collection_name=self.COLLECTION,
                vectors_config=VectorParams(size=dim, distance=Distance.COSINE))

    def upsert_chunks(self, chunks: list[dict], vectors: list[list[float]]) -> int:
        from qdrant_client.models import PointStruct

        points = []
        for c, v in zip(chunks, vectors):
            points.append(PointStruct(
                id=_point_id(c["chunk_id"]), vector=v,
                payload={"chunk_id": c["chunk_id"], "page_id": c["page_id"],
                         "url": c["url"], "section_path": c["section_path"],
                         "version": c.get("version", ""), "title": c.get("title", ""),
                         "text_preview": c["text"][:200]}))
        if points:
            self.client.upsert(collection_name=self.COLLECTION, points=points)
        return len(points)

    def search(self, vector: list[float], k: int = 8) -> list[dict]:
        hits = self.client.query_points(collection_name=self.COLLECTION,
                                        query=vector, limit=k).points
        return [{"score": h.score, **(h.payload or {})} for h in hits]

    def count(self) -> int:
        return self.client.count(collection_name=self.COLLECTION).count


def _point_id(chunk_id: str) -> int:
    """chunk_id（hex16）→ 无符号 63 位整数点 ID（确定性，重跑幂等）。"""
    return int(hashlib.sha256(chunk_id.encode()).hexdigest()[:15], 16)
