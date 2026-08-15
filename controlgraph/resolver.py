from __future__ import annotations

from collections import defaultdict
from typing import Any

from .identity import canonical
from .model import ControlGraph, ControlNode, Evidence, stable_id


def resolve(sel: ControlGraph, ignition: ControlGraph) -> ControlGraph:
    graph = ControlGraph()
    graph.merge(sel)
    graph.merge(ignition)

    ignition_sources: dict[str, list[str]] = defaultdict(list)
    for node in graph.nodes.values():
        if node.kind != "OPC_ITEM":
            continue
        key = canonical(_identity(node))
        if key:
            ignition_sources[key].append(node.id)

    matched_sources: set[str] = set()
    for point in list(graph.nodes.values()):
        if point.kind != "PROTOCOL_POINT":
            continue
        if str(point.attributes.get("direction", "unknown")) == "in":
            continue
        identity = _identity(point)
        key = canonical(identity)
        if not key:
            _add_issue(graph, point, "The protocol point has no complete communication identity", "unresolved")
            continue
        candidates = ignition_sources.get(key, [])
        if len(candidates) == 1:
            source_id = candidates[0]
            matched_sources.add(source_id)
            device_id = _source_device(graph, source_id)
            target = device_id or source_id
            evidence = [
                *point.evidence,
                *graph.nodes[source_id].evidence,
                Evidence("resolver", key, "The normalized communication identities are equal"),
            ]
            graph.add_edge(
                point.id,
                target,
                "communication_identity_match",
                status="resolved",
                attributes={"identityKey": key, "matchedSource": source_id},
                evidence=evidence,
            )
        elif len(candidates) > 1:
            _add_issue(
                graph,
                point,
                f"The communication identity matches {len(candidates)} Ignition sources",
                "ambiguous",
                candidates,
                key,
            )
        else:
            _add_issue(graph, point, "No Ignition source has this communication identity", "unresolved", key=key)

    for source in [node for node in list(graph.nodes.values()) if node.kind == "OPC_ITEM"]:
        if source.id not in matched_sources:
            key = canonical(_identity(source))
            _add_issue(
                graph,
                source,
                "No SEL protocol point has this communication identity" if key else "The OPC item has no complete communication identity",
                "unresolved",
                key=key,
            )
    return graph


def _identity(node: ControlNode) -> dict[str, Any]:
    value = node.attributes.get("identity", {})
    return value if isinstance(value, dict) else {}


def _source_device(graph: ControlGraph, source_id: str) -> str | None:
    for edge in graph.edges.values():
        if edge.target == source_id and edge.kind == "provides" and graph.nodes[edge.source].kind == "IGNITION_DEVICE":
            return edge.source
    return None


def _add_issue(
    graph: ControlGraph,
    subject: ControlNode,
    message: str,
    status: str,
    candidates: list[str] | None = None,
    key: str = "",
) -> None:
    issue_id = stable_id("mapping_issue", subject.id, status, message)
    attrs: dict[str, Any] = {"status": status, "subject": subject.id}
    if candidates:
        attrs["candidates"] = candidates
    if key:
        attrs["identityKey"] = key
    evidence = [*subject.evidence, Evidence("resolver", key or subject.id, message)]
    graph.add_node(ControlNode(issue_id, "MAPPING_ISSUE", message, "RESOLVER", attrs, evidence))
    graph.add_edge(subject.id, issue_id, "has_mapping_issue", status=status, evidence=evidence)
