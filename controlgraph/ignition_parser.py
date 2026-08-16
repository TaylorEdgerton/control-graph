from __future__ import annotations

from contextlib import contextmanager
import copy
from dataclasses import dataclass
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import tempfile
from typing import Any, Iterator
import zipfile
import xml.etree.ElementTree as ET

from .identity import clean_key, enrich_with_device, infer_identity
from .model import ControlGraph, ControlNode, Evidence, stable_id


TAG_MARKERS = {
    "tagtype", "datatype", "valuesource", "opcitempath", "opcservername", "typeid", "parameters", "tags"
}

MAX_BACKUP_FILES = 20_000
MAX_BACKUP_SIZE = 500_000_000


@dataclass(frozen=True)
class GatewayBackupInfo:
    version: str
    version_family: str
    timestamp: str
    backup_type: str
    configuration_format: str
    configuration_source: str
    tag_configuration_count: int
    tag_providers: tuple[str, ...]
    projects: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "fileType": "Ignition Gateway Backup",
            "version": self.version,
            "versionFamily": self.version_family,
            "timestamp": self.timestamp,
            "backupType": self.backup_type,
            "configurationFormat": self.configuration_format,
            "configurationSource": self.configuration_source,
            "tagConfigurationCount": self.tag_configuration_count,
            "tagProviders": list(self.tag_providers),
            "projects": list(self.projects),
        }


def inspect_ignition_backup(path: str | Path) -> GatewayBackupInfo:
    source = Path(path)
    if not source.exists() or not source.is_file():
        raise ValueError(f"The Ignition backup does not exist: {source}")
    if not zipfile.is_zipfile(source):
        raise ValueError("The selected file is not a ZIP-compatible Ignition Gateway backup.")
    with zipfile.ZipFile(source) as archive:
        members = _validated_members(archive)
        names = {item.filename for item in members if not item.is_dir()}
        metadata = _read_backup_metadata_from_archive(archive, names)
        configuration_format = _configuration_format(metadata[0], names)
        if configuration_format == "sqlite":
            count, providers = _inspect_sqlite_tag_configurations(archive)
            source_label = "SQLite TAGCONFIG table"
        elif configuration_format == "json":
            count = _count_json_tag_configurations(archive, names)
            providers = _json_tag_providers(names)
            source_label = "JSON tag resources"
        else:
            raise ValueError("ControlGraph cannot find a supported tag configuration in this backup.")
    version, timestamp, backup_type = metadata
    return GatewayBackupInfo(
        version=version,
        version_family=_version_family(version),
        timestamp=timestamp,
        backup_type=backup_type,
        configuration_format=configuration_format,
        configuration_source=source_label,
        tag_configuration_count=count,
        tag_providers=tuple(providers),
        projects=tuple(_project_names(names)),
    )


def parse_ignition(
    path: str | Path,
    tag_providers: list[str] | tuple[str, ...] | set[str] | None = None,
) -> ControlGraph:
    source = Path(path)
    graph = ControlGraph()
    with _backup_root(source) as root:
        configuration_format = _configuration_format_from_root(root)
        if configuration_format == "sqlite":
            tag_documents = _read_sqlite_tag_documents(root, source)
            devices = _read_sqlite_devices(root, source, graph)
        elif configuration_format == "json":
            tag_documents = _read_json_documents(root, source, _is_tag_resource_path)
            device_documents = _read_83_device_documents(root, source)
            devices = _find_devices(device_documents, graph)
        else:
            tag_documents = _read_json_documents(root, source)
            devices = _find_devices(tag_documents, graph)

    tag_roots: list[tuple[dict[str, Any], str, str, str, str, bool]] = []
    seen: set[tuple[str, int]] = set()
    selected_providers = {provider.casefold() for provider in tag_providers or []}
    for rel, display, data in tag_documents:
        provider = _provider(rel, data)
        if selected_providers and provider.casefold() not in selected_providers:
            continue
        path_prefix = _tag_path_prefix(rel, provider)
        definition_resource = _is_udt_definition_resource(rel)
        for tag, location in _tag_roots(data):
            marker = (display, id(tag))
            if marker not in seen:
                seen.add(marker)
                tag_roots.append(
                    (tag, provider, display, location, path_prefix, definition_resource)
                )

    definitions: dict[str, tuple[dict[str, Any], str, str, str, str]] = {}
    for tag, provider, display, location, path_prefix, definition_resource in tag_roots:
        for definition, definition_location, definition_prefix in _definition_tags(
            tag,
            location,
            path_prefix,
            provider,
            definition_resource,
        ):
            for key in (_text(definition, "typeId"), _text(definition, "name")):
                if key:
                    definitions[_type_key(key)] = (
                        definition,
                        provider,
                        display,
                        definition_location,
                        definition_prefix,
                    )
            _add_definition(
                graph,
                definition,
                provider,
                display,
                definition_location,
                definition_prefix,
            )

    for tag, provider, display, location, path_prefix, definition_resource in tag_roots:
        _add_tag_tree(
            graph,
            tag,
            provider,
            display,
            location,
            devices,
            definitions,
            parent_id=None,
            path_prefix=path_prefix,
            definition_resource=definition_resource,
        )
    return graph


