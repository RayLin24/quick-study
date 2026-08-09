"""学习路径排序（ADR-002）：侧边栏主干 + LLM 依赖边校正。

- 侧边栏顺序是教学主干：拓扑排序的 tiebreaker 用概念映射页面的最小 sidebar_index
- 依赖图不做纯 DAG 假设：Tarjan 求 SCC → 缩点图拓扑 → SCC 内按侧边栏序
- 依赖违例检测：官方顺序 vs LLM 依赖冲突时记录（报告可查），以依赖边为准调整顺序
"""
from __future__ import annotations

import heapq


def tarjan_scc(n: int, edges: list[tuple[int, int]]) -> list[list[int]]:
    """Tarjan 强连通分量（递归版；概念数为几十~几百量级，深度可控）。"""
    import sys

    sys.setrecursionlimit(max(10000, n * 4 + 100))
    adj: list[list[int]] = [[] for _ in range(n)]
    for a, b in edges:
        adj[a].append(b)
    index_of = [-1] * n
    lowlink = [0] * n
    on_stack = [False] * n
    stack: list[int] = []
    sccs: list[list[int]] = []
    counter = [0]

    def strongconnect(v: int) -> None:
        index_of[v] = lowlink[v] = counter[0]
        counter[0] += 1
        stack.append(v)
        on_stack[v] = True
        for w in adj[v]:
            if index_of[w] < 0:
                strongconnect(w)
                lowlink[v] = min(lowlink[v], lowlink[w])
            elif on_stack[w]:
                lowlink[v] = min(lowlink[v], index_of[w])
        if lowlink[v] == index_of[v]:
            scc = []
            while True:
                w = stack.pop()
                on_stack[w] = False
                scc.append(w)
                if w == v:
                    break
            sccs.append(scc)

    for v in range(n):
        if index_of[v] < 0:
            strongconnect(v)
    return sccs


def order_concepts(concepts: list[dict], depends_edges: list[dict],
                   page_sidebar_index: dict[str, int]) -> tuple[list[int], list[dict]]:
    """概念全序。返回 (有序概念索引列表, 违例记录)。

    违例：依赖边 A→B 但 B 的侧边栏序远早于 A（官方目录认为 B 更基础）——
    以依赖为准重排，但记录供人工审。
    """
    n = len(concepts)

    def sidebar_key(ci: int) -> int:
        idxs = [page_sidebar_index.get(pid, 10 ** 6)
                for pid in concepts[ci].get("pages", [])]
        return min(idxs) if idxs else 10 ** 6

    edges = [(e["from"], e["to"]) for e in depends_edges
             if 0 <= e["from"] < n and 0 <= e["to"] < n and e["from"] != e["to"]]

    # 1) SCC 缩点
    sccs = tarjan_scc(n, edges)
    node_scc = {}
    for si, scc in enumerate(sccs):
        for node in scc:
            node_scc[node] = si

    # 2) 缩点图 + 入度
    dag: dict[int, set[int]] = {}
    indeg: dict[int, int] = {si: 0 for si in range(len(sccs))}
    for a, b in edges:
        sa, sb = node_scc[a], node_scc[b]
        if sa != sb and sb not in dag.get(sa, set()):
            dag.setdefault(sa, set()).add(sb)
            indeg[sb] += 1

    def scc_key(si: int) -> int:
        return min(sidebar_key(n) for n in sccs[si])

    # 3) 带优先级拓扑（堆）：入度 0 的 SCC 里优先弹侧边栏序最小者
    heap = [(scc_key(si), si) for si in range(len(sccs)) if indeg[si] == 0]
    heapq.heapify(heap)
    ordered_sccs: list[int] = []
    while heap:
        _, si = heapq.heappop(heap)
        ordered_sccs.append(si)
        for sb in dag.get(si, ()):
            indeg[sb] -= 1
            if indeg[sb] == 0:
                heapq.heappush(heap, (scc_key(sb), sb))

    # 4) 展开：SCC 内部按侧边栏序
    ordered: list[int] = []
    for si in ordered_sccs:
        ordered.extend(sorted(sccs[si], key=sidebar_key))
    leftovers = [i for i in range(n) if i not in set(ordered)]
    ordered.extend(sorted(leftovers, key=sidebar_key))

    # 5) 违例记录
    violations = []
    for e in depends_edges:
        a, b = e["from"], e["to"]
        if not (0 <= a < n and 0 <= b < n):
            continue
        if sidebar_key(b) < sidebar_key(a) - 5:  # 明显早于才记
            violations.append({"depends": f"{concepts[a]['name']} → {concepts[b]['name']}",
                               "sidebar_order": f"{sidebar_key(a)} vs {sidebar_key(b)}",
                               "resolution": "按依赖边重排（B 提前）",
                               "reason": e.get("reason", "")})
    return ordered, violations
