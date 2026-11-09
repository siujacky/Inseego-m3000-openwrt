#!/usr/bin/env python3
"""One-fragment response observation emulator; no hardware."""
import os,socket,struct,subprocess,sys,threading,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];FW=ROOT/'analysis/bt-firmware-candidates';PATCH=(FW/'wcnhpbtfw21.tlv').read_bytes();NVM=bytearray((FW/'wcnhpnv21g.bin').read_bytes());BIN=Path(sys.argv[1]);off=4
while off<len(NVM):
 ident,n=struct.unpack_from('<HH',NVM,off);d=off+12
 if ident==17:NVM[d]|=0x80;NVM[d+1]=0
 if ident==27:NVM[d]|=1
 off=d+n
def exact(s,n):
 b=b''
 while len(b)<n:
  p=s.recv(n-len(b))
  if not p:raise EOFError()
  b+=p
 return b

for kind in ('silence-3s','cc03','cc03-then-vse','vse-then-cc03','unknown-vse','nonzero-status','hardware-error','malformed','partial-event','event-flood','LE-redacted','patch-unchanged','incomplete-fragment','signal'):
 a,b=socket.socketpair();a.settimeout(4);seen=[];errors=[];partials=[];timers=[]
 def cc(data):a.sendall(b'\x04\x0e'+bytes([len(data)+3,1,0,0xfc])+data)
 def command():
  assert exact(a,1)==b'\x01'
  if kind=='incomplete-fragment' and len(seen)==632:
   partials.append(1);cc(b'\0\x1e\x03');assert a.recv(1)==b'';raise EOFError()
  h=exact(a,3);op=struct.unpack_from('<H',h)[0];p=exact(a,h[2]);seen.append((op,p));return op,p
 def later(data):
  def send():
   try:a.sendall(data)
   except OSError:pass
  tm=threading.Timer(.02,send);timers.append(tm);tm.start()
 def controller():
  try:
   assert command()==(0xfc00,b'\x19');cc(bytes.fromhex('00190c13000000e638010211120c40'))
   assert command()==(0xfc48,b'\0')
   for i in range(629):
    part=PATCH[i*243:(i+1)*243];assert command()==(0xfc00,b'\x1e'+bytes([len(part)])+part)
    if i<628:cc(b'\0\x1e\x03')
    else:a.sendall(bytes.fromhex('04ff050000001301'))
   assert command()==(0xfc00,b'\x19');patch=0x38e6 if kind=='patch-unchanged' else 0x8674
   cc(b'\0\x19\x0c'+struct.pack('<IHHI',0x13,patch,0x201,0x400c1211))
   if kind=='patch-unchanged':assert a.recv(1)==b'';return
   assert command()==(0xfc00,b'\x1e\xf3'+bytes(NVM[:243]))
   if kind in ('cc03','cc03-then-vse'):cc(b'\0\x1e\x03')
   if kind=='cc03-then-vse':later(bytes.fromhex('04ff050004001301'))
   if kind=='vse-then-cc03':a.sendall(bytes.fromhex('04ff050004001301'));later(bytes.fromhex('040e060100fc001e03'))
   if kind=='unknown-vse':a.sendall(bytes.fromhex('04ff03aabbcc'))
   if kind=='nonzero-status':cc(b'\x01\x1e\x03')
   if kind=='hardware-error':a.sendall(bytes.fromhex('04100103'))
   if kind=='malformed':a.sendall(b'\x99')
   if kind=='partial-event':a.sendall(bytes.fromhex('04ff05'))
   if kind=='event-flood':a.sendall(bytes.fromhex('04ff0100')*17)
   if kind=='LE-redacted':a.sendall(bytes.fromhex('043e0f0201000011223344556603020106c4'))
   if kind=='signal':os.kill(proc.pid,15)
   assert a.recv(1)==b'','segment2/reset/scan must never be transmitted'
  except (EOFError,BrokenPipeError,ConnectionResetError):pass
  except Exception as e:errors.append(repr(e))
  finally:a.close()
 env={**os.environ}
 if kind!='silence-3s':env['BT_UART_TEST_TAIL_MS']='80'
 if kind=='incomplete-fragment':env['BT_UART_TEST_PARTIAL_TAIL']='1'
 start=time.monotonic();proc=subprocess.Popen([str(BIN),'--test-fd',str(b.fileno()),str(FW),'100'],pass_fds=(b.fileno(),),stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env=env)
 b.close();th=threading.Thread(target=controller);th.start();out,err=proc.communicate(timeout=5);th.join(timeout=4)
 for timer in timers:timer.join()
 elapsed=time.monotonic()-start
 assert not errors,(kind,errors,out,err)
 expected=130 if kind=='signal' else (3 if kind in ('silence-3s','cc03','cc03-then-vse','vse-then-cc03','unknown-vse','LE-redacted') else 1)
 assert (proc.returncode in (1,130) if kind=='signal' else proc.returncode==expected),(kind,proc.returncode,out,err)
 assert 'nvm_accepted=NOT_ESTABLISHED segment2_sent=0 reset_sent=0 scan_started=0' in out,(kind,out,err)
 assert 'original_termios_restored=success' in err,(kind,out,err)
 if kind=='silence-3s':assert elapsed>=3 and elapsed<4.5 and 'window_ms=3000' in out,(elapsed,out,err)
 if kind in ('cc03-then-vse','vse-then-cc03'):assert 'CC=1 VSE=1' in out and '040e060100fc001e03' in err and '04ff050004001301' in err,(out,err)
 if kind=='cc03':assert 'CC=1 VSE=0' in out,(out,err)
 if kind=='LE-redacted':assert 'REDACTED_LE_identity' in err and '112233445566' not in err,(out,err)
 if kind=='patch-unchanged':assert len(seen)==632 and 'patch8674 gate blocks' in err,(out,err)
 if kind=='incomplete-fragment':assert partials==[1] and len(seen)==632 and 'before full fragment submission' in err,(out,err)
 if kind not in ('patch-unchanged','incomplete-fragment'):assert len(seen)==633,(kind,len(seen),out,err)
 print('PASS one-NVM-tail',kind)
