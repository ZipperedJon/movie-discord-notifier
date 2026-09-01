"""Start the app: python run.py  (then open http://127.0.0.1:8000)"""

import argparse

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(description="Movie Discord Notifier")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true", help="auto-reload on code changes")
    args = parser.parse_args()

    print(f"\n  Movie Discord Notifier -> http://{args.host}:{args.port}\n")
    uvicorn.run("app.main:app", host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
