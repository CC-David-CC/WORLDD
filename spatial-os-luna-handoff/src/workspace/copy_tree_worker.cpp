#include <openssl/evp.h>
#include <dirent.h>
#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>
#include <cerrno>
#include <cstdint>
#include <cstring>
#include <algorithm>
#include <memory>
#include <string>
#include <stdexcept>
#include <iostream>
#include <vector>
namespace {
void need(bool v,const char*m){if(!v)throw std::runtime_error(m);}
struct Fd{int n=-1;explicit Fd(int x=-1):n(x){}~Fd(){if(n>=0)close(n);}Fd(const Fd&)=delete;};
struct DirCloser{void operator()(DIR*d)const{if(d)closedir(d);}};
using Dir=std::unique_ptr<DIR,DirCloser>;
struct Hash{
 EVP_MD_CTX*c=EVP_MD_CTX_new();
 Hash(){need(c&&EVP_DigestInit_ex(c,EVP_sha256(),nullptr)==1,"digest init");static const char domain[]="spatial-copy-tree-v1";add(domain,sizeof(domain)-1);}
 ~Hash(){EVP_MD_CTX_free(c);}
 void add(const void*p,size_t n){need(EVP_DigestUpdate(c,p,n)==1,"digest update");}
 void u64(uint64_t v){unsigned char b[8];for(int i=7;i>=0;--i){b[i]=static_cast<unsigned char>(v);v>>=8;}add(b,8);}
 void field(const std::string&s){u64(s.size());add(s.data(),s.size());}
 std::string end(){unsigned char b[32];unsigned n=0;need(EVP_DigestFinal_ex(c,b,&n)==1&&n==32,"digest end");static const char*h="0123456789abcdef";std::string out;out.reserve(64);for(auto c:b){out.push_back(h[c>>4]);out.push_back(h[c&15]);}return out;}
};
uint64_t bytes=0,entries=0,max_bytes=0,max_entries=0;Hash hash;
void tree(int s,int d,const std::string&prefix){
 Fd dupfd(dup(s));need(dupfd.n>=0,"dup");Dir dir(fdopendir(dupfd.n));need(bool(dir),"fdopendir");dupfd.n=-1;
 std::vector<std::string> names;while(auto*e=readdir(dir.get())){std::string n=e->d_name;if(n!="."&&n!="..")names.push_back(std::move(n));}
 std::sort(names.begin(),names.end(),[](const auto&a,const auto&b){return std::lexicographical_compare(a.begin(),a.end(),b.begin(),b.end(),[](unsigned char x,unsigned char y){return x<y;});});
 for(const auto&n:names){
  need(n.find('/')==std::string::npos&&n.find('\0')==std::string::npos,"bad component");struct stat st{};need(fstatat(s,n.c_str(),&st,AT_SYMLINK_NOFOLLOW)==0,"stat");need(++entries<=max_entries,"entry quota");
  auto rel=prefix.empty()?n:prefix+"/"+n;hash.field(rel);unsigned char type=S_ISDIR(st.st_mode)?'D':S_ISREG(st.st_mode)?'F':'?';need(type!='?',"links and special files denied");hash.add(&type,1);hash.u64(st.st_mode&0777);
  if(type=='D'){
   need(mkdirat(d,n.c_str(),0700)==0,"mkdir");Fd a(openat(s,n.c_str(),O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC)),b(openat(d,n.c_str(),O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC));need(a.n>=0&&b.n>=0,"open directory");struct stat opened{};need(fstat(a.n,&opened)==0&&opened.st_dev==st.st_dev&&opened.st_ino==st.st_ino,"source changed");tree(a.n,b.n,rel);need(fchmod(b.n,st.st_mode&0777)==0&&fsync(b.n)==0,"flush directory");
  }else{
   need(st.st_nlink==1,"hard links denied");need(st.st_size>=0&&uint64_t(st.st_size)<=max_bytes-bytes,"byte quota");hash.u64(static_cast<uint64_t>(st.st_size));Fd a(openat(s,n.c_str(),O_RDONLY|O_NOFOLLOW|O_CLOEXEC|O_NONBLOCK)),b(openat(d,n.c_str(),O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW|O_CLOEXEC,0600));struct stat now{};need(a.n>=0&&b.n>=0&&fstat(a.n,&now)==0&&now.st_dev==st.st_dev&&now.st_ino==st.st_ino&&S_ISREG(now.st_mode)&&now.st_nlink==1,"source changed");char buf[65536];uint64_t count=0;for(;;){ssize_t r=read(a.n,buf,sizeof buf);if(r<0&&errno==EINTR)continue;need(r>=0,"read");if(!r)break;need(uint64_t(r)<=max_bytes-bytes,"byte quota");hash.add(buf,r);ssize_t off=0;while(off<r){ssize_t w=write(b.n,buf+off,r-off);if(w<0&&errno==EINTR)continue;need(w>0,"write");off+=w;}count+=r;bytes+=r;}need(count==uint64_t(st.st_size),"source changed");need(fchmod(b.n,st.st_mode&0777)==0&&fsync(b.n)==0,"flush file");
  }
 }
 need(fsync(d)==0,"flush directory");
}
}
int main(int argc,char**argv){try{need(argc==3,"quotas required");max_bytes=std::stoull(argv[1]);max_entries=std::stoull(argv[2]);Fd s(open("/source",O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC)),d(open("/destination",O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC));struct stat a{},b{};need(s.n>=0&&d.n>=0&&fstat(s.n,&a)==0&&fstat(d.n,&b)==0,"roots");need(a.st_dev!=b.st_dev||a.st_ino!=b.st_ino,"source aliases destination");tree(s.n,d.n,"");std::cout<<bytes<<" "<<entries<<" "<<hash.end();return 0;}catch(const std::exception&e){std::cerr<<e.what();return 2;}}
