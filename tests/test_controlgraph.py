from __future__ import annotations

from collections import deque
import asyncio
from pathlib import Path
import tempfile
import unittest
import zipfile

from controlgraph.ignition_parser import parse_ignition
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
        schema = app.openapi()
        self.assertIn("/api/graph", schema["paths"])
        self.assertEqual(schema["info"]["title"], "ControlGraph API")
        health, response, docs = asyncio.run(
            get_responses(app, "/api/health", "/api/graph", "/docs")
        )
        self.assertEqual(health.json(), {"status": "ok"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["summary"]["nodeCount"], len(graph.nodes))
        self.assertEqual(docs.status_code, 200)

    def test_built_mui_frontend_is_served_when_available(self) -> None:
        if not (ROOT / "frontend" / "dist" / "index.html").exists():
            self.skipTest("Run 'make build' to create the frontend")
        app = create_app(build_graph(SEL, IGNITION), serve_static=True)
        response, = asyncio.run(get_responses(app, "/"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("ControlGraph", response.text)


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


if __name__ == "__main__":
    unittest.main()
