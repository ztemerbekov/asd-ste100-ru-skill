#!/usr/bin/env python3

import json
import math
import re
import struct
import sys
import xml.etree.ElementTree as ET
import zlib
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parent.parent
AGENT_PLUGINS_SCHEMA = (
    "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
)
PORTABLE_MANIFEST_FIELDS = {
    "$schema",
    "name",
    "version",
    "description",
    "author",
    "homepage",
    "repository",
    "license",
    "keywords",
    "extensions",
}
PORTABLE_SHARED_FIELDS = (
    "name",
    "version",
    "description",
    "author",
    "homepage",
    "repository",
    "license",
    "keywords",
)
SKILLS_PATH = "./skills/"
CLAUDE_SKILLS_SOURCE = "./skills"
REPOSITORY_URL = "https://github.com/ztemerbekov/asd-ste100-ru-skill.git"
CODEX_LOGO_PATH = "./assets/marketplaces/codex/logo.svg"
CODEX_COMPOSER_ICON_PATH = "./assets/marketplaces/codex/composer-icon.svg"
CURSOR_LOGO_PATH = "assets/marketplaces/cursor/logo.png"
MAX_CODEX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_CODEX_DEFAULT_PROMPTS = 3
MAX_CODEX_PROMPT_CHARS = 128
SUPPORTED_CODEX_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".svg"}
SVG_NUMBER = re.compile(
    r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
)

MANIFEST_PATHS = {
    "portable manifest": ROOT / "plugin.json",
    "claude marketplace": ROOT / ".claude-plugin/marketplace.json",
    "codex marketplace": ROOT / ".agents/plugins/marketplace.json",
    "codex manifest": ROOT / ".codex-plugin/plugin.json",
    "cursor marketplace": ROOT / ".cursor-plugin/marketplace.json",
    "cursor manifest": ROOT / ".cursor-plugin/plugin.json",
}

failures = []


def fail(path, message):
    failures.append(f"{path.relative_to(ROOT)}: {message}")


def load_json(label):
    path = MANIFEST_PATHS[label]
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        fail(path, "missing required manifest")
    except json.JSONDecodeError as error:
        fail(path, f"invalid JSON at line {error.lineno}, column {error.colno}")
    return {}


def plugin_entry(marketplace, path, name):
    entries = [
        entry
        for entry in marketplace.get("plugins", [])
        if isinstance(entry, dict) and entry.get("name") == name
    ]
    if len(entries) != 1:
        fail(path, f"expected exactly one {name!r} plugin entry, found {len(entries)}")
        return {}
    return entries[0]


def check_value(document, path, key, expected):
    actual = document.get(key)
    if actual != expected:
        fail(path, f"{key!r} must be {expected!r}, found {actual!r}")


def keyword_set(document, path, subject):
    keywords = document.get("keywords")
    if not isinstance(keywords, list):
        fail(path, f"{subject} keywords must be an array")
        return None
    if not all(isinstance(keyword, str) for keyword in keywords):
        fail(path, f"{subject} keywords must contain only strings")
        return None
    duplicates = sorted(
        keyword for keyword in set(keywords) if keywords.count(keyword) > 1
    )
    if duplicates:
        fail(path, f"{subject} keywords contain duplicates: {', '.join(duplicates)}")
    return set(keywords)


def resolve_asset(manifest_path, declared_path, *, require_dot_prefix):
    if not isinstance(declared_path, str):
        fail(manifest_path, "declared asset path must be a string")
        return None
    if not declared_path:
        fail(manifest_path, "declared asset path must not be empty")
        return None
    if declared_path != declared_path.strip():
        fail(manifest_path, "declared asset path must not have outer whitespace")
        return None
    if any(ord(character) < 32 or ord(character) == 127 for character in declared_path):
        fail(manifest_path, "declared asset path must not contain control characters")
        return None
    if require_dot_prefix and not declared_path.startswith("./"):
        fail(manifest_path, "Codex branding asset paths must start with './'")
        return None
    relative_path = declared_path.removeprefix("./")
    pure_path = PurePosixPath(relative_path)
    if (
        pure_path.is_absolute()
        or not pure_path.parts
        or ".." in pure_path.parts
        or re.match(r"^[A-Za-z]:", relative_path)
    ):
        fail(manifest_path, f"unsafe declared asset path: {declared_path!r}")
        return None
    resolved_path = (ROOT / Path(*pure_path.parts)).resolve()
    try:
        resolved_path.relative_to(ROOT)
    except ValueError:
        fail(manifest_path, f"declared asset path escapes the plugin: {declared_path!r}")
        return None
    if not resolved_path.is_file():
        fail(manifest_path, f"declared asset does not resolve to a regular file: {declared_path!r}")
        return None
    return resolved_path


