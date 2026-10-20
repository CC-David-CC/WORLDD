import re


def input_tests(cmake_text):
    lines = cmake_text.splitlines()
    starts = [i for i, line in enumerate(lines)
              if line.strip() == "if(SPATIAL_INPUT_TESTS)"]
    if len(starts) != 1:
        raise AssertionError(
            f"expected one SPATIAL_INPUT_TESTS block, found {len(starts)}")
    depth = 0
    end = None
    for i in range(starts[0], len(lines)):
        line = lines[i].strip()
        if line.startswith("if("):
            depth += 1
        if line == "endif()":
            depth -= 1
            if depth == 0:
                end = i
                break
    if end is None:
        raise AssertionError("SPATIAL_INPUT_TESTS block is not closed")
    block = "\n".join(lines[starts[0]:end + 1])
    names = re.findall(r"add_test\(\s*NAME\s+([^\s)]+)", block)
    suites = re.search(r"foreach\(identity_suite\s+([^)]*)\)", block)
    marker = chr(36) + "{identity_suite}"
    expanded = set()
    for name in names:
        if marker in name:
            if not suites:
                raise AssertionError("identity test registration has no foreach")
            expanded.update(name.replace(marker, suite)
                            for suite in suites.group(1).split())
        else:
            if chr(36) in name:
                raise AssertionError(f"unexpanded test name: {name}")
            expanded.add(name)
    if not expanded:
        raise AssertionError("no tests found in SPATIAL_INPUT_TESTS block")
    return expanded


def registration_names_from_text(text, source="CTestTestfile.cmake"):
    names = re.findall(r"add_test\(\[=\[(.*?)\]=\]", text)
    if not names:
        return []
    if len(names) != len(set(names)):
        raise AssertionError(f"duplicate CTest names in {source}")
    return names


def validate_inventory(registration_names, ctest_json, cmake_text, workdir):
    if not registration_names:
        raise AssertionError("no CTest registrations found in copied metadata")
    if len(registration_names) != len(set(registration_names)):
        raise AssertionError("duplicate CTest names in copied metadata")
    tests = ctest_json.get("tests", [])
    names = [test.get("name") for test in tests]
    if any(not name for name in names):
        raise AssertionError("CTest JSON contains a test without a name")
    if len(names) != len(set(names)):
        raise AssertionError("duplicate CTest names in generated metadata")
    if sorted(names) != sorted(registration_names):
        raise AssertionError(
            "CTest inventory differs from copied registration files")
    required = input_tests(cmake_text)
    missing = sorted(required - set(names))
    if missing:
        raise AssertionError(
            f"SPATIAL_INPUT_TESTS registrations missing from package: {missing}")
    actual_workdirs = [
        prop.get("value")
        for test in tests
        for prop in test.get("properties", [])
        if prop.get("name") == "WORKING_DIRECTORY"
    ]
    if len(actual_workdirs) != len(tests) or set(actual_workdirs) != {str(workdir)}:
        raise AssertionError(
            f"working dirs are not package build: {set(actual_workdirs)}")
    return {
        "registered_count": len(names),
        "input_count": len(required),
        "working_directory_count": len(actual_workdirs),
    }
