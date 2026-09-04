from __future__ import annotations


def dependency_matrix(count: int, edges: list[dict]) -> list[list[int]]:
    grid = [[0] * count for _ in range(count)]
    for edge in edges or []:
        src, dst = edge.get("from"), edge.get("to")
        if isinstance(src, int) and isinstance(dst, int) and 0 <= src < count and 0 <= dst < count:
            grid[src][dst] += 1
    return grid


def heatmap_markdown(names: list[str], edges: list[dict]) -> str:
    grid = dependency_matrix(len(names), edges)
    header = "| | " + " | ".join(names) + " |"
    sep = "|" + "|".join(["---"] * (len(names) + 1)) + "|"
    rows = [header, sep]
    for i, name in enumerate(names):
        cells = ["█" if grid[i][j] else "·" for j in range(len(names))]
        rows.append("| " + " | ".join([name, *cells]) + " |")
    return "# 章节依赖热力图\n\n" + "\n".join(rows) + "\n"
