from __future__ import annotations

from pathlib import Path

from .ignition_parser import parse_ignition
from .model import ControlGraph
from .resolver import resolve
from .sel_parser import parse_sel


def load_sources(
    sel_path: str | Path | None = None,
    ignition_path: str | Path | None = None,
) -> tuple[ControlGraph, ControlGraph]:
    sel_graph = parse_sel(sel_path) if sel_path else ControlGraph()
    ignition_graph = parse_ignition(ignition_path) if ignition_path else ControlGraph()
    return sel_graph, ignition_graph


def build_graph(sel_path: str | Path, ignition_path: str | Path) -> ControlGraph:
    sel_graph, ignition_graph = load_sources(sel_path, ignition_path)
    return resolve(sel_graph, ignition_graph)
