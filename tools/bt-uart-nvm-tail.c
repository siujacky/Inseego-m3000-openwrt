/* One fixed NVM fragment then bounded response-tail observation only.
 * Requires reported patch8674. No acceptance, segment2/reset/scan.
 */
#define BT_UART_FIRST_SEGMENT 1
/* SPDX-License-Identifier: GPL-2.0-only */
/* M3100 opt-in RAM-only WCN21 loader and bounded passive LE receive test.
 * No advertising, active scanning, connections, pairing or address assignment.
 * Generic NVM tag2 is preserved. This is NOT normal Linux Bluetooth support.
 */
#define _DEFAULT_SOURCE
#define _POSIX_C_SOURCE 200809L
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <linux/serial.h>
#include <poll.h>
#include <signal.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/stat.h>
#include <sys/utsname.h>
#include <termios.h>
#include <time.h>
#include <unistd.h>

static volatile sig_atomic_t stopped;
static void stop_now(int sig) { (void)sig; stopped=1; }
static int64_t now_ms(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC,&t); return (int64_t)t.tv_sec*1000+t.tv_nsec/1000000; }
static unsigned le16(const uint8_t *p) { return p[0]|(unsigned)p[1]<<8; }
static uint32_t le32(const uint8_t *p) { return (uint32_t)p[0]|(uint32_t)p[1]<<8|(uint32_t)p[2]<<16|(uint32_t)p[3]<<24; }

/* SHA256 over fixed-size input, checked before the UART is opened. */
static uint32_t rr(uint32_t x,unsigned n) { return (x>>n)|(x<<(32-n)); }
static void sha256(const uint8_t *p,size_t n,char hex[65])
{
 static const uint32_t k[64]={
  0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,
  0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,
  0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
  0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,
  0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,
  0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
  0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,
  0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2};
 uint32_t h[8]={0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19};
 size_t padded=(n+9+63)&~(size_t)63;
 for(size_t off=0;off<padded;off+=64) {
  uint32_t w[64]; uint8_t block[64]={0};
  for(unsigned j=0;j<64;j++) {
   size_t at=off+j;
   if(at<n) block[j]=p[at]; else if(at==n) block[j]=0x80;
   if(at>=padded-8) block[j]=(uint8_t)(((uint64_t)n*8)>>(8*(padded-1-at)));
  }
  for(unsigned j=0;j<16;j++) w[j]=(uint32_t)block[4*j]<<24|(uint32_t)block[4*j+1]<<16|(uint32_t)block[4*j+2]<<8|block[4*j+3];
  for(unsigned j=16;j<64;j++) w[j]=w[j-16]+(rr(w[j-15],7)^rr(w[j-15],18)^(w[j-15]>>3))+w[j-7]+(rr(w[j-2],17)^rr(w[j-2],19)^(w[j-2]>>10));
  uint32_t a=h[0],b=h[1],c=h[2],d=h[3],e=h[4],f=h[5],g=h[6],z=h[7];
  for(unsigned j=0;j<64;j++) {
   uint32_t t=z+(rr(e,6)^rr(e,11)^rr(e,25))+((e&f)^(~e&g))+k[j]+w[j];
   uint32_t u=(rr(a,2)^rr(a,13)^rr(a,22))+((a&b)^(a&c)^(b&c));
   z=g;g=f;f=e;e=d+t;d=c;c=b;b=a;a=t+u;
  }
  h[0]+=a;h[1]+=b;h[2]+=c;h[3]+=d;h[4]+=e;h[5]+=f;h[6]+=g;h[7]+=z;
 }
 for(unsigned j=0;j<8;j++) snprintf(hex+8*j,9,"%08x",h[j]);
}
struct firmware { uint8_t *patch,*nvm; size_t patch_n,nvm_n; };
static int file_load(const char *dir,const char *name,size_t wanted,const char *hash,uint8_t **out)
{
 char path[PATH_MAX],actual[65]; struct stat st; size_t got=0; int fd;
 if(snprintf(path,sizeof(path),"%s/%s",dir,name)>=(int)sizeof(path)) return -1;
 fd=open(path,O_RDONLY|O_CLOEXEC|O_NOFOLLOW); if(fd<0) return -1;
 if(fstat(fd,&st)||!S_ISREG(st.st_mode)||st.st_size!=(off_t)wanted) {close(fd);return -1;}
 *out=malloc(wanted); if(!*out) {close(fd);return -1;}
 while(got<wanted) {ssize_t r=read(fd,*out+got,wanted-got);if(r<0&&errno==EINTR) continue;if(r<=0){close(fd);return -1;}got+=(size_t)r;}
 close(fd);sha256(*out,wanted,actual);
 if(strcmp(actual,hash)) {fprintf(stderr,"firmware hash mismatch: %s\n",name);return -1;}
 return 0;
}
static int firmware_load(const char *dir,struct firmware *f)
{
 f->patch_n=152784;f->nvm_n=6450;
 if(file_load(dir,"wcnhpbtfw21.tlv",f->patch_n,"77d8979da5c613c85550549dcef8fb8ec6fe2e5576942855ea03179a11597c1f",&f->patch)||
    file_load(dir,"wcnhpnv21g.bin",f->nvm_n,"75229ef38873a51f711c2a38f3d6adcb80e8105e8e1d5e8570aea94b33441fe0",&f->nvm)) return -1;
 if((le32(f->patch)&255)!=1||(le32(f->patch)>>8)!=f->patch_n-4||f->patch[14]!=3||
    le16(f->patch+16)!=0x13||le16(f->patch+18)!=0x201||le16(f->patch+20)!=0x8674||
    (le32(f->nvm)&255)!=2||(le32(f->nvm)>>8)!=f->nvm_n-4) return -1;
 size_t off=4; unsigned addr=0,hci=0,sleep=0;
 while(off+12<=f->nvm_n) {
  unsigned id=le16(f->nvm+off),n=le16(f->nvm+off+2); uint8_t *p=f->nvm+off+12;
  if(n>f->nvm_n-off-12) return -1;
  if(id==2) {if(n!=6) return -1;addr++;} /* preserve original tag, never log it */
  if(id==17) {if(n<3) return -1;p[0]|=0x80;p[1]=0;hci++;}
  if(id==27) {if(n<1) return -1;p[0]|=1;sleep++;}
  off+=12+n;
 }
 return off==f->nvm_n&&addr==1&&hci==1&&sleep==1?0:-1;
}

