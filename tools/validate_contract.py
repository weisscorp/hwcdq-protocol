#!/usr/bin/env python3
"""Validate the checked-in HWCDQ contract and its normative vectors.

This is a deliberately small, stdlib-only semantic validator.  It validates
the invariants used by the native implementations; it is not, and does not
claim to be, a generic JSON Schema implementation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import re
import string
import struct
import sys
import unicodedata
from typing import Any
from urllib.parse import unquote


CONTRACT_VERSION = "1.0.0"
SCHEMA_VERSION = 1
HEX_RE = re.compile(r"^(?:[0-9a-f]{2})*$")
EXPECTED_COMMANDS = {
    0x01: "get_firmware",
    0x02: "check_credential",
    0x04: "get_serial",
    0x05: "get_config",
    0x06: "get_telemetry",
    0x07: "set_voltage",
    0x08: "set_current",
    0x0C: "output_control",
}


class ContractValidationError(ValueError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ContractValidationError(message)


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ContractValidationError(f"cannot load {path}: {exc}") from exc
    require(isinstance(value, dict), f"{path} must contain a JSON object")
    if path.name not in {"manifest.json", "contract.schema.json"}:
        require(value.get("schema_version") == SCHEMA_VERSION, f"{path}: schema_version must be 1")
        require(value.get("contract_version") == CONTRACT_VERSION, f"{path}: contract_version must be 1.0.0")
    return value


def checked_hex(value: object, location: str) -> bytes:
    require(isinstance(value, str), f"{location} must be a string")
    require(bool(HEX_RE.fullmatch(value)), f"{location} must be lowercase contiguous even-length hex")
    return bytes.fromhex(value)


def validate_hex_convention(value: object, location: str = "$", key: str = "") -> None:
    if isinstance(value, dict):
        for child_key, child in value.items():
            validate_hex_convention(child, f"{location}.{child_key}", child_key)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            validate_hex_convention(child, f"{location}[{index}]", key)
    elif key.endswith("_hex"):
        checked_hex(value, location)


def github_heading_slug(heading: str) -> str:
    """Return the GitHub-style anchor used by this repository's Markdown headings."""

    without_html = re.sub(r"<[^>]*>", "", heading.strip()).lower()
    characters: list[str] = []
    for character in without_html:
        if character in "-_":
            characters.append(character)
        elif character in string.punctuation or unicodedata.category(character).startswith("P"):
            continue
        elif character.isspace():
            characters.append("-")
        else:
            characters.append(character)
    return "".join(characters)


def markdown_anchors(path: Path) -> set[str]:
    anchors: set[str] = set()
    occurrences: dict[str, int] = {}
    fenced = False
    fence_marker = ""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise ContractValidationError(f"cannot read Markdown evidence {path}: {exc}") from exc
    for line in lines:
        stripped = line.lstrip()
        marker = stripped[:3]
        if marker in {"```", "~~~"}:
            if not fenced:
                fenced = True
                fence_marker = marker
            elif marker == fence_marker:
                fenced = False
                fence_marker = ""
            continue
        if fenced:
            continue
        match = re.match(r"^ {0,3}#{1,6}\s+(.+?)\s*$", line)
        if match is None:
            continue
        heading = re.sub(r"\s+#+\s*$", "", match.group(1))
        base = github_heading_slug(heading)
        if not base:
            continue
        occurrence = occurrences.get(base, 0)
        occurrences[base] = occurrence + 1
        anchors.add(base if occurrence == 0 else f"{base}-{occurrence}")
    return anchors