def parse_svg_number(value):
    if not isinstance(value, str) or not SVG_NUMBER.fullmatch(value.strip()):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def validate_codex_svg(path):
    if path.suffix.lower() not in SUPPORTED_CODEX_IMAGE_EXTENSIONS:
        fail(path, f"unsupported Codex image extension: {path.suffix!r}")
        return
    if path.stat().st_size > MAX_CODEX_IMAGE_BYTES:
        fail(path, "Codex image must not exceed 5 MiB")
    if path.suffix.lower() != ".svg":
        fail(path, "approved Codex marketplace exports must be SVG files")
        return
    try:
        text = path.read_bytes().decode("utf-8")
    except UnicodeDecodeError:
        fail(path, "SVG must contain valid UTF-8 XML")
        return
    try:
        root = ET.fromstring(text)
    except ET.ParseError as error:
        fail(path, f"malformed SVG XML: {error}")
        return
    if root.tag.rsplit("}", 1)[-1] != "svg":
        fail(path, "SVG root element must be <svg>")
        return

    view_box = root.get("viewBox")
    if view_box is not None:
        values = [
            value
            for value in re.split(r"[\s,]+", view_box.strip())
            if value
        ]
        if len(values) != 4:
            fail(path, "SVG viewBox must contain four numeric values")
            return
        numbers = [parse_svg_number(value) for value in values]
        if any(number is None for number in numbers):
            fail(path, "SVG viewBox dimensions must be numeric and unitless")
            return
        width, height = numbers[2], numbers[3]
    else:
        width = parse_svg_number(root.get("width"))
        height = parse_svg_number(root.get("height"))
        if width is None or height is None:
            fail(path, "SVG must define a numeric viewBox or numeric width and height")
            return

    if width <= 0 or height <= 0:
        fail(path, "SVG dimensions must be positive finite numbers")
    elif not math.isclose(width, height, rel_tol=0, abs_tol=1e-9):
        fail(path, f"SVG dimensions must be square, found {width:g}×{height:g}")
    elif width < 48:
        fail(path, f"SVG dimensions must be at least 48×48, found {width:g}×{height:g}")


def validate_cursor_png(path):
    if path.suffix.lower() != ".png":
        fail(path, "approved Cursor marketplace export must be a PNG file")
        return
    data = path.read_bytes()
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        fail(path, "Cursor logo does not contain a PNG signature")
        return

    offset = 8
    dimensions = None
    seen_idat = False
    seen_iend = False
    chunk_index = 0
    while offset < len(data):
        if len(data) - offset < 12:
            fail(path, "Cursor logo contains a truncated PNG chunk")
            return
        length = struct.unpack(">I", data[offset : offset + 4])[0]
        chunk_type = data[offset + 4 : offset + 8]
        chunk_end = offset + 12 + length
        if chunk_end > len(data):
            fail(path, "Cursor logo contains a truncated PNG chunk")
            return
        chunk_data = data[offset + 8 : offset + 8 + length]
        expected_crc = struct.unpack(">I", data[offset + 8 + length : chunk_end])[0]
        actual_crc = zlib.crc32(chunk_type + chunk_data) & 0xFFFFFFFF
        if actual_crc != expected_crc:
            fail(path, f"Cursor logo contains an invalid {chunk_type!r} chunk checksum")
            return
        if chunk_index == 0:
            if chunk_type != b"IHDR" or length != 13:
                fail(path, "Cursor logo must begin with a valid PNG IHDR chunk")
                return
            width, height = struct.unpack(">II", chunk_data[:8])
            dimensions = (width, height)
        elif chunk_type == b"IHDR":
            fail(path, "Cursor logo contains more than one PNG IHDR chunk")
            return
        if chunk_type == b"IDAT":
            seen_idat = True
        if chunk_type == b"IEND":
            if length != 0 or chunk_end != len(data):
                fail(path, "Cursor logo contains an invalid PNG IEND chunk")
                return
            seen_iend = True
            break
        offset = chunk_end
        chunk_index += 1

    if dimensions != (512, 512):
        found = (
            f"{dimensions[0]}×{dimensions[1]}"
            if dimensions is not None
            else "no dimensions"
        )
        fail(path, f"Cursor logo must be the approved 512×512 export, found {found}")
    if not seen_idat or not seen_iend:
        fail(path, "Cursor logo must contain PNG image data and a terminal IEND chunk")


