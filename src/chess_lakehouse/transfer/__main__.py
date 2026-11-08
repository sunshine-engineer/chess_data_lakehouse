"""Run a dry-run plan or an acknowledged maintenance-window database transfer."""
import argparse
import json
import os
import psycopg

from .settings import load
from .service import plan, run


def main():
    """Parse non-secret CLI arguments and emit only sanitized operational results."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config/postgres-transfer.toml")
    parser.add_argument("--workdir", default="/migration")
    parser.add_argument("--run", action="store_true", help="Write an initially empty destination; default is plan only")
    parser.add_argument("--maintenance-window", action="store_true", help="Source and destination application writers are stopped")
    parser.add_argument("--recover-tracker", action="store_true")
    args = parser.parse_args()
    if os.environ.get("CHESS_CONTAINER") != "1": parser.error("Run the migration tool inside Docker")
    try:
        source, target, options = load(args.config)
        result = run(source, target, options, args.workdir, args.maintenance_window, args.recover_tracker) if args.run else plan(source, target, options)
        print(json.dumps(result, default=str, sort_keys=True, indent=2))
    except Exception as exc:
        # Native restore logs and dump artifacts are private; never echo SQL/data.
        message = 'Database operation failed; inspect the private logs and database prerequisites' if isinstance(exc, psycopg.Error) else str(exc)
        parser.exit(1, f"Migration stopped ({type(exc).__name__}): {message}\n")


if __name__ == "__main__": main()