def evidence_references(value: object, location: str) -> list[tuple[str, str]]:
    references: list[tuple[str, str]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_location = f"{location}.{key}"
            if key == "evidence":
                require(isinstance(child, list) and child, f"{child_location} must be a nonempty list")
                for index, reference in enumerate(child):
                    require(isinstance(reference, str) and reference, f"{child_location}[{index}] must be a string")
                    references.append((reference, f"{child_location}[{index}]"))
            else:
                references.extend(evidence_references(child, child_location))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            references.extend(evidence_references(child, f"{location}[{index}]"))
    return references


def validate_evidence_references(repo_root: Path, documents: dict[str, dict[str, Any]]) -> None:
    anchor_cache: dict[Path, set[str]] = {}
    for relative, document in documents.items():
        if relative in {"manifest.json", "contract.schema.json"}:
            continue
        for reference, location in evidence_references(document, relative):
            file_reference, separator, encoded_fragment = reference.partition("#")
            pure_path = PurePosixPath(file_reference)
            require(
                file_reference == pure_path.as_posix()
                and not pure_path.is_absolute()
                and ".." not in pure_path.parts,
                f"{location} has invalid repository-relative path {file_reference!r}",
            )
            target = repo_root / pure_path
            require(target.is_file(), f"{location} evidence file does not exist: {file_reference}")
            if not separator:
                continue
            fragment = unquote(encoded_fragment)
            require(fragment and target.suffix.casefold() == ".md", f"{location} has invalid Markdown anchor reference")
            anchors = anchor_cache.setdefault(target, markdown_anchors(target))
            require(fragment in anchors, f"{location} evidence anchor does not exist: {reference}")


def frame(opcode: int, payload: bytes = b"") -> bytes:
    require(0 <= opcode <= 0xFF, "internal opcode outside uint8")
    require(len(payload) <= 253, "internal payload exceeds 253 bytes")
    return bytes((len(payload) + 2, opcode)) + payload + bytes(((opcode + sum(payload)) & 0xFF,))


def validate_frame(raw: bytes) -> tuple[int, bytes]:
    if len(raw) < 3:
        raise ContractValidationError("packet.truncated")
    if raw[0] < 2:
        raise ContractValidationError("packet.length.minimum")
    if len(raw) != raw[0] + 1:
        raise ContractValidationError("packet.length.mismatch")
    opcode, payload = raw[1], raw[2:-1]
    if raw[-1] != ((opcode + sum(payload)) & 0xFF):
        raise ContractValidationError("packet.checksum.mismatch")
    return opcode, payload


def f32_bytes(decimal: str, location: str) -> bytes:
    require(isinstance(decimal, str), f"{location}.decimal must be a string")
    try:
        return struct.pack("<f", float(decimal))
    except (OverflowError, ValueError, struct.error) as exc:
        raise ContractValidationError(f"{location}.decimal is not binary32") from exc


def token_number(token: dict[str, Any], location: str) -> float:
    kind = token.get("kind", "decimal")
    if kind == "nan":
        return math.nan
    if kind == "positive_infinity":
        return math.inf
    if kind == "negative_infinity":
        return -math.inf
    if kind == "boolean":
        value = token.get("value")
        require(isinstance(value, bool), f"{location}.value must be boolean")
        return value  # type: ignore[return-value]
    require(kind == "decimal", f"{location}.kind is unsupported")
    decimal = token.get("decimal")
    require(isinstance(decimal, str), f"{location}.decimal must be a string")
    try:
        return float(decimal)
    except ValueError as exc:
        raise ContractValidationError(f"{location}.decimal is invalid") from exc


def canonical_positive_f32(value: float) -> float | None:
    if isinstance(value, bool) or not math.isfinite(value) or value <= 0:
        return None
    try:
        converted = struct.unpack("<f", struct.pack("<f", value))[0]
    except (OverflowError, struct.error):
        return None
    return converted if math.isfinite(converted) and converted > 0 else None


def validate_manifest(root: Path, manifest: dict[str, Any]) -> None:
    require(manifest.get("schema_version") == SCHEMA_VERSION, "manifest schema_version must be 1")
    require(manifest.get("contract_version") == CONTRACT_VERSION, "manifest contract_version must be 1.0.0")
    hashing = manifest.get("hashing")
    require(isinstance(hashing, dict), "manifest.hashing must be an object")
    require(hashing.get("algorithm") == "sha256", "manifest hash algorithm must be sha256")
    require(hashing.get("scope") == "exact_file_bytes", "manifest hash scope must be exact_file_bytes")
    require(hashing.get("manifest_excluded") is True, "manifest must explicitly exclude itself")
    files = manifest.get("normative_files")
    require(isinstance(files, list) and files, "manifest.normative_files must be nonempty")
    paths = [entry.get("path") for entry in files if isinstance(entry, dict)]
    require(len(paths) == len(files), "every manifest file entry must be an object with path")
    require(paths == sorted(paths), "manifest paths must be sorted")
    require(len(paths) == len(set(paths)), "manifest paths must be unique")
    require("manifest.json" not in paths, "manifest must not hash itself")
    actual_json = sorted(path.relative_to(root).as_posix() for path in root.rglob("*.json") if path.name != "manifest.json")
    require(paths == actual_json, "manifest must list every normative JSON except itself")
    for entry in files:
        relative = entry["path"]
        require(PurePosixPath(relative).as_posix() == relative and not relative.startswith("/"), f"invalid manifest path {relative}")
        digest = entry.get("sha256")
        require(isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest) is not None, f"invalid SHA-256 for {relative}")
        actual = hashlib.sha256((root / relative).read_bytes()).hexdigest()
        require(actual == digest, f"SHA-256 mismatch for {relative}: expected {digest}, got {actual}")


