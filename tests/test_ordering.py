"""排序与大纲校验单测。"""
from quickstudy.writer.ordering import order_concepts, tarjan_scc


def test_tarjan_finds_cycle():
    # 0→1→2→0 构成环，3 独立
    sccs = tarjan_scc(4, [(0, 1), (1, 2), (2, 0)])
    sizes = sorted(len(s) for s in sccs)
    assert sizes == [1, 3]


def test_tarjan_no_edges():
    sccs = tarjan_scc(3, [])
    assert len(sccs) == 3


def _concept(name, pages):
    return {"name": name, "pages": pages, "description": ""}


def test_order_respects_dependency_over_sidebar():
    concepts = [_concept("A 高级", ["p1"]), _concept("B 基础", ["p2"])]
    sidebar = {"p1": 0, "p2": 10}   # 侧栏认为 A 在前
    edges = [{"from": 1, "to": 0, "reason": "A 依赖 B"}]
    ordered, violations = order_concepts(concepts, edges, sidebar)
    assert ordered == [1, 0]        # 依赖优先：B 提前
    assert violations               # 且记录违例


def test_order_uses_sidebar_when_no_dependency():
    concepts = [_concept("A", ["p1"]), _concept("B", ["p2"]), _concept("C", ["p3"])]
    sidebar = {"p1": 5, "p2": 1, "p3": 3}
    ordered, violations = order_concepts(concepts, [], sidebar)
    assert ordered == [1, 2, 0]     # 纯按侧边栏序
    assert not violations


def test_order_breaks_cycle_by_sidebar():
    concepts = [_concept("A", ["p1"]), _concept("B", ["p2"])]
    sidebar = {"p1": 0, "p2": 5}
    edges = [{"from": 0, "to": 1, "reason": ""}, {"from": 1, "to": 0, "reason": ""}]
    ordered, _ = order_concepts(concepts, edges, sidebar)
    assert ordered == [0, 1]        # 环内按侧边栏序
