#!/usr/bin/env python3
"""Controller and host-control fault emulation, never a physical UART."""
import os,socket,struct,subprocess,threading,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];FW=ROOT/'analysis/bt-firmware-candidates'
BIN=Path(sys.argv[1]) if len(sys.argv)>1 else Path('/tmp/bt-uart-prebaud-msm-host')
assert BIN.exists(),'missing pre-baud comparison implementation'
def exact(s,n):
 b=b''
 while len(b)<n:
  p=s.recv(n-len(b))
  if not p:raise EOFError()
  b+=p
 return b
for kind in ('silent','forced-high','reject03','baud-ack','late-baud-ack','baud-error','baud-unknown','signal-preamble','fail-drain','fail-hostbaud','fail-flowoff','fail-flowon','fail-setter','fail-termios','fail-readback'):
 a,b=socket.socketpair();a.settimeout(3);seen=[];errors=[]
 def cc(op,data):a.sendall(b'\x04\x0e'+bytes([len(data)+3,1])+struct.pack('<H',op)+data)
 def controller():
  try:
   while True:
    assert exact(a,1)==b'\x01'
    h=exact(a,3);op=struct.unpack_from('<H',h)[0];p=exact(a,h[2]);seen.append((op,p))
    if len(seen)==1:
     assert op==0xfc00 and p==b'\x19';cc(op,bytes.fromhex('00190c13000000e638010211120c40'))
    elif len(seen)==2:
     assert op==0xfc48 and p==b'\0'
     if kind=='baud-ack':cc(op,b'\0')
     if kind=='baud-error':cc(op,b'\x01')
     if kind=='baud-unknown':a.sendall(b'\x04\xff\x02\x00\x92')
     if kind=='signal-preamble':os.kill(proc.pid,15)
    else:
     assert len(seen)==3 and op==0xfc00 and p==b'\x1e\xf3'+(FW/'wcnhpbtfw21.tlv').read_bytes()[:243]
     if kind=='reject03':cc(op,b'\x00\x1e\x03')
     if kind=='late-baud-ack':cc(0xfc48,b'\0')
  except (EOFError,BrokenPipeError,ConnectionResetError):pass
  except Exception as e:errors.append(str(e))
  finally:a.close()
 env=os.environ.copy()
 if kind.startswith('fail-'):env['BT_UART_TEST_FAIL_STEP']=kind[5:]
 if kind=='forced-high':env['BT_UART_TEST_FORCED_RTS']='1'
 proc=subprocess.Popen([str(BIN),'--test-fd',str(b.fileno()),str(FW),'100'],pass_fds=(b.fileno(),),stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env=env)
 b.close();th=threading.Thread(target=controller);th.start();out,err=proc.communicate(timeout=4);th.join(timeout=3)
 assert not errors,(kind,errors,out,err)
 okay=kind in ('silent','forced-high','baud-ack','late-baud-ack')
 assert proc.returncode==(3 if okay else 1),(kind,proc.returncode,out,err)
 expected=3 if kind in ('silent','forced-high','baud-ack','late-baud-ack','reject03') else (1 if kind in ('fail-flowoff','fail-setter','fail-termios','fail-readback') else 2)
 assert len(seen)==expected,(kind,seen,out,err)
 assert 'PREBAUD_META normal_modem_restore_request=success' in err and 'RTS_physical=unknown' in err,(kind,out,err)
 assert 'PREBAUD_META original_termios_restored=success' in err,(kind,out,err)
 assert 'scan_started=0' in out and 'firmware_loaded=NOT_ESTABLISHED' in out,(kind,out,err)
 if kind=='reject03':assert 'EDL_return_byte2=0x03' in err
 if okay:
  assert err.index('wanted_flow=0 actual_flow=0')<err.index('host_baud=115200')<err.index('wanted_flow=1 actual_flow=1')<err.index('source_sequence_complete=1'),err
 if kind=='late-baud-ack':assert 'responses=0' in out,(out,err)
 if kind=='forced-high':assert 'wanted_RTS=0 reported_RTS=1' in err,(out,err)
 print('PASS pre-baud',kind)
