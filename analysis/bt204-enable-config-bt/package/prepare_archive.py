"""Host-only, fail-closed BT204 ramfs derivation; importing performs no I/O.

Only copied module payloads are stripped. This program never builds a kernel,
executes firmware, contacts a device, or installs/loads a module.
"""
from pathlib import Path, PurePosixPath
import argparse
import base64
import hashlib
import io
import json
import os
import re
import stat
import subprocess
import tempfile
import types


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
PROFILE = HERE.parent
RELEASE = "6.12.112-bt204"
OLD_RELEASE = "6.12.112"
OLD_PREFIX = "lib/modules/" + OLD_RELEASE
PREFIX = "lib/modules/" + RELEASE
MANUAL = "usr/lib/m3100-bt204"
WORKER = "usr/libexec/m3100-cpe168-start"
BASELINE = ROOT / "analysis/cpe177-compact-startup/cpe177-compact-startup.cpio"
ARCHIVE_PROOF = ROOT / "analysis/cpe177-compact-startup/kernel-build/archive-final.json"
EXTERNAL = PROFILE / "external/bt204-external-modules.json"
PINS = {
    "baseline": "eddc109afb292cee16571b79c2c17de204896aa18da9a8fb597a62e16b970b8a",
    "archive_proof": "c4a3350f45462d1ee9e6e61848b3b48114774cad13a5eb48356d35b811f4e056",
    "cpio_parser": "41276602ee1cebfb76b3c61599f6109ed3440fffd48726b16258d725fccd6bdf",
    "worker": "58b04cd9fc15e479b39d37a8d06671eeba8c2039fab099dedcdf0b26b68b129c",
}
OBJCOPY = "/home/dell/m3100-openwrt/openwrt/staging_dir/toolchain-arm_cortex-a7+neon-vfpv4_gcc-14.3.0_musl_eabi/bin/arm-openwrt-linux-muslgnueabi-objcopy"
EXPECTED_CONFIG = {
    "CONFIG_BT": "m", "CONFIG_BT_LE": "y", "CONFIG_BT_BREDR": "y",
    "CONFIG_SERIAL_DEV_BUS": "y", "CONFIG_SERIAL_DEV_CTRL_TTYPORT": "y",
    "CONFIG_BT_HCIUART": "m", "CONFIG_BT_HCIUART_H4": "y",
    "CONFIG_BT_HCIUART_QCA": "y", "CONFIG_CRYPTO_CMAC": "m",
}
TARGET_METADATA = (
    ".config", "Module.symvers", "modules.order", "modules.builtin",
    "modules.builtin.modinfo", "include/generated/utsrelease.h",
    "include/generated/autoconf.h", "include/config/auto.conf",
)


