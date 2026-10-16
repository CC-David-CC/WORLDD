#include "copy_worker.pb.h"
#include "storage.pb.h"
#include "workspace/copy_collections.hpp"
#include "core/protocol.hpp"
#include <openssl/evp.h>
#include <algorithm>
#include <array>
#include <cerrno>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <set>
#include <map>
#include <vector>
#include <stdexcept>
#include <sys/stat.h>
#include <sys/wait.h>
#include <fcntl.h>
#include <unistd.h>

namespace {
namespace pb=spatial::workspace::copyworker::v1;
namespace cp=spatial::workspace::copy::v1;
namespace fs=std::filesystem;
void require(bool value,const char* message){if(!value)throw std::runtime_error(message);}
std::string read_file(const fs::path& path,std::size_t maximum,nlink_t links=1) {
 struct stat st{};require(lstat(path.c_str(),&st)==0 && S_ISREG(st.st_mode) && st.st_nlink==links &&
   st.st_size>=0 && static_cast<std::uint64_t>(st.st_size)<=maximum,"Invalid finalization input file");
 std::ifstream in(path,std::ios::binary);std::string bytes((std::istreambuf_iterator<char>(in)),{});
 require(!in.bad() && bytes.size()==static_cast<std::size_t>(st.st_size),"Finalization input changed");return bytes;
}
void write_file(const fs::path& path,const std::string& bytes) {
 const int fd=open(path.c_str(),O_WRONLY|O_CREAT|O_TRUNC|O_NOFOLLOW|O_CLOEXEC,0600);require(fd>=0,"Cannot write fresh Git link");
 std::size_t used=0;try{while(used<bytes.size()){auto n=write(fd,bytes.data()+used,bytes.size()-used);if(n<0&&errno==EINTR)continue;require(n>0,"Cannot write fresh Git bytes");used+=n;}require(fsync(fd)==0,"Cannot sync fresh Git bytes");}catch(...){close(fd);throw;}close(fd);
}
std::string line(std::string bytes){if(!bytes.empty() && bytes.back()=='\n')bytes.pop_back();require(bytes.find_first_of("\r\n\0",0,3)==std::string::npos,"Malformed Git administrative record");return bytes;}
std::string digest(const std::string& bytes) {
 std::array<unsigned char,32> out{};unsigned size=0;
 require(EVP_Digest(bytes.data(),bytes.size(),out.data(),&size,EVP_sha256(),nullptr)==1 && size==out.size(),"Cannot digest companion contract");
 return {reinterpret_cast<char*>(out.data()),out.size()};
}
std::string git(const std::vector<std::string>& arguments) {
 int pipes[2];require(pipe2(pipes,O_CLOEXEC)==0,"Cannot capture confined Git output");
 const auto pid=fork();require(pid>=0,"Cannot fork confined Git validation");
 if(pid==0){close(pipes[0]);dup2(pipes[1],1);close(pipes[1]);
   const int null=open("/dev/null",O_RDWR|O_CLOEXEC);dup2(null,0);dup2(null,2);close(null);
   std::vector<std::string> argv{"git","-c","core.hooksPath=/nonexistent","-c","core.fsmonitor=false","-c","protocol.allow=never"};
   argv.insert(argv.end(),arguments.begin(),arguments.end());std::vector<char*> pointers;
   for(auto& arg:argv){pointers.push_back(arg.data());}
   pointers.push_back(nullptr);execv("/usr/bin/git",pointers.data());_exit(127);
 }
 close(pipes[1]);std::string output;char bytes[4096];
 for(;;){auto n=read(pipes[0],bytes,sizeof(bytes));if(n<0 && errno==EINTR)continue;require(n>=0,"Git validation read failed");if(!n)break;require(output.size()+n<=1024*1024,"Git validation output exceeds bound");output.append(bytes,n);}
 close(pipes[0]);int status=0;while(waitpid(pid,&status,0)<0)require(errno==EINTR,"Git validation wait failed");
 require(WIFEXITED(status) && WEXITSTATUS(status)==0,"Independent Git repository validation failed");return output;
}
struct Hash {
 EVP_MD_CTX* context=EVP_MD_CTX_new();std::uint64_t bytes=0;std::uint32_t entries=0;
 std::uint64_t max_bytes;std::uint32_t max_entries;
 std::array<char,65536> buffer{};
 Hash(std::uint64_t b,std::uint32_t e):max_bytes(b),max_entries(e){require(context && EVP_DigestInit_ex(context,EVP_sha256(),nullptr)==1,"Cannot initialize validation digest");}
 ~Hash(){EVP_MD_CTX_free(context);}
 void add(const std::string& value){require(EVP_DigestUpdate(context,value.data(),value.size())==1,"Cannot digest validation bytes");}
 void tree(const fs::path& root,const std::string& relative="",unsigned depth=0) {
  require(depth<=128,"Finalization directory depth exceeded");std::vector<fs::path> paths;
  for(const auto& entry:fs::directory_iterator(root)){require(paths.size()<max_entries,"Finalization entry limit exceeded");paths.push_back(entry.path());}
  std::sort(paths.begin(),paths.end());
  for(const auto& path:paths){struct stat st{};require(lstat(path.c_str(),&st)==0,"Cannot inspect finalization tree");
   require(++entries<=max_entries,"Finalization entry limit exceeded");
   const auto name=relative+path.filename().string();add(std::to_string(name.size())+":"+name+":"+std::to_string(st.st_mode&0777)+":");
   if(S_ISDIR(st.st_mode)){add("D");tree(path,name+"/",depth+1);}
   else {require(S_ISREG(st.st_mode) && st.st_nlink==1 && st.st_size>=0,"Finalization rejects links and special entries");
    require(static_cast<std::uint64_t>(st.st_size)<=max_bytes-bytes,"Finalization byte limit exceeded");
    add("F"+std::to_string(st.st_size)+":");
    std::ifstream in(path,std::ios::binary);std::uint64_t count=0;
    while(in){in.read(buffer.data(),buffer.size());const auto n=in.gcount();require(EVP_DigestUpdate(context,buffer.data(),n)==1,"Cannot digest finalization data");count+=n;}
    require(in.eof() && count==static_cast<std::uint64_t>(st.st_size),"Finalization file changed");bytes+=count;
   }
  }
  const int fd=open(root.c_str(),O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC);require(fd>=0,"Cannot pin finalization directory");const auto synced=fsync(fd);close(fd);require(synced==0,"Cannot sync finalization directory");
 }
 std::string finish(){std::array<unsigned char,32> value{};unsigned n=0;require(EVP_DigestFinal_ex(context,value.data(),&n)==1 && n==32,"Cannot finalize validation digest");return {reinterpret_cast<char*>(value.data()),value.size()};}
};
void typed_domain(int data_class,const fs::path& root,const pb::FinalizeRequest& job) {
 using namespace spatial;
 if(data_class==cp::APPLICATION_STATE)require(job.state_collection_version()==2,"Raw application-state schema1 cannot be exported");
 if(data_class!=cp::APPLICATION_DEFINITION && data_class!=cp::ENVIRONMENT_CONFIG && data_class!=cp::APPLICATION_STATE)return;
 std::size_t count=0;for(const auto& entry:fs::directory_iterator(root)){require(entry.path().filename()=="export.pb","Typed domain contains undeclared content");++count;}
 require(count==1,"Typed export requires its versioned export.pb record");const auto bytes=read_file(root/"export.pb",65536);
 if(data_class==cp::APPLICATION_DEFINITION) {
  if(job.definition_collection_version()==2) {
   workspace::v1::CopyApplicationDefinitionCollection value;require(value.ParseFromString(bytes),"Malformed definition collection");
   copy_collections::definitions(value);require(copy_collections::profile_name(job.destination_application_profile()),"Independent destination application profile is invalid");
   std::map<std::string,std::string> contracts;
   for(auto& app:*value.mutable_applications()) {
    contracts.emplace(app.key(),digest(app.state_contract().SerializeAsString()));
    app.set_profile(job.destination_application_profile());
   }
   if(job.state_companions_size()>0) {
    require(contracts.size()==static_cast<std::size_t>(job.state_companions_size()),"Definition/state collection keys differ");
    for(const auto& state:job.state_companions()) {
     const auto found=contracts.find(state.key());
     require(found!=contracts.end() && found->second==state.contract_digest(),"Definition/state collection contracts differ");contracts.erase(found);
    }
    require(contracts.empty(),"State companions omitted a selected definition");
   }
   copy_collections::definitions(value);write_file(root/"export.pb",value.SerializeAsString());
  } else {
   require(job.definition_collection_version()==1,"Unknown definition collection version");
   workspace::v1::CopyApplicationDefinition value;require(value.ParseFromString(bytes),"Malformed application definition");
   copy_collections::definition(value);
  }
 } else if(data_class==cp::APPLICATION_STATE) {
  require(job.state_collection_version()==2 && job.state_companions_size()>0 && job.state_companions_size()<=32,
          "State collection omitted its bounded real companion exports");
  workspace::v1::CopyApplicationStateCollection value;require(value.ParseFromString(bytes),"Malformed state collection");
  const bool published_source=value.slots_size()>0 && value.slots(0).has_state_object();
  copy_collections::states(value,published_source);require(value.slots_size()==job.state_companions_size(),"State collection companion count differs");
  std::map<std::string,workspace::v1::CopyStateSlot*> slots;
  for(auto& slot:*value.mutable_slots())slots.emplace(slot.key(),&slot);
  for(const auto& state:job.state_companions()) {
   const auto found=slots.find(state.key());require(found!=slots.end(),"State collection companion key is missing or duplicated");
   auto& slot=*found->second;require(state.contract_digest()==digest(slot.contract().SerializeAsString()),"Companion export contract differs");
   workspace::v1::StorageObject source,snapshot,destination;
   require(source.ParseFromString(state.source_storage_object()) && source.SerializeAsString()==state.source_storage_object() &&
           snapshot.ParseFromString(state.snapshot_storage_object()) && snapshot.SerializeAsString()==state.snapshot_storage_object() &&
           destination.ParseFromString(state.destination_storage_object()) && destination.SerializeAsString()==state.destination_storage_object() &&
           source.kind()==workspace::v1::StorageObject::APPLICATION_STATE && snapshot.kind()==workspace::v1::StorageObject::SNAPSHOT &&
           destination.kind()==workspace::v1::StorageObject::APPLICATION_STATE &&
           copy_collections::object_id(source.id()) && copy_collections::object_id(snapshot.id()) && copy_collections::object_id(destination.id()) &&
           source.id()!=snapshot.id() && source.id()!=destination.id() && snapshot.id()!=destination.id() && state.content_digest().size()==32,
           "Companion export object identities or digest are invalid");
   require(!published_source || slot.state_object().SerializeAsString()==source.SerializeAsString(),
           "Published source descriptor differs from its current pinned state mapping");
   *slot.mutable_state_object()=destination;slot.set_content_digest(state.content_digest());
   slot.set_actual_bytes(state.actual_bytes());slot.set_actual_entries(state.actual_entries());slots.erase(found);
  }
  require(slots.empty(),"State collection omitted a declared slot");copy_collections::states(value,true);
  write_file(root/"export.pb",value.SerializeAsString());
 } else {
  workspace::v1::CopyEnvironmentConfiguration value;
  require(value.ParseFromString(bytes),"Malformed environment export");validate_message(value);
  require(value.schema_version()==1 && value.dependencies_size()<=256,"Unsupported typed environment export");std::set<std::string> names;
  for(const auto& dependency:value.dependencies())require(!dependency.name().empty() && dependency.name().size()<=128 &&
      names.insert(dependency.name()).second && !dependency.version().empty() && dependency.version().size()<=128 &&
      dependency.content_digest().size()==32,"Malformed reproducible dependency");
 }
}
void copy_index(const fs::path& source,const fs::path& destination,std::uint64_t maximum) {
 const int input=open(source.c_str(),O_RDONLY|O_NOFOLLOW|O_CLOEXEC);
 struct stat st{};require(input>=0 && fstat(input,&st)==0 && S_ISREG(st.st_mode) && st.st_nlink==1 &&
     st.st_size>=0 && static_cast<std::uint64_t>(st.st_size)<=maximum,"Source index exceeds the operation bound");
 const int output=open(destination.c_str(),O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW|O_CLOEXEC,0600);
 if(output<0){close(input);throw std::runtime_error("Cannot allocate fresh independent index");}
 try {
  std::array<char,65536> buffer{};std::uint64_t copied=0;
  for(;;){auto n=read(input,buffer.data(),buffer.size());if(n<0&&errno==EINTR)continue;require(n>=0,"Cannot read source index");if(!n)break;
   require(static_cast<std::uint64_t>(n)<=maximum-copied,"Source index grew beyond its bound");std::size_t used=0;
   while(used<static_cast<std::size_t>(n)){auto w=write(output,buffer.data()+used,n-used);if(w<0&&errno==EINTR)continue;require(w>0,"Cannot write fresh index");used+=w;}copied+=n;
  }
  require(copied==static_cast<std::uint64_t>(st.st_size) && fsync(output)==0,"Independent index copy is incomplete");
 }catch(...){close(input);close(output);throw;}
 close(input);close(output);
}
pb::FinalizeReceipt finalize(const pb::WorkerRequest& request) {
 const auto& job=request.finalization();std::set<int> classes;
 for(const auto data_class:job.classes())require(data_class>=cp::PROJECT_TREE && data_class<=cp::ENVIRONMENT_CONFIG && classes.insert(data_class).second,"Unknown finalization class");
 require(classes.contains(cp::PROJECT_TREE) && classes.contains(cp::GIT_HISTORY),"Finalization requires project/history pair");
 require(job.state_companions_size()<=32 && (job.state_companions_size()==0 || classes.contains(cp::APPLICATION_STATE)),
         "Finalization companion bounds or selected classes differ");
 require(classes.contains(cp::APPLICATION_STATE)?job.state_collection_version()==2:job.state_collection_version()==0,
         "Finalization state schema selection differs");
 require(classes.contains(cp::APPLICATION_DEFINITION)?(job.definition_collection_version()==1 || job.definition_collection_version()==2):job.definition_collection_version()==0,
         "Finalization definition schema selection differs");
 require(job.destination_admin_id().size()==32 && job.destination_admin_id().find_first_not_of("0123456789abcdef")==std::string::npos,"Invalid new Git admin identity");
 const fs::path repository="/domains/2",project="/domains/1";

 const auto link=line(read_file(project/".git",16384));const auto prefix="gitdir: "+job.source_repository_path()+"/worktrees/";
 require(link.starts_with(prefix),"Source project Git link escapes its registered repository");const auto old_admin=link.substr(prefix.size());
 require(!old_admin.empty() && old_admin.size()<=128 && old_admin!="." && old_admin!=".." && old_admin.find_first_not_of("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")==std::string::npos,"Malformed source Git worktree admin link");
 const auto admin=repository/"worktrees"/old_admin;
 const auto head=line(read_file(admin/"HEAD",4096));
 const bool symbolic=head.starts_with("ref: refs/heads/");
 const auto branch=symbolic?head.substr(16):std::string{};
 if(symbolic) {
  require(!branch.empty() && branch.size()<=1024,"Snapshot branch is invalid");
  git({"check-ref-format","refs/heads/"+branch});
 } else {
  require(head.size()==40 && head.find_first_not_of("0123456789abcdef")==std::string::npos,
          "Snapshot HEAD is neither a supported branch nor detached SHA1 commit");
 }
 require((job.derive_head_from_snapshot() && job.expected_branch().empty()) ||
         (!job.derive_head_from_snapshot() && symbolic && branch==job.expected_branch()),
         "Source snapshot differs from its explicit branch contract");
 require(line(read_file(admin/"commondir",4096))=="../..","Source common Git directory escapes its registered repository");
 for(const auto* forbidden:{"objects/info/alternates","objects/info/http-alternates","info/grafts","shallow"})
   require(!fs::exists(repository/forbidden),"Source repository requires unavailable external or rewritten history");
 for(const auto& entry:fs::recursive_directory_iterator(repository/"objects"))
   require(entry.path().extension()!=".promisor","Promisor object stores cannot provide independent complete history");
 // Build a fresh declared Git administration tree. Unknown sidecars, remotes,
 // reflogs, hooks, and application runtime records never enter the destination.
 const auto temporary_admin=repository/"spatial-copy-fresh";
 require(!fs::exists(temporary_admin),"Unexpected preexisting destination administrative staging");
 fs::create_directories(temporary_admin/"objects"/"pack");
 fs::create_directories(temporary_admin/"refs");
 const auto fresh=temporary_admin/"worktrees"/job.destination_admin_id();
 fs::create_directories(fresh);copy_index(admin/"index",fresh/"index",request.maximum_bytes());
 const auto hex=[](const std::string& value,std::size_t length){return value.size()==length && value.find_first_not_of("0123456789abcdef")==std::string::npos;};
 for(const auto& entry:fs::directory_iterator(repository/"objects")) {
  const auto component=entry.path().filename().string();
  if(component=="pack") {
   for(const auto& pack:fs::directory_iterator(entry.path())) {
    const auto name=pack.path().filename().string();const bool suffix=name.ends_with(".pack") || name.ends_with(".idx");
    const auto dot=name.rfind('.');
    if(!suffix || !name.starts_with("pack-") || dot==std::string::npos || !hex(name.substr(5,dot-5),40))continue;
    require(fs::is_regular_file(pack.symlink_status()),"Git pack store contains a non-file");
    fs::rename(pack.path(),temporary_admin/"objects"/"pack"/name);
   }
  } else if(hex(component,2)) {
   require(fs::is_directory(entry.symlink_status()),"Git loose object fanout is malformed");
   fs::create_directory(temporary_admin/"objects"/component);
   for(const auto& object:fs::directory_iterator(entry.path())) {
    const auto name=object.path().filename().string();
    if(!hex(name,38))continue;
    require(fs::is_regular_file(object.symlink_status()),"Git loose object store contains a non-file");
    fs::rename(object.path(),temporary_admin/"objects"/component/name);
   }
  }
 }
 // Refs and their object identities are the declared history namespace.
 fs::remove(temporary_admin/"refs");fs::rename(repository/"refs",temporary_admin/"refs");
 if(fs::exists(repository/"packed-refs"))fs::rename(repository/"packed-refs",temporary_admin/"packed-refs");
 write_file(temporary_admin/"HEAD",head+"\n");
 write_file(temporary_admin/"config","[core]\n\trepositoryformatversion = 0\n\tbare = true\n\tlogallrefupdates = true\n");
 write_file(fresh/"HEAD",head+"\n");write_file(fresh/"commondir","../..\n");
 write_file(fresh/"gitdir",job.destination_project_path()+"/.git\n");
 std::vector<fs::path> obsolete;
 for(const auto& entry:fs::directory_iterator(repository))if(entry.path()!=temporary_admin)obsolete.push_back(entry.path());
 for(const auto& path:obsolete)fs::remove_all(path);
 std::vector<fs::path> prepared;for(const auto& entry:fs::directory_iterator(temporary_admin))prepared.push_back(entry.path());
 for(const auto& path:prepared){fs::rename(path,repository/path.filename());}
 fs::remove(temporary_admin);
 const auto final_admin=repository/"worktrees"/job.destination_admin_id();
 write_file(project/".git","gitdir: "+job.destination_repository_path()+"/worktrees/"+job.destination_admin_id()+"\n");
 const auto git_dir="--git-dir="+final_admin.string();const auto work_tree="--work-tree="+project.string();
 const auto resolved=line(git({git_dir,work_tree,"rev-parse","HEAD"}));
 require((resolved.size()==40 || resolved.size()==64) && resolved.find_first_not_of("0123456789abcdef")==std::string::npos,"Invalid destination Git head");
 git({"--git-dir="+repository.string(),"fsck","--full","--strict","--no-reflogs"});
 git({git_dir,work_tree,"ls-files","--stage","-z"});
 pb::FinalizeReceipt result;result.set_head(resolved);result.set_branch(branch);
 result.set_head_kind(symbolic?pb::SYMBOLIC_BRANCH:pb::DETACHED_COMMIT);
 std::uint64_t remaining_bytes=request.maximum_bytes();std::uint32_t remaining_entries=request.maximum_entries();
 std::set<std::string> companion_keys,companion_ids;
 for(const auto& companion:job.state_companions()) {
  spatial::validate_message(companion);
  require(spatial::copy_collections::key(companion.key()) && companion_keys.insert(companion.key()).second &&
          companion_ids.insert(companion.source_storage_object()).second && companion_ids.insert(companion.snapshot_storage_object()).second &&
          companion_ids.insert(companion.destination_storage_object()).second && companion.content_digest().size()==32 &&
          companion.contract_digest().size()==32 && companion.actual_bytes()<=remaining_bytes && companion.actual_entries()<=remaining_entries,
          "Finalization companion identity/digest or aggregate bound differs");
  remaining_bytes-=companion.actual_bytes();remaining_entries-=companion.actual_entries();*result.add_state_companions()=companion;
 }
 for(const auto data_class:job.classes()) {
  const auto root=fs::path("/domains")/std::to_string(data_class);typed_domain(data_class,root,job);
  Hash hash(remaining_bytes,remaining_entries);hash.tree(root);auto* domain=result.add_domains();
  domain->set_data_class(static_cast<cp::CopyDataClass>(data_class));domain->set_actual_bytes(hash.bytes);domain->set_actual_entries(hash.entries);domain->set_content_digest(hash.finish());
  remaining_bytes-=hash.bytes;remaining_entries-=hash.entries;
 }
 return result;
}
}
int main() {
 pb::WorkerResult result;result.set_protocol_version(2);
 try {const auto bytes=read_file("/job.pb",16384,0);pb::WorkerRequest request;
  require(request.ParseFromString(bytes),"Malformed finalization request");spatial::validate_message(request);
  require(request.protocol_version()==2 && request.has_finalization() && !request.has_state_export() && request.maximum_bytes()>0 && request.maximum_bytes()<=(4ULL<<30) &&
          request.maximum_entries()>0 && request.maximum_entries()<=200000,"Unsupported bounded finalization request");
  *result.mutable_finalized()=finalize(request);
 } catch(const std::exception& error) {std::cerr<<error.what()<<'\n';result.mutable_failure()->set_reason(pb::FAILURE_INTERNAL);}
 try {spatial::write_frame(std::cout,result);return 0;}catch(...){return 2;}
}
