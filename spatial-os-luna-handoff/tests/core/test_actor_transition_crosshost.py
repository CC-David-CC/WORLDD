"""Drive production C++ actor transitions using a protobuf cross-host deployment plan."""
import struct
import os
import select
import time
import shlex
import socket
import subprocess
import sys
import ipaddress
import json
import hashlib
from pathlib import Path

import actor_transition_crosshost_pb2 as plan_pb
import actor_session_client_pb2 as tls_pb
import core_pb2 as core
from google.protobuf import text_format
from google.protobuf.json_format import MessageToDict

ALLOWED_HOSTS = {"sb16", "sb16-2"}
PHASE_ACTIVE = 4
PHASE_PREPARATION_REJECTED = 5

HOST_EVIDENCE = r'''
import hashlib, json, os, pathlib, socket, sys
port = int(sys.argv[1]); database = pathlib.Path(sys.argv[2]).resolve()
inodes = set()
for table in ('/proc/net/tcp', '/proc/net/tcp6'):
    for line in pathlib.Path(table).read_text().splitlines()[1:]:
        fields = line.split()
        if int(fields[1].split(':')[1], 16) == port and fields[3] == '0A':
            inodes.add(fields[9])
owners = []
for proc in pathlib.Path('/proc').iterdir():
    if not proc.name.isdigit(): continue
    try:
        command = (proc / 'cmdline').read_bytes().split(b'\0')
        if pathlib.Path(os.fsdecode(command[0])).name != 'worldd': continue
        for fd in (proc / 'fd').iterdir():
            target = os.readlink(fd)
            if target.startswith('socket:[') and target[8:-1] in inodes:
                parts = (proc / 'stat').read_text().rsplit(') ', 1)[1].split()
                if b'--state-dir' not in command: continue
                state = pathlib.Path(os.fsdecode(command[command.index(b'--state-dir') + 1])).resolve()
                digest = hashlib.sha256((proc / 'exe').read_bytes()).hexdigest()
                owners.append(dict(pid=int(proc.name), start_ticks=int(parts[19]), inode=target[8:-1],
                                   database=str(state / 'world.db'), executable_sha256=digest))
    except (OSError, IndexError, ValueError): pass
print(json.dumps(dict(hostname=socket.gethostname(), machine_id=pathlib.Path('/etc/machine-id').read_text().strip(),
                      owners=owners, listener_inodes=sorted(inodes), database=str(database))))
'''


def frame(message):
    payload = message.SerializeToString()
    if not payload or len(payload) > 1024 * 1024:
        raise AssertionError("cross-host protobuf frame exceeds bound")
    return struct.pack("!I", len(payload)) + payload


def read_frame(data, message_type):
    if len(data) < 4:
        raise AssertionError("missing driver response frame")
    size = struct.unpack("!I", data[:4])[0]
    if not 0 < size <= 1024 * 1024 or len(data) != size + 4:
        raise AssertionError("invalid driver response framing")
    return message_type.FromString(data[4:])


