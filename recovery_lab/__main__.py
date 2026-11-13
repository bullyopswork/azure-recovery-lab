import argparse
from socketserver import ThreadingMixIn
from wsgiref.simple_server import WSGIServer, WSGIRequestHandler, make_server
from .app import Application


class ThreadedServer(ThreadingMixIn, WSGIServer):
    daemon_threads = True


class QuietHandler(WSGIRequestHandler):
    def log_message(self, *_):
        pass


def main():
    parser = argparse.ArgumentParser(description="Local development server, not the production entrypoint")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    app = Application()
    with make_server(args.host, args.port, app, server_class=ThreadedServer, handler_class=QuietHandler) as server:
        server.serve_forever()


if __name__ == "__main__":
    main()
