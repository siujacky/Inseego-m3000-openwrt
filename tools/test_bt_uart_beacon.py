#!/usr/bin/env python3
"""Host-only socket controller emulator; never opens a real UART."""
import os, socket, struct, subprocess, sys, threading, time, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FW = ROOT / 'analysis/bt-firmware-candidates'
BIN = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('/tmp/bt-uart-beacon-host')
ADV_BODY = b'\x02\x01\x00\x00' + b'\x00'*6 + b'\x03\x02\x01\x06\xc0'
ADV = b'\x04\x3e'+bytes([len(ADV_BODY)])+ADV_BODY

def exact(sock, n):
    out = b''
    while len(out) < n:
        more = sock.recv(n-len(out))
        if not more: raise EOFError()
        out += more
    return out

def case(kind):
    a,b = socket.socketpair()
    a.settimeout(4)
    errors=[]; seen=[]; chunks=[]; phase=0; sleep_once=False
    patch=(FW/'wcnhpbtfw21.tlv').read_bytes()
    nvm=bytearray((FW/'wcnhpnv21g.bin').read_bytes())
    pos=4
    while pos<len(nvm):
        tag,n=struct.unpack_from('<HH',nvm,pos)
        if tag==17: nvm[pos+12]|=0x80; nvm[pos+13]=0
        if tag==27: nvm[pos+12]|=1
        pos+=12+n
    def cc(op,data=b'\0'):
        # Fragment every response; the transport must not assume read==event.
        pkt=b'\x04\x0e'+bytes([3+len(data),0 if kind=='zero-credit' else 1])+struct.pack('<H',op)+data
        for piece in (pkt[:1],pkt[1:4],pkt[4:]): a.sendall(piece)
    def emu():
        nonlocal phase,sleep_once,chunks
        try:
            while True:
                typ=exact(a,1)[0]
                if typ==0xfd:
                    if kind!='wake-timeout': a.sendall(b'\xfc')
                    continue
                if typ==0xfc: continue
                assert typ==1, ('bad H4 TX',typ)
                hdr=exact(a,3); op=struct.unpack_from('<H',hdr)[0]; data=exact(a,hdr[2])
                seen.append((op,data))
                if op==0xfc00 and data==b'\x19':
                    identity=bytes.fromhex('00190c13000000e638010211120c40')
                    if kind=='wrong-rom': identity=identity[:-1]+b'\x41'
                    if kind=='already-loaded': identity=identity[:7]+b'\x74\x86'+identity[9:]
                    cc(op,identity)
                    if kind=='stall': time.sleep(1.5); return
                elif op==0xfc00 and data[:1]==b'\x1e':
                    assert len(data)==data[1]+2
                    chunks.append(data[2:]); total=sum(map(len,chunks))
                    target=patch if phase==0 else bytes(nvm)
                    assert b''.join(chunks)==target[:total], 'firmware/NVM content mismatch'
                    if kind=='intermediate-events' and phase==0 and total<len(target): cc(op,b'\x00\x1e\x03')
                    if kind=='timeout' and phase==0 and total==len(target): continue
                    if kind=='nvm-timeout' and phase==1: continue
                    if phase==1 or total==len(target):
                        cc(op,b'\x00\x1f' if kind=='bad-ack' else b'\x00\x1e')
                    if total==len(target): chunks=[]; phase+=1
                elif op==0xfc17:
                    assert phase==2 and data==b'\x14\0'; cc(op)
                elif op==0x0c03:
                    assert phase==2 and not data; cc(op)
                    # Firmware can emit unsolicited IBS wake; must acknowledge.
                    a.sendall(b'\xfd')
                elif op in (0x0c01,0x2001,0x200b,0x200c):
                    if op==0x200b: assert data==bytes.fromhex('00600030000000')
                    if op==0x200c and data==b'\1\0':
                        # Startup traffic is not scan evidence.
                        a.sendall(ADV); cc(op)
                        if kind=='startup-only': pass
                        elif kind=='empty-data': a.sendall(b'\x04\x3e\x0c\x02\x01\x00\x00'+b'\0'*6+b'\0\xc0')
                        elif kind=='malformed': a.sendall(bytes.fromhex('043e020201'))
                        elif kind=='hardware-error': a.sendall(bytes.fromhex('04100142'))
                        elif kind=='signal': os.kill(proc.pid,15)
                        else:
                            a.sendall(b'\xfe\xfd')
                            # Wait for wake ACK before reports.
                            assert exact(a,1)==b'\xfc'
                            if kind=='ibs-in-payload': a.sendall(ADV[:14]+b'\xfd\xfc\xfe'+ADV[17:])
                            else: a.sendall(ADV)
                            if kind=='rx-then-hardware-error':
                                time.sleep(.02); a.sendall(bytes.fromhex('04100142'))
                    else:
                        if op==0x200c: a.sendall(ADV) # cleanup must not count
                        cc(op)
                else: raise AssertionError(('unexpected command',hex(op),data.hex()))
        except (EOFError,ConnectionResetError,BrokenPipeError): pass
        except Exception as exc: errors.append(str(exc))
        finally: a.close()
    proc=subprocess.Popen([str(BIN),'--test-fd',str(b.fileno()),str(FW),'100'],pass_fds=(b.fileno(),),stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    b.close(); th=threading.Thread(target=emu); th.start()
    out,err=proc.communicate(timeout=8); th.join(timeout=4)
    assert not errors,(kind,errors,out,err)
    expected={'ok':0,'ibs-in-payload':0,'startup-only':3,'empty-data':3,'signal':130}.get(kind,1)
    assert proc.returncode==expected,(kind,proc.returncode,out,err)
    if kind in ('ok','ibs-in-payload'):
        assert 'reports=1 ' in out and 'ibs_wakes=2 ' in out and 'min_dBm=-64' in out,(out,err)
    if kind=='startup-only': assert 'reports=0 ' in out,(out,err)
    if kind=='rx-then-hardware-error': assert 'reports=1 ' in out and 'code=0x42' in err,(out,err)
    if kind=='intermediate-events':
        assert 'unexpected intermediate patch completion' in err and 'phase=patch' in err and 'EDL_return_byte2=0x03' in err,(out,err)
    if kind not in ('wrong-rom','already-loaded','timeout','bad-ack','zero-credit','nvm-timeout','stall','wake-timeout','intermediate-events'):
        assert seen[-1]==(0x200c,b'\0\0'),(kind,seen[-1])
        assert 'scan_disable=success' in out,(kind,out,err)
    else: assert not any(op==0x200b for op,_ in seen),kind
    print('PASS',kind)

if not BIN.exists():
    raise AssertionError('missing implementation: host UART protocol binary')
for name in ('ok','ibs-in-payload','startup-only','empty-data','malformed','hardware-error','rx-then-hardware-error','signal','wrong-rom','already-loaded','timeout','bad-ack','zero-credit','nvm-timeout','stall','wake-timeout','intermediate-events'):
    case(name)

with tempfile.TemporaryDirectory() as directory:
    d=Path(directory)
    for name in ('wcnhpbtfw21.tlv','wcnhpnv21g.bin'):
        (d/name).write_bytes((FW/name).read_bytes())
    changed=bytearray((d/'wcnhpnv21g.bin').read_bytes()); changed[-1]^=1
    (d/'wcnhpnv21g.bin').write_bytes(changed)
    a,b=socket.socketpair()
    p=subprocess.run([str(BIN),'--test-fd',str(b.fileno()),str(d),'100'],pass_fds=(b.fileno(),),capture_output=True,text=True,timeout=2)
    b.close()
    assert p.returncode==2 and 'hash mismatch' in p.stderr,(p.returncode,p.stderr)
    assert a.recv(1)==b'', 'unverified firmware must never write transport'
    a.close()
    print('PASS changed-firmware-rejected-before-I/O')
