spatial_proto = custom_target('spatial-input-protobuf',
    input: ['input.proto', 'security.proto'], output: ['input.pb-c.c', 'input.pb-c.h'],
    command: [find_program('protoc-c'), '--c_out=@OUTDIR@',
              '--proto_path=' + meson.current_source_dir(), meson.current_source_dir() / 'input.proto'])
srcs_xext += files('spatialinput.c')
srcs_xext += spatial_proto
spatial_security_proto = custom_target('spatial-security-protobuf',
    input: 'security.proto', output: ['security.pb-c.c', 'security.pb-c.h'],
    command: [find_program('protoc-c'), '--c_out=@OUTDIR@',
              '--proto_path=' + meson.current_source_dir(), meson.current_source_dir() / 'security.proto'])
srcs_xext += files('spatialsecurity.c', 'spatialpolicy.c')
srcs_xext += spatial_security_proto
spatial_selection_proto = custom_target('spatial-selection-protobuf',
    input: ['selection_scope.proto', 'security.proto'],
    output: ['selection_scope.pb-c.c', 'selection_scope.pb-c.h'],
    command: [find_program('protoc-c'), '--c_out=@OUTDIR@',
              '--proto_path=' + meson.current_source_dir(), meson.current_source_dir() / 'selection_scope.proto'])
srcs_xext += spatial_selection_proto
spatial_input_dep = dependency('libprotobuf-c', version: '>=1.4')

xext_dependencies = [common_dep, spatial_input_dep]
if get_option('spatial_native_input_observer')
    observer_proto = custom_target('native-input-observer-protobuf-c',
        input: 'native_input_observer.proto',
        output: ['native_input_observer.pb-c.c', 'native_input_observer.pb-c.h'],
        command: [find_program('protoc-c'), '--c_out=@OUTDIR@',
                  '--proto_path=' + meson.current_source_dir(),
                  meson.current_source_dir() / 'native_input_observer.proto'])
    observer_v2_proto = custom_target('native-input-observer-v2-protobuf-c',
        input: 'native_input_observer_v2.proto',
        output: ['native_input_observer_v2.pb-c.c', 'native_input_observer_v2.pb-c.h'],
        command: [find_program('protoc-c'), '--c_out=@OUTDIR@',
                  '--proto_path=' + meson.current_source_dir(),
                  meson.current_source_dir() / 'native_input_observer_v2.proto'])
    observer_control_proto = custom_target('native-input-observer-control-v2-protobuf-c',
        input: ['native_input_observer_control_v2.proto', 'native_input_observer_v2.proto'],
        output: ['native_input_observer_control_v2.pb-c.c', 'native_input_observer_control_v2.pb-c.h'],
        command: [find_program('protoc-c'), '--c_out=@OUTDIR@',
                  '--proto_path=' + meson.current_source_dir(),
                  meson.current_source_dir() / 'native_input_observer_control_v2.proto'])
    observer_library = static_library('xserver_native_input_observer',
        files('spatialinputobserver_lifetime.c',
              'spatialinputobserver_capture.c',
              'spatialinputobserver_serialize.c',
              'spatialinputobserver_serialize_v2.c',
              'spatialinputobserver_endpoint.c',
              'spatialinputobserver_transition_v2.c'),
        observer_proto, observer_v2_proto, observer_control_proto,
        include_directories: [inc, include_directories('.')],
        dependencies: [common_dep, spatial_input_dep])
    xext_dependencies += [declare_dependency(link_whole: observer_library,
        sources: [observer_v2_proto[1], observer_control_proto[1]])]
endif
