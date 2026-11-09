"""Firewall + VPN setter specs: mifi_qcmap_cli (router2 firewall commands) and vpn_cli.

Every spec below was established by static analysis of the stock binaries in
reference/stock-fs/opt/nvtl/bin/ (WSL `arm-linux-gnueabihf-objdump -d -M force-thumb`, handler
addresses read from the {name, handler, help} command table in .data, prompt strings from
.rodata) plus the client libraries libmifi_qcmap_router2_api.so / libvpn_api.so and the daemons
mifi_qcmapd / vpnd for value semantics.  Scratch analysis: %TEMP%/claude/area-firewall/.

Two small Spec subclasses live here because the core Spec only repeats a single prompt:

* RecordListSpec - argv list setters `<cli> <cmd> <count> <f1..fk>*count` (the handlers check
  `(argc-3) == k*count` exactly and fall back to stdin prompts otherwise).  Accepts count 0
  (= clear the list), which the stdin path of set_port_fwd_list rejects.
* BranchSpec - vpn_cli stdin setters whose prompt sequence depends on earlier answers
  (server type, set/clear, authentication type ...).  `flow(values)` returns the exact prompt
  list for that combination or raises ValueError for anything not supported.  Every vpn_cli
  prompt reads with `fgets` in a loop that skips EOF *and empty lines*, so an empty answer
  would shift every later answer: BranchSpec refuses empty values and TABs (the strip loop
  truncates at TAB).

Both keep the fail-closed contract of Spec.check (exact answer count, no line breaks).
Candidates for the core: `Spec(group=(count_index, first, size))` and `Spec(flow=...)`.
"""
from ...errors import ApiError
from ..cli_specs import Spec


class RecordListSpec(Spec):
    """argv list setter: values = [count] + fields*count, 0 <= count <= max_records."""

    def __init__(self, prompts, fields, max_records, **kw):
        kw.setdefault("mode", "argv")
        kw.setdefault("argc", 1 + fields)   # one record; the real count comes from expected_count()
        super().__init__(prompts, **kw)
        self.fields = fields
        self.max_records = max_records

    def expected_count(self, values):
        try:
            n = int(str(values[0]), 10)
        except (IndexError, ValueError):
            raise ValueError("record count missing or not a number")
        if n < 0 or n > self.max_records:
            raise ValueError("record count must be 0..%d" % self.max_records)
        return 1 + self.fields * n

    def sample_answers(self):
        if self.sample:
            return [str(x) for x in self.sample]
        return ["1"] + ["1"] * self.fields


class BranchSpec(Spec):
    """stdin setter whose prompts depend on earlier answers; flow(values) -> list of prompts."""

    def __init__(self, flow, sample, **kw):
        super().__init__(flow([str(x) for x in sample]), sample=sample, **kw)
        self.flow = flow

    def prompts_for(self, values):
        return self.flow([str(v) for v in values])

    def expected_count(self, values):
        try:
            return len(self.flow([str(v) for v in values]))
        except IndexError:
            raise ValueError("not enough answers for this combination")

    def check(self, values):
        super().check(values)
        for v in values:
            s = str(v)
            if not s.strip():
                raise ApiError(400, "invalid_value", "Empty values are not allowed (the CLI would skip the line)")
            if "\t" in s:
                raise ApiError(400, "invalid_value", "Tabs are not allowed")


def _int(values, i):
    try:
        return int(values[i], 10)
    except (IndexError, ValueError):
        raise ValueError("answer %d missing or not a number" % i)


# --------------------------------------------------------------------------------------------
# mifi_qcmap_cli (router2).  Single-value setters: `if (argc == 3) v = strtol(argv[2], 10)`,
# otherwise one prompt + one fgets (no loop).  No reset prompt in any of these handlers.
# --------------------------------------------------------------------------------------------
_QC = "mifi_qcmap_cli 0x%x: cmp r0,#3; beq argv path -> strtol(argv[2],10) -> %s; prompt .rodata 0x%x"

PF_PROMPTS = ["Enter the number of records: ", "Enable this port forwarding record (0,1)?: ", "Enter name: ",
              "Enter Ip address: ", "Enter Remote Port number: ", "Enter Local Port number: ",
              "Enter protocol (1 for tcp, 2 for udp, 3 for both): "]
