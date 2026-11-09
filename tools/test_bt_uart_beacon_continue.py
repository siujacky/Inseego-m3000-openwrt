#!/usr/bin/env python3
"""Controller emulation. No physical UART, RF or device operations."""
import os,select,socket,struct,subprocess,sys,threading,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];FW=ROOT/'analysis/bt-firmware-candidates'
BIN=Path(sys.argv[1]);PATCH=(FW/'wcnhpbtfw21.tlv').read_bytes();ORIGINAL=(FW/'wcnhpnv21g.bin').read_bytes()
NVM=bytearray(ORIGINAL);off=4;tag2=None;allowed=set()
while off<len(NVM):
 ident,n=struct.unpack_from('<HH',NVM,off);data=off+12
 if ident==2:tag2=bytes(NVM[data:data+n])
 if ident==17:NVM[data]|=0x80;NVM[data+1]=0;allowed.update((data,data+1))
 if ident==27:NVM[data]|=1;allowed.add(data)
 off=data+n
assert off==len(NVM) and tag2 is not None
assert all(NVM[i]==ORIGINAL[i] for i in range(len(NVM)) if i not in allowed)

def exact(s,n):
 b=b''
 while len(b)<n:
  p=s.recv(n-len(b))
  if not p:raise EOFError()
  b+=p
 return b

def ad(empty=False,malformed=False,address=bytes(6)):
 data=b'' if empty else b'\x02\x01\x06'
 payload=b'\x02\x01\x00\x00'+address+bytes([len(data)+(2 if malformed else 0)])+data+b'\xc4'
 return b'\x04\x3e'+bytes([len(payload)])+payload

