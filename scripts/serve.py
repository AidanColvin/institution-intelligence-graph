"""
Start the standalone UNC Research Graph API server.
Usage:
  python scripts/serve.py [--host 0.0.0.0] [--port 8001] [--db path/to/graph.db]
"""
import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def main() -> None:
    parser = argparse.ArgumentParser(description="UNC Research Graph API server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("--db", default=None, help="Path to graph.db (overrides GRAPH_DB_PATH)")
    args = parser.parse_args()

    if args.db:
        os.environ["GRAPH_DB_PATH"] = args.db

    import uvicorn
    uvicorn.run("backend.serve.api:app", host=args.host, port=args.port, reload=False)


if __name__ == "__main__":
    main()
