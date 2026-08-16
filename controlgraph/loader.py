from __future__ import annotations

from pathlib import Path

from .ignition_parser import parse_ignition
from .model import ControlGraph
from .resolver import resolve
from .sel_parser import parse_sel


def load_sources(
    sel_path: str | Path,
    ignition_path: str | Path,
) -> tuple[ControlGraph, ControlGraph]:
    return parse_sel(sel_path), parse_ignition(ignition_path)


def build_graph(sel_path: str | Path, ignition_path: str | Path) -> ControlGraph:
    sel_graph, ignition_graph = load_sources(sel_path, ignition_path)
    return resolve(sel_graph, ignition_graph)
