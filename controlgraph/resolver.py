from __future__ import annotations

from collections import defaultdict
import ipaddress
from typing import Any

from .identity import canonical, opc_node_display_name
from .model import ControlGraph, ControlNode, Evidence, stable_id


IGNITION_PROTOCOL_KINDS = {"OPC_ITEM", "OPC_NODE"}
IGNITION_CONNECTION_KINDS = {"IGNITION_DEVICE", "OPC_SERVER_CONNECTION"}


def resolve(source: ControlGraph, ignition: ControlGraph) -> ControlGraph:
    graph = ControlGraph()
    graph.merge(source)
    graph.merge(ignition)
    connection_matches = _resolve_opc_connections(graph)

    connections = _connections_by_source(graph)
    ignition_sources: dict[str, list[str]] = defaultdict(list)
    scoped_sources: dict[tuple[str, str], list[str]] = defaultdict(list)
    for node in graph.nodes.values():
        if node.kind not in IGNITION_PROTOCOL_KINDS:
            continue
        for key in _identity_keys(_identity(node)):
            if node.id not in ignition_sources[key]:
                ignition_sources[key].append(node.id)
        connection_id = connections.get(node.id)
        if connection_id:
            for key in _symbol_keys(_identity(node), node.name):
                if node.id not in scoped_sources[(connection_id, key)]:
                    scoped_sources[(connection_id, key)].append(node.id)

    devices = _devices_by_point(graph)
    matched_sources: set[str] = set()
    for point in list(graph.nodes.values()):
        if point.kind != "PROTOCOL_POINT":
            continue
        if str(point.attributes.get("direction", "unknown")) == "in":
            continue
        identity = _identity(point)
        keys = _identity_keys(identity)
        key = keys[0] if keys else ""
        source_device_id = devices.get(point.id)
        scoped_connection_id = connection_matches.get(source_device_id or "")
        candidates: list[str] = []
        mapping_source = "POINT_IDENTITY"
        for candidate_key in keys:
            candidate_ids = ignition_sources.get(candidate_key, [])
            if scoped_connection_id:
                candidate_ids = [
                    candidate
                    for candidate in candidate_ids
                    if connections.get(candidate) == scoped_connection_id
                ]
            if candidate_ids:
                key = candidate_key
                candidates = candidate_ids
                break
        if not candidates and scoped_connection_id:
            # The endpoint already paired the two connections, so the host is scope, not identity:
            # inside that pair a symbol path is enough (an RTAC export has no OPC UA node id).
            for candidate_key in _symbol_keys(identity, point.name):
                candidate_ids = scoped_sources.get((scoped_connection_id, candidate_key), [])
                if candidate_ids:
                    key = candidate_key
                    candidates = candidate_ids
                    mapping_source = "SYMBOL_PATH"
                    break
        if not candidates and not keys:
            _add_issue(graph, point, "The protocol point has no complete communication identity", "unresolved")
            continue
        if len(candidates) == 1:
            source_id = candidates[0]
            matched_sources.add(source_id)
            connection_id = connections.get(source_id)
            target = connection_id or source_id
            evidence = [
                *point.evidence,
                *graph.nodes[source_id].evidence,
                Evidence(
                    "resolver",
                    key,
                    "The symbol paths are equal inside the connection matched by endpoint"
                    if mapping_source == "SYMBOL_PATH"
                    else "The normalized communication identities are equal",
                ),
            ]
            graph.add_edge(
                point.id,
                target,
                "communication_identity_match",
                status="resolved",
                attributes={
                    "identityKey": key,
                    "matchedSource": source_id,
                    "mappingSource": mapping_source,
                },
                evidence=evidence,
            )
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


