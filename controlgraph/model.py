from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
from typing import Any, Iterable


def stable_id(kind: str, *parts: object) -> str:
    raw = "\x1f".join(str(part) for part in parts)
    digest = hashlib.sha1(raw.encode("utf-8"), usedforsecurity=False).hexdigest()[:12]
    return f"{kind.lower()}:{digest}"


@dataclass(frozen=True)
class Evidence:
    source: str
    location: str
    detail: str


@dataclass
class ControlNode:
    id: str
    kind: str
    name: str
    system: str
    attributes: dict[str, Any] = field(default_factory=dict)
    evidence: list[Evidence] = field(default_factory=list)


@dataclass
class ControlEdge:
    id: str
    source: str
    target: str
    kind: str
    status: str = "resolved"
    attributes: dict[str, Any] = field(default_factory=dict)
    evidence: list[Evidence] = field(default_factory=list)


class ControlGraph:
    """A small deterministic in-memory graph."""

    def __init__(self) -> None:
        self.nodes: dict[str, ControlNode] = {}
        self.edges: dict[str, ControlEdge] = {}

    def add_node(self, node: ControlNode) -> ControlNode:
        current = self.nodes.get(node.id)
        if current is None:
            self.nodes[node.id] = node
            return node
        current.attributes.update({k: v for k, v in node.attributes.items() if v not in (None, "")})
        current.evidence = _unique_evidence([*current.evidence, *node.evidence])
        return current

    def add_edge(
        self,
        source: str,
        target: str,
        kind: str,
        *,
        status: str = "resolved",
        attributes: dict[str, Any] | None = None,
        evidence: Iterable[Evidence] = (),
    ) -> ControlEdge | None:
        if source == target or source not in self.nodes or target not in self.nodes:
            return None
        edge_id = stable_id("edge", source, target, kind, status)
        edge = ControlEdge(
            id=edge_id,
            source=source,
            target=target,
            kind=kind,
            status=status,
            attributes=attributes or {},
            evidence=_unique_evidence(list(evidence)),
        )
        current = self.edges.get(edge_id)
        if current:
            current.evidence = _unique_evidence([*current.evidence, *edge.evidence])
            current.attributes.update(edge.attributes)
            return current
        self.edges[edge_id] = edge
        return edge

    def merge(self, other: "ControlGraph") -> None:
        for node in other.nodes.values():
            self.add_node(node)
        for edge in other.edges.values():
            self.add_edge(
                edge.source,
                edge.target,
                edge.kind,
                status=edge.status,
                attributes=edge.attributes,
                evidence=edge.evidence,
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodes": [asdict(node) for node in sorted(self.nodes.values(), key=lambda n: n.id)],
            "edges": [asdict(edge) for edge in sorted(self.edges.values(), key=lambda e: e.id)],
            "summary": self.summary(),
        }

    def summary(self) -> dict[str, Any]:
        kinds: dict[str, int] = {}
        for node in self.nodes.values():
            kinds[node.kind] = kinds.get(node.kind, 0) + 1
        statuses: dict[str, int] = {}
        for edge in self.edges.values():
            statuses[edge.status] = statuses.get(edge.status, 0) + 1
        return {
            "nodeCount": len(self.nodes),
            "edgeCount": len(self.edges),
            "nodeKinds": dict(sorted(kinds.items())),
            "edgeStatuses": dict(sorted(statuses.items())),
        }


def _unique_evidence(items: list[Evidence]) -> list[Evidence]:
    seen: set[tuple[str, str, str]] = set()
    result: list[Evidence] = []
    for item in items:
        key = (item.source, item.location, item.detail)
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result