def validate_layout(layout: dict[str, Any], expected_size: int, location: str) -> None:
    require(layout.get("payload_bytes") == expected_size, f"{location} payload size mismatch")
    fields = layout.get("fields")
    require(isinstance(fields, list) and fields, f"{location}.fields must be nonempty")
    cursor = 0
    names: set[str] = set()
    allowed_types = {"f32le": 4, "uint8": 1, "uint16le": 2, "bytes": None}
    for index, field in enumerate(fields):
        require(isinstance(field, dict), f"{location}.fields[{index}] must be an object")
        name, offset, size, field_type = field.get("name"), field.get("offset"), field.get("size"), field.get("type")
        require(isinstance(name, str) and name not in names, f"{location} field names must be unique")
        names.add(name)
        require(offset == cursor, f"{location}.{name} must begin at {cursor}")
        require(isinstance(size, int) and size > 0, f"{location}.{name} size must be positive")
        require(field_type in allowed_types, f"{location}.{name} has unsupported type")
        fixed = allowed_types[field_type]
        require(fixed is None or size == fixed, f"{location}.{name} has wrong scalar size")
        cursor += size
    require(cursor == expected_size, f"{location} layout ends at {cursor}, expected {expected_size}")


def validate_wire(wire: dict[str, Any]) -> None:
    frame_doc = wire.get("frame", {})
    require(frame_doc.get("minimum_frame_bytes") == 3, "wire minimum frame must be 3")
    require(frame_doc.get("maximum_frame_bytes") == 256, "wire maximum frame must be 256")
    require(frame_doc.get("maximum_payload_bytes") == 253, "wire maximum payload must be 253")
    checksum = wire.get("checksum", {})
    require(checksum.get("name") == "sum8", "checksum must be sum8")
    require(checksum.get("excluded") == ["remaining_length", "checksum"], "checksum exclusion must be explicit")
    require(checksum.get("crc_parameters") is None, "sum8 must not claim CRC parameters")
    commands = wire.get("commands")
    require(isinstance(commands, list), "wire.commands must be a list")
    actual = {row.get("opcode"): row.get("name") for row in commands if isinstance(row, dict)}
    require(actual == EXPECTED_COMMANDS, "wire executable command inventory mismatch")
    for row in commands:
        require(row.get("executable") is True, f"named command {row.get('name')} must be executable")
        require(row.get("confidence") not in {"tentative", "unknown"}, f"tentative/unknown command {row.get('name')} must not be executable")
        require(row.get("opcode_hex") == f"{row['opcode']:02x}", f"opcode hex mismatch for {row.get('name')}")
        require(isinstance(row.get("evidence"), list) and row["evidence"], f"command {row.get('name')} needs evidence")
    output_control = next(row for row in commands if row.get("opcode") == 0x0C)
    variants = {variant.get("name"): variant for variant in output_control.get("variants", [])}
    require(variants.get("start", {}).get("confidence") == "confirmed", "Start confidence must remain confirmed")
    require(variants.get("stop", {}).get("confidence") == "high_confidence", "Stop confidence must remain high_confidence")
    for row in wire.get("non_executable", []):
        require(row.get("executable") is False, f"non-executable row {row.get('name')} must be false")
        if row.get("confidence") in {"tentative", "unknown"}:
            require(row.get("executable") is False, "tentative/unknown operations must remain non-executable")
    require(wire.get("acknowledgement_opcodes") == [2, 7, 8, 12], "ack opcode inventory mismatch")
    validate_layout(wire.get("config103", {}), 103, "wire.config103")
    validate_layout(wire.get("telemetry46", {}), 46, "wire.telemetry46")
    arithmetic = wire["telemetry46"].get("derived_fields", {}).get("arithmetic", {})
    require(
        arithmetic
        == {
            "decoded_operand": "decode f32le to its exact IEEE-754 binary32 value, then promote that value to IEEE-754 binary64",
            "operation": "IEEE-754 binary64 multiplication",
            "rounding": "round to nearest, ties to even",
        },
        "telemetry derived-power binary32-to-binary64 arithmetic must be explicit",
    )
    codes = wire.get("conformance_codes")
    require(isinstance(codes, list) and codes == sorted(set(codes)), "conformance codes must be sorted and unique")