def _validated_members(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    members = archive.infolist()
    if len(members) > MAX_BACKUP_FILES or sum(item.file_size for item in members) > MAX_BACKUP_SIZE:
        raise ValueError("The Ignition backup exceeds the safety limit.")
    for item in members:
        member = PurePosixPath(item.filename)
        if member.is_absolute() or ".." in member.parts:
            raise ValueError(f"The backup contains an unsafe path: {item.filename}")
    return members


def _read_backup_metadata_from_archive(
    archive: zipfile.ZipFile, names: set[str]
) -> tuple[str, str, str]:
    metadata_name = next((name for name in names if name.casefold() == "backupinfo.xml"), "")
    if not metadata_name:
        return "", "", ""
    try:
        root = ET.fromstring(archive.read(metadata_name))
    except (ET.ParseError, KeyError, OSError) as error:
        raise ValueError("The backup metadata is not valid XML.") from error
    values = {_xml_name(child.tag): (child.text or "").strip() for child in root}
    return values.get("version", ""), values.get("timestamp", ""), values.get("backup-type", "")


def _read_backup_metadata_from_root(root: Path) -> tuple[str, str, str]:
    metadata = next((file for file in root.iterdir() if file.name.casefold() == "backupinfo.xml"), None)
    if metadata is None:
        return "", "", ""
    try:
        document = ET.parse(metadata).getroot()
    except (ET.ParseError, OSError) as error:
        raise ValueError("The backup metadata is not valid XML.") from error
    values = {_xml_name(child.tag): (child.text or "").strip() for child in document}
    return values.get("version", ""), values.get("timestamp", ""), values.get("backup-type", "")


def _xml_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].casefold()


def _version_family(version: str) -> str:
    match = re.match(r"^(\d+)\.(\d+)", version)
    return f"{match.group(1)}.{match.group(2)}" if match else "Unknown"


def _version_tuple(version: str) -> tuple[int, int] | None:
    match = re.match(r"^(\d+)\.(\d+)", version)
    return (int(match.group(1)), int(match.group(2))) if match else None


def _configuration_format(version: str, names: set[str]) -> str:
    version_number = _version_tuple(version)
    has_database = any(PurePosixPath(name).name.casefold() == "db_backup_sqlite.idb" for name in names)
    has_json_tags = any(_is_tag_resource_path(name) for name in names)
    if version_number and version_number >= (8, 3):
        return "json" if has_json_tags else ""
    if version_number and version_number < (8, 3):
        return "sqlite" if has_database else ""
    if has_json_tags:
        return "json"
    if has_database:
        return "sqlite"
    return ""


def _configuration_format_from_root(root: Path) -> str:
    names = {file.relative_to(root).as_posix() for file in root.rglob("*") if file.is_file()}
    version, _, _ = _read_backup_metadata_from_root(root)
    return _configuration_format(version, names)


def _is_tag_resource_path(path: str) -> bool:
    lower = path.replace("\\", "/").casefold()
    filename = PurePosixPath(lower).name
    return (
        filename in {"tag.json", "tags.json", "udts.json"}
        and (
            "/ignition/tag-definition/" in f"/{lower}"
            or "/ignition/tag-type-definition/" in f"/{lower}"
        )
    )


def _is_udt_definition_resource(path: str) -> bool:
    normalized = "/" + path.replace("\\", "/").casefold()
    return "/ignition/tag-type-definition/" in normalized


def _inspect_sqlite_tag_configurations(archive: zipfile.ZipFile) -> tuple[int, list[str]]:
    database_name = next(
        (item.filename for item in archive.infolist() if PurePosixPath(item.filename).name.casefold() == "db_backup_sqlite.idb"),
        "",
    )
    if not database_name:
        return 0, []
    with tempfile.TemporaryDirectory(prefix="controlgraph-inspect-") as temp:
        database = Path(temp) / "gateway.idb"
        with archive.open(database_name) as source, database.open("wb") as target:
            shutil.copyfileobj(source, target)
        with sqlite3.connect(database) as connection:
            if not _sqlite_has_table(connection, "TAGCONFIG"):
                return 0, []
            count = int(connection.execute("SELECT COUNT(*) FROM TAGCONFIG").fetchone()[0])
            provider_map = _sqlite_tag_providers(connection)
            provider_ids = [int(row[0]) for row in connection.execute(
                "SELECT DISTINCT PROVIDERID FROM TAGCONFIG ORDER BY PROVIDERID"
            )]
            return count, [provider_map.get(provider_id, f"provider-{provider_id}") for provider_id in provider_ids]


