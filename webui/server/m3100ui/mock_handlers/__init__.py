"""Mock implementations of vendor CLI setters / REST actions, one module per feature area.

Every module in this package is imported automatically; register handlers with
`@cli_handler(cli, cmd)` / `@rest_handler(method, path)` from m3100ui.adapters.mock.
Handlers should update store state (FixtureStore.set_text) so the matching get_* command
returns the new value — that is what makes the UI testable in --mock.
A module that fails to import is logged and skipped.
"""
import importlib
import logging
import pkgutil
import traceback

FAILED = {}
for _m in pkgutil.iter_modules(__path__):
    try:
        importlib.import_module(__name__ + "." + _m.name)
    except Exception as _e:  # isolate broken mock modules
        FAILED[_m.name] = "%s: %s" % (type(_e).__name__, _e)
        logging.getLogger("m3100ui.mock").error("mock handler module %s failed:\n%s", _m.name, traceback.format_exc())
