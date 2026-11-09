"""Feature services.  Every module here exposes `register(app)` and is loaded automatically.

Conventions: see docs/dev-guide.md.  A module that fails to import or register is skipped
(logged and listed in app.failed_modules / GET /api/meta/full) so one broken feature never
takes the whole UI down.
"""
import importlib
import logging
import pkgutil
import traceback

log = logging.getLogger("m3100ui.services")


def register_all(app) -> None:
    if app.cfg.get("platform") == "openwrt":
        from . import meta, session, ui_pages
        from ..native import register
        app.failed_modules = {}
        for module in (meta, session, ui_pages):
            module.register(app)
        register(app)
        return
    failed = getattr(app, "failed_modules", None)
    if failed is None:
        failed = app.failed_modules = {}
    for m in sorted(pkgutil.iter_modules(__path__), key=lambda m: m.name):
        name = __name__ + "." + m.name
        before = len(app.router.routes)
        try:
            mod = importlib.import_module(name)
            reg = getattr(mod, "register", None)
            if reg:
                reg(app)
        except Exception as e:  # isolate broken feature modules
            # drop routes the module managed to add before failing
            del app.router.routes[before:]
            failed[name] = "%s: %s" % (type(e).__name__, e)
            log.error("service module %s failed to load:\n%s", name, traceback.format_exc())
