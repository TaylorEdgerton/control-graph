from __future__ import annotations

from dataclasses import asdict, dataclass, field
import copy
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
        self.audit: dict[str, Any] = {}

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
        _merge_audit(self.audit, other.audit)
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
        audit = copy.deepcopy(self.audit)
        matched_sources: set[str] = set()
        materialized_tags: dict[str, set[str]] = {}
        for edge in self.edges.values():
            if (
                edge.kind == "communication_identity_match"
                and edge.status == "resolved"
                and edge.attributes.get("matchedSource")
            ):
                matched_sources.add(str(edge.attributes["matchedSource"]))
            elif edge.kind == "materializes_as_tag":
                materialized_tags.setdefault(edge.source, set()).add(edge.target)
        resolved_tags: set[str] = set()
        for edge in self.edges.values():
            if edge.kind != "drives" or edge.source not in matched_sources:
                continue
            target = self.nodes.get(edge.target)
            if target and target.kind == "IGNITION_TAG":
                resolved_tags.add(target.id)
            elif target and target.kind == "UDT_MEMBER":
                resolved_tags.update(materialized_tags.get(target.id, set()))
        if audit:
            audit["resolvedTagCount"] = len(resolved_tags)
            audit["unresolvedTagCount"] = max(
                0,
                int(audit.get("opcTagCount", 0)) - len(resolved_tags),
            )
        return {
            "nodeCount": len(self.nodes),
            "edgeCount": len(self.edges),
            "nodeKinds": dict(sorted(kinds.items())),
            "edgeStatuses": dict(sorted(statuses.items())),
            "audit": audit,
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


def _merge_audit(target: dict[str, Any], source: dict[str, Any]) -> None:
    for key, value in source.items():
        if isinstance(value, dict):
            nested = target.setdefault(key, {})
            if isinstance(nested, dict):
                _merge_audit(nested, value)
        elif isinstance(value, int):
            target[key] = int(target.get(key, 0)) + value
        elif key not in target:
            target[key] = copy.deepcopy(value)
