"""Explicit resource profiles for the copy fixture launchers, never production policy."""
import hashlib
from pathlib import Path
import re
import subprocess

COPY_BINARIES = ('workspaced', 'worldd', 'identityd', 'spatial-issuer-client',
    'spatial-process-scope-probe', 'spatial-storage-owner', 'spatial-app-init',
    'spatial-copy-tree-worker', 'spatial-copy-finalize-worker', 'spatial-security-auth')

def consume_profile(arguments, flag='--profile='):
    arguments = list(arguments)
    choices = [value for value in arguments if value.startswith(flag)]
    if not choices:
        return arguments, 'Debug'
    if len(choices) != 1 or arguments[-1] != choices[0]:
        raise ValueError('The explicit copy profile must occur once, as the final argument')
    profile = choices[0][len(flag):]
    manager_memory_mib(profile)
    return arguments[:-1], profile

def manager_memory_mib(profile):
    if profile not in ('Debug', 'ASan-UBSan'):
        raise ValueError('Unsupported explicit copy resource profile: ' + profile)
    return 512 if profile == 'ASan-UBSan' else 256


STORAGE_GIT_PARENT_MIB = 256
STORAGE_HELD_APPLICATION_MIB = 128
STORAGE_NEXT_WRITER_APPLICATION_MIB = 128
STORAGE_BROKER_OVERHEAD_MIB = 256


def broker_memory_mib(profile):
    """Fund the manager, standing Git parent, held and next app, and overhead."""
    return (manager_memory_mib(profile) + STORAGE_GIT_PARENT_MIB +
            STORAGE_HELD_APPLICATION_MIB + STORAGE_NEXT_WRITER_APPLICATION_MIB +
            STORAGE_BROKER_OVERHEAD_MIB)


def build_adapter_resources(profile, major, minor, broker_module, process_module):
    """Build the adapter fallback with the shared profile headroom and fixed policy."""
    manager_memory = manager_memory_mib(profile)
    broker_memory = broker_memory_mib(profile)
    if (not isinstance(major, int) or not 0 <= major < 2**32 or
            not isinstance(minor, int) or not 0 <= minor < 2**32):
        raise ValueError('Adapter I/O device identity is invalid')
    mib = 2**20

    def budget(memory, processes, cpu):
        return process_module.ProcessScopeBudget(
            memory_bytes=memory*mib, swap_bytes=0, processes=processes,
            cpu_microseconds=cpu, io=[dict(major=major, minor=minor,
                read_bytes_per_second=64*mib, write_bytes_per_second=16*mib)])

    return broker_module.BrokerResourceConfig(protocol_version=2,
        broker_budget=budget(broker_memory, 128, 400000),
        manager_budget=budget(manager_memory, 32, 200000),
        default_policy=broker_module.WorkspaceResourcePolicy(total=budget(512, 64, 200000),
            actor_default=budget(256, 32, 100000), profiles=[
                broker_module.ApplicationResourceProfile(name=name, maximum=budget(128, 16, 100000))
                for name in ('development', 'read-only')]))


def bind_definition_resource_devices(resources, devices):
    """Copy definition budgets and bind the provisioned, pinned device limits."""
    expected = type(resources)()
    expected.CopyFrom(resources)
    device_limits = _checked_io_devices(devices, description='Pinned storage owner budget')
    budgets = process_scope_budgets(expected)
    if any(budget.io for budget in budgets):
        raise ValueError('Pinned storage definition must not prebind runtime I/O devices')
    for budget in budgets:
        for (major, minor), read, write in device_limits:
            budget.io.add(major=major, minor=minor,
                          read_bytes_per_second=read, write_bytes_per_second=write)
    return expected


def require_exact_provisioned_resources(actual, pinned_definition_resources, owner_devices):
    """Require runtime resources to equal pinned budgets plus provisioned device binding."""
    expected = bind_definition_resource_devices(pinned_definition_resources, owner_devices)
    if actual != expected:
        raise ValueError('Runtime resources differ from pinned storage definition and device binding')
    return expected

def instrumentation_preflight(build, profile, *, additional_binaries=()):
    manager_memory_mib(profile)
    result = dict(profile=profile, manager_memory_bytes=manager_memory_mib(profile)*2**20,
                  binaries={})
    for name in (*COPY_BINARIES,*additional_binaries):
        path = Path(build)/name
        if not path.is_file() or path.is_symlink():
            raise ValueError('Missing regular SAN executable: ' + name)
        output = subprocess.run(['readelf', '--dynamic', str(path)], capture_output=True,
                                text=True, timeout=10, check=True).stdout
        needed = re.findall(r'\(NEEDED\).*?\[([^]]+)\]', output)
        sanitizers = [any(item.startswith(library) for item in needed)
                      for library in ('libasan.so.', 'libubsan.so.')]
        if profile == 'ASan-UBSan' and not all(sanitizers):
            raise ValueError('Explicit SAN profile requires ASan and UBSan on every executable: ' + name)
        if profile == 'Debug' and any(sanitizers):
            raise ValueError('Debug profile requires executables without ASan or UBSan: ' + name)
        result['binaries'][name] = dict(sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                                                    needed=needed)
    return result


