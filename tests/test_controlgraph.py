from __future__ import annotations

from collections import deque
import asyncio
import json
import sqlite3
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from controlgraph.ignition_parser import inspect_ignition_backup, parse_ignition
from controlgraph.identity import parse_opc_item
from controlgraph.cli import main
from controlgraph.loader import build_graph
from controlgraph.model import ControlGraph, ControlNode, stable_id
from controlgraph.resolver import resolve
from controlgraph.server import create_app
from controlgraph.source_parser import parse_source
import httpx


ROOT = Path(__file__).resolve().parent.parent
SOURCE_PROJECT = ROOT / "examples" / "source_project.xml"
IGNITION = ROOT / "examples" / "ignition_backup"


class ControlGraphTests(unittest.TestCase):
    def test_cli_starts_without_example_inputs(self) -> None:
        with patch("controlgraph.cli.serve") as serve_mock:
            with patch("sys.argv", ["controlgraph", "--api-only"]):
                main()

        graph = serve_mock.call_args.args[0]
        self.assertEqual(graph.summary()["nodeCount"], 0)
        self.assertEqual(serve_mock.call_args.kwargs["source_graph"].summary()["nodeCount"], 0)
        self.assertEqual(serve_mock.call_args.kwargs["ignition_graph"].summary()["nodeCount"], 0)

    def test_demo_has_complete_deterministic_lineage(self) -> None:
        graph = build_graph(SOURCE_PROJECT, IGNITION)
        start = next(node.id for node in graph.nodes.values() if node.name == "Relay_A")
        end = next(
            node.id for node in graph.nodes.values()
            if node.name == "[default]Pump_01/Run" and node.kind == "IGNITION_TAG"
        )
        path = directed_path(graph, start, end, ignored_edge_kinds={"device_connection_match"})

        self.assertIsNotNone(path)
        kinds = [graph.nodes[node_id].kind for node_id in path]
        self.assertEqual(
            kinds,
            [
                "SOURCE_DEVICE", "PROTOCOL_POINT", "SOURCE_TAG", "IEC_LOGIC", "SOURCE_TAG",
                "PROTOCOL_POINT", "IGNITION_DEVICE", "OPC_ITEM", "UDT_MEMBER", "IGNITION_TAG",
            ],
        )
        match = next(edge for edge in graph.edges.values() if edge.kind == "communication_identity_match")
        self.assertEqual(match.status, "resolved")
        self.assertEqual(match.attributes["identityKey"], "dnp3|10.20.1.20|1|binary input|12")
        self.assertGreaterEqual(len(match.evidence), 3)
        device_match = next(edge for edge in graph.edges.values() if edge.kind == "device_connection_match")
        self.assertEqual(graph.nodes[device_match.source].name, "Ignition_Link")
        self.assertEqual(graph.nodes[device_match.target].name, "DNP_Gateway")
        self.assertEqual(device_match.attributes["matchedPointCount"], 1)
        self.assertEqual(device_match.attributes["identityKeys"], [match.attributes["identityKey"]])

    def test_gwbk_zip_is_extracted_and_parsed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            archive_path = Path(temp) / "gateway.gwbk"
            with zipfile.ZipFile(archive_path, "w") as archive:
                for file in IGNITION.rglob("*"):
                    if file.is_file():
                        archive.write(file, file.relative_to(IGNITION))
            graph = parse_ignition(archive_path)
        names = {node.name for node in graph.nodes.values()}
        self.assertIn("[default]Pump_01/Run", names)
        run_source = next(node for node in graph.nodes.values() if node.name == "[DNP_Gateway]Binary Input 12")
        self.assertEqual(run_source.attributes["identity"]["host"], "10.20.1.20")
        self.assertTrue(run_source.evidence[0].source.startswith(str(archive_path)))

    def test_gateway_backup_formats_and_tag_providers_are_detected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            gateway_81, gateway_83 = create_gateway_backups(Path(temp))
            version_81 = inspect_ignition_backup(gateway_81)
            version_83 = inspect_ignition_backup(gateway_83)

            self.assertEqual(version_81.version_family, "8.1")
            self.assertEqual(version_81.configuration_format, "sqlite")
            self.assertEqual(version_81.tag_providers, ("default", "System"))
            self.assertIn("test", version_81.projects)
            self.assertEqual(version_83.version_family, "8.3")
            self.assertEqual(version_83.configuration_format, "json")
            self.assertEqual(version_83.tag_providers, ("default", "fes"))
            self.assertIn("test", version_83.projects)

    def test_backup_tags_can_be_filtered_by_provider(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            gateway_81, gateway_83 = create_gateway_backups(Path(temp))
            graph_81 = parse_ignition(gateway_81, ["default"])
            graph_83 = parse_ignition(gateway_83, ["default"])

            tags_81 = {node.name for node in graph_81.nodes.values() if node.kind == "IGNITION_TAG"}
            tags_83 = {node.name for node in graph_83.nodes.values() if node.kind == "IGNITION_TAG"}
            connections_81 = {
                node.name
                for node in graph_81.nodes.values()
                if node.kind == "OPC_SERVER_CONNECTION"
            }
            self.assertEqual(tags_81, set())
            self.assertEqual(tags_83, set())
            self.assertEqual(graph_81.summary()["audit"]["totalTagCount"], 2)
            self.assertEqual(graph_81.summary()["audit"]["excludedTagCount"], 2)
            self.assertEqual(graph_83.summary()["audit"]["totalTagCount"], 1)
            self.assertEqual(graph_83.summary()["audit"]["excludedTagCount"], 1)
            self.assertIn("CODESYS Connection", connections_81)

    def test_opc_only_audit_excludes_non_opc_tags_and_udt_members(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            backup = create_opc_scope_backup(Path(temp))
            graph = parse_ignition(backup, ["default"])

        summary = graph.summary()["audit"]
        tag_names = {
            node.name for node in graph.nodes.values() if node.kind == "IGNITION_TAG"
        }
        member_names = {
            node.name for node in graph.nodes.values() if node.kind == "UDT_MEMBER"
        }
        self.assertEqual(summary["scope"], "OPC_TAGS_ONLY")
        self.assertEqual(summary["totalTagCount"], 14)
        self.assertEqual(summary["opcTagCount"], 4)
        self.assertEqual(summary["excludedTagCount"], 10)
        self.assertEqual(summary["excludedByValueSource"], {
            "derived": 2,
            "expression": 2,
            "memory": 2,
            "query": 2,
            "reference": 2,
        })
        self.assertEqual(summary["invalidOpcPathCount"], 1)
        self.assertEqual(summary["missingConnectionCount"], 1)
        self.assertEqual(len(tag_names), 4)
        self.assertEqual(member_names, {"[default]Area/Meter01/Voltage"})
        self.assertFalse(any(
            excluded in name
            for name in tag_names | member_names
            for excluded in ("Power", "DisplayName", "VoltageRef", "QueryValue", "DerivedValue")
        ))
        self.assertTrue(any(
            node.kind == "MAPPING_ISSUE" and "does not define an OPC item path" in node.name
            for node in graph.nodes.values()
        ))

    def test_udt_instance_parameters_resolve_the_opc_item_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            backup = create_parameterized_backup(Path(temp))
            graph = parse_ignition(backup, ["default"])

        source = next(node for node in graph.nodes.values() if node.kind == "OPC_ITEM")
        tag = next(node for node in graph.nodes.values() if node.kind == "IGNITION_TAG")
        instance = next(node for node in graph.nodes.values() if node.kind == "UDT_INSTANCE")
        template = next(node for node in graph.nodes.values() if node.kind == "UDT_DEFINITION")
        device = next(node for node in graph.nodes.values() if node.kind == "IGNITION_DEVICE")
        self.assertEqual(source.name, "RTAC_A.Binary Input 12")
        self.assertEqual(
            source.attributes["opcItemPathTemplate"],
            "{OPC_Connection_String}.{Control_Tag}",
        )
        self.assertEqual(
            source.attributes["resolvedParameters"]["OPC_Connection_String"],
            "RTAC_A",
        )
        self.assertEqual(source.attributes["configuredDevice"], "RTAC_A")
        self.assertEqual(source.attributes["deviceMatch"], "parameter RTAC_Device")
        self.assertEqual(source.attributes["identity"]["host"], "10.20.1.20")
        self.assertEqual(source.attributes["identity"]["unit"], "1")
        self.assertEqual(tag.name, "[default]Area/Motor_1/Status")
        self.assertNotIn("_types_", tag.name)
        self.assertTrue(template.name.startswith("[default]_types_"))
        self.assertTrue(template.attributes["isTemplate"])
        self.assertFalse(any("Unnamed" in node.name for node in graph.nodes.values()))
        self.assertEqual(instance.attributes["resolvedParameters"]["Control_Tag"], "Binary Input 12")
        self.assertTrue(any(edge.source == device.id and edge.target == source.id for edge in graph.edges.values()))

        resolved = resolve(parse_source(SOURCE_PROJECT), graph)
        match = next(edge for edge in resolved.edges.values() if edge.kind == "communication_identity_match")
        self.assertEqual(match.status, "resolved")
        device_match = next(edge for edge in resolved.edges.values() if edge.kind == "device_connection_match")
        self.assertEqual(resolved.nodes[device_match.source].name, "Ignition_Link")
        self.assertEqual(resolved.nodes[device_match.target].name, "RTAC_A")

    def test_nested_udt_instance_is_distinct_from_its_definition(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            backup = create_nested_parameterized_backup(Path(temp))
            graph = parse_ignition(backup, ["default"])

        definitions = [node for node in graph.nodes.values() if node.kind == "UDT_DEFINITION"]
        instances = [node for node in graph.nodes.values() if node.kind == "UDT_INSTANCE"]
        sources = [node for node in graph.nodes.values() if node.kind == "OPC_ITEM"]
        tags = [node for node in graph.nodes.values() if node.kind == "IGNITION_TAG"]

        self.assertEqual(len(definitions), 1)
        self.assertEqual(len(instances), 1)
        self.assertTrue(definitions[0].attributes["isTemplate"])
        self.assertFalse(instances[0].attributes["isTemplate"])
        self.assertEqual(sources[0].name, "RTAC_A.Binary Input 12")
        self.assertFalse(any("_types_" in tag.name for tag in tags))
        self.assertFalse(any("{" in source.name or "}" in source.name for source in sources))

    def test_child_udt_instance_inherits_parent_instance_parameters(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            backup = create_child_udt_backup(Path(temp))
            graph = parse_ignition(backup, ["default"])

        instances = [node for node in graph.nodes.values() if node.kind == "UDT_INSTANCE"]
        source = next(node for node in graph.nodes.values() if node.kind == "OPC_ITEM")
        self.assertEqual(len(instances), 2)
        self.assertEqual(source.name, "RTAC_A.Binary Input 12")
        self.assertFalse(any("{" in node.name or "}" in node.name for node in graph.nodes.values()))

    def test_codesys_namespace_node_maps_by_opc_server_not_symbol_suffix(self) -> None:
        item_path = (
            "nsu=CODESYSSPV3/3S/IecVarAccess;"
            "s=|var|Logic.Application.LV_Meter_MODBUS"
        )
        identity = parse_opc_item(item_path, "CODESYS Connection")
        self.assertEqual(identity["kind"], "opcua")
        self.assertEqual(identity["namespaceUri"], "CODESYSSPV3/3S/IecVarAccess")
        self.assertEqual(identity["identifier"], "|var|Logic.Application.LV_Meter_MODBUS")
        self.assertEqual(identity["identifierTypeName"], "String")
        self.assertEqual(identity["displayName"], "LV_Meter_MODBUS")

        with tempfile.TemporaryDirectory() as temp:
            backup = create_codesys_parameter_backup(Path(temp))
            graph = parse_ignition(backup, ["default"])

        source = next(node for node in graph.nodes.values() if node.kind == "OPC_NODE")
        connections = {
            node.name: node
            for node in graph.nodes.values()
            if node.kind in {"IGNITION_DEVICE", "OPC_SERVER_CONNECTION"}
        }
        self.assertEqual(source.name, "LV_Meter_MODBUS")
        self.assertEqual(source.attributes["rawNodeId"], item_path)
        self.assertEqual(source.attributes["iecPath"], "Logic.Application.LV_Meter_MODBUS")
        self.assertEqual(source.attributes["configuredConnection"], "CODESYS Connection")
        self.assertEqual(source.attributes["connectionMatch"], "OPC server")
        self.assertEqual(source.attributes["identity"]["namespaceUri"], "CODESYSSPV3/3S/IecVarAccess")
        self.assertTrue(any(
            edge.source == connections["CODESYS Connection"].id and edge.target == source.id
            for edge in graph.edges.values()
        ))
        self.assertFalse(any(
            edge.source == connections["LV_Meter_MODBUS"].id and edge.target == source.id
            for edge in graph.edges.values()
        ))

        source_graph = ControlGraph()
        controller_id = stable_id("source_device", "codesys-controller")
        point_id = stable_id("protocol_point", "codesys-voltage")
        source_graph.add_node(ControlNode(
            controller_id,
            "SOURCE_DEVICE",
            "CODESYS Application",
            "SOURCE",
        ))
        source_graph.add_node(ControlNode(
            point_id,
            "PROTOCOL_POINT",
            "test_Meter_MODBUS.Voltage",
            "SOURCE",
            {"direction": "out", "identity": identity},
        ))
        source_graph.add_edge(controller_id, point_id, "contains")
        resolved = resolve(source_graph, graph)
        connection_match = next(
            edge for edge in resolved.edges.values()
            if edge.kind == "device_connection_match"
        )
        self.assertEqual(
            resolved.nodes[connection_match.target].kind,
            "OPC_SERVER_CONNECTION",
        )

        with tempfile.TemporaryDirectory() as temp:
            missing_connection = create_codesys_parameter_backup(
                Path(temp),
                include_connection=False,
            )
            unresolved_graph = parse_ignition(missing_connection, ["default"])
        inferred_connection = next(
            node for node in unresolved_graph.nodes.values()
            if node.kind == "OPC_SERVER_CONNECTION" and node.name == "CODESYS Connection"
        )
        unresolved_source = next(
            node for node in unresolved_graph.nodes.values() if node.kind == "OPC_NODE"
        )
        self.assertEqual(inferred_connection.attributes["configurationStatus"], "inferred")
        self.assertEqual(
            inferred_connection.attributes["namespaceUris"],
            ["CODESYSSPV3/3S/IecVarAccess"],
        )
        self.assertTrue(any(
            edge.source == inferred_connection.id and edge.target == unresolved_source.id
            for edge in unresolved_graph.edges.values()
        ))
        self.assertTrue(any(
            node.kind == "MAPPING_ISSUE" and "OPC server connection" in node.name
            for node in unresolved_graph.nodes.values()
        ))

        with tempfile.TemporaryDirectory() as temp:
            namespace_only = create_codesys_parameter_backup(
                Path(temp),
                include_connection=False,
                include_server_property=False,
            )
            namespace_graph = parse_ignition(namespace_only, ["default"])
        namespace_root = next(
            node for node in namespace_graph.nodes.values()
            if node.kind == "OPC_SERVER_CONNECTION" and node.name.startswith("OPC namespace:")
        )
        namespace_source = next(
            node for node in namespace_graph.nodes.values() if node.kind == "OPC_NODE"
        )
        self.assertTrue(any(
            edge.source == namespace_root.id and edge.target == namespace_source.id
            for edge in namespace_graph.edges.values()
        ))

    def test_shared_opc_node_is_deduplicated_and_counted_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            backup = create_shared_codesys_signal_backup(Path(temp))
            graph = parse_ignition(backup, ["default"])

        signals = [node for node in graph.nodes.values() if node.kind == "OPC_NODE"]
        connection = next(
            node for node in graph.nodes.values() if node.kind == "OPC_SERVER_CONNECTION"
        )
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].attributes["referencedTagCount"], 2)
        self.assertEqual(connection.attributes["usedSignalCount"], 1)
        self.assertEqual(connection.attributes["referencedTagCount"], 2)
        self.assertEqual(len([
            edge for edge in graph.edges.values()
            if edge.source == signals[0].id and edge.kind == "drives"
        ]), 2)

    def test_numeric_namespace_node_keeps_an_inferred_connection_upstream(self) -> None:
        item_path = "ns=2;s=|var|Logic.Application.Program.Struct.Voltage"
        identity = parse_opc_item(item_path)
        self.assertEqual(identity["kind"], "opcua")
        self.assertEqual(identity["namespaceIndex"], "2")
        self.assertEqual(identity["displayName"], "Program.Struct.Voltage")

        with tempfile.TemporaryDirectory() as temp:
            backup = create_numeric_namespace_backup(Path(temp))
            graph = parse_ignition(backup, ["default"])

        signal = next(node for node in graph.nodes.values() if node.kind == "OPC_NODE")
        connection = next(
            node for node in graph.nodes.values() if node.kind == "OPC_SERVER_CONNECTION"
        )
        tag = next(node for node in graph.nodes.values() if node.kind == "IGNITION_TAG")
        self.assertEqual(signal.attributes["namespaceIndex"], "2")
        self.assertEqual(connection.attributes["namespaceIndexes"], ["2"])
        self.assertEqual(connection.attributes["usedSignalCount"], 1)
        self.assertTrue(any(
            edge.source == connection.id
            and edge.target == signal.id
            and edge.kind == "provides"
            for edge in graph.edges.values()
        ))
        self.assertTrue(any(
            edge.source == signal.id and edge.target == tag.id and edge.kind == "drives"
            for edge in graph.edges.values()
        ))

    def test_opc_connections_match_by_explicit_endpoint_before_points(self) -> None:
        source_graph = ControlGraph()
        source_connection_id = stable_id("source_device", "plc")
        point_id = stable_id("protocol_point", "voltage")
        source_graph.add_node(ControlNode(
            source_connection_id,
            "SOURCE_DEVICE",
            "PLC project",
            "SOURCE",
            {"connectionIdentity": {
                "kind": "opcua_endpoint",
                "host": "10.20.30.40",
                "port": "4840",
                "hostAliases": ["10.20.30.40"],
            }},
        ))
        source_graph.add_node(ControlNode(
            point_id,
            "PROTOCOL_POINT",
            "Voltage",
            "SOURCE",
            {"direction": "out", "identity": {
                "kind": "opcua",
                "host": "10.20.30.40",
                "port": "4840",
                "nodeid": "nsu=plant;s=voltage",
            }},
        ))
        source_graph.add_edge(source_connection_id, point_id, "contains")

        ignition_graph = ControlGraph()
        ignition_connection_id = stable_id("ignition_connection", "plc")
        opc_node_id = stable_id("opc_node", "voltage")
        ignition_graph.add_node(ControlNode(
            ignition_connection_id,
            "OPC_SERVER_CONNECTION",
            "PLC_OPCUA",
            "IGNITION",
            {"connectionIdentity": {
                "kind": "opcua_endpoint",
                "host": "10.20.30.40",
                "port": "4840",
                "hostAliases": ["10.20.30.40"],
            }},
        ))
        ignition_graph.add_node(ControlNode(
            opc_node_id,
            "OPC_NODE",
            "Voltage",
            "IGNITION",
            {"identity": {
                "kind": "opcua",
                "host": "10.20.30.40",
                "port": "4840",
                "nodeid": "nsu=plant;s=voltage",
            }},
        ))
        ignition_graph.add_edge(ignition_connection_id, opc_node_id, "provides")

        graph = resolve(source_graph, ignition_graph)
        connection_match = next(
            edge for edge in graph.edges.values()
            if edge.source == source_connection_id
            and edge.target == ignition_connection_id
            and edge.kind == "device_connection_match"
        )
        point_match = next(
            edge for edge in graph.edges.values()
            if edge.source == point_id and edge.kind == "communication_identity_match"
        )
        self.assertEqual(connection_match.attributes["mappingSource"], "ENDPOINT_IDENTITY")
        self.assertEqual(connection_match.attributes["confidence"], 100)
        self.assertEqual(connection_match.attributes["matchEvidence"], "IP address and OPC UA port")
        self.assertEqual(connection_match.attributes["matchedPointCount"], 1)
        self.assertEqual(point_match.target, ignition_connection_id)

    def test_unresolved_opc_template_is_an_issue_not_a_protocol_item(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            backup = create_unresolved_parameter_backup(Path(temp))
            graph = parse_ignition(backup, ["default"])

        self.assertFalse(any(
            node.kind in {"OPC_ITEM", "OPC_NODE"} for node in graph.nodes.values()
        ))
        issue = next(
            node for node in graph.nodes.values()
            if node.kind == "MAPPING_ISSUE" and "unresolved parameters" in node.name
        )
        self.assertEqual(
            issue.attributes["candidatePath"],
            "{OPC_Connection_String}.{Control_Tag}",
        )
        self.assertEqual(
            issue.attributes["parameters"],
            ["Control_Tag", "OPC_Connection_String"],
        )

    def test_unresolved_mapping_is_explicit(self) -> None:
        graph = build_graph(SOURCE_PROJECT, IGNITION)
        issues = [node for node in graph.nodes.values() if node.kind == "MAPPING_ISSUE"]
        self.assertTrue(any("No source protocol point" in issue.name for issue in issues))
        unresolved = [edge for edge in graph.edges.values() if edge.status == "unresolved"]
        self.assertTrue(unresolved)

    def test_ambiguous_mapping_is_explicit_and_not_resolved(self) -> None:
        source_graph = parse_source(SOURCE_PROJECT)
        ignition = parse_ignition(IGNITION)
        source = next(node for node in ignition.nodes.values() if node.name == "[DNP_Gateway]Binary Input 12")
        duplicate = ControlNode(
            stable_id("opc_item", "duplicate", source.name),
            source.kind,
            f"{source.name} duplicate",
            source.system,
            dict(source.attributes),
            list(source.evidence),
        )
        ignition.add_node(duplicate)
        graph = resolve(source_graph, ignition)
        issues = [node for node in graph.nodes.values() if node.attributes.get("status") == "ambiguous"]
        self.assertEqual(len(issues), 1)
        point = next(node for node in graph.nodes.values() if node.name == "Published_Run")
        resolved = [edge for edge in graph.edges.values() if edge.source == point.id and edge.kind == "communication_identity_match"]
        self.assertEqual(resolved, [])

    def test_fastapi_exposes_documented_graph_endpoint(self) -> None:
        graph = build_graph(SOURCE_PROJECT, IGNITION)
        app = create_app(graph, serve_static=False)
        try:
            schema = app.openapi()
            self.assertIn("/api/graph", schema["paths"])
            self.assertIn("/api/imports/stage", schema["paths"])
            self.assertIn("/api/imports/confirm", schema["paths"])
            self.assertEqual(schema["info"]["title"], "ControlGraph API")
            health, response, docs = asyncio.run(
                get_responses(app, "/api/health", "/api/graph", "/docs")
            )
            self.assertEqual(health.json(), {"status": "ok"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["summary"]["nodeCount"], len(graph.nodes))
            self.assertEqual(docs.status_code, 200)
        finally:
            app.state.workspace.close()

    def test_import_api_stages_confirms_and_removes_a_backup(self) -> None:
        graph = build_graph(SOURCE_PROJECT, IGNITION)
        app = create_app(
            graph,
            serve_static=False,
            source_graph=parse_source(SOURCE_PROJECT),
            ignition_graph=parse_ignition(IGNITION),
        )
        try:
            with tempfile.TemporaryDirectory() as temp:
                backup = Path(temp) / "gateway-8.3.gwbk"
                with zipfile.ZipFile(backup, "w") as archive:
                    archive.writestr(
                        "backupinfo.xml",
                        "<backup><version>8.3.6.2026042713</version>"
                        "<timestamp>2026-08-16 09:35:56</timestamp>"
                        "<backup-type>ALL</backup-type></backup>",
                    )
                    archive.writestr(
                        "config/resources/core/ignition/tag-definition/default/tags.json",
                        '[{"name":"ApiTag","tagType":"AtomicTag","valueSource":"memory"}]',
                    )
                staged, confirmed, imported_graph, removed = asyncio.run(import_workflow(app, backup))
                self.assertEqual(staged.status_code, 200)
                staged_backup = staged.json()["staged"][0]
                self.assertEqual(staged_backup["versionFamily"], "8.3")
                self.assertEqual(staged_backup["configurationFormat"], "json")
                self.assertEqual(staged_backup["tagProviders"], ["default"])
                self.assertEqual(confirmed.status_code, 200)
                imported = confirmed.json()["imports"][0]
                self.assertEqual(imported["selectedTagProviders"], ["default"])
                imported_names = {node["name"] for node in imported_graph.json()["nodes"]}
                self.assertNotIn("[default]ApiTag", imported_names)
                self.assertEqual(imported["totalTagCount"], 1)
                self.assertEqual(imported["opcTagCount"], 0)
                self.assertEqual(imported["excludedTagCount"], 1)
                self.assertEqual(removed.status_code, 200)
                restored_names = {node["name"] for node in removed.json()["graph"]["nodes"]}
                self.assertIn("[default]Pump_01/Run", restored_names)
        finally:
            app.state.workspace.close()

    def test_import_api_stages_confirms_and_removes_control_device_xml(self) -> None:
        source_graph = ControlGraph()
        ignition_graph = parse_ignition(IGNITION)
        app = create_app(
            resolve(source_graph, ignition_graph),
            serve_static=False,
            source_graph=source_graph,
            ignition_graph=ignition_graph,
        )
        try:
            staged, confirmed, imported_graph, removed = asyncio.run(
                import_workflow(app, SOURCE_PROJECT, [])
            )
            self.assertEqual(staged.status_code, 200)
            project = staged.json()["staged"][0]
            self.assertEqual(project["importKind"], "source")
            self.assertEqual(project["fileType"], "Control-device XML project")
            self.assertEqual(project["configurationFormat"], "xml")
            self.assertEqual(project["rootElement"], "RTACProject")
            self.assertEqual(project["projectName"], "Water_Plant_Controller")
            self.assertEqual(project["sourceDeviceCount"], 2)
            self.assertEqual(project["protocolPointCount"], 2)
            self.assertEqual(project["tagProviders"], [])

            self.assertEqual(confirmed.status_code, 200)
            imported = confirmed.json()["imports"][0]
            self.assertEqual(imported["selectedTagProviders"], [])
            imported_payload = imported_graph.json()
            imported_names = {node["name"] for node in imported_payload["nodes"]}
            self.assertIn("Relay_A", imported_names)
            self.assertIn("[default]Pump_01/Run", imported_names)
            self.assertTrue(any(
                edge["kind"] == "communication_identity_match"
                for edge in imported_payload["edges"]
            ))

            self.assertEqual(removed.status_code, 200)
            restored_names = {node["name"] for node in removed.json()["graph"]["nodes"]}
            self.assertNotIn("Relay_A", restored_names)
            self.assertIn("[default]Pump_01/Run", restored_names)
        finally:
            app.state.workspace.close()

    def test_built_mui_frontend_is_served_when_available(self) -> None:
        if not (ROOT / "frontend" / "dist" / "index.html").exists():
            self.skipTest("Run 'make build' to create the frontend")
        app = create_app(build_graph(SOURCE_PROJECT, IGNITION), serve_static=True)
        try:
            response, = asyncio.run(get_responses(app, "/"))
            self.assertEqual(response.status_code, 200)
            self.assertIn("ControlGraph", response.text)
        finally:
            app.state.workspace.close()


def directed_path(
    graph,
    start: str,
    end: str,
    ignored_edge_kinds: set[str] | None = None,
) -> list[str] | None:
    ignored = ignored_edge_kinds or set()
    queue = deque([(start, [start])])
    seen = {start}
    while queue:
        current, path = queue.popleft()
        if current == end:
            return path
        for edge in graph.edges.values():
            if (
                edge.source != current
                or edge.status != "resolved"
                or edge.kind in ignored
                or edge.target in seen
            ):
                continue
            seen.add(edge.target)
            queue.append((edge.target, [*path, edge.target]))
    return None


async def get_responses(app, *paths: str) -> tuple[httpx.Response, ...]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://controlgraph.test") as client:
        return tuple([await client.get(path) for path in paths])


async def import_workflow(
    app,
    path: Path,
    tag_providers: list[str] | None = None,
) -> tuple[httpx.Response, ...]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://controlgraph.test") as client:
        with path.open("rb") as upload:
            staged = await client.post(
                "/api/imports/stage",
                content=upload.read(),
                headers={
                    "Content-Type": "application/octet-stream",
                    "X-ControlGraph-Filename": path.name,
                },
            )
        record_id = staged.json()["staged"][0]["id"]
        confirmed = await client.post(
            "/api/imports/confirm",
            json={
                "selections": [{
                    "stagedId": record_id,
                    "tagProviders": ["default"] if tag_providers is None else tag_providers,
                }],
            },
        )
        imported_graph = await client.get("/api/graph")
        removed = await client.delete(f"/api/imports/{record_id}")
        return staged, confirmed, imported_graph, removed


def create_gateway_backups(root: Path) -> tuple[Path, Path]:
    database = root / "gateway.idb"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE TAGCONFIG "
            "(ID TEXT, PROVIDERID INTEGER, FOLDERID TEXT, RANK INTEGER, NAME TEXT, CFG TEXT)"
        )
        connection.execute("CREATE TABLE SIMPLETAGPROVIDERPROFILE (PROVIDERID INTEGER, NAME TEXT)")
        connection.execute(
            "CREATE TABLE OPCSERVERSETTINGS (NAME TEXT, TYPE TEXT, ENDPOINTURL TEXT)"
        )
        connection.execute("INSERT INTO SIMPLETAGPROVIDERPROFILE VALUES (1, 'System')")
        connection.execute(
            "INSERT INTO OPCSERVERSETTINGS VALUES "
            "('CODESYS Connection', 'OPC UA', 'opc.tcp://codesys-controller:4840')"
        )
        rows = [
            ("default-1", 0, None, 0, "testtag1"),
            ("default-2", 0, None, 1, "testtag2"),
            ("system-1", 1, None, 0, "SystemTag"),
        ]
        for tag_id, provider_id, folder_id, rank, name in rows:
            configuration = (
                '{"name":"' + name + '","tagType":"AtomicTag","valueSource":"memory"}'
            )
            connection.execute(
                "INSERT INTO TAGCONFIG VALUES (?, ?, ?, ?, ?, ?)",
                (tag_id, provider_id, folder_id, rank, name, configuration),
            )

    gateway_81 = root / "gateway-8.1.gwbk"
    with zipfile.ZipFile(gateway_81, "w") as archive:
        archive.writestr(
            "backupinfo.xml",
            "<backup><version>8.1.48.2025042910</version><backup-type>DATA_ONLY</backup-type></backup>",
        )
        archive.write(database, "db_backup_sqlite.idb")
        archive.writestr("projects/test/project.json", "{}")

    gateway_83 = root / "gateway-8.3.gwbk"
    with zipfile.ZipFile(gateway_83, "w") as archive:
        archive.writestr(
            "backupinfo.xml",
            "<backup><version>8.3.6.2026042713</version><backup-type>ALL</backup-type></backup>",
        )
        archive.writestr(
            "config/resources/core/ignition/tag-definition/default/tags.json",
            '[{"name":"test","tagType":"AtomicTag","valueSource":"memory"}]',
        )
        archive.writestr(
            "config/resources/core/ignition/tag-definition/fes/tags.json",
            '[{"name":"Other","tagType":"AtomicTag","valueSource":"memory"}]',
        )
        archive.writestr("projects/test/project.json", "{}")
    return gateway_81, gateway_83


def create_parameterized_backup(root: Path) -> Path:
    definition = [{
        "name": "ParameterizedDevice",
        "parameters": {
            "RTAC_Device": {"dataType": "String", "value": "Default Device"},
            "OPC_Connection_String": {
                "dataType": "String",
                "value": "{bindType=parameter, binding=RTAC_Device}",
            },
            "Control_Tag": {"dataType": "String", "value": "Binary Input 0"},
        },
        "tags": [
            {
                "name": "Status",
                "tagType": "AtomicTag",
                "valueSource": "opc",
                "opcItemPath": (
                    "{bindType=parameter, "
                    "binding={OPC_Connection_String}.{Control_Tag}}"
                ),
            },
            {
                "tagType": "AtomicTag",
                "valueSource": "memory",
            },
        ],
    }]
    instance = [{
        "name": "Motor_1",
        "tagType": "UdtInstance",
        "typeId": "ParameterizedDevice",
        "parameterValues": {
            "RTAC_Device": {"value": "RTAC_A"},
            "Control_Tag": {"value": "Binary Input 12"},
        },
    }]
    backup = root / "parameterized.gwbk"
    with zipfile.ZipFile(backup, "w") as archive:
        archive.writestr(
            "backupinfo.xml",
            "<backup><version>8.3.6.2026042713</version><backup-type>ALL</backup-type></backup>",
        )
        archive.writestr(
            "config/resources/core/ignition/tag-type-definition/default/Types/udts.json",
            json.dumps(definition),
        )
        archive.writestr(
            "config/resources/core/ignition/tag-definition/default/Area/tags.json",
            json.dumps(instance),
        )
        archive.writestr(
            "config/resources/core/com.inductiveautomation.opcua/device/RTAC_A/config.json",
            json.dumps({
                "type": "DNP3",
                "hostname": "10.20.1.20",
                "destinationAddress": 1,
            }),
        )
    return backup


def create_nested_parameterized_backup(root: Path) -> Path:
    backup = create_parameterized_backup(root)
    nested = root / "nested-parameterized.gwbk"
    with zipfile.ZipFile(backup) as source, zipfile.ZipFile(nested, "w") as target:
        definition = json.loads(source.read(
            "config/resources/core/ignition/tag-type-definition/default/Types/udts.json"
        ))[0]
        instance = json.loads(source.read(
            "config/resources/core/ignition/tag-definition/default/Area/tags.json"
        ))[0]
        target.writestr(
            "backupinfo.xml",
            "<backup><version>8.3.6.2026042713</version><backup-type>ALL</backup-type></backup>",
        )
        target.writestr(
            "config/resources/core/ignition/tag-definition/default/tags.json",
            json.dumps([
                {"name": "_types_", "tagType": "Folder", "tags": [definition]},
                {"name": "Area", "tagType": "Folder", "tags": [instance]},
            ]),
        )
        target.writestr(
            "config/resources/core/com.inductiveautomation.opcua/device/RTAC_A/config.json",
            source.read(
                "config/resources/core/com.inductiveautomation.opcua/device/RTAC_A/config.json"
            ),
        )
    return nested


def create_unresolved_parameter_backup(root: Path) -> Path:
    backup = root / "unresolved-parameter.gwbk"
    definition = [{
        "name": "IncompleteDevice",
        "parameters": {},
        "tags": [{
            "name": "Status",
            "tagType": "AtomicTag",
            "valueSource": "opc",
            "opcItemPath": "{OPC_Connection_String}.{Control_Tag}",
        }],
    }]
    instance = [{
        "name": "Incomplete_1",
        "tagType": "UdtInstance",
        "typeId": "IncompleteDevice",
    }]
    with zipfile.ZipFile(backup, "w") as archive:
        archive.writestr(
            "backupinfo.xml",
            "<backup><version>8.3.6.2026042713</version><backup-type>ALL</backup-type></backup>",
        )
        archive.writestr(
            "config/resources/core/ignition/tag-type-definition/default/Types/udts.json",
            json.dumps(definition),
        )
        archive.writestr(
            "config/resources/core/ignition/tag-definition/default/Area/tags.json",
            json.dumps(instance),
        )
    return backup


def create_child_udt_backup(root: Path) -> Path:
    point_definition = {
        "name": "PointType",
        "parameters": {
            "Device": "Default Device",
            "Point": "Binary Input 0",
        },
        "tags": [{
            "name": "Value",
            "tagType": "AtomicTag",
            "valueSource": "opc",
            "opcItemPath": "{Device}.{Point}",
        }],
    }
    assembly_definition = {
        "name": "AssemblyType",
        "parameters": {
            "Connection": "Default Device",
            "ControlTag": "Binary Input 0",
        },
        "tags": [{
            "name": "PointInstance",
            "tagType": "UdtInstance",
            "typeId": "PointType",
            "parameterBindings": {
                "Device": {"bindType": "parameter", "binding": "Connection"},
                "Point": {"bindType": "parameter", "binding": "ControlTag"},
            },
        }],
    }
    instance = [{
        "name": "Assembly_1",
        "tagType": "UdtInstance",
        "typeId": "AssemblyType",
        "paramValues": {
            "Connection": "RTAC_A",
            "ControlTag": "Binary Input 12",
        },
    }]
    backup = root / "child-udt.gwbk"
    with zipfile.ZipFile(backup, "w") as archive:
        archive.writestr(
            "backupinfo.xml",
            "<backup><version>8.3.6.2026042713</version><backup-type>ALL</backup-type></backup>",
        )
        archive.writestr(
            "config/resources/core/ignition/tag-type-definition/default/Types/udts.json",
            json.dumps([point_definition, assembly_definition]),
        )
        archive.writestr(
            "config/resources/core/ignition/tag-definition/default/Area/tags.json",
            json.dumps(instance),
        )
        archive.writestr(
            "config/resources/core/com.inductiveautomation.opcua/device/RTAC_A/config.json",
            json.dumps({
                "type": "DNP3",
                "hostname": "10.20.1.20",
                "destinationAddress": 1,
            }),
        )
    return backup


def create_codesys_parameter_backup(
    root: Path,
    *,
    include_connection: bool = True,
    include_server_property: bool = True,
) -> Path:
    definition = [{
        "name": "CodesysPoint",
        "parameters": {
            "OPC Connection String": "nsu=CODESYSSPV3/3S/IecVarAccess;s=|var|Logic.Application",
            "RTAC Device": ".LV_Meter_MODBUS",
        },
        "tags": [{
            "name": "Value",
            "tagType": "AtomicTag",
            "valueSource": "opc",
            "opcItemPath": "{OPC Connection String}{RTAC Device}",
        }],
    }]
    if include_server_property:
        definition[0]["tags"][0]["opcServer"] = "CODESYS Connection"
    instance = [{
        "name": "Meter_1",
        "tagType": "UdtInstance",
        "typeId": "CodesysPoint",
    }]
    backup = root / "codesys-parameter.gwbk"
    with zipfile.ZipFile(backup, "w") as archive:
        archive.writestr(
            "backupinfo.xml",
            "<backup><version>8.3.6.2026042713</version><backup-type>ALL</backup-type></backup>",
        )
        archive.writestr(
            "config/resources/core/ignition/tag-type-definition/default/Types/udts.json",
            json.dumps(definition),
        )
        archive.writestr(
            "config/resources/core/ignition/tag-definition/default/Area/tags.json",
            json.dumps(instance),
        )
        if include_connection:
            archive.writestr(
                "config/resources/core/com.inductiveautomation.opcua/client-connection/"
                "CODESYS Connection/config.json",
                json.dumps({
                    "name": "CODESYS Connection",
                    "type": "OPC UA",
                    "endpointUrl": "opc.tcp://codesys-controller:4840",
                }),
            )
        archive.writestr(
            "config/resources/core/com.inductiveautomation.opcua/device/"
            "LV_Meter_MODBUS/config.json",
            json.dumps({
                "type": "Modbus TCP",
                "hostname": "10.20.1.30",
                "unitId": 1,
            }),
        )
    return backup


def create_shared_codesys_signal_backup(root: Path) -> Path:
    item_path = (
        "nsu=CODESYSSPV3/3S/IecVarAccess;"
        "s=|var|Logic.Application.Meter.Voltage"
    )
    backup = root / "shared-codesys-signal.gwbk"
    tags = [
        {
            "name": name,
            "tagType": "AtomicTag",
            "valueSource": "opc",
            "opcServer": "CODESYS Connection",
            "opcItemPath": item_path,
        }
        for name in ("Voltage", "VoltageAlias")
    ]
    with zipfile.ZipFile(backup, "w") as archive:
        archive.writestr(
            "backupinfo.xml",
            "<backup><version>8.3.6.2026042713</version><backup-type>ALL</backup-type></backup>",
        )
        archive.writestr(
            "config/resources/core/ignition/tag-definition/default/tags.json",
            json.dumps(tags),
        )
        archive.writestr(
            "config/resources/core/com.inductiveautomation.opcua/client-connection/"
            "CODESYS Connection/config.json",
            json.dumps({
                "name": "CODESYS Connection",
                "type": "OPC UA",
                "endpointUrl": "opc.tcp://codesys-controller:4840",
            }),
        )
    return backup


def create_numeric_namespace_backup(root: Path) -> Path:
    backup = root / "numeric-namespace.gwbk"
    with zipfile.ZipFile(backup, "w") as archive:
        archive.writestr(
            "backupinfo.xml",
            "<backup><version>8.3.6.2026042713</version><backup-type>ALL</backup-type></backup>",
        )
        archive.writestr(
            "config/resources/core/ignition/tag-definition/default/tags.json",
            json.dumps([{
                "name": "NestedVoltage",
                "tagType": "AtomicTag",
                "valueSource": "opc",
                "opcItemPath": "ns=2;s=|var|Logic.Application.Program.Struct.Voltage",
            }]),
        )
    return backup


def create_opc_scope_backup(root: Path) -> Path:
    definition = {
        "name": "AuditMeter",
        "parameters": {
            "BasePath": "nsu=CODESYSSPV3/3S/IecVarAccess;s=|var|Logic.Application.Meter01",
        },
        "tags": [
            {
                "name": "Voltage",
                "tagType": "AtomicTag",
                "valueSource": "opc",
                "opcServer": "CODESYS Connection",
                "opcItemPath": "{BasePath}.Voltage",
            },
            {"name": "Power", "tagType": "AtomicTag", "valueSource": "expression"},
            {"name": "DisplayName", "tagType": "AtomicTag", "valueSource": "memory"},
            {"name": "VoltageRef", "tagType": "AtomicTag", "valueSource": "reference"},
            {"name": "QueryValue", "tagType": "AtomicTag", "valueSource": "query"},
            {"name": "DerivedValue", "tagType": "AtomicTag", "valueSource": "derived"},
        ],
    }
    direct_tags = [
        {
            "name": "DirectOpc",
            "tagType": "AtomicTag",
            "valueSource": "opc",
            "opcServer": "CODESYS Connection",
            "opcItemPath": (
                "nsu=CODESYSSPV3/3S/IecVarAccess;"
                "s=|var|Logic.Application.Meter01.Current"
            ),
        },
        {"name": "DirectExpression", "tagType": "AtomicTag", "valueSource": "expression"},
        {"name": "DirectMemory", "tagType": "AtomicTag", "valueSource": "memory"},
        {"name": "DirectReference", "tagType": "AtomicTag", "valueSource": "reference"},
        {"name": "DirectQuery", "tagType": "AtomicTag", "valueSource": "query"},
        {"name": "DirectDerived", "tagType": "AtomicTag", "valueSource": "derived"},
        {"name": "InvalidOpc", "tagType": "AtomicTag", "valueSource": "opc"},
        {
            "name": "MissingConnection",
            "tagType": "AtomicTag",
            "valueSource": "opc",
            "opcServer": "Missing OPC Connection",
            "opcItemPath": (
                "nsu=CODESYSSPV3/3S/IecVarAccess;"
                "s=|var|Logic.Application.Meter01.Frequency"
            ),
        },
    ]
    backup = root / "opc-scope.gwbk"
    with zipfile.ZipFile(backup, "w") as archive:
        archive.writestr(
            "backupinfo.xml",
            "<backup><version>8.3.6.2026042713</version><backup-type>ALL</backup-type></backup>",
        )
        archive.writestr(
            "config/resources/core/ignition/tag-type-definition/default/Types/udts.json",
            json.dumps([definition]),
        )
        archive.writestr(
            "config/resources/core/ignition/tag-definition/default/Area/tags.json",
            json.dumps([{
                "name": "Meter01",
                "tagType": "UdtInstance",
                "typeId": "AuditMeter",
            }]),
        )
        archive.writestr(
            "config/resources/core/ignition/tag-definition/default/tags.json",
            json.dumps(direct_tags),
        )
        archive.writestr(
            "config/resources/core/com.inductiveautomation.opcua/client-connection/"
            "CODESYS Connection/config.json",
            json.dumps({
                "name": "CODESYS Connection",
                "type": "OPC UA",
                "endpointUrl": "opc.tcp://codesys-controller:4840",
            }),
        )
    return backup


if __name__ == "__main__":
    unittest.main()
