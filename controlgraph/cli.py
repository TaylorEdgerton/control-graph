from __future__ import annotations

import argparse
import json
from pathlib import Path

from .loader import load_sources
from .resolver import resolve
from .server import serve


def main() -> None:
    parser = argparse.ArgumentParser(description="Trace source-device data to Ignition tags.")
    parser.add_argument("--source", type=Path, help="Optional path to an initial source-device XML export")
    parser.add_argument("--ignition", type=Path, help="Optional path to an initial Ignition backup")
    parser.add_argument("--export", type=Path, help="Write the resolved model to JSON and exit")
    parser.add_argument("--host", default="127.0.0.1", help="Local bind address")
    parser.add_argument("--port", default=8765, type=int, help="Local port")
    parser.add_argument("--api-only", action="store_true", help="Run the API without the built frontend")
    args = parser.parse_args()

    source_graph, ignition_graph = load_sources(args.source, args.ignition)
    graph = resolve(source_graph, ignition_graph)
    if args.export:
        args.export.write_text(json.dumps(graph.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Wrote {len(graph.nodes)} nodes and {len(graph.edges)} edges to {args.export}")
        return
    serve(
        graph,
        args.host,
        args.port,
        serve_static=not args.api_only,
        source_graph=source_graph,
        ignition_graph=ignition_graph,
    )


if __name__ == "__main__":
    main()
