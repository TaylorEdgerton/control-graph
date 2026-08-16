from __future__ import annotations

from collections import defaultdict
from typing import Any

from .identity import canonical
from .model import ControlGraph, ControlNode, Evidence, stable_id


IGNITION_PROTOCOL_KINDS = {"OPC_ITEM", "OPC_NODE"}
IGNITION_CONNECTION_KINDS = {"IGNITION_DEVICE", "OPC_SERVER_CONNECTION"}


def resolve(source: ControlGraph, ignition: ControlGraph) -> ControlGraph:
    graph = ControlGraph()
    graph.merge(source)
    graph.merge(ignition)

    ignition_sources: dict[str, list[str]] = defaultdict(list)
    for node in graph.nodes.values():
        if node.kind not in IGNITION_PROTOCOL_KINDS:
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
            connection_id = _ignition_connection(graph, source_id)
            target = connection_id or source_id
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
            source_device_id = _point_device(graph, point.id)
            if source_device_id and connection_id:
                _add_device_connection_match(
                    graph,
                    source_device_id,
                    connection_id,
                    point.id,
                    source_id,
                    key,
                    evidence,
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

    for source in [
        node for node in list(graph.nodes.values())
        if node.kind in IGNITION_PROTOCOL_KINDS
    ]:
        if source.id not in matched_sources:
            key = canonical(_identity(source))
            _add_issue(
                graph,
                source,
                "No source protocol point has this communication identity" if key else "The OPC source has no complete communication identity",
                "unresolved",
                key=key,
            )
    return graph


def _identity(node: ControlNode) -> dict[str, Any]:
    value = node.attributes.get("identity", {})
    return value if isinstance(value, dict) else {}


def _ignition_connection(graph: ControlGraph, source_id: str) -> str | None:
    for edge in graph.edges.values():
        if (
            edge.target == source_id
            and edge.kind == "provides"
            and graph.nodes[edge.source].kind in IGNITION_CONNECTION_KINDS
        ):
            return edge.source
    return None


def _point_device(graph: ControlGraph, point_id: str) -> str | None:
    for edge in graph.edges.values():
        if (
            edge.target == point_id
            and edge.kind == "contains"
            and graph.nodes[edge.source].kind == "SOURCE_DEVICE"
        ):
            return edge.source
    return None


def _add_device_connection_match(
    graph: ControlGraph,
    source_device_id: str,
    ignition_connection_id: str,
    point_id: str,
    source_id: str,
    identity_key: str,
    evidence: list[Evidence],
) -> None:
    edge_id = stable_id(
        "edge", source_device_id, ignition_connection_id, "device_connection_match", "resolved"
    )
    attributes = {} if edge_id in graph.edges else {
        "matchedPoints": [],
        "matchedSources": [],
        "identityKeys": [],
    }
    edge = graph.add_edge(
        source_device_id,
        ignition_connection_id,
        "device_connection_match",
        status="resolved",
        attributes=attributes,
        evidence=[
            *evidence,
            Evidence(
                "resolver",
                f"{source_device_id}->{ignition_connection_id}",
                "At least one protocol point maps through this configured Ignition connection",
            ),
        ],
    )
    if edge is None:
        return
    edge.attributes.setdefault("matchedPoints", [])
    edge.attributes.setdefault("matchedSources", [])
    edge.attributes.setdefault("identityKeys", [])
    _append_unique(edge.attributes["matchedPoints"], point_id)
    _append_unique(edge.attributes["matchedSources"], source_id)
    _append_unique(edge.attributes["identityKeys"], identity_key)
    edge.attributes["matchedPointCount"] = len(edge.attributes["matchedPoints"])


def _append_unique(values: list[str], value: str) -> None:
    if value not in values:
        values.append(value)


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
