"""Prove the actor marker migration adds only three specified protobuf fields."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from google.protobuf import descriptor_pb2

root = Path(sys.argv[1]).resolve()
relative = 'spatial/core/v1/core.proto'
old = subprocess.check_output(['git', '-C', str(root), 'show',
                              '1ecbebe:spatial-os-luna-handoff/proto/' + relative])
current = (root / 'proto' / relative).read_bytes()
with tempfile.TemporaryDirectory(prefix='spatial-actor-schema-') as temporary:
    work = Path(temporary)
    descriptors = []
    for label, content in (('old', old), ('new', current)):
        source = work / label / relative
        source.parent.mkdir(parents=True)
        source.write_bytes(content)
        output = work / (label + '.pb')
        subprocess.run(['protoc', '-I' + str(work / label), '-I' + str(root / 'proto'),
                        '-I' + str(root / 'proto/spatial/security/v1'),
                        '--descriptor_set_out=' + str(output), relative], check=True)
        descriptors.append(descriptor_pb2.FileDescriptorSet.FromString(output.read_bytes()))
    before, after = descriptors
    additions = {'RenderConfig': {'actor_size': 9, 'actor_color': 10}, 'RenderScene': {'actors': 9}}
    found = []
    for definition in after.file:
        for message in definition.message_type:
            names = additions.get(message.name, {})
            for index in range(len(message.field) - 1, -1, -1):
                field = message.field[index]
                if field.name in names:
                    assert field.number == names[field.name]
                    found.append(message.name + '.' + field.name)
                    del message.field[index]
            # proto3 optional fields also introduce a synthetic oneof.
            if message.name == 'RenderConfig':
                indexes = [i for i, item in enumerate(message.oneof_decl) if item.name == '_actor_size']
                assert len(indexes) == 1 and indexes[0] == len(message.oneof_decl) - 1
                del message.oneof_decl[indexes[0]]
    assert sorted(found) == ['RenderConfig.actor_color', 'RenderConfig.actor_size', 'RenderScene.actors']
    assert before == after, 'Existing protobuf definitions changed'
print(json.dumps(dict(status='additive-only', additions=sorted(found),
                      previous_sha256=hashlib.sha256(old).hexdigest(),
                      current_sha256=hashlib.sha256(current).hexdigest()), indent=2))
