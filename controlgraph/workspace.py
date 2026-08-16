from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path
import tempfile
from threading import RLock
from typing import Any
from uuid import uuid4

from .ignition_parser import GatewayBackupInfo, inspect_ignition_backup, parse_ignition
from .model import ControlGraph
from .resolver import resolve


@dataclass
class BackupRecord:
    id: str
    name: str
    path: Path
    size: int
    digest: str
    info: GatewayBackupInfo
    selected_tag_providers: tuple[str, ...] = ()
    graph: ControlGraph | None = None

    def to_dict(self, status: str) -> dict[str, Any]:
        result = {
            "id": self.id,
            "name": self.name,
            "size": self.size,
            "status": status,
            **self.info.to_dict(),
            "selectedTagProviders": list(self.selected_tag_providers),
        }
        if self.graph is not None:
            summary = self.graph.summary()
            audit = summary.get("audit", {})
            result.update(
                {
                    "nodeCount": summary["nodeCount"],
                    "deviceCount": (
                        summary["nodeKinds"].get("IGNITION_DEVICE", 0)
                        + summary["nodeKinds"].get("OPC_SERVER_CONNECTION", 0)
                    ),
                    "tagCount": summary["nodeKinds"].get("IGNITION_TAG", 0),
                    "totalTagCount": audit.get("totalTagCount", 0),
                    "opcTagCount": audit.get("opcTagCount", 0),
                    "excludedTagCount": audit.get("excludedTagCount", 0),
                    "invalidOpcPathCount": audit.get("invalidOpcPathCount", 0),
                    "missingConnectionCount": audit.get("missingConnectionCount", 0),
                }
            )
        return result


class AnalysisWorkspace:
    def __init__(
        self,
        initial_graph: ControlGraph,
        *,
        source_graph: ControlGraph | None = None,
        ignition_graph: ControlGraph | None = None,
    ) -> None:
        self._initial_graph = copy.deepcopy(initial_graph)
        self._source_graph = copy.deepcopy(source_graph)
        self._ignition_graph = copy.deepcopy(ignition_graph)
        self._current_graph = copy.deepcopy(initial_graph)
        self._temporary_directory = tempfile.TemporaryDirectory(prefix="controlgraph-workspace-")
        self._root = Path(self._temporary_directory.name)
        self._staged: dict[str, BackupRecord] = {}
        self._imports: dict[str, BackupRecord] = {}
        self._lock = RLock()

    def reserve_upload(self, original_name: str) -> tuple[str, Path]:
        record_id = uuid4().hex
        safe_name = Path(original_name.replace("\\", "/")).name or "gateway.gwbk"
        return record_id, self._root / f"{record_id}-{safe_name}"

    def finish_stage(
        self,
        record_id: str,
        name: str,
        path: Path,
        size: int,
        digest: str,
    ) -> dict[str, Any]:
        info = inspect_ignition_backup(path)
        safe_name = Path(name.replace("\\", "/")).name or "gateway.gwbk"
        with self._lock:
            existing = [*self._staged.values(), *self._imports.values()]
            if any(item.digest == digest for item in existing):
                path.unlink(missing_ok=True)
                raise ValueError(f"{safe_name} is already in the analysis workspace.")
            record = BackupRecord(record_id, safe_name, path, size, digest, info)
            self._staged[record_id] = record
            return record.to_dict("ready")

    def discard_unfinished(self, path: Path) -> None:
        if path.is_relative_to(self._root):
            path.unlink(missing_ok=True)

    def discard_stage(self, record_id: str) -> list[dict[str, Any]]:
        with self._lock:
            record = self._staged.pop(record_id, None)
            if record is None:
                raise KeyError(record_id)
            record.path.unlink(missing_ok=True)
            return self.list_staged()

    def confirm(self, selections: dict[str, list[str]]) -> dict[str, Any]:
        with self._lock:
            records = [self._staged.get(record_id) for record_id in selections]
            if not records or any(record is None for record in records):
                raise KeyError("One or more staged backups do not exist.")
            parsed: list[tuple[BackupRecord, ControlGraph, tuple[str, ...]]] = []
            for record in records:
                if record is None:
                    continue
                selected = tuple(selections[record.id])
                available = {provider.casefold() for provider in record.info.tag_providers}
                if not selected or any(provider.casefold() not in available for provider in selected):
                    raise ValueError(f"Select at least one valid tag provider for {record.name}.")
                parsed.append((record, parse_ignition(record.path, selected), selected))
            for record, graph, selected in parsed:
                record.graph = graph
                record.selected_tag_providers = selected
                self._imports[record.id] = record
                self._staged.pop(record.id, None)
            self._rebuild()
            return {"imports": self.list_imports(), "graph": self.graph_payload()}

    def remove_import(self, record_id: str) -> dict[str, Any]:
        with self._lock:
            record = self._imports.pop(record_id, None)
            if record is None:
                raise KeyError(record_id)
            record.path.unlink(missing_ok=True)
            self._rebuild()
            return {"imports": self.list_imports(), "graph": self.graph_payload()}

    def list_staged(self) -> list[dict[str, Any]]:
        with self._lock:
            return [record.to_dict("ready") for record in self._staged.values()]

    def list_imports(self) -> list[dict[str, Any]]:
        with self._lock:
            return [record.to_dict("imported") for record in self._imports.values()]

    def graph_payload(self) -> dict[str, Any]:
        with self._lock:
            return self._current_graph.to_dict()

    def close(self) -> None:
        self._temporary_directory.cleanup()

    def _rebuild(self) -> None:
        if self._source_graph is not None:
            ignition = ControlGraph()
            if self._imports:
                for record in self._imports.values():
                    if record.graph is not None:
                        ignition.merge(copy.deepcopy(record.graph))
            elif self._ignition_graph is not None:
                ignition.merge(copy.deepcopy(self._ignition_graph))
            self._current_graph = resolve(copy.deepcopy(self._source_graph), ignition)
            return

        graph = copy.deepcopy(self._initial_graph)
        for record in self._imports.values():
            if record.graph is not None:
                graph.merge(copy.deepcopy(record.graph))
        self._current_graph = graph