PFL_PROMPTS = ["Enter the number of records: ", "Enable this port filtering record(0,1)?: ", "Enter name: ",
               "Enter start/external Port number: ", "Enter end/internal Port number: ",
               "Enter protocol (1 for tcp, 2 for udp, 3 for both): "]
AP_PROMPTS = ["Enter the number of records: ", "Enable this allowed  port record (0,1)?: ",
              "Enter Interface  Name(s) (Separate by comma if many): ", "Enter port type (0 for range, 1 for list): ",
              "Enter start/external Port number: ", "Enter end/internal Port number: ",
              "Enter Allowed Ports list separated by comma:", "Enter protocol (1 for tcp, 2 for udp, 3 for both): "]

# --------------------------------------------------------------------------------------------
# vpn_cli
# --------------------------------------------------------------------------------------------
P_TYPE = ("Enter server type (0-NONE, 1-PPTP, 2-OpenVPN, 3-L2TP Over IPSec, 4-IPSec, "
          "5-OpenVPN with config files 6- WiredGuard VPN): ")
P_TUNNEL = "Enter Tunnel Index:"
P_CLEAR = "Clear/Set server configuration(Set-0, Clear-1): "
P_RETRIES = "Enter server connection retries: "
OVPN_CFG = ["Enter OpenVPN uploaded config file path: ", "Enter OpenVPN uploaded config file name: ",
            "Enter OpenVPN server URL: ", "Enter OpenVPN server username: ", "Enter OpenVPN server password: "]
WG_SET = ["Enter WGuard VPN Tunnel Index[0-2]:", "Enable WGuard VPN Tunnel(0-Disable, 1-Enable):",
          "Select Tunnel Startup(0-Manually, 1-Automatically):", "Enter WGuard Tunnel name:",
          "Enter WGuard VPN connection retries: ", "Enter WGuard VPN Path where Keys are stored: ",
          "Enter WGuard VPN Private Key file name: ", "Enter WGuard VPN Private Key content: ",
          "Enter WGuard VPN Public Key content: ", "Enter WGuard VPN Tunnel IP: ", "Enter WGuard VPN Peer IP: ",
          "Enter WGuard VPN Allowed IPs/Subnets separated by ',': ", "Enter WGuard VPN EndPoint IP: ",
          "Enter WGuard VPN Listen port: ", "Enter WGuard VPN EndPoint port: ", "Enter WGuard VPN Tunnel Keepalive:"]
WG_CLEAR = ["Enter WGuard Tunnel Index[0-2]:"]
IPSEC_CLEAR = ["Enter IPsec Tunnel Index[0-5]:"]
IPSEC_HEAD = ["Enter IPsec Tunnel Index[0-5]:", "Select Tunnel Startup(0-Manually, 1-Automatically):",
              "Enable IPsec Tunnel(0-Disable, 1-Enable):", "Enter IPSec Tunnel name:",
              "Enable Left Firewall (0-Disable, 1-Enable):", "Enable Anonymous Mode (0-Disable, 1-Enable):",
              "Enter IPSec Local ID: ", "Enter IPSec Remote ID: "]
P_CLIENT_AUTH = ("Enter IPSec IKE Client or Local Authentication Type(0-None, 1-Pre shared key, 2-EAP_MSCHAPv2, "
                 "3-Pub Key/RSA certificates): ")
P_SERVER_AUTH = ("Enter IPSec IKE Server or Remote Authentication Type(0-None, 1-Pre shared key, 2-EAP_MSCHAPv2, "
                 "3-Pub Key/RSA certificates): ")
CLIENT_AUTH = {1: ["Enter IPSec IKE client pre shared key: "],
               2: ["Enter IPSec IKE client username: ", "Enter IPSec IKE client password: "]}
SERVER_AUTH = {1: ["Enter IPSec IKE server pre shared key: "],
               2: ["Enter IPSec IKE server username: ", "Enter IPSec IKE server password: "]}
