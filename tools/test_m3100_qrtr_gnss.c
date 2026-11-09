// SPDX-License-Identifier: MIT
/* Host-only simulated modem. No sockets or device commands are executed. */
#define _GNU_SOURCE
#include <sys/socket.h>
#include <poll.h>
#include <time.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <unistd.h>
static ssize_t fake_sendto(int,const void *,size_t,int,const struct sockaddr *,socklen_t);
static ssize_t fake_recvfrom(int,void *,size_t,int,struct sockaddr *,socklen_t *);
static int fake_poll(struct pollfd *,nfds_t,int);
static int fake_clock_gettime(clockid_t,struct timespec *);
#define sendto fake_sendto
#define recvfrom fake_recvfrom
#define poll fake_poll
#define clock_gettime fake_clock_gettime
#define main gnss_cli_main
#include "m3100-qrtr-gnss.c"
#undef main

static struct { uint8_t b[32]; size_t n; } queue[4];
static unsigned head,tail,mock_lock,mock_mode,fail_msg,fail_value,signal_during_wait,expect_afw;
static int64_t mock_ms;
static struct { unsigned msg,value; } actions[64];
static unsigned action_count;
#define CHECK(x) do { if (!(x)) { fprintf(stderr,"FAIL line%u: %s\n",__LINE__,#x); exit(1); } } while(0)
static void append_reply(unsigned type,unsigned txn,unsigned msg,unsigned failure,int value)
{
    uint8_t *b=queue[tail].b; size_t n=7;
    memset(b,0,32); b[0]=type; put16(b+1,txn); put16(b+3,msg);
    if (type==2) {
        uint8_t result[4]={0}; put16(result,failure); put16(result+2,failure?42:0);
        n+=add_tlv(b+n,2,result,4);
        if (msg==0x2d && !failure) { uint8_t mode=1;n+=add_tlv(b+n,1,&mode,1); }
    } else {
        n+=add_u32(b+n,1,0);
        if (value>=0) n+=add_u32(b+n,0x10,(unsigned)value);
    }
    put16(b+5,n-7); queue[tail++].n=n;
}
static ssize_t fake_sendto(int sock,const void *data,size_t n,int flags,const struct sockaddr *a,socklen_t alen)
{
    const uint8_t *b=data; unsigned msg=u16(b+3),txn=u16(b+1),value=0;
    (void)sock;(void)flags;(void)a;(void)alen;
    CHECK(n>=7); CHECK(head==tail);
    head=tail=0;
    if (msg==0x3a || msg==0x4a) { CHECK(n==14); value=u32(b+10); }
    if (msg==0x22 || msg==0x23) { CHECK(b[7]==1 && u16(b+8)==1 && b[10]==101); }
    if (msg==0x21) {
        CHECK(b[7]==1 && u16(b+8)==8 && u32(b+10)==7 && u32(b+14)==0);
        if (expect_afw) {
            CHECK(n==36);
            CHECK(!memcmp(b+18,(uint8_t[]){0x10,4,0,'M','3','1','0',0x11,4,0,1,0,0,0,0x12,1,0,1},18));
        } else CHECK(n==18);
    }
    CHECK(action_count<64); actions[action_count].msg=msg; actions[action_count++].value=value;
    unsigned failed=msg==fail_msg && (!fail_value || value==fail_value);
    append_reply(2,txn,msg,failed,-1);
    if (failed) return (ssize_t)n;
    if (msg==0x3a) mock_lock=value;
    if (msg==0x4a) mock_mode=value;
    if (msg==0x3b) append_reply(4,0,msg,0,mock_lock);
    else if (msg==0x4b) append_reply(4,0,msg,0,mock_mode);
    else if (msg==0x3a || msg==0x4a) append_reply(4,0,msg,0,-1);
    return (ssize_t)n;
}
static ssize_t fake_recvfrom(int sock,void *out,size_t n,int flags,struct sockaddr *a,socklen_t *alen)
{
    struct qrtr_addr addr={.family=AF_QIPCRTR,.node=3,.port=65};
    (void)sock;(void)flags; CHECK(head<tail); CHECK(n>=queue[head].n); CHECK(*alen>=sizeof(addr));
    memcpy(a,&addr,sizeof(addr));*alen=sizeof(addr);
    memcpy(out,queue[head].b,queue[head].n); return (ssize_t)queue[head++].n;
}
static int fake_poll(struct pollfd *p,nfds_t n,int timeout)
{
    CHECK(n==1); CHECK(timeout>=0);
    if (head<tail) { p->revents=POLLIN; return 1; }
    mock_ms+=timeout;
    if (signal_during_wait) interrupted=1;
    return 0;
}
static int fake_clock_gettime(clockid_t clk,struct timespec *t)
{ (void)clk;t->tv_sec=mock_ms/1000;t->tv_nsec=mock_ms%1000*1000000;return 0; }
static void reset_mock(unsigned lock,unsigned mode)
{
    head=tail=action_count=0;mock_ms=0;mock_lock=lock;mock_mode=mode;
    fail_msg=fail_value=signal_during_wait=expect_afw=0;interrupted=0;transaction=0;
    node=3;port=65;fd=7;program="/tmp/m3100-qrtr-gnss";
}
static int index_of(unsigned msg,unsigned value)
{
    for (unsigned i=0;i<action_count;i++) if(actions[i].msg==msg && actions[i].value==value) return (int)i;
    return -1;
}
int main(void)
{
    CHECK(selftest()==0);
    reset_mock(3,4);CHECK(command(0x2d,NULL,0,0,NULL)==0);
    CHECK(action_count==1 && actions[0].msg==0x2d && mock_lock==3 && mock_mode==4);
    puts("PASS: DMS mode is queried with one GET and leaves GNSS state untouched");

    reset_mock(3,4); CHECK(session(1,0,0)==0);
    CHECK(mock_lock==3 && mock_mode==4); CHECK(index_of(0x3a,1)<0 && index_of(0x4a,4)<0);
    CHECK(index_of(0x23,0)>index_of(0x22,0));
    puts("PASS: observed MT3/standalone4 uses no lock/mode SET and stops session101");

    reset_mock(3,4);expect_afw=1;CHECK(session(1,0,1)==0);
    CHECK(mock_lock==3 && mock_mode==4);CHECK(index_of(0x3a,1)<0 && index_of(0x4a,4)<0);
    puts("PASS: explicit AFW uses exact documented client metadata TLVs with no lock/mode change");

    reset_mock(2,4);CHECK(session(1,0,0)!=0);
    CHECK(index_of(0x3a,1)<0 && index_of(0x22,0)<0 && mock_lock==2);
    puts("PASS: MI lock cannot be changed without explicit temporary-unlock flag");

    reset_mock(2,2); CHECK(session(1,1,0)==0);
    CHECK(mock_lock==2 && mock_mode==2);
    CHECK(index_of(0x23,0)<index_of(0x4a,2));CHECK(index_of(0x4a,2)<index_of(0x3a,2));
    puts("PASS: explicit temporary MI unlock restores mode then lock after STOP");

    reset_mock(2,2); fail_msg=0x22; CHECK(session(1,1,0)!=0);
    CHECK(mock_lock==2 && mock_mode==2 && index_of(0x23,0)>=0);
    puts("PASS: rejected START still attempts STOP and restores both original settings");

    reset_mock(2,2);fail_msg=0x21;CHECK(session(1,1,0)!=0);
    CHECK(index_of(0x22,0)<0 && index_of(0x23,0)<0 && mock_lock==2 && mock_mode==2);
    puts("PASS: rejected registration never starts a session and restores prior changes");

    reset_mock(2,2); fail_msg=0x23; CHECK(session(1,1,0)!=0);CHECK(mock_lock==2 && mock_mode==2);
    puts("PASS: STOP failure preserves failure status while still restoring settings");

    reset_mock(2,2); fail_msg=0x4a;fail_value=2;CHECK(session(1,1,0)!=0);
    CHECK(mock_lock==2 && mock_mode==4);CHECK(index_of(0x3a,2)>=0);
    puts("PASS: failed mode restoration does not skip lock restoration; readback failure detected");

    reset_mock(2,2);signal_during_wait=1;CHECK(session(2,1,0)==0);
    CHECK(mock_lock==2 && mock_mode==2 && index_of(0x23,0)>=0);
    puts("PASS: signal during monitoring stops own session and restores both settings");
    return 0;
}
