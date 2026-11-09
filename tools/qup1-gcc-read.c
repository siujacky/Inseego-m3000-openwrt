/* M3100 RAM-only fixed GCC clock-register snapshot. No QUP-bank access.
 * Source: Linux6.12 gcc-sdx65.c, clk-rcg2.c, clk-branch.c; GCC base100000.
 * No clock enable/reset/pin/bus operation; O_RDONLY and PROT_READ only.
 */
#define _GNU_SOURCE
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>
static int read_text(const char *name,char *out,size_t count)
{
 int fd=open(name,O_RDONLY|O_CLOEXEC);if(fd<0)return -1;
 ssize_t n=read(fd,out,count-1);close(fd);if(n<=0||(size_t)n==count-1)return -1;
 out[n]=0;return 0;
}
static int guard(void)
{
 char text[4096];unsigned char id[8],expected[8]={2,1,0,8,0,0,3,2};
 if(geteuid()!=0||read_text("/proc/device-tree/compatible",text,sizeof(text))||strcmp(text,"inseego,m3100"))return -1;
 int fd=open("/proc/device-tree/qcom,board-id",O_RDONLY|O_CLOEXEC);if(fd<0)return -1;
 ssize_t n=read(fd,id,8);close(fd);if(n!=8||memcmp(id,expected,8))return -1;
 if(read_text("/proc/cmdline",text,sizeof(text)))return -1;
 unsigned int roots=0;char *save,*token=strtok_r(text," \t\n",&save);
 while(token){
  if(!strncmp(token,"root=",5)){if(strcmp(token,"root=/dev/ram0"))return -1;roots++;}
  if(!strncmp(token,"ubi.",4)||!strncmp(token,"mtdparts=",9)||!strncmp(token,"rootfstype=",11))return -1;
  token=strtok_r(NULL," \t\n",&save);
 }
 if(roots!=1||sysconf(_SC_PAGESIZE)!=4096)return -1;
 DIR *dir=opendir("/sys/class/mtd");
 if(dir){struct dirent *e;while((e=readdir(dir)))if(!strncmp(e->d_name,"mtd",3)){closedir(dir);return -1;}closedir(dir);}
 else if(errno!=ENOENT)return -1;
 return 0;
}
int main(int argc,char **argv)
{
 (void)argv;
 static const struct {uint32_t address;const char *name;} regs[]={
  {0x0011c024,"QUP1_I2C_CMD_RCGR"},
  {0x0011c028,"QUP1_I2C_CFG_RCGR"},
  {0x0011c008,"QUP1_I2C_APPS_CBCR"},
  {0x0016d008,"BLSP1_SHARED_AHB_VOTE"},
  {0x0011b004,"BLSP1_AHB_HALT"}
 };
 if(argc!=1||guard()){fputs("requires exact M3100, board ID, RAM root, no MTD\n",stderr);return 2;}
 int fd=open("/dev/mem",O_RDONLY|O_SYNC|O_CLOEXEC);if(fd<0){perror("open devmem");return 1;}
 puts("READ_ONLY fixed GCC registers; no QUP-bank access; idle state is not transfer-time proof");
 for(unsigned int i=0;i<sizeof(regs)/sizeof(regs[0]);i++){
  uint32_t base=regs[i].address&~4095U;
  void *map=mmap(NULL,4096,PROT_READ,MAP_SHARED,fd,base);
  if(map==MAP_FAILED){perror("map GCC register");close(fd);return 1;}
  const volatile uint32_t *reg=(const volatile uint32_t *)((const char *)map+regs[i].address-base);
  uint32_t v=*reg;
  printf("0x%08x %-25s = 0x%08x\n",regs[i].address,regs[i].name,v);
  if(i==0)printf("  root_enable_bit1=%u root_off_bit31=%u\n",!!(v&2),!!(v&(1U<<31)));
  if(i==1)printf("  hw_clk_ctrl_bit20=%u src_sel_bits10_8=%u hid_div_bits4_0=%u\n",!!(v&(1U<<20)),(v>>8)&7,v&31);
  if(i==2)printf("  software_enable_bit0=%u halt_bit31=%u\n",v&1,!!(v&(1U<<31)));
  if(i==3)printf("  shared_blsp1_ahb_vote_bit14=%u\n",!!(v&(1U<<14)));
  if(i==4)printf("  ahb_halt_bit31=%u\n",!!(v&(1U<<31)));
  munmap(map,4096);
 }
 close(fd);return 0;
}