struct packet { uint8_t p[258]; size_t n; };
struct transport {
 int fd; bool ibs,tx_awake,writing,ack_pending,cleanup;
 int timeout_ms,init_budget_ms; int64_t init_end;
 uint8_t frame[4101]; size_t have,want,header;
 struct packet queue[16]; unsigned head,count;
 unsigned reports,nonempty,events,ibs_wakes;
 unsigned rssi_samples;
 int rssi_min,rssi_max;
 const char *phase;
 unsigned segment,segment_total,rx_cc,rx_status,rx_vendor,rx_other,diag_samples;
 size_t submitted_bytes,wire_tx,wire_rx;
 struct serial_icounter_struct initial_counts;
 bool have_initial_counts;
 unsigned patch_stage,intermediate_cc,intermediate_03,intermediate_two,final_seen;
 bool final_submitted,attribution_ambiguous;
 int64_t patch_started,final_submit_ms,final_rx_ms;
 int64_t last_complete_ms,first_intermediate_ms,last_intermediate_ms;
 unsigned complete_events,raw_event_samples;
 bool final_hardware_drained,require_parent_reboot;
 bool observed_vse_gate,version_query_sent,version_reply_valid;
 unsigned reported_patch_version;
 bool tail_active,tail_submitted,tail_drained;
 unsigned tail_events,tail_cc,tail_vse,tail_other,tail_before_drain,tail_window_ms;
 size_t tail_wire_start;int64_t tail_started,tail_end;
 int original_modem;
 bool have_original_modem;
#ifdef BT_UART_HOST_TEST
 int mock_modem;
 bool mock_flow;
 bool setter_fault_once;
#endif
};
static const char *first_failure;
static int fail(const char *what) {
 if(!first_failure) {first_failure=what;fprintf(stderr,"failure: %s\n",what);}
 else fprintf(stderr,"additional failure: %s (first: %s)\n",what,first_failure);
 return -1;
}
static void diagnostic(struct transport *t,const char *reason)
{
 fprintf(stderr,"DIAG %s phase=%s segment=%u/%u file_bytes_submitted=%zu wire_tx=%zu wire_rx=%zu queued=%u rx_cc=%u rx_status=%u rx_vendor=%u rx_other=%u first_error=%s\n",
  reason,t->phase?t->phase:"preflight",t->segment,t->segment_total,t->submitted_bytes,t->wire_tx,t->wire_rx,t->count,
  t->rx_cc,t->rx_status,t->rx_vendor,t->rx_other,first_failure?first_failure:"none");
 int outq;
 if(ioctl(t->fd,TIOCOUTQ,&outq)==0)fprintf(stderr,"UART_META pending_kernel_tx=%d (not controller consumption proof)\n",outq);
 else fprintf(stderr,"UART_META pending_kernel_tx=unknown errno=%d\n",errno);
 struct serial_icounter_struct counts;
 if(ioctl(t->fd,TIOCGICOUNT,&counts)==0&&t->have_initial_counts)
  fprintf(stderr,"UART_META delta_rx=%u delta_tx=%u delta_frame=%u delta_overrun=%u delta_parity=%u delta_break=%u delta_buf_overrun=%u\n",
   (unsigned)counts.rx-(unsigned)t->initial_counts.rx,(unsigned)counts.tx-(unsigned)t->initial_counts.tx,
   (unsigned)counts.frame-(unsigned)t->initial_counts.frame,(unsigned)counts.overrun-(unsigned)t->initial_counts.overrun,
   (unsigned)counts.parity-(unsigned)t->initial_counts.parity,(unsigned)counts.brk-(unsigned)t->initial_counts.brk,
   (unsigned)counts.buf_overrun-(unsigned)t->initial_counts.buf_overrun);
 else fprintf(stderr,"UART_META counters=unknown\n");
}
static void sample_event(struct transport *t)
{
 uint8_t *p=t->frame;size_t n=t->have;
 if(p[1]==0x0e)t->rx_cc++;else if(p[1]==0x0f)t->rx_status++;else if(p[1]==0xff)t->rx_vendor++;else t->rx_other++;
 /* Only fixed protocol metadata; no addresses, names, version tuple or payload dumps. */
 if(t->diag_samples++>=24)return;
 fprintf(stderr,"RX_META sample=%u phase=%s segment=%u/%u event=0x%02x plen=%u",t->diag_samples,t->phase?t->phase:"preflight",t->segment,t->segment_total,p[1],p[2]);
 if(p[1]==0x0e&&n>=6) {
  fprintf(stderr," opcode=0x%04x credit=%u return_len=%zu",le16(p+4),p[3],n-6);
  if(le16(p+4)==0xfc00&&n>=8) {
   fprintf(stderr," return_prefix=%02x:%02x",p[6],p[7]);
   if(p[6]==0&&p[7]==0x1e&&n==9)fprintf(stderr," EDL_return_byte2=0x%02x",p[8]);
  }
  else if(n==7)fprintf(stderr," status=0x%02x",p[6]);
 } else if(p[1]==0x0f&&n==7)fprintf(stderr," opcode=0x%04x credit=%u status=0x%02x",le16(p+5),p[4],p[3]);
 else if(p[1]==0xff&&n>=5&&p[3]==0)fprintf(stderr," EDL_header=%02x:%02x",p[3],p[4]);
 fputc('\n',stderr);
}
/* HCI event maximum is255 payload +3 H4/header bytes. Full bounded frame. */
static void patch_raw_event(struct transport *t,const uint8_t *p,size_t n)
{
 bool version_phase=t->phase&&!strcmp(t->phase,"post-patch-version");
 if(!t->patch_stage&&!version_phase)return;
 bool known_intermediate=p[1]==0x0e&&n>=7&&p[3]&&
  ((le16(p+4)==0xfc00&&p[6]==0)||(le16(p+4)==0xfc48&&n==7&&p[6]==0));
 if(t->patch_stage!=2&&!version_phase&&known_intermediate)return;
 int64_t received=now_ms();
 if(t->patch_stage==2)t->final_rx_ms=received;
 if(t->raw_event_samples++>=8)return;
 size_t shown=n;if(shown>258)shown=258;
 fprintf(stderr,"PATCH_RAW_EVENT sample=%u stage=%u monotonic_ms=%lld elapsed_ms=%lld frame_len=%zu captured=%zu truncated=%u hardware_drained_before_RX=%u hex=",t->raw_event_samples,t->patch_stage,(long long)received,(long long)(t->patch_started?received-t->patch_started:0),n,shown,shown!=n,t->final_hardware_drained);
 for(size_t i=0;i<shown;i++)fprintf(stderr,"%02x",p[i]);
 fputc('\n',stderr);
}
static int tail_observe(struct transport *t,const uint8_t *p,size_t n)
{
 t->tail_events++;
 if(t->tail_events>16)return fail("NVM tail event-count bound");
 int64_t when=now_ms();
 fprintf(stderr,"NVM_TAIL_EVENT number=%u monotonic_ms=%lld elapsed_ms=%lld frame_len=%zu hardware_drained_before_RX=%u interpretation=NONE",t->tail_events,(long long)when,(long long)(when-t->tail_started),n,t->tail_drained);
 if(p[1]==0x3e)fprintf(stderr," event=0x3e payload=REDACTED_LE_identity\n");
 else {fprintf(stderr," hex=");for(size_t i=0;i<n&&i<258;i++)fprintf(stderr,"%02x",p[i]);fputc('\n',stderr);}
 if(!t->tail_submitted){t->attribution_ambiguous=true;return fail("NVM tail event before full fragment submission");}
 if(!t->tail_drained)t->tail_before_drain++;
 if(p[1]==0x0e) {
  t->tail_cc++;
  if(n<7||!p[3])return fail("malformed NVM tail CommandComplete");
  if(p[6])return fail("NVM tail nonzero generic HCI status");
 } else if(p[1]==0xff)t->tail_vse++;
 else t->tail_other++;
 return 0; /* Observed only; no accepted ACK or successful-NVM state exists. */
}
/* Stage1 observes upstream-unchecked mode3 returns without retaining a queue. */
static int patch_observe(struct transport *t,const uint8_t *p,size_t n)
{
 t->last_complete_ms=now_ms();t->complete_events++;
 static const uint8_t observed_vse[8]={4,0xff,5,0,0,0,0x13,1};
 if(t->patch_stage==2&&n==sizeof(observed_vse)&&!memcmp(p,observed_vse,sizeof(observed_vse))) {
  t->final_rx_ms=now_ms();t->final_seen++;
  fprintf(stderr,"PATCH_FINAL_META exact_observed_VSE=1 prefix_legacy_result=0 trailer=1301 trailer_meaning=UNKNOWN hardware_drained_before_RX=%u\n",t->final_hardware_drained);
  if(!t->final_submitted||!t->final_hardware_drained) {
   t->attribution_ambiguous=true;t->require_parent_reboot=true;return fail("observed VSE before final hardware boundary; attribution inconclusive");
  }
  if(t->final_seen!=1||t->intermediate_two) {
   t->attribution_ambiguous=true;return fail("observed VSE final attribution inconclusive");
  }
  t->observed_vse_gate=true;return 0; /* Gate ONE read query, not download-success proof. */
 }
 if(p[1]!=0x0e||n<7)return fail("patch unexpected event or malformed completion");
 unsigned op=le16(p+4);
 if(op==0xfc48) {
  if(n!=7||!p[3]||p[6])return fail("patch unexpected/error late baud completion");
  return 0;
 }
 if(op!=0xfc00||!p[3])return fail("patch unexpected opcode/zero command credit");
 if(p[6])return fail("patch nonzero generic HCI status");
 if(t->patch_stage==1) {
  t->intermediate_cc++;
  if(!t->first_intermediate_ms)t->first_intermediate_ms=t->last_complete_ms;
  t->last_intermediate_ms=t->last_complete_ms;
  if(n==9&&p[6]==0&&p[7]==0x1e&&p[8]==3)t->intermediate_03++;
  if(n==8&&p[7]==0x1e)t->intermediate_two++;
  return 0; /* Metadata observed; vendor byte2 is NOT interpreted as success. */
 }
 if(!t->final_submitted) {
  t->attribution_ambiguous=true;return fail("completion during final frame write; attribution inconclusive");
 }
 t->final_rx_ms=now_ms();t->final_seen++;
 fprintf(stderr,"PATCH_FINAL_META elapsed_ms=%lld return_len=%zu generic_status=0x%02x header_byte1=0x%02x vendor_byte2=0x%02x hardware_drained_before_RX=%u\n",(long long)(t->final_rx_ms-t->patch_started),n-6,p[6],n>7?p[7]:0,n>8?p[8]:0,t->final_hardware_drained);
 if(!t->final_hardware_drained) {
  t->attribution_ambiguous=true;t->require_parent_reboot=true;return fail("completion before proven final hardware drain; attribution inconclusive");
 }
 if(t->final_seen!=1)return fail("multiple final completions; attribution inconclusive");
 if(n!=8||p[7]!=0x1e)return fail("final response requires exact two-byte00:1e");
 if(t->intermediate_two) {
  t->attribution_ambiguous=true;return fail("earlier identical completion; final attribution inconclusive");
 }
 return 0;
}
static int pump(struct transport *t,int64_t end,bool output_ready);
static int write_bytes(struct transport *t,const uint8_t *p,size_t n,int64_t end)
{
 t->writing=true;
 while(n) {
  if((stopped&&!t->cleanup)||now_ms()>=end) {t->writing=false;return fail("write deadline/signal");}
  size_t request=n;
#ifdef BT_UART_HOST_TEST
  bool partial_version=t->phase&&!strcmp(t->phase,"post-patch-version")&&getenv("BT_UART_TEST_PARTIAL_VERSION");
  bool partial_tail=t->phase&&!strcmp(t->phase,"one-NVM-tail")&&getenv("BT_UART_TEST_PARTIAL_TAIL");
  if((partial_version||partial_tail)&&request>1)request=1;
#endif
  ssize_t r=write(t->fd,p,request);
  if(r>0) {
   p+=r;n-=(size_t)r;t->wire_tx+=(size_t)r;
#ifdef BT_UART_HOST_TEST
   if((partial_version||partial_tail)&&n&&pump(t,now_ms()+100,false)<0){t->writing=false;return -1;}
#endif
   continue;
  }
  if(r<0&&(errno==EAGAIN||errno==EINTR)) {if(pump(t,end,true)<0){t->writing=false;return -1;}continue;}
  t->writing=false;return fail("transport write");
 }
 t->writing=false;
 if(t->ack_pending) {const uint8_t ack=0xfc;t->ack_pending=false;return write_bytes(t,&ack,1,end);}
 return 0;
}
static int incoming(struct transport *t,const uint8_t *p,size_t n)
{
 for(size_t i=0;i<n;i++) {
  uint8_t b=p[i];
  if(!t->have&&(b==0xfd||b==0xfc||b==0xfe)) {
   if(b==0xfd) {
    t->ibs_wakes++;
    /* Never recurse into RX halfway through this input buffer or insert
     * an IBS byte into a partially written outbound H4 command. */
    t->ack_pending=true;
   } else if(b==0xfc) t->tx_awake=true;
   continue; /* controller TX sleep does not mean host TX is asleep */
  }
  if(!t->have) {
   if(b!=4&&b!=2&&b!=3&&b!=5) return fail("unknown H4 packet type");
   t->header=b==4?3:(b==3?4:5);t->want=t->header;
  }
  if(t->have>=sizeof(t->frame)) return fail("H4 frame overflow");
  t->frame[t->have++]=b;
  if(t->have==t->header) {
   size_t payload=t->frame[0]==4?t->frame[2]:(t->frame[0]==3?t->frame[3]:le16(t->frame+3));
   if(t->frame[0]==5) payload&=0x3fff;
   t->want=t->header+payload;
   if(t->want>sizeof(t->frame)) return fail("oversized H4 frame");
  }
  if(t->have==t->want) {
   if(t->frame[0]==4) {
    sample_event(t);
    patch_raw_event(t,t->frame,t->have);
    if(t->phase&&!strcmp(t->phase,"patch")&&t->segment<t->segment_total&&
       t->frame[1]==0x0e&&t->have>=6&&le16(t->frame+4)==0xfc00) {
     fail("unexpected intermediate patch completion");diagnostic(t,"patch-response-rejected");return -1;
    }
    if(t->tail_active&&t->frame[1]==0x10) {
     if(tail_observe(t,t->frame,t->have))return -1;
    }
    if(t->frame[1]==0x10) {
     if(t->have==4&&t->frame[2]==1)fprintf(stderr,"HCI Hardware Error code=0x%02x\n",t->frame[3]);
     t->have=t->want=t->header=0;return fail("controller Hardware Error");
    }
    if(t->phase&&!strcmp(t->phase,"post-patch-version")&&!t->version_query_sent) {
     t->attribution_ambiguous=true;t->have=t->want=t->header=0;
     return fail("version event before complete query submission; attribution inconclusive");
    }
    if(t->tail_active) {
     int observed=tail_observe(t,t->frame,t->have);t->have=t->want=t->header=0;
     if(observed)return -1;
     continue;
    }
    if(t->patch_stage) {
     int result=patch_observe(t,t->frame,t->have);
     t->have=t->want=t->header=0;
     if(result)return -1;
     continue;
    }
    if(t->count==16) {fail("event queue overflow");diagnostic(t,"queue-overflow");return -1;}
    struct packet *q=&t->queue[(t->head+t->count)%16];q->n=t->have;memcpy(q->p,t->frame,q->n);t->count++;
   }
   if((t->patch_stage||t->tail_active||(t->phase&&!strcmp(t->phase,"post-patch-version")))&&t->frame[0]!=4)return fail("patch unexpected non-event H4 packet");
   t->have=t->want=t->header=0;
  }
 }
 return 0;
}
static int pump(struct transport *t,int64_t end,bool output_ready)
{
 struct pollfd p={t->fd,POLLIN|(output_ready?POLLOUT:0),0};
 int64_t left=end-now_ms(); if(left<=0||(stopped&&!t->cleanup)) return 0;
 if(left>100) left=100; /* bounded signal responsiveness */
 int r=poll(&p,1,(int)left);
 if(r<0) return errno==EINTR?0:fail("poll");
 if(!r) return 0;
 if(p.revents&POLLIN) {
  uint8_t b[1024];ssize_t n=read(t->fd,b,sizeof(b));
  if(n>0) {
   t->wire_rx+=(size_t)n;
   if(incoming(t,b,(size_t)n))return -1;
   if(t->ack_pending&&!t->writing) {const uint8_t ack=0xfc;t->ack_pending=false;return write_bytes(t,&ack,1,end);}
   return 0;
  }
  if(n<0&&(errno==EINTR||errno==EAGAIN)) return 0;
  return fail("transport EOF/read");
 }
 if(p.revents&(POLLERR|POLLHUP|POLLNVAL)) return fail("transport hangup");
 return 0;
}
static int event(struct transport *t,struct packet *q,int64_t end)
{
 while(!t->count) {
  if(now_ms()>=end||(stopped&&!t->cleanup)) return 0;
  if(pump(t,end,false)) return -1;
 }
 *q=t->queue[t->head];t->head=(t->head+1)%16;t->count--;return 1;
}
/* Validate entire legacy Advertising Report event before incrementing counters. */
static int adv(struct transport *t,const struct packet *q,bool count)
{
 const uint8_t *p=q->p;size_t n=q->n,off=5;
 if(n<3||p[0]!=4||n!=(size_t)p[2]+3) return fail("bad event length");
 if(p[1]!=0x3e) return 0;
 if(n<4) return fail("bad LE meta event");
 if(p[3]!=2) return 0;
 if(n<5||!p[4]) return fail("empty Advertising Report event");
 for(unsigned i=0;i<p[4];i++) {
  if(off+10>n||p[off]>4||p[off+1]>3||p[off+8]>31||off+10+p[off+8]>n) return fail("malformed Advertising Report");
  off+=10+p[off+8];
 }
 if(off!=n) return fail("trailing Advertising Report bytes");
 if(count) {
  t->events++;off=5;
  for(unsigned i=0;i<p[4];i++) {
   unsigned len=p[off+8];int rssi=(int8_t)p[off+9+len];t->reports++;if(len)t->nonempty++;
   if(rssi!=127) {
    if(!t->rssi_samples||rssi<t->rssi_min)t->rssi_min=rssi;
    if(!t->rssi_samples||rssi>t->rssi_max)t->rssi_max=rssi;
    t->rssi_samples++;
   }
   off+=10+len;
  }
 }
 return 0;
}
static int wake(struct transport *t,int64_t end)
{
 if(!t->ibs||t->tx_awake) return 0;
 for(unsigned attempts=0;attempts<5&&!t->tx_awake;attempts++) {
  const uint8_t b=0xfd;
  if(write_bytes(t,&b,1,end)) return -1;
  int64_t until=now_ms()+100;if(until>end) until=end;
  while(!t->tx_awake&&now_ms()<until) if(pump(t,until,false)) return -1;
 }
 return t->tx_awake?0:fail("IBS wake timeout");
}
static int send_cmd(struct transport *t,unsigned op,const uint8_t *data,size_t n,int64_t end)
{
 uint8_t tx[259]={1,op&255,op>>8,(uint8_t)n};
 if(n>255||wake(t,end)) return -1;
 if(n) memcpy(tx+4,data,n);
 return write_bytes(t,tx,n+4,end);
}
/* expected EDL return body has no extra legacy result byte on WCN6855. */
static int wait_cc(struct transport *t,unsigned op,const uint8_t *expected,size_t expected_n,int64_t end,struct packet *reply)
{
 for(;;) {
  struct packet q;int r=event(t,&q,end);
  if(r<=0) return fail(r?"event transport":"command deadline/signal");
  if(adv(t,&q,false)) return -1;
  if(q.p[1]==0x0f) {
   if(q.n!=7) return fail("malformed CommandStatus");
   if(le16(q.p+5)==op&&q.p[3]) return fail("CommandStatus error");
  }
  if(q.p[1]!=0x0e) continue;
  if(q.n<6) return fail("malformed CommandComplete");
  if(le16(q.p+4)!=op) return fail("unexpected CommandComplete opcode");
  if(q.p[3]==0) return fail("zero command credit");
  if(expected&&(q.n!=expected_n+6||memcmp(q.p+6,expected,expected_n))) return fail("CommandComplete return mismatch");
  if(reply) *reply=q;
  return 0;
 }
}
static int cmd(struct transport *t,unsigned op,const uint8_t *data,size_t n,const uint8_t *expected,size_t expected_n)
{
 if(op==0xfc17)t->phase="disable-logging";
 else if(op==0x0c03)t->phase="HCI-reset";
 int64_t end=now_ms()+t->timeout_ms;
 if(!t->ibs&&t->init_end<end)end=t->init_end;
 return send_cmd(t,op,data,n,end)||wait_cc(t,op,expected,expected_n,end,NULL)?-1:0;
}
static int download(struct transport *t,const uint8_t *p,size_t n,bool patch)
{
 size_t off=0; unsigned segments=0; static const uint8_t good[2]={0,0x1e};
 t->phase=patch?"patch":"NVM";t->segment=0;t->segment_total=(unsigned)((n+242)/243);t->submitted_bytes=0;
 while(off<n) {
  size_t len=n-off;if(len>243)len=243;
  uint8_t payload[245]={0x1e,(uint8_t)len};memcpy(payload+2,p+off,len);
  t->segment=segments+1;
  if(send_cmd(t,0xfc00,payload,len+2,t->init_end)) return -1;
  off+=len;segments++;
  t->submitted_bytes=off;
  /* Read already-available responses every segment rather than waiting for
   * a full kernel TX queue. Mode3 does not authorize ignoring error events. */
  struct pollfd ready={t->fd,POLLIN,0};int available=poll(&ready,1,0);
  if(available<0&&errno!=EINTR)return fail("download readiness poll");
  if(available>0&&(ready.revents&POLLIN)&&pump(t,t->init_end,false))return -1;
  if(!patch||off==n) {
   int64_t end=now_ms()+t->timeout_ms;if(end>t->init_end)end=t->init_end;
   if(wait_cc(t,0xfc00,good,sizeof(good),end,NULL)) return -1;
  }
 }
 printf("%s download acknowledged segments=%u\n",patch?"patch":"NVM",segments);fflush(stdout);
 return 0;
}
static int identify_rom(struct transport *t)
{
 const uint8_t version=0x19;struct packet reply={0};
 t->phase="ROM-version";
 int64_t end=now_ms()+t->timeout_ms;
 if(send_cmd(t,0xfc00,&version,1,end)||wait_cc(t,0xfc00,NULL,0,end,&reply))return -1;
 if(reply.n!=21||reply.p[6]!=0||reply.p[7]!=0x19||reply.p[8]!=12||le32(reply.p+9)!=0x13||
    le16(reply.p+13)!=0x38e6||le16(reply.p+15)!=0x201||le32(reply.p+17)!=0x400c1211) return fail("ROM tuple mismatch (fresh power cycle required)");
 puts("ROM tuple verified product13 ROM0201 SOC400c1211");fflush(stdout);return 0;
}
static int initialize(struct transport *t,const struct firmware *f)
{
 const uint8_t logging[2]={0x14,0},success=0;
 if(identify_rom(t))return -1;
 if(download(t,f->patch,f->patch_n,true))return -1;
 struct timespec gap={0,10000000};while(nanosleep(&gap,&gap)&&errno==EINTR)if(stopped)return -1;
 if(download(t,f->nvm,f->nvm_n,false)||cmd(t,0xfc17,logging,2,&success,1)||cmd(t,0x0c03,NULL,0,&success,1))return -1;
 t->ibs=true;t->tx_awake=false;
 puts("firmware/NVM and HCI reset acknowledged; IBS enabled");fflush(stdout);return 0;
}
#ifdef BT_UART_FIRST_SEGMENT
static bool host_fault(const char *step)
{
#ifdef BT_UART_HOST_TEST
 const char *value=getenv("BT_UART_TEST_FAIL_STEP");return value&&!strcmp(value,step);
#else
 (void)step;return false;
#endif
}
static int snapshot_modem(struct transport *t)
{
#ifdef BT_UART_HOST_TEST
 t->mock_modem=TIOCM_DTR|TIOCM_RTS;t->mock_flow=true;t->original_modem=t->mock_modem;
#else
 if(ioctl(t->fd,TIOCMGET,&t->original_modem))return fail("snapshot modem control");
#endif
 t->have_original_modem=true;return 0;
}
/* On this exact driver get_mctrl ORs RTS high unconditionally. This API
 * reports a driver mask, not the physical RTS pin or the core output cache. */
