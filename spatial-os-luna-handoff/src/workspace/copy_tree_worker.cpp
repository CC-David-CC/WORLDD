#include "copy_worker.pb.h"
#include <openssl/evp.h>
#include <dirent.h>
#include <fcntl.h>
#include <sys/stat.h>
#include <linux/stat.h>
#include <sys/syscall.h>
#include <sys/resource.h>
#include <unistd.h>
#include <cerrno>
#include <cstdint>
#include <cstring>
#include <algorithm>
#include <memory>
#include <string>
#include <stdexcept>
#include <vector>
#include <set>
#include <google/protobuf/unknown_field_set.h>
#ifdef SPATIAL_COPY_WORKER_TEST_FAULTS
#include <cstdlib>
#endif
namespace {
namespace pb = spatial::workspace::copyworker::v1;
struct Failure final : std::runtime_error {
    pb::FailureReason reason;
    explicit Failure(pb::FailureReason value) : std::runtime_error("copy worker rejected input"), reason(value) {}
};
void need(bool value, pb::FailureReason reason=pb::FAILURE_INTERNAL) {
    if (!value) throw Failure(reason);
}
struct Fd { int n=-1; explicit Fd(int x=-1):n(x){} ~Fd(){if(n>=0)close(n);} Fd(const Fd&)=delete; Fd& operator=(const Fd&)=delete; Fd(Fd&& other) noexcept:n(other.n){other.n=-1;} Fd& operator=(Fd&& other) noexcept {if(this!=&other){if(n>=0)close(n);n=other.n;other.n=-1;}return *this;} };
struct DirCloser { void operator()(DIR* d) const { if (d) closedir(d); } };
using Dir = std::unique_ptr<DIR,DirCloser>;
struct Hash {
    std::unique_ptr<EVP_MD_CTX, decltype(&EVP_MD_CTX_free)> c{EVP_MD_CTX_new(), EVP_MD_CTX_free};
    explicit Hash(const char* domain="spatial-copy-tree-v1",bool length_prefix=false){need(c&&EVP_DigestInit_ex(c.get(),EVP_sha256(),nullptr)==1);if(length_prefix)field(domain);else add(domain,std::strlen(domain));}
    void add(const void* p,size_t n){need(EVP_DigestUpdate(c.get(),p,n)==1);}
    void u64(uint64_t v){unsigned char b[8];for(int i=7;i>=0;--i){b[i]=static_cast<unsigned char>(v);v>>=8;}add(b,8);}
    void field(const std::string& s){u64(s.size());add(s.data(),s.size());}
    std::string end(){unsigned char b[32];unsigned n=0;need(EVP_DigestFinal_ex(c.get(),b,&n)==1&&n==32);return {reinterpret_cast<char*>(b),n};}
};
uint64_t bytes=0,entries=0,enumerated=0,max_bytes=0,max_entries=0;
unsigned max_depth=128;
bool cabinet_mode=false;
dev_t cabinet_source_device=0;
uint64_t cabinet_source_mount_id=0;
uint64_t cabinet_generation(const struct stat& st,bool directory) {
 uint64_t gen=static_cast<uint64_t>(st.st_ino)*0x9e3779b185ebca87ULL;
 gen^=static_cast<uint64_t>(st.st_dev)+(gen<<6)+(gen>>2);
 if(!directory){gen^=static_cast<uint64_t>(st.st_ctim.tv_sec)+(gen<<6)+(gen>>2);
  gen^=static_cast<uint64_t>(st.st_ctim.tv_nsec)+(gen<<6)+(gen>>2);gen^=static_cast<uint64_t>(st.st_size)+(gen<<6)+(gen>>2);}
 return gen?gen:1;
}
#ifdef SPATIAL_COPY_WORKER_TEST_FAULTS
long fail_readdir_after=-1;
bool readdir_fault_used=false;
#endif
std::unique_ptr<Hash> hash;
uint64_t mount_id(int fd) {
    struct statx info{};
    if(syscall(SYS_statx,fd,"",AT_EMPTY_PATH|AT_STATX_SYNC_AS_STAT,STATX_MNT_ID,&info)!=0 ||
       !(info.stx_mask&STATX_MNT_ID)) throw Failure(pb::FAILURE_IO);
    return info.stx_mnt_id;
}
void copy_file(int s,int d,const std::string& name,const struct stat& st,const std::string& output_name={}) {
    const auto& destination_name=output_name.empty()?name:output_name;
    if(st.st_nlink!=1)throw Failure(pb::FAILURE_HARD_LINK);
    if(st.st_size<0||uint64_t(st.st_size)>max_bytes-bytes)throw Failure(pb::FAILURE_BYTE_LIMIT);
    hash->u64(static_cast<uint64_t>(st.st_size));
    Fd a(openat(s,name.c_str(),O_RDONLY|O_NOFOLLOW|O_CLOEXEC|O_NONBLOCK));
    if(a.n<0) throw Failure(errno==ENOENT?pb::FAILURE_SOURCE_CHANGED:pb::FAILURE_IO);
    if(cabinet_mode&&mount_id(a.n)!=cabinet_source_mount_id)throw Failure(pb::FAILURE_SPECIAL_ENTRY);
    Fd b(openat(d,destination_name.c_str(),O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW|O_CLOEXEC,0600));
    if(b.n<0) throw Failure(errno==EEXIST?pb::FAILURE_DESTINATION_CONFLICT:pb::FAILURE_IO);
    struct stat now{};
    if(fstat(a.n,&now)<0) throw Failure(pb::FAILURE_IO);
    if(now.st_dev!=st.st_dev||now.st_ino!=st.st_ino||!S_ISREG(now.st_mode)||now.st_nlink!=1)
        throw Failure(pb::FAILURE_SOURCE_CHANGED);
    char buf[65536];uint64_t count=0;
    for(;;) {
        ssize_t r=read(a.n,buf,sizeof buf);if(r<0&&errno==EINTR)continue;
        if(r<0)throw Failure(pb::FAILURE_IO);
        if(!r)break;
        if(uint64_t(r)>max_bytes-bytes)throw Failure(pb::FAILURE_BYTE_LIMIT);
        hash->add(buf,r);ssize_t off=0;
        while(off<r){ssize_t w=write(b.n,buf+off,r-off);if(w<0&&errno==EINTR)continue;
            if(w<=0) throw Failure(pb::FAILURE_IO);
            off+=w;
        }
        count+=r;bytes+=r;
    }
    if(count!=uint64_t(st.st_size))throw Failure(pb::FAILURE_SOURCE_CHANGED);
    if(cabinet_mode){struct stat after{};if(fstat(a.n,&after)!=0||after.st_dev!=st.st_dev||after.st_ino!=st.st_ino||after.st_nlink!=st.st_nlink||after.st_size!=st.st_size||after.st_mode!=st.st_mode||after.st_mtim.tv_sec!=st.st_mtim.tv_sec||after.st_mtim.tv_nsec!=st.st_mtim.tv_nsec||after.st_ctim.tv_sec!=st.st_ctim.tv_sec||after.st_ctim.tv_nsec!=st.st_ctim.tv_nsec)throw Failure(pb::FAILURE_SOURCE_CHANGED);}
    if(fchmod(b.n,st.st_mode&0777)!=0||fsync(b.n)!=0)throw Failure(pb::FAILURE_IO);
}
void tree(int s,int d,const std::string& prefix,unsigned depth=0) {
    struct stat directory_before{};if(cabinet_mode&&fstat(s,&directory_before)!=0)throw Failure(pb::FAILURE_IO);
    need(depth<=128,pb::FAILURE_DEPTH_LIMIT);
    need(depth<=max_depth,pb::FAILURE_DESCRIPTOR_LIMIT);
    Fd dupfd(dup(s));if(dupfd.n<0)throw Failure(pb::FAILURE_IO);
    Dir dir(fdopendir(dupfd.n));if(!dir)throw Failure(pb::FAILURE_IO);dupfd.n=-1;
    std::vector<std::string> names;
    for(;;) {
        dirent* e=nullptr;
#ifdef SPATIAL_COPY_WORKER_TEST_FAULTS
        if(!readdir_fault_used&&fail_readdir_after>=0&&enumerated>=static_cast<uint64_t>(fail_readdir_after)) {
            errno=EIO;readdir_fault_used=true;
        } else
#endif
        {
            errno=0;e=readdir(dir.get());
            if(!e&&errno!=0)throw Failure(pb::FAILURE_IO);
        }
#ifdef SPATIAL_COPY_WORKER_TEST_FAULTS
        if(readdir_fault_used&&errno==EIO&&e==nullptr)throw Failure(pb::FAILURE_IO);
#endif
        if(!e)break;
        std::string n=e->d_name;
        if(n=="."||n=="..")continue;
        if(++enumerated>max_entries)throw Failure(pb::FAILURE_ENTRY_LIMIT);
        names.push_back(std::move(n));
    }
    std::sort(names.begin(),names.end(),[](const auto&a,const auto&b){return std::lexicographical_compare(a.begin(),a.end(),b.begin(),b.end(),[](unsigned char x,unsigned char y){return x<y;});});
    for(const auto& n:names) {
        need(n.find('/')==std::string::npos&&n.find('\0')==std::string::npos);
        struct stat st{};
        if(fstatat(s,n.c_str(),&st,AT_SYMLINK_NOFOLLOW)!=0)
            throw Failure(errno==ENOENT?pb::FAILURE_SOURCE_CHANGED:pb::FAILURE_IO);
        if(S_ISLNK(st.st_mode))throw Failure(pb::FAILURE_SYMLINK);
        if(cabinet_mode && st.st_dev!=cabinet_source_device)throw Failure(pb::FAILURE_SPECIAL_ENTRY);
        if(++entries>max_entries)throw Failure(pb::FAILURE_ENTRY_LIMIT);
        auto rel=prefix.empty()?n:prefix+"/"+n;
        hash->field(rel);
        unsigned char type=S_ISDIR(st.st_mode)?'D':S_ISREG(st.st_mode)?'F':'?';
        if(type=='?')throw Failure(pb::FAILURE_SPECIAL_ENTRY);
        hash->add(&type,1);hash->u64(st.st_mode&0777);
        if(type=='D') {
            if(mkdirat(d,n.c_str(),0700)!=0)
                throw Failure(errno==EEXIST?pb::FAILURE_DESTINATION_CONFLICT:pb::FAILURE_IO);
            Fd a(openat(s,n.c_str(),O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC));
            Fd b(openat(d,n.c_str(),O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC));
            if(a.n<0||b.n<0)throw Failure(pb::FAILURE_IO);
            struct stat opened{};
            if(fstat(a.n,&opened)<0)throw Failure(pb::FAILURE_IO);
            if(opened.st_dev!=st.st_dev||opened.st_ino!=st.st_ino)throw Failure(pb::FAILURE_SOURCE_CHANGED);
            if(cabinet_mode&&mount_id(a.n)!=cabinet_source_mount_id)throw Failure(pb::FAILURE_SPECIAL_ENTRY);
            tree(a.n,b.n,rel,depth+1);
            if(fchmod(b.n,st.st_mode&0777)!=0||fsync(b.n)!=0)throw Failure(pb::FAILURE_IO);
        } else {
            copy_file(s,d,n,st);
        }
    }
    if(fsync(d)!=0)throw Failure(pb::FAILURE_IO);
    if(cabinet_mode){struct stat directory_after{};if(fstat(s,&directory_after)!=0||directory_after.st_dev!=directory_before.st_dev||directory_after.st_ino!=directory_before.st_ino||directory_after.st_mode!=directory_before.st_mode||directory_after.st_size!=directory_before.st_size||directory_after.st_mtim.tv_sec!=directory_before.st_mtim.tv_sec||directory_after.st_mtim.tv_nsec!=directory_before.st_mtim.tv_nsec||directory_after.st_ctim.tv_sec!=directory_before.st_ctim.tv_sec||directory_after.st_ctim.tv_nsec!=directory_before.st_ctim.tv_nsec)throw Failure(pb::FAILURE_SOURCE_CHANGED);}
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
        Fd request_fd(open("/job.pb",O_RDONLY|O_NOFOLLOW|O_CLOEXEC));
        if(request_fd.n<0)throw Failure(pb::FAILURE_IO);
        struct stat st{};if(fstat(request_fd.n,&st)!=0)throw Failure(pb::FAILURE_IO);
        need(S_ISREG(st.st_mode)&&st.st_size>0&&st.st_size<=4096);
        std::string input(static_cast<size_t>(st.st_size),'\0');size_t used=0;
        while(used<input.size()){ssize_t n=read(request_fd.n,input.data()+used,input.size()-used);if(n<0&&errno==EINTR)continue;
            if(n<=0) throw Failure(pb::FAILURE_IO);
            used+=n;
        }
        pb::WorkerRequest request;need(request.ParseFromString(input)&&request.maximum_bytes()>0&&request.maximum_bytes()<=4ULL*1024*1024*1024&&request.maximum_entries()>0&&request.maximum_entries()<=200000);
        need(request.GetReflection()->GetUnknownFields(request).empty());
        const bool exporting=request.protocol_version()==2 && request.has_state_export() && !request.has_finalization();
        const bool cabinet=request.protocol_version()==3 && !request.has_state_export() && !request.has_finalization() && !request.cabinet_source_name().empty() && !request.cabinet_destination_name().empty();
        need(exporting || cabinet || (request.protocol_version()==1 && !request.has_state_export() && !request.has_finalization() && request.cabinet_source_name().empty() && request.cabinet_destination_name().empty()));
        cabinet_mode=cabinet;
        result.set_protocol_version(request.protocol_version());
        if(exporting) {
            const auto& state=request.state_export();
            need(state.GetReflection()->GetUnknownFields(state).empty() &&
                 !state.key().empty() && state.key().size()<=64 &&
                 state.key().find_first_not_of("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")==std::string::npos &&
                 state.schema_id()=="spatial.application.quiesced-files" && state.schema_version()==1 &&
                 state.subdomains_size()>0 && state.subdomains_size()<=2);
            std::set<unsigned> selected;
            for(const auto domain:state.subdomains())need((domain==1 || domain==2) && selected.insert(domain).second);
        }
        max_bytes=request.maximum_bytes();max_entries=request.maximum_entries();
        struct rlimit nofile{};
        need(getrlimit(RLIMIT_NOFILE,&nofile)==0,pb::FAILURE_IO);
        constexpr rlim_t kDescriptorReserve=16;
        if(nofile.rlim_cur!=RLIM_INFINITY) {
            max_depth=nofile.rlim_cur>kDescriptorReserve
                ? static_cast<unsigned>(std::min<rlim_t>(128,(nofile.rlim_cur-kDescriptorReserve)/3))
                : 0;
        }
#ifdef SPATIAL_COPY_WORKER_TEST_FAULTS
        if(const char* fault=std::getenv("SPATIAL_COPY_TEST_FAIL_READDIR_AFTER")) {
            char* end=nullptr;errno=0;const long parsed=std::strtol(fault,&end,10);
            need(errno==0&&end&&*end=='\0'&&parsed>=0&&parsed<=200000);
            fail_readdir_after=parsed;
        }
#endif
        hash=cabinet?std::make_unique<Hash>("spatial-cabinet-copy-v1",true):std::make_unique<Hash>();
        Fd s(open("/source",O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC)),d(open("/destination",O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC));
        if(s.n<0||d.n<0)throw Failure(pb::FAILURE_IO);
        struct stat a{},b{};if(fstat(s.n,&a)!=0||fstat(d.n,&b)!=0)throw Failure(pb::FAILURE_IO);
        cabinet_source_device=a.st_dev;
        if(cabinet)cabinet_source_mount_id=mount_id(s.n);
        if(!cabinet)need(a.st_dev!=b.st_dev||a.st_ino!=b.st_ino);
        if(cabinet) {
            const auto& source_name=request.cabinet_source_name();const auto& destination_name=request.cabinet_destination_name();
            need(source_name.size()<=255 && destination_name.size()<=255 && source_name.find_first_of("/\0",0,2)==std::string::npos && destination_name.find_first_of("/\0",0,2)==std::string::npos && source_name!="." && source_name!=".." && destination_name!="." && destination_name!="..");
            struct stat before{};if(fstatat(s.n,source_name.c_str(),&before,AT_SYMLINK_NOFOLLOW)!=0)throw Failure(pb::FAILURE_SOURCE_CHANGED);
            if(S_ISLNK(before.st_mode))throw Failure(pb::FAILURE_SYMLINK);
            if(request.cabinet_source_is_directory()!=S_ISDIR(before.st_mode) || (!S_ISDIR(before.st_mode)&&!S_ISREG(before.st_mode)))throw Failure(pb::FAILURE_SPECIAL_ENTRY);
            if(before.st_dev!=a.st_dev)throw Failure(pb::FAILURE_SPECIAL_ENTRY);
            if(request.expected_source_device()!=0 || request.expected_source_inode()!=0 || request.expected_source_generation()!=0) {
                if(request.expected_source_device()!=static_cast<std::uint64_t>(before.st_dev) ||
                   request.expected_source_inode()!=static_cast<std::uint64_t>(before.st_ino) ||
                   request.expected_source_generation()!=cabinet_generation(before,S_ISDIR(before.st_mode)))
                    throw Failure(pb::FAILURE_SOURCE_CHANGED);
            }
            if(++entries>max_entries||++enumerated>max_entries)throw Failure(pb::FAILURE_ENTRY_LIMIT);
            hash->field("");const unsigned char type=S_ISDIR(before.st_mode)?'D':'F';hash->add(&type,1);hash->u64(before.st_mode&0777);
            if(S_ISDIR(before.st_mode)){
                Fd source_root(openat(s.n,source_name.c_str(),O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC));
                struct stat pinned{};if(source_root.n<0||fstat(source_root.n,&pinned)!=0||pinned.st_dev!=before.st_dev||pinned.st_ino!=before.st_ino)throw Failure(pb::FAILURE_SOURCE_CHANGED);
                if(mount_id(source_root.n)!=cabinet_source_mount_id)throw Failure(pb::FAILURE_SPECIAL_ENTRY);
                if (mkdirat(d.n,destination_name.c_str(),0700) != 0)
                    throw Failure(pb::FAILURE_DESTINATION_CONFLICT);
                Fd output_root(openat(d.n,destination_name.c_str(),
                    O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC));
                if (output_root.n < 0) throw Failure(pb::FAILURE_IO);
                tree(source_root.n,output_root.n,"");
                if (fchmod(output_root.n,before.st_mode&0777) != 0 ||
                    fsync(output_root.n) != 0 || fsync(d.n) != 0)
                    throw Failure(pb::FAILURE_IO);
            } else {
                Fd source_file(openat(s.n,source_name.c_str(),O_RDONLY|O_NOFOLLOW|O_CLOEXEC|O_NONBLOCK));
                if(source_file.n<0)throw Failure(pb::FAILURE_SOURCE_CHANGED);
                struct stat pinned{};if(fstat(source_file.n,&pinned)!=0||pinned.st_dev!=before.st_dev||pinned.st_ino!=before.st_ino)throw Failure(pb::FAILURE_SOURCE_CHANGED);
                if(mount_id(source_file.n)!=cabinet_source_mount_id)throw Failure(pb::FAILURE_SPECIAL_ENTRY);
                if(pinned.st_nlink!=1)throw Failure(pb::FAILURE_HARD_LINK);
                if(pinned.st_size<0||static_cast<uint64_t>(pinned.st_size)>max_bytes)throw Failure(pb::FAILURE_BYTE_LIMIT);
                hash->u64(static_cast<uint64_t>(pinned.st_size));
                Fd output(openat(d.n,destination_name.c_str(),O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW|O_CLOEXEC,0600));if(output.n<0)throw Failure(pb::FAILURE_DESTINATION_CONFLICT);
                char buffer[65536];uint64_t copied=0;for(;;){ssize_t n=read(source_file.n,buffer,sizeof buffer);if(n<0&&errno==EINTR)continue;if(n<0)throw Failure(pb::FAILURE_IO);if(!n)break;if(static_cast<uint64_t>(n)>max_bytes-bytes)throw Failure(pb::FAILURE_BYTE_LIMIT);hash->add(buffer,n);ssize_t off=0;while(off<n){ssize_t w=write(output.n,buffer+off,n-off);if(w<0&&errno==EINTR)continue;if(w<=0)throw Failure(pb::FAILURE_IO);off+=w;}copied+=n;bytes+=n;}
                struct stat after{};if(copied!=static_cast<uint64_t>(pinned.st_size)||fstat(source_file.n,&after)!=0||after.st_dev!=pinned.st_dev||after.st_ino!=pinned.st_ino||after.st_nlink!=pinned.st_nlink||after.st_size!=pinned.st_size||after.st_mode!=pinned.st_mode||after.st_mtim.tv_sec!=pinned.st_mtim.tv_sec||after.st_mtim.tv_nsec!=pinned.st_mtim.tv_nsec||after.st_ctim.tv_sec!=pinned.st_ctim.tv_sec||after.st_ctim.tv_nsec!=pinned.st_ctim.tv_nsec)throw Failure(pb::FAILURE_SOURCE_CHANGED);
                if(fchmod(output.n,pinned.st_mode&0777)!=0||fsync(output.n)!=0||fsync(d.n)!=0)throw Failure(pb::FAILURE_IO);
            }
        } else if(exporting) {
            // The trusted contract selects only these persistent roots. Runtime home,
            // cache, temporary files, logs, credentials, and process records have no export path.
            hash->field("spatial-application-state-export-v2");
            hash->field(request.state_export().SerializeAsString());
            std::set<unsigned> selected(request.state_export().subdomains().begin(),request.state_export().subdomains().end());
            for(const auto domain:selected) {
                const char* name=domain==1?"data":"config";
                struct stat before{};need(fstatat(s.n,name,&before,AT_SYMLINK_NOFOLLOW)==0 && S_ISDIR(before.st_mode),pb::FAILURE_SOURCE_CHANGED);
                need(++entries<=max_entries && ++enumerated<=max_entries,pb::FAILURE_ENTRY_LIMIT);
                hash->field(name);const unsigned char type='D';hash->add(&type,1);hash->u64(before.st_mode&0777);
                need(mkdirat(d.n,name,0700)==0,pb::FAILURE_DESTINATION_CONFLICT);
                Fd input(openat(s.n,name,O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC));
                Fd output(openat(d.n,name,O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC));
                struct stat pinned{};
                need(input.n>=0 && output.n>=0 && fstat(input.n,&pinned)==0 &&
                     before.st_dev==pinned.st_dev && before.st_ino==pinned.st_ino,pb::FAILURE_SOURCE_CHANGED);
                tree(input.n,output.n,name,1);
                need(fchmod(output.n,before.st_mode&0777)==0 && fsync(output.n)==0,pb::FAILURE_IO);
            }
            need(fsync(d.n)==0,pb::FAILURE_IO);
        } else tree(s.n,d.n,"");
        auto* receipt=result.mutable_copied();receipt->set_actual_bytes(bytes);receipt->set_actual_entries(static_cast<uint32_t>(entries));receipt->set_content_digest(hash->end());
    } catch(const Failure& e) {
        result.mutable_failure()->set_reason(e.reason);
    } catch(...) {
        result.mutable_failure()->set_reason(pb::FAILURE_INTERNAL);
    }
    return emit(std::move(result));
}
