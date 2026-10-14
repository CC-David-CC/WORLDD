#include "copy_worker.pb.h"
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
#include <vector>
#include <google/protobuf/unknown_field_set.h>
namespace {
namespace pb = spatial::workspace::copyworker::v1;
struct Failure final : std::runtime_error {
    pb::FailureReason reason;
    explicit Failure(pb::FailureReason value) : std::runtime_error("copy worker rejected input"), reason(value) {}
};
void need(bool value, pb::FailureReason reason=pb::FAILURE_INTERNAL) {
    if (!value) throw Failure(reason);
}
struct Fd { int n=-1; explicit Fd(int x=-1):n(x){} ~Fd(){if(n>=0)close(n);} Fd(const Fd&)=delete; };
struct DirCloser { void operator()(DIR* d) const { if (d) closedir(d); } };
using Dir = std::unique_ptr<DIR,DirCloser>;
struct Hash {
    EVP_MD_CTX* c=EVP_MD_CTX_new();
    Hash(){need(c&&EVP_DigestInit_ex(c,EVP_sha256(),nullptr)==1);static const char domain[]="spatial-copy-tree-v1";add(domain,sizeof(domain)-1);}
    ~Hash(){EVP_MD_CTX_free(c);}
    void add(const void* p,size_t n){need(EVP_DigestUpdate(c,p,n)==1);}
    void u64(uint64_t v){unsigned char b[8];for(int i=7;i>=0;--i){b[i]=static_cast<unsigned char>(v);v>>=8;}add(b,8);}
    void field(const std::string& s){u64(s.size());add(s.data(),s.size());}
    std::string end(){unsigned char b[32];unsigned n=0;need(EVP_DigestFinal_ex(c,b,&n)==1&&n==32);return {reinterpret_cast<char*>(b),n};}
};
uint64_t bytes=0,entries=0,max_bytes=0,max_entries=0;
std::unique_ptr<Hash> hash;
void tree(int s,int d,const std::string& prefix) {
    Fd dupfd(dup(s));need(dupfd.n>=0);
    Dir dir(fdopendir(dupfd.n));need(bool(dir));dupfd.n=-1;
    std::vector<std::string> names;
    while(auto* e=readdir(dir.get())){std::string n=e->d_name;if(n!="."&&n!="..")names.push_back(std::move(n));}
    std::sort(names.begin(),names.end(),[](const auto&a,const auto&b){return std::lexicographical_compare(a.begin(),a.end(),b.begin(),b.end(),[](unsigned char x,unsigned char y){return x<y;});});
    for(const auto& n:names) {
        need(n.find('/')==std::string::npos&&n.find('\0')==std::string::npos);
        struct stat st{};need(fstatat(s,n.c_str(),&st,AT_SYMLINK_NOFOLLOW)==0);
        if(S_ISLNK(st.st_mode))throw Failure(pb::FAILURE_SYMLINK);
        if(++entries>max_entries)throw Failure(pb::FAILURE_ENTRY_LIMIT);
        auto rel=prefix.empty()?n:prefix+"/"+n;
        hash->field(rel);
        unsigned char type=S_ISDIR(st.st_mode)?'D':S_ISREG(st.st_mode)?'F':'?';
        if(type=='?')throw Failure(pb::FAILURE_SPECIAL_ENTRY);
        hash->add(&type,1);hash->u64(st.st_mode&0777);
        if(type=='D') {
            if(mkdirat(d,n.c_str(),0700)!=0)throw Failure(pb::FAILURE_DESTINATION_CONFLICT);
            Fd a(openat(s,n.c_str(),O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC)),b(openat(d,n.c_str(),O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC));
            need(a.n>=0&&b.n>=0);
            struct stat opened{};if(fstat(a.n,&opened)<0||opened.st_dev!=st.st_dev||opened.st_ino!=st.st_ino)throw Failure(pb::FAILURE_SOURCE_CHANGED);
            tree(a.n,b.n,rel);
            need(fchmod(b.n,st.st_mode&0777)==0&&fsync(b.n)==0);
        } else {
            if(st.st_nlink!=1)throw Failure(pb::FAILURE_HARD_LINK);
            if(st.st_size<0||uint64_t(st.st_size)>max_bytes-bytes)throw Failure(pb::FAILURE_BYTE_LIMIT);
            hash->u64(static_cast<uint64_t>(st.st_size));
            Fd a(openat(s,n.c_str(),O_RDONLY|O_NOFOLLOW|O_CLOEXEC|O_NONBLOCK)),b(openat(d,n.c_str(),O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW|O_CLOEXEC,0600));
            struct stat now{};need(a.n>=0&&b.n>=0);
            if(fstat(a.n,&now)<0||now.st_dev!=st.st_dev||now.st_ino!=st.st_ino||!S_ISREG(now.st_mode)||now.st_nlink!=1)throw Failure(pb::FAILURE_SOURCE_CHANGED);
            char buf[65536];uint64_t count=0;
            for(;;) {
                ssize_t r=read(a.n,buf,sizeof buf);if(r<0&&errno==EINTR)continue;need(r>=0);
                if(!r)break;
                if(uint64_t(r)>max_bytes-bytes)throw Failure(pb::FAILURE_BYTE_LIMIT);
                hash->add(buf,r);ssize_t off=0;
                while(off<r){ssize_t w=write(b.n,buf+off,r-off);if(w<0&&errno==EINTR)continue;need(w>0);off+=w;}
                count+=r;bytes+=r;
            }
            if(count!=uint64_t(st.st_size))throw Failure(pb::FAILURE_SOURCE_CHANGED);
            need(fchmod(b.n,st.st_mode&0777)==0&&fsync(b.n)==0);
        }
    }
    need(fsync(d)==0);
}
bool write_all(int fd,const void* data,size_t size) {
    auto* p=static_cast<const unsigned char*>(data);
    while(size){ssize_t n=write(fd,p,size);if(n<0&&errno==EINTR)continue;if(n<=0)return false;p+=n;size-=n;}
    return true;
}
int emit(pb::WorkerResult result) {
    std::string payload;if(!result.SerializeToString(&payload)||payload.empty()||payload.size()>4096)return 2;
    unsigned char prefix[4]={static_cast<unsigned char>(payload.size()>>24),static_cast<unsigned char>(payload.size()>>16),static_cast<unsigned char>(payload.size()>>8),static_cast<unsigned char>(payload.size())};
    return write_all(STDOUT_FILENO,prefix,sizeof(prefix))&&write_all(STDOUT_FILENO,payload.data(),payload.size())?0:2;
}
}
int main() {
    pb::WorkerResult result;result.set_protocol_version(1);
    try {
        Fd request_fd(open("/job.pb",O_RDONLY|O_NOFOLLOW|O_CLOEXEC));need(request_fd.n>=0);
        struct stat st{};need(fstat(request_fd.n,&st)==0&&S_ISREG(st.st_mode)&&st.st_size>0&&st.st_size<=4096);
        std::string input(static_cast<size_t>(st.st_size),'\0');size_t used=0;
        while(used<input.size()){ssize_t n=read(request_fd.n,input.data()+used,input.size()-used);if(n<0&&errno==EINTR)continue;need(n>0);used+=n;}
        pb::WorkerRequest request;need(request.ParseFromString(input)&&request.protocol_version()==1&&request.maximum_bytes()>0&&request.maximum_bytes()<=4ULL*1024*1024*1024&&request.maximum_entries()>0&&request.maximum_entries()<=200000);
        need(request.GetReflection()->GetUnknownFields(request).empty());
        max_bytes=request.maximum_bytes();max_entries=request.maximum_entries();hash=std::make_unique<Hash>();
        Fd s(open("/source",O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC)),d(open("/destination",O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC));
        struct stat a{},b{};need(s.n>=0&&d.n>=0&&fstat(s.n,&a)==0&&fstat(d.n,&b)==0);
        need(a.st_dev!=b.st_dev||a.st_ino!=b.st_ino);
        tree(s.n,d.n,"");
        auto* receipt=result.mutable_copied();receipt->set_actual_bytes(bytes);receipt->set_actual_entries(static_cast<uint32_t>(entries));receipt->set_content_digest(hash->end());
    } catch(const Failure& e) {
        result.mutable_failure()->set_reason(e.reason);
    } catch(...) {
        result.mutable_failure()->set_reason(pb::FAILURE_INTERNAL);
    }
    return emit(std::move(result));
}