static int reported_modem(struct transport *t,int *value)
{
#ifdef BT_UART_HOST_TEST
 *value=t->mock_modem;
 if(getenv("BT_UART_TEST_FORCED_RTS"))*value|=TIOCM_RTS;
 return 0;
#else
 return ioctl(t->fd,TIOCMGET,value);
#endif
}
static int modem_request(struct transport *t,int value)
{
#ifdef BT_UART_HOST_TEST
 if(host_fault("setter")&&!t->setter_fault_once) {t->setter_fault_once=true;return -1;}
 t->mock_modem=value;return 0;
#else
 return ioctl(t->fd,TIOCMSET,&value);
#endif
}
static int restore_original_modem(struct transport *t)
{
 if(!t->have_original_modem)return 0;
 int normal=t->original_modem|TIOCM_RTS,reported;
 if(modem_request(t,normal))return fail("normal modem restoration request failed");
 if(reported_modem(t,&reported))return fail("read driver modem report after restoration");
 fprintf(stderr,"PREBAUD_META normal_modem_restore_request=success requested=0x%x reported=0x%x RTS_physical=unknown\n",normal,reported);return 0;
}
/* Match the serdev WCN6855 branch: flow flag first, then RTS. Preserve
 * other modem bits; exit verifies termios and only requests normal RTS. */
