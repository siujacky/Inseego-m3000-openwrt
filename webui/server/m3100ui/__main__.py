"""python3 -m m3100ui [--mock] [--port N] [--config FILE] …"""
import argparse
import logging
import os
import signal
import sys
import tempfile

from . import __version__
from . import config as config_mod


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="m3100ui", description="Vendor-neutral web UI for the M3100 5G router")
    ap.add_argument("--config", default="/data/m3100-webui/config.json")
    ap.add_argument("--host")
    ap.add_argument("--port", type=int)
    ap.add_argument("--web-root")
    ap.add_argument("--state-dir")
    ap.add_argument("--mock", action="store_true", help="use fixtures instead of the device (PC development)")
    ap.add_argument("--fixtures")
    ap.add_argument("--transport", choices=["local", "adb"], help="adb: drive a real unit from a PC")
    ap.add_argument("--adb-serial")
    ap.add_argument("--vendor-base", help="vendor web base URL, e.g. http://192.168.1.1")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    overrides = {"host": args.host, "port": args.port, "web_root": args.web_root, "state_dir": args.state_dir,
                 "fixtures_dir": args.fixtures, "transport": args.transport, "adb_serial": args.adb_serial,
                 "vendor_base": args.vendor_base}
    if args.mock:
        overrides["mock"] = True
        if not args.state_dir:
            overrides["state_dir"] = os.path.join(tempfile.gettempdir(), "m3100ui-mock-state")
    cfg = config_mod.load(args.config, overrides)
    logging.basicConfig(level=logging.DEBUG if args.verbose else getattr(logging, str(cfg["log_level"]).upper(), logging.INFO),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    from .app import App
    from .httpd import make_server
    app = App(cfg)
    srv = make_server(app, cfg["host"], int(cfg["port"]))
    logging.getLogger("m3100ui").info("m3100ui %s listening on [%s]:%d (%s)", __version__, cfg["host"], cfg["port"],
                                      "mock" if cfg.get("mock") else cfg.get("transport"))

    def stop(*_a):
        srv.shutdown()

    signal.signal(signal.SIGTERM, lambda *_a: (srv.server_close(), sys.exit(0)))
    try:
        srv.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
