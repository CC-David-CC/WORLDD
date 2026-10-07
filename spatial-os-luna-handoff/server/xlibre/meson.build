spatial_proto = custom_target('spatial-input-protobuf',
    input: 'input.proto', output: ['input.pb-c.c', 'input.pb-c.h'],
    command: [find_program('protoc-c'), '--c_out=@OUTDIR@',
              '--proto_path=' + meson.current_source_dir(), meson.current_source_dir() / 'input.proto'])
srcs_xext += files('spatialinput.c')
srcs_xext += spatial_proto
spatial_input_dep = dependency('libprotobuf-c', version: '>=1.4')
