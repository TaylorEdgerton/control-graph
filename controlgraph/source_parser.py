from __future__ import annotations

from collections import defaultdict
from pathlib import Path
import re
import xml.etree.ElementTree as ET

from .identity import clean_key, infer_identity
from .model import ControlGraph, ControlNode, Evidence, stable_id


DEVICE_TERMS = ("device", "client", "server", "connection", "channel", "ied")
POINT_TERMS = ("point", "protocolpoint", "datasetmember")
POU_TERMS = ("pou", "program", "functionblock", "function")
MAP_TERMS = ("mapping", "map", "assignment", "link", "connectionmap")
NAME_KEYS = ("name", "id", "tagname", "variablename", "pointname", "displayname")
REFERENCE_KEYS = (
    "source",
    "target",
    "from",
    "to",
    "tag",
    "tagname",
    "variable",
    "destination",
    "sourcetag",
    "destinationtag",
    "internaltag",
)


def parse_source(path: str | Path) -> ControlGraph:
    source = Path(path)
    graph = ControlGraph()
    try:
        root = ET.parse(source).getroot()
    except ET.ParseError as exc:
        raise ValueError(f"The source XML is not valid: {exc}") from exc

    element_nodes: dict[int, str] = {}
    records: list[tuple[ET.Element, str, dict[str, str], str, str | None, str | None]] = []
    names: dict[str, list[str]] = defaultdict(list)

    def visit(
        element: ET.Element,
        xpath: str,
        inherited: dict[str, str],
        device_id: str | None,
        pou_id: str | None,
    ) -> None:
        local = _local(element.tag)
        props = _properties(element)
        context = dict(inherited)
        context.update(props)
        kind = _classify(local, props)
        node_id: str | None = None
        name = _name(props, local)
        evidence = Evidence(str(source), xpath, _detail(local, props, element.text))

        if kind:
            node_id = stable_id(kind, str(source.resolve()), xpath, name)
            attributes: dict[str, object] = dict(props)
            if kind == "PROTOCOL_POINT":
                identity = infer_identity(props, inherited)
                if identity:
                    attributes["identity"] = identity
                attributes["direction"] = _direction(props, local)
            node = ControlNode(node_id, kind, name, "SOURCE", attributes, [evidence])
            graph.add_node(node)
            element_nodes[id(element)] = node_id
            names[name.casefold()].append(node_id)
            for key in NAME_KEYS:
                if props.get(key):
                    names[props[key].casefold()].append(node_id)

            if kind == "SOURCE_DEVICE":
                device_id = node_id
                context["device"] = name
            elif kind == "IEC_LOGIC":
                pou_id = node_id
            elif kind == "PROTOCOL_POINT" and device_id:
                graph.add_edge(device_id, node_id, "contains", evidence=[evidence])
            elif kind == "IEC_VARIABLE" and pou_id:
                graph.add_edge(pou_id, node_id, "declares", evidence=[evidence])

        records.append((element, xpath, props, kind or "", device_id, pou_id))
        counts: dict[str, int] = defaultdict(int)
        for child in element:
            child_local = _local(child.tag)
            counts[child_local] += 1
            visit(child, f"{xpath}/{child_local}[{counts[child_local]}]", context, device_id, pou_id)

    root_name = _local(root.tag)
    visit(root, f"/{root_name}[1]", {}, None, None)

    for element, xpath, props, kind, _device_id, pou_id in records:
        evidence = Evidence(str(source), xpath, _detail(_local(element.tag), props, element.text))
        node_id = element_nodes.get(id(element))
        if kind == "PROTOCOL_POINT" and node_id:
            _connect_point(graph, node_id, props, names, evidence)
        if kind == "MAPPING":
            _connect_mapping(graph, props, names, evidence)
        if kind == "IEC_LOGIC" and node_id:
            _connect_structured_text(graph, node_id, _all_text(element), names, evidence)
        elif pou_id and _looks_like_st(_local(element.tag), props):
            _connect_structured_text(graph, pou_id, _all_text(element), names, evidence)

    return graph


def _connect_point(
    graph: ControlGraph,
    point_id: str,
    props: dict[str, str],
    names: dict[str, list[str]],
    evidence: Evidence,
) -> None:
    point = graph.nodes[point_id]
    direction = str(point.attributes.get("direction", "unknown"))
    refs = _references(props)
    for ref in refs:
        for target in names.get(ref.casefold(), []):
            if target == point_id or graph.nodes[target].kind not in {"SOURCE_TAG", "IEC_VARIABLE"}:
                continue
            if direction in {"out", "write", "server"}:
                graph.add_edge(target, point_id, "maps_to_outbound_point", evidence=[evidence])
            else:
                graph.add_edge(point_id, target, "maps_to_source_tag", evidence=[evidence])