claude_marketplace = load_json("claude marketplace")
codex_marketplace = load_json("codex marketplace")
codex_manifest = load_json("codex manifest")
cursor_marketplace = load_json("cursor marketplace")
cursor_manifest = load_json("cursor manifest")
portable_manifest = load_json("portable manifest")

portable_manifest_path = MANIFEST_PATHS["portable manifest"]
if not isinstance(portable_manifest, dict):
    fail(portable_manifest_path, "portable manifest must be a JSON object")
    portable_manifest = {}

unknown_portable_fields = sorted(set(portable_manifest) - PORTABLE_MANIFEST_FIELDS)
if unknown_portable_fields:
    fail(
        portable_manifest_path,
        "portable manifest contains unsupported top-level fields: "
        + ", ".join(unknown_portable_fields),
    )

check_value(portable_manifest, portable_manifest_path, "$schema", AGENT_PLUGINS_SCHEMA)
plugin_name = portable_manifest.get("name")
if (
    not isinstance(plugin_name, str)
    or not 1 <= len(plugin_name) <= 64
    or re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", plugin_name) is None
    or "--" in plugin_name
    or ".." in plugin_name
):
    fail(portable_manifest_path, "name must satisfy the Agent Plugins v1 name constraints")

portable_version = portable_manifest.get("version")
if not isinstance(portable_version, str) or not re.fullmatch(
    r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)", portable_version
):
    fail(
        portable_manifest_path,
        f"version must use MAJOR.MINOR.PATCH semver, found {portable_version!r}",
    )

for label, marketplace in (
    ("claude marketplace", claude_marketplace),
    ("codex marketplace", codex_marketplace),
    ("cursor marketplace", cursor_marketplace),
):
    check_value(marketplace, MANIFEST_PATHS[label], "name", plugin_name)

for label, manifest in (
    ("codex manifest", codex_manifest),
    ("cursor manifest", cursor_manifest),
):
    path = MANIFEST_PATHS[label]
    for field in PORTABLE_SHARED_FIELDS:
        check_value(manifest, path, field, portable_manifest.get(field))
    check_value(manifest, path, "skills", SKILLS_PATH)

claude_plugin = plugin_entry(claude_marketplace, MANIFEST_PATHS["claude marketplace"], plugin_name)
codex_plugin = plugin_entry(codex_marketplace, MANIFEST_PATHS["codex marketplace"], plugin_name)
cursor_plugin = plugin_entry(cursor_marketplace, MANIFEST_PATHS["cursor marketplace"], plugin_name)

display_name = claude_plugin.get("displayName")
if not isinstance(display_name, str) or not display_name:
    fail(MANIFEST_PATHS["claude marketplace"], "plugin displayName must be a non-empty string")
codex_marketplace_interface = codex_marketplace.get("interface")
codex_plugin_interface = codex_manifest.get("interface")
for path, document in (
    (MANIFEST_PATHS["codex marketplace"], codex_marketplace_interface),
    (MANIFEST_PATHS["codex manifest"], codex_plugin_interface),
):
    if not isinstance(document, dict):
        fail(path, "'interface' must be an object")
    else:
        check_value(document, path, "displayName", display_name)
check_value(cursor_manifest, MANIFEST_PATHS["cursor manifest"], "displayName", display_name)

description = portable_manifest.get("description")
check_value(claude_plugin, MANIFEST_PATHS["claude marketplace"], "description", description)
check_value(cursor_plugin, MANIFEST_PATHS["cursor marketplace"], "description", description)

portable_keywords = keyword_set(portable_manifest, portable_manifest_path, "plugin")
claude_keywords = keyword_set(claude_plugin, MANIFEST_PATHS["claude marketplace"], "plugin")
if portable_keywords is not None and claude_keywords is not None and portable_keywords != claude_keywords:
    fail(MANIFEST_PATHS["claude marketplace"], "plugin keywords must match plugin.json")

