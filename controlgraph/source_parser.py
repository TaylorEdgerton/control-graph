from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
import re
import xml.etree.ElementTree as ET

from .identity import clean_key, infer_identity, opc_endpoint_identity
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


@dataclass(frozen=True)
class SourceProjectInfo:
    root_element: str
    project_name: str
    node_count: int
    device_count: int
    protocol_point_count: int

    def to_dict(self) -> dict[str, object]:
        return {
            "importKind": "source",
            "fileType": "Control-device XML project",
            "configurationFormat": "xml",
            "configurationSource": f"XML root: {self.root_element}",
            "rootElement": self.root_element,
            "projectName": self.project_name,
            "sourceNodeCount": self.node_count,
            "sourceDeviceCount": self.device_count,
            "protocolPointCount": self.protocol_point_count,
        }


def inspect_source_project(path: str | Path) -> SourceProjectInfo:
    source = Path(path)
    if not source.exists() or not source.is_file():
        raise ValueError(f"The control-device XML project does not exist: {source}")
    try:
        root = ET.parse(source).getroot()
    except (ET.ParseError, OSError) as exc:
        raise ValueError(f"The control-device project is not valid XML: {exc}") from exc
    graph = parse_source(source)
    if not graph.nodes:
        raise ValueError("The XML file does not contain supported control-device project objects.")
    root_name = _local(root.tag)
    properties = _properties(root)
    project_name = _name(properties, source.stem)
    return SourceProjectInfo(
        root_element=root_name,
        project_name=project_name,
        node_count=len(graph.nodes),
        device_count=len([node for node in graph.nodes.values() if node.kind == "SOURCE_DEVICE"]),
        protocol_point_count=len([
            node for node in graph.nodes.values() if node.kind == "PROTOCOL_POINT"
        ]),
    )


def parse_source(path: str | Path) -> ControlGraph:
    source = Path(path)
    graph = ControlGraph()
    try:
        root = ET.parse(source).getroot()
    except (ET.ParseError, OSError) as exc:
        raise ValueError(f"The source XML is not valid: {exc}") from exc

    element_nodes: dict[int, str] = {}
    records: list[tuple[ET.Element, str, dict[str, str], str, str | None, str | None]] = []
    names: dict[str, list[str]] = defaultdict(list)
    pou_variables: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))

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
        if (
            kind == "SOURCE_DEVICE"
            and clean_key(local) in {"connection", "client", "server", "channel"}
            and not _first_prop(props, NAME_KEYS)
        ):
            kind = None
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
            elif kind == "SOURCE_DEVICE":
                endpoint_identity = opc_endpoint_identity(props)
                if endpoint_identity:
                    attributes["connectionIdentity"] = endpoint_identity
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
                local_name = _first_prop(props, NAME_KEYS) or name
                pou_variables[pou_id][local_name.casefold()].append(node_id)
            elif kind == "SOURCE_TAG" and device_id:
                graph.add_edge(device_id, node_id, "maps_to_internal_tag", evidence=[evidence])

        records.append((element, xpath, props, kind or "", device_id, pou_id))
        counts: dict[str, int] = defaultdict(int)
        for child in element:
            child_local = _local(child.tag)
            counts[child_local] += 1
            visit(child, f"{xpath}/{child_local}[{counts[child_local]}]", context, device_id, pou_id)

    root_name = _local(root.tag)
    visit(root, f"/{root_name}[1]", {}, None, None)

    for element, xpath, _props, _kind, _device_id, pou_id in records:
        if pou_id and _looks_like_st_interface(_local(element.tag)):
            _add_st_declarations(
                graph,
                pou_id,
                _all_text(element),
                names,
                pou_variables[pou_id],
                Evidence(str(source), xpath, _detail(_local(element.tag), {}, element.text)),
            )

    for element, xpath, props, kind, _device_id, pou_id in records:
        evidence = Evidence(str(source), xpath, _detail(_local(element.tag), props, element.text))
        node_id = element_nodes.get(id(element))
        if kind == "PROTOCOL_POINT" and node_id:
            _connect_point(graph, node_id, props, names, evidence)
        if kind == "MAPPING":
            _connect_mapping(graph, props, names, evidence)
        if kind == "IEC_LOGIC" and node_id and not _has_nested_st_body(element):
            _connect_structured_text(
                graph,
                node_id,
                _all_text(element),
                names,
                evidence,
                pou_variables.get(node_id),
            )
        elif pou_id and _looks_like_st(_local(element.tag), props):
            _connect_structured_text(
                graph,
                pou_id,
                _all_text(element),
                names,
                evidence,
                pou_variables.get(pou_id),
            )

    link_source_symbols(graph)
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
    local_names: dict[str, list[str]] | None = None,
) -> None:
    text = _strip_st_comments(text)
    keywords = {
        "if", "then", "else", "elsif", "end_if", "and", "or", "not", "true", "false",
        "case", "of", "end_case", "for", "while", "do", "end_for", "end_while", "mod",
        "program", "function", "function_block", "var", "var_input", "var_output",
        "var_in_out", "var_global", "var_external", "end_var", "end_program",
        "end_function", "end_function_block",
    }
    for statement in text.split(";"):
        assignment = re.match(
            r"\s*([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)\s*:=\s*(.*)$",
            statement,
            re.S,
        )
        if not assignment:
            for ref in re.findall(r"[A-Za-z_][A-Za-z0-9_.]*", statement):
                if ref.casefold() not in keywords:
                    _record_logic_reference(graph, logic_id, "readReferences", ref)
            _connect_st_calls(graph, logic_id, statement, names, evidence)
            continue
        lhs, rhs = assignment.groups()
        _record_logic_reference(graph, logic_id, "writeReferences", lhs)
        rhs_tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_.]*", rhs)
        for target_id in _lookup_reference(names, lhs, local_names):
            if graph.nodes[target_id].kind in {"SOURCE_TAG", "IEC_VARIABLE"}:
                graph.add_edge(logic_id, target_id, "writes", evidence=[evidence])
        for ref in rhs_tokens:
            if ref.casefold() in keywords:
                continue
            _record_logic_reference(graph, logic_id, "readReferences", ref)
            for source_id in _lookup_reference(names, ref, local_names):
                if graph.nodes[source_id].kind in {"SOURCE_TAG", "IEC_VARIABLE"}:
                    graph.add_edge(source_id, logic_id, "reads", evidence=[evidence])
        _connect_st_calls(graph, logic_id, rhs, names, evidence)


