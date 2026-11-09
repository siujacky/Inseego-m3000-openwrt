#!/usr/bin/env python3
"""Exact-file controller emulation for one board-ID read. No hardware."""
import os,socket,struct,subprocess,sys,threading
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];FW=ROOT/'analysis/bt-firmware-candidates';PATCH=(FW/'wcnhpbtfw21.tlv').read_bytes();BIN=Path(sys.argv[1])
def exact(s,n):
 b=b''
 while len(b)<n:
  p=s.recv(n-len(b))
  if not p:raise EOFError()
  b+=p
 return b

for kind in ('id1234','id-zero','id-ffff','unused-byte','extra-data','status-error','wrong-type','short','stale-version','stale-download','vendor','timeout','patch-unchanged','incomplete-query'):
 a,b=socket.socketpair();a.settimeout(4);seen=[];errors=[];partial=[]
 def cc(body):a.sendall(b'\x04\x0e'+bytes([len(body)+3,1,0,0xfc])+body)
 def command():
  prefix=exact(a,1);assert prefix==b'\x01'
  if kind=='incomplete-query' and len(seen)==632:
   partial.append(prefix);cc(b'\0\x23\x02\x12\x34');assert a.recv(1)==b'';raise EOFError()
  h=exact(a,3);op=struct.unpack_from('<H',h)[0];p=exact(a,h[2]);seen.append((op,p));return op,p
 def controller():
  try:
   assert command()==(0xfc00,b'\x19');cc(bytes.fromhex('00190c13000000e638010211120c40'))
   assert command()==(0xfc48,b'\0')
   for i in range(629):
    part=PATCH[i*243:(i+1)*243];assert command()==(0xfc00,b'\x1e'+bytes([len(part)])+part)
    if i<628:cc(b'\0\x1e\x03')
    else:a.sendall(bytes.fromhex('04ff050000001301'))
   assert command()==(0xfc00,b'\x19')
   patch=0x38e6 if kind=='patch-unchanged' else 0x8674
   version=b'\0\x19\x0c'+struct.pack('<IHHI',0x13,patch,0x201,0x400c1211);cc(version)
   if kind=='patch-unchanged':assert a.recv(1)==b'';return
   assert command()==(0xfc00,b'\x23')
   if kind=='timeout':assert a.recv(1)==b'';return
   if kind=='stale-version':cc(version)
   elif kind=='stale-download':cc(b'\0\x1e')
   elif kind=='vendor':a.sendall(bytes.fromhex('04ff050023021234'))
   elif kind=='short':cc(b'\0\x23\x02\x12')
   elif kind=='status-error':cc(b'\x01\x23\x02\x12\x34')
   elif kind=='wrong-type':cc(b'\0\x24\x02\x12\x34')
   else:
    bid=0 if kind=='id-zero' else (0xffff if kind=='id-ffff' else 0x1234)
    cc(b'\0\x23'+bytes([0xa5 if kind=='unused-byte' else 2])+struct.pack('>H',bid)+(b'\x99' if kind=='extra-data' else b''))
   assert a.recv(1)==b'','no further command allowed'
  except (EOFError,BrokenPipeError,ConnectionResetError):pass
  except Exception as e:errors.append(repr(e))
  finally:a.close()
 env={**os.environ,**({'BT_UART_TEST_PARTIAL_BOARD':'1'} if kind=='incomplete-query' else {})}
 proc=subprocess.Popen([str(BIN),'--test-fd',str(b.fileno()),str(FW),'100'],pass_fds=(b.fileno(),),stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env=env)
 b.close();th=threading.Thread(target=controller);th.start();out,err=proc.communicate(timeout=5);th.join(timeout=4)
 assert not errors,(kind,errors,out,err)
 good=kind in ('id1234','id-zero','id-ffff','unused-byte','extra-data')
 assert proc.returncode==(3 if good else 1),(kind,proc.returncode,out,err)
 assert 'NVM_sent=0 reset_sent=0 scan_started=0' in out and 'firmware_running=NOT_ESTABLISHED' in out,(kind,out,err)
 assert 'original_termios_restored=success' in err and 'normal_modem_restore_request=success' in err,(out,err)
 if good:
  assert len(seen)==633 and 'board_query_sent=1 board_reply_valid=1' in out and 'BOARD_ID_OBSERVATION' in err,(kind,out,err)
  assert 'unused_data0_meaning=UNKNOWN' in err and 'selection_or_unlock=NOT_ESTABLISHED' in err,(out,err)
 if kind=='unused-byte':assert 'unused_data0=0xa5' in err,(out,err)
 if kind=='extra-data':assert 'extra_bytes=1' in err,(out,err)
 if kind=='patch-unchanged':assert len(seen)==632 and 'board_query_sent=0' in out,(out,err)
 if kind=='incomplete-query':assert partial==[b'\x01'] and len(seen)==632 and 'board-ID event before complete query submission' in err and 'board_query_sent=0' in out,(out,err)
 if kind in ('stale-version','stale-download','short','wrong-type','status-error','vendor'):assert 'board-ID response shape/status mismatch or stale completion' in err,(out,err)
 print('PASS board-ID',kind)