if isinstance(codex_plugin_interface, dict):
    check_value(codex_plugin_interface, MANIFEST_PATHS["codex manifest"], "logo", CODEX_LOGO_PATH)
    check_value(
        codex_plugin_interface,
        MANIFEST_PATHS["codex manifest"],
        "composerIcon",
        CODEX_COMPOSER_ICON_PATH,
    )
    codex_logo = resolve_asset(
        MANIFEST_PATHS["codex manifest"],
        codex_plugin_interface.get("logo"),
        require_dot_prefix=True,
    )
    codex_composer_icon = resolve_asset(
        MANIFEST_PATHS["codex manifest"],
        codex_plugin_interface.get("composerIcon"),
        require_dot_prefix=True,
    )
    default_prompts = codex_plugin_interface.get("defaultPrompt")
    if (
        not isinstance(default_prompts, list)
        or not 1 <= len(default_prompts) <= MAX_CODEX_DEFAULT_PROMPTS
        or not all(
            isinstance(prompt, str) and 0 < len(prompt) <= MAX_CODEX_PROMPT_CHARS
            for prompt in default_prompts
        )
    ):
        fail(
            MANIFEST_PATHS["codex manifest"],
            f"defaultPrompt must hold 1-{MAX_CODEX_DEFAULT_PROMPTS} non-empty strings "
            f"of at most {MAX_CODEX_PROMPT_CHARS} characters; Codex ignores the rest",
        )
    else:
        # The skills are command-only: a prompt without $<skill> never starts one.
        skill_commands = [
            f"${path.parent.name}" for path in (ROOT / "skills").glob("*/SKILL.md")
        ]
        for prompt in default_prompts:
            if not any(command in prompt for command in skill_commands):
                fail(
                    MANIFEST_PATHS["codex manifest"],
                    f"defaultPrompt must invoke a skill with {' or '.join(skill_commands)}: {prompt!r}",
                )
    for asset in (codex_logo, codex_composer_icon):
        if asset is not None:
            validate_codex_svg(asset)
    if codex_logo is not None and codex_logo == codex_composer_icon:
        fail(MANIFEST_PATHS["codex manifest"], "logo and composerIcon must use separate exports")

check_value(cursor_manifest, MANIFEST_PATHS["cursor manifest"], "logo", CURSOR_LOGO_PATH)
cursor_logo = resolve_asset(
    MANIFEST_PATHS["cursor manifest"],
    cursor_manifest.get("logo"),
    require_dot_prefix=False,
)
if cursor_logo is not None:
    validate_cursor_png(cursor_logo)

for document in (claude_marketplace, *claude_marketplace.get("plugins", [])):
    if not isinstance(document, dict):
        continue
    for unsupported_field in ("logo", "icon"):
        if unsupported_field in document:
            fail(
                MANIFEST_PATHS["claude marketplace"],
                f"Claude marketplace must not declare unsupported {unsupported_field!r} metadata",
            )

if codex_plugin.get("source") != {"source": "url", "url": REPOSITORY_URL}:
    fail(MANIFEST_PATHS["codex marketplace"], f"plugin source must point to {REPOSITORY_URL}")
if codex_plugin.get("policy") != {"installation": "AVAILABLE", "authentication": "ON_INSTALL"}:
    fail(MANIFEST_PATHS["codex marketplace"], "plugin must use the approved installation and authentication policy")
if cursor_plugin.get("source") != "./":
    fail(MANIFEST_PATHS["cursor marketplace"], "plugin source must be './'")

check_value(claude_plugin, MANIFEST_PATHS["claude marketplace"], "source", CLAUDE_SKILLS_SOURCE)
declared_skills = []
claude_skills = claude_plugin.get("skills", [])
if not isinstance(claude_skills, list):
    fail(MANIFEST_PATHS["claude marketplace"], "plugin skills must be an array")
    claude_skills = []
for skill_path in claude_skills:
    if not isinstance(skill_path, str):
        fail(MANIFEST_PATHS["claude marketplace"], "plugin contains a non-string skill path")
        continue
    resolved_skill = (ROOT / CLAUDE_SKILLS_SOURCE.removeprefix("./") / skill_path.removeprefix("./")).resolve()
    if not (resolved_skill / "SKILL.md").is_file():
        fail(MANIFEST_PATHS["claude marketplace"], f"skill path does not resolve: {skill_path!r}")
    declared_skills.append(Path(skill_path).name)

canonical_skills = sorted(path.parent.name for path in (ROOT / "skills").glob("*/SKILL.md"))
if sorted(declared_skills) != canonical_skills:
    fail(
        MANIFEST_PATHS["claude marketplace"],
        "plugin must list every canonical skill exactly once "
        f"(declared: {', '.join(sorted(declared_skills)) or 'none'}; "
        f"canonical: {', '.join(canonical_skills) or 'none'})",
    )

if failures:
    for failure in failures:
        print(f"FAIL {failure}")
    print(f"\nPackaging summary: {len(failures)} failures")
    sys.exit(1)

print(
    "Packaging summary: plugin name, version, description, display name, and keywords "
    "are synchronized across Agent Plugins, Claude, Codex, and Cursor; "
    f"{len(canonical_skills)} canonical skills covered"
)
