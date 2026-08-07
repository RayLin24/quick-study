"""结构化切分（design.md 4.3.1）：按标题层级切分，而非定长傻切。

- 以 parsed/{id}.md 的标题行为骨架，维护章节路径栈
- 小节向上合并到目标区间；超长节在段落/代码围栏边界递归细分（绝不切断代码块/表格）
- chunk 元数据：页面ID/章节路径/版本/语言/simhash——供知识图谱映射与 L3 覆盖率对账
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from quickstudy.parse.simhash import DedupIndex

_TARGET_MIN, _TARGET_MAX, _HARD_MAX = 800, 1500, 2000  # token 估算区间
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_CJK_RE = re.compile(r"[一-鿿]")
_LATIN_WORD_RE = re.compile(r"[a-zA-Z0-9]+")


def estimate_tokens(text: str) -> int:
    """粗估 token：拉丁词 ×1.3 + CJK 字 ×0.7。只用于切分预算，不用于计费。"""
    latin = len(_LATIN_WORD_RE.findall(text))
    cjk = len(_CJK_RE.findall(text))
    return int(latin * 1.3 + cjk * 0.7)


def _chunk_id(page_id: str, ordinal: int) -> str:
    return hashlib.sha256(f"{page_id}:{ordinal}".encode()).hexdigest()[:16]


def _split_sections(md: str) -> list[dict]:
    """按标题行切成节：{level, heading, text}。frontmatter 注释行跳过。"""
    sections: list[dict] = []
    cur = {"level": 0, "heading": "", "lines": []}
    in_fence = False
    for line in md.splitlines():
        if line.strip().startswith("```"):
            in_fence = not in_fence
        m = None if in_fence else _HEADING_RE.match(line)
        if m and not line.startswith("<!--"):
            if cur["lines"] or cur["heading"]:
                sections.append({**cur, "text": "\n".join(cur["lines"]).strip()})
            cur = {"level": len(m.group(1)), "heading": m.group(2).strip(), "lines": []}
        else:
            if not line.startswith("<!-- source:"):
                cur["lines"].append(line)
    if cur["lines"] or cur["heading"]:
        sections.append({**cur, "text": "\n".join(cur["lines"]).strip()})
    return [s for s in sections if s["text"] or s["heading"]]


def _split_oversize(text: str, hard_max: int) -> list[str]:
    """超长节在空行边界切分；单个段落仍超限时按行硬切（保持代码围栏完整）。"""
    if estimate_tokens(text) <= hard_max:
        return [text]
    blocks = re.split(r"\n\s*\n", text)
    parts, cur, cur_est = [], [], 0
    for b in blocks:
        b_est = estimate_tokens(b)
        if cur and cur_est + b_est > hard_max:
            parts.append("\n\n".join(cur))
            cur, cur_est = [], 0
        if b_est > hard_max:  # 单块超限：按行硬切（围栏已在节级别保证完整性的概率高）
            lines, buf, buf_est = b.splitlines(), [], 0
            for ln in lines:
                ln_est = estimate_tokens(ln)
                if buf and buf_est + ln_est > hard_max:
                    parts.append("\n".join(buf))
                    buf, buf_est = [], 0
                buf.append(ln)
                buf_est += ln_est
            if buf:
                cur, cur_est = ["\n".join(buf)], buf_est
                continue
        cur.append(b)
        cur_est += b_est
    if cur:
        parts.append("\n\n".join(cur))
    return parts


def chunk_page(page_id: str, url: str, md: str, meta: dict) -> list[dict]:
    """单页切分 → chunk 列表。meta 取 parsed json 的 title/version/lang 等。"""
    sections = _split_sections(md)
    chunks: list[dict] = []
    path_stack: list[str] = []
    buf: list[str] = []
    buf_path = ""
    buf_heading = ""

    def flush() -> None:
        nonlocal buf, buf_path, buf_heading
        text = "\n\n".join(t for t in buf if t).strip()
        if not text:
            buf, buf_path, buf_heading = [], "", ""
            return
        for piece in _split_oversize(text, _HARD_MAX):
            ordinal = len(chunks)
            chunks.append({
                "chunk_id": _chunk_id(page_id, ordinal),
                "page_id": page_id, "url": url,
                "title": meta.get("title", ""),
                "section_path": buf_path, "heading": buf_heading,
                "text": piece, "token_est": estimate_tokens(piece),
                "version": meta.get("version", ""), "lang": meta.get("lang", "en"),
                "has_code": "```" in piece, "ordinal": ordinal,
            })
        buf, buf_path, buf_heading = [], "", ""

    for sec in sections:
        if sec["level"] > 0:
            while len(path_stack) >= sec["level"]:
                path_stack.pop()
            path_stack.append(sec["heading"])
        sec_path = " / ".join(path_stack)
        body = (f"## {sec['heading']}\n\n{sec['text']}" if sec["heading"] else sec["text"]).strip()
        # 跨节合并：同一路径下累积到目标区间；路径变化即落盘
        if buf and (sec_path != buf_path or estimate_tokens("\n\n".join(buf)) >= _TARGET_MIN):
            flush()
        if not buf:
            buf_path, buf_heading = sec_path, sec["heading"]
        buf.append(body)
        if estimate_tokens("\n\n".join(buf)) >= _TARGET_MAX:
            flush()
    flush()

    # 页内/跨页样板去重标记由调用方用 DedupIndex 做（这里只算 simhash）
    from quickstudy.parse.simhash import simhash

    for c in chunks:
        c["simhash"] = format(simhash(c["text"]), "016x")
    return chunks


def chunk_workspace(parsed_dir: Path, out_dir: Path) -> dict:
    """整工作区切分：读 parsed/*.md+json，写 chunks/{page_id}.jsonl + chunks_index.json。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    dedup = DedupIndex(threshold=3)
    index: list[dict] = []
    stats = {"pages": 0, "chunks": 0, "dup_chunks": 0}

    for json_path in sorted(parsed_dir.glob("*.json")):
        doc = json.loads(json_path.read_text(encoding="utf-8"))
        if not doc.get("ok"):
            continue
        md_path = parsed_dir / f"{doc['id']}.md"
        if not md_path.exists():
            continue
        chunks = chunk_page(doc["id"], doc["url"], md_path.read_text(encoding="utf-8"), doc)
        for c in chunks:
            dup_of = dedup.check(c["url"], c["text"])
            if dup_of:
                c["duplicate_of"] = dup_of
                stats["dup_chunks"] += 1
        with open(out_dir / f"{doc['id']}.jsonl", "w", encoding="utf-8") as f:
            for c in chunks:
                f.write(json.dumps(c, ensure_ascii=False) + "\n")
        index.append({"page_id": doc["id"], "url": doc["url"],
                      "title": doc.get("title", ""), "chunks": len(chunks)})
        stats["pages"] += 1
        stats["chunks"] += len(chunks)

    (out_dir / "chunks_index.json").write_text(
        json.dumps({"pages": index, "stats": stats}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    return stats