def _count_json_tag_configurations(archive: zipfile.ZipFile, names: set[str]) -> int:
    count = 0
    for name in names:
        if not _is_tag_resource_path(name):
            continue
        try:
            data = json.loads(archive.read(name).decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError):
            continue
        if isinstance(data, list):
            count += len([item for item in data if isinstance(item, dict)])
        elif isinstance(data, dict):
            count += len(_tag_roots(data))
    return count


def _json_tag_providers(names: set[str]) -> list[str]:
    providers: set[str] = set()
    marker = re.compile(r"(?:^|/)ignition/(?:tag-definition|tag-type-definition)/([^/]+)", re.I)
    for name in names:
        match = marker.search(name.replace("\\", "/"))
        if match and _is_tag_resource_path(name):
            providers.add(match.group(1))
    return sorted(providers, key=str.casefold)


def _project_names(names: set[str]) -> list[str]:
    projects: set[str] = set()
    marker = re.compile(r"^projects/([^/]+)/", re.I)
    for name in names:
        match = marker.match(name.replace("\\", "/"))
        if match:
            projects.add(match.group(1))
    return sorted(projects, key=str.casefold)


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
            _validated_members(archive)
            archive.extractall(root)
        yield root


def _read_json_documents(
    root: Path,
    archive: Path,
    predicate: Any | None = None,
) -> list[tuple[str, str, Any]]:
    result: list[tuple[str, str, Any]] = []
    for file in sorted(root.rglob("*")):
        if not file.is_file() or file.suffix.lower() not in {".json", ".config", ".txt"}:
            continue
        rel = file.relative_to(root).as_posix()
        if predicate and not predicate(rel):
            continue
        if file.stat().st_size > 20_000_000:
            continue
        try:
            data = json.loads(file.read_text(encoding="utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError, OSError):
            continue
        display = f"{archive}!{rel}" if archive.is_file() else str(file)
        result.append((rel, display, data))
    return result


def _database_file(root: Path) -> Path | None:
    return next(
        (file for file in root.rglob("*") if file.is_file() and file.name.casefold() == "db_backup_sqlite.idb"),
        None,
    )


def _read_sqlite_tag_documents(root: Path, archive: Path) -> list[tuple[str, str, Any]]:
    database = _database_file(root)
    if database is None:
        raise ValueError("The 8.1 backup does not contain db_backup_sqlite.idb.")
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        if not _sqlite_has_table(connection, "TAGCONFIG"):
            raise ValueError("The 8.1 backup database does not contain TAGCONFIG.")
        providers = _sqlite_tag_providers(connection)
        records: dict[str, tuple[int, str | None, int, dict[str, Any]]] = {}
        rows = connection.execute(
            "SELECT ID, PROVIDERID, FOLDERID, RANK, NAME, CFG FROM TAGCONFIG "
            "ORDER BY PROVIDERID, FOLDERID, RANK, NAME"
        )
        for row in rows:
            try:
                config = json.loads(row["CFG"] or "{}")
            except json.JSONDecodeError:
                continue
            if not isinstance(config, dict):
                continue
            config = copy.deepcopy(config)
            name = _text(config, "name") or str(row["NAME"] or "").strip()
            if not name:
                continue
            config["name"] = name
            records[str(row["ID"])] = (
                int(row["PROVIDERID"] or 0),
                str(row["FOLDERID"]) if row["FOLDERID"] else None,
                int(row["RANK"] or 0),
                config,
            )

    children: dict[str, list[tuple[int, dict[str, Any]]]] = {}
    grouped_roots: dict[int, list[tuple[int, dict[str, Any]]]] = {}
    for record_id, (provider_id, folder_id, rank, config) in records.items():
        if folder_id and folder_id in records:
            children.setdefault(folder_id, []).append((rank, config))
        else:
            grouped_roots.setdefault(provider_id, []).append((rank, config))
    for record_id, (_, _, _, config) in records.items():
        nested = [item for _, item in sorted(children.get(record_id, []), key=lambda item: (item[0], _text(item[1], "name")))]
        if nested:
            existing = _children(config)
            config["tags"] = [*existing, *nested]

    display = f"{archive}!{database.relative_to(root).as_posix()}" if archive.is_file() else str(database)
    documents: list[tuple[str, str, Any]] = []
    for provider_id, roots in sorted(grouped_roots.items()):
        provider = providers.get(provider_id, f"provider-{provider_id}")
        tags = [item for _, item in sorted(roots, key=lambda item: (item[0], _text(item[1], "name")))]
        documents.append((f"db_backup_sqlite.idb/{provider}", display, {"provider": provider, "tags": tags}))
    return documents


def _sqlite_tag_providers(connection: sqlite3.Connection) -> dict[int, str]:
    providers = {0: "default"}
    if _sqlite_has_table(connection, "TAGPROVIDERSETTINGS"):
        for row in connection.execute("SELECT TAGPROVIDERSETTINGS_ID, NAME FROM TAGPROVIDERSETTINGS"):
            providers[int(row[0])] = str(row[1])
    if _sqlite_has_table(connection, "SIMPLETAGPROVIDERPROFILE"):
        for row in connection.execute("SELECT PROVIDERID, NAME FROM SIMPLETAGPROVIDERPROFILE"):
            providers[int(row[0])] = str(row[1])
    return providers


def _read_sqlite_devices(
    root: Path,
    archive: Path,
    graph: ControlGraph,
) -> dict[str, tuple[str, dict[str, Any]]]:
    database = _database_file(root)
    if database is None:
        return {}
    devices: dict[str, tuple[str, dict[str, Any]]] = {}
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        if not _sqlite_has_table(connection, "DEVICESETTINGS"):
            return devices
        settings_by_id: dict[int, dict[str, Any]] = {}
        for table in _sqlite_table_names(connection):
            upper = table.upper()
            if upper == "DEVICESETTINGS" or not upper.endswith(("DEVICESETTINGS", "DRIVERSETTINGS")):
                continue
            identifier = _sqlite_identifier(table)
            columns = [str(row[1]).upper() for row in connection.execute(f"PRAGMA table_info({identifier})")]
            if "DEVICESETTINGSID" not in columns:
                continue
            for row in connection.execute(f"SELECT * FROM {identifier}"):
                values = _safe_database_values(dict(row))
                device_id = int(values.pop("DEVICESETTINGSID"))
                settings_by_id.setdefault(device_id, {}).update(values)

        display = f"{archive}!{database.relative_to(root).as_posix()}" if archive.is_file() else str(database)
        for row in connection.execute("SELECT * FROM DEVICESETTINGS ORDER BY NAME"):
            values = _safe_database_values(dict(row))
            device_id = int(values.pop("DEVICESETTINGS_ID"))
            name = str(values.get("NAME") or f"Device {device_id}")
            attrs = {**values, **settings_by_id.get(device_id, {})}
            attrs["deviceName"] = name
            attrs["protocol"] = str(attrs.get("TYPE") or "")
            identity = infer_identity(attrs)
            if identity:
                attrs["identity"] = identity
            evidence = Evidence(display, f"DEVICESETTINGS/{device_id}", _json_detail(attrs))
            node_id = stable_id("ignition_device", display, device_id, name)
            graph.add_node(ControlNode(node_id, "IGNITION_DEVICE", name, "IGNITION", attrs, [evidence]))
            devices[name.casefold()] = (node_id, attrs)
    return devices


def _safe_database_values(values: dict[str, Any]) -> dict[str, Any]:
    blocked = ("PASSWORD", "SECRET", "TOKEN", "KEYSTORE")
    return {
        str(key): value
        for key, value in values.items()
        if value is None or isinstance(value, (str, int, float, bool))
        if not any(word in str(key).upper() for word in blocked)
    }


def _sqlite_table_names(connection: sqlite3.Connection) -> list[str]:
    return [str(row[0]) for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")]


def _sqlite_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _sqlite_has_table(connection: sqlite3.Connection, name: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND upper(name)=upper(?)", (name,)
    ).fetchone() is not None


def _read_83_device_documents(root: Path, archive: Path) -> list[tuple[str, str, Any]]:
    result: list[tuple[str, str, Any]] = []
    marker = re.compile(r"(?:^|/)com\.inductiveautomation\.opcua/device/([^/]+)/config\.json$", re.I)
    for file in sorted(root.rglob("config.json")):
        rel = file.relative_to(root).as_posix()
        match = marker.search(rel)
        if not match or file.stat().st_size > 20_000_000:
            continue
        try:
            data = json.loads(file.read_text(encoding="utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError, OSError):
            continue
        attrs: dict[str, Any] = {"name": match.group(1)}
        _flatten_config(data, attrs)
        attrs["deviceName"] = match.group(1)
        attrs["protocol"] = str(attrs.get("type") or attrs.get("protocol") or "")
        display = f"{archive}!{rel}" if archive.is_file() else str(file)
        result.append((rel, display, attrs))
    return result


def _flatten_config(value: Any, result: dict[str, Any]) -> None:
    if not isinstance(value, dict):
        return
    for key, item in value.items():
        if isinstance(item, (str, int, float, bool)) or item is None:
            result[str(key)] = item
        elif isinstance(item, dict):
            _flatten_config(item, result)


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
    graph: ControlGraph,
    tag: dict[str, Any],
    provider: str,
    display: str,
    location: str,
    path_prefix: str = "",
) -> str:
    name = _text(tag, "name")
    if not name:
        return ""
    full_name = _join_tag_path(path_prefix or f"[{provider}]_types_", name)
    node_id = stable_id("udt_definition", display, location, full_name)
    evidence = Evidence(display, location, _json_detail(tag))
    attributes = _scalar_values(tag)
    attributes["isTemplate"] = True
    graph.add_node(
        ControlNode(node_id, "UDT_DEFINITION", full_name, "IGNITION", attributes, [evidence])
    )
    return node_id


def _add_instance(
    graph: ControlGraph,
    tag: dict[str, Any],
    provider: str,
    display: str,
    location: str,
    path_prefix: str,
    definitions: dict[str, tuple[dict[str, Any], str, str, str, str]],
    devices: dict[str, tuple[str, dict[str, Any]]],
) -> None:
    name = _text(tag, "name")
    if not name:
        return
    instance_path = _join_tag_path(path_prefix or f"[{provider}]", name)
    evidence = Evidence(display, location, _json_detail(tag))
    instance_id = stable_id("udt_instance", display, location, instance_path)
    instance_attributes = _scalar_values(tag)
    instance_attributes["isTemplate"] = False
    graph.add_node(
        ControlNode(instance_id, "UDT_INSTANCE", instance_path, "IGNITION", instance_attributes, [evidence])
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

    definition, def_provider, def_display, def_location, def_prefix = definition_record
    definition_id = _add_definition(
        graph, definition, def_provider, def_display, def_location, def_prefix
    )
    graph.add_edge(definition_id, instance_id, "instantiates", evidence=[evidence])
    parameters = _effective_parameters(definition, tag)
    instance_node = graph.nodes[instance_id]
    instance_node.attributes["resolvedParameters"] = copy.deepcopy(parameters)
    overrides = {(_text(item, "name").casefold()): item for item in _children(tag)}
    for position, member in enumerate(_children(definition)):
        member_name = _text(member, "name") or f"Member {position + 1}"
        effective = _deep_merge(member, overrides.get(member_name.casefold(), {}))
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
            parameters,
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
    parameters: dict[str, Any],
    *,
    definition_evidence: Evidence,
    instance_evidence: Evidence,
) -> None:
    template_tag = tag
    parameters = _resolved_parameters(parameters, _parameters(template_tag))
    tag = _substitute(template_tag, parameters)
    name = _text(tag, "name")
    if not name:
        return
    tag_path = _join_tag_path(path_prefix, name)
    evidence = [definition_evidence, instance_evidence]
    member_id = stable_id("udt_member", display, location, tag_path)
    member_attributes = _scalar_values(tag)
    member_attributes["resolvedParameters"] = copy.deepcopy(parameters)
    member_attributes["isTemplate"] = False
    graph.add_node(
        ControlNode(member_id, "UDT_MEMBER", tag_path, "IGNITION", member_attributes, evidence)
    )
    graph.add_edge(parent_id, member_id, "contains_member", evidence=evidence)

    children = _children(template_tag)
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
                parameters,
                definition_evidence=definition_evidence,
                instance_evidence=instance_evidence,
            )
        return


    tag_id = stable_id("ignition_tag", display, location, tag_path)
    tag_attributes = _scalar_values(tag)
    tag_attributes["resolvedParameters"] = copy.deepcopy(parameters)
    tag_attributes["isTemplate"] = False
    graph.add_node(ControlNode(tag_id, "IGNITION_TAG", tag_path, "IGNITION", tag_attributes, evidence))
    graph.add_edge(member_id, tag_id, "materializes_as_tag", evidence=evidence)
    _add_source(
        graph,
        tag,
        display,
        location,
        devices,
        tag_id,
        member_id,
        evidence,
        template_tag=template_tag,
        parameters=parameters,
    )


def _add_atomic_tree(
    graph: ControlGraph,
    tag: dict[str, Any],
    provider: str,
    display: str,
    location: str,
    devices: dict[str, tuple[str, dict[str, Any]]],
    definitions: dict[str, tuple[dict[str, Any], str, str, str, str]],
    parent_id: str | None,
    path_prefix: str,
) -> None:
    name = _text(tag, "name")
    if not name:
        return
    tag_path = _join_tag_path(path_prefix or f"[{provider}]", name)
    evidence = [Evidence(display, location, _json_detail(tag))]
    tag_id = stable_id("ignition_tag", display, location, tag_path)
    graph.add_node(ControlNode(tag_id, "IGNITION_TAG", tag_path, "IGNITION", _scalar_values(tag), evidence))
    if parent_id:
        graph.add_edge(parent_id, tag_id, "contains", evidence=evidence)
    _add_source(graph, tag, display, location, devices, tag_id, tag_id, evidence)
    for position, child in enumerate(_children(tag)):
        _add_tag_tree(
            graph,
            child,
            provider,
            display,
            f"{location}/tags/{position}",
            devices,
            definitions,
            parent_id=tag_id,
            path_prefix=tag_path,
        )


def _add_tag_tree(
    graph: ControlGraph,
    tag: dict[str, Any],
    provider: str,
    display: str,
    location: str,
    devices: dict[str, tuple[str, dict[str, Any]]],
    definitions: dict[str, tuple[dict[str, Any], str, str, str, str]],
    *,
    parent_id: str | None,
    path_prefix: str,
    definition_resource: bool = False,
) -> None:
    kind = _tag_kind(tag, definition_resource)
    if kind == "definition" or _is_definition_folder(tag):
        return
    if kind == "instance":
        _add_instance(
            graph,
            tag,
            provider,
            display,
            location,
            path_prefix,
            definitions,
            devices,
        )
        instance_path = _join_tag_path(path_prefix or f"[{provider}]", _text(tag, "name"))
        instance_id = stable_id("udt_instance", display, location, instance_path)
        if parent_id and instance_id in graph.nodes:
            graph.add_edge(parent_id, instance_id, "contains", evidence=graph.nodes[instance_id].evidence)
        return
    _add_atomic_tree(
        graph,
        tag,
        provider,
        display,
        location,
        devices,
        definitions,
        parent_id,
        path_prefix,
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
    *,
    template_tag: dict[str, Any] | None = None,
    parameters: dict[str, Any] | None = None,
) -> None:
    template_path = _text(template_tag or tag, "opcItemPath", "itemPath", "sourcePath")
    item_path = _text(tag, "opcItemPath", "itemPath", "sourcePath")
    if parameters:
        item_path = str(_substitute(item_path, parameters))
    if not item_path:
        return
    attrs = _scalar_values(tag)
    attrs["opcItemPath"] = item_path
    if template_path and template_path != item_path:
        attrs["opcItemPathTemplate"] = template_path
    if parameters:
        attrs["resolvedParameters"] = copy.deepcopy(parameters)
    unresolved_parameters = sorted(set(re.findall(r"\{([^{}]+)}", item_path)), key=str.casefold)
    if unresolved_parameters:
        attrs["unresolvedParameters"] = unresolved_parameters
        message = f"The OPC item path has unresolved parameters: {', '.join(unresolved_parameters)}"
        issue_id = stable_id("mapping_issue", target_id, item_path, message)
        graph.add_node(
            ControlNode(
                issue_id,
                "MAPPING_ISSUE",
                message,
                "IGNITION",
                {
                    "status": "unresolved",
                    "subject": target_id,
                    "opcItemPathTemplate": template_path or item_path,
                    "candidatePath": item_path,
                    "parameters": unresolved_parameters,
                    "resolvedParameters": copy.deepcopy(parameters or {}),
                },
                evidence,
            )
        )
        graph.add_edge(target_id, issue_id, "has_mapping_issue", status="unresolved", evidence=evidence)
        return
    server = _text(tag, "opcServer", "opcServerName")
    if server:
        attrs["opcServer"] = server
    identity = infer_identity(attrs)
    device_name, device_record, device_match = _match_configured_device(
        identity,
        item_path,
        parameters or {},
        devices,
    )
    if device_record:
        identity = enrich_with_device(identity, device_record[1])
        identity.setdefault("device", device_name.casefold())
        attrs["configuredDevice"] = device_name
        attrs["deviceMatch"] = device_match
    attrs["identity"] = identity
    source_id = stable_id("opc_item", display, location, item_path)
    graph.add_node(ControlNode(source_id, "OPC_ITEM", item_path, "IGNITION", attrs, evidence))
    if device_record:
        graph.add_edge(device_record[0], source_id, "provides", evidence=evidence)
    graph.add_edge(source_id, target_id, "drives", evidence=evidence)


def _match_configured_device(
    identity: dict[str, str],
    item_path: str,
    parameters: dict[str, Any],
    devices: dict[str, tuple[str, dict[str, Any]]],
) -> tuple[str, tuple[str, dict[str, Any]] | None, str]:
    candidates: list[tuple[str, str]] = []
    if identity.get("device"):
        candidates.append(("OPC item device", identity["device"]))
    parameter_items = [
        (name, str(value))
        for name, value in parameters.items()
        if isinstance(value, (str, int, float)) and value != ""
    ]
    parameter_items.sort(key=lambda item: (_device_parameter_priority(item[0]), item[0].casefold()))
    for name, value in parameter_items:
        if _device_parameter_priority(name) < 9:
            candidates.append((f"parameter {name}", value))
    candidates.append(("resolved OPC item path", item_path))

    for reason, candidate in candidates:
        for key, record in devices.items():
            display_name = str(record[1].get("deviceName") or record[1].get("name") or key)
            aliases = {key, display_name}
            aliases.update(
                str(value)
                for name, value in record[1].items()
                if isinstance(value, (str, int, float))
                and any(term in clean_key(name) for term in ("devicename", "connectionname", "iedname"))
            )
            if any(_path_contains_name(candidate, alias) for alias in aliases if alias):
                return display_name, record, reason
    return "", None, ""


def _device_parameter_priority(name: str) -> int:
    key = clean_key(name)
    if key in {"device", "devicename", "rtacdevice", "opcdevice"}:
        return 0
    if "device" in key:
        return 1
    if "ied" in key:
        return 2
    if "connection" in key:
        return 3
    return 9


def _path_contains_name(value: str, name: str) -> bool:
    candidate = value.strip().strip("[]\"'").casefold()
    alias = name.strip().strip("[]\"'").casefold()
    if not candidate or not alias:
        return False
    if candidate == alias:
        return True
    return re.search(
        rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])",
        candidate,
        re.I,
    ) is not None


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


def _definition_tags(
    tag: dict[str, Any],
    location: str,
    path_prefix: str,
    provider: str,
    definition_resource: bool = False,
) -> Iterator[tuple[dict[str, Any], str, str]]:
    if _tag_kind(tag, definition_resource) == "definition":
        yield tag, location, path_prefix
        return
    name = _text(tag, "name")
    child_prefix = _join_tag_path(path_prefix or f"[{provider}]", name) if name else path_prefix
    child_definition_resource = definition_resource or _is_definition_folder(tag)
    for position, child in enumerate(_children(tag)):
        yield from _definition_tags(
            child,
            f"{location}/tags/{position}",
            child_prefix,
            provider,
            child_definition_resource,
        )


def _is_definition_folder(tag: dict[str, Any]) -> bool:
    return _text(tag, "name").strip("/ ").casefold() == "_types_"


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


def _tag_kind(tag: dict[str, Any], definition_resource: bool = False) -> str:
    tag_type = _text(tag, "tagType", "type").lower().replace(" ", "")
    if definition_resource and tag_type != "folder":
        return "definition"
    type_id = _text(tag, "typeId")
    if tag_type in {"udttype", "datatype", "definition"} or _bool(tag, "isUdtDefinition"):
        return "definition"
    if tag_type in {"udtinstance", "instance"} or type_id:
        return "instance"
    return "atomic"


def _children(tag: dict[str, Any]) -> list[dict[str, Any]]:
    for key, value in tag.items():
        if clean_key(key) in {"tags", "members", "children"} and isinstance(value, list):
            return [
                item for item in value
                if isinstance(item, dict) and bool(_text(item, "name"))
            ]
    return []


def _parameters(tag: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in tag.items():
        if clean_key(key) not in {
            "parameters", "parameterbindings", "parameterdefinitions",
            "parametervalues", "paramvalues", "parameteroverrides", "params",
        }:
            continue
        if isinstance(value, dict):
            for name, item in value.items():
                _set_parameter(result, str(name), _parameter_value(item))
        elif isinstance(value, list):
            for item in value:
                if not isinstance(item, dict):
                    continue
                name = _text(item, "name", "parameterName", "key")
                if name:
                    _set_parameter(result, name, _parameter_value(item))
    return result


def _parameter_value(value: Any) -> Any:
    if isinstance(value, dict):
        binding_type = _raw_text(value, "bindType", "bindingType", "type").casefold()
        for wanted in ("value", "binding"):
            for key, item in value.items():
                if clean_key(key) == wanted:
                    resolved = _parameter_value(item)
                    if wanted == "binding" and "parameter" in binding_type:
                        return _parameter_expression(resolved)
                    return resolved
    if isinstance(value, str):
        binding = _serialized_binding(value)
        if binding:
            return _parameter_expression(binding) if _is_serialized_parameter_binding(value) else binding
        return value
    return copy.deepcopy(value)


def _effective_parameters(definition: dict[str, Any], instance: dict[str, Any]) -> dict[str, Any]:
    return _resolved_parameters(_parameters(definition), _parameters(instance))


def _resolved_parameters(*sources: dict[str, Any]) -> dict[str, Any]:
    parameters: dict[str, Any] = {}
    for source in sources:
        for name, value in source.items():
            _set_parameter(parameters, name, value)

    resolved = copy.deepcopy(parameters)
    for _ in range(max(1, len(resolved) * 2)):
        next_values = {name: _substitute(value, resolved) for name, value in resolved.items()}
        if next_values == resolved:
            break
        resolved = next_values
    return resolved


def _set_parameter(parameters: dict[str, Any], name: str, value: Any) -> None:
    existing = next((key for key in parameters if key.casefold() == name.casefold()), None)
    if existing is not None:
        parameters.pop(existing)
    parameters[name] = value


def _substitute(value: Any, parameters: dict[str, Any]) -> Any:
    if isinstance(value, str):
        def replace(match: re.Match[str]) -> str:
            key = match.group(1)
            found = _parameter_lookup(parameters, key, match.group(0))
            return str(found)
        return re.sub(r"\{([^{}]+)}", replace, value)
    if isinstance(value, list):
        return [_substitute(item, parameters) for item in value]
    if isinstance(value, dict):
        return {key: _substitute(item, parameters) for key, item in value.items()}
    return value


def _parameter_lookup(parameters: dict[str, Any], name: str, fallback: Any) -> Any:
    wanted = clean_key(name)
    return next(
        (value for key, value in parameters.items() if clean_key(key) == wanted),
        fallback,
    )


def _parameter_expression(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text or re.search(r"\{[^{}]+}", text):
        return text
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_. -]*", text):
        return "{" + text + "}"
    return text


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
    match = re.search(
        r"(?:tag-definition|tag-type-definition|tag-provider|tagprovider)[/\\]([^/\\]+)",
        rel,
        re.I,
    )
    if match:
        return match.group(1)
    if isinstance(data, dict):
        return _text(data, "provider", "providerName") or "default"
    return "default"


def _tag_path_prefix(rel: str, provider: str) -> str:
    normalized = rel.replace("\\", "/")
    match = re.search(
        r"ignition/(tag-definition|tag-type-definition)/[^/]+(?:/(.*))?/(?:tag|tags|udts)\.json$",
        normalized,
        re.I,
    )
    if not match:
        return ""
    resource_type = match.group(1).casefold()
    folders = (match.group(2) or "").strip("/")
    base = f"[{provider}]_types_" if resource_type == "tag-type-definition" else f"[{provider}]"
    return _join_tag_path(base, folders) if folders else base


def _join_tag_path(prefix: str, name: str) -> str:
    clean_name = str(name).strip("/")
    if not prefix:
        return clean_name
    if not clean_name:
        return prefix
    separator = "" if prefix.endswith("]") else "/"
    return f"{prefix.rstrip('/')}{separator}{clean_name}"


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
            text = str(item).strip()
            return _serialized_binding(text) or text
        if clean_key(key) in wanted and isinstance(item, dict):
            for nested_name in ("binding", "value"):
                for nested_key, nested_value in item.items():
                    if clean_key(nested_key) == nested_name and isinstance(
                        nested_value, (str, int, float)
                    ):
                        text = str(nested_value).strip()
                        return _serialized_binding(text) or text
    return ""


def _raw_text(value: dict[str, Any], *keys: str) -> str:
    wanted = {clean_key(key) for key in keys}
    for key, item in value.items():
        if clean_key(key) in wanted and isinstance(item, (str, int, float)):
            return str(item).strip()
    return ""


def _is_serialized_parameter_binding(value: str) -> bool:
    match = re.search(
        r"(?:bind\s*type|bindtype)\s*[:=]\s*[\"']?([^,}\"']+)",
        value,
        re.I,
    )
    return bool(match and "parameter" in match.group(1).casefold())


def _serialized_binding(value: str) -> str:
    text = value.strip()
    if not re.search(r"bind\s*type|bindtype", text, re.I) or not re.search(
        r"\bbinding\b\s*[:=]", text, re.I
    ):
        return ""
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, dict):
        for key, item in parsed.items():
            if clean_key(key) == "binding" and isinstance(item, (str, int, float)):
                return str(item).strip()

    marker = re.search(r"(?:[\"']?binding[\"']?)\s*[:=]\s*", text, re.I)
    if marker is None:
        return ""
    expression = text[marker.end():].strip()
    if expression[:1] in {"\"", "'"}:
        quote = expression[0]
        expression = expression[1:]
        if quote in expression:
            expression = expression.split(quote, 1)[0]
    depth = 0
    for position, character in enumerate(expression):
        if character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
        elif character == "," and depth == 0:
            expression = expression[:position]
            break
    expression = expression.strip()
    while expression.endswith("}") and expression.count("}") > expression.count("{"):
        expression = expression[:-1].rstrip()
    return expression


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
