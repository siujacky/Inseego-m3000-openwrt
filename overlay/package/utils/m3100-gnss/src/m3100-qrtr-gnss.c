// SPDX-License-Identifier: MIT
/* Standalone QRTR discovery and bounded QMI LOC client.
 * Protocol sources: Linux uapi/linux/qrtr.h; andersson/qrtr lib/qrtr.c;
 * libqmi data/qmi-service-{loc,pds}.json and qmi-{enums,flags64}-loc.h.
 * No filesystem writes. Read queries do not change GNSS state. Explicit
 * bounded sessions start/stop GNSS; any needed mode/lock changes are restored.
 */
#define _GNU_SOURCE
#include <sys/socket.h>
#include <poll.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <unistd.h>
#include <signal.h>
#include <time.h>
#ifndef AF_QIPCRTR
#define AF_QIPCRTR 42
#endif
struct qrtr_addr { sa_family_t family; uint32_t node, port; };
static volatile sig_atomic_t interrupted;
static uint16_t transaction;
static uint32_t node, port;
static int fd;
static const char *program;
enum { LOC_LOCK_NONE=1, LOC_LOCK_MI=2, LOC_LOCK_MT=3, LOC_LOCK_ALL=4 };
static const char *lock_name(uint32_t lock)
{
    switch (lock) {
    case LOC_LOCK_NONE: return "NONE";
    case LOC_LOCK_MI: return "MI";
    case LOC_LOCK_MT: return "MT";
    case LOC_LOCK_ALL: return "ALL";
    default: return "UNKNOWN";
    }
}
static int mi_permitted(uint32_t lock) { return lock==LOC_LOCK_NONE || lock==LOC_LOCK_MT; }
static const char *dms_mode_name(unsigned mode)
{
    static const char *names[]={"ONLINE","LOW_POWER","FACTORY_TEST","OFFLINE","RESET","SHUTTING_DOWN","PERSISTENT_LOW_POWER","MODE_ONLY_LOW_POWER"};
    return mode<8?names[mode]:"UNKNOWN";
}
static void on_signal(int sig) { (void)sig; interrupted = 1; }
static uint16_t u16(const uint8_t *p) { return p[0] | (uint16_t)p[1] << 8; }
static uint32_t u32(const uint8_t *p) { return u16(p) | (uint32_t)u16(p+2) << 16; }
static void put16(uint8_t *p, unsigned v) { p[0] = v; p[1] = v >> 8; }
static void put32(uint8_t *p, uint32_t v) { put16(p,v); put16(p+2,v>>16); }
static int64_t now_ms(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC,&t); return (int64_t)t.tv_sec*1000+t.tv_nsec/1000000; }
static int remaining_ms(int64_t deadline) { int64_t n=deadline-now_ms(); return n>0 ? (int)n : 0; }
static int transmit(uint32_t n, uint32_t p, const void *b, size_t len)
{
    struct qrtr_addr a = { .family=AF_QIPCRTR, .node=n, .port=p };
    if (sendto(fd,b,len,0,(struct sockaddr *)&a,sizeof(a)) != (ssize_t)len) { perror("QRTR sendto"); return -1; }
    return 0;
}
static int receive(uint8_t *b, size_t cap, int timeout, struct qrtr_addr *a)
{
    struct pollfd p = { .fd=fd, .events=POLLIN }; socklen_t alen=sizeof(*a);
    int ret=poll(&p,1,timeout);
    if (ret<=0) return ret;
    ret=recvfrom(fd,b,cap,0,(struct sockaddr *)a,&alen);
    if (ret<0) perror("QRTR recvfrom");
    return ret;
}
static const uint8_t *tlv(const uint8_t *b, size_t n, unsigned id, size_t *len)
{
    size_t off=7;
    if (n<7 || (size_t)u16(b+5)+7 != n) return NULL;
    while (off+3<=n) {
        size_t l=u16(b+off+1);
        if (l>n-off-3) return NULL;
        if (b[off]==id) { *len=l; return b+off+3; }
        off+=3+l;
    }
    return NULL;
}
static size_t add_tlv(uint8_t *p, uint8_t id, const void *value, size_t len)
{ p[0]=id; put16(p+1,len); memcpy(p+3,value,len); return len+3; }
static size_t add_u32(uint8_t *p, uint8_t id, uint32_t value)
{ uint8_t v[4]; put32(v,value); return add_tlv(p,id,v,4); }
/* Handles response ACK and, where required, separate LOC indication. */
static int command(unsigned msg, const uint8_t *data, size_t len, int indication,
                   uint32_t *value)
{
    uint8_t b[8192], req[128]; int got_ack=0,got_ind=!indication;
    struct qrtr_addr a; int64_t end=now_ms()+5000; uint16_t txn=++transaction;
    if (len>sizeof(req)-7) return -1;
    req[0]=0; put16(req+1,txn); put16(req+3,msg); put16(req+5,len);
    if (len) memcpy(req+7,data,len);
    printf("QMI TX msg=0x%04x txn=%u\n",msg,txn);
    if (transmit(node,port,req,len+7)) return -1;
    while (now_ms()<end && (!got_ack || !got_ind)) {
        int n=receive(b,sizeof(b),remaining_ms(end),&a); size_t l; const uint8_t *v;
        if (n<0 && errno==EINTR) continue;
        if (n<=0) break;
        if (a.node!=node || a.port!=port || n<7 || u16(b+3)!=msg) continue;
        if (b[0]==2 && u16(b+1)==txn) {
            v=tlv(b,n,2,&l);
            if (!v || l!=4) { fprintf(stderr,"Missing result TLV\n"); return -1; }
            printf("QMI ACK msg=0x%04x result=%u error=%u\n",msg,u16(v),u16(v+2));
            if (u16(v)) return -1;
            got_ack=1;
            /* PDS Get GPS Service State is in its synchronous response. */
            if (!indication && msg==0x20 && (v=tlv(b,n,1,&l)) && l==2)
                printf("PDS gps_enabled=%u tracking_state=%u\n",v[0],v[1]);
            if (!indication && msg==0x2d) {
                v=tlv(b,n,1,&l);
                if (!v || l!=1) { fprintf(stderr,"DMS mode TLV missing or malformed\n"); return -1; }
                printf("DMS operating_mode=%u (%s)\n",v[0],dms_mode_name(v[0]));
                if ((v=tlv(b,n,0x10,&l)) && l==2) printf("DMS offline_reason_bits=0x%04x\n",u16(v));
                if ((v=tlv(b,n,0x11,&l)) && l==1) printf("DMS hardware_restricted=%u\n",v[0]);
            }
        } else if (b[0]==4 && indication) {
            v=tlv(b,n,1,&l);
            if (!v || l!=4) return -1;
            printf("LOC indication msg=0x%04x status=%u\n",msg,u32(v));
            if (u32(v)) return -1;
            got_ind=1;
            if (value) {
                v=tlv(b,n,0x10,&l);
                if (!v || l!=4) return -1;
                *value=u32(v); printf("LOC value msg=0x%04x value=%u\n",msg,*value);
                if (msg==0x3b) printf("LOC engine_lock=%u (%s) mobile_initiated_permitted=%d\n",*value,lock_name(*value),mi_permitted(*value));
            }
        }
    }
    if (!got_ack || !got_ind) { fprintf(stderr,"QMI timeout msg=0x%04x ack=%d ind=%d\n",msg,got_ack,got_ind); return -1; }
    return 0;
}
static int discover(void)
{
    struct qrtr_addr self={0},a; socklen_t slen=sizeof(self);
    uint8_t req[20]={0},b[512]; int64_t end=now_ms()+5000;
    /* getsockname binds an unbound QRTR socket, as in libqrtr. */
    if (getsockname(fd,(struct sockaddr *)&self,&slen)) { perror("getsockname"); return 1; }
    put32(req,10); /* NEW_LOOKUP with wildcard service/version/instance. */
    if (transmit(self.node,0xfffffffeU,req,sizeof(req))) return 1;
    printf("QRTR discovery local_node=%u local_port=%u\n",self.node,self.port);
    while (now_ms()<end && !interrupted) {
        int n=receive(b,sizeof(b),remaining_ms(end),&a);
        if (n<=0) break;
        if (a.port!=0xfffffffeU || n<20 || u32(b)!=4) continue;
        uint32_t service=u32(b+4),instance=u32(b+8),sn=u32(b+12),sp=u32(b+16);
        if (!sn && !sp) { puts("QRTR initial lookup complete"); break; }
        printf("SERVICE id=%u version=%u instance=%u node=%u port=%u%s\n",
               service,instance&255,instance>>8,sn,sp,
               service==16?" LOC":service==6?" PDS":"");
    }
    return 0;
}
static double le_double(const uint8_t *v)
{
    uint64_t bits=(uint64_t)u32(v)|((uint64_t)u32(v+4)<<32); double d;
    memcpy(&d,&bits,8); return d;
}
static void print_event(const uint8_t *b, size_t n)
{
    size_t l; const uint8_t *v; unsigned msg;
    if (n<7 || b[0]!=4) return;
    msg=u16(b+3);
    if (msg==0x26 && (v=tlv(b,n,1,&l))) {
        printf("NMEA %.*s\n",(int)l,(const char *)v);
    } else if (msg==0x24) {
        uint32_t status=UINT32_MAX; double lat=0,lon=0; int has_lat=0,has_lon=0;
        if ((v=tlv(b,n,1,&l)) && l==4) status=u32(v);
        if ((v=tlv(b,n,0x10,&l)) && l==8) { lat=le_double(v); has_lat=1; }
        if ((v=tlv(b,n,0x11,&l)) && l==8) { lon=le_double(v); has_lon=1; }
        printf("POSITION session_status=%u",status);
        if (has_lat && has_lon) printf(" latitude=%.9f longitude=%.9f",lat,lon);
        printf(" valid_fix=%d\n",status==0 && has_lat && has_lon && lat>=-90 && lat<=90 && lon>=-180 && lon<=180);
    } else if (msg==0x25) {
        printf("SATELLITE_REPORT bytes=%zu\n",n);
    } else printf("LOC event msg=0x%04x bytes=%zu\n",msg,n);
}
static int set_value(unsigned msg, uint32_t value)
{
    uint8_t p[7]; size_t len=add_u32(p,1,value);
    return command(msg,p,len,1,NULL);
}
static int verify_value(unsigned query, uint32_t expected)
{
    uint32_t actual=0;
    if (command(query,NULL,0,1,&actual)) return -1;
    if (actual!=expected) { fprintf(stderr,"RESTORE MISMATCH msg=0x%04x expected=%u actual=%u\n",query,expected,actual); return -1; }
    printf("RESTORE VERIFIED msg=0x%04x value=%u\n",query,actual);
    return 0;
}
static int register_events(unsigned mask, int client_type)
{
    uint8_t p[40],bits[8]={0};size_t len;
    put32(bits,mask);len=add_tlv(p,1,bits,8);
    if (client_type) {
        len+=add_tlv(p+len,0x10,"M310",4);
        len+=add_u32(p+len,0x11,client_type);
        { uint8_t notify=1; len+=add_tlv(p+len,0x12,&notify,1); }
    }
    printf("LOC register mask=%u client=%s TLVs=",mask,client_type==1?"M310/AFW":client_type==2?"M310/NFW":"unspecified");
    for(size_t i=0;i<len;i++) printf("%02x",p[i]);
    putchar('\n');
    return command(0x21,p,len,0,NULL);
}
static int session(unsigned seconds, int temporary_unlock, int client_type)
{
    uint32_t mode=0,lock=0; int changed=0,lock_changed=0,started=0,rc=1; uint8_t p[64],sid=101;
    size_t len; struct qrtr_addr a;
    if (command(0x3b,NULL,0,1,&lock) || command(0x4b,NULL,0,1,&mode)) return 1;
    if (lock<1 || lock>4) { fprintf(stderr,"Unknown lock; leaving unchanged\n"); return 1; }
    if (!mi_permitted(lock) && !temporary_unlock) {
        fprintf(stderr,"GNSS mobile-initiated sessions locked (%s); leaving unchanged. Explicit --temporary-unlock required.\n",lock_name(lock)); return 1;
    }
    if (!mode || mode>6) { fprintf(stderr,"Unknown operation mode; leaving unchanged\n"); return 1; }
    if (interrupted) goto cleanup;
    if (!mi_permitted(lock)) {
        lock_changed=1; /* Restore even if successful SET loses its reply. */
        if (set_value(0x3a,LOC_LOCK_NONE) || verify_value(0x3b,LOC_LOCK_NONE)) goto cleanup;
    }
    if (mode!=4) {
        changed=1; len=add_u32(p,1,4);
        if (command(0x4a,p,len,1,NULL)) goto cleanup;
    }
    if (interrupted) goto cleanup;
    if (register_events(7,client_type)) goto cleanup;
    if (interrupted) goto cleanup;
    len=add_tlv(p,1,&sid,1); len+=add_u32(p+len,0x10,1);
    len+=add_u32(p+len,0x12,1); len+=add_u32(p+len,0x13,1000);
    started=1; /* STOP even if the ACK is lost after successful startup. */
    if (command(0x22,p,len,0,NULL)) goto cleanup;
    printf("LOC session101 started standalone; bounded_seconds=%u\n",seconds);
    { int64_t end=now_ms()+(int64_t)seconds*1000; uint8_t b[8192];
      while (now_ms()<end && !interrupted) {
        int n=receive(b,sizeof(b),1000,&a);
        if (n>0 && a.node==node && a.port==port) print_event(b,n);
      }
    }
    rc=0;
cleanup:
    if (started) { len=add_tlv(p,1,&sid,1); if (command(0x23,p,len,0,NULL)) rc=1; }
    if (changed && set_value(0x4a,mode)) rc=1;
    if (lock_changed && set_value(0x3a,lock)) rc=1;
    if (verify_value(0x4b,mode)) {
        rc=1; fprintf(stderr,"RETRY: %s loc-restore-mode %u %u %u\n",program,node,port,mode);
    }
    if (verify_value(0x3b,lock)) {
        rc=1; fprintf(stderr,"RETRY: %s loc-restore-lock %u %u %u\n",program,node,port,lock);
    }
    return rc;
}
static int selftest(void)
{
    uint8_t b[32]={2,1,0,0x4b,0,7,0,2,4,0,0,0,0,0}, p[8];
    size_t len=0; const uint8_t *v=tlv(b,14,2,&len);
    if (!v || len!=4 || u16(v)!=0 || tlv(b,13,2,&len)) return 1;
    if (add_u32(p,1,4)!=7 || memcmp(p,(uint8_t[]){1,4,0,4,0,0,0},7)) return 1;
    if (le_double((uint8_t[]){0,0,0,0,0,0,0xf0,0x3f})!=1.0) return 1;
    if (LOC_LOCK_NONE!=1 || LOC_LOCK_MI!=2 || LOC_LOCK_MT!=3 || LOC_LOCK_ALL!=4) return 1;
    if (!mi_permitted(1) || mi_permitted(2) || !mi_permitted(3) || mi_permitted(4) || mi_permitted(0)) return 1;
    if (strcmp(lock_name(3),"MT")) return 1;
    if (strcmp(dms_mode_name(0),"ONLINE") || strcmp(dms_mode_name(1),"LOW_POWER") || strcmp(dms_mode_name(3),"OFFLINE") || strcmp(dms_mode_name(255),"UNKNOWN")) return 1;
    puts("PASS: QMI result/lengths, standalone TLV, LE double, lock enums and MI permission fixtures");
    return 0;
}
int main(int argc, char **argv)
{
    int rc=1;
    program=argv[0];
    setvbuf(stdout,NULL,_IOLBF,0); signal(SIGINT,on_signal); signal(SIGTERM,on_signal);
    if (argc<2) goto usage;
    if (!strcmp(argv[1],"selftest")) return selftest();
    if (strcmp(argv[1],"discover") && argc<4) goto usage;
    if (argc>=4) { node=strtoul(argv[2],NULL,0); port=strtoul(argv[3],NULL,0); }
    fd=socket(AF_QIPCRTR,SOCK_DGRAM,0);
    if (fd<0) { perror("socket AF_QIPCRTR"); return 1; }
    if (!strcmp(argv[1],"discover")) rc=discover();
    else if (!strcmp(argv[1],"loc-query")) {
        uint32_t mode,lock;
        rc=command(0x3b,NULL,0,1,&lock) || command(0x4b,NULL,0,1,&mode);
    } else if (!strcmp(argv[1],"pds-query")) rc=command(0x20,NULL,0,0,NULL)!=0;
    else if (!strcmp(argv[1],"dms-query")) rc=command(0x2d,NULL,0,0,NULL)!=0;
    else if (!strcmp(argv[1],"loc-register") && (argc==5 || (argc==6 && (!strcmp(argv[5],"--nfw-client") || !strcmp(argv[5],"--afw-client"))))) {
        unsigned mask=strtoul(argv[4],NULL,0);
        int client_type=argc==6 ? (!strcmp(argv[5],"--afw-client")?1:2) : 0;
        if (mask>=1 && mask<=7) rc=register_events(mask,client_type)!=0;
    }
    else if (!strcmp(argv[1],"loc-session") && argc>=5 && argc<=7) {
        unsigned seconds=strtoul(argv[4],NULL,0);
        int unlock=0,client_type=0;
        for(int i=5;i<argc;i++) {
            if(!strcmp(argv[i],"--temporary-unlock")) unlock=1;
            else if(!strcmp(argv[i],"--afw-client")) client_type=1;
            else goto usage_close;
        }
        if (seconds>=1 && seconds<=300) rc=session(seconds,unlock,client_type);
    } else if ((!strcmp(argv[1],"loc-restore-lock") || !strcmp(argv[1],"loc-restore-mode")) && argc==5) {
        unsigned value=strtoul(argv[4],NULL,0),is_lock=!strcmp(argv[1],"loc-restore-lock");
        if (value>=1 && value<=(is_lock?4U:6U))
            rc=set_value(is_lock?0x3a:0x4a,value) || verify_value(is_lock?0x3b:0x4b,value);
    } else goto usage_close;
    close(fd); return rc;
usage_close:
    close(fd);
usage:
    fprintf(stderr,"Usage: %s discover | loc-query NODE PORT | pds-query NODE PORT | dms-query NODE PORT | loc-register NODE PORT MASK(1..7) [--afw-client|--nfw-client] | loc-session NODE PORT SECONDS(1..300) [--afw-client] [--temporary-unlock] | loc-restore-lock NODE PORT VALUE | loc-restore-mode NODE PORT VALUE\n",argv[0]);
    return 2;
}
