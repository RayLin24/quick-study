from __future__ import annotations

from collections import defaultdict


def in_degree_order(count: int, edges: list[dict]) -> list[int]:
    indeg = [0] * count
    outgoing = defaultdict(list)
    for edge in edges or []:
        src, dst = edge.get("from"), edge.get("to")
        if isinstance(src, int) and isinstance(dst, int) and 0 <= src < count and 0 <= dst < count:
            indeg[dst] += 1
            outgoing[src].append(dst)
    ready = [i for i, deg in enumerate(indeg) if deg == 0]
    order = []
    while ready:
        node = ready.pop(0)
        order.append(node)
        for nxt in outgoing[node]:
            indeg[nxt] -= 1
            if indeg[nxt] == 0:
                ready.append(nxt)
    for i in range(count):
        if i not in order:
            order.append(i)
    return order


def pagerank_order(count: int, edges: list[dict], *, damping: float = 0.85, rounds: int = 20) -> list[int]:
    if count <= 0:
        return []
    score = [1.0 / count] * count
    inbound = defaultdict(list)
    outdeg = [0] * count
    for edge in edges or []:
        src, dst = edge.get("from"), edge.get("to")
        if isinstance(src, int) and isinstance(dst, int) and 0 <= src < count and 0 <= dst < count:
            inbound[dst].append(src)
            outdeg[src] += 1
    for _ in range(rounds):
        nxt = [(1 - damping) / count] * count
        for node in range(count):
            for src in inbound[node]:
                denom = outdeg[src] or 1
                nxt[node] += damping * score[src] / denom
        score = nxt
    ranked = sorted(range(count), key=lambda i: (-score[i], i))
    return ranked