def _connect_mapping(
    graph: ControlGraph,
    props: dict[str, str],
    names: dict[str, list[str]],
    evidence: Evidence,
) -> None:
    source = _first_prop(props, ("source", "from", "sourcetag", "sourcepoint"))
    target = _first_prop(props, ("target", "to", "destination", "destinationtag", "targetpoint"))
    if not source or not target:
        return
    for source_id in names.get(source.casefold(), []):
        for target_id in names.get(target.casefold(), []):
            graph.add_edge(source_id, target_id, "configured_mapping", evidence=[evidence])


def _connect_structured_text(
    graph: ControlGraph,
    logic_id: str,
    text: str,
    names: dict[str, list[str]],
    evidence: Evidence,
) -> None:
    if ":=" not in text:
        return
    keywords = {
        "if", "then", "else", "elsif", "end_if", "and", "or", "not", "true", "false",
        "case", "of", "end_case", "for", "while", "do", "end_for", "end_while", "mod",
    }
    for statement in text.split(";"):
        if ":=" not in statement:
            continue
        lhs, rhs = statement.split(":=", 1)
        lhs_tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_.]*", lhs)
        rhs_tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_.]*", rhs)
        if lhs_tokens:
            ref = lhs_tokens[-1]
            for target_id in _lookup_reference(names, ref):
                graph.add_edge(logic_id, target_id, "writes", evidence=[evidence])
        for ref in rhs_tokens:
            if ref.casefold() in keywords:
                continue
            for source_id in _lookup_reference(names, ref):
                graph.add_edge(source_id, logic_id, "reads", evidence=[evidence])


def _lookup_reference(names: dict[str, list[str]], ref: str) -> list[str]:
    direct = names.get(ref.casefold(), [])
    if direct:
        return direct
    return names.get(ref.rsplit(".", 1)[-1].casefold(), [])


def _classify(local: str, props: dict[str, str]) -> str | None:
    key = clean_key(local)
    prop_keys = {clean_key(item) for item in props}
    if any(term in key for term in MAP_TERMS) and any(clean_key(k) in prop_keys for k in REFERENCE_KEYS):
        return "MAPPING"
    if any(term in key for term in POINT_TERMS) or (
        any(item in prop_keys for item in ("index", "pointindex", "register", "nodeid"))
        and not any(term in key for term in DEVICE_TERMS)
    ):
        return "PROTOCOL_POINT"
    if key in POU_TERMS or key.endswith("program") or key.endswith("functionblock"):
        return "IEC_LOGIC"
    if "variable" in key or key in {"var", "inputvar", "outputvar", "localvar"}:
        return "IEC_VARIABLE"
    if ("tag" in key and key not in {"tags", "taglist", "tagdatabase"}) or key in {"globalvar", "globalvariable"}:
        return "SOURCE_TAG"
    if any(term in key for term in DEVICE_TERMS) and key not in {"devices", "connections", "clients", "servers"}:
        return "SOURCE_DEVICE"
    if any(term in key for term in MAP_TERMS):
        return "MAPPING"
    return None


def _properties(element: ET.Element) -> dict[str, str]:
    result = {clean_key(key): str(value).strip() for key, value in element.attrib.items()}
    for child in element:
        if len(child) == 0 and child.text and child.text.strip() and len(child.text.strip()) < 500:
            result.setdefault(clean_key(_local(child.tag)), child.text.strip())
    return result


def _name(props: dict[str, str], fallback: str) -> str:
    for key in NAME_KEYS:
        if props.get(key):
            return props[key]
    return fallback


def _references(props: dict[str, str]) -> set[str]:
    refs: set[str] = set()
    for key in REFERENCE_KEYS:
        value = props.get(clean_key(key), "")
        if value:
            refs.add(value)
    return refs


def _direction(props: dict[str, str], local: str) -> str:
    text = " ".join(
        [local, props.get("direction", ""), props.get("mode", ""), props.get("pointtype", "")]
    ).lower()
    if any(word in text for word in ("output", "outbound", "write", "server")):
        return "out"
    if any(word in text for word in ("input", "inbound", "read", "client")):
        return "in"
    return "unknown"


def _looks_like_st(local: str, props: dict[str, str]) -> bool:
    text = f"{local} {props.get('language', '')}".lower()
    return local in {"st", "body", "implementation", "structuredtext"} or "structuredtext" in text


def _all_text(element: ET.Element) -> str:
    return " ".join(part.strip() for part in element.itertext() if part.strip())


def _detail(local: str, props: dict[str, str], text: str | None) -> str:
    values = ", ".join(f"{key}={value}" for key, value in sorted(props.items()))
    body = (text or "").strip().replace("\n", " ")[:180]
    return f"<{local}> {values or body}".strip()


def _first_prop(props: dict[str, str], keys: tuple[str, ...]) -> str:
    for key in keys:
        if props.get(clean_key(key)):
            return props[clean_key(key)]
    return ""


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].rsplit(":", 1)[-1]
