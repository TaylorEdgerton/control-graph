from __future__ import annotations

import argparse
import json
from pathlib import Path

from .loader import load_sources
from .resolver import resolve
from .server import serve


def main() -> None:
    parser = argparse.ArgumentParser(description="Trace SEL RTAC data to Ignition tags.")
    parser.add_argument("--sel", type=Path, help="Path to an SEL RTAC XML export")
    parser.add_argument("--ignition", type=Path, help="Path to an Ignition .gwbk file or extracted directory")
    parser.add_argument("--demo", action="store_true", help="Use the included demonstration files")
    parser.add_argument("--export", type=Path, help="Write the resolved model to JSON and exit")
    parser.add_argument("--host", default="127.0.0.1", help="Local bind address")
    parser.add_argument("--port", default=8765, type=int, help="Local port")
    parser.add_argument("--api-only", action="store_true", help="Run the API without the built frontend")
    args = parser.parse_args()

    if args.demo:
        root = Path(__file__).resolve().parent.parent
        sel_path = root / "examples" / "sel_project.xml"
        ignition_path = root / "examples" / "ignition_backup"
    else:
        sel_path = args.sel
        ignition_path = args.ignition
    if not sel_path or not ignition_path:
        parser.error("Use --demo, or give both --sel and --ignition.")

    sel_graph, ignition_graph = load_sources(sel_path, ignition_path)
    graph = resolve(sel_graph, ignition_graph)
    if args.export:
        args.export.write_text(json.dumps(graph.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Wrote {len(graph.nodes)} nodes and {len(graph.edges)} edges to {args.export}")
        return
    serve(
        graph,
        args.host,
        args.port,
        serve_static=not args.api_only,
        sel_graph=sel_graph,
        ignition_graph=ignition_graph,
    )


if __name__ == "__main__":
    main()