def _record_logic_reference(
    graph: ControlGraph,
    logic_id: str,
    attribute: str,
    reference: str,
) -> None:
    values = graph.nodes[logic_id].attributes.setdefault(attribute, [])
    if reference not in values:
        values.append(reference)


def link_source_symbols(graph: ControlGraph) -> ControlGraph:
    """Resolve exact symbolic references after independently imported XML modules are merged."""
    tags: dict[str, list[ControlNode]] = defaultdict(list)
    for node in graph.nodes.values():
        if node.kind == "SOURCE_TAG":
            tags[node.name.casefold()].append(node)
    for logic in list(graph.nodes.values()):
        if logic.kind != "IEC_LOGIC":
            continue
        for attribute, edge_kind, reverse in (
            ("readReferences", "reads", True),
            ("writeReferences", "writes", False),
        ):
            references = logic.attributes.get(attribute, [])
            if not isinstance(references, list):
                continue
            for reference in references:
                for tag in tags.get(str(reference).casefold(), []):
                    evidence = [*tag.evidence, *logic.evidence]
                    if reverse:
                        graph.add_edge(tag.id, logic.id, edge_kind, evidence=evidence)
                    else:
                        graph.add_edge(logic.id, tag.id, edge_kind, evidence=evidence)
    return graph


def _lookup_reference(
    names: dict[str, list[str]],
    ref: str,
    local_names: dict[str, list[str]] | None = None,
) -> list[str]:
    key = ref.casefold()
    root = ref.split(".", 1)[0].casefold()
    leaf = ref.rsplit(".", 1)[-1].casefold()
    for index in (local_names or {}, names):
        for candidate in (key, root, leaf):
            direct = index.get(candidate, [])
            if direct:
                return direct
    return []


def _add_st_declarations(
    graph: ControlGraph,
    logic_id: str,
    text: str,
    names: dict[str, list[str]],
    local_names: dict[str, list[str]],
    evidence: Evidence,
) -> None:
    clean_text = _strip_st_comments(text)
    logic = graph.nodes[logic_id]
    function_header = re.search(
        r"\bFUNCTION\s+([A-Za-z_][A-Za-z0-9_]*)\s*:\s*([^\r\n]+)",
        clean_text,
        re.I,
    )
    if function_header:
        function_name, return_type = function_header.groups()
        _register_st_variable(
            graph,
            logic_id,
            function_name,
            f"{logic.name}.Result",
            return_type.strip(),
            "FUNCTION_RETURN",
            "output",
            names,
            local_names,
            evidence,
        )
    block_pattern = re.compile(
        r"\b(VAR(?:_INPUT|_OUTPUT|_IN_OUT|_GLOBAL|_EXTERNAL|_TEMP|_STAT|_INST)?)\b"
        r"(?:\s+(?:CONSTANT|RETAIN|NON_RETAIN))*\s*(.*?)\bEND_VAR\b",
        re.I | re.S,
    )
    for block_match in block_pattern.finditer(clean_text):
        block_type = block_match.group(1).upper()
        direction = {
            "VAR_INPUT": "input",
            "VAR_OUTPUT": "output",
            "VAR_IN_OUT": "in_out",
            "VAR_GLOBAL": "global",
            "VAR_EXTERNAL": "external",
        }.get(block_type, "local")
        for declaration in block_match.group(2).split(";"):
            parsed = re.match(
                r"\s*([A-Za-z_][A-Za-z0-9_]*(?:\s*,\s*[A-Za-z_][A-Za-z0-9_]*)*)"
                r"(?:\s+AT\s+([^:]+))?\s*:\s*(.+?)\s*$",
                declaration,
                re.S,
            )
            if not parsed:
                continue
            declared_names, address, type_and_initial = parsed.groups()
            data_type, separator, initial_value = type_and_initial.partition(":=")
            for local_name in [item.strip() for item in declared_names.split(",")]:
                qualified_name = f"{logic.name}.{local_name}"
                _register_st_variable(
                    graph,
                    logic_id,
                    local_name,
                    qualified_name,
                    data_type.strip(),
                    block_type,
                    direction,
                    names,
                    local_names,
                    evidence,
                    address.strip() if address else "",
                    initial_value.strip() if separator else "",
                )


