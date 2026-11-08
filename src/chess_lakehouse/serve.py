"""Configurable WSGI entrypoint shared by Docker Compose and Kubernetes."""
import argparse
import os
from pathlib import Path

from .runtime import load


def main():
    """Start one HTTP role using validated ports, threads and upload limits."""
    from waitress import serve
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("role", choices=("web", "study"))
    args = parser.parse_args()
    settings = load()
    if args.role == "web":
        from .web.app import create_app
    else:
        temporary=Path(os.environ.setdefault('TMPDIR',str(settings.paths.study/'workspace/tmp')))
        temporary.mkdir(parents=True,exist_ok=True)
        from .personal.workspace.application import create_app
    serve(create_app(), host=settings.server.host, port=getattr(settings.server, args.role+"_port"),
          threads=getattr(settings.server, args.role+"_threads"),
          max_request_body_size=settings.server.max_upload_bytes)


if __name__ == "__main__": main()
