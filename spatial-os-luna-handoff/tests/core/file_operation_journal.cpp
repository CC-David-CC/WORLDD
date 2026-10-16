#include "workspace/file_operation_journal.hpp"
#include "grants.pb.h"
#include <filesystem>
#include <array>
#include <sys/stat.h>
#include <iostream>
#include <stdexcept>
#include <string>
#include <unistd.h>

using namespace spatial;
using namespace spatial::workspace::v1;

namespace {
void check(bool ok,const char* message) { if(!ok) throw std::runtime_error(message); }
FileOperationBinding binding(std::string operation="operation-id-001") {
  FileOperationBinding value;
  value.workspace="workspace-one"; value.operation_id=std::move(operation);
  value.principal=std::string(64,'p'); value.worktree="tree-one";
  value.root_device=7; value.root_inode=19; value.right=spatial::authority::v1::FILE_CREATE;
  value.request_sha256=std::string(32,'d'); value.engine_incarnation=std::string(16,'e');
  return value;
}
FileActionReply outcome(const std::string& operation,FileActionError::Code code) {
  FileActionReply reply; reply.set_protocol_version(1); reply.set_operation_id(operation);
  reply.mutable_error()->set_code(code); reply.mutable_error()->set_recovery_operation_id(operation);
  return reply;
}
}
int main() {
  try {
    std::array<char,64> temporary{};
    const std::string pattern=(std::filesystem::temp_directory_path()/"spatial-file-journal-XXXXXX").string();
    if(pattern.size()+1>temporary.size()) throw std::runtime_error("temporary state path too long");
    std::copy(pattern.begin(),pattern.end(),temporary.begin());
    char* created=mkdtemp(temporary.data());
    if(!created) throw std::runtime_error("could not create journal state directory");
    auto path=std::filesystem::path(created);
    struct stat owned{};
    if(lstat(path.c_str(),&owned)!=0 || !S_ISDIR(owned.st_mode) || S_ISLNK(owned.st_mode) || path.filename().string().rfind("spatial-file-journal-",0)!=0)
      throw std::runtime_error("unsafe journal fixture directory");
    const FileOperationJournal::Observer observer=[]{};
    auto request=binding();
    {
      FileOperationJournal journal(path);
      check(journal.bind_root(request.worktree,request.root_device,request.root_inode,observer),"initial root pin failed");
      check(!journal.bind_root(request.worktree,request.root_device,request.root_inode+1,observer),"root rebinding was accepted");
      check(journal.find(request,observer).state==FileOperationRecord::State::absent,"new operation was not absent");
      journal.begin(request,observer);
    }
    {
      FileOperationJournal recovered(path);
      check(recovered.find(request,observer).state==FileOperationRecord::State::intent,"unresolved intent did not survive reopen");
      auto pending=outcome(request.operation_id,FileActionError::UNCERTAIN);
      recovered.finish(request,FileOperationRecord::State::uncertain,pending,observer);
      check(recovered.find(request,observer).state==FileOperationRecord::State::uncertain,"uncertain outcome did not persist");
      auto mismatch=request; mismatch.request_sha256=std::string(32,'x');
      check(recovered.find(mismatch,observer).state==FileOperationRecord::State::conflict,"changed request did not conflict");
      mismatch=request; mismatch.principal.back()='x';
      check(recovered.find(mismatch,observer).state==FileOperationRecord::State::conflict,"changed principal did not conflict");
    }
    auto terminal=binding("operation-id-002");
    {
      FileOperationJournal journal(path); journal.begin(terminal,observer);
      journal.finish(terminal,FileOperationRecord::State::complete,
                     outcome(terminal.operation_id,FileActionError::UNIMPLEMENTED),observer);
    }
    {
      FileOperationJournal recovered(path);
      auto prior=recovered.find(terminal,observer);
      check(prior.state==FileOperationRecord::State::complete && prior.reply.error().code()==FileActionError::UNIMPLEMENTED,
            "terminal historical outcome did not survive reopen");
    }
    std::filesystem::remove_all(path);
    return 0;
  } catch(const std::exception& error) {
    std::cerr<<error.what()<<'\n'; return 1;
  }
}