def validate_gatt(gatt: dict[str, Any]) -> None:
    expected = {
        "service": ("ffe1", "0000ffe1-0000-1000-8000-00805f9b34fb"),
        "rx": ("ffe2", "0000ffe2-0000-1000-8000-00805f9b34fb"),
        "tx": ("ffe3", "0000ffe3-0000-1000-8000-00805f9b34fb"),
    }
    for name, (short, full) in expected.items():
        item = gatt.get(name, {})
        require(item.get("short_uuid") == short and item.get("uuid") == full, f"GATT {name} UUID mismatch")
        require(item.get("confidence") == "confirmed" and item.get("evidence"), f"GATT {name} must be confirmed with evidence")
    require(gatt["rx"].get("direction") == "charger_to_client", "FFE2 direction mismatch")
    require(gatt["rx"].get("required_operation") == "notify", "FFE2 must require notify")
    require(gatt["rx"].get("application_read") is False, "FFE2 must not prescribe application GATT reads")
    require(gatt["tx"].get("direction") == "client_to_charger", "FFE3 direction mismatch")
    require(gatt["tx"].get("preferred_operation") == "write-without-response", "FFE3 must prefer WNR")
    require(gatt["tx"].get("fallback_operation") == "write", "FFE3 write fallback mismatch")
    require(gatt["rejected_preliminary_service"].get("short_uuid") == "ffe0", "FFE0 rejection missing")
    require(gatt["rejected_preliminary_service"].get("executable") is False, "FFE0 must be non-executable")


def validate_profile_document(profile: dict[str, Any]) -> None:
    require(profile.get("profile_id") == "pidzoom-hw178p", "profile id mismatch")
    expected = {("voltage", "minimum"): ("50.0", "00004842"), ("voltage", "maximum"): ("178.0", "00003243"), ("current", "minimum"): ("0.01", "0ad7233c"), ("current", "maximum"): ("14.0", "00006041")}
    for (quantity, bound), (decimal, encoded) in expected.items():
        actual = profile[quantity][bound]
        require(actual == {"decimal": decimal, "f32le_hex": encoded}, f"profile {quantity} {bound} mismatch")
        require(f32_bytes(decimal, f"profile.{quantity}.{bound}").hex() == encoded, f"profile {quantity} {bound} binary32 mismatch")
    policy = profile.get("device_limit_policy", {})
    require(policy.get("requires_fresh_config") is True, "profile must require fresh config")
    require(policy.get("larger_device_limit_expands_profile") is False, "device limits must not expand profile")
    require(policy.get("module_count_multiplies_current_limit") is False, "module count must not multiply current limit")
    require(profile.get("mutation_policy", {}).get("unknown_or_tentative_commands_executable") is False, "unknown commands must remain non-executable")


