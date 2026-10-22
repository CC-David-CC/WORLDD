#!/usr/bin/env python3
"""Reproduce the candidate's Protobuf generation in two isolated roots."""
import argparse
import hashlib
import json
import os
import pathlib
import re
import shlex
import subprocess
import sys

CANDIDATE = "580834ceeda112da0a1cdcd37ca7b288db1512fc"
PROVENANCE_SHA = "14cf36a0937bd9aa495f73f091693ef207c98e30a82b6dfdba567dee7615f1ab"
H = pathlib.Path("/home/dflanag3/spatial-artifacts/m08-issuer-fixture-580834ceeda1-20261021")
PROVENANCE = H / "independent-extract-01/provenance.json"
SOURCE = pathlib.Path("/home/dflanag3/src/m08-issuer-durability-precedence-repair-20261021/spatial-os-luna-handoff")
BUILD = pathlib.Path("/home/dflanag3/spatial-artifacts/m08-issuer-durability-precedence-repair-20261021/build/Debug")
PRODUCTION_OFF_PY = H / "prepared/m08-issuer-fixture-580834ce-prepare-01/runtime-production-off/Debug/python"
OUT_PARENT = H / "packaging-cache-repair-01"
PROTOC = pathlib.Path("/usr/bin/protoc")
CMAKE = pathlib.Path("/usr/bin/cmake")
NINJA = pathlib.Path("/usr/bin/ninja")
COMPAT = "src/workspace/copy_worker_python_compat.py"
PINNED_TOOLS = {
    "protoc": ("libprotoc 3.21.12", "fc386306299ba6a2f3668a0bb85cb059f389d52842f72dc618626a96c60506c7", "e13a55dd717e18fbb386dd4531248a3021a3e7ddb97142676d895ba63e81a2d5"),
    "cmake": ("cmake version 3.28.3", "1c5227af4edd22d8d689def545e18ee458260c0fd579eba2187967f38817e638", "c42a6bc1a3e08f43081fa596e3fc32852c5ee8874bd3868e5a057ae98a66cbd5"),
    "ninja": ("1.11.1", "5965527e09fe2b3787772aa4f711d6a36b393e7f2fcaa744a7a96c5a4ddf59cb", "a89a3aa1a104c52b32551ed0e1d5c98eab76d1acab1d74d2aee1598d86742810"),
}
DEFAULT_TOOLCHAIN = {name: {"version": values[0], "sha256": values[1], "closure_sha256": values[2]}
                    for name, values in PINNED_TOOLS.items()}
DEFAULT_CONTRACT = {
    "schema": 1, "candidate": CANDIDATE, "provenance_path": str(PROVENANCE),
    "provenance_sha256": PROVENANCE_SHA, "source_root": str(SOURCE), "build_dir": str(BUILD),
    "production_off_python_path": str(PRODUCTION_OFF_PY), "artifact_parent": str(OUT_PARENT),
    "source_file_count": 1222, "recipe_count": 67, "candidate_python_output_count": 36,
    "production_off_python_output_count": 30, "cpp_output_count": 66, "round_output_count": 102,
    "toolchain": DEFAULT_TOOLCHAIN,
}

class Refusal(ValueError):
    pass

def need(ok, message):
    if not ok:
        raise Refusal(message)

def sha_bytes(data):
    return hashlib.sha256(data).hexdigest()

def sha_file(path):
    h = hashlib.sha256()
    with pathlib.Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()

def canon_sha(value):
    return sha_bytes(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode())

def inside(path, root):
    path, root = pathlib.Path(path).resolve(), pathlib.Path(root).resolve()
    return path == root or root in path.parents

def pinned_source(source, relative):
    rel = pathlib.PurePosixPath(relative)
    need(not rel.is_absolute() and ".." not in rel.parts, "unsafe source path")
    path = source.joinpath(*rel.parts)
    need(path.is_file() and not path.is_symlink() and inside(path, source), "missing or linked source: " + relative)
    return path

