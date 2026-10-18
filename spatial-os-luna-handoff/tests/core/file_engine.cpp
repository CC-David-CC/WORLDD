#include "workspace/file_engine.hpp"

#include <fcntl.h>
#include <signal.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <unistd.h>

#include <cassert>
#include <cerrno>
#include <chrono>
#include <cstdlib>
#include <filesystem>
#include <iostream>
#include <new>
#include <string>

namespace {
// Fail allocation only after the real mutation becomes visible. This catches
// late protobuf allocations without replacing any engine or filesystem helper.
struct AllocationFault {
  bool armed = false;
  bool triggered = false;
  int directory = -1;
  const char* first = nullptr;
  const char* second = nullptr;
  bool after_delete = false;
} allocation_fault;
bool disarm_allocation_fault() noexcept {
  allocation_fault.armed = false;
  return allocation_fault.triggered;
}
bool fail_allocation_after_effect() noexcept {
  if (!allocation_fault.armed) return false;
  const int saved = errno;
  struct stat st {};
  const int first =
      fstatat(allocation_fault.directory, allocation_fault.first, &st, AT_SYMLINK_NOFOLLOW);
  bool observed = first == 0;
  if (allocation_fault.after_delete) {
    observed = first < 0 && errno == ENOENT;
    const int second =
        fstatat(allocation_fault.directory, allocation_fault.second, &st, AT_SYMLINK_NOFOLLOW);
    observed = observed && second < 0 && errno == ENOENT;
  }
  errno = saved;
  if (!observed) return false;
  allocation_fault.armed = false;
  allocation_fault.triggered = true;
  return true;
}
}  // namespace
void* operator new(std::size_t size) {
  if (fail_allocation_after_effect()) throw std::bad_alloc();
  if (void* pointer = std::malloc(size ? size : 1)) return pointer;
  throw std::bad_alloc();
}
void* operator new[](std::size_t size) { return ::operator new(size); }
void operator delete(void* pointer) noexcept { std::free(pointer); }
void operator delete[](void* pointer) noexcept { std::free(pointer); }
void operator delete(void* pointer, std::size_t) noexcept { std::free(pointer); }
void operator delete[](void* pointer, std::size_t) noexcept { std::free(pointer); }

namespace pb = spatial::workspace::v1;
namespace az = spatial::authority::v1;
struct Auth final : spatial::FileAuthority {
  bool allow = true;
  az::Right last = az::RIGHT_UNSPECIFIED;
  unsigned calls = 0;
  pb::ScopedAuthorityCheckReply check_fresh(const pb::ScopedAuthorityCheckRequest& q) override {
    ++calls;
    last = q.right();
    pb::ScopedAuthorityCheckReply r;
    r.set_allowed(allow);
    r.set_revision(calls);
    r.set_incarnation(std::string(16, 'i'));
    r.set_epoch(calls);
    return r;
  }
};
static pb::FileActionRequest request(std::uint64_t id) {
  pb::FileActionRequest r;
  r.set_protocol_version(1);
  r.set_operation_id(std::string(16, static_cast<char>(id)));
  r.set_workspace_id("workspace-a");
  r.mutable_principal()->set_issuer_id(std::string(32, 'i'));
  r.mutable_principal()->set_actor_id(std::string(32, 'a'));
  r.add_authority_components("cabinet-opaque-id");
  return r;
}
static std::string read_file(int root, const char* name) {
  int f = openat(root, name, O_RDONLY | O_CLOEXEC);
  assert(f >= 0);
  char b[64]{};
  const auto n = read(f, b, sizeof(b));
  close(f);
  assert(n >= 0);
  return {b, static_cast<std::size_t>(n)};
}
struct TempProject {
  std::string path;
  int root = -1;
  TempProject() {
    char pattern[] = "/tmp/spatial-file-repair-XXXXXX";
    const char* created = mkdtemp(pattern);
    assert(created);
    path = created;
    root = open(path.c_str(), O_RDONLY | O_DIRECTORY | O_CLOEXEC);
    assert(root >= 0);
  }
  ~TempProject() {
    close(root);
    std::filesystem::remove_all(path);
  }
};
static std::string operation_hex(const std::string& id) {
  std::string result;
  constexpr char digits[] = "0123456789abcdef";
  for (unsigned char byte : id) {
    result += digits[byte >> 4];
    result += digits[byte & 15];
  }
  return result;
}
static void allocator_interceptor_positive_control(int directory) {
  constexpr char marker[] = "allocation-interceptor-marker";
  const int marker_fd = openat(directory, marker, O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, 0600);
  assert(marker_fd >= 0);
  close(marker_fd);

  const int original_errno = errno;
  errno = EDOM;
  allocation_fault = {true, false, directory, marker, nullptr, false};
  bool threw_bad_alloc = false;
  void* allocation = nullptr;
  try {
    allocation = ::operator new(1);
  } catch (const std::bad_alloc&) {
    threw_bad_alloc = true;
  } catch (...) {
    allocation_fault.armed = false;
    errno = original_errno;
    throw;
  }
  const bool triggered = disarm_allocation_fault();
  const int observed_errno = errno;
  errno = original_errno;
  std::free(allocation);
  assert(threw_bad_alloc && triggered);
  assert(observed_errno == EDOM && errno == original_errno);
  std::cout << "allocator interceptor positive control: fired and threw std::bad_alloc\n";
}
static pb::FileActionReply create(spatial::FileEngine& engine, const char* name,
                                  std::uint64_t id = 100) {
  auto action = request(id);
  action.mutable_create()->set_basename(name);
  action.mutable_create()->set_content("data");
  return engine.handle(action);
}
static void recovery_requires_scope_cleanup_for_same_boot_root_replacement() {
  const std::string saved_boot(36, 'a');
  const std::string current_boot(36, 'a');
  const std::string prior_boot(36, 'b');
  assert(!spatial::file_engine_detail::can_skip_recovered_scope_cleanup(
      false, saved_boot, current_boot));
  assert(!spatial::file_engine_detail::can_skip_recovered_scope_cleanup(
      true, saved_boot, current_boot));
  assert(spatial::file_engine_detail::can_skip_recovered_scope_cleanup(
      false, saved_boot, prior_boot));
  assert(!spatial::file_engine_detail::can_skip_recovered_scope_cleanup(
      false, {}, current_boot));
  assert(!spatial::file_engine_detail::can_skip_recovered_scope_cleanup(
      false, saved_boot, {}));
}
static void copy_staging_root_is_reserved_and_stale_state_fails_closed() {
  TempProject project;
  Auth auth;
  assert(mkdirat(project.root, ".spatial-copy-staging", 0700) == 0);
  const int stage = openat(project.root, ".spatial-copy-staging",
                           O_RDONLY | O_DIRECTORY | O_CLOEXEC);
  assert(stage >= 0);
  assert(mkdirat(stage, ".job-stale", 0700) == 0);
  close(stage);
  bool rejected = false;
  try {
    spatial::FileEngine engine(project.root, "workspace-a", {"cabinet-opaque-id"}, auth);
  } catch (const std::invalid_argument&) {
    rejected = true;
  }
  assert(rejected);
  struct stat stale {};
  assert(fstatat(project.root, ".spatial-copy-staging/.job-stale", &stale,
                 AT_SYMLINK_NOFOLLOW) == 0 && S_ISDIR(stale.st_mode));
  const int stage_again = openat(project.root, ".spatial-copy-staging",
                                 O_RDONLY | O_DIRECTORY | O_CLOEXEC);
  assert(stage_again >= 0 && unlinkat(stage_again, ".job-stale", AT_REMOVEDIR) == 0);
  close(stage_again);
  spatial::FileEngine engine(project.root, "workspace-a", {"cabinet-opaque-id"}, auth);
  auto browse = request(98);
  browse.mutable_browse()->set_page_size(64);
  const auto listing = engine.handle(browse);
  assert(listing.has_directory());
  for (const auto& entry : listing.directory().entries())
    assert(entry.name() != ".spatial-copy-staging");
  auto create_reserved = request(99);
  create_reserved.mutable_create()->set_basename(".spatial-copy-staging");
  create_reserved.mutable_create()->set_content("must not replace broker staging");
  assert(engine.handle(create_reserved).error().code() ==
         pb::FileActionError::INVALID_ARGUMENT);
}