def validate_codec_vectors(document: dict[str, Any]) -> None:
    operations = {
        "encode_get_firmware": lambda _a: frame(1),
        "encode_get_serial": lambda _a: frame(4),
        "encode_get_config": lambda _a: frame(5),
        "encode_get_telemetry": lambda _a: frame(6),
        "encode_set_voltage": lambda a: frame(7, f32_bytes(a["value"]["decimal"], "codec.value")),
        "encode_set_current": lambda a: frame(8, f32_bytes(a["value"]["decimal"], "codec.value")),
        "encode_start": lambda _a: frame(12, struct.pack("<i", 0)),
        "encode_stop": lambda _a: frame(12, struct.pack("<i", 1)),
        "encode_authentication_apk_fallback": lambda _a: frame(2, b"D41D8CD98F00B204E9800998ECF8427E\0"),
        "encode_authentication_credential": lambda a: frame(2, a["credential"].encode("ascii") + b"\0"),
    }
    cases = document.get("encode_cases")
    require(isinstance(cases, list) and cases, "codec encode cases must be nonempty")
    ids: set[str] = set()
    seen_operations: set[str] = set()
    for case in cases:
        case_id, operation = case.get("id"), case.get("operation")
        require(isinstance(case_id, str) and case_id not in ids, "codec ids must be unique")
        ids.add(case_id)
        require(operation in operations, f"unsupported codec operation {operation}")
        seen_operations.add(operation)
        expected = case.get("expected", {})
        actual = operations[operation](case.get("arguments", {}))
        require(actual == checked_hex(expected.get("frame_hex"), f"codec.{case_id}.frame_hex"), f"codec frame mismatch for {case_id}")
        opcode, payload = validate_frame(actual)
        require(opcode == expected.get("opcode"), f"codec opcode mismatch for {case_id}")
        require(payload == checked_hex(expected.get("payload_hex"), f"codec.{case_id}.payload_hex"), f"codec payload mismatch for {case_id}")
        require(actual[-1:] == checked_hex(expected.get("checksum_hex"), f"codec.{case_id}.checksum_hex"), f"codec checksum mismatch for {case_id}")
        if operation in {"encode_set_voltage", "encode_set_current"}:
            value = case["arguments"]["value"]
            require(f32_bytes(value["decimal"], f"codec.{case_id}").hex() == value["f32le_hex"], f"codec f32 mismatch for {case_id}")
        require(case.get("confidence") not in {"tentative", "unknown"}, f"executable vector {case_id} cannot be tentative/unknown")
        require(case.get("evidence"), f"codec vector {case_id} needs evidence")
    require(set(operations) == seen_operations, "codec vectors must cover every named encoder")

    credentials = document.get("credential_cases")
    require(isinstance(credentials, list) and len(credentials) >= 3, "credential cases incomplete")
    for case in credentials:
        operation, arguments, expected = case["operation"], case["arguments"], case["expected"]
        if operation == "apk_fallback_credential":
            actual = "D41D8CD98F00B204E9800998ECF8427E"
        elif operation == "canonicalize_direct_credential":
            digest = arguments["digest"]
            actual = "D41D8CD98F00B204E9800998ECF8427E" if digest.casefold() == "d41d8cd98f00b204e9800998ecf8427e" else digest.lower()
            require(frame(2, actual.encode("ascii") + b"\0").hex() == expected["frame_hex"], f"credential frame mismatch for {case['id']}")
        else:
            raise ContractValidationError(f"unsupported credential operation {operation}")
        require(actual == expected["credential_text"], f"credential canonicalization mismatch for {case['id']}")
        if "ascii_hex" in expected:
            require(actual.encode("ascii").hex() == expected["ascii_hex"], f"credential ASCII mismatch for {case['id']}")

    legacy = document.get("python_legacy_cases")
    require(isinstance(legacy, list) and legacy, "Python legacy compatibility cases must be explicit")
    for case in legacy:
        require(case.get("implementations") == ["python"], f"legacy vector {case.get('id')} must be Python-only")
        require(case.get("shared_native_requirement") is False, f"legacy vector {case.get('id')} must not bind native APIs")
        require(case.get("operation") == "derive_password_credential", f"unsupported Python legacy operation {case.get('operation')}")
        password = case["arguments"]["password"]
        credential = hashlib.md5(password.encode("utf-8"), usedforsecurity=False).hexdigest()
        expected = case["expected"]
        require(credential == expected["credential_text"], f"legacy credential mismatch for {case['id']}")
        require(frame(2, credential.encode("ascii") + b"\0").hex() == expected["frame_hex"], f"legacy authentication frame mismatch for {case['id']}")


def semantic_command(opcode: int, payload: bytes) -> str:
    if opcode == 1:
        return "get_firmware" if not payload else "firmware_response"
    if opcode == 2:
        if len(payload) == 1 and payload[0] in (0, 1):
            return "check_password_ack"
        return "check_password" if payload.endswith(b"\0") else "check_password_unknown_payload"
    if opcode == 4:
        return "get_serial" if not payload else "serial_response"
    if opcode == 5:
        return "get_config" if not payload else "config_response"
    if opcode == 6:
        if not payload:
            return "get_telemetry"
        return "telemetry_response" if len(payload) == 46 else "telemetry_opcode_unknown_payload"
    if opcode == 7:
        return "set_voltage"
    if opcode == 8:
        return "set_current"
    if opcode == 12:
        return "output_control"
    return "unknown"


def field_from_payload(path: str, opcode: int, payload: bytes, wire: dict[str, Any]) -> Any:
    if path == "payload":
        return payload
    if path == "credential_format_valid":
        return len(payload) == 33 and payload.endswith(b"\0") and all(
            byte in b"0123456789abcdefABCDEF" for byte in payload[:-1]
        )
    if path == "acknowledged":
        return bool(payload[0])
    if path in {"state", "state_valid", "enabled"}:
        state = struct.unpack("<i", payload)[0]
        if path == "state":
            return state
        if path == "state_valid":
            return state in (0, 1)
        return {0: True, 1: False}.get(state)
    root, name = path.split(".", 1)
    layout_name = "config103" if root == "config" else "telemetry46"
    if root == "telemetry" and name in {"output_enabled", "input_power_w", "output_power_w"}:
        values = {field["name"]: field_from_payload(f"telemetry.{field['name']}", opcode, payload, wire) for field in wire["telemetry46"]["fields"]}
        if name == "output_enabled":
            return {0: True, 1: False}.get(values["current_output"])
        if name == "input_power_w":
            return values["input_voltage"] * values["input_current"]
        return values["output_voltage"] * values["output_current"]
    field = next((item for item in wire[layout_name]["fields"] if item["name"] == name), None)
    require(field is not None, f"unknown expected field path {path}")
    raw = payload[field["offset"]:field["offset"] + field["size"]]
    return {"f32le": lambda: struct.unpack("<f", raw)[0], "uint8": lambda: raw[0], "uint16le": lambda: struct.unpack("<H", raw)[0], "bytes": lambda: raw}[field["type"]]()


