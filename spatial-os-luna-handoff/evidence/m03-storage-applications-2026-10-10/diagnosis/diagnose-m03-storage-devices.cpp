#include <linux/btrfs.h>
#include <sys/ioctl.h>
#include <sys/stat.h>
#include <fcntl.h>
#include <unistd.h>
#include <iostream>
#include <cstring>
int main(int argc,char**argv){
 if(argc!=2)return 2;
 int fd=open(argv[1],O_RDONLY|O_DIRECTORY|O_CLOEXEC);if(fd<0)return 3;
 struct stat st{};fstat(fd,&st);
 std::cout<<"uid="<<getuid()<<" filesystem_st_dev="<<st.st_dev<<"\n";
 btrfs_ioctl_fs_info_args fs{};int result=ioctl(fd,BTRFS_IOC_FS_INFO,&fs);
 std::cout<<"FS_INFO="<<result<<" errno="<<errno<<" "<<strerror(errno)<<" num="<<fs.num_devices<<" max="<<fs.max_id<<"\n";
 btrfs_ioctl_dev_info_args dev{};dev.devid=fs.max_id;errno=0;result=ioctl(fd,BTRFS_IOC_DEV_INFO,&dev);
 std::cout<<"DEV_INFO="<<result<<" errno="<<errno<<" "<<strerror(errno)<<" path="<<dev.path<<"\n";
 close(fd);return 0;
}
