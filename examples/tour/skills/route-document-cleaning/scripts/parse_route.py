"""Locate the selected checkout and invoke its shared parser, without copying business logic."""

import argparse
import os
from pathlib import Path

parser = argparse.ArgumentParser(add_help=False)
parser.add_argument("--repo-root", required=True, type=Path)
args, rest = parser.parse_known_args()
root = args.repo_root.resolve()
python = root / ".venv/bin/python"
if not python.is_file() or not (root / "examples/tour/api/route_document_workflow.py").is_file():
    raise SystemExit(
        "Select a configured Tour_Agent checkout containing the shared document workflow"
    )
env = dict(os.environ, PYTHONPATH=str(root / "examples"))
os.chdir(root)
os.execve(python, [str(python), "-m", "tour.api.route_document_workflow", *rest], env)
