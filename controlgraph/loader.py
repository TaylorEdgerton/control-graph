from __future__ import annotations

from pathlib import Path

from .ignition_parser import parse_ignition
from .model import ControlGraph
from .resolver import resolve
from .source_parser import parse_source


def load_sources(
    source_path: str | Path | None = None,
    ignition_path: str | Path | None = None,
) -> tuple[ControlGraph, ControlGraph]:
    source_graph = parse_source(source_path) if source_path else ControlGraph()
    ignition_graph = parse_ignition(ignition_path) if ignition_path else ControlGraph()
    return source_graph, ignition_graph


def build_graph(source_path: str | Path, ignition_path: str | Path) -> ControlGraph:
    source_graph, ignition_graph = load_sources(source_path, ignition_path)
    return resolve(source_graph, ignition_graph)