static pb::FileCopyJobJournalRecord durable_copy_start_intent(
    const std::string& job_id, const struct stat& root_identity) {
  pb::FileCopyJobJournalRecord record;
  record.set_format_version(2);
  record.set_workspace_id("workspace-a");
  record.set_job_id(job_id);
  record.set_initiating_operation_id(std::string(16, job_id[0]));
  record.set_initiating_request_sha256(std::string(32, 'd'));
  record.set_principal_issuer_id(std::string(32, 'i'));
  record.set_principal_actor_id(std::string(32, 'a'));
  record.set_worktree_id("cabinet-opaque-id");
  record.set_worktree_root_device(static_cast<std::uint64_t>(root_identity.st_dev));
  record.set_worktree_root_inode(static_cast<std::uint64_t>(root_identity.st_ino));
  record.set_exact_right(az::FILE_COPY);
  record.add_source_path_components("source.bin");
  auto* source=record.mutable_source();
  source->set_device(static_cast<std::uint64_t>(root_identity.st_dev));
  source->set_inode(static_cast<std::uint64_t>(root_identity.st_ino)+1);
  source->set_type(pb::FileCopyFilesystemIdentity::REGULAR_FILE);
  auto* destination=record.mutable_destination_directory();
  destination->set_device(static_cast<std::uint64_t>(root_identity.st_dev));
  destination->set_inode(static_cast<std::uint64_t>(root_identity.st_ino));
  destination->set_type(pb::FileCopyFilesystemIdentity::DIRECTORY);
  record.set_destination_basename("recovered-copy.bin");
  record.set_staging_name(".spatial-copy-stage-"+operation_hex(job_id));
  auto* stage_root=record.mutable_staging_root();
  stage_root->set_device(static_cast<std::uint64_t>(root_identity.st_dev));
  stage_root->set_inode(static_cast<std::uint64_t>(root_identity.st_ino)+2);
  stage_root->set_type(pb::FileCopyFilesystemIdentity::DIRECTORY);
  record.set_staging_subvolume_id(0);
  record.set_destination_subvolume_id(0);
  record.set_maximum_bytes(1024);
  record.set_maximum_entries(8);
  auto* parent=record.mutable_process_scope_parent();
  parent->set_launch_id(std::string(32, 'p'));
  parent->set_scope_name("spatial-git-workers");
  parent->set_boot_id("12345678-1234-1234-1234-123456789012");
  parent->set_device(1);
  parent->set_inode(2);
  record.set_broker_root_device(1);
  record.set_broker_root_inode(3);
  record.set_broker_root_boot_id(parent->boot_id());
  record.set_reserved_process_scope_name("copy-"+operation_hex(job_id));
  record.set_state(pb::FILE_COPY_JOB_JOURNAL_START_INTENT);
  record.set_created_at_unix_ms(1);
  record.set_updated_at_unix_ms(1);
  return record;
}

static void recovered_copy_job_poll_and_cancel_use_durable_receipt() {
  TempProject project;
  Auth auth;
  struct stat root_identity{};
  assert(fstat(project.root,&root_identity)==0);
  const auto state=std::filesystem::path(project.path)/"journal-state";
  assert(std::filesystem::create_directory(state));
  spatial::FileOperationJournal journal(state);
  const std::string job_id(16,'r');
  assert(journal.bind_root("cabinet-opaque-id",
      static_cast<std::uint64_t>(root_identity.st_dev),
      static_cast<std::uint64_t>(root_identity.st_ino),{}));
  auto cancelled=durable_copy_start_intent(job_id,root_identity);
  journal.begin_copy_job(cancelled,{});
  cancelled.set_state(pb::FILE_COPY_JOB_JOURNAL_CANCELLED);
  cancelled.set_updated_at_unix_ms(2);
  auto* terminal=cancelled.mutable_terminal_receipt();
  terminal->set_job_id(job_id);
  terminal->set_state(pb::FileCopyJobReceipt_State_CANCELLED);
  terminal->set_detail("recovered and stopped before publication");
  journal.update_copy_job(cancelled,{});

  auto unresolved=durable_copy_start_intent(std::string(16,'u'),root_identity);
  unresolved.set_initiating_operation_id(std::string(16,'v'));
  journal.begin_copy_job(unresolved,{});

  spatial::FileEngine engine(project.root,"workspace-a",{"cabinet-opaque-id"},
      auth,[]{return spatial::FileEngine::MonotonicClock::now();},nullptr,nullptr,
      -1,{},&journal);
  auto poll=request(901);
  poll.mutable_copy_job()->set_command(pb::FileCopyJob_Command_POLL);
  poll.mutable_copy_job()->set_job_id(job_id);
  const auto polled=engine.handle(poll);
  assert(polled.has_copy_job() &&
      polled.copy_job().SerializeAsString()==terminal->SerializeAsString());

  auto cancel=request(902);
  cancel.mutable_copy_job()->set_command(pb::FileCopyJob_Command_CANCEL);
  cancel.mutable_copy_job()->set_job_id(job_id);
  const auto cancelled_again=engine.handle(cancel);
  assert(cancelled_again.has_copy_job() &&
      cancelled_again.copy_job().SerializeAsString()==terminal->SerializeAsString());

  auto foreign=request(903);
  foreign.mutable_principal()->set_actor_id(std::string(32,'x'));
  foreign.mutable_copy_job()->set_command(pb::FileCopyJob_Command_POLL);
  foreign.mutable_copy_job()->set_job_id(job_id);
  assert(engine.handle(foreign).error().code()==pb::FileActionError::UNAUTHORIZED);

  auto other_worktree=spatial::FileEngine(project.root,"workspace-a",{"another-worktree"},auth);
  auto wrong_scope=request(904);
  wrong_scope.set_authority_components(0,"another-worktree");
  wrong_scope.mutable_copy_job()->set_command(pb::FileCopyJob_Command_POLL);
  wrong_scope.mutable_copy_job()->set_job_id(job_id);
  assert(other_worktree.handle(wrong_scope).error().code()==pb::FileActionError::NOT_FOUND);

  auto missing=request(905);
  missing.mutable_copy_job()->set_command(pb::FileCopyJob_Command_POLL);
  missing.mutable_copy_job()->set_job_id(std::string(16,'m'));
  assert(engine.handle(missing).error().code()==pb::FileActionError::NOT_FOUND);

  auto unresolved_poll=request(906);
  unresolved_poll.mutable_copy_job()->set_command(pb::FileCopyJob_Command_POLL);
  unresolved_poll.mutable_copy_job()->set_job_id(unresolved.job_id());
  assert(engine.handle(unresolved_poll).error().code()==pb::FileActionError::UNCERTAIN);
  auto unresolved_cancel=request(907);
  unresolved_cancel.mutable_copy_job()->set_command(pb::FileCopyJob_Command_CANCEL);
  unresolved_cancel.mutable_copy_job()->set_job_id(unresolved.job_id());
  assert(engine.handle(unresolved_cancel).error().code()==pb::FileActionError::UNCERTAIN);
}

