#include <fcntl.h>
#include <stdio.h>
#include <time.h>
#include <unistd.h>
#include <sys/syscall.h>
int main(int argc,char**argv) {
 if(argc!=3)return 2;
 int fd=open(argv[2],O_WRONLY|O_CREAT|O_EXCL,0600);
 if(fd<0 || write(fd,"TIMING_PRIVATE_MARKER",21)!=21 || fsync(fd) || fdatasync(fd))return 3;
 struct timespec delay={0,180000000}; if(nanosleep(&delay,0))return 4;close(fd);
 printf("PROBE kind=%s pid=%d parent=%d fsync=%d fdatasync=%d sleep=%d\n",argv[1],getpid(),getppid(),SYS_fsync,SYS_fdatasync,SYS_clock_nanosleep);
 return 0;
}
