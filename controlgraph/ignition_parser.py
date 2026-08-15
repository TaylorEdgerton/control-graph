from __future__ import annotations

from contextlib import contextmanager
import copy
import json
from pathlib import Path, PurePosixPath
import re
import tempfile
from typing import Any, Iterator
import zipfile

from .identity import clean_key, enrich_with_device, infer_identity
from .model import ControlGraph, ControlNode, Evidence, stable_id


TAG_MARKERS = {
    "tagtype", "datatype", "valuesource", "opcitempath", "opcservername", "typeid", "parameters", "tags"
}


def parse_ignition(path: str | Path) -> ControlGraph:
    source = Path(path)
    graph = ControlGraph()
    with _backup_root(source) as root:
        documents = _read_json_documents(root, source)

    devices = _find_devices(documents, graph)
    tag_roots: list[tuple[dict[str, Any], str, str, str]] = []
    seen: set[tuple[str, int]] = set()
    for rel, display, data in documents:
        provider = _provider(rel, data)
        for tag, location in _tag_roots(data):
            marker = (display, id(tag))
            if marker not in seen:
                seen.add(marker)
                tag_roots.append((tag, provider, display, location))

    definitions: dict[str, tuple[dict[str, Any], str, str, str]] = {}
    for tag, provider, display, location in tag_roots:
        if _tag_kind(tag) == "definition":
            for key in (_text(tag, "typeId"), _text(tag, "name")):
                if key:
                    definitions[_type_key(key)] = (tag, provider, display, location)
            _add_definition(graph, tag, provider, display, location)

    for tag, provider, display, location in tag_roots:
        kind = _tag_kind(tag)
        if kind == "definition":
            continue
        if kind == "instance":
            _add_instance(graph, tag, provider, display, location, definitions, devices)
        else:
            _add_atomic_tree(graph, tag, provider, display, location, devices, parent_id=None, path_prefix="")
    return graph


@contextmanager
def _backup_root(source: Path) -> Iterator[Path]:
    if source.is_dir():
        yield source
        return
    if not source.exists():
        raise ValueError(f"The Ignition backup does not exist: {source}")
    if not zipfile.is_zipfile(source):
        raise ValueError(f"The Ignition backup is not a ZIP-compatible .gwbk file: {source}")
    with tempfile.TemporaryDirectory(prefix="controlgraph-gwbk-") as temp:
        root = Path(temp)
        with zipfile.ZipFile(source) as archive:
            members = archive.infolist()
            if len(members) > 20_000 or sum(item.file_size for item in members) > 500_000_000:
                raise ValueError("The Ignition backup exceeds the PoC safety limit")
            for item in members:
                member = PurePosixPath(item.filename)
                if member.is_absolute() or ".." in member.parts:
                    raise ValueError(f"The backup contains an unsafe path: {item.filename}")
            archive.extractall(root)
        yield root