cases=('receive','no-reports','empty-reports','startup-only','malformed-ad','hardware-error','patch-unchanged','nvm-three-byte','nvm-vendor','nvm-status','nvm-timeout','logging-error','reset-error','scan-enable-error','scan-disable-error','ibs-boundary','nvm-ad-privacy','control-ad-privacy','nvm-hardware','nvm-duplicate','nvm-partial-boundary','nvm-incomplete','startup-stale-cc','receive-stale-cc','cleanup-stale-cc','cleanup-partial-off','signal-receive')
for kind in cases:
 a,b=socket.socketpair();a.settimeout(3);seen=[];errors=[];nv=[];timers=[];ibs=[];incomplete_bytes=[];partial_off=[]
 def cc(op,data):a.sendall(b'\x04\x0e'+bytes([len(data)+3,1])+struct.pack('<H',op)+data)
 def command():
  while True:
   prefix=exact(a,1)
   if prefix==b'\xfd':ibs.append('host-wake');a.sendall(b'\xfc');continue
   if prefix==b'\xfc':ibs.append('host-ack');continue
   assert prefix==b'\x01'
   if kind=='nvm-incomplete' and len(seen)==632:
    incomplete_bytes.append(prefix);cc(0xfc00,b'\x00\x1e')
    assert a.recv(1)==b'','partial NVM remainder must not be sent';raise EOFError()
   if kind=='cleanup-partial-off' and len(seen)==666:
    partial_off.append(prefix);cc(0x200c,b'\0')
    assert a.recv(1)==b'','partial OFF remainder must not be sent';raise EOFError()
   h=exact(a,3);op=struct.unpack_from('<H',h)[0];p=exact(a,h[2]);seen.append((op,p));return op,p
 def no_more():
  try:
   unexpected=command();raise AssertionError(('unexpected later command',unexpected))
  except EOFError:return
 def later(data):
  def send():
   try:a.sendall(data)
   except (OSError,ConnectionError):pass
  timer=threading.Timer(.02,send);timers.append(timer);timer.start()
 def controller():
  try:
   assert command()==(0xfc00,b'\x19');cc(0xfc00,bytes.fromhex('00190c13000000e638010211120c40'))
   assert command()==(0xfc48,b'\0')
   for i in range(629):
    part=PATCH[i*243:(i+1)*243]
    assert command()==(0xfc00,b'\x1e'+bytes([len(part)])+part)
    if i<628:cc(0xfc00,b'\x00\x1e\x03')
    else:a.sendall(bytes.fromhex('04ff050000001301'))
   assert command()==(0xfc00,b'\x19')
   pv=0x38e6 if kind=='patch-unchanged' else 0x8674
   cc(0xfc00,b'\x00\x19\x0c'+struct.pack('<IHHI',0x13,pv,0x201,0x400c1211))
   if kind=='patch-unchanged':no_more();return
   for i in range(27):
    part=bytes(NVM[i*243:(i+1)*243]);op,p=command();assert (op,p)==(0xfc00,b'\x1e'+bytes([len(part)])+part);nv.append(part)
    # No next NVM command may be submitted before this segment's ACK.
    if i==0:assert not select.select([a],[],[],.005)[0],'NVM unexpectedly pipelined'
    if i==0 and kind=='nvm-three-byte':cc(op,b'\x00\x1e\x00');no_more();return
    if i==0 and kind=='nvm-vendor':a.sendall(bytes.fromhex('04ff050000001301'));no_more();return
    if i==0 and kind=='nvm-status':cc(op,b'\x01\x1e');no_more();return
    if i==0 and kind=='nvm-timeout':no_more();return
    if i==0 and kind=='nvm-duplicate':
     frame=bytes.fromhex('040e050100fc001e');a.sendall(frame+frame);no_more();return
    if i==0 and kind=='nvm-partial-boundary':a.sendall(bytes.fromhex('040e050100fc001e04'));no_more();return
    if i==0 and kind=='nvm-ad-privacy':a.sendall(ad(address=bytes.fromhex('112233445566')));no_more();return
    if i==0 and kind=='nvm-hardware':a.sendall(b'\x04\x10\x01\x03');no_more();return
    if kind=='ibs-boundary' and i==0:a.sendall(b'\xfd')
    cc(op,b'\x00\x1e')
   assert b''.join(nv)==bytes(NVM)
   assert command()==(0xfc17,b'\x14\x00')
   if kind=='control-ad-privacy':a.sendall(ad(address=bytes.fromhex('112233445566')));no_more();return
   cc(0xfc17,b'\x01' if kind=='logging-error' else b'\0')
   if kind=='logging-error':no_more();return
   assert command()==(0x0c03,b'');cc(0x0c03,b'\x01' if kind=='reset-error' else b'\0')
   if kind=='reset-error':no_more();return
   startup=((0x200c,b'\0\0'),(0x0c01,bytes.fromhex('00e0000000000020')),(0x2001,bytes.fromhex('0200000000000000')),(0x200b,bytes.fromhex('00600030000000')),(0x200c,b'\x01\0'))
   for op,p in startup:
    assert command()==(op,p)
    if op==0x200c and p==b'\x01\0' and kind in ('startup-only','startup-stale-cc'):
     packet=bytes.fromhex('040e04010c2000');a.sendall(packet+(ad() if kind=='startup-only' else packet))
    else:cc(op,b'\x01' if kind=='scan-enable-error' and p==b'\x01\0' and op==0x200c else b'\0')
    if op==0x200c and p==b'\x01\0':
     if kind=='receive-stale-cc':later(bytes.fromhex('040e04010c2000'))
     if kind=='signal-receive':
      timer=threading.Timer(.02,lambda:os.kill(proc.pid,15));timers.append(timer);timer.start()
     if kind in ('receive','ibs-boundary','scan-disable-error'):later(ad()+ad())
     if kind=='empty-reports':later(ad(empty=True))
     if kind=='malformed-ad':later(ad(malformed=True))
     if kind=='hardware-error':later(b'\x04\x10\x01\x03')
   assert command()==(0x200c,b'\0\0');cc(0x200c,b'\x01' if kind=='scan-disable-error' else b'\0')
   no_more()
  except (EOFError,BrokenPipeError,ConnectionResetError):pass
  except Exception as e:errors.append(repr(e))
  finally:a.close()
 proc=subprocess.Popen([str(BIN),'--test-fd',str(b.fileno()),str(FW),'100'],pass_fds=(b.fileno(),),stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env={**os.environ,**({'BT_UART_TEST_PARTIAL_NVM':'1'} if kind=='nvm-incomplete' else {}),**({'BT_UART_TEST_STALE_OFF':'1'} if kind=='cleanup-stale-cc' else {}),**({'BT_UART_TEST_PARTIAL_OFF':'1'} if kind=='cleanup-partial-off' else {})})
 b.close();th=threading.Thread(target=controller);th.start();out,err=proc.communicate(timeout=5);th.join(timeout=3)
 for timer in timers:timer.join()
 assert not errors,(kind,errors,out,err)
 expected=130 if kind=='signal-receive' else (0 if kind in ('receive','ibs-boundary') else (3 if kind in ('no-reports','empty-reports','startup-only') else 1))
 assert proc.returncode==expected,(kind,proc.returncode,out,err)
 assert 'CONTINUATION ' in out and 'generic_NVM_board_calibration=UNPROVEN normal_Linux_BT_stack=NOT_ESTABLISHED' in out,(out,err)
 assert 'original_termios_restored=success' in err and 'normal_modem_restore_request=success' in err,(kind,out,err)
 if kind.startswith('nvm-') and kind!='nvm-incomplete':
  assert len(nv)==1 and not any(op in (0xfc17,0x0c03,0x200c) for op,_ in seen),(out,err)
 if kind in ('nvm-three-byte','nvm-vendor','nvm-status'):assert 'PATCH_RAW_EVENT' in err and 'phase=NVM' in err,(out,err)
 if kind=='patch-unchanged':assert not nv and 'patch8674 gate blocks NVM' in err,(out,err)
 if kind=='reset-error':assert not any(op==0x200c for op,_ in seen),(out,err)
 if kind in ('receive','ibs-boundary'):assert 'nonempty_reports=2' in out and 'scan_disable=success' in out,(out,err)
 if kind=='empty-reports':assert 'nonempty_reports=0' in out,(out,err)
 if kind=='startup-only':assert 'reports=0 nonempty_reports=0' in out,(out,err)
 if kind=='scan-disable-error':assert 'parent_OFF_reboot_required=1' in err,(out,err)
 if kind=='ibs-boundary':assert 'host-ack' in ibs and 'host-wake' in ibs,(out,err)
 # No advertising/connection/address-setting opcodes can hide in the transcript.
 assert not any(op in (0x200a,0x200d,0x0c0a,0xfc14,0xfc0b) for op,_ in seen),(kind,seen)
 if kind in ('nvm-ad-privacy','control-ad-privacy'):assert '112233445566' not in err and 'unexpected control response type' in err,(out,err)
 if kind in ('nvm-duplicate','nvm-partial-boundary'):assert 'queued/partial response before next NVM request' in err,(out,err)
 if kind=='nvm-incomplete':assert incomplete_bytes==[b'\x01'] and not nv and 'NVM event before complete current request submission' in err,(out,err)
 if kind in ('startup-stale-cc','receive-stale-cc','cleanup-stale-cc','cleanup-partial-off'):assert 'scan_disable=NOT_PROVEN' in out and 'parent_OFF_reboot_required=1' in err,(out,err)
 if kind=='cleanup-partial-off':assert partial_off==[b'\x01'] and 'scan completion before complete current request' in err,(out,err)
 if kind=='signal-receive':assert 'scan_disable=success' in out,(out,err)
 print('PASS beacon-continuation',kind)
