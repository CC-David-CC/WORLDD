"""Small parser for key/value records in cgroup v2 event files."""

def populated_value(contents: str) -> int:
    """Return the populated value, accepting other current or future keys."""
    fields: dict[str, int] = {}
    for line_number, line in enumerate(contents.splitlines(), start=1):
        parts = line.split()
        if len(parts) != 2:
            raise ValueError(f"invalid cgroup.events line {line_number}: {line!r}")
        key, value = parts
        if key in fields:
            raise ValueError(f"duplicate cgroup.events key {key!r}")
        try:
            number = int(value, 10)
        except ValueError as error:
            raise ValueError(f"invalid cgroup.events value for {key!r}: {value!r}") from error
        if number < 0:
            raise ValueError(f"negative cgroup.events value for {key!r}")
        fields[key] = number
    if "populated" not in fields:
        raise ValueError("cgroup.events is missing the populated field")
    return fields["populated"]
