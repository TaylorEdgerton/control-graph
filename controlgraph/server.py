from __future__ import annotations

from pathlib import Path
import mimetypes
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field
import uvicorn

from .model import ControlGraph


class EvidenceResponse(BaseModel):
    source: str = Field(description="The input file that supports this item")
    location: str = Field(description="The location in the input file")
    detail: str = Field(description="A short source excerpt or configuration summary")


class NodeResponse(BaseModel):
    id: str
    kind: str
    name: str
    system: str
    attributes: dict[str, Any]
    evidence: list[EvidenceResponse]


class EdgeResponse(BaseModel):
    id: str
    source: str
    target: str
    kind: str
    status: str
    attributes: dict[str, Any]
    evidence: list[EvidenceResponse]


class SummaryResponse(BaseModel):
    nodeCount: int
    edgeCount: int
    nodeKinds: dict[str, int]
    edgeStatuses: dict[str, int]


class GraphResponse(BaseModel):
    nodes: list[NodeResponse]
    edges: list[EdgeResponse]
    summary: SummaryResponse


def create_app(graph: ControlGraph, *, serve_static: bool = True) -> FastAPI:
    app = FastAPI(
        title="ControlGraph API",
        version="0.1.0",
        description="Inspect the resolved lineage between an SEL RTAC project and Ignition tags.",
        contact={"name": "Local ControlGraph PoC"},
    )
    payload = graph.to_dict()

    @app.get("/api/health", tags=["system"], summary="Check API health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get(
        "/api/graph",
        response_model=GraphResponse,
        tags=["lineage"],
        summary="Get the complete resolved control graph",
    )
    async def get_graph() -> dict[str, Any]:
        return payload

    @app.get("/api/nodes/{node_id}", response_model=NodeResponse, tags=["lineage"], summary="Get one node")
    async def get_node(node_id: str) -> dict[str, Any] | JSONResponse:
        node = next((item for item in payload["nodes"] if item["id"] == node_id), None)
        if node is None:
            return JSONResponse(status_code=404, content={"detail": "The node does not exist."})
        return node

    if serve_static:
        static_root = Path(__file__).resolve().parent.parent / "frontend" / "dist"
        if not static_root.joinpath("index.html").exists():
            raise RuntimeError("The frontend build is missing. Run 'make build'.")
        static_root = static_root.resolve()

        @app.get("/{path:path}", include_in_schema=False)
        async def frontend(path: str) -> Response:
            requested = (static_root / path).resolve()
            if not requested.is_relative_to(static_root):
                return Response(status_code=404, content="The file does not exist.")
            if requested.is_dir():
                requested = requested / "index.html"
            if not requested.is_file():
                requested = static_root / "index.html"
            media_type = mimetypes.guess_type(requested.name)[0] or "application/octet-stream"
            return Response(content=requested.read_bytes(), media_type=media_type)
    else:
        @app.get("/", tags=["system"], summary="Get development API links")
        async def api_root() -> dict[str, str]:
            return {"message": "ControlGraph API is ready.", "docs": "/docs", "graph": "/api/graph"}
    return app


def serve(
    graph: ControlGraph,
    host: str = "127.0.0.1",
    port: int = 8765,
    *,
    serve_static: bool = True,
) -> None:
    print(f"ControlGraph is ready at http://{host}:{port}")
    print(f"API documentation is ready at http://{host}:{port}/docs")
    uvicorn.run(create_app(graph, serve_static=serve_static), host=host, port=port, log_level="info")
