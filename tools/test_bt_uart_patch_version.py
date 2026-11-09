#!/usr/bin/env python3
"""Controller/drain emulation for the patch-only policy, never physical UART."""
import os,socket,struct,subprocess,threading,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];FW=ROOT/'analysis/bt-firmware-candidates'
BIN=Path(sys.argv[1]);PATCH=(FW/'wcnhpbtfw21.tlv').read_bytes()

def exact(s,n):
 b=b''
 while len(b)<n:
  p=s.recv(n-len(b))
  if not p:raise EOFError()
  b+=p
 return b

cases=('final2','intermediate03-final2','intermediate03-final03','queued2-final2','late03-final2','two-finals','nonzero-status','hardware-error','malformed','unexpected-opcode','signal','patch-outq','patch-lsr','patch-stall','patch-deadline','patch-timeout','ibs','partial-boundary','write-deadline','quiet-delayed03','quiet-flood','pending-final03','pending-final2','cleanup-flush','cleanup-set','cleanup-get','cleanup-mismatch','final03-and-cleanup-set','early-exclusive','early-discipline','early-snapshot','final-vendor-after628','final-hwe-after628','early-vendor','version-changed','version-unchanged','version-identity','version-stale-cc','version-unknown-vse','version-timeout','unknown-vse-trailer','unknown-vse-status','vse-pending','version-status','version-incomplete')
for kind in cases:
 a,b=socket.socketpair();a.settimeout(3);seen=[];errors=[];segments=[];ibs_acks=[];partial_query=[]
 if kind=='write-deadline':b.setsockopt(socket.SOL_SOCKET,socket.SO_SNDBUF,1024)
 def cc(op,data):a.sendall(b'\x04\x0e'+bytes([len(data)+3,1])+struct.pack('<H',op)+data)
 def controller():
  try:
   while True:
    prefix=exact(a,1)
    while prefix==b'\xfc':ibs_acks.append(len(segments));prefix=exact(a,1)
    assert prefix==b'\x01'
    if kind=='version-incomplete' and len(segments)==629:
     partial_query.append(prefix)
     cc(0xfc00,b'\x00\x19\x0c'+struct.pack('<IHHI',0x13,0x8674,0x201,0x400c1211))
     assert a.recv(1)==b'','remaining partial query bytes must not be sent';return
    hdr=exact(a,3);op=struct.unpack_from('<H',hdr)[0];p=exact(a,hdr[2]);seen.append(op)
    if len(seen)==1:
     assert op==0xfc00 and p==b'\x19';cc(op,bytes.fromhex('00190c13000000e638010211120c40'));continue
    if len(seen)==2:assert op==0xfc48 and p==b'\0';continue
    if len(segments)==629 and kind.startswith('version-'):
     assert op==0xfc00 and p==b'\x19','only one documented read version query is allowed'
     assert len(seen)==632,'unexpected repeated query'
     if kind=='version-timeout':continue
     if kind=='version-stale-cc':cc(op,b'\x00\x1e');continue
     if kind=='version-status':cc(op,b'\x01\x19\x0c'+struct.pack('<IHHI',0x13,0x8674,0x201,0x400c1211));continue
     if kind=='version-unknown-vse':a.sendall(b'\x04\xff\x02\x00\x19');continue
     patch=0x38e6 if kind=='version-unchanged' else 0x8674
     soc=0x400c1210 if kind=='version-identity' else 0x400c1211
     cc(op,b'\x00\x19\x0c'+struct.pack('<IHHI',0x13,patch,0x201,soc));continue
    assert op==0xfc00 and len(p)>=2 and p[0]==0x1e and p[1]==len(p)-2
    i=len(segments);assert p[2:]==PATCH[i*243:(i+1)*243];segments.append(p[2:])
    final=len(segments)==629
    if len(segments)==1:
     if kind=='queued2-final2':cc(op,b'\x00\x1e')
     if kind=='nonzero-status':cc(op,b'\x01\x1e\x03')
     if kind=='hardware-error':a.sendall(b'\x04\x10\x01\x03')
     if kind=='malformed':a.sendall(b'\x99')
     if kind=='unexpected-opcode':cc(0xfc99,b'\0')
     if kind=='signal':os.kill(proc.pid,15)
     if kind=='early-vendor':a.sendall(b'\x04\xff\x02\x00\x04')
     if kind=='ibs':a.sendall(b'\xfd')
     if kind=='write-deadline':
      import time
      time.sleep(.4);return
    if len(segments)==628 and kind=='partial-boundary':a.sendall(b'\x04\x0e')
    if len(segments)==628 and kind=='quiet-delayed03':
     import time
     time.sleep(.05);cc(op,b'\x00\x1e\x03')
    if len(segments)==628 and kind=='quiet-flood':
     import time
     for _ in range(60):cc(op,b'\x00\x1e\x03');time.sleep(.02)
    if not final and (kind.startswith('intermediate03') or kind in ('final-vendor-after628','final-hwe-after628')):cc(op,b'\x00\x1e\x03')
    if final:
     assert b''.join(segments)==PATCH and len(segments[-1])==180
     if kind=='patch-timeout':continue
     if kind.startswith('version-') or kind=='vse-pending':a.sendall(b'\x04\xff\x05\x00\x00\x00\x13\x01')
     elif kind=='unknown-vse-status':a.sendall(b'\x04\xff\x05\x00\x00\x03\x13\x01')
     elif kind=='unknown-vse-trailer':a.sendall(b'\x04\xff\x05\x00\x00\x00\x13\x02')
     elif kind=='final-vendor-after628':a.sendall(b'\x04\xff\x05\x00\x04\x00\x00\x00')
     elif kind=='final-hwe-after628':a.sendall(b'\x04\x10\x01\x03')
     elif kind=='late03-final2':
      # A bad first final candidate must stop; the following valid one cannot rescue it.
      cc(op,b'\x00\x1e\x03');cc(op,b'\x00\x1e')
     elif kind in ('intermediate03-final03','pending-final03','final03-and-cleanup-set'):cc(op,b'\x00\x1e\x03')
     elif kind=='two-finals':cc(op,b'\x00\x1e');cc(op,b'\x00\x1e')
     else:cc(op,b'\x00\x1e')
  except (EOFError,BrokenPipeError,ConnectionResetError):pass
  except Exception as e:errors.append(repr(e))
  finally:a.close()
 env=os.environ.copy()
 if kind.startswith('patch-'):env['BT_UART_TEST_FAIL_STEP']=kind
 if kind=='write-deadline':env['BT_UART_TEST_FAIL_STEP']='patch-timeout'
 if kind in ('pending-final03','pending-final2','vse-pending'):env['BT_UART_TEST_FAIL_STEP']='postfinal-pending'
 if kind.startswith('cleanup-'):env['BT_UART_TEST_CLEANUP_FAIL']=kind[8:]
 if kind=='final03-and-cleanup-set':env['BT_UART_TEST_CLEANUP_FAIL']='set'
 if kind=='version-incomplete':env['BT_UART_TEST_PARTIAL_VERSION']='1'
 if kind in ('early-exclusive','early-discipline','early-snapshot'):env['BT_UART_TEST_EARLY_GUARD']=kind
 proc=subprocess.Popen([str(BIN),'--test-fd',str(b.fileno()),str(FW),'100'],pass_fds=(b.fileno(),),stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env=env)
 b.close();th=threading.Thread(target=controller);th.start();out,err=proc.communicate(timeout=5);th.join(timeout=3)
 assert not errors,(kind,errors,out,err)
 good=kind in ('final2','intermediate03-final2','ibs','quiet-delayed03','version-changed','version-unchanged')
 assert proc.returncode==(3 if good else 1),(kind,proc.returncode,out,err)
 if kind not in ('early-exclusive','early-discipline','early-snapshot'):assert 'firmware_running=NOT_ESTABLISHED NVM_sent=0 reset_sent=0 scan_started=0' in out,(kind,out,err)
 if kind not in ('early-exclusive','early-discipline','early-snapshot') and kind not in ('pending-final03','pending-final2','vse-pending','cleanup-set','cleanup-get','cleanup-mismatch','final03-and-cleanup-set'):
  assert 'original_termios_restored=success' in err and 'normal_modem_restore_request=success' in err,(kind,err)
 if kind in ('pending-final03','pending-final2'):
  assert 'attribution_ambiguous=1' in out and 'hardware_drained_before_RX=0' in err and 'SKIPPED_hardware_empty_unproven' in err and 'parent_OFF_reboot_required=1' in err,(out,err)
 if good:
  assert len(segments)==629 and 'patch_bytes=152784' in out,(kind,out,err)
  if not kind.startswith('version-'):assert 'final_strict_completion=1' in out,(out,err)
  assert 'stage=pre-final OUTQ=0 TEMT=1' in err and 'stage=post-final OUTQ=0 TEMT=1' in err,(kind,err)
 if kind=='intermediate03-final2':assert 'intermediate_cc=628 intermediate_unknown03=628' in out,(out,err)
 if kind=='queued2-final2':assert 'attribution inconclusive' in err,(out,err)
 if kind in ('late03-final2','intermediate03-final03'):assert 'final response requires exact two-byte00:1e' in err,(out,err)
 if kind=='patch-timeout':assert 'patch final completion deadline' in err,(out,err)
 if kind=='patch-deadline':assert not segments,(out,err)
 if kind=='ibs':assert ibs_acks,(out,err)
 if kind=='partial-boundary':assert 'partial H4 at quiet boundary' in err,(out,err)
 if kind=='write-deadline':assert 'write deadline/signal' in err,(out,err)
 assert 'event queue overflow' not in err,(kind,out,err)
 if good:
  import re
  match=re.search(r'PATCH_QUIET quiet_ms=(\d+) total_ms=(\d+)',err)
  assert match and int(match[1])>=100 and int(match[2])<=1000,(out,err)
 if kind=='quiet-delayed03':assert 'events_during_quiet=1' in err and 'intermediate_cc=1' in err,(out,err)
 if kind=='quiet-flood':assert 'quiet boundary deadline' in err and len(segments)==628,(out,err)
 if kind.startswith('cleanup-'):assert 'parent_OFF_reboot_required=1' in err,(out,err)
 if kind=='final03-and-cleanup-set':assert '(first: final response requires exact two-byte00:1e)' in err,(out,err)
 if kind in ('early-exclusive','early-discipline','early-snapshot'):
  assert not seen and 'no_flush_or_reconfiguration=1' in err and 'TCIOFLUSH_errno' not in err and 'CLEANUP_TERMIOS' not in err,(kind,out,err)
 if kind in ('final-vendor-after628','final-hwe-after628','early-vendor'):
  import re
  raw=re.search(r'PATCH_RAW_EVENT .*monotonic_ms=(\d+).*hex=([0-9a-f]+)',err)
  assert raw and int(raw[1])>0,(kind,out,err)
  expected={'final-vendor-after628':'04ff050004000000','final-hwe-after628':'04100103','early-vendor':'04ff020004'}[kind]
  assert raw[2]==expected,(kind,out,err)
  if kind!='early-vendor':
   assert 'intermediate_cc=628 intermediate_unknown03=628' in out,(out,err)
   assert err.index('RX_META sample=24')<err.index('PATCH_RAW_EVENT'),err
   assert 'final_rx_ms=0 ' not in err,(out,err)
  assert 'final_strict_completion=0' in out,(out,err)
 if kind.startswith('version-') and kind!='version-incomplete':
  assert len(seen)==632 and 'version_query_sent=1' in out and 'final_strict_completion=0' in out,(kind,out,err)
 if kind=='version-changed':assert 'version_reply_valid=1 reported_patch=0x8674 patch_field_changed=1' in out,(out,err)
 if kind=='version-unchanged':assert 'version_reply_valid=1 reported_patch=0x38e6 patch_field_changed=0' in out,(out,err)
 if kind in ('version-stale-cc','version-unknown-vse','version-identity','version-status'):assert 'shape/identity mismatch or stale completion' in err,(out,err)
 if kind in ('unknown-vse-trailer','unknown-vse-status','vse-pending'):assert len(seen)==631 and 'version_query_sent=0' in out,(out,err)
 if kind=='vse-pending':assert 'observed VSE before final hardware boundary' in err and 'attribution_ambiguous=1' in out and 'parent_OFF_reboot_required=1' in err,(out,err)
 if kind=='version-incomplete':assert len(seen)==631 and partial_query==[b'\x01'] and 'version_query_sent=0' in out and 'version event before complete query submission' in err and 'attribution_ambiguous=1' in out,(out,err)
 print('PASS patch-version',kind)
