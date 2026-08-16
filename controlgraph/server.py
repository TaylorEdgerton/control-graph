from __future__ import annotations

import hashlib
from pathlib import Path
import mimetypes
from typing import Any
from urllib.parse import unquote

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field
import uvicorn

from .model import ControlGraph
from .workspace import AnalysisWorkspace


MAX_UPLOAD_SIZE = 500_000_000


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
    audit: dict[str, Any] = Field(default_factory=dict)


class GraphResponse(BaseModel):
    nodes: list[NodeResponse]
    edges: list[EdgeResponse]
    summary: SummaryResponse


class BackupFileResponse(BaseModel):
    id: str
    name: str
    size: int
    status: str
    fileType: str
    version: str
    versionFamily: str
    timestamp: str
    backupType: str
    configurationFormat: str
    configurationSource: str
    tagConfigurationCount: int
    tagProviders: list[str]
    projects: list[str]
    selectedTagProviders: list[str]
    nodeCount: int | None = None
    deviceCount: int | None = None
    tagCount: int | None = None
    totalTagCount: int | None = None
    opcTagCount: int | None = None
    excludedTagCount: int | None = None
    invalidOpcPathCount: int | None = None
    missingConnectionCount: int | None = None


class WorkspaceResponse(BaseModel):
    staged: list[BackupFileResponse]
    imports: list[BackupFileResponse]


class StagedResponse(BaseModel):
    staged: list[BackupFileResponse]


class ImportSelection(BaseModel):
    stagedId: str
    tagProviders: list[str] = Field(min_length=1)


class ConfirmImportsRequest(BaseModel):
    selections: list[ImportSelection] = Field(min_length=1)


class ImportMutationResponse(BaseModel):
    imports: list[BackupFileResponse]
    graph: GraphResponse


def create_app(
    graph: ControlGraph,
    *,
    serve_static: bool = True,
    source_graph: ControlGraph | None = None,
    ignition_graph: ControlGraph | None = None,
) -> FastAPI:
    app = FastAPI(
        title="ControlGraph API",
        version="0.1.0",
        description="Inspect the resolved lineage between a source-device project and Ignition tags.",
        contact={"name": "Local ControlGraph PoC"},
    )
    workspace = AnalysisWorkspace(graph, source_graph=source_graph, ignition_graph=ignition_graph)
    app.state.workspace = workspace
    app.add_event_handler("shutdown", workspace.close)

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
        return workspace.graph_payload()

    @app.get("/api/nodes/{node_id}", response_model=NodeResponse, tags=["lineage"], summary="Get one node")
    async def get_node(node_id: str) -> dict[str, Any] | JSONResponse:
        payload = workspace.graph_payload()
        node = next((item for item in payload["nodes"] if item["id"] == node_id), None)
        if node is None:
            return JSONResponse(status_code=404, content={"detail": "The node does not exist."})
        return node

    @app.get(
        "/api/imports",
        response_model=WorkspaceResponse,
        tags=["imports"],
        summary="List staged and imported Gateway backups",
    )
    async def list_imports() -> dict[str, Any]:
        return {"staged": workspace.list_staged(), "imports": workspace.list_imports()}

    @app.post(
        "/api/imports/stage",
        response_model=StagedResponse,
        tags=["imports"],
        summary="Upload and inspect Gateway backups before import",
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {
                    "application/octet-stream": {
                        "schema": {"type": "string", "format": "binary"}
                    }
                },
            }
        },
    )
    async def stage_import(
        request: Request,
        encoded_filename: str = Header(alias="X-ControlGraph-Filename"),
    ) -> dict[str, Any]:
        filename = unquote(encoded_filename) or "gateway.gwbk"
        record_id, destination = workspace.reserve_upload(filename)
        size = 0
        digest = hashlib.sha256()
        try:
            with destination.open("wb") as target:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > MAX_UPLOAD_SIZE:
                        raise ValueError("The selected backup exceeds the 500 MB upload limit.")
                    digest.update(chunk)
                    target.write(chunk)
            if size == 0:
                raise ValueError("The selected backup is empty.")
            result = workspace.finish_stage(
                record_id,
                filename,
                destination,
                size,
                digest.hexdigest(),
            )
        except (OSError, ValueError) as error:
            workspace.discard_unfinished(destination)
            raise HTTPException(status_code=400, detail=str(error)) from error
        except Exception:
            workspace.discard_unfinished(destination)
            raise
        return {"staged": [result]}

    @app.delete(
        "/api/imports/staged/{record_id}",
        response_model=StagedResponse,
        tags=["imports"],
        summary="Discard a staged Gateway backup",
    )
    async def discard_staged_import(record_id: str) -> dict[str, Any]:
        try:
            staged = workspace.discard_stage(record_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="The staged backup does not exist.") from error
        return {"staged": staged}

    @app.post(
        "/api/imports/confirm",
        response_model=ImportMutationResponse,
        tags=["imports"],
        summary="Confirm staged backups and add them to the analysis",
    )
    async def confirm_imports(request: ConfirmImportsRequest) -> dict[str, Any]:
        try:
            selections = {selection.stagedId: selection.tagProviders for selection in request.selections}
            return workspace.confirm(selections)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.delete(
        "/api/imports/{record_id}",
        response_model=ImportMutationResponse,
        tags=["imports"],
        summary="Remove a Gateway backup from the analysis",
    )
    async def remove_import(record_id: str) -> dict[str, Any]:
        try:
            return workspace.remove_import(record_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="The imported backup does not exist.") from error

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
    source_graph: ControlGraph | None = None,
    ignition_graph: ControlGraph | None = None,
) -> None:
    print(f"ControlGraph is ready at http://{host}:{port}")
    print(f"API documentation is ready at http://{host}:{port}/docs")
    uvicorn.run(
        create_app(
            graph,
            serve_static=serve_static,
            source_graph=source_graph,
            ignition_graph=ignition_graph,
        ),
        host=host,
        port=port,
        log_level="info",
    )