static int set_runtime_flow(struct transport *t,bool on)
{
 if(host_fault(on?"flowon":"flowoff"))return fail(on?"restore runtime flow":"disable runtime flow");
 int modem,reported;bool actual_flow;
 if(reported_modem(t,&modem))return fail("read pre-baud driver modem report");
#ifdef BT_UART_HOST_TEST
 if(host_fault("termios"))return fail("set pre-baud flow termios");
 t->mock_flow=on;
#else
 struct termios value;
 if(tcgetattr(t->fd,&value))return fail("get pre-baud flow termios");
 if(on)value.c_cflag|=CRTSCTS;else value.c_cflag&=~CRTSCTS;
 if(tcsetattr(t->fd,TCSANOW,&value))return fail("set pre-baud flow termios");
#endif
 if(on)modem|=TIOCM_RTS;else modem&=~TIOCM_RTS;
 if(modem_request(t,modem))return fail("pre-baud RTS request failed");
#ifdef BT_UART_HOST_TEST
 actual_flow=host_fault("readback")?!t->mock_flow:t->mock_flow;
#else
 if(tcgetattr(t->fd,&value))return fail("read back pre-baud flow termios");
 actual_flow=!!(value.c_cflag&CRTSCTS);
#endif
 if(reported_modem(t,&reported))return fail("read back driver modem report");
 fprintf(stderr,"PREBAUD_META wanted_flow=%u actual_flow=%u wanted_RTS=%u reported_RTS=%u requested_mctrl=0x%x reported_mctrl=0x%x RTS_observability=driver-forced-high RTS_physical=unknown\n",on,actual_flow,on,!!(reported&TIOCM_RTS),modem,reported);
 if(actual_flow!=on)return fail("verify pre-baud CRTSCTS termios");
 return 0;
}
static int prebaud_drain(struct transport *t,int64_t end)
{
 if(host_fault("drain"))return fail("pre-baud drain deadline");
#ifdef BT_UART_HOST_TEST
 (void)t;(void)end;return 0;
#else
 while(now_ms()<end&&!stopped) {
  int pending,lsr;
  if(ioctl(t->fd,TIOCOUTQ,&pending))return fail("pre-baud TIOCOUTQ unavailable");
  if(pending==0) {
   if(ioctl(t->fd,TIOCSERGETLSR,&lsr)==0) {
    if(lsr&TIOCSER_TEMT)return 0;
   } else {
    /* Kernel queue empty may precede shift-register empty. Five bytes
     * take0.44ms at115200; allow10ms without an unbounded tcdrain. */
    int64_t guard=now_ms()+10;if(guard>end)guard=end;
    while(now_ms()<guard&&!stopped)if(pump(t,guard,false))return -1;
    return stopped?fail("pre-baud drain signal"):0;
   }
  }
  int64_t tick=now_ms()+2;if(tick>end)tick=end;
  if(pump(t,tick,false))return -1;
 }
 return fail("pre-baud drain deadline/signal");
#endif
}
static int host_115200(struct transport *t)
{
 if(host_fault("hostbaud"))return fail("restore host baud115200");
#ifndef BT_UART_HOST_TEST
 struct termios value;
 if(tcgetattr(t->fd,&value))return fail("read host baud termios");
 cfsetispeed(&value,B115200);cfsetospeed(&value,B115200);
 if(tcsetattr(t->fd,TCSANOW,&value)||tcgetattr(t->fd,&value)||
    cfgetispeed(&value)!=B115200||cfgetospeed(&value)!=B115200)return fail("verify host115200 restore");
#else
 (void)t;
#endif
 fprintf(stderr,"PREBAUD_META host_baud=115200\n");return 0;
}
static int baud_response(const struct packet *q)
{
 if(q->p[1]==0x0e&&q->n==7&&le16(q->p+4)==0xfc48&&q->p[3]&&q->p[6]==0) {
  fprintf(stderr,"PREBAUD_META optional_fc48_CommandComplete=success\n");return 0;
 }
 return fail("unexpected/error pre-baud response");
}
static int linux_prebaud(struct transport *t)
{
 const uint8_t zero=0;int result=-1;
 t->phase="Linux-prebaud";t->segment=t->segment_total=0;
 if(set_runtime_flow(t,false))goto restore;
 int64_t end=now_ms()+100;
 if(send_cmd(t,0xfc48,&zero,1,end)||prebaud_drain(t,end))goto restore;
 /* Exact upper edge of source usleep_range(1000,10000). */
 int64_t settle=now_ms()+10;
 while(now_ms()<settle&&!stopped)if(pump(t,settle,false))goto restore;
 if(stopped){fail("pre-baud signal");goto restore;}
 if(host_115200(t))goto restore;
 result=0;
restore:
 if(set_runtime_flow(t,true))result=-1;
 if(result)return -1;
 /* Source does not require an ACK on WCN6855. Already-arrived responses
  * must nevertheless be validated; never feed stale FC48 into TLV proof. */
 struct pollfd ready={t->fd,POLLIN,0};int n=poll(&ready,1,0);
 if(n<0&&errno!=EINTR)return fail("pre-baud response readiness");
 if(n>0&&(ready.revents&POLLIN)&&pump(t,now_ms()+20,false))return -1;
 while(t->count) {
  struct packet q=t->queue[t->head];t->head=(t->head+1)%16;t->count--;
  if(baud_response(&q))return -1;
 }
 fprintf(stderr,"PREBAUD_META source_sequence_complete=1 (download unlock NOT established)\n");return 0;
}
static int patch_rx_available(struct transport *t,int64_t end)
{
 while(!stopped&&now_ms()<end) {
  struct pollfd p={t->fd,POLLIN,0};int r=poll(&p,1,0);
  if(r<0){if(errno==EINTR)continue;return fail("patch readiness poll");}
  if(r>0&&(p.revents&(POLLERR|POLLHUP|POLLNVAL)))return fail("patch transport hangup");
  if(!r||!(p.revents&POLLIN))return 0;
  if(pump(t,end,false))return -1;
 }
 return fail(stopped?"patch RX signal":"patch whole-download deadline");
}
static int patch_drain(struct transport *t,int64_t end,const char *stage)
{
 unsigned checks=0;
 while(now_ms()<end&&!stopped) {
  int pending,lsr;
#ifdef BT_UART_HOST_TEST
  if(host_fault("patch-outq"))return fail("patch TIOCOUTQ unavailable");
  if(host_fault("patch-lsr"))return fail("patch TIOCSERGETLSR unavailable");
  if(ioctl(t->fd,TIOCOUTQ,&pending))return fail("host socket OUTQ emulation");
  if(host_fault("patch-stall")||(host_fault("postfinal-pending")&&t->patch_stage==2))pending=1;
  lsr=TIOCSER_TEMT;
#else
  if(ioctl(t->fd,TIOCOUTQ,&pending))return fail("patch TIOCOUTQ unavailable");
  if(ioctl(t->fd,TIOCSERGETLSR,&lsr))return fail("patch TIOCSERGETLSR unavailable");
#endif
  checks++;
  if(t->patch_stage==2)t->final_hardware_drained=pending==0&&(lsr&TIOCSER_TEMT);
  if(t->tail_active)t->tail_drained=pending==0&&(lsr&TIOCSER_TEMT);
  if(patch_rx_available(t,end))return -1;
  if(!pending&&(lsr&TIOCSER_TEMT)) {
   fprintf(stderr,"PATCH_DRAIN stage=%s OUTQ=0 TEMT=1 successful_ioctl_pairs=%u elapsed_ms=%lld\n",stage,checks,(long long)(now_ms()-t->patch_started));return 0;
  }
  /* Wake on RX, then re-sample OUTQ/TEMT before consuming that RX. */
  struct pollfd ready={t->fd,POLLIN,0};int delay=(int)(end-now_ms());if(delay>2)delay=2;if(delay<0)delay=0;
  int polled=poll(&ready,1,delay);
  if(polled<0&&errno!=EINTR)return fail("patch drain readiness poll");
 }
 return fail(stopped?"patch drain signal":"patch whole-download deadline");
}
static int patch_quiet_boundary(struct transport *t,int64_t whole_end)
{
 int64_t start=now_ms(),end=start+1000,last=start;
 unsigned before=t->complete_events;
 if(end>whole_end)end=whole_end;
 while(!stopped&&now_ms()<end) {
  if(patch_rx_available(t,end))return -1;
  if(t->have) {
   t->attribution_ambiguous=true;return fail("partial H4 at quiet boundary; attribution inconclusive");
  }
  if(t->last_complete_ms>last)last=t->last_complete_ms;
  if(now_ms()-last>=100) {
   fprintf(stderr,"PATCH_QUIET quiet_ms=%lld total_ms=%lld events_during_quiet=%u intermediate_cc=%u first_event_ms=%lld last_event_ms=%lld\n",(long long)(now_ms()-last),(long long)(now_ms()-start),t->complete_events-before,t->intermediate_cc,(long long)t->first_intermediate_ms,(long long)t->last_intermediate_ms);
   return 0;
  }
  int64_t tick=last+100;if(tick>end)tick=end;
  if(pump(t,tick,false))return -1;
 }
 t->attribution_ambiguous=true;
 return fail(stopped?"quiet boundary signal":"quiet boundary deadline; attribution inconclusive");
}
static int post_patch_version(struct transport *t,int64_t whole_end)
{
 if(t->count||t->have)return fail("unconsumed event at version-query boundary");
 t->patch_stage=0;t->phase="post-patch-version";
 int64_t end=now_ms()+3000;if(end>whole_end)end=whole_end;
 const uint8_t version=0x19;struct packet reply={0};
 if(send_cmd(t,0xfc00,&version,1,end))return -1;
 t->version_query_sent=true;
 int r=event(t,&reply,end);
 if(r<0)return -1;
 if(!r)return fail("post-patch version reply deadline");
 /* Do not use wait_cc: it skips VSEs. Exactly one expected frame is allowed. */
 if(reply.n!=21||reply.p[0]!=4||reply.p[1]!=0x0e||reply.p[2]!=18||!reply.p[3]||le16(reply.p+4)!=0xfc00||reply.p[6]!=0||reply.p[7]!=0x19||reply.p[8]!=12||le32(reply.p+9)!=0x13||le16(reply.p+15)!=0x201||le32(reply.p+17)!=0x400c1211)return fail("post-patch version shape/identity mismatch or stale completion");
 t->reported_patch_version=le16(reply.p+13);
 fprintf(stderr,"VERSION_OBSERVATION patch_before=0x38e6 patch_after=0x%04x expected_file_patch=0x8674 product_ROM_SOC=matched reported_field_only=1\n",t->reported_patch_version);
 if(t->reported_patch_version!=0x8674&&t->reported_patch_version!=0x38e6)return fail("unexpected reported patch version");
 t->version_reply_valid=true;
 if(patch_rx_available(t,end))return -1;
 if(t->count||t->have)return fail("extra/partial event after version reply");
 return 0;
}
static int patch_only(struct transport *t,const struct firmware *f)
{
 int result=1;size_t off=0;unsigned sent=0;
 if(identify_rom(t)||linux_prebaud(t))goto done;
 t->patch_started=now_ms();
 int64_t end=t->patch_started+60000;
#ifdef BT_UART_HOST_TEST
 if(host_fault("patch-deadline"))end=t->patch_started;
 if(host_fault("patch-timeout")||host_fault("patch-stall"))end=t->patch_started+300;
#endif
 t->init_end=end;t->patch_stage=1;t->phase="patch-only-mode3";
 t->segment_total=(unsigned)((f->patch_n+242)/243);
 /* Never credit an existing queued completion as final proof. */
 while(t->count) {
  struct packet *q=&t->queue[t->head];
  if(patch_observe(t,q->p,q->n))goto done;
  t->head=(t->head+1)%16;t->count--;
 }
 while(off<f->patch_n) {
  if(stopped||now_ms()>=end){fail(stopped?"patch TX signal":"patch whole-download deadline");goto done;}
  size_t n=f->patch_n-off;if(n>243)n=243;
  bool final=off+n==f->patch_n;
  t->segment=sent+1;t->submitted_bytes=off;
  if(final) {
   /* Drain all earlier output and classify currently available RX while it
    * is still intermediate; there is no segment ID in an FC00 response. */
   if(patch_drain(t,end,"pre-final")||patch_quiet_boundary(t,end))goto done;
   if(t->have){fail("partial event at final boundary; attribution inconclusive");t->attribution_ambiguous=true;goto done;}
   t->patch_stage=2;t->phase="patch-only-final";
  }
  uint8_t payload[245]={0x1e,(uint8_t)n};memcpy(payload+2,f->patch+off,n);
  if(send_cmd(t,0xfc00,payload,n+2,end))goto done;
  off+=n;sent++;t->submitted_bytes=off;
  if(final) {t->final_submitted=true;t->final_submit_ms=now_ms();}
  if(!final&&patch_rx_available(t,end))goto done;
 }
 if(patch_drain(t,end,"post-final"))goto done;
 while(!t->final_seen&&!stopped&&now_ms()<end)if(pump(t,end,false))goto done;
 if(stopped){fail("patch final signal");goto done;}
 if(!t->final_seen){fail("patch final completion deadline");goto done;}
 /* Inspect already available extra completions; never hunt past a mismatch. */
 if(patch_rx_available(t,end))goto done;
 if(t->observed_vse_gate&&post_patch_version(t,end))goto done;
 result=3; /* strict completion candidate only, never firmware-running proof */
done:
 printf("diagnostic_only=1 patch_packets=%u patch_bytes=%zu intermediate_cc=%u intermediate_unknown03=%u intermediate_exact2=%u final_strict_completion=%u observed_VSE_gate=%u version_query_sent=%u version_reply_valid=%u reported_patch=0x%04x patch_field_changed=%u attribution_ambiguous=%u attribution_proven=0 firmware_running=NOT_ESTABLISHED NVM_sent=0 reset_sent=0 scan_started=0 result=%d\n",sent,off,t->intermediate_cc,t->intermediate_03,t->intermediate_two,result==3&&!t->observed_vse_gate,t->observed_vse_gate,t->version_query_sent,t->version_reply_valid,t->reported_patch_version,t->version_reply_valid&&t->reported_patch_version==0x8674,t->attribution_ambiguous,result);
 fprintf(stderr,"PATCH_TIMING final_submit_ms=%lld final_rx_ms=%lld elapsed_ms=%lld whole_deadline_ms=60000 same_opcode_identity=unproven\n",(long long)t->final_submit_ms,(long long)t->final_rx_ms,(long long)(t->patch_started?now_ms()-t->patch_started:0));
 diagnostic(t,"patch-only-finished");return result;
}
static int one_nvm_tail(struct transport *t,const struct firmware *f)
{
 int result=1;
 if(patch_only(t,f)!=3)goto done;
 if(!t->observed_vse_gate||!t->version_reply_valid||t->reported_patch_version!=0x8674){fail("patch8674 gate blocks one-NVM observation");goto done;}
 int64_t gap=now_ms()+10;
 while(now_ms()<gap&&!stopped)if(pump(t,gap,false))goto done;
 if(stopped){result=130;goto done;}
 if(patch_rx_available(t,t->init_end))goto done;
 if(t->count||t->have){fail("queued/partial response before one-NVM observation");goto done;}
 t->patch_stage=0;t->phase="one-NVM-tail";t->segment=t->segment_total=1;t->submitted_bytes=0;
 t->tail_active=true;t->tail_started=now_ms();t->tail_wire_start=t->wire_tx;
 uint8_t payload[245]={0x1e,243};memcpy(payload+2,f->nvm,243);
 if(send_cmd(t,0xfc00,payload,sizeof(payload),t->init_end))goto done;
 t->tail_submitted=true;t->submitted_bytes=243;
 t->tail_window_ms=3000;
#ifdef BT_UART_HOST_TEST
 const char *window=getenv("BT_UART_TEST_TAIL_MS");
 if(window){unsigned value=(unsigned)strtoul(window,NULL,10);if(value&&value<=3000)t->tail_window_ms=value;}
#endif
 t->tail_end=now_ms()+t->tail_window_ms;if(t->tail_end>t->init_end)t->tail_end=t->init_end;
 if(patch_drain(t,t->tail_end,"one-NVM-fragment"))goto done;
 while(now_ms()<t->tail_end&&!stopped)if(pump(t,t->tail_end,false))goto done;
 if(t->have){fail("partial response at NVM tail deadline");goto done;}
 result=stopped?130:3;
done:
 printf("NVM_TAIL_OBSERVATION fragment_complete=%u payload_bytes_requested=243 frame_bytes_submitted=%zu events=%u CC=%u VSE=%u other=%u responses_before_drain=%u window_ms=%u nvm_accepted=NOT_ESTABLISHED segment2_sent=0 reset_sent=0 scan_started=0 result=%d\n",t->tail_submitted,t->tail_active?t->wire_tx-t->tail_wire_start:0,t->tail_events,t->tail_cc,t->tail_vse,t->tail_other,t->tail_before_drain,t->tail_window_ms,result);
 diagnostic(t,"one-NVM-tail-finished");return result;
}
static int first_segment(struct transport *t,const struct firmware *f)
{
 int result=1;unsigned responses=0;bool sent=false;int64_t until;
 if(identify_rom(t))goto done;
 if(linux_prebaud(t))goto done;
 t->phase="first-segment-only";t->segment=t->segment_total=1;t->submitted_bytes=0;
 uint8_t payload[245]={0x1e,243};memcpy(payload+2,f->patch,243);
 if(send_cmd(t,0xfc00,payload,sizeof(payload),t->init_end))goto done;
 sent=true;t->submitted_bytes=243;
 /* No unbounded tcdrain: inspect kernel queue for <=250ms, then allow
  * an additional250ms for wire/controller processing before300ms RX window.
  * Unsupported TIOCOUTQ uses the same bounded conservative wait. */
 until=now_ms()+250;
 while(now_ms()<until&&!stopped) {
  int pending;
  if(ioctl(t->fd,TIOCOUTQ,&pending)==0&&pending==0)break;
  if(pump(t,until,false))goto done;
 }
 until=now_ms()+250;
 while(now_ms()<until&&!stopped)if(pump(t,until,false))goto done;
 until=now_ms()+300;
 while(!stopped&&now_ms()<until) {
  struct packet q;int r=event(t,&q,until);
  if(r<0)goto done;
  if(!r)break;
  if(q.p[1]==0x0e&&q.n>=6&&le16(q.p+4)==0xfc48) {if(baud_response(&q))goto done;continue;}
  responses++;
  /* A response is diagnostic evidence only, never full-image acceptance. */
  if(q.p[1]!=0x0e||q.n!=8||le16(q.p+4)!=0xfc00||q.p[3]==0||q.p[6]!=0||q.p[7]!=0x1e) {
   fail("first-segment unexpected/rejection response");goto done;
  }
 }
 result=stopped?130:3; /* silence/ACK both remain diagnostic-only, not load success */
done:
 if(first_failure)result=1;
 printf("diagnostic_only=1 first_segment_bytes=%u responses=%u firmware_loaded=NOT_ESTABLISHED scan_started=0 result=%d\n",sent?243:0,responses,result);
 diagnostic(t,"first-segment-finished");return result;
}
#endif
static int passive(struct transport *t,unsigned duration)
{
 static const uint8_t off[2]={0,0},on[2]={1,0},good=0;
 static const uint8_t mask[8]={0,0xe0,0,0,0,0,0,0x20},le_mask[8]={2,0,0,0,0,0,0,0};
 static const uint8_t params[7]={0,0x60,0,0x30,0,0,0};
 int result=1;
 t->phase="scan-startup";t->segment=t->segment_total=0;t->submitted_bytes=0;
 if(cmd(t,0x200c,off,2,&good,1)||cmd(t,0x0c01,mask,8,&good,1)||cmd(t,0x2001,le_mask,8,&good,1)||
    cmd(t,0x200b,params,7,&good,1)||cmd(t,0x200c,on,2,&good,1))goto cleanup;
 /* Any queued reports arrived with the enable ACK; discard as startup. */
 while(t->count) {struct packet q=t->queue[t->head];t->head=(t->head+1)%16;t->count--;if(adv(t,&q,false))goto cleanup;}
 t->reports=t->nonempty=t->events=t->rssi_samples=0;
 t->phase="passive-receive";
 int64_t end=now_ms()+duration;
 while(now_ms()<end&&!stopped) {
  struct packet q;int r=event(t,&q,end);if(r<0)goto cleanup;if(!r)break;
  if(adv(t,&q,true))goto cleanup;
 }
 result=stopped?130:(t->nonempty?0:3);
cleanup:
 /* Keep a real earlier failure; signal only changes an otherwise-successful result. */
 if(stopped&&result!=1)result=130;
 t->cleanup=true;
 t->phase="scan-cleanup";
 int stop=cmd(t,0x200c,off,2,&good,1);
 if(stop)result=1;
 printf("scan_disable=%s reports=%u nonempty_reports=%u events=%u ibs_wakes=%u result=%d\n",stop?"FAILED":"success",t->reports,t->nonempty,t->events,t->ibs_wakes,result);
 if(t->rssi_samples)printf("RSSI_samples=%u min_dBm=%d max_dBm=%d\n",t->rssi_samples,t->rssi_min,t->rssi_max);
 else puts("RSSI_samples=0 min_dBm=unknown max_dBm=unknown");
 return result;
}
static int session(struct transport *t,const struct firmware *f,unsigned duration)
{
 t->init_end=now_ms()+t->init_budget_ms;
#ifdef BT_UART_FIRST_SEGMENT
 return one_nvm_tail(t,f);
#endif
 if(initialize(t,f)) {diagnostic(t,"initialization-failed");return 1;} /* no speculative HCI command */
 int result=passive(t,duration);diagnostic(t,"session-finished");return result;
}
#ifndef BT_UART_HOST_TEST
static int board_guard(void)
{
 char buf[512],path[PATH_MAX];int fd=open("/proc/device-tree/compatible",O_RDONLY|O_CLOEXEC);ssize_t n;
 if(fd<0)return -1;
 n=read(fd,buf,sizeof(buf));close(fd);bool found=false;
 for(size_t i=0;n>0&&i<(size_t)n;) {size_t len=strnlen(buf+i,(size_t)n-i);if(len==(size_t)n-i)return -1;if(!strcmp(buf+i,"inseego,m3100"))found=true;i+=len+1;}
 if(!found||geteuid())return -1;
 if(!realpath("/sys/class/tty/ttyMSM1/device",path)||!strstr(path,"/830000."))return -1;
 /* ttyMSM1/device is a nested UART port, not the physical platform device. */
 if(!realpath("/sys/bus/platform/devices/830000.serial/driver",path))return -1;
 struct utsname kernel;
 if(uname(&kernel))return -1;
 fprintf(stderr,"BOARD_META UART_driver=%s kernel=%s\n",path,kernel.release);
 if(strcmp(path,"/sys/bus/platform/drivers/msm_serial")||strcmp(kernel.release,"6.12.112"))return -1;
 fd=open("/sys/bus/platform/devices/m3100-bt-power/enabled",O_RDONLY|O_CLOEXEC);
 if(fd<0)return -1;
 n=read(fd,buf,sizeof(buf));close(fd);if(n!=2||memcmp(buf,"1\n",2))return -1;
 return 0;
}
#endif
static bool cleanup_fault(const char *step)
{
#ifdef BT_UART_HOST_TEST
 const char *value=getenv("BT_UART_TEST_CLEANUP_FAIL");return value&&!strcmp(value,step);
#else
 (void)step;return false;
#endif
}
/* Cleanup never treats software flush as a hardware FIFO reset. */
static int quiet_exit_cleanup(struct transport *t,const struct termios *saved,bool restore)
{
 int result=0,pending=-1,lsr=0,out_errno=0,lsr_errno=0,flush_errno=0;
 bool empty=false;
#ifdef BT_UART_HOST_TEST
 (void)saved;(void)restore;
 pending=host_fault("postfinal-pending")?58:0;lsr=pending?0:TIOCSER_TEMT;
#else
 if(ioctl(t->fd,TIOCOUTQ,&pending))out_errno=errno;
 if(ioctl(t->fd,TIOCSERGETLSR,&lsr))lsr_errno=errno;
#endif
 if(out_errno||lsr_errno||pending||!(lsr&TIOCSER_TEMT))t->require_parent_reboot=true;
#ifndef BT_UART_HOST_TEST
 if(tcflush(t->fd,TCIOFLUSH))flush_errno=errno;
#else
 if(cleanup_fault("flush"))flush_errno=EIO;
#endif
 if(flush_errno){fail("exit software queue flush failed");result=-1;t->require_parent_reboot=true;}
 fprintf(stderr,"CLEANUP_META initial_OUTQ=%d OUTQ_errno=%d TEMT=%u LSR_errno=%d TCIOFLUSH_errno=%d flush_scope=software_only\n",pending,out_errno,!!(lsr&TIOCSER_TEMT),lsr_errno,flush_errno);
 /* Observe only: no protocol RX parser or deferred IBS output on this path. */
 int64_t end=now_ms()+100;
 do {
#ifdef BT_UART_HOST_TEST
  empty=!host_fault("postfinal-pending");
#else
  pending=-1;lsr=0;
  empty=ioctl(t->fd,TIOCOUTQ,&pending)==0&&ioctl(t->fd,TIOCSERGETLSR,&lsr)==0&&pending==0&&(lsr&TIOCSER_TEMT);
#endif
  if(empty)break;
  /* Status polling bounded to100ms. No write, tcdrain or baud change. */
 }while(now_ms()<end);
 if(!empty){t->require_parent_reboot=true;result=-1;fail("exit hardware empty unproven; parent OFF+reboot required");}
 int set_errno=0,get_errno=0;bool mismatch=false;
 if(empty) {
#ifndef BT_UART_HOST_TEST
  if(restore) {
   struct termios actual;memset(&actual,0,sizeof(actual));
   if(tcsetattr(t->fd,TCSANOW,saved))set_errno=errno;
   if(tcgetattr(t->fd,&actual))get_errno=errno;
   fprintf(stderr,"CLEANUP_TERMIOS tcsetattr_errno=%d tcgetattr_errno=%d\n",set_errno,get_errno);
   if(!get_errno) {
    bool cc_mismatch=memcmp(actual.c_cc,saved->c_cc,sizeof(saved->c_cc))!=0;
    mismatch=actual.c_cflag!=saved->c_cflag||actual.c_iflag!=saved->c_iflag||actual.c_oflag!=saved->c_oflag||actual.c_lflag!=saved->c_lflag||cc_mismatch||cfgetispeed(&actual)!=cfgetispeed(saved)||cfgetospeed(&actual)!=cfgetospeed(saved);
    fprintf(stderr,"CLEANUP_TERMIOS cflag_want=0x%lx actual=0x%lx iflag_want=0x%lx actual=0x%lx oflag_want=0x%lx actual=0x%lx lflag_want=0x%lx actual=0x%lx ispeed_want=%lu actual=%lu ospeed_want=%lu actual=%lu cc_mismatch=%u\n",(unsigned long)saved->c_cflag,(unsigned long)actual.c_cflag,(unsigned long)saved->c_iflag,(unsigned long)actual.c_iflag,(unsigned long)saved->c_oflag,(unsigned long)actual.c_oflag,(unsigned long)saved->c_lflag,(unsigned long)actual.c_lflag,(unsigned long)cfgetispeed(saved),(unsigned long)cfgetispeed(&actual),(unsigned long)cfgetospeed(saved),(unsigned long)cfgetospeed(&actual),cc_mismatch);
    for(unsigned i=0;i<NCCS;i++)if(actual.c_cc[i]!=saved->c_cc[i])fprintf(stderr,"CLEANUP_CC index=%u wanted=%u actual=%u\n",i,(unsigned)saved->c_cc[i],(unsigned)actual.c_cc[i]);
   }
  }
#else
  set_errno=cleanup_fault("set")?EIO:0;get_errno=cleanup_fault("get")?EIO:0;mismatch=cleanup_fault("mismatch");
  fprintf(stderr,"CLEANUP_TERMIOS tcsetattr_errno=%d tcgetattr_errno=%d emulated_field_mismatch=%u\n",set_errno,get_errno,mismatch);
#endif
  if(set_errno||get_errno||mismatch){fail("restore/verify original termios");result=-1;t->require_parent_reboot=true;}
  else fprintf(stderr,"PREBAUD_META original_termios_restored=success\n");
  if(restore_original_modem(t)){result=-1;t->require_parent_reboot=true;}
 } else {
  fprintf(stderr,"CLEANUP_TERMIOS tcsetattr=SKIPPED_hardware_empty_unproven modem_restore=SKIPPED RTS_physical=unknown\n");
#ifndef BT_UART_HOST_TEST
  struct termios actual;if(tcgetattr(t->fd,&actual))fprintf(stderr,"CLEANUP_TERMIOS read_only_actual_errno=%d\n",errno);
  else fprintf(stderr,"CLEANUP_TERMIOS read_only_actual_cflag=0x%lx ispeed=%lu ospeed=%lu\n",(unsigned long)actual.c_cflag,(unsigned long)cfgetispeed(&actual),(unsigned long)cfgetospeed(&actual));
#endif
 }
 fprintf(stderr,"CLEANUP_META hardware_empty_observed=%u parent_OFF_reboot_required=%u close_may_wait_kernel_finite_30s=1\n",empty,t->require_parent_reboot);
 return result;
}
int main(int argc,char **argv)
{
 struct firmware f={0};struct transport t={.fd=-1,.timeout_ms=3000,.init_budget_ms=90000};struct termios saved;
#ifndef BT_UART_HOST_TEST
 struct termios raw;
#endif
 bool restore=false,exclusive=false,uart_touched=false;unsigned duration=0;int result=2;const char *dir=NULL;
#if !defined(BT_UART_FIRST_SEGMENT) || defined(BT_UART_HOST_TEST)
 char *tail;
#endif
 signal(SIGINT,stop_now);signal(SIGTERM,stop_now);signal(SIGHUP,stop_now);signal(SIGPIPE,SIG_IGN);
#ifdef BT_UART_HOST_TEST
 if(argc!=5||strcmp(argv[1],"--test-fd"))return 2;
 t.fd=(int)strtol(argv[2],&tail,10);if(*tail||t.fd<0)return 2;
 dir=argv[3];duration=(unsigned)strtoul(argv[4],&tail,10);if(*tail||!duration||duration>1000)return 2;
 t.timeout_ms=200;t.init_budget_ms=1000;
#else
#ifdef BT_UART_FIRST_SEGMENT
 if(argc!=4||strcmp(argv[1],"--one-nvm-response-tail")||strcmp(argv[2],"--firmware-dir")) {
  fprintf(stderr,"usage: %s --one-nvm-response-tail --firmware-dir /lib/firmware/qca\n",argv[0]);return 2;
 }
 dir=argv[3];duration=0;
#else
 if(argc!=5||strcmp(argv[1],"--firmware-dir")||strcmp(argv[3],"--seconds")) {
  fprintf(stderr,"usage: %s --firmware-dir /lib/firmware/qca --seconds 1..120\n",argv[0]);return 2;
 }
 dir=argv[2];unsigned long seconds=strtoul(argv[4],&tail,10);if(*tail||seconds<1||seconds>120)return 2;duration=(unsigned)seconds*1000;
#endif
 if(board_guard()) {fail("requires root, exact M3100, ttyMSM1 UART830000 and enabled power helper");return 2;}
#endif
 if(firmware_load(dir,&f)) {fail("fixed firmware hashes/headers/NVM bounds");goto done;}
#ifndef BT_UART_HOST_TEST
 t.fd=open("/dev/ttyMSM1",O_RDWR|O_NOCTTY|O_NONBLOCK|O_CLOEXEC|O_NOFOLLOW);
 if(t.fd<0){fail("open ttyMSM1");goto done;}
 if(ioctl(t.fd,TIOCEXCL)){fail("exclusive UART");goto done;}exclusive=true;
 int discipline;
 if(ioctl(t.fd,TIOCGETD,&discipline)||discipline!=0){fail("UART line discipline is not N_TTY");goto done;}
 if(tcgetattr(t.fd,&saved)){fail("get termios");goto done;}restore=true;raw=saved;
 if(snapshot_modem(&t)){result=1;goto done;}
 cfmakeraw(&raw);cfsetispeed(&raw,B115200);cfsetospeed(&raw,B115200);
 raw.c_cflag&=~(CSIZE|CSTOPB|PARENB|PARODD);raw.c_cflag|=CS8|CLOCAL|CREAD|CRTSCTS;
 uart_touched=true; /* Exclusive N_TTY and saved state already verified. */
 if(tcsetattr(t.fd,TCSANOW,&raw)||tcflush(t.fd,TCIOFLUSH)){fail("configure UART115200 RTSCTS");goto done;}
 struct termios actual;
 if(tcgetattr(t.fd,&actual)||cfgetispeed(&actual)!=B115200||cfgetospeed(&actual)!=B115200||
    (actual.c_cflag&(CSIZE|CSTOPB|PARENB|CRTSCTS))!=(CS8|CRTSCTS)) {fail("verify UART1152008N1 RTSCTS");goto done;}
 fprintf(stderr,"UART_META configured=1152008N1 RTSCTS=enabled cflag=0x%lx\n",(unsigned long)actual.c_cflag);
#endif
 t.have_initial_counts=ioctl(t.fd,TIOCGICOUNT,&t.initial_counts)==0;
 int flags=fcntl(t.fd,F_GETFL);if(flags<0||fcntl(t.fd,F_SETFL,flags|O_NONBLOCK)<0){fail("nonblocking transport");goto done;}
#ifdef BT_UART_HOST_TEST
 const char *early_guard=getenv("BT_UART_TEST_EARLY_GUARD");
 if(early_guard){fail(early_guard);result=1;goto done;}
 if(snapshot_modem(&t)){result=1;goto done;}
 uart_touched=true;
#endif
 result=session(&t,&f,duration);
done:
 if(t.fd>=0) {
  if(uart_touched) {if(quiet_exit_cleanup(&t,&saved,restore))result=1;}
  else fprintf(stderr,"CLEANUP_META uart_not_owned_or_untouched=1 no_flush_or_reconfiguration=1\n");
  if(exclusive&&ioctl(t.fd,TIOCNXCL)){fail("release exclusive UART");result=1;}
  close(t.fd);
 }
 free(f.patch);free(f.nvm);return result;
}