def validate_decode_vectors(document: dict[str, Any], wire: dict[str, Any]) -> None:
    cases = document.get("decode_cases")
    require(isinstance(cases, list) and cases, "decode cases must be nonempty")
    ids: set[str] = set()
    seen_payload_lengths: set[tuple[int, int]] = set()
    seen_shapes: set[tuple[int, int, str]] = set()
    for case in cases:
        case_id = case.get("id")
        require(isinstance(case_id, str) and case_id not in ids, "decode ids must be unique")
        ids.add(case_id)
        raw = checked_hex(case.get("packet_hex"), f"decode.{case_id}.packet_hex")
        opcode, payload = validate_frame(raw)
        expected = case.get("expected", {})
        require(opcode == expected.get("opcode"), f"decode opcode mismatch for {case_id}")
        require(len(payload) == expected.get("payload_length"), f"decode payload length mismatch for {case_id}")
        require(semantic_command(opcode, payload) == expected.get("command"), f"decode command mismatch for {case_id}")
        seen_payload_lengths.add((opcode, len(payload)))
        seen_shapes.add((opcode, len(payload), expected["command"]))
        for item in expected.get("fields", []):
            actual = field_from_payload(item["path"], opcode, payload, wire)
            kind = item["type"]
            if kind == "f32":
                require(struct.pack("<f", actual).hex() == item["f32le_hex"], f"decode f32 bytes mismatch for {case_id}:{item['path']}")
                require(f32_bytes(item["decimal"], f"decode.{case_id}.{item['path']}").hex() == item["f32le_hex"], f"decode f32 decimal mismatch for {case_id}:{item['path']}")
            elif kind == "bytes":
                require(actual == checked_hex(item["hex"], f"decode.{case_id}.{item['path']}"), f"decode bytes mismatch for {case_id}:{item['path']}")
            elif kind == "number":
                require(actual == float(item["decimal"]), f"decode number mismatch for {case_id}:{item['path']}")
            else:
                require(actual == item.get("value"), f"decode value mismatch for {case_id}:{item['path']}")
        if case.get("confidence") in {"tentative", "unknown"}:
            require(case.get("executable") is False, f"unknown decode vector {case_id} must be non-executable")
        require(case.get("evidence"), f"decode vector {case_id} needs evidence")
    for required in {(5, 103), (6, 46), (225, 4), (2, 1), (7, 1), (8, 1), (12, 1)}:
        require(required in seen_payload_lengths, f"decode vectors missing opcode/payload {required}")
    required_unrecognized = {
        (2, 0, "check_password_unknown_payload"),
        (2, 1, "check_password_unknown_payload"),
        (2, 2, "check_password"),
        (5, 1, "config_response"),
        (6, 1, "telemetry_opcode_unknown_payload"),
        (7, 1, "set_voltage"),
        (8, 1, "set_current"),
        (12, 1, "output_control"),
    }
    require(
        required_unrecognized <= seen_shapes,
        f"decode vectors missing known-opcode unrecognized shapes {sorted(required_unrecognized - seen_shapes)}",
    )
    arithmetic_case = next(
        (case for case in cases if case.get("id") == "telemetry_binary64_derived_power"),
        None,
    )
    require(arithmetic_case is not None, "decode vectors need a binary64 derived-power discriminator")
    raw = checked_hex(arithmetic_case["packet_hex"], "decode.telemetry_binary64_derived_power.packet_hex")
    _opcode, payload = validate_frame(raw)
    input_product = struct.unpack_from("<f", payload, 0)[0] * struct.unpack_from("<f", payload, 4)[0]
    output_product = struct.unpack_from("<f", payload, 20)[0] * struct.unpack_from("<f", payload, 24)[0]
    expected_fields = {field["path"]: field for field in arithmetic_case["expected"]["fields"]}
    require(input_product == float(expected_fields["telemetry.input_power_w"]["decimal"]), "binary64 input-power vector mismatch")
    require(output_product == float(expected_fields["telemetry.output_power_w"]["decimal"]), "binary64 output-power vector mismatch")
    input_f32_product = struct.unpack("<f", struct.pack("<f", input_product))[0]
    output_f32_product = struct.unpack("<f", struct.pack("<f", output_product))[0]
    require(input_product != input_f32_product, "input-power vector does not distinguish binary64 from binary32 multiplication")
    require(output_product != output_f32_product, "output-power vector does not distinguish binary64 from binary32 multiplication")


