"""The bootstrap fixture must start a fresh owner proof after a long setup."""
import time
import unittest
import test_workspace_bootstrap as existing


class BootstrapOwnerIdleTests(existing.BootstrapWorkspaceFixture):
    def workspace(self, *args, **kwargs):
        world = super().workspace(*args, **kwargs)
        time.sleep(self.config.idle_timeout_ms / 1000 + .2)
        return world

    def test_idle_owner_does_not_block_workspace_bootstrap(self):
        # Reuse every assertion in the existing confined-file acceptance case.
        existing.WorkspaceBootstrapTests.test_explicit_application_enables_confined_real_file_write(self)


if __name__ == '__main__': unittest.main(verbosity=2)