def run(driver, case, helper_dirs):
    endpoints = {(case.source_host, case.source_world_id, case.source_database_path)}
    endpoints.update((step.destination_host, step.destination_world_id, step.destination_database_path)
                     for step in case.steps)
    evidence_dir = os.environ.get("SPATIAL_ACTOR_TRANSITION_EVIDENCE")
    stderr_log = None
    if evidence_dir:
        stderr_log = (Path(evidence_dir) / f"probe-{case.source_host}-{case.scenario}.stderr").open("wb")
    process = subprocess.Popen([driver], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=stderr_log or subprocess.PIPE)
    snapshots = []
    deadline = time.monotonic() + 90
    def exact(size):
        data = bytearray()
        while len(data) < size:
            if not select.select([process.stdout], [], [], max(0, deadline - time.monotonic()))[0]:
                raise TimeoutError("production driver boundary deadline expired")
            part = os.read(process.stdout.fileno(), size - len(data))
            if not part:
                raise EOFError("production driver ended before boundary")
            data.extend(part)
        return bytes(data)
    def receive():
        header = exact(4)
        size = struct.unpack("!I", header)[0]
        if not 0 < size <= 1024 * 1024:
            raise AssertionError("invalid production driver frame length")
        return plan_pb.ActorTransitionCrosshostReply.FromString(exact(size))
    try:
        if evidence_dir:
            proc_path = Path('/proc') / str(process.pid)
            start = int((proc_path / 'stat').read_text().rsplit(') ', 1)[1].split()[19])
            (Path(evidence_dir) / f"probe-{case.source_host}-{case.scenario}.process.json").write_text(
                json.dumps(dict(pid=process.pid, start_ticks=start, driver=driver,
                                executable_sha256=hashlib.sha256(Path(driver).read_bytes()).hexdigest())) + '\n')
        process.stdin.write(frame(case))
        process.stdin.flush()
        for index in range(len(case.steps) + 1):
            reply = receive()
            if reply.done or reply.driver_error or reply.boundary_index != index:
                raise AssertionError("production driver missed durable witness boundary")
            actor_id = reply.initial_opened.actor_id
            if not actor_id:
                raise AssertionError("missing authenticated actor at durable boundary")
            snapshot = {(host, world): read_durable_entity(host, path, actor_id, world, helper_dirs)
                        for host, world, path in endpoints}
            snapshots.append(snapshot)
            for (host, world), durable in sorted(snapshot.items()):
                saved = core.Entity.FromString(durable.entity) if durable.actor_present else None
                print(json.dumps({"kind": "durable_boundary", "boundary": index, "host": host,
                                  "world_id": world, "world_revision": durable.world_revision,
                                  "actor_present": durable.actor_present,
                                  "room": saved.room if saved else None,
                                  "pose": MessageToDict(saved.pose) if saved else None}), flush=True)
            process.stdin.write(frame(plan_pb.ActorTransitionCrosshostAck(protocol_version=1,
                                                                          boundary_index=index)))
            process.stdin.flush()
        process.stdin.close()
        reply = receive()
        if not reply.done or reply.driver_error or process.wait(timeout=5):
            raise AssertionError("production driver failed: " + reply.driver_error)
        if evidence_dir:
            (Path(evidence_dir) / f"probe-{case.source_host}-{case.scenario}.reply.pb").write_bytes(frame(reply))
        return reply, snapshots
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=3)
        process.stdout.close()
        if process.stderr:
            process.stderr.close()
        if stderr_log:
            stderr_log.close()
            (Path(evidence_dir) / f"probe-{case.source_host}-{case.scenario}.exit").write_text(str(process.returncode) + '\n')


def assert_host_roles(case):
    if not case.steps:
        raise AssertionError("case has no destination step")
    hosts = [case.source_host] + [step.destination_host for step in case.steps]
    if any(host not in ALLOWED_HOSTS for host in hosts):
        raise AssertionError("runtime host selector is outside sb16/sb16-2")
    if len(set(hosts[:2])) != 2:
        raise AssertionError("source and first destination must be different hosts")
    ids = [case.source_world_id] + [step.destination_world_id for step in case.steps]
    if any(not value for value in ids) or len(set(ids[:2])) != 2:
        raise AssertionError("plan must use distinct actual persisted world IDs")


def host_addresses(host, cache):
    if host not in cache:
        result = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
                                 host, "hostname", "-I"], capture_output=True, text=True,
                                timeout=8, check=False)
        if result.returncode:
            raise AssertionError(f"cannot identify orchestration peer {host}: {result.stderr.strip()}")
        cache[host] = {ipaddress.ip_address(value) for value in result.stdout.split()}
        if not cache[host]:
            raise AssertionError(f"peer {host} reported no host addresses")
    return cache[host]


