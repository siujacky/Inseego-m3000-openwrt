#!/usr/bin/env python3
"""Socket/controller and drain-ioctl emulation only. Never a physical UART."""
import os,re,socket,struct,subprocess,threading,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
FW=ROOT/'analysis/bt-firmware-candidates'
BIN=Path(sys.argv[1])
FRAME=b'\x01\x00\xfc\xf5\x1e\xf3'+(FW/'wcnhpbtfw21.tlv').read_bytes()[:243]

def exact(s,n):
 b=b''
 while len(b)<n:
  part=s.recv(n-len(b))
  if not part:raise EOFError()
  b+=part
 return b

for kind in ('silent','partial','ibs','reject03','early03','early-hwe','early-malformed','signal-frame','outq','lsr','stuck-outq','stuck-lsr'):
 a,b=socket.socketpair();a.settimeout(3);captured=bytearray();errors=[]
 def cc(op,data):a.sendall(b'\x04\x0e'+bytes([len(data)+3,1])+struct.pack('<H',op)+data)
 def controller():
  try:
   assert exact(a,5)==b'\x01\x00\xfc\x01\x19'
   cc(0xfc00,bytes.fromhex('00190c13000000e638010211120c40'))
   assert exact(a,5)==b'\x01\x48\xfc\x01\0'
   captured.extend(exact(a,16))
   if kind=='ibs':a.sendall(b'\xfd')
   if kind=='early03':cc(0xfc00,b'\x00\x1e\x03')
   if kind=='early-hwe':a.sendall(b'\x04\x10\x01\x03')
   if kind=='early-malformed':a.sendall(b'\x99')
   if kind=='signal-frame':os.kill(proc.pid,15)
   while len(captured)<249:
    part=a.recv(249-len(captured))
    if not part:return
    captured.extend(part)
   assert bytes(captured)==FRAME,'aggregate bytes differ'
   if kind=='ibs':assert exact(a,1)==b'\xfc','IBS ACK not deferred to H4 boundary'
   if kind=='reject03':cc(0xfc00,b'\x00\x1e\x03')
   extra=a.recv(1)
   assert extra==b'','unexpected further command'
  except (EOFError,BrokenPipeError,ConnectionResetError):pass
  except Exception as e:errors.append(repr(e))
  finally:a.close()
 env=os.environ.copy()
 if kind in ('partial','outq','lsr','stuck-outq','stuck-lsr'):env['BT_UART_TEST_FAIL_STEP']=kind
 proc=subprocess.Popen([str(BIN),'--test-fd',str(b.fileno()),str(FW),'100'],pass_fds=(b.fileno(),),stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env=env)
 b.close();th=threading.Thread(target=controller);th.start()
 out,err=proc.communicate(timeout=4);th.join(timeout=3)
 assert not errors,(kind,errors,out,err)
 okay=kind in ('silent','partial','ibs')
 assert proc.returncode==(3 if okay else 1),(kind,proc.returncode,out,err)
 writes=re.findall(r'FRAGMENT_WRITE requested=(\d+) written=(\d+) aggregate=(\d+)',err)
 assert writes and all(0<int(w)<=int(r)<=16 for r,w,_ in writes),(kind,err)
 assert sum(int(w) for _,w,_ in writes)==len(captured),(kind,len(captured),err)
 if okay or kind=='reject03':
  assert bytes(captured)==FRAME,(kind,captured,err)
  assert 'pieces=16 ' in err and 'frame_bytes=249' in err and 'complete=1' in err,(kind,err)
 else:
  assert len(captured)==16,(kind,len(captured),err)
  assert 'complete=0' in err,(kind,err)
 if kind=='partial':assert len(writes)>16,(kind,err)
 if kind=='reject03':assert 'EDL_return_byte2=0x03' in err
 if kind=='early03':assert 'early response during fragmented H4 frame' in err
 if kind=='early-hwe':assert 'controller Hardware Error' in err
 if kind.startswith('stuck-'):assert 'fragment whole-frame deadline' in err
 assert 'firmware_loaded=NOT_ESTABLISHED scan_started=0' in out,(kind,out,err)
 assert 'original_termios_restored=success' in err and 'normal_modem_restore_request=success' in err,(kind,err)
 print('PASS fragmented',kind)