static void uncertain_copy_recovery_preserves_evidence_and_isolates_workspace() {
  TempProject project;
  Auth auth;
  struct stat root_identity{};
  assert(fstat(project.root,&root_identity)==0);
  const auto state=std::filesystem::path(project.path)/"uncertain-journal";
  assert(std::filesystem::create_directory(state));
  auto journal=std::make_unique<spatial::FileOperationJournal>(state);
  assert(journal->bind_root("cabinet-opaque-id",
      static_cast<std::uint64_t>(root_identity.st_dev),
      static_cast<std::uint64_t>(root_identity.st_ino),{}));

  assert(mkdirat(project.root,".spatial-copy-staging",0700)==0);
  const int staging_root=openat(project.root,".spatial-copy-staging",
      O_RDONLY|O_DIRECTORY|O_CLOEXEC);
  assert(staging_root>=0);
  struct stat staging_root_identity{};
  assert(fstat(staging_root,&staging_root_identity)==0);
  const std::string job_id(16,'u');
  auto uncertain=durable_copy_start_intent(job_id,root_identity);
  auto* root_identity_record=uncertain.mutable_staging_root();
  root_identity_record->set_device(static_cast<std::uint64_t>(staging_root_identity.st_dev));
  root_identity_record->set_inode(static_cast<std::uint64_t>(staging_root_identity.st_ino));
  auto* stage_identity=uncertain.mutable_staging();
  stage_identity->set_device(static_cast<std::uint64_t>(staging_root_identity.st_dev));
  stage_identity->set_inode(static_cast<std::uint64_t>(staging_root_identity.st_ino)+1);
  stage_identity->set_generation(1);
  stage_identity->set_type(pb::FileCopyFilesystemIdentity::DIRECTORY);
  auto* scope=uncertain.mutable_process_scope();
  scope->set_launch_id(std::string(32,'l'));
  scope->set_scope_name("copy-"+operation_hex(job_id));
  scope->set_boot_id("12345678-1234-1234-1234-123456789012");
  scope->set_device(1);scope->set_inode(2);
  uncertain.set_state(pb::FILE_COPY_JOB_JOURNAL_QUEUED);
  uncertain.set_updated_at_unix_ms(2);
  (void)journal->reserve_git_worker_parent(uncertain.broker_root_device(),uncertain.broker_root_inode(),
      project.path,uncertain.broker_root_boot_id(),{});
  journal->bind_git_worker_parent(uncertain.broker_root_device(),uncertain.broker_root_inode(),
      project.path,uncertain.broker_root_boot_id(),uncertain.process_scope_parent().SerializeAsString(),{});
  const std::string stage_name=uncertain.staging_name();
  assert(mkdirat(staging_root,stage_name.c_str(),0700)==0);
  const int stage=openat(staging_root,stage_name.c_str(),O_RDONLY|O_DIRECTORY|O_CLOEXEC);
  assert(stage>=0);
  const int marker=openat(stage,"retain-for-review",O_WRONLY|O_CREAT|O_EXCL|O_CLOEXEC,0600);
  assert(marker>=0);close(marker);
  struct stat exact_stage{};
  assert(fstat(stage,&exact_stage)==0);
  stage_identity->set_device(static_cast<std::uint64_t>(exact_stage.st_dev));
  stage_identity->set_inode(static_cast<std::uint64_t>(exact_stage.st_ino));
  close(stage);
  // Bind the observed stage inode, never an assumed allocation sequence.
  journal->begin_copy_job(uncertain,{});
  uncertain.set_state(pb::FILE_COPY_JOB_JOURNAL_UNCERTAIN);
  uncertain.set_updated_at_unix_ms(3);
  auto* receipt=uncertain.mutable_terminal_receipt();
  receipt->set_job_id(job_id);
  receipt->set_state(pb::FileCopyJobReceipt_State_UNCERTAIN);
  receipt->set_detail("preserve stage for explicit recovery review");
  const auto immutable_receipt=receipt->SerializeAsString();
  journal->update_copy_job(uncertain,{});

  for(int restart=0;restart<2;++restart) {
    journal.reset();
    journal=std::make_unique<spatial::FileOperationJournal>(state);
    const auto parent=journal->find_git_worker_parent({});
    assert(parent.state==spatial::GitWorkerParentRecord::State::bound &&
           parent.identity_bytes==uncertain.process_scope_parent().SerializeAsString());
    spatial::FileEngine recovered(project.root,"workspace-a",{"cabinet-opaque-id"},
        auth,[]{return spatial::FileEngine::MonotonicClock::now();},nullptr,nullptr,
        -1,{},journal.get());
    auto poll=request(908+restart);
    poll.mutable_copy_job()->set_command(pb::FileCopyJob_Command_POLL);
    poll.mutable_copy_job()->set_job_id(job_id);
    const auto polled=recovered.handle(poll);
    assert(polled.has_copy_job() &&
           polled.copy_job().state()==pb::FileCopyJobReceipt_State_UNCERTAIN &&
           polled.copy_job().SerializeAsString()==immutable_receipt);
    auto browse=request(918+restart);
    browse.mutable_browse()->set_page_size(16);
    assert(recovered.handle(browse).has_directory());
  }
  TempProject independent;
  Auth independent_auth;
  spatial::FileEngine independent_workspace(independent.root,"workspace-b",{"other-cabinet"},independent_auth);
  auto independent_browse=request(930);
  independent_browse.set_workspace_id("workspace-b");
  independent_browse.clear_authority_components();
  independent_browse.add_authority_components("other-cabinet");
  independent_browse.mutable_browse()->set_page_size(16);
  assert(independent_workspace.handle(independent_browse).has_directory());

  struct stat retained{};
  assert(fstatat(staging_root,stage_name.c_str(),&retained,AT_SYMLINK_NOFOLLOW)==0 &&
         retained.st_dev==exact_stage.st_dev && retained.st_ino==exact_stage.st_ino);
  const int retained_stage=openat(staging_root,stage_name.c_str(),O_RDONLY|O_DIRECTORY|O_CLOEXEC);
  assert(retained_stage>=0);
  struct stat marker_stat{};
  assert(fstatat(retained_stage,"retain-for-review",&marker_stat,AT_SYMLINK_NOFOLLOW)==0 &&
         S_ISREG(marker_stat.st_mode));
  struct stat unpublished{};
  assert(fstatat(project.root,"recovered-copy.bin",&unpublished,AT_SYMLINK_NOFOLLOW)<0 && errno==ENOENT);
  close(retained_stage);close(staging_root);
  const auto durable=journal->find_copy_job("workspace-a",job_id,{});
  assert(durable && durable->state()==pb::FILE_COPY_JOB_JOURNAL_UNCERTAIN &&
         durable->terminal_receipt().SerializeAsString()==immutable_receipt);
}