def validate_tls_config(path, expected_world, host, address_cache):
    config = tls_pb.ActorSessionClientTlsConfig()
    text_format.Parse(Path(path).read_text(encoding="utf-8"), config)
    try:
        address = ipaddress.ip_address(config.address)
    except ValueError as error:
        raise AssertionError("TLS endpoint must use a numeric address") from error
    if address not in host_addresses(host, address_cache):
        raise AssertionError(f"TLS endpoint address does not belong to configured host {host}")
    if config.protocol_version != 1 or config.expected_world_id != expected_world or \
            not config.server_name or not 1 <= config.port <= 65535:
        raise AssertionError("TLS config world identity or certificate server name mismatch")
    for material in (config.certificate_path, config.private_key_path, config.server_ca_path,
                     config.actor_signing_key_path):
        if not Path(material).is_file():
            raise AssertionError("TLS config references missing private test material")
    if not config.HasField("actor_credential") or not config.actor_credential.proof.signature:
        raise AssertionError("TLS config lacks a signed enrolled actor credential")
    if config.io_timeout_ms > 10000:
        raise AssertionError("TLS client timeout exceeds fixture bound")
    print(json.dumps({"kind": "tls_endpoint", "host": host, "address": config.address,
                      "port": config.port, "server_name": config.server_name,
                      "world_id": expected_world}), flush=True)
    return config


def host_evidence(host, port, database_path, expected_binary_hash):
    result = ssh(host, ["python3", "-c", HOST_EVIDENCE, str(port), database_path], timeout=8)
    if result.returncode:
        raise AssertionError("remote process evidence failed: " + result.stderr.decode(errors="replace"))
    evidence = json.loads(result.stdout)
    if evidence["hostname"] != host or not evidence["machine_id"] or \
            len(evidence["owners"]) != 1 or len(evidence["listener_inodes"]) != 1:
        raise AssertionError("remote host or listener ownership is ambiguous")
    owner = evidence["owners"][0]
    if owner["database"] != str(Path(database_path).resolve()) or \
            owner["inode"] != evidence["listener_inodes"][0] or owner["start_ticks"] <= 0:
        raise AssertionError("listener does not belong to the expected worldd state")
    if owner["executable_sha256"] != expected_binary_hash:
        raise AssertionError("listener owner does not run the deployed worldd binary")
    return evidence


def ssh(host, command, *, input_bytes=None, timeout=12):
    remote = shlex.join(command)
    return subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", host, remote],
                          input=input_bytes, capture_output=True, timeout=timeout, check=False)


def read_durable_entity(host, database_path, actor_id, expected_world_id, helper_dirs):
    # Provisioned run03 bundles already contain this read-only witness on both hosts.
    # Reuse that deployment. It creates no additional paths requiring cleanup.
    deployed = os.environ.get("SPATIAL_ACTOR_TRANSITION_WITNESS")
    if deployed:
        python_path = os.environ["SPATIAL_ACTOR_TRANSITION_WITNESS_PYTHONPATH"]
        request = plan_pb.ActorTransitionDurableReadRequest(protocol_version=1,
            database_path=database_path, actor_id=actor_id, expected_world_id=expected_world_id)
        completed = ssh(host, ["env", "PYTHONPATH=" + python_path, "python3", deployed],
                        input_bytes=frame(request), timeout=8)
        if completed.returncode:
            raise AssertionError("deployed durable witness failed: " + completed.stderr.decode(errors="replace"))
        reply = read_frame(completed.stdout, plan_pb.ActorTransitionDurableReadReply)
        if reply.protocol_version != 1 or reply.error or reply.world_id != expected_world_id or not reply.world_revision:
            raise AssertionError("deployed witness returned invalid durable world identity")
        return reply
    if host not in helper_dirs:
        created = ssh(host, ["mktemp", "-d", "/tmp/spatial-m06-actor-state-XXXXXXXX"], timeout=8)
        if created.returncode:
            raise AssertionError(f"cannot allocate witness directory on {host}")
        remote_dir = created.stdout.decode().strip()
        if not remote_dir.startswith("/tmp/spatial-m06-actor-state-") or "/" in remote_dir[5:]:
            raise AssertionError("remote witness directory escaped its test-owned prefix")
        helper_dirs[host] = remote_dir
        python_dir = Path(plan_pb.__file__).parent
        sources = list(python_dir.glob("*.py")) + [Path(__file__).with_name("actor_transition_durable_witness.py")]
        result = subprocess.run(["scp", "-q", *map(str, sources), f"{host}:{remote_dir}/"],
                                timeout=20, check=False)
        if result.returncode:
            raise AssertionError(f"failed to deploy read-only protobuf witness on {host}")
    request = plan_pb.ActorTransitionDurableReadRequest(protocol_version=1,
        database_path=database_path, actor_id=actor_id, expected_world_id=expected_world_id)
    payload = request.SerializeToString()
    framed = struct.pack("!I", len(payload)) + payload
    remote_dir = helper_dirs[host]
    completed = ssh(host, ["env", "PYTHONPATH=" + remote_dir, "python3",
                           remote_dir + "/actor_transition_durable_witness.py"],
                    input_bytes=framed, timeout=8)
    if completed.returncode:
        raise AssertionError(f"read-only durable witness failed on {host}: {completed.stderr.decode(errors='replace')}")
    reply = read_frame(completed.stdout, plan_pb.ActorTransitionDurableReadReply)
    if reply.protocol_version != 1 or reply.error or reply.world_id != expected_world_id or not reply.world_revision:
        raise AssertionError("durable witness did not return the requested actor entity")
    return reply