def verify_sources(source, provenance, expected_count=None):
    pins = provenance.get("source_sha256")
    need(isinstance(pins, dict) and bool(pins), "source inventory is empty or malformed")
    if expected_count is not None:
        need(len(pins) == expected_count, "source inventory count mismatch")
    seen = {}
    for rel, expected in sorted(pins.items()):
        actual = sha_file(pinned_source(source, rel))
        need(actual == expected, "source hash mismatch: " + rel)
        seen[rel] = actual
    return canon_sha(seen), seen

def cache_values(path):
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line and ":" in line.split("=", 1)[0]:
            key, value = line.split("=", 1)
            values[key.split(":", 1)[0]] = value
    return values

def chain(line):
    parts, current = [], []
    for token in shlex.split(line, posix=True):
        if token == "&&":
            need(current, "empty recipe command")
            parts.append(current)
            current = []
        else:
            need(token not in (";", "|", "||", ">", ">>", "<", "2>", "&") and "$" not in token and "`" not in token,
                 "shell operator or expansion in recipe")
            current.append(token)
    need(current, "empty trailing recipe command")
    parts.append(current)
    return parts

def parse_recipe(line, source, build):
    parts = chain(line)
    need(parts[0] == ["cd", str(build)], "recipe cwd mismatch")
    commands = parts[1:]
    protoc = [x for x in commands if x and x[0] == str(PROTOC)]
    need(len(protoc) == 1, "recipe must contain exactly one pinned protoc call")
    allowed = []
    for cmd in commands:
        if cmd[0] == str(PROTOC):
            allowed.append("protoc")
        elif cmd[:3] == [str(CMAKE), "-E", "make_directory"] and len(cmd) == 4:
            need(inside(cmd[3], build), "mkdir escapes build root")
            allowed.append("mkdir")
        elif cmd[:3] == [str(CMAKE), "-E", "copy_if_different"] and len(cmd) == 5:
            need(pathlib.Path(cmd[3]).resolve() == (source / COMPAT).resolve(), "copy source differs from handwritten compatibility module")
            need(pathlib.Path(cmd[4]).resolve() == (build / "python/spatial/workspace/v1/copy_worker_pb2.py").resolve(),
                 "copy destination differs from expected binding")
            allowed.append("compat-copy")
        else:
            raise Refusal("recipe contains an unapproved command")
    need(allowed in (["protoc"], ["mkdir", "protoc"], ["protoc", "compat-copy"], ["mkdir", "protoc", "compat-copy"]), "recipe ordering is unsupported")
    parsed = {"segments": commands, "inputs": [], "proto_paths": [], "outputs": {}, "copy": None}
    args = protoc[0][1:]
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in ("--cpp_out", "--python_out"):
            need(i + 1 < len(args), "protobuf output flag has no value")
            value = args[i + 1]
            if arg == "--cpp_out" and value.startswith(":"):
                value = value[1:]
            parsed["outputs"][arg[2:-4]] = value
            i += 2
        elif arg.startswith("--cpp_out=") or arg.startswith("--python_out="):
            name, value = arg.split("=", 1)
            parsed["outputs"][name[2:-4]] = value
            i += 1
        elif arg.startswith("--proto_path="):
            parsed["proto_paths"].append(arg.split("=", 1)[1])
            i += 1
        elif arg == "-I":
            need(i + 1 < len(args), "-I has no path")
            parsed["proto_paths"].append(args[i + 1])
            i += 2
        elif arg.startswith("-I"):
            parsed["proto_paths"].append(arg[2:])
            i += 1
        elif arg.endswith(".proto"):
            parsed["inputs"].append(arg)
            i += 1
        else:
            raise Refusal("unreviewed protoc argument: " + arg)
    need(set(parsed["outputs"]) in ({"cpp"}, {"python"}) and parsed["inputs"] and parsed["proto_paths"],
         "unsupported protoc recipe shape")
    for value in parsed["proto_paths"] + parsed["inputs"]:
        p = pathlib.Path(value)
        need(p.is_absolute() and inside(p, source), "proto path or input escaped source root")
    for value in parsed["outputs"].values():
        need(pathlib.Path(value).is_absolute() and inside(value, build), "generation output escaped build root")
    for cmd in commands:
        if cmd[0] == str(CMAKE) and cmd[2] == "make_directory":
            parsed["segments"][commands.index(cmd)] = cmd
        elif cmd[0] == str(CMAKE) and cmd[2] == "copy_if_different":
            parsed["copy"] = cmd[3:]
    return parsed

