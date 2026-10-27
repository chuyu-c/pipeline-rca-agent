"""In-memory lineage graph: traversal is table-level; column edges are carried along."""

from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from rca.lineage.parser import Edge
from rca.simulator.dag import toposort

Direction = Literal["upstream", "downstream"]


@dataclass
class Subgraph:
    root: str
    direction: Direction
    depth: int
    hops: dict[str, int]  # table -> distance from root (root = 0)
    edges: list[Edge]  # table- and column-level edges among `hops` tables


class LineageGraph:
    def __init__(self, edges: Iterable[Edge]) -> None:
        self.edges = sorted(set(edges), key=Edge.sort_key)
        self._parents: dict[str, set[str]] = {}
        self._children: dict[str, set[str]] = {}
        for edge in self.edges:
            self._parents.setdefault(edge.dst_table, set())
            self._children.setdefault(edge.dst_table, set())
            if edge.src_table is None:
                continue
            self._parents.setdefault(edge.src_table, set())
            self._children.setdefault(edge.src_table, set())
            if edge.src_table != edge.dst_table:  # self-reads (incremental SQL) aren't dependencies
                self._parents[edge.dst_table].add(edge.src_table)
                self._children[edge.src_table].add(edge.dst_table)

    @property
    def tables(self) -> set[str]:
        return set(self._parents)

    def _check(self, table: str) -> None:
        if table not in self._parents:
            raise KeyError(f"unknown table {table!r}")

    def _bfs(self, table: str, adjacency: dict[str, set[str]], depth: int | None) -> dict[str, int]:
        self._check(table)
        if depth is not None and depth < 1:
            raise ValueError("depth must be >= 1")
        hops = {table: 0}
        queue = deque([table])
        while queue:
            node = queue.popleft()
            if depth is not None and hops[node] == depth:
                continue
            for nxt in sorted(adjacency[node]):
                if nxt not in hops:
                    hops[nxt] = hops[node] + 1
                    queue.append(nxt)
        return hops

    def _subgraph(self, table: str, direction: Direction, depth: int) -> Subgraph:
        adjacency = self._parents if direction == "upstream" else self._children
        hops = self._bfs(table, adjacency, depth)
        # Upstream also shows where the furthest tables are loaded from (external sources).
        external_ok = direction == "upstream"
        edges = [
            e
            for e in self.edges
            if e.dst_table in hops
            and (e.src_table in hops or (e.src_table is None and external_ok))
        ]
        return Subgraph(table, direction, depth, hops, edges)

    def upstream(self, table: str, depth: int = 1) -> Subgraph:
        """Tables `table` reads from, up to `depth` hops, with the edges between them."""
        return self._subgraph(table, "upstream", depth)

    def downstream(self, table: str, depth: int = 1) -> Subgraph:
        """Tables built from `table`, up to `depth` hops, with the edges between them."""
        return self._subgraph(table, "downstream", depth)

    def ancestors(self, table: str) -> set[str]:
        """Every table `table` transitively depends on (excluding itself)."""
        return set(self._bfs(table, self._parents, None)) - {table}

    def descendants(self, table: str) -> set[str]:
        return set(self._bfs(table, self._children, None)) - {table}

    def topo_order(self, tables: Iterable[str] | None = None) -> list[str]:
        """Upstream-first order of all tables, or of the given subset (relative order preserved)."""
        order = toposort({t: ps for t, ps in self._parents.items()})
        if tables is None:
            return order
        wanted = set(tables)
        for table in wanted:
            self._check(table)
        return [t for t in order if t in wanted]

    def outputs(self, task_id: str) -> list[str]:
        """Tables written by `task_id`, upstream-first (empty if the task is unknown)."""
        written = {e.dst_table for e in self.edges if e.task_id == task_id}
        return self.topo_order(written) if written else []

    def producers(self, table: str) -> set[str]:
        """Task ids that write `table`."""
        self._check(table)
        return {e.task_id for e in self.edges if e.dst_table == table}