P_IKE_VERSION = "Enter IPSec IKE version(1-IKEv1, 2-IKEv2): "
IPSEC_LOCAL = ["Enter IPSec Local IP address: ", "Enter IPSec Local Network Subnet: "]
IPSEC_TAIL = ["Enter IPSec Remote IP address: ", "Enter IPSec Remote Network Subnet: ",
              "Enter IPSec IKE SA Key Lifetime: ", "Enter IPSec Algorithms for Phase1: ",
              "Enter IPSec Key Lifetime: ", "Enter IPSec Algorithms for Phase2: ",
              "Enable DPD(0-Disable, 1-Enable):"]
DPD = ["Enter DPD Action(0-None, 1-CLEAR, 2-HOLD, 3-RESTART): ", "Enter DPD Delay: ", "Enter DPD Timeout: "]


def ipsec_set_prompts(values, base=2):
    """Prompt sequence of vpn_cli sub_38c8 (IPsec tunnel set) starting at answers[base]."""
    out = list(IPSEC_HEAD)
    i = base + len(IPSEC_HEAD)
    out.append(P_CLIENT_AUTH)
    ca = _int(values, i)
    if ca not in CLIENT_AUTH:                     # 0 = none, 3 = certificates: not exposed
        raise ValueError("client authentication type %d not supported" % ca)
    out += CLIENT_AUTH[ca]
    i += 1 + len(CLIENT_AUTH[ca])
    out.append(P_SERVER_AUTH)
    sa = _int(values, i)
    if sa not in SERVER_AUTH:
        raise ValueError("server authentication type %d not supported" % sa)
    out += SERVER_AUTH[sa]
    i += 1 + len(SERVER_AUTH[sa])
    out.append(P_IKE_VERSION)
    i += 1
    if ca != 2:                                   # 0x3c1c: EAP client skips local IP + subnet
        out += IPSEC_LOCAL
        i += len(IPSEC_LOCAL)
    out += IPSEC_TAIL
    i += len(IPSEC_TAIL)
    if _int(values, i - 1) not in (0, 1):
        raise ValueError("DPD enable must be 0 or 1")
    if _int(values, i - 1) == 1:                  # 0x3eaa: DPD on -> action, delay, timeout
        out += DPD
    return out


def _set_settings_flow(values):
    t = _int(values, 0)
    clear = _int(values, 1)
    if clear not in (0, 1):
        raise ValueError("set/clear flag must be 0 or 1")
    head = [P_TYPE, P_CLEAR]
    if t == 5:
        return head if clear else head + [P_RETRIES] + OVPN_CFG
    if t == 6:
        return head + (WG_CLEAR if clear else WG_SET)
    if t == 4:
        return head + (IPSEC_CLEAR if clear else ipsec_set_prompts(values, 2))
    # 1 PPTP / 2 OpenVPN manual / 3 L2TP: not exposed (no empty optional fields possible,
    # vpn_get_setting rejects 1 and 3); 0 NONE calls vpn_clear_all_settings.
    raise ValueError("server type %d is not supported by this UI" % t)


def _connect_flow(values):
    t = _int(values, 0)
    if t == 5:
        return [P_TYPE]
    if t in (4, 6):
        return [P_TYPE, P_TUNNEL]
    raise ValueError("server type %d is not supported for connect/disconnect" % t)