def remove_witness_dirs(helper_dirs):
    for host, path in helper_dirs.items():
        if path.startswith("/tmp/spatial-m06-actor-state-") and "/" not in path[5:]:
            result = ssh(host, ["rm", "-rf", "--", path], timeout=8)
            if result.returncode:
                raise AssertionError(f"could not remove recorded witness directory on {host}")


def entity(snapshot, host, world):
    item = snapshot[(host, world)]
    if not item.actor_present or not item.entity:
        raise AssertionError("durable actor row is absent")
    return core.Entity.FromString(item.entity)


def assert_round_trip(case, reply, snapshots):
    if case.scenario != plan_pb.ActorTransitionCrosshostRequest.ROUND_TRIP or len(case.steps) != 2:
        raise AssertionError("round-trip case must contain A-to-B-to-A")
    if not case.HasField("expected_source_room") or not case.HasField("expected_source_pose") or \
            not case.steps[0].HasField("expected_destination_room") or \
            not case.steps[0].HasField("expected_destination_pose"):
        raise AssertionError("round-trip plan must name saved room and pose for both worlds")
    if any(step.HasField("destination_room") or step.HasField("destination_pose") for step in case.steps):
        raise AssertionError("round trip must resume persisted context without an explicit spawn")
    if case.steps[1].destination_world_id != case.source_world_id or case.steps[1].destination_host != case.source_host:
        raise AssertionError("return step does not target the actual source world and host")
    if reply.initial_opened.world_id != case.source_world_id:
        raise AssertionError("initial authentication reported an unexpected source world")
    if len(reply.results) != 2 or any(item.phase != PHASE_ACTIVE or
            not item.destination_preparation_confirmed or not item.source_departure_confirmed or
            not item.destination_activation_confirmed or not item.HasField("departure_world_revision")
            for item in reply.results):
        raise AssertionError("A-to-B-to-A did not activate both prepared destinations")
    expected_pairs = [(case.source_world_id, case.steps[0].destination_world_id),
                      (case.steps[0].destination_world_id, case.source_world_id)]
    for result, pair in zip(reply.results, expected_pairs):
        if (result.source_world_id, result.destination_world_id) != pair or \
                result.actor_id != reply.initial_opened.actor_id:
            raise AssertionError("coordinator result reported an unexpected actor or world identity")
    if len(reply.observations) != 3:
        raise AssertionError("expected source, B, and returned A inspections")
    if any(not item.HasField("state") for item in reply.observations):
        raise AssertionError("expected an authoritative actor inspection at every transition boundary")
    first, middle, returned = [item.state for item in reply.observations]
    if case.HasField("expected_source_room") and first.room != case.expected_source_room:
        raise AssertionError("A did not resume its expected saved room")
    if case.HasField("expected_source_pose") and first.pose != case.expected_source_pose:
        raise AssertionError("A did not resume its expected saved pose")
    if case.steps[0].HasField("expected_destination_room") and middle.room != case.steps[0].expected_destination_room:
        raise AssertionError("B did not resume its expected saved room")
    if case.steps[0].HasField("expected_destination_pose") and middle.pose != case.steps[0].expected_destination_pose:
        raise AssertionError("B did not resume its expected saved pose")
    if first.id != reply.initial_opened.actor_id or first.id != returned.id or \
            first.room != returned.room or first.pose != returned.pose:
        raise AssertionError("return to A did not restore its saved actor room and pose")
    if reply.observations[0].expected_world_id != reply.observations[2].expected_world_id:
        raise AssertionError("return inspection world identity mismatch")
    if middle.id != first.id or reply.observations[1].expected_world_id != case.steps[0].destination_world_id:
        raise AssertionError("B inspection actor/world identity mismatch")
    saved_b = entity(snapshots[0], case.steps[0].destination_host, case.steps[0].destination_world_id)
    if saved_b.room != case.steps[0].expected_destination_room or \
            saved_b.pose != case.steps[0].expected_destination_pose:
        raise AssertionError("B did not have its independently persisted context before transition")
    if reply.active_world_id != case.source_world_id:
        raise AssertionError("active world is not A after return")
    actor_id = reply.initial_opened.actor_id
    durable_a = entity(snapshots[-1], case.source_host, case.source_world_id)
    durable_b = entity(snapshots[-1], case.steps[0].destination_host,
                       case.steps[0].destination_world_id)
    if durable_a.room != returned.room or durable_a.pose != returned.pose:
        raise AssertionError("A durable actor context differs from the returned room and pose")
    if durable_b.room != middle.room or durable_b.pose != middle.pose:
        raise AssertionError("B durable actor context differs from its saved room and pose")
    for index, observation in enumerate(reply.observations):
        host = case.source_host if index != 1 else case.steps[0].destination_host
        world = observation.expected_world_id
        saved = entity(snapshots[index], host, world)
        if saved.room != observation.state.room or saved.pose != observation.state.pose:
            raise AssertionError("boundary inspection differs from persisted room and pose")
    for previous, current in zip(snapshots, snapshots[1:]):
        for key in previous:
            if current[key].world_revision < previous[key].world_revision:
                raise AssertionError("durable world revision regressed")