_PROCESS_SCOPE_BUDGET = 'spatial.supervisor.v1.ProcessScopeBudget'
_MAX_IO_COMPONENT = 2**40

def process_scope_budgets(message):
    """Return every ProcessScopeBudget nested in a protobuf message."""
    found = []

    def visit(value):
        descriptor = getattr(value, 'DESCRIPTOR', None)
        if descriptor is None:
            raise ValueError('Resource policy must be a protobuf message')
        if descriptor.full_name == _PROCESS_SCOPE_BUDGET:
            found.append(value)
            return
        for field, item in value.ListFields():
            if not field.message_type:
                continue
            children = item if field.label == field.LABEL_REPEATED else (item,)
            for child in children:
                visit(child)

    visit(message)
    if not found:
        raise ValueError('Resource policy contains no process scope budgets')
    return tuple(found)

def _checked_io_devices(devices, *, description):
    result = []
    keys = set()
    for item in devices:
        major = getattr(item, 'major', None)
        minor = getattr(item, 'minor', None)
        read = getattr(item, 'read_bytes_per_second', None)
        write = getattr(item, 'write_bytes_per_second', None)
        if (not isinstance(major, int) or not 0 <= major < 2**32 or
                not isinstance(minor, int) or not 0 <= minor < 2**32 or
                not isinstance(read, int) or not 0 < read <= _MAX_IO_COMPONENT or
                not isinstance(write, int) or not 0 < write <= _MAX_IO_COMPONENT):
            raise ValueError(description + ' contains an invalid I/O device limit')
        key = (major, minor)
        if key in keys:
            raise ValueError(description + ' contains a duplicate I/O device')
        keys.add(key)
        result.append((key, read, write))
    if not result:
        raise ValueError(description + ' contains no I/O devices')
    return tuple(sorted(result))

def validate_resource_io_closure(resource, fixture_devices):
    """Require every policy budget to use the fixture volume's complete device set."""
    expected = _checked_io_devices(fixture_devices, description='Fixture volume')
    expected_keys = {item[0] for item in expected}
    budgets = process_scope_budgets(resource)
    for budget in budgets:
        actual = _checked_io_devices(budget.io, description='Process scope budget')
        if {item[0] for item in actual} != expected_keys:
            raise ValueError('Resource budget does not cover the fixture volume I/O device set')
    return len(budgets)

def set_resource_io_closure(resource, fixture_devices):
    """Rebind all nested budgets to the fixture volume without raising their I/O limits."""
    expected = _checked_io_devices(fixture_devices, description='Fixture volume')
    budgets = process_scope_budgets(resource)
    rewrites = []
    expected_keys = {item[0] for item in expected}
    for budget in budgets:
        old = _checked_io_devices(budget.io, description='Process scope budget')
        old_by_key = {item[0]: item for item in old}
        unmatched = [item for item in old if item[0] not in expected_keys]
        replacements = []
        for key, ceiling_read, ceiling_write in expected:
            source = old_by_key.get(key)
            if source is None and unmatched:
                source = unmatched.pop(0)
            if source is None:
                source_read = min(item[1] for item in old)
                source_write = min(item[2] for item in old)
            else:
                source_read, source_write = source[1], source[2]
            replacements.append((key[0], key[1], min(source_read, ceiling_read),
                                 min(source_write, ceiling_write)))
        rewrites.append((budget, replacements))

    for budget, replacements in rewrites:
        budget.ClearField('io')
        for major, minor, read, write in replacements:
            budget.io.add(major=major, minor=minor,
                          read_bytes_per_second=read, write_bytes_per_second=write)
    return len(rewrites)


def configure_copy_fixture_application_profiles(resource, workspace):
    """Declare bounded copy applications in the policies selected by this fixture."""
    policies=[resource.default_policy,*[policy for policy in resource.workspaces
                                      if policy.workspace==workspace]]
    pending=[]
    for policy in policies:
        baseline=next((profile for profile in policy.profiles if profile.name=='development'),None)
        if baseline is None:
            raise ValueError('Copy fixture policy requires a development application profile')
        if any(profile.name in ('copy-source','copy-app') for profile in policy.profiles):
            raise ValueError('Copy fixture application profiles already exist')
        if (baseline.maximum.memory_bytes<64*2**20 or baseline.maximum.processes<8 or
                baseline.maximum.cpu_microseconds<25000):
            raise ValueError('Copy fixture applications exceed the configured development profile')
        maximum=type(baseline.maximum)();maximum.CopyFrom(baseline.maximum)
        maximum.memory_bytes=64*2**20;maximum.processes=8;maximum.cpu_microseconds=25000
        pending.append((policy,maximum))
    for policy,maximum in pending:
        for name in ('copy-source','copy-app'):
            policy.profiles.add(name=name).maximum.CopyFrom(maximum)
    return len(pending)