SPECS = {
    # ---- mifi_qcmap_cli: firewall switches -----------------------------------------------
    ("mifi_qcmap_cli", "set_vpnpassthrough"): Spec(
        ["Enter VPN Passthrough status (0=Disable, 1=Enable): "], tier=1, mode="argv", argc=1, sample=["1"],
        evidence=_QC % (0x3664, "router2_set_vpn_passthrough_enabled", 0x831c)),
    ("mifi_qcmap_cli", "set_http_redirect"): Spec(
        ["Enter HTTP redirect status (0=Disable, 1=Enable): "], tier=1, mode="argv", argc=1, sample=["1"],
        evidence=_QC % (0x3924, "router2_set_http_redirect_enabled", 0x83c4)),
    ("mifi_qcmap_cli", "set_icmp_response"): Spec(
        ["Enter ICMP response status (0=Disable, 1=Enable): "], tier=1, mode="argv", argc=1, sample=["0"],
        evidence=_QC % (0x47b8, "router2_set_icmp_response_enabled", 0x89b0)),
    ("mifi_qcmap_cli", "set_ipv6_firewall"): Spec(
        ["Enter Ipv6 firewall status (0=Disable, 1=Enable): "], tier=1, mode="argv", argc=1, sample=["1"],
        evidence="mifi_qcmap_cli 0x4930: argc==2 -> prompt (.rodata 0x8a04), argc==3 -> strtol(argv[2],10) -> "
                 "router2_set_ipv6_firewall_enabled, any other argc -> rc 2"),
    ("mifi_qcmap_cli", "set_allowed_ports"): Spec(
        ["Enter allowed port status (0=Disable, 1=Enable): "], tier=1, mode="argv", argc=1, sample=["0"],
        evidence=_QC % (0x4a54, "router2_set_allowed_port_enabled", 0x8a48)),
    # fallbacks used only when the vendor REST resource answers unsupported/disabled
    ("mifi_qcmap_cli", "set_port_forwarding"): Spec(
        ["Enter port forwarding status (0=Disable, 1=Enable): "], tier=1, mode="argv", argc=1, sample=["0"],
        evidence=_QC % (0x3168, "router2_set_port_forwarding_enabled", 0x8194),
        notes="fallback for REST PortForwardingSettings"),
    ("mifi_qcmap_cli", "set_port_filter"): Spec(
        ["Enter port filtering status (0=Disable, 1=Enable): "], tier=1, mode="argv", argc=1, sample=["0"],
        evidence=_QC % (0x3028, "router2_set_port_filtering_enabled", 0x8144),
        notes="fallback for REST PortFilteringSettings"),
    ("mifi_qcmap_cli", "set_dmz"): Spec(
        ["Enter DMZ Status (0=OFF, 1=ON): ", "Enter DMZ IP address: "], tier=1, mode="argv", argc=2,
        sample=["0", "192.168.1.50"],
        evidence="mifi_qcmap_cli 0x3cec: cmp r6,#4; beq 0x3e08 -> strtol(argv[2],10) status, strncpy(argv[3]) ip "
                 "-> router2_set_dmz_settings (stdin path asks the IP only after status, .rodata 0x851c/0x8540)",
        notes="fallback for REST DMZSettings"),
    # ---- mifi_qcmap_cli: lists (argv) --------------------------------------------------------
    ("mifi_qcmap_cli", "set_port_fwd_list"): RecordListSpec(
        PF_PROMPTS, fields=7, max_records=30, tier=1,
        sample=["1", "1", "Web", "192.168.1.50", "2", "8080", "80", "1"],
        evidence="mifi_qcmap_cli 0x2c60: argc>2, (argc-3)%7==0, count<=30, 7*count==argc-3 -> argv path; per "
                 "record argv: enable,name(strcpy_chk 28),ip(17),range_type(+56),remote(+60),local(+64),proto(+68); "
                 "router2_set_port_fwd_app_list (lib 0x5494) validates enable 0/1, name<=27 printable, inet_pton, "
                 "proto 1..3, ports<65536, range_type 0..2 (0 requires remote<=local); mifi_qcmapd 0xc5b0 adds "
                 "one SNAT entry gport=+60 pri_port=+64 (range type ignored). count 0 clears the list.",
        notes="answers: [count] + [enable, name, ip, range_type(2=translate), external, internal, proto]*count"),
    ("mifi_qcmap_cli", "set_port_filter_list"): RecordListSpec(
        PFL_PROMPTS, fields=5, max_records=25, tier=1, sample=["1", "HTTP", "1", "80", "80", "1"],
        evidence="mifi_qcmap_cli 0x3308: argc>2, (argc-3)%5==0, count<=25, 5*count==argc-3 -> argv path; per "
                 "record argv: name(strcpy_chk 28),enable,start,end,proto; router2_set_port_filter_app_list (lib "
                 "0x4798) validates count<=25, enable 0/1, name<=27 printable, proto 1..3, start<=end unless end==0. "
                 "count 0 clears the list.",
        notes="answers: [count] + [name, enable, start, end, proto]*count"),
    ("mifi_qcmap_cli", "set_allowed_port_list"): RecordListSpec(
        AP_PROMPTS, fields=7, max_records=10, tier=1, sample=["1", "1", "rmnet_data0", "1", "22", "0", "3", "1"],
        evidence="mifi_qcmap_cli 0x4ba0: argc>2, (argc-3)%7==0, count<=10, 7*count==argc-3 -> argv path; per "
                 "record argv: enable, interfaces(strcpy_chk 51), type(0 range/1 list), start|port-list(64), end, "
                 "proto, slot 7 unused (instance = index); stdin path asks start/end only for type 0 and the list "
                 "only for type 1. count 0 clears the list.",
        notes="answers: [count] + [enable, interfaces, type, start_or_list, end, proto, instance]*count"),
    # ---- vpn_cli --------------------------------------------------------------------------
    ("vpn_cli", "set_auto_connect"): Spec(
        ["Enter VPN auto connect status (0=Disable, 1=Enable): "], tier=1, mode="argv", argc=1, sample=["0"],
        evidence="vpn_cli 0x17d0: cmp r5,#3; beq 0x187a -> strtol(argv[2]) -> vpn_set_auto_connect_enabled; "
                 "prompt .rodata 0x4ce0"),
    ("vpn_cli", "set_ipsec_vpn"): Spec(
        ["Enter IPSec VPN Service status (0=Disable, 1=Enable): "], tier=1, mode="argv", argc=1, sample=["0"],
        evidence="vpn_cli 0x49a4: argc==3 -> strtol(argv[2]) -> vpn_set_ipsec_vpn_enabled; rc 0x1d0006 calls "
                 "0x47f8 'Do you want to reset the device Y/N' whose single fgets reads EOF in argv mode -> no reboot",
        notes="rc 6 = a reboot is needed for the change to take effect (reported, never triggered)"),
    ("vpn_cli", "set_wguard_vpn"): Spec(
        ["Enter WGuard VPN Service status (0=Disable, 1=Enable): "], tier=1, mode="argv", argc=1, sample=["0"],
        evidence="vpn_cli 0x48b8: argc==3 -> strtol(argv[2]) -> vpn_set_wguard_vpn_enabled; rc 0x1d0006 -> 0x47f8 "
                 "reset prompt reads EOF -> no reboot",
        notes="rc 6 = a reboot is needed for the change to take effect (reported, never triggered)"),
    ("vpn_cli", "connect_vpn"): BranchSpec(
        _connect_flow, ["5"], tier=1,
        evidence="vpn_cli 0x2164: fgets-loop type; tbb (0x21ee) type 2/5 -> connect_vpn(type,-1), 4/6 -> "
                 "'Enter Tunnel Index:' (0x4fdc, idx<=5), 3/other -> rc 2"),
    ("vpn_cli", "disconnect_vpn"): BranchSpec(
        _connect_flow, ["5"], tier=1,
        evidence="vpn_cli 0x1ff4: same switch as connect_vpn (tbb 0x207e)"),
    ("vpn_cli", "set_settings"): BranchSpec(
        _set_settings_flow, ["4", "1", "5"], tier=1,
        evidence="vpn_cli 0x43e0: type (0x4f60) + set/clear (0x6fdc); 5/set -> retries (0x70d0) + sub_27e4 "
                 "(path, name, URL, user, password); 5/clear -> vpn_clear_openvpn_cfg_settings; 6/set -> sub_29a4 "
                 "(16 prompts, idx<=2); 6/clear -> idx (0x6494) -> vpn_clear_wguard_cfg_settings; 4/set -> sub_38c8 "
                 "(auth-type and DPD branches, EAP client skips local IP/subnet at 0x3c1c); 4/clear -> idx (0x6810). "
                 "All prompts are fgets loops that skip EOF and empty lines. No reset prompt in this handler.",
        notes="answers start with [server_type, clear_flag]; see BranchSpec flow for the rest"),
    ("vpn_cli", "validate_openvpn_config_files"): Spec(
        ["Enter OpenVPN uploaded config file path: "], tier=1, sample=["/tmp/vpnFiles"],
        evidence="vpn_cli 0x1940: fgets loop (255) -> vpn_validate_openvpn_config_file_settings; prints "
                 "'config file name:[%s]', 'config URL:[%s]', 'Missed configuration file name is: [%s]'"),
}