def assert_preserved_source(case, reply, snapshots):
    if len(reply.results) != 1 or reply.results[0].phase != PHASE_PREPARATION_REJECTED:
        raise AssertionError("destination preparation was not rejected before source departure")
    result = reply.results[0]
    if reply.initial_opened.world_id != case.source_world_id:
        raise AssertionError("initial authentication reported an unexpected source world")
    if result.source_world_id != case.source_world_id or \
            result.destination_world_id != case.steps[0].destination_world_id or \
            result.actor_id != reply.initial_opened.actor_id:
        raise AssertionError("failed coordinator result reported an unexpected actor or world identity")
    if result.source_departure_confirmed or result.destination_activation_confirmed:
        raise AssertionError("failed preparation reported a departure or activation")
    if not result.source_inspection_confirmed:
        raise AssertionError("source was not inspected before preparation")
    if case.scenario == plan_pb.ActorTransitionCrosshostRequest.PRIVATE_DENIAL:
        if not result.HasField("server_error") or result.server_error.code != core.FORBIDDEN:
            raise AssertionError("private destination did not reject admission with FORBIDDEN")
    elif result.HasField("server_error") or result.effect_uncertain:
        raise AssertionError("unavailable endpoint must fail before a server-side preparation effect")
    if len(reply.observations) != 2 or not reply.observations[-1].HasField("state"):
        raise AssertionError("source could not be inspected after failed preparation")
    before, after = reply.observations[0].state, reply.observations[1].state
    if before.id != reply.initial_opened.actor_id or before.id != after.id or \
            before.room != after.room or before.pose != after.pose:
        raise AssertionError("failed preparation changed source actor context")
    if reply.active_world_id != case.source_world_id:
        raise AssertionError("failed preparation changed the active source world")
    follow_up = case.steps[0].source_after_failure_actions
    if not follow_up or len(reply.action_replies) != len(case.initial_actions) + len(follow_up) or \
            not all(item.accepted for item in reply.action_replies[-len(follow_up):]):
        raise AssertionError("authorized source follow-up action was not accepted")
    durable = entity(snapshots[-1], case.source_host, case.source_world_id)
    if durable.room != before.room or durable.pose != before.pose or \
            durable.room != after.room or durable.pose != after.pose:
        raise AssertionError("failed destination preparation changed durable source context")
    earlier = entity(snapshots[0], case.source_host, case.source_world_id)
    if earlier.room != durable.room or earlier.pose != durable.pose:
        raise AssertionError("rejected transition changed persisted source context")
    source_key = (case.source_host, case.source_world_id)
    if snapshots[0][source_key].entity != snapshots[-1][source_key].entity or \
            snapshots[0][source_key].world_revision != snapshots[-1][source_key].world_revision:
        raise AssertionError("rejected transition changed durable source entity or revision")
    if snapshots[-1][(case.source_host, case.source_world_id)].world_revision < \
            snapshots[0][(case.source_host, case.source_world_id)].world_revision:
        raise AssertionError("source world revision regressed")
    target = (case.steps[0].destination_host, case.steps[0].destination_world_id)
    first_target, last_target = snapshots[0][target], snapshots[-1][target]
    if first_target.actor_present != last_target.actor_present or first_target.entity != last_target.entity:
        raise AssertionError("rejected destination changed persisted actor context")
    if last_target.world_revision < first_target.world_revision:
        raise AssertionError("destination world revision regressed")


