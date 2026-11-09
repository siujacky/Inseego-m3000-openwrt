#!/usr/bin/env python3
"""First-segment diagnostic controller emulation; no UART/hardware access."""
import socket, subprocess, struct, sys, threading, tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
FW=ROOT/'analysis/bt-firmware-candidates'
BIN=Path(sys.argv[1]) if len(sys.argv)>1 else Path('/tmp/bt-uart-first-host')
PATCH='hpbtfw21.tlv' if len(sys.argv)>2 and sys.argv[2]=='hp21' else 'wcnhpbtfw21.tlv'
assert BIN.exists(), 'missing first-segment-only implementation'
def exact(s,n):
    b=b''
    while len(b)<n:
        p=s.recv(n-len(b))
        if not p: raise EOFError()
        b+=p
    return b
for kind in ('silent','rejected03','two-byte-reply','hardware-error'):
    a,b=socket.socketpair();a.settimeout(3);seen=[];errors=[]
    def controller():
        try:
            while True:
                assert exact(a,1)==b'\x01'
                h=exact(a,3);op=struct.unpack_from('<H',h)[0];p=exact(a,h[2]);seen.append((op,p))
                if op==0xfc00 and p==b'\x19':
                    a.sendall(bytes.fromhex('040e120100fc00190c13000000e638010211120c40'))
                else:
                    assert op==0xfc00 and p==b'\x1e\xf3'+(FW/PATCH).read_bytes()[:243]
                    assert len(seen)==2, 'must send exactly one firmware segment'
                    if kind=='rejected03': a.sendall(bytes.fromhex('040e060100fc001e03'))
                    if kind=='two-byte-reply': a.sendall(bytes.fromhex('040e050100fc001e'))
                    if kind=='hardware-error': a.sendall(bytes.fromhex('04100142'))
        except (EOFError,ConnectionResetError,BrokenPipeError): pass
        except Exception as e: errors.append(str(e))
        finally: a.close()
    proc=subprocess.Popen([str(BIN),'--test-fd',str(b.fileno()),str(FW),'100'],pass_fds=(b.fileno(),),stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    b.close();th=threading.Thread(target=controller);th.start();out,err=proc.communicate(timeout=3);th.join(timeout=3)
    assert not errors,(kind,errors,out,err)
    assert len(seen)==2,(kind,seen)
    assert proc.returncode==(1 if kind in ('rejected03','hardware-error') else 3),(kind,proc.returncode,out,err)
    assert 'diagnostic_only=1' in out and 'scan_started=0' in out and 'first_segment_bytes=243' in out,(kind,out,err)
    if kind=='silent': assert 'responses=0' in out
    if kind=='rejected03': assert 'EDL_return_byte2=0x03' in err
    print('PASS first-segment',kind)
if PATCH=='hpbtfw21.tlv':
    with tempfile.TemporaryDirectory() as directory:
        target=Path(directory)
        for name in ('hpbtfw21.tlv','hpnv21g.bin'):
            (target/name).write_bytes((FW/name).read_bytes())
        changed=bytearray((target/PATCH).read_bytes());changed[50]^=1
        (target/PATCH).write_bytes(changed)
        a,b=socket.socketpair()
        p=subprocess.run([str(BIN),'--test-fd',str(b.fileno()),str(target),'100'],pass_fds=(b.fileno(),),capture_output=True,text=True,timeout=2)
        b.close()
        assert p.returncode==2 and 'hash mismatch' in p.stderr,(p.returncode,p.stderr)
        assert a.recv(1)==b'', 'unverified HP firmware must not touch transport'
        a.close();print('PASS HP21 changed-file rejection before I/O')
