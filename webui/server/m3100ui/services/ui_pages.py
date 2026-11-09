"""Page manifest for the SPA: every web/app/pages/**/*.js module, so pages need no shared index file."""
import os


def register(app):
    @app.router.route("GET", "/api/ui/pages", auth=False)
    def pages(req):
        """List of page modules to import (paths relative to the web root)."""
        root = os.path.join(app.cfg["web_root"], "app", "pages")
        out = []
        for dirpath, _dirs, files in os.walk(root):
            for f in files:
                if f.endswith(".js") and not f.startswith("_"):
                    rel = os.path.relpath(os.path.join(dirpath, f), app.cfg["web_root"]).replace(os.sep, "/")
                    native = app.cfg.get("platform") == "openwrt"
                    if native and not rel.startswith("app/pages/openwrt/") and rel not in ("app/pages/dashboard.js", "app/pages/system/openwrt.js", "app/pages/firewall/openwrt.js",
                                               "app/pages/wifi/primary.js", "app/pages/tools/audit.js",
                                               "app/pages/tools/apiexplorer.js"):
                        continue
                    if not native and (rel.startswith("app/pages/openwrt/") or rel in ("app/pages/system/openwrt.js", "app/pages/firewall/openwrt.js")):
                        continue
                    out.append("/" + rel)
        return sorted(out)
