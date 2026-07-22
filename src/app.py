# Main entry point for agent-server-platform
import os
import sys

# Add src to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Load .env BEFORE any other imports — watchdog/etc create ConnectionManager at import time
from dotenv import load_dotenv
load_dotenv()

import argparse
import signal
import multiprocessing

from database import init_database, close_database
from logger import logger
from core.watchdog import watchdog


def signal_handler(signum, frame):
    """Handle shutdown signals"""
    logger.info(f"Received signal {signum}, shutting down...")
    watchdog.stop()
    close_database()
    sys.exit(0)


def start_gradio(port: int, host: str):
    """Start Gradio server"""
    import httpx
    import threading
    from route.router import make_gr_route

    # ponytail: Gradio 5.x startup race — uvicorn hasn't bound the socket
    # when launch() verifies localhost. We must:
    # 1. Patch httpx.head to fake url_ok (just checks server reachable)
    # 2. Patch httpx.get to fake startup-events (so launch() doesn't crash)
    # 3. Manually call run_startup_events() to actually start the queue
    #    worker — the patched GET skips the real endpoint that does this.
    _orig_head = httpx.head
    _orig_get = httpx.get
    _local = f"{host}:{port}"

    def _fake_resp(method, url):
        return httpx.Response(200, request=httpx.Request(method, url))

    def _patched_head(url, *a, **kw):
        if _local in str(url) or "localhost" in str(url):
            return _fake_resp("HEAD", url)
        return _orig_head(url, *a, **kw)

    def _patched_get(url, *a, **kw):
        if "startup-events" in str(url):
            return _fake_resp("GET", url)
        return _orig_get(url, *a, **kw)

    httpx.head = _patched_head
    httpx.get = _patched_get

    logger.info(f"Starting Gradio on {host}:{port}")
    demo = make_gr_route()

    # Start queue worker in a background thread (since the patched
    # startup-events endpoint won't call run_startup_events for us).
    # Needs server_app set first, which launch() does before the check.
    def _start_queue():
        import asyncio
        import time
        time.sleep(2)  # wait for launch() to set server_app
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        demo._queue.set_server_app(demo.server_app)
        demo.run_startup_events()
        loop.run_forever()

    threading.Thread(target=_start_queue, daemon=True).start()

    demo.launch(server_name=host, server_port=port, share=False)


def start_flask(port: int, host: str):
    """Start Flask API server"""
    from api.flask_app import create_app

    logger.info(f"Starting Flask on {host}:{port}")
    app = create_app()
    app.run(host=host, port=port, debug=False)


def start_ws_server(port: int, host: str):
    """Start the WebSocket server + CentralDispatcher process.

    Accepts inbound connections from execution-agent-servers and routes
    dispatched tasks to them (or runs them locally if no server is selected).
    """
    # init_database is idempotent; needed here so the execution_servers table
    # exists and ConnectionManager initializes in this process.
    init_database()

    from database.repositories.execution_server_repository import ExecutionServerRepository
    from core.ws_server import WSDispatcher
    from core.central_dispatcher import central_dispatcher

    # Clear stale connected=True rows from a previous backend run.
    ExecutionServerRepository().mark_all_offline()

    ws = WSDispatcher(host, port)
    central_dispatcher.set_ws_dispatcher(ws)
    central_dispatcher.start()

    logger.info(f"Starting WS server on {host}:{port}")
    ws.run()  # blocks (runs the asyncio loop forever)


def main():
    """Main entry point"""
    # Parse arguments
    parser = argparse.ArgumentParser(description="Agent Server Platform")
    parser.add_argument("--gradio-only", action="store_true", help="Start only Gradio server")
    parser.add_argument("--flask-only", action="store_true", help="Start only Flask server")
    parser.add_argument("--ws-only", action="store_true", help="Start only the WS server + CentralDispatcher")
    parser.add_argument("--all", action="store_true", help="Start Gradio, Flask, and WS servers")
    parser.add_argument("--gradio-port", type=int, default=int(os.getenv("GRADIO_PORT", 8080)))
    parser.add_argument("--flask-port", type=int, default=int(os.getenv("FLASK_PORT", 5000)))
    parser.add_argument("--ws-port", type=int, default=int(os.getenv("WS_PORT", 8765)))
    parser.add_argument("--host", type=str, default=os.getenv("HOST", "0.0.0.0"))

    args = parser.parse_args()

    # Default to --all if no option specified
    if not (args.gradio_only or args.flask_only or args.ws_only or args.all):
        args.all = True

    # Initialize database
    logger.info("Initializing database...")
    init_database()

    # Register signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Start watchdog
    logger.info("Starting watchdog...")
    watchdog.start()

    try:
        if args.gradio_only:
            start_gradio(args.gradio_port, args.host)
        elif args.flask_only:
            start_flask(args.flask_port, args.host)
        elif args.ws_only:
            start_ws_server(args.ws_port, args.host)
        elif args.all:
            # Start all servers in separate processes
            gradio_proc = multiprocessing.Process(
                target=start_gradio,
                args=(args.gradio_port, args.host)
            )
            flask_proc = multiprocessing.Process(
                target=start_flask,
                args=(args.flask_port, args.host)
            )
            ws_proc = multiprocessing.Process(
                target=start_ws_server,
                args=(args.ws_port, args.host)
            )

            gradio_proc.start()
            flask_proc.start()
            ws_proc.start()

            logger.info(f"Gradio server: http://{args.host}:{args.gradio_port}")
            logger.info(f"Flask API server: http://{args.host}:{args.flask_port}")
            logger.info(f"WS server: ws://{args.host}:{args.ws_port}")

            # Wait for processes
            gradio_proc.join()
            flask_proc.join()
            ws_proc.join()

    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        watchdog.stop()
        close_database()


if __name__ == "__main__":
    main()
