import hashlib,io,json,pathlib,tarfile
root=pathlib.Path(__file__).resolve().parents[1]
manifest=json.loads((root/'local/adapter-integration-prepared-04.json').read_text())['files']
with tarfile.open(root/'local/bootstrap-adapters-candidate-04.tar') as archive:
    files={name:archive.extractfile(name).read() for name in manifest}
files['tests/core/test_processes.py']=(root/'tests/core/test_processes.py').read_bytes().replace(b'\r\n',b'\n')
for name,digest in manifest.items():assert hashlib.sha256(files[name]).hexdigest()==digest,name
def replace(name,old,new,count=1):
    text=files[name].decode();assert text.count(old)==count,(name,old,text.count(old))
    files[name]=text.replace(old,new).encode()
proto='proto/spatial/identity/v1/world_session.proto'
replace(proto,'message WorldAuthoritySeed {','''// TLS connection permission is separate from signed actor identity and world admission.
message WorldActorTlsConfig {
  uint32 protocol_version = 1;
  string certificate_path = 2;
  string private_key_path = 3;
  string client_ca_path = 4;
  optional uint32 io_timeout_ms = 5;
  optional uint32 idle_timeout_ms = 6; // Unauthenticated channels only.
  optional uint32 connection_limit = 7; // Includes inherited local channels.
}
message WorldAuthoritySeed {''')
header='src/world/identity_sessions.hpp'
replace(header,'// Actor-only channels are inherited unnamed local stream sockets.',
    '// Local actor channels are inherited unnamed sockets. Remote channels use the explicit TLS listener.')
replace(header,'WorldStore& world, Actors& actors, BrokerClient* broker = nullptr);',
    'WorldStore& world, Actors& actors, BrokerClient* broker = nullptr,\n                     int listener = -1, const std::filesystem::path& tls_configuration = {});')
name='src/world/main.cpp'
replace(name,'#include "world/identity_sessions.hpp"','#include "world/identity_sessions.hpp"\n#include "world/actor_tls.hpp"')
replace(name,'state, config, identity_config, authority_seed;', 'state, config, identity_config, authority_seed, actor_tls_config;')
replace(name,'int broker_fd = -1, broker_pid = -1;', 'int broker_fd = -1, broker_pid = -1, actor_listener = -1;')
replace(name,'else if (argument == "--actor-fd" && i + 1 < argc) {','''else if (argument == "--actor-tls-config" && actor_tls_config.empty() && i + 1 < argc) actor_tls_config = argv[++i];
            else if (argument == "--actor-listen-fd" && actor_listener == -1 && i + 1 < argc) {
                const std::string_view number(argv[++i]);
                const auto parsed = std::from_chars(number.data(), number.data()+number.size(), actor_listener);
                if (parsed.ec != std::errc{} || parsed.ptr != number.data()+number.size() || actor_listener <= STDERR_FILENO)
                    throw std::invalid_argument("Invalid actor listener descriptor");
            }
            else if (argument == "--actor-fd" && i + 1 < argc) {''')
replace(name,'if (identity_config.empty() != actor_channels.empty()) throw std::invalid_argument("World identity config and actor channels are required together");',
'''if ((actor_listener == -1) != actor_tls_config.empty()) throw std::invalid_argument("Actor TLS listener and configuration are required together");
        if (identity_config.empty() != (actor_channels.empty() && actor_listener == -1))
            throw std::invalid_argument("World identity configuration and actor transport are required together");
        if (actor_listener >= 0) {
            if (actor_listener == broker_fd) throw std::invalid_argument("Actor listener cannot share the broker descriptor");
            spatial::prepare_actor_listener(actor_listener);
        }''')
replace(name,'if (!unique_channels.insert(fd).second || fd == broker_fd)',
    'if (!unique_channels.insert(fd).second || fd == broker_fd || fd == actor_listener)')
replace(name,'identity_config, actor_channels, world, actors, broker.get());',
    'identity_config, actor_channels, world, actors, broker.get(), actor_listener, actor_tls_config);')
name='src/world/actors.hpp'
replace(name,'std::optional<spatial::authority::v1::ActorRef> principal = {});',
    'std::optional<spatial::authority::v1::ActorRef> principal = {}, bool local_application_output = true);')
