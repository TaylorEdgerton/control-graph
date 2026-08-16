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
from controlgraph.cli import main
from controlgraph.loader import build_graph
from controlgraph.model import ControlNode, stable_id
from controlgraph.resolver import resolve
from controlgraph.server import create_app
from controlgraph.sel_parser import parse_sel
import httpx


ROOT = Path(__file__).resolve().parent.parent
SEL = ROOT / "examples" / "sel_project.xml"
IGNITION = ROOT / "examples" / "ignition_backup"


class ControlGraphTests(unittest.TestCase):
    def test_cli_starts_without_example_inputs(self) -> None:
        with patch("controlgraph.cli.serve") as serve_mock:
            with patch("sys.argv", ["controlgraph", "--api-only"]):
                main()

        graph = serve_mock.call_args.args[0]
        self.assertEqual(graph.summary()["nodeCount"], 0)
        self.assertEqual(serve_mock.call_args.kwargs["sel_graph"].summary()["nodeCount"], 0)
        self.assertEqual(serve_mock.call_args.kwargs["ignition_graph"].summary()["nodeCount"], 0)

    def test_demo_has_complete_deterministic_lineage(self) -> None:
        graph = build_graph(SEL, IGNITION)
        start = next(node.id for node in graph.nodes.values() if node.name == "Relay_A")
        end = next(
            node.id for node in graph.nodes.values()
            if node.name == "[default]Pump_01/Run" and node.kind == "IGNITION_TAG"
        )
        path = directed_path(graph, start, end)

        self.assertIsNotNone(path)
        kinds = [graph.nodes[node_id].kind for node_id in path]
        self.assertEqual(
            kinds,
            [
                "SEL_DEVICE", "PROTOCOL_POINT", "RTAC_TAG", "IEC_LOGIC", "RTAC_TAG",
                "PROTOCOL_POINT", "IGNITION_DEVICE", "OPC_ITEM", "UDT_MEMBER", "IGNITION_TAG",
            ],
        )
        match = next(edge for edge in graph.edges.values() if edge.kind == "communication_identity_match")
        self.assertEqual(match.status, "resolved")
        self.assertEqual(match.attributes["identityKey"], "dnp3|10.20.1.20|1|binary input|12")
        self.assertGreaterEqual(len(match.evidence), 3)

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
            self.assertEqual(tags_81, {"[default]testtag1", "[default]testtag2"})
            self.assertEqual(tags_83, {"[default]test"})

    def test_udt_instance_parameters_resolve_the_opc_item_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            backup = create_parameterized_backup(Path(temp))
            graph = parse_ignition(backup, ["default"])

        source = next(node for node in graph.nodes.values() if node.kind == "OPC_ITEM")
        tag = next(node for node in graph.nodes.values() if node.kind == "IGNITION_TAG")
        instance = next(node for node in graph.nodes.values() if node.kind == "UDT_INSTANCE")
        self.assertEqual(source.name, "Ignition OPC UA Server.Line1.IED_7")
        self.assertEqual(
            source.attributes["opcItemPathTemplate"],
            "{OPC_Connection_String}.{IED}",
        )
        self.assertEqual(
            source.attributes["resolvedParameters"]["OPC_Connection_String"],
            "Ignition OPC UA Server.Line1",
        )
        self.assertEqual(tag.name, "[default]Area/Motor_1/Status")
        self.assertEqual(instance.attributes["resolvedParameters"]["IED"], "IED_7")

    def test_unresolved_mapping_is_explicit(self) -> None:
        graph = build_graph(SEL, IGNITION)
        issues = [node for node in graph.nodes.values() if node.kind == "MAPPING_ISSUE"]
        self.assertTrue(any("No SEL protocol point" in issue.name for issue in issues))
        unresolved = [edge for edge in graph.edges.values() if edge.status == "unresolved"]
        self.assertTrue(unresolved)

    def test_ambiguous_mapping_is_explicit_and_not_resolved(self) -> None:
        sel = parse_sel(SEL)
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
        graph = resolve(sel, ignition)
        issues = [node for node in graph.nodes.values() if node.attributes.get("status") == "ambiguous"]
        self.assertEqual(len(issues), 1)
        point = next(node for node in graph.nodes.values() if node.name == "Published_Run")
        resolved = [edge for edge in graph.edges.values() if edge.source == point.id and edge.kind == "communication_identity_match"]
        self.assertEqual(resolved, [])

    def test_fastapi_exposes_documented_graph_endpoint(self) -> None:
        graph = build_graph(SEL, IGNITION)
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
        graph = build_graph(SEL, IGNITION)
        app = create_app(
            graph,
            serve_static=False,
            sel_graph=parse_sel(SEL),
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
                self.assertIn("[default]ApiTag", imported_names)
                self.assertEqual(removed.status_code, 200)
                restored_names = {node["name"] for node in removed.json()["graph"]["nodes"]}
                self.assertIn("[default]Pump_01/Run", restored_names)
        finally:
            app.state.workspace.close()

    def test_built_mui_frontend_is_served_when_available(self) -> None:
        if not (ROOT / "frontend" / "dist" / "index.html").exists():
            self.skipTest("Run 'make build' to create the frontend")
        app = create_app(build_graph(SEL, IGNITION), serve_static=True)
        try:
            response, = asyncio.run(get_responses(app, "/"))
            self.assertEqual(response.status_code, 200)
            self.assertIn("ControlGraph", response.text)
        finally:
            app.state.workspace.close()


def directed_path(graph, start: str, end: str) -> list[str] | None:
    queue = deque([(start, [start])])
    seen = {start}
    while queue:
        current, path = queue.popleft()
        if current == end:
            return path
        for edge in graph.edges.values():
            if edge.source != current or edge.status != "resolved" or edge.target in seen:
                continue
            seen.add(edge.target)
            queue.append((edge.target, [*path, edge.target]))
    return None


async def get_responses(app, *paths: str) -> tuple[httpx.Response, ...]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://controlgraph.test") as client:
        return tuple([await client.get(path) for path in paths])


async def import_workflow(app, path: Path) -> tuple[httpx.Response, ...]:
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
            json={"selections": [{"stagedId": record_id, "tagProviders": ["default"]}]},
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
        connection.execute("INSERT INTO SIMPLETAGPROVIDERPROFILE VALUES (1, 'System')")
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
        "tagType": "UdtType",
        "parameters": {
            "Gateway": {"dataType": "String", "value": "Default Gateway"},
            "Channel": {"dataType": "String", "value": "Default Channel"},
            "OPC_Connection_String": {
                "dataType": "String",
                "value": "{Gateway}.{Channel}",
            },
            "IED": {"dataType": "String", "value": "Default IED"},
        },
        "tags": [{
            "name": "Status",
            "tagType": "AtomicTag",
            "valueSource": "opc",
            "opcItemPath": {
                "bindType": "parameter",
                "binding": "{OPC_Connection_String}.{IED}",
            },
        }],
    }]
    instance = [{
        "name": "Motor_1",
        "tagType": "UdtInstance",
        "typeId": "ParameterizedDevice",
        "parameters": {
            "Gateway": {"value": "Ignition OPC UA Server"},
            "Channel": {"value": "Line1"},
            "IED": {"value": "IED_7"},
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
    return backup


if __name__ == "__main__":
    unittest.main()