def _read_json_documents(root: Path, archive: Path) -> list[tuple[str, str, Any]]:
    result: list[tuple[str, str, Any]] = []
    for file in sorted(root.rglob("*")):
        if not file.is_file() or file.suffix.lower() not in {".json", ".config", ".txt"}:
            continue
        if file.stat().st_size > 20_000_000:
            continue
        try:
            data = json.loads(file.read_text(encoding="utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError, OSError):
            continue
        rel = file.relative_to(root).as_posix()
        display = f"{archive}!{rel}" if archive.is_file() else str(file)
        result.append((rel, display, data))
    return result


def _find_devices(
    documents: list[tuple[str, str, Any]], graph: ControlGraph
) -> dict[str, tuple[str, dict[str, Any]]]:
    devices: dict[str, tuple[str, dict[str, Any]]] = {}
    for rel, display, data in documents:
        lower_path = rel.lower()
        if "tag-provider" in lower_path or "tagprovider" in lower_path:
            continue
        if not any(word in lower_path for word in ("device", "connection", "opc")):
            continue
        for obj, location in _walk_objects(data):
            name = _text(obj, "name", "deviceName", "connectionName")
            flat_keys = {clean_key(key) for key in obj}
            if not name or not flat_keys.intersection(
                {"protocol", "driver", "devicetype", "hostname", "host", "ipaddress", "opcserver", "outstationid", "unitid"}
            ):
                continue
            attrs = _scalar_values(obj)
            identity = infer_identity(attrs)
            if identity:
                attrs["identity"] = identity
            evidence = Evidence(display, location, _json_detail(obj))
            node_id = stable_id("ignition_device", display, location, name)
            graph.add_node(ControlNode(node_id, "IGNITION_DEVICE", name, "IGNITION", attrs, [evidence]))
            devices[name.casefold()] = (node_id, attrs)
    return devices


def _add_definition(
    graph: ControlGraph, tag: dict[str, Any], provider: str, display: str, location: str
) -> str:
    name = _text(tag, "name") or "Unnamed UDT definition"
    full_name = f"[{provider}]_types_/{name}"
    node_id = stable_id("udt_definition", display, location, full_name)
    evidence = Evidence(display, location, _json_detail(tag))
    graph.add_node(
        ControlNode(node_id, "UDT_DEFINITION", full_name, "IGNITION", _scalar_values(tag), [evidence])
    )
    return node_id


def _add_instance(
    graph: ControlGraph,
    tag: dict[str, Any],
    provider: str,
    display: str,
    location: str,
    definitions: dict[str, tuple[dict[str, Any], str, str, str]],
    devices: dict[str, tuple[str, dict[str, Any]]],
) -> None:
    name = _text(tag, "name") or "Unnamed UDT instance"
    instance_path = f"[{provider}]{name}"
    evidence = Evidence(display, location, _json_detail(tag))
    instance_id = stable_id("udt_instance", display, location, instance_path)
    graph.add_node(
        ControlNode(instance_id, "UDT_INSTANCE", instance_path, "IGNITION", _scalar_values(tag), [evidence])
    )
    type_id = _text(tag, "typeId")
    definition_record = definitions.get(_type_key(type_id))
    if not definition_record:
        issue_id = stable_id("issue", "missing-udt", provider, type_id, instance_path)
        graph.add_node(
            ControlNode(
                issue_id,
                "MAPPING_ISSUE",
                f"UDT definition is not available: {type_id or '(empty type ID)'}",
                "IGNITION",
                {"status": "unresolved", "instance": instance_path},
                [evidence],
            )
        )
        graph.add_edge(issue_id, instance_id, "affects", status="unresolved", evidence=[evidence])
        return

    definition, def_provider, def_display, def_location = definition_record
    definition_id = _add_definition(graph, definition, def_provider, def_display, def_location)
    graph.add_edge(definition_id, instance_id, "instantiates", evidence=[evidence])
    parameters = _parameters(definition)
    parameters.update(_parameters(tag))
    overrides = {(_text(item, "name").casefold()): item for item in _children(tag)}
    for position, member in enumerate(_children(definition)):
        member_name = _text(member, "name") or f"Member {position + 1}"
        effective = _deep_merge(member, overrides.get(member_name.casefold(), {}))
        effective = _substitute(effective, parameters)
        member_location = f"{location}/resolved/{member_name}"
        _add_resolved_member(
            graph,
            effective,
            provider,
            display,
            member_location,
            devices,
            instance_id,
            instance_path,
            definition_evidence=Evidence(def_display, f"{def_location}/tags/{position}", _json_detail(member)),
            instance_evidence=evidence,
        )


def _add_resolved_member(
    graph: ControlGraph,
    tag: dict[str, Any],
    provider: str,
    display: str,
    location: str,
    devices: dict[str, tuple[str, dict[str, Any]]],
    parent_id: str,
    path_prefix: str,
    *,
    definition_evidence: Evidence,
    instance_evidence: Evidence,
) -> None:
    name = _text(tag, "name") or "Unnamed member"
    tag_path = f"{path_prefix}/{name}"
    evidence = [definition_evidence, instance_evidence]
    member_id = stable_id("udt_member", display, location, tag_path)
    graph.add_node(
        ControlNode(member_id, "UDT_MEMBER", tag_path, "IGNITION", _scalar_values(tag), evidence)
    )
    graph.add_edge(parent_id, member_id, "contains_member", evidence=evidence)

    children = _children(tag)
    if children:
        for position, child in enumerate(children):
            child_name = _text(child, "name") or str(position)
            _add_resolved_member(
                graph,
                child,
                provider,
                display,
                f"{location}/{child_name}",
                devices,
                member_id,
                tag_path,
                definition_evidence=definition_evidence,
                instance_evidence=instance_evidence,
            )
        return


    tag_id = stable_id("ignition_tag", display, location, tag_path)
    graph.add_node(ControlNode(tag_id, "IGNITION_TAG", tag_path, "IGNITION", _scalar_values(tag), evidence))
    graph.add_edge(member_id, tag_id, "materializes_as_tag", evidence=evidence)
    _add_source(graph, tag, display, location, devices, tag_id, member_id, evidence)


def _add_atomic_tree(
    graph: ControlGraph,
    tag: dict[str, Any],
    provider: str,
    display: str,
    location: str,
    devices: dict[str, tuple[str, dict[str, Any]]],
    parent_id: str | None,
    path_prefix: str,
) -> None:
    name = _text(tag, "name") or "Unnamed tag"
    tag_path = f"{path_prefix}/{name}" if path_prefix else f"[{provider}]{name}"
    evidence = [Evidence(display, location, _json_detail(tag))]
    tag_id = stable_id("ignition_tag", display, location, tag_path)
    graph.add_node(ControlNode(tag_id, "IGNITION_TAG", tag_path, "IGNITION", _scalar_values(tag), evidence))
    if parent_id:
        graph.add_edge(parent_id, tag_id, "contains", evidence=evidence)
    _add_source(graph, tag, display, location, devices, tag_id, tag_id, evidence)
    for position, child in enumerate(_children(tag)):
        _add_atomic_tree(
            graph,
            child,
            provider,
            display,
            f"{location}/tags/{position}",
            devices,
            tag_id,
            tag_path,
        )


def _add_source(
    graph: ControlGraph,
    tag: dict[str, Any],
    display: str,
    location: str,
    devices: dict[str, tuple[str, dict[str, Any]]],
    tag_id: str,
    target_id: str,
    evidence: list[Evidence],
) -> None:
    item_path = _text(tag, "opcItemPath", "itemPath", "sourcePath")
    if not item_path:
        return
    attrs = _scalar_values(tag)
    identity = infer_identity(attrs)
    device_name = identity.get("device", "")
    device_record = devices.get(device_name.casefold())
    if device_record:
        identity = enrich_with_device(identity, device_record[1])
    attrs["identity"] = identity
    source_id = stable_id("opc_item", display, location, item_path)
    graph.add_node(ControlNode(source_id, "OPC_ITEM", item_path, "IGNITION", attrs, evidence))
    if device_record:
        graph.add_edge(device_record[0], source_id, "provides", evidence=evidence)
    graph.add_edge(source_id, target_id, "drives", evidence=evidence)


def _tag_roots(data: Any) -> list[tuple[dict[str, Any], str]]:
    result: list[tuple[dict[str, Any], str]] = []
    visited: set[int] = set()

    def walk(value: Any, location: str, under_tags: bool = False) -> None:
        if isinstance(value, dict):
            if id(value) in visited:
                return
            visited.add(id(value))
            if under_tags and _is_tag(value):
                result.append((value, location))
                return
            if _is_tag(value) and _text(value, "name") and not under_tags:
                result.append((value, location))
                return
            for key, child in value.items():
                if isinstance(child, list):
                    for pos, item in enumerate(child):
                        walk(item, f"{location}/{key}/{pos}", clean_key(key) in {"tags", "tagdefinitions"})
                elif isinstance(child, dict):
                    walk(child, f"{location}/{key}", False)
        elif isinstance(value, list):
            for pos, item in enumerate(value):
                walk(item, f"{location}/{pos}", under_tags)

    walk(data, "$", False)
    return result


def _walk_objects(data: Any) -> Iterator[tuple[dict[str, Any], str]]:
    def walk(value: Any, location: str) -> Iterator[tuple[dict[str, Any], str]]:
        if isinstance(value, dict):
            yield value, location
            for key, child in value.items():
                yield from walk(child, f"{location}/{key}")
        elif isinstance(value, list):
            for pos, child in enumerate(value):
                yield from walk(child, f"{location}/{pos}")

    return walk(data, "$")


def _is_tag(value: dict[str, Any]) -> bool:
    keys = {clean_key(key) for key in value}
    return bool(_text(value, "name") and keys.intersection(TAG_MARKERS))


def _tag_kind(tag: dict[str, Any]) -> str:
    tag_type = _text(tag, "tagType", "type").lower().replace(" ", "")
    type_id = _text(tag, "typeId")
    if tag_type in {"udttype", "datatype", "definition"} or _bool(tag, "isUdtDefinition"):
        return "definition"
    if tag_type in {"udtinstance", "instance"} or type_id:
        return "instance"
    return "atomic"


def _children(tag: dict[str, Any]) -> list[dict[str, Any]]:
    for key, value in tag.items():
        if clean_key(key) in {"tags", "members", "children"} and isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _parameters(tag: dict[str, Any]) -> dict[str, Any]:
    for key, value in tag.items():
        if clean_key(key) == "parameters" and isinstance(value, dict):
            result: dict[str, Any] = {}
            for name, item in value.items():
                if isinstance(item, dict) and "value" in item:
                    result[name] = item["value"]
                else:
                    result[name] = item
            return result
    return {}


def _substitute(value: Any, parameters: dict[str, Any]) -> Any:
    if isinstance(value, str):
        def replace(match: re.Match[str]) -> str:
            key = match.group(1)
            found = next((item for name, item in parameters.items() if name.casefold() == key.casefold()), match.group(0))
            return str(found)
        return re.sub(r"\{([^{}]+)}", replace, value)
    if isinstance(value, list):
        return [_substitute(item, parameters) for item in value]
    if isinstance(value, dict):
        return {key: _substitute(item, parameters) for key, item in value.items()}
    return value


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in override.items():
        if clean_key(key) in {"tags", "members", "children"} and isinstance(value, list):
            base_children = result.get(key, [])
            if not isinstance(base_children, list):
                result[key] = copy.deepcopy(value)
                continue
            override_by_name = {
                _text(item, "name").casefold(): item for item in value
                if isinstance(item, dict) and _text(item, "name")
            }
            result[key] = [
                _deep_merge(item, override_by_name.get(_text(item, "name").casefold(), {}))
                if isinstance(item, dict) else copy.deepcopy(item)
                for item in base_children
            ]
            continue
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _provider(rel: str, data: Any) -> str:
    match = re.search(r"(?:tag-provider|tagprovider)[/\\]([^/\\]+)", rel, re.I)
    if match:
        return match.group(1)
    if isinstance(data, dict):
        return _text(data, "provider", "providerName") or "default"
    return "default"


def _scalar_values(value: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, item in value.items():
        if isinstance(item, (str, int, float, bool)) or item is None:
            result[key] = item
        elif clean_key(key) == "parameters" and isinstance(item, dict):
            result[key] = _parameters({key: item})
    return result


def _text(value: dict[str, Any], *keys: str) -> str:
    wanted = {clean_key(key) for key in keys}
    for key, item in value.items():
        if clean_key(key) in wanted and isinstance(item, (str, int, float)):
            return str(item).strip()
    return ""


def _bool(value: dict[str, Any], key: str) -> bool:
    wanted = clean_key(key)
    for name, item in value.items():
        if clean_key(name) == wanted:
            return item is True or str(item).lower() == "true"
    return False


def _type_key(value: str) -> str:
    return value.replace("\\", "/").rsplit("/", 1)[-1].casefold()


def _json_detail(value: dict[str, Any]) -> str:
    return json.dumps(_scalar_values(value), ensure_ascii=False, sort_keys=True)[:500]
