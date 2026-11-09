"""Setter specifications, one module per feature area (cellular.py, wifi.py, router.py …).

Each module defines `SPECS = {(cli, command): Spec(...)}`.  Modules are merged at import
time by cli_specs.  Keeping one file per area lets features be developed in parallel.

Verification results produced on a real unit by tools/device/verify_specs.py live in
fixtures/sandbox/verified.json and upgrade a spec's `source` from "static" to
"sandbox"/"roundtrip" without editing these files.
"""