def invalid_scalar_code(value: object) -> str | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "scalar.type"
    converted = float(value)
    if not math.isfinite(converted):
        return "scalar.non_finite"
    if converted <= 0:
        return "scalar.non_positive"
    try:
        rounded = struct.unpack("<f", struct.pack("<f", converted))[0]
    except (OverflowError, struct.error):
        return "scalar.not_float32"
    if not math.isfinite(rounded) or rounded <= 0:
        return "scalar.not_float32"
    return None


def validate_invalid_vectors(document: dict[str, Any], codes: set[str]) -> None:
    cases = document.get("invalid_cases")
    require(isinstance(cases, list) and cases, "invalid cases must be nonempty")
    seen: set[str] = set()
    for case in cases:
        if "implementations" in case or "shared_native_requirement" in case:
            require(case.get("implementations") == ["python"], f"invalid vector {case.get('id')} has unsupported applicability")
            require(case.get("shared_native_requirement") is False, f"invalid vector {case.get('id')} must explicitly opt out of native conformance")
        expected = case.get("expected_code")
        require(expected in codes, f"invalid vector {case.get('id')} uses undeclared code {expected}")
        operation, arguments = case["operation"], case["arguments"]
        if operation == "decode_packet":
            try:
                validate_frame(checked_hex(arguments["packet_hex"], f"invalid.{case['id']}"))
            except ContractValidationError as exc:
                actual = str(exc)
            else:
                actual = None
        elif operation in {"encode_set_voltage", "encode_set_current"}:
            actual = invalid_scalar_code(token_number(arguments["value"], f"invalid.{case['id']}.value"))
        elif operation in {"encode_check_password_credential", "encode_authentication_credential"}:
            if arguments.get("credential_kind") == "integer":
                actual = "credential.type"
            else:
                credential = arguments["credential"]
                encoded = credential.encode("utf-8")
                actual = "credential.length" if len(encoded) != 32 else ("credential.non_hex" if any(byte not in b"0123456789abcdefABCDEF" for byte in encoded) else None)
        else:
            raise ContractValidationError(f"unsupported invalid-vector operation {operation}")
        require(actual == expected, f"invalid vector {case['id']} expected {expected}, computed {actual}")
        seen.add(expected)
    required = {"packet.truncated", "packet.length.minimum", "packet.length.mismatch", "packet.checksum.mismatch", "scalar.non_finite", "scalar.non_positive", "scalar.not_float32", "credential.length", "credential.non_hex"}
    require(required <= seen, f"invalid vectors missing codes {sorted(required - seen)}")


def stream_feed(buffer: bytearray, chunk: bytes, maximum: int) -> tuple[list[bytes], str | None]:
    buffer.extend(chunk)
    frames: list[bytes] = []
    while buffer:
        declared = buffer[0]
        total = declared + 1
        if declared < 2 or total > maximum:
            buffer.clear()
            return [], "stream.length.invalid"
        if len(buffer) < total:
            return frames, None
        candidate = bytes(buffer[:total])
        del buffer[:total]
        try:
            validate_frame(candidate)
        except ContractValidationError:
            buffer.clear()
            return [], "stream.frame.invalid"
        frames.append(candidate)
    return frames, None


def validate_framing_vectors(document: dict[str, Any], codes: set[str]) -> None:
    for case in document.get("cases", []):
        buffer = bytearray()
        frames: list[bytes] = []
        actual_code = None
        maximum = case.get("maximum_frame_size", 256)
        for chunk_hex in case["chunks_hex"]:
            emitted, actual_code = stream_feed(buffer, checked_hex(chunk_hex, f"framing.{case['id']}"), maximum)
            if actual_code:
                frames = []
                break
            frames.extend(emitted)
        expected_code = case.get("expected_code")
        require(actual_code == expected_code, f"framing code mismatch for {case['id']}")
        if expected_code is not None:
            require(expected_code in codes, f"framing vector {case['id']} uses undeclared code")
        require([value.hex() for value in frames] == case["expected_frames_hex"], f"framing frames mismatch for {case['id']}")
        require(len(buffer) == case["expected_buffered_bytes"], f"framing buffer mismatch for {case['id']}")
        if "post_error_chunks_hex" in case:
            post_frames: list[bytes] = []
            for chunk_hex in case["post_error_chunks_hex"]:
                emitted, code = stream_feed(buffer, checked_hex(chunk_hex, f"framing.{case['id']}.post"), maximum)
                require(code is None, f"framing post-error recovery failed for {case['id']}")
                post_frames.extend(emitted)
            require([value.hex() for value in post_frames] == case["post_error_frames_hex"], f"framing recovery mismatch for {case['id']}")