replace(name,'bool require_observation_grant = false;', 'bool require_observation_grant = false;\n        bool local_application_output = true;')
name='src/world/actors.cpp'
replace(name,'std::optional<spatial::authority::v1::ActorRef> principal) {',
    'std::optional<spatial::authority::v1::ActorRef> principal, bool local_application_output) {')
replace(name,'actor.require_observation_grant = require_observation_grant;',
    'actor.require_observation_grant = require_observation_grant;\n    actor.local_application_output = local_application_output;')
replace(name,'case wire::ActorActionRequest::kScroll:\n',
    'case wire::ActorActionRequest::kScroll:\n        if (!actor.local_application_output) throw WorldError(wire::UNIMPLEMENTED, "Remote application input is not supported");\n')
replace(name,'if (surfaces_) {\n        surfaces_->append(result, actor.entity.room());',
    'if (surfaces_ && actor.local_application_output) {\n        surfaces_->append(result, actor.entity.room());')
name='src/world/identity_sessions.cpp'
replace(name,'#include "world/authority.hpp"','#include "world/authority.hpp"\n#include "world/actor_tls.hpp"')
replace(name,'identity::tls_detail::Fd fd;','identity::tls_detail::Fd fd;\n    std::unique_ptr<ActorTlsPeer> tls;')
replace(name,'explicit Channel(int descriptor) : fd(descriptor) {}','''explicit Channel(int descriptor) : fd(descriptor) {}
    void close_transport() { tls.reset(); fd.reset(); }
    int transport_read() {
        const bool empty = input.empty();
        const auto result = tls->read(input);
        if (empty && !input.empty()) input_started = Clock::now();
        return result;
    }''')
replace(name,'std::vector<std::unique_ptr<Channel>> channels;',
    'std::unique_ptr<ActorTlsListener> listener;\n    std::vector<std::unique_ptr<Channel>> channels;')
replace(name,'WorldStore& store, Actors& engine, BrokerClient* execution)',
    'WorldStore& store, Actors& engine, BrokerClient* execution, int listener_fd, const std::filesystem::path& tls_config)')
replace(name,'require(!descriptors.empty() && descriptors.size() <= 32, "World requires between one and 32 actor channels");',
'''require((!descriptors.empty() || listener_fd >= 0) && descriptors.size() <= 32, "World requires actor transport");
        if (listener_fd >= 0) {
            listener = std::make_unique<ActorTlsListener>(listener_fd, tls_config);
            require(descriptors.size() <= listener->limit(), "Local channels exceed the total actor connection limit");
        }''')
replace(name,'channel.fd.reset();','channel.close_transport();',2)
replace(name,'channel->fd.reset();','channel->close_transport();')
replace(name,'if (!lost && (fd.revents & POLLIN)) {','if (!lost && !channel->tls && (fd.revents & POLLIN)) {')
replace(name,'            if (lost) {\n                disconnect(*channel);',
'''            if (!lost && channel->tls) {
                lost = channel->tls->expired(!channel->token.empty());
                if (!lost && channel->tls->ready() && channel->output.empty()) lost = channel->transport_read() < 0;
            }
            if (lost) {
                disconnect(*channel);''')
replace(name,'actors.open(open, true, std::move(check), actor);',
    'actors.open(open, true, std::move(check), actor, !channel.tls);')
replace(name,'            const auto now = Clock::now();\n            if ((!channel.input.empty()',
'''            const auto now = Clock::now();
            if (channel.tls) {
                if (channel.tls->expired(!channel.token.empty())) { drop(); continue; }
                const auto handshake = channel.tls->handshake();
                if (handshake < 0) { drop(); continue; }
                if (!handshake) continue;
                if (channel.tls->read_needs_write()) {
                    if (channel.transport_read() < 0) { drop(); continue; }
                    if (channel.tls->read_needs_write()) continue;
                }
            }
            const auto timeout = channel.tls ? channel.tls->timeout() : std::chrono::milliseconds(10000);
            if ((!channel.input.empty()''')