static void root_clock_and_descriptor_regressions() {
  TempProject project;
  Auth auth;
  auto tick = spatial::FileEngine::MonotonicClock::time_point{};
  const int path_root = open(project.path.c_str(), O_PATH | O_DIRECTORY | O_CLOEXEC);
  assert(path_root >= 0);
  spatial::FileEngine engine(path_root, "workspace-a", {"cabinet-opaque-id"}, auth,
                             [&] { return tick; });
  close(path_root);
  const auto file = create(engine, "clock-file");
  assert(file.has_object());
  auto read = request(101);
  read.mutable_read()->set_file_handle(file.object().handle());
  read.mutable_read()->set_maximum_bytes(8);
  tick += std::chrono::minutes(10);
  assert(engine.handle(read).content().data() == "data");
  tick += std::chrono::minutes(10) + std::chrono::milliseconds(1);
  auto browse = request(102);
  browse.mutable_browse();
  assert(engine.handle(browse).has_directory());
  assert(engine.handle(read).error().code() == pb::FileActionError::STALE_HANDLE);
  const auto later = create(engine, "after-root-idle", 103);
  assert(later.has_object());
  auto trash = request(104);
  trash.mutable_trash()->set_file_handle(later.object().handle());
  trash.mutable_trash()->set_expected_generation(later.object().generation());
  // Creates trash and fsyncs the normalized root, even when supplied as O_PATH.
  assert(engine.handle(trash).has_trash());

  struct stat st {};
  assert(fstat(project.root, &st) == 0);
  // The bootstrap root has no public generation field. Include its exact inode
  // generation, as well as arbitrary generations, to reach the old UB branch.
  std::uint64_t gen = static_cast<std::uint64_t>(st.st_ino) * 0x9e3779b185ebca87ULL;
  gen ^= static_cast<std::uint64_t>(st.st_dev) + (gen << 6) + (gen >> 2);
  if (!gen) gen = 1;
  for (const auto expected : {std::uint64_t{0}, std::uint64_t{1}, gen}) {
    auto rename = request(105);
    rename.mutable_rename()->set_expected_generation(expected);
    rename.mutable_rename()->set_new_basename("root-must-remain");
    assert(engine.handle(rename).error().code() == pb::FileActionError::INVALID_ARGUMENT);
    auto move = request(106);
    move.mutable_move()->set_expected_generation(expected);
    move.mutable_move()->set_new_basename("root-must-remain");
    assert(engine.handle(move).error().code() == pb::FileActionError::INVALID_ARGUMENT);
    auto root_trash = request(107);
    root_trash.mutable_trash()->set_expected_generation(expected);
    assert(engine.handle(root_trash).error().code() == pb::FileActionError::INVALID_ARGUMENT);
  }
  assert(fstat(project.root, &st) == 0 && S_ISDIR(st.st_mode));
}
static void reservation_and_directory_regressions() {
  TempProject project;
  Auth auth;
  spatial::FileEngine engine(project.root, "workspace-a", {"cabinet-opaque-id"}, auth);
  const auto source = create(engine, "source");
  assert(source.has_object());
  assert(create(engine, "occupied", 108).has_object());
  auto collision = request(109);
  collision.mutable_create()->set_basename("occupied");
  auto rename = request(110);
  rename.mutable_rename()->set_file_handle(source.object().handle());
  rename.mutable_rename()->set_expected_generation(source.object().generation());
  rename.mutable_rename()->set_new_basename("occupied");
  auto edit = request(111);
  edit.mutable_edit()->set_file_handle(source.object().handle());
  edit.mutable_edit()->set_expected_generation(source.object().generation());
  edit.mutable_edit()->set_content("replacement");
  const auto stage = ".spatial-edit-" + operation_hex(edit.operation_id());
  const int blocker = openat(project.root, stage.c_str(), O_WRONLY | O_CREAT | O_EXCL, 0600);
  assert(blocker >= 0);
  close(blocker);
  const auto trash_source = create(engine, "trash-source", 112);
  assert(trash_source.has_object());
  auto trash = request(113);
  trash.mutable_trash()->set_file_handle(trash_source.object().handle());
  trash.mutable_trash()->set_expected_generation(trash_source.object().generation());
  const auto stored = engine.handle(trash);
  assert(stored.has_trash());
  auto restore = request(114);
  restore.mutable_restore()->set_trash_handle(stored.trash().handle());
  restore.mutable_restore()->set_expected_generation(stored.trash().generation());
  restore.mutable_restore()->set_destination_basename("occupied");
  // Exceeds the complete table bound for each independently failing mutation.
  for (unsigned i = 0; i < 17000; ++i) {
    assert(engine.handle(collision).error().code() == pb::FileActionError::CONFLICT);
    assert(engine.handle(rename).error().code() == pb::FileActionError::CONFLICT);
    assert(engine.handle(edit).error().code() == pb::FileActionError::UNAVAILABLE);
    assert(engine.handle(restore).error().code() == pb::FileActionError::CONFLICT);
  }
  assert(read_file(project.root, "source") == "data");
  assert(read_file(project.root, "occupied") == "data");
  auto read = request(115);
  read.mutable_read()->set_file_handle(source.object().handle());
  read.mutable_read()->set_maximum_bytes(8);
  assert(engine.handle(read).content().data() == "data");
  assert(create(engine, "after-collisions", 116).has_object());
  assert(mkdirat(project.root, "directory", 0700) == 0);
  auto browse = request(117);
  browse.mutable_browse();
  const auto listing = engine.handle(browse);
  std::string directory_handle;
  for (const auto& entry : listing.directory().entries())
    if (entry.name() == "directory") directory_handle = entry.handle();
  assert(!directory_handle.empty());
  assert(renameat(project.root, "directory", project.root, "original-directory") == 0);
  assert(mkdirat(project.root, "directory", 0700) == 0);
  auto stale_create = request(118);
  stale_create.mutable_create()->set_directory_handle(directory_handle);
  stale_create.mutable_create()->set_basename("must-not-exist");
  assert(engine.handle(stale_create).error().code() == pb::FileActionError::STALE_HANDLE);
  struct stat st {};
  assert(fstatat(project.root, "directory/must-not-exist", &st, 0) < 0 && errno == ENOENT);
  assert(fstatat(project.root, "original-directory/must-not-exist", &st, 0) < 0 && errno == ENOENT);
}
static void post_effect_allocation_regressions() {
  TempProject project;
  Auth auth;
  spatial::FileEngine engine(project.root, "workspace-a", {"cabinet-opaque-id"}, auth);
  const auto source = create(engine, "allocation-source");
  assert(source.has_object());
  assert(mkdirat(project.root, ".spatial-trash", 0700) == 0);
  const int trash_fd = openat(project.root, ".spatial-trash", O_RDONLY | O_DIRECTORY | O_CLOEXEC);
  assert(trash_fd >= 0);
  allocator_interceptor_positive_control(project.root);
  auto trash = request(119);
  trash.mutable_trash()->set_file_handle(source.object().handle());
  trash.mutable_trash()->set_expected_generation(source.object().generation());
  const auto stored = operation_hex(trash.operation_id());
  allocation_fault = {true, false, trash_fd, stored.c_str(), nullptr, false};
  const auto trashed = engine.handle(trash);
  const bool trash_injection_fired = disarm_allocation_fault();
  if (trash_injection_fired) {
    assert(trashed.error().code() == pb::FileActionError::UNCERTAIN);
    assert(trashed.error().recovery_operation_id() == trash.operation_id());
    std::cout << "post-effect trash: injection fired; UNCERTAIN with exact recovery operation ID\n";
  } else {
    assert(trashed.has_trash());
    std::cout << "post-effect trash: no late allocation; safe success\n";
  }
  struct stat st {};
  assert(fstatat(trash_fd, stored.c_str(), &st, AT_SYMLINK_NOFOLLOW) == 0);
  auto inspect = request(120);
  inspect.mutable_trash_inspect()->set_trash_handle(trash.operation_id());
  const auto entry = engine.handle(inspect);
  assert(entry.has_trash_entry());
  auto deletion = request(121);
  deletion.mutable_permanent_delete()->set_trash_handle(trash.operation_id());
  deletion.mutable_permanent_delete()->set_expected_generation(entry.trash_entry().generation());
  const auto quarantine = ".spatial-delete-" + operation_hex(deletion.operation_id());
  allocation_fault = {true, false, trash_fd, stored.c_str(), quarantine.c_str(), true};
  const auto deleted = engine.handle(deletion);
  const bool delete_injection_fired = disarm_allocation_fault();
  if (delete_injection_fired) {
    assert(deleted.error().code() == pb::FileActionError::UNCERTAIN);
    assert(deleted.error().recovery_operation_id() == deletion.operation_id());
    std::cout << "post-effect delete: injection fired; UNCERTAIN with exact recovery operation ID\n";
  } else {
    assert(deleted.has_object());
    std::cout << "post-effect delete: no late allocation; safe success\n";
  }
  assert(fstatat(trash_fd, stored.c_str(), &st, AT_SYMLINK_NOFOLLOW) < 0 && errno == ENOENT);
  assert(fstatat(trash_fd, quarantine.c_str(), &st, AT_SYMLINK_NOFOLLOW) < 0 && errno == ENOENT);
  close(trash_fd);
}
int main() {
  recovery_requires_scope_cleanup_for_same_boot_root_replacement();
  copy_staging_root_is_reserved_and_stale_state_fails_closed();
  recovered_copy_job_poll_and_cancel_use_durable_receipt();
  uncertain_copy_recovery_preserves_evidence_and_isolates_workspace();
  root_clock_and_descriptor_regressions();
  reservation_and_directory_regressions();
  post_effect_allocation_regressions();
  int pipe_fds[2]{};
  assert(pipe(pipe_fds) == 0);
  assert(!spatial::file_engine_detail::sync_directory(pipe_fds[0]));
  close(pipe_fds[0]);
  close(pipe_fds[1]);
  char pattern[] = "/tmp/spatial-file-engine-XXXXXX";
  char* path = mkdtemp(pattern);
  assert(path);
  const int root = open(path, O_RDONLY | O_DIRECTORY | O_CLOEXEC);
  assert(root >= 0);
  assert(mkdirat(root, "dest", 0700) == 0);
  Auth auth;
  spatial::FileEngine engine(root, "workspace-a", {"cabinet-opaque-id"}, auth);
  auto b = request(1);
  b.mutable_browse()->set_page_size(32);
  auto br = engine.handle(b);
  assert(br.has_directory());
  const auto root_handle = br.directory().handle();
  auth.allow = false;
  auto bd = request(18);
  bd.mutable_browse()->set_directory_handle(root_handle);
  assert(engine.handle(bd).error().code() == pb::FileActionError::UNAUTHORIZED &&
         auth.last == az::FILE_BROWSE);
  auth.allow = true;
  auto c = request(2);
  c.mutable_create()->set_directory_handle(root_handle);
  c.mutable_create()->set_basename("a");
  c.mutable_create()->set_content("one");
  auto cr = engine.handle(c);
  assert(cr.has_object() && auth.last == az::FILE_CREATE);
  assert(read_file(root, "a") == "one");
  auto first_page = request(25);
  first_page.mutable_browse()->set_directory_handle(root_handle);
  first_page.mutable_browse()->set_page_size(1);
  auto page1 = engine.handle(first_page);
  assert(page1.has_directory() && page1.directory().entries_size() == 1 &&
         !page1.directory().next_cursor().empty());
  auto second_page = request(26);
  second_page.mutable_browse()->set_directory_handle(root_handle);
  second_page.mutable_browse()->set_page_size(1);
  second_page.mutable_browse()->set_cursor(page1.directory().next_cursor());
  auto page2 = engine.handle(second_page);
  assert(page2.has_directory() && page2.directory().entries_size() == 1 &&
         page2.directory().next_cursor().empty());
  assert(engine.handle(second_page).error().code() == pb::FileActionError::STALE_HANDLE);
  auto changing_page = request(27);
  changing_page.mutable_browse()->set_directory_handle(root_handle);
  changing_page.mutable_browse()->set_page_size(1);
  auto change_cursor = engine.handle(changing_page);
  assert(!change_cursor.directory().next_cursor().empty());
  auto concurrent_create = request(28);
  concurrent_create.mutable_create()->set_directory_handle(root_handle);
  concurrent_create.mutable_create()->set_basename("during-browse");
  assert(engine.handle(concurrent_create).has_object());
  auto stale_cursor = request(29);
  stale_cursor.mutable_browse()->set_directory_handle(root_handle);
  stale_cursor.mutable_browse()->set_page_size(1);
  stale_cursor.mutable_browse()->set_cursor(change_cursor.directory().next_cursor());
  assert(engine.handle(stale_cursor).error().code() == pb::FileActionError::STALE_HANDLE);
  auto denied = request(3);
  auth.allow = false;
  denied.mutable_create()->set_directory_handle(root_handle);
  denied.mutable_create()->set_basename("denied");
  auto dr = engine.handle(denied);
  assert(dr.has_error() && dr.error().code() == pb::FileActionError::UNAUTHORIZED &&
         auth.last == az::FILE_CREATE);
  struct stat st {};
  assert(fstatat(root, "denied", &st, AT_SYMLINK_NOFOLLOW) < 0);
  auth.allow = true;
  auto rd = request(4);
  rd.mutable_read()->set_file_handle(cr.object().handle());
  rd.mutable_read()->set_maximum_bytes(32);
  auth.allow = false;
  assert(engine.handle(rd).error().code() == pb::FileActionError::UNAUTHORIZED &&
         auth.last == az::FILE_READ);
  auth.allow = true;
  auto rr = engine.handle(rd);
  assert(rr.has_content() && rr.content().data() == "one" && auth.last == az::FILE_READ);
  int external = openat(root, "a", O_WRONLY | O_CLOEXEC);
  assert(external >= 0);
  assert(pwrite(external, "new", 3, 0) == 3);
  assert(ftruncate(external, 3) == 0);
  assert(fsync(external) == 0);
  close(external);
  auto raced = request(21);
  raced.mutable_read()->set_file_handle(cr.object().handle());
  raced.mutable_read()->set_maximum_bytes(32);
  assert(engine.handle(raced).error().code() == pb::FileActionError::STALE_HANDLE);
  auto fresh = request(22);
  fresh.mutable_browse()->set_directory_handle(root_handle);
  auto fresh_listing = engine.handle(fresh);
  std::string fresh_handle;
  std::uint64_t fresh_generation = 0;
  for (const auto& e : fresh_listing.directory().entries())
    if (e.name() == "a") {
      fresh_handle = e.handle();
      fresh_generation = e.generation();
    }
  assert(!fresh_handle.empty());
  auto ed = request(5);
  ed.mutable_edit()->set_file_handle(fresh_handle);
  ed.mutable_edit()->set_expected_generation(fresh_generation);
  ed.mutable_edit()->set_content("two");
  auth.allow = false;
  assert(engine.handle(ed).error().code() == pb::FileActionError::UNAUTHORIZED &&
         auth.last == az::FILE_EDIT && read_file(root, "a") == "new");
  auth.allow = true;
  auto er = engine.handle(ed);
  assert(er.has_object() && auth.last == az::FILE_EDIT && read_file(root, "a") == "two");
  auto stale = request(6);
  stale.mutable_read()->set_file_handle(cr.object().handle());
  stale.mutable_read()->set_maximum_bytes(16);
  assert(engine.handle(stale).error().code() == pb::FileActionError::STALE_HANDLE);
  auto nm = request(7);
  nm.mutable_rename()->set_file_handle(er.object().handle());
  nm.mutable_rename()->set_expected_generation(er.object().generation());
  nm.mutable_rename()->set_new_basename("b");
  auth.allow = false;
  assert(engine.handle(nm).error().code() == pb::FileActionError::UNAUTHORIZED &&
         auth.last == az::FILE_RENAME);
  auth.allow = true;
  auto nr = engine.handle(nm);
  assert(nr.has_object() && auth.last == az::FILE_RENAME);
  assert(fstatat(root, "a", &st, AT_SYMLINK_NOFOLLOW) < 0);
  auto bb = request(8);
  bb.mutable_browse()->set_page_size(32);
  auto listing = engine.handle(bb);
  assert(listing.has_directory());
  std::string dest_handle;
  for (const auto& e : listing.directory().entries())
    if (e.name() == "dest") dest_handle = e.handle();
  assert(!dest_handle.empty());
  char other_pattern[] = "/tmp/spatial-file-engine-foreign-XXXXXX";
  char* other_path = mkdtemp(other_pattern);
  assert(other_path);
  const int other_root = open(other_path, O_RDONLY | O_DIRECTORY | O_CLOEXEC);
  assert(other_root >= 0);
  assert(mkdirat(other_root, "dest", 0700) == 0);
  Auth other_auth;
  spatial::FileEngine other_engine(other_root, "workspace-a", {"cabinet-opaque-id"}, other_auth);
  auto other_list = request(32);
  other_list.mutable_browse();
  auto other_browse = other_engine.handle(other_list);
  assert(other_browse.has_directory());
  std::string foreign_dest;
  for (const auto& e : other_browse.directory().entries())
    if (e.name() == "dest") foreign_dest = e.handle();
  assert(!foreign_dest.empty());
  auto foreign_move = request(33);
  foreign_move.mutable_move()->set_file_handle(nr.object().handle());
  foreign_move.mutable_move()->set_expected_generation(nr.object().generation());
  foreign_move.mutable_move()->set_destination_directory_handle(foreign_dest);
  foreign_move.mutable_move()->set_new_basename("foreign");
  auto foreign_result = engine.handle(foreign_move);
  assert(foreign_result.error().code() == pb::FileActionError::STALE_HANDLE);
  assert(fstatat(root, "b", &st, AT_SYMLINK_NOFOLLOW) == 0);
  close(other_root);
  std::filesystem::remove_all(other_path);
  auto mv = request(9);
  mv.mutable_move()->set_file_handle(nr.object().handle());
  mv.mutable_move()->set_expected_generation(nr.object().generation());
  mv.mutable_move()->set_destination_directory_handle(dest_handle);
  mv.mutable_move()->set_new_basename("moved");
  auth.allow = false;
  assert(engine.handle(mv).error().code() == pb::FileActionError::UNAUTHORIZED &&
         auth.last == az::FILE_MOVE);
  auth.allow = true;
  auto mr = engine.handle(mv);
  assert(mr.has_object() && auth.last == az::FILE_MOVE);
  int dest = openat(root, "dest", O_RDONLY | O_DIRECTORY | O_CLOEXEC);
  assert(dest >= 0);
  assert(read_file(dest, "moved") == "two");
  auto tr = request(10);
  tr.mutable_trash()->set_file_handle(mr.object().handle());
  tr.mutable_trash()->set_expected_generation(mr.object().generation());
  auth.allow = false;
  assert(engine.handle(tr).error().code() == pb::FileActionError::UNAUTHORIZED &&
         auth.last == az::FILE_TRASH && read_file(dest, "moved") == "two");
  auth.allow = true;
  auto trash = engine.handle(tr);
  assert(trash.has_trash() && auth.last == az::FILE_TRASH);
  struct stat trash_directory_stat {};
  assert(fstatat(root, ".spatial-trash", &trash_directory_stat, AT_SYMLINK_NOFOLLOW) == 0);
  assert(S_ISDIR(trash_directory_stat.st_mode) && fsync(root) == 0);
  auto inspect = request(24);
  inspect.mutable_trash_inspect()->set_trash_handle(trash.trash().handle());
  auto inspected = engine.handle(inspect);
  assert(inspected.has_trash_entry() &&
         inspected.trash_entry().generation() == trash.trash().generation() &&
         auth.last == az::FILE_BROWSE);
  auto collision = request(11);
  collision.mutable_create()->set_directory_handle(dest_handle);
  collision.mutable_create()->set_basename("occupied");
  collision.mutable_create()->set_content("keep");
  auto occ = engine.handle(collision);
  assert(occ.has_object());
  auto create_collision = request(34);
  create_collision.mutable_create()->set_directory_handle(dest_handle);
  create_collision.mutable_create()->set_basename("occupied");
  create_collision.mutable_create()->set_content("replace");
  auto rejected_create = engine.handle(create_collision);
  assert(rejected_create.error().code() == pb::FileActionError::CONFLICT &&
         read_file(dest, "occupied") == "keep");
  auto rs = request(12);
  rs.mutable_restore()->set_trash_handle(trash.trash().handle());
  rs.mutable_restore()->set_destination_directory_handle(dest_handle);
  rs.mutable_restore()->set_destination_basename("occupied");
  rs.mutable_restore()->set_expected_generation(trash.trash().generation());
  auth.allow = false;
  assert(engine.handle(rs).error().code() == pb::FileActionError::UNAUTHORIZED &&
         auth.last == az::FILE_RESTORE);
  auth.allow = true;
  auto conflict = engine.handle(rs);
  assert(conflict.has_error() && conflict.error().code() == pb::FileActionError::CONFLICT &&
         auth.last == az::FILE_RESTORE);
  rs.mutable_restore()->set_destination_basename("restored");
  auto restored = engine.handle(rs);
  assert(restored.has_object() && auth.last == az::FILE_RESTORE &&
         read_file(dest, "restored") == "two");
  auto tr2 = request(13);
  tr2.mutable_trash()->set_file_handle(restored.object().handle());
  tr2.mutable_trash()->set_expected_generation(restored.object().generation());
  auto trash2 = engine.handle(tr2);
  assert(trash2.has_trash());
  auto pd = request(14);
  pd.mutable_permanent_delete()->set_trash_handle(trash2.trash().handle());
  pd.mutable_permanent_delete()->set_expected_generation(trash2.trash().generation());
  auth.allow = false;
  auto denied_delete = engine.handle(pd);
  assert(denied_delete.has_error() &&
         denied_delete.error().code() == pb::FileActionError::UNAUTHORIZED &&
         auth.last == az::FILE_PERMANENT_DELETE);
  auth.allow = true;
  auto deleted = engine.handle(pd);
  assert(deleted.has_object() && auth.last == az::FILE_PERMANENT_DELETE);
  auto invalid = request(15);
  invalid.mutable_create()->set_directory_handle(root_handle);
  invalid.mutable_create()->set_basename("../escape");
  assert(engine.handle(invalid).has_error());
  auto nul = request(19);
  nul.mutable_create()->set_directory_handle(root_handle);
  nul.mutable_create()->set_basename(std::string("bad\0name", 8));
  assert(nul.mutable_create()->basename().size() == 8 && engine.handle(nul).has_error());
  assert(symlinkat("/etc/passwd", root, "link") == 0);
  auto links = request(20);
  links.mutable_browse()->set_directory_handle(root_handle);
  auto linklist = engine.handle(links);
  assert(linklist.has_directory());
  bool saw_link = false;
  for (const auto& e : linklist.directory().entries())
    if (e.name() == "link") {
      saw_link = true;
      assert(e.handle().empty());
    }
  assert(saw_link);
  const std::string posix_name(1, static_cast<char>(0xff));
  auto arbitrary = request(30);
  arbitrary.mutable_create()->set_directory_handle(root_handle);
  arbitrary.mutable_create()->set_basename(posix_name);
  arbitrary.mutable_create()->set_content("bytes");
  auto arbitrary_result = engine.handle(arbitrary);
  assert(arbitrary_result.has_object());
  auto arbitrary_browse = request(31);
  arbitrary_browse.mutable_browse()->set_directory_handle(root_handle);
  auto arbitrary_listing = engine.handle(arbitrary_browse);
  bool saw_arbitrary = false;
  for (const auto& e : arbitrary_listing.directory().entries())
    if (e.name() == posix_name) saw_arbitrary = true;
  assert(saw_arbitrary);
  auto malformed = request(23);
  malformed.set_protocol_version(2);
  malformed.mutable_create()->set_directory_handle(root_handle);
  malformed.mutable_create()->set_basename("bad-version");
  assert(engine.handle(malformed).error().code() == pb::FileActionError::INVALID_ARGUMENT);
  auto linkread = request(16);
  linkread.mutable_read()->set_file_handle(std::string("bad-handle"));
  linkread.mutable_read()->set_maximum_bytes(16);
  assert(engine.handle(linkread).error().code() == pb::FileActionError::STALE_HANDLE);
  auto copy = request(17);
  copy.mutable_copy_job()->set_command(pb::FileCopyJob::START);
  auth.allow = false;
  assert(engine.handle(copy).error().code() == pb::FileActionError::UNAUTHORIZED &&
         auth.last == az::FILE_COPY);
  auth.allow = true;
  assert(engine.handle(copy).error().code() == pb::FileActionError::INVALID_ARGUMENT &&
         auth.last == az::FILE_COPY);
  const auto existing_source = create(engine, "copy-existing-source", 18);
  assert(existing_source.has_object());
  auto no_overwrite = request(19);
  auto* no_overwrite_job = no_overwrite.mutable_copy_job();
  no_overwrite_job->set_command(pb::FileCopyJob::START);
  no_overwrite_job->set_source_handle(existing_source.object().handle());
  no_overwrite_job->set_destination_directory_handle(root_handle);
  no_overwrite_job->set_destination_basename("copy-existing-source");
  no_overwrite_job->set_maximum_bytes(1024);
  no_overwrite_job->set_maximum_entries(8);
  assert(engine.handle(no_overwrite).error().code() == pb::FileActionError::CONFLICT);
  auto bad_limit = request(20);
  auto* bad_limit_job = bad_limit.mutable_copy_job();
  bad_limit_job->set_command(pb::FileCopyJob::START);
  bad_limit_job->set_source_handle(existing_source.object().handle());
  bad_limit_job->set_destination_directory_handle(root_handle);
  bad_limit_job->set_destination_basename("copy-new-name");
  bad_limit_job->set_maximum_bytes(4ULL * 1024 * 1024 * 1024 + 1);
  bad_limit_job->set_maximum_entries(8);
  assert(engine.handle(bad_limit).error().code() == pb::FileActionError::INVALID_ARGUMENT);

  // Reject trashing the project root before dereferencing a basename.
  auto root_trash = request(40);
  root_trash.mutable_trash()->set_file_handle(root_handle);
  root_trash.mutable_trash()->set_expected_generation(
      br.directory().entries_size() ? br.directory().entries(0).generation() : 1);
  auto root_trash_result = engine.handle(root_trash);
  assert(root_trash_result.has_error() &&
         root_trash_result.error().code() == pb::FileActionError::INVALID_ARGUMENT);

  // A non-empty directory supports rename, move, trash, and restore.
  assert(mkdirat(root, "nested", 0700) == 0);
  int nested = openat(root, "nested", O_RDONLY | O_DIRECTORY | O_CLOEXEC);
  assert(nested >= 0);
  int child = openat(nested, "child", O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, 0600);
  assert(child >= 0 && write(child, "payload", 7) == 7);
  close(child);
  assert(fsync(nested) == 0);
  close(nested);
  auto dirs = request(41);
  dirs.mutable_browse()->set_directory_handle(root_handle);
  auto dir_listing = engine.handle(dirs);
  std::string nested_handle;
  std::uint64_t nested_generation = 0;
  for (const auto& entry : dir_listing.directory().entries()) {
    if (entry.name() == "nested") {
      nested_handle = entry.handle();
      nested_generation = entry.generation();
    }
  }
  assert(!nested_handle.empty());
  auto rename_dir = request(42);
  rename_dir.mutable_rename()->set_file_handle(nested_handle);
  rename_dir.mutable_rename()->set_expected_generation(nested_generation);
  rename_dir.mutable_rename()->set_new_basename("renamed-dir");
  auto renamed_dir = engine.handle(rename_dir);
  assert(renamed_dir.has_object());
  auto move_dir = request(43);
  move_dir.mutable_move()->set_file_handle(renamed_dir.object().handle());
  move_dir.mutable_move()->set_expected_generation(renamed_dir.object().generation());
  move_dir.mutable_move()->set_destination_directory_handle(dest_handle);
  move_dir.mutable_move()->set_new_basename("moved-dir");
  auto moved_dir = engine.handle(move_dir);
  assert(moved_dir.has_object());
  assert(renameat(dest, "moved-dir", dest, "moved-dir-renamed") == 0);
  auto dest_listing = request(44);
  dest_listing.mutable_browse()->set_directory_handle(dest_handle);
  auto dest_entries = engine.handle(dest_listing);
  std::string moved_dir_handle;
  std::uint64_t moved_dir_generation = 0;
  for (const auto& entry : dest_entries.directory().entries()) {
    if (entry.name() == "moved-dir-renamed") {
      moved_dir_handle = entry.handle();
      moved_dir_generation = entry.generation();
    }
  }
  assert(!moved_dir_handle.empty());
  auto trash_dir = request(45);
  trash_dir.mutable_trash()->set_file_handle(moved_dir_handle);
  trash_dir.mutable_trash()->set_expected_generation(moved_dir_generation);
  auto trashed_dir = engine.handle(trash_dir);
  assert(trashed_dir.has_trash());
  auto restore_dir = request(46);
  restore_dir.mutable_restore()->set_trash_handle(trashed_dir.trash().handle());
  restore_dir.mutable_restore()->set_expected_generation(trashed_dir.trash().generation());
  restore_dir.mutable_restore()->set_destination_directory_handle(dest_handle);
  restore_dir.mutable_restore()->set_destination_basename("restored-dir");
  auto restored_dir = engine.handle(restore_dir);
  assert(restored_dir.has_object());
  int restored_dir_fd = openat(dest, "restored-dir", O_RDONLY | O_DIRECTORY | O_CLOEXEC);
  assert(restored_dir_fd >= 0);
  assert(read_file(restored_dir_fd, "child") == "payload");
  close(restored_dir_fd);

  // Browse must not issue actionable handles for FIFOs. A stale file handle
  // replaced by a FIFO must also fail before opening a blocking descriptor.
  assert(mkfifoat(root, "fifo", 0600) == 0);
  auto fifo_browse = request(47);
  fifo_browse.mutable_browse()->set_directory_handle(root_handle);
  auto fifo_listing = engine.handle(fifo_browse);
  for (const auto& entry : fifo_listing.directory().entries()) {
    if (entry.name() == "fifo") assert(entry.handle().empty());
  }
  auto regular = request(48);
  regular.mutable_create()->set_directory_handle(root_handle);
  regular.mutable_create()->set_basename("replace-with-fifo");
  regular.mutable_create()->set_content("x");
  auto regular_reply = engine.handle(regular);
  assert(regular_reply.has_object());
  auto regular_read = request(148);
  regular_read.mutable_read()->set_file_handle(regular_reply.object().handle());
  regular_read.mutable_read()->set_maximum_bytes(1);
  assert(engine.handle(regular_read).content().data() == "x");
  const int regular_fd = spatial::file_engine_detail::open_regular_file(root, "replace-with-fifo");
  assert(regular_fd >= 0 && (fcntl(regular_fd, F_GETFL) & O_NONBLOCK) != 0);
  close(regular_fd);
  // /proc is an existing leaf mount. No mount creation or privilege is needed.
  const int host_root = open("/", O_PATH | O_DIRECTORY | O_CLOEXEC);
  assert(host_root >= 0);
  assert(spatial::file_engine_detail::open_regular_file(host_root, "proc") < 0 && errno == EXDEV);
  close(host_root);
  assert(unlinkat(root, "replace-with-fifo", 0) == 0);
  assert(mkfifoat(root, "replace-with-fifo", 0600) == 0);
  auto stale_fifo = request(49);
  stale_fifo.mutable_read()->set_file_handle(regular_reply.object().handle());
  stale_fifo.mutable_read()->set_maximum_bytes(1);
  const pid_t fifo_child = fork();
  assert(fifo_child >= 0);
  if (fifo_child == 0) {
    const int fifo_fd = spatial::file_engine_detail::open_regular_file(root, "fifo");
    if (fifo_fd >= 0 || errno != EINVAL) _exit(2);
    const auto result = engine.handle(stale_fifo);
    // INVALID_ARGUMENT comes from the actual regular-open boundary, not an
    // earlier stale-generation lookup. The old get() precheck cannot satisfy it.
    _exit(result.error().code() == pb::FileActionError::INVALID_ARGUMENT ? 0 : 1);
  }
  int fifo_status = 0;
  bool fifo_finished = false;
  for (int i = 0; i < 100; ++i) {
    const auto waited = waitpid(fifo_child, &fifo_status, WNOHANG);
    if (waited == fifo_child) {
      fifo_finished = true;
      break;
    }
    usleep(10000);
  }
  if (!fifo_finished) {
    kill(fifo_child, SIGKILL);
    waitpid(fifo_child, &fifo_status, 0);
  }
  assert(fifo_finished && WIFEXITED(fifo_status) && WEXITSTATUS(fifo_status) == 0);

  // Handle exhaustion is detected before create publishes any filesystem entry.
  for (unsigned i = 0; i < 6000; ++i) {
    auto fill = request(50 + i);
    fill.mutable_browse()->set_directory_handle(root_handle);
    const auto filled = engine.handle(fill);
    if (filled.has_error() && filled.error().code() == pb::FileActionError::EXHAUSTED) break;
  }
  auto exhausted_create = request(6051);
  exhausted_create.mutable_create()->set_directory_handle(root_handle);
  exhausted_create.mutable_create()->set_basename("must-not-publish");
  exhausted_create.mutable_create()->set_content("x");
  auto exhausted_result = engine.handle(exhausted_create);
  assert(exhausted_result.has_error() &&
         exhausted_result.error().code() == pb::FileActionError::EXHAUSTED);
  assert(fstatat(root, "must-not-publish", &st, AT_SYMLINK_NOFOLLOW) < 0);

  close(dest);
  close(root);
  std::filesystem::remove_all(path);
}
