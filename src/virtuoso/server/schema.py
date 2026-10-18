"""A small, strict JSON Schema subset for the operation registry.

The registry's schemas are published to MCP clients as tool input and
output schemas, and the server validates every request and every response
against them before either crosses a binding. Only the keywords the
registry uses are supported; an unsupported keyword is a programming
error, not a silent pass.
"""

from __future__ import annotations

import re
from typing import Any

_SUPPORTED = {
    "type", "properties", "required", "additionalProperties", "enum", "const",
    "minLength", "maxLength", "pattern", "minimum", "maximum", "items",
    "description", "maxItems",
}
_TYPES = {
    "object": dict,
    "array": list,
    "string": str,
    "boolean": bool,
    "null": type(None),
}


class SchemaError(ValueError):
    """A value does not match its schema. The message names the field."""


def _matches_type(value: object, name: str) -> bool:
    if name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if name in {"string", "array", "object", "null"}:
        return isinstance(value, _TYPES[name])
    if name == "boolean":
        return isinstance(value, bool)
    raise AssertionError(f"unsupported schema type: {name}")


def validate(value: object, schema: dict[str, Any], *, where: str = "request") -> None:
    unknown = set(schema) - _SUPPORTED
    if unknown:
        raise AssertionError(f"unsupported schema keywords: {sorted(unknown)}")
    expected = schema.get("type")
    if expected is not None:
        names = expected if isinstance(expected, list) else [expected]
        if not any(_matches_type(value, name) for name in names):
            raise SchemaError(f"{where} must be {' or '.join(names)}")
    if "const" in schema and value != schema["const"]:
        raise SchemaError(f"{where} must be {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        raise SchemaError(
            f"{where} must be one of " + ", ".join(repr(item) for item in schema["enum"])
        )
    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            raise SchemaError(f"{where} must have at least {schema['minLength']} characters")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            raise SchemaError(f"{where} must have at most {schema['maxLength']} characters")
        if "pattern" in schema and re.search(schema["pattern"], value) is None:
            raise SchemaError(f"{where} has an invalid format")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            raise SchemaError(f"{where} must be at least {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            raise SchemaError(f"{where} must be at most {schema['maximum']}")
    if isinstance(value, list):
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            raise SchemaError(f"{where} must have at most {schema['maxItems']} entries")
        if "items" in schema:
            for index, entry in enumerate(value):
                validate(entry, schema["items"], where=f"{where}[{index}]")
    if isinstance(value, dict):
        properties: dict[str, Any] = schema.get("properties", {})
        missing = [name for name in schema.get("required", ()) if name not in value]
        if missing:
            raise SchemaError(f"{where} is missing required field(s): {', '.join(missing)}")
        if schema.get("additionalProperties") is False:
            extra = sorted(set(value) - set(properties))
            if extra:
                raise SchemaError(f"{where} has unknown field(s): {', '.join(extra)}")
        for name, entry in value.items():
            if name in properties:
                validate(entry, properties[name], where=f"{where}.{name}")