replace(name,'now-channel.input_started > std::chrono::seconds(10)','now-channel.input_started > timeout')
replace(name,'now-channel.output_started > std::chrono::seconds(10)','now-channel.output_started > timeout')
replace(name,'if (!channel.output.empty() && (fd.revents & POLLOUT)) {','if (!channel.output.empty() && (channel.tls || (fd.revents & POLLOUT))) {')
old='''                const auto written = write(channel.fd.value, channel.output.data()+channel.sent, channel.output.size()-channel.sent);
                if (written < 0 && errno != EAGAIN && errno != EINTR) { drop(); continue; }
                if (written > 0) { channel.sent += static_cast<std::size_t>(written); if (channel.sent == channel.output.size()) { channel.output.clear(); channel.needs_observation = false; } }
'''
new='''                if (channel.tls) {
                    if (channel.tls->write(channel.output, channel.sent) < 0) { drop(); continue; }
                } else {
                    const auto written = write(channel.fd.value, channel.output.data()+channel.sent, channel.output.size()-channel.sent);
                    if (written < 0 && errno != EAGAIN && errno != EINTR) { drop(); continue; }
                    if (written > 0) channel.sent += static_cast<std::size_t>(written);
                }
                if (channel.sent == channel.output.size()) { channel.output.clear(); channel.needs_observation = false; }
'''
replace(name,old,new)
replace(name,'            if (channel.output.empty() && (fd.revents & POLLIN)) {',
'''            if (channel.tls && channel.output.empty()) {
                if (channel.transport_read() < 0) { drop(); continue; }
                if (channel.tls->read_needs_write()) continue;
            }
            if (!channel.tls && channel.output.empty() && (fd.revents & POLLIN)) {''')
replace(name,'            channel.output_started = Clock::now();\n        }\n    }',
'''            channel.output_started = Clock::now();
        }
        std::erase_if(channels, [](const auto& channel) { return channel->fd.value < 0; });
        if (listener && !broken) {
            const auto fd = listener->accept();
            if (fd >= 0) {
                auto channel = std::make_unique<Channel>(fd);
                if (channels.size() < listener->limit()) {
                    channel->tls = listener->peer(fd); channels.push_back(std::move(channel));
                }
            }
        }
    }''')
replace(name,'WorldStore& world, Actors& actors, BrokerClient* broker)\n    : impl_(std::make_unique<Impl>(config, channels, world, actors, broker)) {}',
'''WorldStore& world, Actors& actors, BrokerClient* broker,
    int listener, const std::filesystem::path& tls_configuration)
    : impl_(std::make_unique<Impl>(config, channels, world, actors, broker, listener, tls_configuration)) {}''')
for file in ('actor_tls.hpp','actor_tls.cpp'):files['src/world/'+file]=(root/'local'/file).read_bytes().replace(b'\r\n',b'\n')
files['tests/core/test_world_remote.py']=(root/'local/test_world_remote.py').read_bytes().replace(b'\r\n',b'\n')
replace('CMakeLists.txt','add_executable(worldd src/world/main.cpp','add_executable(worldd src/world/actor_tls.cpp src/world/main.cpp')
replace('CMakeLists.txt','    find_package(Python3 3.11 REQUIRED COMPONENTS Interpreter)\n','''    find_package(Python3 3.11 REQUIRED COMPONENTS Interpreter)
    add_test(NAME world-remote COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_world_remote.py" $<TARGET_FILE:identityd>
        $<TARGET_FILE:spatial-issuer-client> $<TARGET_FILE:worldd>)
    set_tests_properties(world-remote PROPERTIES TIMEOUT 180
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
''')
hashes={name:hashlib.sha256(data).hexdigest() for name,data in files.items()}
record={'base':'candidate 04 plus qualified process witness','files':hashes,
        'changed_files':[name for name,digest in hashes.items() if manifest.get(name)!=digest]}
(root/'local/world-remote-source-01.json').write_text(json.dumps(record,indent=2)+'\n',newline='\n')
draft=root/'local/world-remote-draft';draft.mkdir(exist_ok=True)
for name in record['changed_files']:
    path=draft/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(files[name])
with tarfile.open(root/'local/world-remote-source-01.tar','w') as archive:
    for name,data in files.items():
        member=tarfile.TarInfo(name);member.size=len(data);member.mode=0o644;archive.addfile(member,io.BytesIO(data))
print('Packaged isolated remote actor draft:',len(hashes),'source files;',len(record['changed_files']),'changed; production unchanged')