def _resolve_opc_connections(graph: ControlGraph) -> dict[str, str]:
    source_connections = [
        node for node in graph.nodes.values()
        if node.kind == "SOURCE_DEVICE" and _endpoint_identity(node)
    ]
    ignition_connections = [
        node for node in graph.nodes.values()
        if node.kind == "OPC_SERVER_CONNECTION" and _endpoint_identity(node)
    ]
    matches: dict[str, str] = {}
    for source_connection in source_connections:
        source_endpoint = _endpoint_identity(source_connection)
        candidates: list[tuple[int, ControlNode, str]] = []
        for ignition_connection in ignition_connections:
            target_endpoint = _endpoint_identity(ignition_connection)
            score, reason = _endpoint_match(source_endpoint, target_endpoint)
            if score:
                candidates.append((score, ignition_connection, reason))
        if not candidates:
            continue
        candidates.sort(key=lambda item: (-item[0], item[1].name.casefold(), item[1].id))
        best_score = candidates[0][0]
        best = [candidate for candidate in candidates if candidate[0] == best_score]
        if len(best) != 1:
            _add_issue(
                graph,
                source_connection,
                f"The OPC UA endpoint matches {len(best)} Ignition connections",
                "ambiguous",
                [candidate[1].id for candidate in best],
                _endpoint_key(source_endpoint),
            )
            continue
        score, ignition_connection, reason = best[0]
        matches[source_connection.id] = ignition_connection.id
        evidence = [
            *source_connection.evidence,
            *ignition_connection.evidence,
            Evidence(
                "resolver",
                _endpoint_key(source_endpoint),
                f"The OPC UA connection endpoints match by {reason}",
            ),
        ]
        edge = graph.add_edge(
            source_connection.id,
            ignition_connection.id,
            "device_connection_match",
            status="resolved",
            attributes={
                "mappingSource": "ENDPOINT_IDENTITY",
                "confidence": score,
                "matchEvidence": reason,
                "sourceEndpoint": _endpoint_key(source_endpoint),
                "ignitionEndpoint": _endpoint_key(_endpoint_identity(ignition_connection)),
                "matchedPoints": [],
                "matchedSources": [],
                "identityKeys": [],
                "matchedPointCount": 0,
            },
            evidence=evidence,
        )
        if edge:
            ignition_connection.attributes["sourceConnectionMatch"] = source_connection.id
            ignition_connection.attributes["connectionMatchConfidence"] = score
            ignition_connection.attributes["connectionMatchEvidence"] = reason
    return matches


def _endpoint_identity(node: ControlNode) -> dict[str, Any]:
    value = node.attributes.get("connectionIdentity", {})
    return value if isinstance(value, dict) else {}


def _endpoint_match(
    source: dict[str, Any],
    target: dict[str, Any],
) -> tuple[int, str]:
    if str(source.get("port", "")) != str(target.get("port", "")):
        return 0, ""
    source_hosts = _endpoint_hosts(source)
    target_hosts = _endpoint_hosts(target)
    shared_hosts = source_hosts & target_hosts
    if not shared_hosts:
        return 0, ""
    exact_host = str(source.get("host", "")).casefold() == str(target.get("host", "")).casefold()
    address_match = any(_is_ip_address(host) for host in shared_hosts)
    if exact_host and address_match:
        return 100, "IP address and OPC UA port"
    if exact_host:
        return 95, "hostname and OPC UA port"
    return 90, "configured host alias and OPC UA port"


def _endpoint_hosts(identity: dict[str, Any]) -> set[str]:
    aliases = identity.get("hostAliases", [])
    return {
        str(value).strip().casefold().rstrip(".")
        for value in [identity.get("host", ""), *(aliases if isinstance(aliases, list) else [])]
        if str(value).strip()
    }


def _endpoint_key(identity: dict[str, Any]) -> str:
    host = str(identity.get("host", ""))
    port = str(identity.get("port", ""))
    return f"opc.tcp://{host}:{port}" if host and port else ""


def _is_ip_address(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True


def _identity(node: ControlNode) -> dict[str, Any]:
    value = node.attributes.get("identity", {})
    return value if isinstance(value, dict) else {}


def _identity_keys(identity: dict[str, Any]) -> list[str]:
    keys: list[str] = []
    primary = canonical(identity)
    if primary:
        keys.append(primary)
    if str(identity.get("kind", "")).casefold() in {"opc", "opcua"} and identity.get("host"):
        name_identity = dict(identity)
        name_identity.pop("host", None)
        name_identity.pop("port", None)
        alternate = canonical(name_identity)
        if alternate and alternate not in keys:
            keys.append(alternate)
    return keys


def _symbol_keys(identity: dict[str, Any], name: str) -> list[str]:
    """Connection-scoped fallback keys: the IEC symbol path, however each side spells it."""
    if str(identity.get("kind", "")).casefold() not in {"opc", "opcua", ""}:
        return []
    keys: list[str] = []
    for value in (
        identity.get("displayName", ""),
        identity.get("iecPath", ""),
        identity.get("identifier", ""),
        identity.get("nodeid", ""),
        name,
    ):
        text = str(value).strip()
        if not text:
            continue
        key = opc_node_display_name(text.rsplit(";", 1)[-1].split("=", 1)[-1]).casefold()
        if key and key not in keys:
            keys.append(key)
    return keys


def _connections_by_source(graph: ControlGraph) -> dict[str, str]:
    return {
        edge.target: edge.source
        for edge in graph.edges.values()
        if edge.kind == "provides" and graph.nodes[edge.source].kind in IGNITION_CONNECTION_KINDS
    }


def _devices_by_point(graph: ControlGraph) -> dict[str, str]:
    return {
        edge.target: edge.source
        for edge in graph.edges.values()
        if edge.kind == "contains" and graph.nodes[edge.source].kind == "SOURCE_DEVICE"
    }


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
    edge.attributes.setdefault("mappingSource", "POINT_IDENTITY")
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