def _register_st_variable(
    graph: ControlGraph,
    logic_id: str,
    local_name: str,
    qualified_name: str,
    data_type: str,
    block_type: str,
    direction: str,
    names: dict[str, list[str]],
    local_names: dict[str, list[str]],
    evidence: Evidence,
    address: str = "",
    initial_value: str = "",
) -> str:
    existing = local_names.get(local_name.casefold(), [])
    if existing:
        variable = graph.nodes[existing[0]]
        variable.attributes.update({
            "dataType": data_type,
            "variableBlock": block_type,
            "direction": direction,
            **({"address": address} if address else {}),
            **({"initialValue": initial_value} if initial_value else {}),
        })
        return variable.id
    variable_id = stable_id("iec_variable", logic_id, block_type, local_name.casefold())
    attributes = {
        "localName": local_name,
        "qualifiedName": qualified_name,
        "dataType": data_type,
        "variableBlock": block_type,
        "direction": direction,
        **({"address": address} if address else {}),
        **({"initialValue": initial_value} if initial_value else {}),
    }
    graph.add_node(ControlNode(
        variable_id,
        "IEC_VARIABLE",
        qualified_name,
        "SOURCE",
        attributes,
        [evidence],
    ))
    graph.add_edge(logic_id, variable_id, "declares", evidence=[evidence])
    for alias in (local_name, qualified_name):
        if variable_id not in names[alias.casefold()]:
            names[alias.casefold()].append(variable_id)
    local_names.setdefault(local_name.casefold(), []).append(variable_id)
    return variable_id


def _connect_st_calls(
    graph: ControlGraph,
    logic_id: str,
    text: str,
    names: dict[str, list[str]],
    evidence: Evidence,
) -> None:
    ignored = {"if", "elsif", "for", "while", "case", "not", "and", "or"}
    for called_name in re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(", text):
        if called_name.casefold() in ignored:
            continue
        for called_id in names.get(called_name.casefold(), []):
            if called_id != logic_id and graph.nodes[called_id].kind == "IEC_LOGIC":
                graph.add_edge(logic_id, called_id, "calls", evidence=[evidence])


def _classify(local: str, props: dict[str, str]) -> str | None:
    key = clean_key(local)
    prop_keys = {clean_key(item) for item in props}
    if (
        key == "setting"
        and clean_key(props.get("column", "")) == "tagname"
        and props.get("value")
    ):
        return "SOURCE_TAG"
    if any(term in key for term in MAP_TERMS) and any(clean_key(k) in prop_keys for k in REFERENCE_KEYS):
        return "MAPPING"
    if key in {"points", "protocolpoints", "pointlist", "datasetmembers"}:
        return None
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
        elif clean_key(_local(child.tag)) == "setting":
            column, value = _column_value(child)
            if column:
                result.setdefault(clean_key(column), value)
        elif clean_key(_local(child.tag)) == "row":
            # A "Setting"/"Value" row is one named property of this element, not a record of its own.
            row = _properties(child)
            if row.get("setting"):
                result.setdefault(clean_key(row["setting"]), row.get("value", ""))
    return result


def _column_value(setting: ET.Element) -> tuple[str, str]:
    values = {
        clean_key(_local(child.tag)): (child.text or "").strip()
        for child in setting
        if len(child) == 0
    }
    return values.get("column", ""), values.get("value", "")


def _name(props: dict[str, str], fallback: str) -> str:
    if clean_key(props.get("column", "")) == "tagname" and props.get("value"):
        return props["value"]
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
    return clean_key(local) in {"st", "body", "implementation", "structuredtext"} or "structuredtext" in text


def _looks_like_st_interface(local: str) -> bool:
    return clean_key(local) in {"interface", "declaration", "declarations"}


def _has_nested_st_body(element: ET.Element) -> bool:
    return any(
        child is not element and _looks_like_st(_local(child.tag), _properties(child))
        for child in element.iter()
    )


def _strip_st_comments(text: str) -> str:
    without_blocks = re.sub(r"\(\*.*?\*\)", " ", text, flags=re.S)
    return re.sub(r"//[^\r\n]*", " ", without_blocks)


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
