from __future__ import annotations

from pathlib import Path

from .ignition_parser import parse_ignition
from .model import ControlGraph
from .resolver import resolve
from .sel_parser import parse_sel


def build_graph(sel_path: str | Path, ignition_path: str | Path) -> ControlGraph:
    return resolve(parse_sel(sel_path), parse_ignition(ignition_path))