def need(condition, message):
    if not condition:
        raise ValueError(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def host_path(path):
    """Accept WSL Linux paths from manifests without shell interpolation."""
    value = str(path).replace("\\", "/") if os.name == "nt" else str(path)
    if os.name == "nt" and value.startswith("/") and not value.startswith("//"):
        return Path("//wsl.localhost/Ubuntu-24.04") / value.lstrip("/")
    return Path(path)


def linux_path(path):
    value = Path(path).resolve().as_posix()
    prefix = "//wsl.localhost/Ubuntu-24.04/"
    if value.lower().startswith(prefix.lower()):
        return "/" + value[len(prefix):]
    if re.match(r"^[A-Za-z]:/", value):
        return "/mnt/" + value[0].lower() + value[2:]
    need(value.startswith("/"), "cannot translate host path: " + value)
    return value


def safe_name(name):
    need(isinstance(name, str) and name and "\\" not in name and "\0" not in name,
         "invalid archive name")
    need(not PurePosixPath(name).is_absolute() and
         all(p not in ("", ".", "..") for p in name.split("/")),
         "unsafe archive name: " + name)
    return name


def parser():
    path = ROOT / "analysis/wlan148-integration/cpio148.py"
    source = path.read_bytes()
    need(sha(source) == PINS["cpio_parser"], "CPIO parser source pin changed")
    module = types.ModuleType("bt204_pinned_cpio")
    exec(compile(source, str(path), "exec"), module.__dict__)
    return module.parse


def baseline_inputs():
    parse = parser()
    data = BASELINE.read_bytes()
    proof_data = ARCHIVE_PROOF.read_bytes()
    need(sha(data) == PINS["baseline"], "baseline CPIO pin changed")
    need(sha(proof_data) == PINS["archive_proof"], "archive proof pin changed")
    proof = json.loads(proof_data)
    entries = parse(data)
    old = {e["name"]: e for e in entries}
    for name in old:
        if name != "TRAILER!!!":
            safe_name(name)
    rows = proof["modules"]
    need(len(entries) == 901 and len(rows) == 51, "baseline entry/module count")
    need({n for n in old if n.endswith(".ko")} == {m["archive_path"] for m in rows},
         "baseline module mapping incomplete")
    for item in rows:
        payload = old[item["archive_path"]]["payload"]
        need(sha(payload) == item["embedded_sha256"] and
             len(payload) == item["embedded_bytes"], "baseline payload differs")
    need(sha(old[WORKER]["payload"]) == PINS["worker"], "worker pin differs")
    need(sum("kernel_relative" in m for m in rows) == 43, "native/external split differs")
    return parse, entries, old, rows


def module_key(value):
    name = PurePosixPath(value).name
    return re.sub(r"\.(?:ko|o)$", "", name).replace("-", "_")


def elf_info(data):
    """Read bounded ELF data only; runtime relocation proof ignores debug tables."""
    from elftools.elf.elffile import ELFFile
    from elftools.elf.relocation import RelocationSection
    need(len(data) < 32 * 1024 * 1024, "unbounded module payload")
    elf = ELFFile(io.BytesIO(data))
    need(elf.elfclass == 32 and elf.little_endian and
         elf["e_machine"] == "EM_ARM" and elf["e_type"] == "ET_REL",
         "expected ARM32 little-endian relocatable module")
    symtab = elf.get_section_by_name(".symtab")
    need(symtab is not None, "module has no symbol table")
    section = elf.get_section_by_name(".modinfo")
    need(section is not None, "module has no modinfo")
    modinfo = {}
    for part in section.data().split(b"\0"):
        if not part:
            continue
        key, sep, value = part.partition(b"=")
        need(bool(sep), "malformed modinfo")
        modinfo.setdefault(key.decode(), []).append(value.decode())
    need(len(modinfo.get("vermagic", [])) == 1 and
         len(modinfo.get("name", [])) == 1, "module identity missing")
    imports = sorted({s.name for s in symtab.iter_symbols()
                      if s.name and s["st_shndx"] == "SHN_UNDEF" and
                      s["st_info"]["bind"] != "STB_WEAK"})
    weak = sorted({s.name for s in symtab.iter_symbols()
                   if s.name and s["st_shndx"] == "SHN_UNDEF" and
                   s["st_info"]["bind"] == "STB_WEAK"})
    allocated = {}
    relocations = []
    for sec in elf.iter_sections():
        if sec["sh_flags"] & 2:
            allocated[sec.name] = {
                "type": sec["sh_type"], "flags": sec["sh_flags"],
                "alignment": sec["sh_addralign"], "size": sec["sh_size"],
                "sha256": None if sec["sh_type"] == "SHT_NOBITS" else sha(sec.data()),
            }
        if not isinstance(sec, RelocationSection):
            continue
        target = elf.get_section(sec["sh_info"])
        if not target["sh_flags"] & 2:
            continue
        symbols = elf.get_section(sec["sh_link"])
        for rel in sec.iter_relocations():
            symbol = symbols.get_symbol(rel["r_info_sym"])
            identity = symbol.name
            if symbol["st_info"]["type"] == "STT_SECTION":
                identity = elf.get_section(symbol["st_shndx"]).name
            relocations.append((target.name, rel["r_offset"], rel["r_info_type"],
                                identity, symbol["st_value"],
                                rel.entry.get("r_addend")))
    versions = elf.get_section_by_name("__versions")
    return {
        "name": modinfo["name"][0], "vermagic": modinfo["vermagic"][0],
        "modinfo": modinfo, "imports": imports, "weak_imports": weak,
        "allocated_sections": allocated, "runtime_relocations": relocations,
        "versions_sha256": sha(versions.data()) if versions else None,
    }


def read_symvers(data, source):
    rows = {}
    for line in data.decode().splitlines():
        parts = line.split()
        need(4 <= len(parts) <= 5 and re.fullmatch(r"0x[0-9a-fA-F]+", parts[0]),
             "malformed Module.symvers: " + str(source))
        crc, symbol, provider, export = parts[:4]
        item = {"crc": crc.lower(), "provider": provider,
                "provider_module": "vmlinux" if provider == "vmlinux" else module_key(provider),
                "export": export, "namespace": parts[4] if len(parts) == 5 else ""}
        need(symbol not in rows or rows[symbol] == item, "duplicate symvers export")
        rows[symbol] = item
    return rows


def new_entry(name, payload, mode, inode, original=None):
    safe_name(name)
    rawname = name.encode() + b"\0"
    fields = original["fields"][:] if original else [inode, mode, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0]
    fields[6] = len(payload)
    fields[11] = len(rawname)
    magic = original["raw"][:6] if original else b"070701"
    fields[12] = sum(payload) & 0xffffffff if magic == b"070702" else 0
    header = magic + b"".join(f"{v:08x}".encode() for v in fields) + rawname
    header += bytes(-len(header) % 4)
    return header + payload + bytes(-len(payload) % 4)


def apk_file_metadata(data, payloads):
    """Refresh installed-file paths/hashes, retaining original APK identities.

    APK C/V/D fields identify original distribution packages. They are not a
    signature or ABI attestation for this local ramfs derivation.
    """
    directory = filename = None
    lines = []
    changed_hashes = 0
    for line in data.decode().splitlines(keepends=True):
        if line.startswith("F:"):
            directory = line[2:].strip().replace(OLD_PREFIX, PREFIX)
            line = "F:" + directory + "\n"
            filename = None
        elif line.startswith("R:"):
            filename = (directory + "/" if directory else "") + line[2:].strip()
        elif line.startswith("Z:") and filename in payloads:
            need(line.startswith("Z:Q1"), "unsupported installed-file digest scheme")
            digest = base64.b64encode(hashlib.sha1(payloads[filename]).digest()).decode()
            replacement = "Z:Q1" + digest + "\n"
            changed_hashes += replacement != line
            line = replacement
        lines.append(line)
    return "".join(lines).encode(), changed_hashes


def topological(keys, modules):
    result, active, done = [], set(), set()
    def visit(key):
        need(key not in active, "module dependency cycle: " + key)
        if key in done:
            return
        active.add(key)
        for dep in sorted(modules[key]["dependencies"]):
            visit(dep)
        active.remove(key)
        done.add(key)
        result.append(key)
    for key in sorted(keys):
        visit(key)
    return result


def _manual_plan(core, transport, modules):
    def commands(keys):
        return "\n".join("[ -d /sys/module/" + key + " ] || /sbin/insmod /" +
                         modules[key]["archive_path"] for key in keys)
    return ("BT204 MANUAL REVIEW PLAN - text only; no startup invokes this file.\n"
            "No commands have been executed on a device by the packager.\n"
            "Verify the exact M3100, RAM-only boot/recovery authorization, uname -r " + RELEASE +
            ", USB management, and manifest hashes before a separately approved runtime test.\n"
            "\nPHASE 1: core and crypto only. These modules provide Bluetooth APIs;\n"
            "they do not open UART, power a controller, attach an HCI transport, or scan.\n" +
            commands(core) + "\n"
            "\nPHASE 2: SEPARATE TRANSPORT REVIEW - not part of core loading.\n"
            "No Bluetooth DT child, hciattach/btattach, rfkill, UART open, HCI power,\n"
            "scan, or transport activation is supplied by this package. Loading a\n"
            "transport driver could bind any separately existing matching device.\n"
            "After separate device/power/recovery review only, transport dependency order is:\n" +
            "\n".join("/" + modules[k]["archive_path"] for k in transport if k not in core) +
            "\nNo executable UART loader is included.\n").encode()


def prepare_archive(kernel_dir: Path, output_cpio: Path) -> dict:
    """Derive one new archive and a JSON receipt from an already rebuilt tree.

    Refuses to overwrite outputs or to use any old-release native/external KO.
    The external rebuild agent supplies external/bt204-external-modules.json.
    """
    kernel_dir = host_path(kernel_dir).resolve()
    output_cpio = Path(output_cpio).resolve()
    need(not output_cpio.exists(), "output archive already exists")
    receipt_path = output_cpio.with_suffix(output_cpio.suffix + ".receipt.json")
    need(not receipt_path.exists(), "output receipt already exists")
    parse, entries, old, baseline_rows = baseline_inputs()
    source_pins = {}
    def read_pin(path):
        path = host_path(path).resolve()
        data = path.read_bytes()
        digest = sha(data)
        need(str(path) not in source_pins or source_pins[str(path)] == digest,
             "input changed while packaging: " + str(path))
        source_pins[str(path)] = digest
        return data
    metadata = {name: read_pin(kernel_dir / name) for name in TARGET_METADATA}
    need(metadata["include/generated/utsrelease.h"].decode().strip() ==
         '#define UTS_RELEASE "' + RELEASE + '"', "target kernel release differs")
    config = dict(re.findall(r"^(CONFIG_[A-Z0-9_]+)=(.*)$", metadata[".config"].decode(), re.M))
    for key, value in EXPECTED_CONFIG.items():
        need(config.get(key) == value, key + " must be " + value)
    need(config.get("CONFIG_MODULE_SIG", "n") == "n", "signed module stripping is unsupported")
    need(config.get("CONFIG_MODVERSIONS", "n") == "n", "BT204 baseline expects MODVERSIONS=n")
    native = {}
    for line in metadata["modules.order"].decode().splitlines():
        safe_name(line)
        relative = re.sub(r"\.o$", ".ko", line)
        need(relative.endswith(".ko"), "unexpected modules.order entry")
        key = module_key(relative)
        need(key not in native, "native module basename collision")
        native[key] = relative
    need(0 < len(native) <= 256, "unbounded native module count")
    external_data = read_pin(EXTERNAL)
    external_manifest = json.loads(external_data)
    need(external_manifest.get("kernel_release") == RELEASE and
         external_manifest.get("kernel_config_sha256") == sha(metadata[".config"]) and
         external_manifest.get("kernel_symvers_sha256") == sha(metadata["Module.symvers"]) and
         external_manifest.get("fresh_target_build") is True,
         "external rebuild receipt does not match the target ABI")
    external_rows = external_manifest["modules"]
    expected_external = {m["archive_path"] for m in baseline_rows if "kernel_relative" not in m}
    need(len(external_rows) == 8 and {m["archive_path"] for m in external_rows} == expected_external,
         "fresh external mapping must cover exactly eight baseline modules")
    externals = {m["archive_path"]: m for m in external_rows}
    exports = read_symvers(metadata["Module.symvers"], kernel_dir / "Module.symvers")
    for filename in sorted({m["symvers"] for m in external_rows}):
        for symbol, item in read_symvers(read_pin(filename), filename).items():
            need(symbol not in exports or exports[symbol] == item,
                 "conflicting native/external export: " + symbol)
            exports[symbol] = item
    modules = {}
    def add(relative, archive_path, source, kind, expected_sha=None):
        source = host_path(source).resolve()
        raw = read_pin(source)
        if expected_sha:
            need(sha(raw) == expected_sha, "external module hash differs")
        info = elf_info(raw)
        key = module_key(archive_path)
        need(info["name"] == key, "module name/path identity differs: " + str(source))
        need(info["vermagic"].split()[0] == RELEASE, "stale module vermagic: " + str(source))
        need(key not in modules, "module key collision: " + key)
        modules[key] = {"kernel_relative": relative, "archive_path": archive_path,
                        "source": str(source), "kind": kind, "raw": raw,
                        "raw_sha256": sha(raw), "raw_bytes": len(raw), "elf": info}
    for row in baseline_rows:
        name = row["archive_path"]
        target_name = name.replace(OLD_PREFIX, PREFIX, 1)
        if "kernel_relative" in row:
            relative = safe_name(row["kernel_relative"])
            need(native.get(module_key(relative)) == relative, "baseline KO missing from target modules.order")
            add(relative, target_name, kernel_dir / relative, "fresh native replacement")
        else:
            row = externals[name]
            add(None, target_name, row["source"], "fresh external replacement", row["sha256"])
    legacy_keys = set(modules)
    bluetooth_seeds = {key for key, path in native.items()
                       if path.startswith(("net/bluetooth/", "drivers/bluetooth/"))}
    need({"bluetooth", "hci_uart", "btqca"} <= bluetooth_seeds,
         "target is missing Bluetooth core/QCA UART modules")
    pending = set(bluetooth_seeds)
    while pending:
        key = min(pending)
        pending.remove(key)
        if key not in modules:
            need(key in native, "dependency has no fresh target module: " + key)
            path = native[key]
            add(path, MANUAL + "/" + PurePosixPath(path).name, kernel_dir / path,
                "manual-only native Bluetooth/crypto closure")
        item = modules[key]
        for symbol in item["elf"]["imports"]:
            need(symbol in exports, "unresolved target export: " + key + ":" + symbol)
            provider = exports[symbol]["provider_module"]
            if provider != "vmlinux" and provider not in modules:
                pending.add(provider)
    for key, item in modules.items():
        dependencies, imported = set(), []
        for symbol in item["elf"]["imports"]:
            need(symbol in exports, "unresolved export: " + key + ":" + symbol)
            export = exports[symbol]
            provider = export["provider_module"]
            need(provider == "vmlinux" or provider in modules,
                 "module provider absent from payload set: " + provider)
            if provider != "vmlinux" and provider != key:
                dependencies.add(provider)
            imported.append({"symbol": symbol, **export})
        declared = {module_key(d) for d in item["elf"]["modinfo"].get("depends", [""])[0].split(",") if d}
        need(declared <= dependencies, "declared dependency missing export proof: " + key)
        item["dependencies"] = sorted(dependencies)
        item["import_proof"] = imported
    need(all(set(modules[k]["dependencies"]) <= legacy_keys for k in legacy_keys),
         "legacy startup unexpectedly depends on manual-only Bluetooth payload")
    need(len({m["elf"]["vermagic"] for m in modules.values()}) == 1,
         "module vermagic ABI flags differ")
    all_order = topological(modules, modules)
    core_order = topological({"bluetooth"}, modules)
    transport_order = topological({"hci_uart", "btqca"}, modules)
    need(not {"hci_uart", "btqca"} & set(core_order), "core plan includes UART transport")
    output_cpio.parent.mkdir(parents=True, exist_ok=True)
    objcopy = host_path(OBJCOPY)
    tool_digest = sha(objcopy.read_bytes())
    with tempfile.TemporaryDirectory(prefix=".bt204-strip-", dir=output_cpio.parent) as tmp:
        for key in all_order:
            item = modules[key]
            copied = Path(tmp) / (key + ".ko")
            copied.write_bytes(item["raw"])
            command = [OBJCOPY, "--strip-debug", linux_path(copied)]
            if os.name == "nt":
                command = ["wsl", "-d", "Ubuntu-24.04", "--"] + command
            subprocess.run(command, check=True, capture_output=True)
            payload = copied.read_bytes()
            check = elf_info(payload)
            need(check == item["elf"], "strip changed loadable ELF/modinfo/import/relocation state: " + key)
            need(not payload.endswith(b"~Module signature appended~\n"), "signed payload unexpected")
            item["payload"] = payload
            item["payload_sha256"] = sha(payload)
            item["payload_bytes"] = len(payload)
    payloads, source_for, modes = {}, {}, {}
    for e in entries:
        name = e["name"]
        if name == "TRAILER!!!":
            continue
        target_name = PREFIX + name[len(OLD_PREFIX):] if name == OLD_PREFIX or name.startswith(OLD_PREFIX + "/") else name
        payload = e["payload"]
        if not name.endswith(".ko") and b"\0" not in payload and b"/lib/modules/6.12.112" in payload:
            payload = payload.replace(("/" + OLD_PREFIX).encode(), ("/" + PREFIX).encode())
        if name == WORKER:
            payload = payload.replace(b"= 6.12.112 ]", b"= 6.12.112-bt204 ]")
            need(payload.replace(RELEASE.encode(), OLD_RELEASE.encode()) == e["payload"],
                 "worker behavior changed beyond release/path substitution")
        payloads[target_name] = payload
        source_for[target_name] = e
        modes[target_name] = e["fields"][1]
    for item in modules.values():
        payloads[item["archive_path"]] = item["payload"]
        modes[item["archive_path"]] = stat.S_IFREG | 0o644
    for name in ("modules.builtin", "modules.builtin.modinfo"):
        payloads[PREFIX + "/" + name] = metadata[name]
    hashlist_name = "etc/cpe168-modules.sha256"
    guarded = []
    for line in old[hashlist_name]["payload"].decode().splitlines():
        match = re.fullmatch(r"([0-9a-f]{64})  /(.+)", line)
        need(match is not None, "invalid baseline module hash guard")
        name = match[2].replace(OLD_PREFIX, PREFIX, 1)
        need(name in payloads, "hash guard path absent")
        guarded.append(sha(payloads[name]) + "  /" + name + "\n")
    need(len(guarded) == 11, "baseline worker hash guard count changed")
    payloads[hashlist_name] = "".join(guarded).encode()
    normal_order = [k for k in all_order if k in legacy_keys]
    dep_lines, alias_lines, symbol_lines, softdep_lines = [], [], [], []
    for key in normal_order:
        item = modules[key]
        filename = PurePosixPath(item["archive_path"]).name
        deps = [PurePosixPath(modules[d]["archive_path"]).name for d in item["dependencies"]]
        dep_lines.append(filename + ":" + (" " + " ".join(deps) if deps else "") + "\n")
        alias_lines.extend("alias " + alias + " " + key + "\n"
                           for alias in item["elf"]["modinfo"].get("alias", []))
        softdep_lines.extend("softdep " + key + " " + value + "\n"
                             for value in item["elf"]["modinfo"].get("softdep", []))
    for symbol, row in sorted(exports.items()):
        if row["provider_module"] in legacy_keys:
            symbol_lines.append("alias symbol:" + symbol + " " + row["provider_module"] + "\n")
    generated = {
        PREFIX + "/modules.dep": "".join(dep_lines).encode(),
        PREFIX + "/modules.alias": "".join(sorted(set(alias_lines))).encode(),
        PREFIX + "/modules.symbols": "".join(symbol_lines).encode(),
        PREFIX + "/modules.softdep": "".join(sorted(set(softdep_lines))).encode(),
        PREFIX + "/modules.order": "".join(PurePosixPath(modules[k]["archive_path"]).name + "\n" for k in normal_order).encode(),
        MANUAL + "/manual-load-plan.txt": _manual_plan(core_order, transport_order, modules),
        MANUAL + "/modules.sha256": "".join(modules[k]["payload_sha256"] + "  /" + modules[k]["archive_path"] + "\n" for k in all_order).encode(),
        MANUAL + "/package-provenance.txt": (
            "Locally derived BT204 RAM filesystem. Runtime kernel release: " + RELEASE + ".\n"
            "Original OpenWrt APK C/V/D/world identities are retained as distribution\n"
            "provenance; they do not certify the local kernel, modules or changed files.\n"
            "APK file paths and installed-file SHA1 digests are refreshed. No new APK\n"
            "signature or installable package set is claimed. Do not use package-manager\n"
            "kernel/kmod replacement as a recovery or ABI compatibility path.\n"
            "Bluetooth and its added crypto dependencies are outside /lib/modules;\n"
            "no Bluetooth autoload aliases, init entry, DT child or UART loader is added.\n"
        ).encode(),
    }
    payloads.update(generated)
    db = "lib/apk/db/installed"
    changed_payloads = {n: b for n, b in payloads.items()
                        if n not in old or b != old[n]["payload"]}
    payloads[db], apk_hash_updates = apk_file_metadata(payloads[db], changed_payloads)
    for name in list(payloads):
        for parent in PurePosixPath(name).parents:
            if str(parent) != "." and str(parent) not in payloads:
                payloads[str(parent)] = b""
                modes[str(parent)] = stat.S_IFDIR | 0o755
    out, written = [], set()
    inode = max(e["fields"][0] for e in entries) + 1
    for e in entries:
        name = e["name"]
        if name == "TRAILER!!!":
            continue
        target_name = PREFIX + name[len(OLD_PREFIX):] if name == OLD_PREFIX or name.startswith(OLD_PREFIX + "/") else name
        payload = payloads[target_name]
        out.append(e["raw"] if target_name == name and payload == e["payload"] else
                   new_entry(target_name, payload, modes[target_name], inode, e))
        written.add(target_name)
    for name in sorted(set(payloads) - written, key=lambda p: (len(PurePosixPath(p).parts), p)):
        out.append(new_entry(name, payloads[name], modes.get(name, stat.S_IFREG | 0o644), inode))
        inode += 1
    out.append(entries[-1]["raw"])
    result = b"".join(out)
    result += bytes(-len(result) % 512)
    need(len(result) <= 48 * 1024 * 1024, "BT204 archive exceeds bounded size budget")
    actual_entries = parse(result)
    actual = {e["name"]: e for e in actual_entries}
    need(set(actual) == set(payloads) | {"TRAILER!!!"}, "archive output entry mismatch")
    need(all(actual[n]["payload"] == b for n, b in payloads.items()), "archive payload mismatch")
    firmware_names = {n for n in old if n.startswith("lib/firmware/")}
    need(all(actual[n]["raw"] == old[n]["raw"] for n in firmware_names), "firmware entries changed")
    need(not any(n == OLD_PREFIX or n.startswith(OLD_PREFIX + "/") for n in actual), "old module directory remains")
    need(sum(n.endswith(".ko") and n.startswith(PREFIX + "/") for n in actual) == 51,
         "legacy payload count changed")
    for name, item in actual.items():
        if name.startswith(("etc/modules.d/", "etc/modules-boot.d/", "etc/init.d/", "etc/rc.d/")):
            if name in old:
                need(item["raw"] == old[name]["raw"], "autoload/init entry changed: " + name)
            else:
                need(False, "new autoload/init entry: " + name)
        if name not in ("etc/apk/world", "lib/apk/db/installed") and not name.startswith(MANUAL + "/"):
            if b"\0" not in item["payload"] and not name.endswith(".ko"):
                need(not re.search(rb"6\.12\.112(?!-bt204)", item["payload"]), "stale runtime release in " + name)
    for path, digest in source_pins.items():
        need(sha(Path(path).read_bytes()) == digest, "input changed during package: " + path)
    baseline_inputs()
    need(sha(objcopy.read_bytes()) == tool_digest, "objcopy changed during packaging")
    module_receipts = []
    for key in all_order:
        item = modules[key]
        module_receipts.append({k: v for k, v in item.items() if k not in ("raw", "payload")} | {
            "stripped_copy_only": True,
            "allocated_sections_modinfo_imports_runtime_relocations_identical": True,
        })
    changes = sorted(n for n in actual if n not in old or actual[n]["raw"] != old[n]["raw"])
    receipt = {
        "schema": 1, "kernel_release": RELEASE, "kernel_dir": str(kernel_dir),
        "archive_sha256": sha(result), "archive_bytes": len(result),
        "entries_including_trailer": len(actual_entries), "input_pins": PINS,
        "source_pins": source_pins, "external_receipt_sha256": sha(external_data),
        "objcopy": OBJCOPY, "objcopy_sha256": tool_digest,
        "legacy_module_count": 51, "fresh_native_replacements": 43,
        "fresh_external_replacements": 8,
        "manual_only_module_count": len(modules) - 51, "modules": module_receipts,
        "all_imports_resolve_to_target_exports": True, "modules_modversions": False,
        "single_exact_vermagic": next(iter(modules.values()))["elf"]["vermagic"],
        "core_manual_load_order": core_order, "transport_manual_load_order": transport_order,
        "startup_behavior_change": "release guard and explicit module-path substitutions only",
        "worker_sha256": sha(actual[WORKER]["payload"]), "worker_hash_guard_count": 11,
        "apk_file_hashes_updated": apk_hash_updates,
        "apk_distribution_identities_retained_as_provenance_only": True,
        "builtin_metadata_exact_target": True, "normal_module_metadata_regenerated": True,
        "bluetooth_excluded_from_normal_module_search_path": True,
        "firmware_raw_entries_preserved": len(firmware_names),
        "changed_or_added_entries": changes,
        "removed_or_renamed_entries": sorted(set(old) - set(actual)),
        "hardware_actions": False, "module_loads": False, "DT_changes": False,
        "build_or_runtime_behavior_verified": False,
    }
    encoded_receipt = (json.dumps(receipt, indent=2, sort_keys=True) + "\n").encode()
    with output_cpio.open("xb") as stream:
        stream.write(result)
    with receipt_path.open("xb") as stream:
        stream.write(encoded_receipt)
    return receipt


def validate_archive(archive_path: Path) -> dict:
    """Independently validate emitted bytes and pinned inputs; never mutate files."""
    archive_path = Path(archive_path).resolve()
    receipt_path = archive_path.with_suffix(archive_path.suffix + ".receipt.json")
    receipt = json.loads(receipt_path.read_bytes())
    need(receipt["schema"] == 1 and receipt["kernel_release"] == RELEASE and
         receipt["input_pins"] == PINS, "receipt profile mismatch")
    parse, baseline_entries, old, rows = baseline_inputs()
    archive = archive_path.read_bytes()
    need(sha(archive) == receipt["archive_sha256"] and
         len(archive) == receipt["archive_bytes"], "archive receipt hash/size mismatch")
    entries = parse(archive)
    actual = {e["name"]: e for e in entries}
    need(len(entries) == receipt["entries_including_trailer"], "receipt entry count mismatch")
    for path, digest in receipt["source_pins"].items():
        need(sha(Path(path).read_bytes()) == digest, "package source no longer matches: " + path)
    modules = receipt["modules"]
    need(len({m["archive_path"] for m in modules}) == len(modules), "duplicate module receipt")
    need({n for n in actual if n.endswith(".ko")} == {m["archive_path"] for m in modules},
         "archive has unrecorded or missing modules")
    legacy_paths = {r["archive_path"].replace(OLD_PREFIX, PREFIX, 1) for r in rows}
    need({m["archive_path"] for m in modules if m["kind"].endswith("replacement")} == legacy_paths,
         "receipt legacy mapping differs")
    module_by_key = {}
    for module in modules:
        name = module["archive_path"]
        raw = Path(module["source"]).read_bytes()
        payload = actual[name]["payload"]
        need(sha(raw) == module["raw_sha256"] and len(raw) == module["raw_bytes"],
             "raw module input differs")
        need(sha(payload) == module["payload_sha256"] and len(payload) == module["payload_bytes"],
             "embedded module differs")
        raw_elf, payload_elf = elf_info(raw), elf_info(payload)
        need(raw_elf == payload_elf, "embedded/raw ELF loadable parity differs")
        # JSON stores tuples as lists; compare a normalized representation.
        need(json.loads(json.dumps(raw_elf)) == module["elf"], "ELF proof differs")
        need(payload_elf["vermagic"] == receipt["single_exact_vermagic"] and
             payload_elf["vermagic"].split()[0] == RELEASE, "module ABI differs")
        module_by_key[module_key(name)] = module
    exports = read_symvers((Path(receipt["kernel_dir"]) / "Module.symvers").read_bytes(), "target")
    external = json.loads(EXTERNAL.read_bytes())
    need(sha(EXTERNAL.read_bytes()) == receipt["external_receipt_sha256"], "external receipt differs")
    for filename in sorted({row["symvers"] for row in external["modules"]}):
        for symbol, export in read_symvers(host_path(filename).read_bytes(), filename).items():
            need(symbol not in exports or exports[symbol] == export, "conflicting export in validation")
            exports[symbol] = export
    for key, module in module_by_key.items():
        proof, dependencies = [], set()
        for symbol in module["elf"]["imports"]:
            need(symbol in exports, "unresolved import in validation")
            export = exports[symbol]
            provider = export["provider_module"]
            need(provider == "vmlinux" or provider in module_by_key, "module dependency missing")
            if provider not in ("vmlinux", key):
                dependencies.add(provider)
            proof.append({"symbol": symbol, **export})
        need(proof == module["import_proof"] and sorted(dependencies) == module["dependencies"],
             "target export/dependency proof differs: " + key)
    need(len(legacy_paths) == 51 and len(modules) == 51 + receipt["manual_only_module_count"],
         "receipt module counts differ")
    need(all(n.startswith(PREFIX + "/") or n.startswith(MANUAL + "/")
             for n in (m["archive_path"] for m in modules)), "module outside qualified paths")
    need(not any(n == OLD_PREFIX or n.startswith(OLD_PREFIX + "/") for n in actual),
         "stale module search directory")
    for name, item in old.items():
        if name.startswith("lib/firmware/") or name.startswith((
                "etc/modules.d/", "etc/modules-boot.d/", "etc/init.d/", "etc/rc.d/")):
            need(name in actual and actual[name]["raw"] == item["raw"],
                 "firmware/autoload/init raw entry changed: " + name)
    for name in actual:
        if name.startswith(("etc/modules.d/", "etc/modules-boot.d/", "etc/init.d/", "etc/rc.d/")):
            need(name in old, "new automatic startup entry")
    worker = actual[WORKER]["payload"]
    need(worker.replace(RELEASE.encode(), OLD_RELEASE.encode()) == old[WORKER]["payload"],
         "worker changes exceed release/path substitution")
    need(sha(worker) == receipt["worker_sha256"], "worker receipt differs")
    guard_names = []
    for line in actual["etc/cpe168-modules.sha256"]["payload"].decode().splitlines():
        match = re.fullmatch(r"([0-9a-f]{64})  /(.+)", line)
        need(match is not None and match[2] in actual and
             sha(actual[match[2]]["payload"]) == match[1], "worker guard digest/path differs")
        guard_names.append(match[2])
    need(len(guard_names) == len(set(guard_names)) == 11, "worker guard count differs")
    for filename in ("modules.builtin", "modules.builtin.modinfo"):
        need(actual[PREFIX + "/" + filename]["payload"] ==
             (Path(receipt["kernel_dir"]) / filename).read_bytes(), "built-in metadata differs")
    legacy_keys = {module_key(n) for n in legacy_paths}
    normal_order = [key for key in topological(module_by_key, module_by_key) if key in legacy_keys]
    dependency_lines, alias_lines, softdep_lines = [], [], []
    for key in normal_order:
        module = module_by_key[key]
        need(set(module["dependencies"]) <= legacy_keys, "normal module depends on manual-only module")
        filename = PurePosixPath(module["archive_path"]).name
        dependencies = [PurePosixPath(module_by_key[d]["archive_path"]).name for d in module["dependencies"]]
        dependency_lines.append(filename + ":" + (" " + " ".join(dependencies) if dependencies else "") + "\n")
        alias_lines.extend("alias " + alias + " " + key + "\n" for alias in module["elf"]["modinfo"].get("alias", []))
        softdep_lines.extend("softdep " + key + " " + value + "\n" for value in module["elf"]["modinfo"].get("softdep", []))
    expected_metadata = {
        "modules.dep": "".join(dependency_lines),
        "modules.alias": "".join(sorted(set(alias_lines))),
        "modules.softdep": "".join(sorted(set(softdep_lines))),
        "modules.order": "".join(PurePosixPath(module_by_key[k]["archive_path"]).name + "\n" for k in normal_order),
        "modules.symbols": "".join("alias symbol:" + symbol + " " + row["provider_module"] + "\n"
                                  for symbol, row in sorted(exports.items()) if row["provider_module"] in legacy_keys),
    }
    for filename, text in expected_metadata.items():
        need(actual[PREFIX + "/" + filename]["payload"] == text.encode(),
             "normal metadata differs or exposes manual modules: " + filename)
    need(not {"hci_uart", "btqca"} & set(receipt["core_manual_load_order"]),
         "core plan includes transport")
    expected_core = topological({"bluetooth"}, module_by_key)
    expected_transport = topological({"hci_uart", "btqca"}, module_by_key)
    need(expected_core == receipt["core_manual_load_order"] and
         expected_transport == receipt["transport_manual_load_order"], "manual dependency order differs")
    need(actual[MANUAL + "/manual-load-plan.txt"]["payload"] ==
         _manual_plan(expected_core, expected_transport, module_by_key), "manual plan differs")
    need(all(not flag for flag in (receipt["hardware_actions"], receipt["module_loads"],
                                  receipt["DT_changes"], receipt["build_or_runtime_behavior_verified"])),
         "receipt claims unperformed device/runtime actions")
    return {"all_checks_passed": True, "archive_sha256": sha(archive),
            "kernel_release": RELEASE, "legacy_modules_verified": 51,
            "manual_only_modules_verified": len(modules) - 51,
            "firmware_and_automatic_startup_preserved": True,
            "hardware_actions": False, "runtime_verified": False}


def main():
    arguments = argparse.ArgumentParser(description=__doc__)
    arguments.add_argument("--kernel", type=Path)
    arguments.add_argument("--output", type=Path, required=True)
    arguments.add_argument("--validate-only", action="store_true")
    args = arguments.parse_args()
    if args.validate_only:
        print(json.dumps(validate_archive(args.output), indent=2))
        return
    arguments.error("--kernel is required unless --validate-only") if args.kernel is None else None
    receipt = prepare_archive(args.kernel, args.output)
    print(json.dumps({k: receipt[k] for k in (
        "archive_sha256", "archive_bytes", "legacy_module_count",
        "manual_only_module_count", "single_exact_vermagic", "hardware_actions")}, indent=2))


if __name__ == "__main__":
    main()
