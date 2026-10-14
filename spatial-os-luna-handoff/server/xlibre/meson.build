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