def main():
    if len(sys.argv) != 3:
        raise SystemExit("usage: test_actor_transition_crosshost.py DRIVER PLAN.pb")
    driver, plan_path = sys.argv[1:]
    expected_worldd_hash = hashlib.sha256(Path(driver).with_name("worldd").read_bytes()).hexdigest()
    plan_bytes = Path(plan_path).read_bytes()
    if not plan_bytes or len(plan_bytes) > 1024 * 1024:
        raise AssertionError("cross-host deployment plan exceeds its protobuf frame limit")
    plan = plan_pb.ActorTransitionCrosshostPlan.FromString(plan_bytes)
    if plan.protocol_version != 1 or len(plan.cases) != 6:
        raise AssertionError("invalid or empty protobuf deployment plan")
    scenarios = set()
    round_trip_roles = set()
    scenario_roles = {}
    address_cache = {}
    helper_dirs = {}
    host_machines = {}
    try:
        for case in plan.cases:
            assert_host_roles(case)
            if len(case.steps) != (2 if case.scenario == plan_pb.ActorTransitionCrosshostRequest.ROUND_TRIP else 1):
                raise AssertionError("scenario has an unexpected number of transition steps")
            source_tls = validate_tls_config(case.source_tls_config_path, case.source_world_id,
                                             case.source_host, address_cache)
            source_evidence = host_evidence(case.source_host, source_tls.port, case.source_database_path,
                                            expected_worldd_hash)
            evidence = [(case.source_host, source_tls.port, case.source_database_path, source_evidence)]
            for index, step in enumerate(case.steps):
                dest_tls = validate_tls_config(step.destination_tls_config_path, step.destination_world_id,
                                               step.destination_host, address_cache)
                if case.scenario == plan_pb.ActorTransitionCrosshostRequest.DESTINATION_UNAVAILABLE and index == 0:
                    continue
                observed = host_evidence(step.destination_host, dest_tls.port, step.destination_database_path,
                                         expected_worldd_hash)
                evidence.append((step.destination_host, dest_tls.port, step.destination_database_path, observed))
            hosts = {host: item["machine_id"] for host, _, _, item in evidence}
            for host, port, database, item in evidence:
                print(json.dumps({"kind": "authority", "host": host, "port": port,
                                  "database": database, **item}), flush=True)
            for host, machine in hosts.items():
                if host in host_machines and host_machines[host] != machine:
                    raise AssertionError("authority host identity changed between cases")
                host_machines[host] = machine
            if len(set(hosts.values())) != len(hosts):
                raise AssertionError("two authority host names resolve to one physical host")
            if case.scenario == plan_pb.ActorTransitionCrosshostRequest.DESTINATION_UNAVAILABLE:
                unavailable = tls_pb.ActorSessionClientTlsConfig()
                text_format.Parse(Path(case.steps[0].destination_tls_config_path).read_text(encoding="utf-8"), unavailable)
                probe = socket.socket(socket.AF_INET6 if ":" in unavailable.address else socket.AF_INET,
                                      socket.SOCK_STREAM)
                probe.settimeout(1)
                try:
                    connected = probe.connect_ex((unavailable.address, unavailable.port)) == 0
                finally:
                    probe.close()
                if connected:
                    raise AssertionError("unavailable-destination endpoint accepted a TCP connection")
            reply, snapshots = run(driver, case, helper_dirs)
            for host, port, database, before in evidence:
                after = host_evidence(host, port, database, expected_worldd_hash)
                if before != after:
                    raise AssertionError("authority process or listener changed during transition")
            scenarios.add(case.scenario)
            if case.scenario != plan_pb.ActorTransitionCrosshostRequest.ROUND_TRIP:
                scenario_roles.setdefault(case.scenario, set()).add((case.source_host, case.steps[0].destination_host))
            if case.scenario == plan_pb.ActorTransitionCrosshostRequest.ROUND_TRIP:
                assert_round_trip(case, reply, snapshots)
                round_trip_roles.add((case.source_host, case.steps[0].destination_host))
            elif case.scenario in (plan_pb.ActorTransitionCrosshostRequest.PRIVATE_DENIAL,
                                   plan_pb.ActorTransitionCrosshostRequest.DESTINATION_UNAVAILABLE):
                assert_preserved_source(case, reply, snapshots)
            else:
                raise AssertionError("unsupported cross-host scenario")
            print(json.dumps({"kind": "case_result", "scenario": case.scenario,
                              "source_host": case.source_host,
                              "destination_host": case.steps[0].destination_host,
                              "actor_id": reply.initial_opened.actor_id,
                              "result_phases": [item.phase for item in reply.results],
                              "active_world_id": reply.active_world_id}), flush=True)
    finally:
        remove_witness_dirs(helper_dirs)
    required = {plan_pb.ActorTransitionCrosshostRequest.ROUND_TRIP,
                plan_pb.ActorTransitionCrosshostRequest.PRIVATE_DENIAL,
                plan_pb.ActorTransitionCrosshostRequest.DESTINATION_UNAVAILABLE}
    if scenarios != required:
        raise AssertionError("plan must include round-trip, private denial, and unavailable destination cases")
    if len(host_machines) != 2 or len(set(host_machines.values())) != 2:
        raise AssertionError("authorities were not observed on two physical hosts")
    if round_trip_roles != {("sb16", "sb16-2"), ("sb16-2", "sb16")}:
        raise AssertionError("round-trip cases must cover both reversible host roles")
    reversible = {("sb16", "sb16-2"), ("sb16-2", "sb16")}
    if any(roles != reversible for roles in scenario_roles.values()):
        raise AssertionError("denial and unavailable cases must also cover both host roles")
    print("cross-host actor transition protobuf plan passed")


if __name__ == "__main__":
    main()
