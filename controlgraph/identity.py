from __future__ import annotations

import re
from typing import Any, Mapping
from urllib.parse import urlsplit


ALIASES = {
    "host": (
        "host", "hostname", "ip", "ipaddress", "endpoint", "endpointurl",
        "discoveryurl", "serverurl",
    ),
    "device": ("device", "devicename", "connection", "channel"),
    "unit": (
        "unit", "unitid", "slave", "slaveid", "station", "outstation", "outstationid",
        "destinationaddress",
    ),
    "object": ("object", "objecttype", "pointtype", "registertype", "type"),
    "index": ("index", "pointindex", "address", "offset", "register"),
    "nodeid": ("nodeid", "node", "itemid"),
    "server": ("server", "opcserver", "opcservername"),
    "protocol": ("protocol", "driver", "connectiontype"),
}


def clean_key(value: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def flattened(values: Mapping[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for key, value in values.items():
        if isinstance(value, (str, int, float, bool)) and value != "":
            result[clean_key(key)] = str(value).strip()
    return result


def first(values: Mapping[str, Any], field: str) -> str:
    flat = flattened(values)
    for alias in ALIASES[field]:
        value = flat.get(clean_key(alias))
        if value:
            return value
    return ""


def parse_opc_item(item: str, server: str = "", device_hint: str = "") -> dict[str, str]:
    text = item.strip()
    device = device_hint
    match = re.match(r"^\[([^]]+)]\s*(.*)$", text)
    body = text
    if match:
        device, body = match.groups()

    expanded_node = re.match(
        r"^\s*(?:(ns)\s*=\s*(\d+)|(nsu)\s*=\s*([^;]+))\s*;\s*([isgb])\s*=\s*(.+)$",
        body,
        re.I,
    )
    if expanded_node:
        namespace_kind, namespace_index, uri_kind, namespace_uri, identifier_type, identifier = (
            expanded_node.groups()
        )
        identity = compact({
            "kind": "opcua",
            "server": server,
            "device": device,
        })
        identity["nodeid"] = body.strip()
        identity["identifierType"] = identifier_type.lower()
        identity["identifierTypeName"] = {
            "s": "String",
            "i": "Numeric",
            "g": "GUID",
            "b": "ByteString",
        }[identifier_type.lower()]
        identity["identifier"] = identifier.strip()
        identity["iecPath"] = opc_ua_iec_path(identifier)
        identity["displayName"] = opc_node_display_name(identifier)
        if namespace_kind:
            identity["namespaceIndex"] = namespace_index
        if uri_kind:
            identity["namespaceUri"] = namespace_uri.strip()
        return identity

    if re.search(r"\bnodeid\s*=", body, re.I):
        nodeid = re.sub(r"^.*?nodeid\s*=\s*", "", body, flags=re.I).strip()
        identity = compact({"kind": "opcua", "server": server, "device": device})
        identity["nodeid"] = nodeid
        return identity

    dnp = re.search(
        r"(?:dnp3?\s*)?(binary|analog|counter|octet|string)\s*(input|output)?\s*[:/_ -]*([0-9]+)$",
        body,
        re.I,
    )
    if dnp:
        family, direction, index = dnp.groups()
        obj = " ".join(part for part in (family, direction) if part).lower()
        return compact({"kind": "dnp3", "device": device, "object": obj, "index": index})

    modbus = re.search(r"(?:modbus\s*)?(HR|IR|DI|C|holding\s*register|input\s*register|coil)\s*[:/_ -]*([0-9]+)$", body, re.I)
    if modbus:
        register_type, address = modbus.groups()
        return compact(
            {"kind": "modbus", "device": device, "object": normalize_object(register_type), "index": address}
        )

    return compact({"kind": "opc", "server": server, "device": device, "nodeid": body})


def opc_ua_iec_path(identifier: str) -> str:
    value = identifier.strip()
    if value.casefold().startswith("|var|"):
        value = value[5:]
    return value.strip(".")


def opc_node_display_name(identifier: str) -> str:
    path = opc_ua_iec_path(identifier)
    parts = [part for part in path.split(".") if part]
    if len(parts) > 2 and [part.casefold() for part in parts[:2]] == ["logic", "application"]:
        parts = parts[2:]
    return ".".join(parts) or path or identifier


def infer_identity(values: Mapping[str, Any], inherited: Mapping[str, Any] | None = None) -> dict[str, str]:
    merged: dict[str, Any] = dict(inherited or {})
    merged.update(values)
    item = _find_value(merged, ("opcitempath", "itempath", "sourcepath"))
    if item:
        result = parse_opc_item(item, first(merged, "server"), first(merged, "device"))
    else:
        protocol = first(merged, "protocol").lower()
        nodeid = first(merged, "nodeid")
        object_type = normalize_object(first(merged, "object"))
        index = normalize_number(first(merged, "index"))
        if "dnp" in protocol or object_type in {"binary input", "binary output", "analog input", "analog output", "counter"}:
            kind = "dnp3"
        elif "modbus" in protocol or object_type in {"holding register", "input register", "coil", "discrete input"}:
            kind = "modbus"
        elif nodeid:
            kind = "opcua"
        else:
            return {}
        result = compact(
            {
                "kind": kind,
                "host": first(merged, "host").lower(),
                "device": first(merged, "device").lower(),
                "unit": normalize_number(first(merged, "unit")),
                "object": object_type,
                "index": index,
                "server": first(merged, "server").lower(),
                "nodeid": nodeid,
            }
        )
    if result.get("kind") == "opcua":
        endpoint = opc_endpoint_identity(merged, assume_opc=True)
        if endpoint.get("host") and not result.get("host"):
            result["host"] = str(endpoint["host"])
        if endpoint.get("port") and not result.get("port"):
            result["port"] = str(endpoint["port"])
    return result


def opc_endpoint_identity(
    values: Mapping[str, Any],
    *,
    assume_opc: bool = False,
) -> dict[str, Any]:
    """Return explicit OPC endpoint evidence without assuming missing configuration."""
    flat = flattened(values)
    endpoint = next(
        (
            flat.get(clean_key(key), "")
            for key in ("endpointUrl", "discoveryUrl", "serverUrl", "endpoint")
            if flat.get(clean_key(key), "")
        ),
        "",
    )
    protocol = first(values, "protocol").casefold()
    opc_evidence = (
        assume_opc
        or endpoint.casefold().startswith(("opc.tcp://", "opc.https://"))
        or "opc" in protocol
        or any("opc" in key for key in flat)
    )
    if not opc_evidence:
        return {}

    aliases: list[str] = []
    for key in ("host", "hostname", "ip", "ipAddress", "hostOverride", "bindAddress"):
        value = flat.get(clean_key(key), "").strip().casefold()
        if value and value not in aliases:
            aliases.append(value)

    endpoint_host = ""
    endpoint_port = ""
    if endpoint:
        candidate = endpoint if "://" in endpoint else f"//{endpoint}"
        try:
            parsed = urlsplit(candidate)
            endpoint_host = (parsed.hostname or "").strip().casefold()
            endpoint_port = str(parsed.port or "")
        except ValueError:
            endpoint_host = ""
            endpoint_port = ""
        if endpoint_host and endpoint_host not in aliases:
            aliases.append(endpoint_host)

    explicit_port = next(
        (
            flat.get(clean_key(key), "")
            for key in ("port", "opcPort", "serverPort", "endpointPort")
            if flat.get(clean_key(key), "")
        ),
        "",
    )
    port = normalize_number(endpoint_port or explicit_port)
    host = endpoint_host or (aliases[0] if aliases else "")
    if not host or not port:
        return {}
    return {
        "kind": "opcua_endpoint",
        "host": host,
        "port": port,
        "hostAliases": aliases,
        **({"endpointUrl": endpoint} if endpoint else {}),
    }


def canonical(identity: Mapping[str, Any]) -> str:
    kind = str(identity.get("kind", "")).lower()
    if kind == "dnp3":
        endpoint = identity.get("host") or identity.get("device") or ""
        return _join(kind, endpoint, identity.get("unit", ""), normalize_object(identity.get("object", "")), normalize_number(identity.get("index", "")))
    if kind == "modbus":
        endpoint = identity.get("host") or identity.get("device") or ""
        return _join(kind, endpoint, identity.get("unit", ""), normalize_object(identity.get("object", "")), normalize_number(identity.get("index", "")))
    if kind in {"opcua", "opc"}:
        endpoint = identity.get("host") or identity.get("server") or identity.get("device") or ""
        port = identity.get("port", "") if identity.get("host") else ""
        return _join(kind, endpoint, port, str(identity.get("nodeid", "")).lower())
    return ""


def enrich_with_device(identity: Mapping[str, str], device: Mapping[str, Any]) -> dict[str, str]:
    result = dict(identity)
    inferred = infer_identity(device)
    for key in ("host", "unit", "server", "device"):
        if inferred.get(key) and not result.get(key):
            result[key] = inferred[key]
    endpoint = device.get("connectionIdentity")
    if not isinstance(endpoint, Mapping):
        endpoint = opc_endpoint_identity(device, assume_opc=result.get("kind") == "opcua")
    for key in ("host", "port"):
        if endpoint.get(key) and not result.get(key):
            result[key] = str(endpoint[key])
    return result


def normalize_object(value: object) -> str:
    text = re.sub(r"[_-]+", " ", str(value)).strip().lower()
    aliases = {
        "ai": "analog input",
        "ao": "analog output",
        "bi": "binary input",
        "bo": "binary output",
        "hr": "holding register",
        "ir": "input register",
        "di": "discrete input",
        "c": "coil",
    }
    return aliases.get(text, text)


def normalize_number(value: object) -> str:
    text = str(value).strip()
    try:
        return str(int(text, 0))
    except (ValueError, TypeError):
        return text.lower()


def compact(values: Mapping[str, Any]) -> dict[str, str]:
    return {key: str(value).strip().lower() for key, value in values.items() if value not in (None, "")}


def _find_value(values: Mapping[str, Any], names: tuple[str, ...]) -> str:
    flat = flattened(values)
    for name in names:
        if flat.get(clean_key(name)):
            return flat[clean_key(name)]
    return ""


def _join(*parts: object) -> str:
    return "|".join(str(part).strip().lower() for part in parts)