def recipes_for(build, source, expected_count=None):
    result = subprocess.run([str(NINJA), "-C", str(build), "-t", "commands"], check=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            env={"PATH": "/usr/bin:/bin", "LC_ALL": "C", "TZ": "UTC"})
    recipes = []
    for line in result.stdout.splitlines():
        tokens = shlex.split(line, posix=True)
        if str(PROTOC) in tokens:
            recipes.append(parse_recipe(line, source, build))
        elif "copy_if_different" in tokens and "copy_worker_python_compat.py" in tokens:
            raise Refusal("unpaired compatibility copy command")
    need(bool(recipes), "protoc recipe inventory is empty")
    if expected_count is not None:
        need(len(recipes) == expected_count, "protoc recipe count mismatch")
    return recipes, sha_bytes(result.stdout.encode())

def source_inputs(source, source_manifest, recipes):
    names = {COMPAT}
    names.update(x for x in source_manifest if x.endswith(".proto"))
    need(names <= source_manifest.keys(), "proto input is outside pinned source inventory")
    return {name: source_manifest[name] for name in sorted(names)}

def tool_deps(path):
    result = subprocess.run(["/usr/bin/ldd", str(path)], check=True, capture_output=True, text=True,
                            env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"})
    deps = []
    for line in result.stdout.splitlines():
        match = re.search(r"=>\s+(\S+)|^\s*(/\S+)\s+\(", line)
        if match:
            dep = pathlib.Path(match.group(1) or match.group(2)).resolve()
            need(dep.is_file(), "unresolved tool dependency")
            deps.append({"path": str(dep), "sha256": sha_file(dep)})
    return sorted(deps, key=lambda x: x["path"])

def require_tool_pin(name, version, digest, closure_digest):
    need(name in PINNED_TOOLS, "unreviewed generator tool")
    expected_version, expected_digest, expected_closure = PINNED_TOOLS[name]
    need(version == expected_version, name + " version mismatch")
    need(digest == expected_digest, name + " executable hash mismatch")
    need(closure_digest == expected_closure, name + " dependency closure mismatch")

def verify_toolchain(tools, contract):
    need(set(tools) == set(PINNED_TOOLS), "toolchain inventory differs")
    for name, item in tools.items():
        require_tool_pin(name, item.get("version"), item.get("sha256"), canon_sha(item.get("dependencies")))
        expected = contract["toolchain"][name]
        need(item.get("version") == expected["version"] and item.get("sha256") == expected["sha256"] and
             canon_sha(item.get("dependencies")) == expected["closure_sha256"], name + " differs from contract toolchain")

def remap(value, old, new):
    path = pathlib.Path(value)
    relative = path.resolve().relative_to(pathlib.Path(old).resolve())
    mapped = pathlib.Path(new) / relative
    need(inside(mapped, new), "path remapping escaped owned root")
    return str(mapped)

def rebase_protoc(args, source, build, stage_source, stage_build):
    out, i = [], 0
    while i < len(args):
        arg = args[i]
        if arg in ("--cpp_out", "--python_out"):
            value = args[i + 1]
            prefix = ":" if arg == "--cpp_out" and value.startswith(":") else ""
            if prefix:
                value = value[1:]
            out.extend([arg, prefix + remap(value, build, stage_build)])
            i += 2
        elif arg.startswith("--cpp_out=") or arg.startswith("--python_out="):
            name, value = arg.split("=", 1)
            prefix = ":" if name == "--cpp_out" and value.startswith(":") else ""
            if prefix:
                value = value[1:]
            out.append(name + "=" + prefix + remap(value, build, stage_build))
            i += 1
        elif arg.startswith("--proto_path="):
            out.append("--proto_path=" + remap(arg.split("=", 1)[1], source, stage_source))
            i += 1
        elif arg == "-I":
            out.extend([arg, remap(args[i + 1], source, stage_source)])
            i += 2
        elif arg.startswith("-I"):
            out.append("-I" + remap(arg[2:], source, stage_source))
            i += 1
        else:
            out.append(remap(arg, source, stage_source))
            i += 1
    return out

def generated_map(root, allow_bytecode_cache=False):
    result = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise Refusal("generated tree contains symlink")
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        parts = pathlib.PurePosixPath(rel).parts
        if "__pycache__" in parts and path.suffix in (".pyc", ".pyo"):
            need(allow_bytecode_cache, "bytecode appeared in generated source tree")
            continue
        if path.suffix in (".pyc", ".pyo"):
            raise Refusal("bytecode outside __pycache__ in generated source tree")
        if path.name.endswith("_pb2.py") or path.name.endswith(".pb.cc") or path.name.endswith(".pb.h"):
            result[rel] = sha_file(path)
        else:
            raise Refusal("unexpected generation output: " + rel)
    need(result, "generator produced no outputs")
    return result

def compare_maps(first, second, label="generated outputs"):
    need(first.keys() == second.keys(), label + " file coverage mismatch")
    for name, digest in first.items():
        need(second[name] == digest, label + " bytes differ: " + name)

def compare_subset(expected, observed, label):
    need(expected.keys() == observed.keys(), label + " file coverage mismatch")
    for name, digest in expected.items():
        need(observed[name] == digest, label + " hash mismatch: " + name)

def cpp_map(root):
    result = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise Refusal("candidate C++ tree contains symlink")
        if path.is_file() and path.name.endswith((".pb.cc", ".pb.h")):
            result[path.relative_to(root).as_posix()] = sha_file(path)
    return result

def copy_pinned_inputs(source, inputs, destination):
    for name, expected in inputs.items():
        original = pinned_source(source, name)
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(original.read_bytes())
        need(sha_file(target) == expected, "staged source bytes changed: " + name)

def replay_round(label, root, source, build, recipes, inputs, epoch):
    stage_source = root / "source"
    stage_build = root / "build"
    stage_source.mkdir(parents=True)
    stage_build.mkdir(parents=True)
    copy_pinned_inputs(source, inputs, stage_source)
    logs = root / "command-logs"
    logs.mkdir()
    env = {"PATH": "/usr/bin:/bin", "LC_ALL": "C", "LANG": "C", "TZ": "UTC",
           "SOURCE_DATE_EPOCH": epoch, "HOME": str(root), "TMPDIR": str(root),
           "PYTHONDONTWRITEBYTECODE": "1", "PYTHONHASHSEED": "0"}
    for n, recipe in enumerate(recipes):
        for cmd in recipe["segments"]:
            if cmd[0] == str(CMAKE) and cmd[2] == "make_directory":
                target = pathlib.Path(remap(cmd[3], build, stage_build))
                subprocess.run([str(CMAKE), "-E", "make_directory", str(target)], check=True, env=env)
        for original_output in recipe["outputs"].values():
            output_dir = pathlib.Path(remap(original_output, build, stage_build))
            need(inside(output_dir, stage_build), "protoc output directory escaped staged build")
            output_dir.mkdir(parents=True, exist_ok=True)
        args = rebase_protoc(recipe["segments"][[x[0] for x in recipe["segments"]].index(str(PROTOC))][1:],
                             source, build, stage_source, stage_build)
        result = subprocess.run([str(PROTOC), *args], cwd=stage_build, env=env,
                                capture_output=True, timeout=60, check=False)
        (logs / f"{n:03d}.stdout").write_bytes(result.stdout)
        (logs / f"{n:03d}.stderr").write_bytes(result.stderr)
        need(result.returncode == 0, f"{label} recipe {n} failed with exit {result.returncode}")
        if recipe["copy"]:
            src, dest = recipe["copy"]
            subprocess.run([str(CMAKE), "-E", "copy_if_different",
                            remap(src, source, stage_source), remap(dest, build, stage_build)],
                           check=True, env=env)
    return stage_build

def load_contract(path):
    if path is None:
        payload = json.dumps(DEFAULT_CONTRACT, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    else:
        contract_path = pathlib.Path(path).resolve(strict=True)
        need(contract_path.is_file(), "contract is not a regular file")
        payload = contract_path.read_bytes()
    try:
        contract = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Refusal("contract is not valid JSON") from exc
    keys = {"schema", "candidate", "provenance_path", "provenance_sha256", "source_root", "build_dir",
            "production_off_python_path", "artifact_parent", "source_file_count", "recipe_count",
            "candidate_python_output_count", "production_off_python_output_count", "cpp_output_count",
            "round_output_count", "toolchain"}
    need(isinstance(contract, dict) and set(contract) == keys, "contract key set mismatch")
    need(contract["schema"] == 1, "unsupported contract schema")
    for key in ("provenance_path", "source_root", "build_dir", "production_off_python_path", "artifact_parent"):
        value = pathlib.Path(contract[key])
        need(value.is_absolute(), "contract path is not absolute: " + key)
    for key in ("source_file_count", "recipe_count", "candidate_python_output_count", "production_off_python_output_count", "cpp_output_count", "round_output_count"):
        need(type(contract[key]) is int and contract[key] > 0, "invalid contract count: " + key)
    need(contract["production_off_python_output_count"] <= contract["candidate_python_output_count"], "production-off count exceeds candidate")
    need(contract["round_output_count"] == contract["candidate_python_output_count"] + contract["cpp_output_count"], "round output count is inconsistent")
    need(type(contract["candidate"]) is str and re.fullmatch(r"[0-9a-f]{40}", contract["candidate"]), "invalid candidate pin")
    for key in ("provenance_sha256",):
        need(type(contract[key]) is str and re.fullmatch(r"[0-9a-f]{64}", contract[key]), "invalid contract digest: " + key)
    need(contract["toolchain"] == {name: {"version": values[0], "sha256": values[1], "closure_sha256": values[2]}
                                   for name, values in PINNED_TOOLS.items()}, "contract toolchain differs from reviewed generator")
    return contract, sha_bytes(payload)

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--contract", type=pathlib.Path, help="versioned JSON provenance and toolchain contract")
    ap.add_argument("--artifact-root", required=True, type=pathlib.Path)
    args = ap.parse_args(argv)
    verifier_sha = sha_file(__file__)
    contract, contract_sha = load_contract(args.contract)
    global CANDIDATE, PROVENANCE, PROVENANCE_SHA, SOURCE, BUILD, PRODUCTION_OFF_PY, OUT_PARENT
    CANDIDATE = contract["candidate"]
    PROVENANCE = pathlib.Path(contract["provenance_path"])
    PROVENANCE_SHA = contract["provenance_sha256"]
    SOURCE = pathlib.Path(contract["source_root"])
    BUILD = pathlib.Path(contract["build_dir"])
    PRODUCTION_OFF_PY = pathlib.Path(contract["production_off_python_path"])
    OUT_PARENT = pathlib.Path(contract["artifact_parent"])
    source, build = SOURCE.resolve(strict=True), BUILD.resolve(strict=True)
    provenance = PROVENANCE.resolve(strict=True)
    need(str(provenance) == contract["provenance_path"] and sha_file(provenance) == PROVENANCE_SHA, "provenance pin mismatch")
    data = json.loads(provenance.read_text(encoding="utf-8"))
    need(data.get("candidate") == CANDIDATE and data.get("source_root") == str(source), "candidate/source root mismatch")
    need(str(source) == contract["source_root"] and str(build) == contract["build_dir"], "contract paths must be canonical")
    need(str(PRODUCTION_OFF_PY.resolve(strict=True)) == contract["production_off_python_path"], "production-off contract path mismatch")
    need(str(OUT_PARENT.resolve(strict=True)) == contract["artifact_parent"], "artifact parent contract path mismatch")
    need(PRODUCTION_OFF_PY.resolve(strict=True).is_dir(), "production-off Python path missing")
    need(subprocess.check_output(["git", "-C", str(source.parent), "rev-parse", "HEAD"], text=True).strip() == CANDIDATE, "source HEAD differs from candidate")
    need(not subprocess.check_output(["git", "-C", str(source.parent), "status", "--porcelain"], text=True).strip(), "source checkout is dirty")
    cache_path = build / "CMakeCache.txt"
    cache = cache_values(cache_path)
    need(cache.get("CMAKE_HOME_DIRECTORY") == str(source) and cache.get("CMAKE_BUILD_TYPE") == "Debug", "CMakeCache path/profile mismatch")
    need(cache.get("Protobuf_PROTOC_EXECUTABLE") == str(PROTOC) and data["profiles"]["Debug"]["build"] == str(build), "CMakeCache generator/build mismatch")
    source_sha, source_manifest = verify_sources(source, data, contract["source_file_count"])
    cache_sha = sha_file(build / "CMakeCache.txt")
    build_ninja_sha = sha_file(build / "build.ninja")
    recipes, commands_sha = recipes_for(build, source, contract["recipe_count"])
    inputs = source_inputs(source, source_manifest, recipes)
    input_sha = canon_sha(inputs)
    candidate_python_path = (build / "python").resolve(strict=True)
    candidate_python = generated_map(candidate_python_path, allow_bytecode_cache=True)
    production_off = PRODUCTION_OFF_PY.resolve(strict=True)
    production_off_python = generated_map(production_off, allow_bytecode_cache=True)
    cpp_actual = cpp_map(build)
    need(len(candidate_python) == contract["candidate_python_output_count"] and len(production_off_python) == contract["production_off_python_output_count"] and len(cpp_actual) == contract["cpp_output_count"],
         "candidate generated output inventory count mismatch")
    version = subprocess.check_output([str(PROTOC), "--version"], text=True, env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"}).strip()
    cmake_version = subprocess.check_output([str(CMAKE), "--version"], text=True, env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"}).splitlines()[0]
    ninja_version = subprocess.check_output([str(NINJA), "--version"], text=True, env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"}).strip()
    tools = {}
    for tool in (PROTOC, CMAKE, NINJA):
        tools[tool.name] = {"path": str(tool.resolve()), "sha256": sha_file(tool), "dependencies": tool_deps(tool)}
    tools["protoc"]["version"] = version
    tools["cmake"]["version"] = cmake_version
    tools["ninja"]["version"] = ninja_version
    verify_toolchain(tools, contract)
    epoch = subprocess.check_output(["git", "-C", str(source.parent), "show", "-s", "--format=%ct", CANDIDATE],
                                    text=True, env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"}).strip()
    artifact = args.artifact_root.resolve()
    need(OUT_PARENT.resolve() in artifact.parents and not artifact.exists(), "artifact root must be new under approved external parent")
    artifact.mkdir(parents=True)
    rounds = []
    for label, rel in (("round_a", pathlib.Path("round-a")), ("round_b", pathlib.Path("different-layout/round-b"))):
        stage = artifact / rel
        stage.mkdir(parents=True)
        stage_build = replay_round(label, stage, source, build, recipes, inputs, epoch)
        outputs = generated_map(stage_build)
        rounds.append((label, stage_build, outputs))
    compare_maps(rounds[0][2], rounds[1][2])
    need(len(rounds[0][2]) == contract["round_output_count"], "generated output count differs from contract")
    round_python = {name.removeprefix("python/"): value for name, value in rounds[0][2].items() if name.startswith("python/")}
    compare_maps(candidate_python, round_python, "candidate generated Python bindings")
    need(production_off_python.keys() <= candidate_python.keys(), "production-off bindings are not a candidate subset")
    compare_subset(production_off_python, {name: candidate_python[name] for name in production_off_python}, "production-off Python bindings")
    expected_python = {}
    for profile in ("Debug", "ASan-UBSan"):
        generated = data["profiles"][profile].get("generated_python_sha256", {})
        profile_python = {name.removeprefix("python/"): digest for name, digest in generated.items()
                          if name.startswith("python/") and name.endswith(".py") and "__pycache__" not in name}
        need(profile_python.keys() == round_python.keys(), "profile-generated Python inventory differs: " + profile)
        compare_subset(profile_python, round_python, "provenance " + profile + " Python bindings")
        if expected_python:
            compare_maps(expected_python, profile_python, "Debug/SAN Python binding agreement")
        expected_python = profile_python
    cpp_generated = {name: digest for name, digest in rounds[0][2].items()
                     if name.endswith(".pb.cc") or name.endswith(".pb.h")}
    compare_maps(cpp_actual, cpp_generated)
    need(len(cpp_actual) == contract["cpp_output_count"], "candidate C++ count differs from contract")
    # Recheck all pinned bytes after generation to catch concurrent source or tool changes.
    need(verify_sources(source, data, contract["source_file_count"])[0] == source_sha, "source changed during generation")
    need(generated_map(candidate_python_path, allow_bytecode_cache=True) == candidate_python, "candidate generated Python changed during generation")
    need(generated_map(production_off, allow_bytecode_cache=True) == production_off_python, "production-off Python changed during generation")
    need(sha_file(provenance) == PROVENANCE_SHA, "provenance changed during generation")
    need(sha_file(cache_path) == cache_sha and sha_file(build / "build.ninja") == build_ninja_sha, "build inputs changed during generation")
    need(recipes_for(build, source, contract["recipe_count"])[1] == commands_sha, "Ninja generation recipes changed during generation")
    need(cpp_map(build) == cpp_actual, "candidate generated C++ changed during generation")
    for tool in (PROTOC, CMAKE, NINJA):
        need(sha_file(tool) == tools[tool.name]["sha256"], "generator tool changed during generation")
        need(tool_deps(tool) == tools[tool.name]["dependencies"], "generator dependency closure changed during generation")
    verify_toolchain(tools, contract)
    need(sha_file(__file__) == verifier_sha, "verifier source changed during generation")
    if args.contract is not None:
        need(sha_file(args.contract.resolve(strict=True)) == contract_sha, "contract changed during generation")
    proof = {
        "schema": 1, "status": "PROTOBUF_REPRODUCIBILITY_PASSED",
        "qualification": "UNQUALIFIED_M08", "qualification_effect": "NONE", "candidate": CANDIDATE,
        "contract_schema": contract["schema"], "contract_sha256": contract_sha,
        "contract_manifest_sha256": canon_sha(contract), "contract": contract,
        "verifier_sha256": verifier_sha,
        "provenance_sha256": PROVENANCE_SHA, "source_manifest_sha256": source_sha,
        "source_file_count": len(source_manifest), "source_manifest": source_manifest,
        "input_manifest": inputs, "recipes": recipes,
        "recipes_sha256": canon_sha(recipes), "ninja_commands_sha256": commands_sha,
        "inputs_sha256": input_sha, "cmake_cache_sha256": cache_sha,
        "build_ninja_sha256": build_ninja_sha,
        "controlled_environment": {"PATH": "/usr/bin:/bin", "LC_ALL": "C", "LANG": "C", "TZ": "UTC",
                                  "SOURCE_DATE_EPOCH": epoch, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONHASHSEED": "0"},
        "generator": {"protoc_path": str(PROTOC), "protoc_version": version, "cmake_version": cmake_version, "ninja_version": ninja_version,
                      "protoc_sha256": tools["protoc"]["sha256"],
                      "closure_sha256": canon_sha(tools["protoc"]["dependencies"]),
                      "cmake_sha256": tools["cmake"]["sha256"], "ninja_sha256": tools["ninja"]["sha256"],
                      "tool_closures": tools},
        "round_a": {"output_count": len(rounds[0][2]), "manifest_sha256": canon_sha(rounds[0][2])},
        "round_b": {"output_count": len(rounds[1][2]), "manifest_sha256": canon_sha(rounds[1][2])},
        "rounds_equal": True,
        "candidate_output_comparison": {
            "candidate_python_path": str(candidate_python_path), "candidate_python_output_count": len(candidate_python),
            "candidate_python_manifest_sha256": canon_sha(candidate_python), "candidate_python_equal": True,
            "production_off_python_path": str(production_off), "production_off_python_output_count": len(production_off_python),
            "production_off_python_manifest_sha256": canon_sha(production_off_python), "production_off_subset_equal": True,
            "cpp_build_path": str(build), "cpp_output_count": len(cpp_actual),
            "cpp_manifest_sha256": canon_sha(cpp_actual), "cpp_equal": True},
        "recipe_count": len(recipes), "input_count": len(inputs),
        "round_a_outputs": rounds[0][2], "round_b_outputs": rounds[1][2]
    }
    output = artifact / "protobuf-reproducibility.json"
    output.write_text(json.dumps(proof, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": proof["status"], "proof_path": str(output),
                      "proof_sha256": sha_file(output)}, sort_keys=True))
    return 0

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print("REFUSED: " + str(exc), file=sys.stderr)
        raise SystemExit(2)