def validate_profile_vectors(document: dict[str, Any], profile: dict[str, Any], codes: set[str]) -> None:
    bounds = {}
    for quantity in ("voltage", "current"):
        bounds[quantity] = tuple(struct.unpack("<f", bytes.fromhex(profile[quantity][bound]["f32le_hex"]))[0] for bound in ("minimum", "maximum"))
    for case in document.get("contains_cases", []):
        token = case["value"]
        value = token_number(token, f"profile.{case['id']}")
        canonical = canonical_positive_f32(value)
        minimum, maximum = bounds[case["quantity"]]
        accepted = canonical is not None and minimum <= canonical <= maximum
        require(accepted is case["accepted"], f"profile containment mismatch for {case['id']}")
        if "f32le_hex" in token:
            require(f32_bytes(token["decimal"], f"profile.{case['id']}").hex() == token["f32le_hex"], f"profile f32 mismatch for {case['id']}")
        if not accepted:
            require(case.get("expected_code") in codes, f"profile rejection {case['id']} needs declared code")

    profile_human = {"voltage": (50.0, 178.0), "current": (0.01, 14.0)}
    for case in document.get("effective_limits_cases", []):
        config = case["config"]
        parsed: dict[str, float] = {}
        for key, token in config.items():
            parsed[key] = token_number(token, f"profile.{case['id']}.{key}")
            if "f32le_hex" in token:
                require(f32_bytes(token["decimal"], f"profile.{case['id']}.{key}").hex() == token["f32le_hex"], f"device limit f32 mismatch for {case['id']}:{key}")
        voltage = canonical_positive_f32(parsed.get("max_voltage", math.nan))
        current = canonical_positive_f32(parsed.get("max_single_module_current", math.nan))
        result = None
        if voltage is not None and current is not None:
            voltage_max = min(voltage, bounds["voltage"][1])
            current_max = min(current, bounds["current"][1])
            if voltage_max >= bounds["voltage"][0] and current_max >= bounds["current"][0]:
                if voltage_max == bounds["voltage"][0]:
                    voltage_max = profile_human["voltage"][0]
                if current_max == bounds["current"][0]:
                    current_max = profile_human["current"][0]
                result = {"voltage_minimum": "50.0", "voltage_maximum": str(voltage_max), "current_minimum": "0.01", "current_maximum": str(current_max)}
        expected = case.get("expected")
        require(result == expected, f"effective limits mismatch for {case['id']}: {result!r} != {expected!r}")
        if expected is None:
            require(case.get("expected_code") == "profile.device_limits.invalid", f"invalid effective limits need stable code for {case['id']}")


def validate_contract(root: Path) -> None:
    require(root.is_dir(), f"contract root does not exist: {root}")
    documents = {path.relative_to(root).as_posix(): load_json(path) for path in sorted(root.rglob("*.json"))}
    required = {"manifest.json", "contract.schema.json", "wire.json", "gatt.json", "profiles/hw178p.json", "vectors/codec.json", "vectors/decode.json", "vectors/invalid.json", "vectors/framing.json", "vectors/profile.json"}
    require(required == set(documents), f"contract JSON inventory mismatch: missing={sorted(required - set(documents))}, extra={sorted(set(documents) - required)}")
    validate_evidence_references(root.parents[1], documents)
    for relative, document in documents.items():
        validate_hex_convention(document, relative)
    wire = documents["wire.json"]
    profile = documents["profiles/hw178p.json"]
    validate_wire(wire)
    validate_gatt(documents["gatt.json"])
    validate_profile_document(profile)
    validate_codec_vectors(documents["vectors/codec.json"])
    validate_decode_vectors(documents["vectors/decode.json"], wire)
    codes = set(wire["conformance_codes"])
    validate_invalid_vectors(documents["vectors/invalid.json"], codes)
    validate_framing_vectors(documents["vectors/framing.json"], codes)
    validate_profile_vectors(documents["vectors/profile.json"], profile, codes)
    validate_manifest(root, documents["manifest.json"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="validate the contract without modifying it")
    parser.add_argument("--contract-root", type=Path, default=Path(__file__).resolve().parents[1] / "contract" / "v1")
    args = parser.parse_args(argv)
    if not args.check:
        parser.error("only check mode is supported; pass --check")
    try:
        validate_contract(args.contract_root.resolve())
    except ContractValidationError as exc:
        print(f"contract validation failed: {exc}", file=sys.stderr)
        return 1
    print(f"HWCDQ contract {CONTRACT_VERSION}: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
