#include "supervisor/process_scope.hpp"
#include <cassert>
#include <stdexcept>
#include <string>

int main() {
    const std::string embedded_name("job\0scope", 9);
    bool prepare_rejected = false;
    try {
        (void)spatial::supervisor::ProcessScope::prepare_named("/nonexistent", embedded_name);
    } catch (const std::invalid_argument&) {
        prepare_rejected = true;
    }
    assert(prepare_rejected);

    spatial::supervisor::v1::ProcessScopeIdentity identity;
    identity.set_scope_name(embedded_name);
    identity.set_device(1);
    identity.set_inode(1);
    identity.set_boot_id("00000000-0000-0000-0000-000000000000");
    identity.set_launch_id(std::string(16, 'x'));
    bool recovery_rejected = false;
    try {
        (void)spatial::supervisor::ProcessScope::stop_recovered("/nonexistent", 1, 1, identity);
    } catch (const std::invalid_argument&) {
        recovery_rejected = true;
    }
    assert(recovery_rejected);

    for (const std::string invalid_name : {".", ".."}) {
        auto invalid_identity = identity;
        invalid_identity.set_scope_name(invalid_name);
        bool dot_name_rejected = false;
        try {
            (void)spatial::supervisor::ProcessScope::stop_recovered(
                "/nonexistent", 1, 1, invalid_identity);
        } catch (const std::invalid_argument&) {
            dot_name_rejected = true;
        }
        assert(dot_name_rejected);
    }

    auto valid_identity=identity;
    valid_identity.set_scope_name("copy-recovery-missing-parent");
    bool missing_parent_unconfirmed=false;
    try {
        (void)spatial::supervisor::ProcessScope::stop_recovered(
            "/nonexistent", 1, 1, valid_identity);
    } catch(const std::runtime_error&) {
        missing_parent_unconfirmed=true;
    }
    assert(missing_parent_unconfirmed);

    bool unjournaled_root_rejected=false;
    try {
        (void)spatial::supervisor::ProcessScope::remove_unjournaled_empty_git_parent(
            "/nonexistent",0,0);
    } catch(const std::invalid_argument&) {
        unjournaled_root_rejected=true;
    }
    assert(unjournaled_root_rejected);
    bool unjournaled_path_rejected=false;
    try {
        (void)spatial::supervisor::ProcessScope::remove_unjournaled_empty_git_parent(
            "/nonexistent",1,1);
    } catch(const std::exception&) {
        unjournaled_path_rejected=true;
    }
    assert(unjournaled_path_rejected);
    return 0;
}
