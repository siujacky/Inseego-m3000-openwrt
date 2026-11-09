"""Every frontend JavaScript file must parse (catches a single broken module that would stop the SPA)."""
import glob
import os
import shutil
import subprocess
import unittest

from tests.helpers import ROOT


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class FrontendSyntaxTests(unittest.TestCase):
    def test_all_js_files_parse(self):
        files = sorted(glob.glob(os.path.join(ROOT, "web", "app", "**", "*.js"), recursive=True))
        self.assertGreater(len(files), 20)
        bad = []
        for f in files:
            # --input-type is not allowed with a file; ".mjs" copy forces ES-module parsing on every Node version
            p = subprocess.run(["node", "--check", f], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30)
            if p.returncode != 0:
                bad.append("%s: %s" % (os.path.relpath(f, ROOT), p.stdout.decode("utf-8", "replace").strip()[:300]))
        self.assertEqual(bad, [], "\n".join(bad))

    def test_no_raw_html_sinks(self):
        """Device data must never reach innerHTML/outerHTML/insertAdjacentHTML/document.write."""
        offenders = []
        for f in glob.glob(os.path.join(ROOT, "web", "app", "**", "*.js"), recursive=True):
            if f.endswith(os.path.join("ui", "icons.js")):
                continue  # static SVG path table only
            with open(f, encoding="utf-8") as fh:
                src = fh.read()
            for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write(", "eval("):
                if sink in src:
                    offenders.append("%s uses %s" % (os.path.relpath(f, ROOT), sink))
        self.assertEqual(offenders, [], "\n".join(offenders))


if __name__ == "__main__":
    unittest.main()
