import argparse
import sys


def main():
    parser = argparse.ArgumentParser(description="Weather Event Analyzer")
    parser.add_argument("--web", action="store_true", help="Launch Gradio web UI")
    parser.add_argument("--share", action="store_true", help="Create public Gradio link")
    parser.add_argument("--host", default="127.0.0.1", help="Server bind address")
    parser.add_argument("--port", type=int, default=7860, help="Web UI port")
    parser.add_argument("--cache-dir", default=None, help="Cache directory (default: XDG standard)")
    parser.add_argument("--cache-max", type=float, default=5.0, help="Max cache size in GB")

    subparsers = parser.add_subparsers(dest="command")

    from cli import add_subparsers
    add_subparsers(subparsers)

    args = parser.parse_args()

    if args.web:
        from app import create_app
        app = create_app(cache_dir=args.cache_dir, cache_max_gb=args.cache_max)
        app.launch(server_name=args.host, server_port=args.port, share=args.share)
    elif args.command:
        from cli import run_cli
        run_cli(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
