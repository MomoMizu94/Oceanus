""" Discover local skills and load explicitly requested instructions """
from collections.abc import Iterable
from dataclasses import dataclass
import os
from pathlib import Path
import re
import stat
import tomllib

import yaml

from agent_core.instructions import _read_instruction_text


MAX_SKILL_ENTRIES = 512
MAX_ACTIVE_SKILLS = 8
MAX_SKILL_PROMPT_BYTES = 192 * 1024
SKILL_NAME = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*")
SKILL_MENTION = re.compile(r"(?<![\w\\$/])\$([a-z][a-z0-9-]*)(?![\w$/-])")


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    path: Path
    body: str


def skill_directories() -> list[Path]:
    """ Use Oceanus skills unless explicit search directories are configured """
    configured = os.environ.get("OCEANUS_SKILL_DIRS")
    if configured is None:
        return [Path.home() / ".oceanus" / "skills"]
    if not configured.strip():
        return []
    roots = []
    for value in configured.split(os.pathsep):
        root = Path(value).expanduser()
        if not value or not root.is_absolute():
            raise ValueError("OCEANUS_SKILL_DIRS requires absolute paths separated by ':'")
        root = root.resolve()
        if root not in roots:
            roots.append(root)
    return roots


def _read_skill_file(path: Path) -> str:
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    descriptor = os.open(path, flags)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError(f"{path} must be a regular file")
        with os.fdopen(descriptor, "rb", closefd=False) as source:
            return _read_instruction_text(source, path)
    finally:
        os.close(descriptor)


def _yaml_mapping(text: str, path: Path) -> dict:
    try:
        value = yaml.safe_load(text)
    except (yaml.YAMLError, RecursionError) as error:
        raise ValueError(f"Invalid YAML in {path}") from error
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain YAML fields")
    return value


def _load_skill(path: Path) -> Skill:
    text = _read_skill_file(path)
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        raise ValueError(f"{path} needs YAML frontmatter between '---' lines")
    end = None
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            end = index
            break
    if end is None:
        raise ValueError(f"{path} has unclosed YAML frontmatter")
    metadata = _yaml_mapping("".join(lines[1:end]), path)
    name = metadata.get("name")
    description = metadata.get("description")
    if not isinstance(name, str) or len(name) > 64 or not SKILL_NAME.fullmatch(name):
        raise ValueError(f"{path} needs a lowercase, hyphen-separated name of at most 64 characters")
    if not isinstance(description, str) or not description.strip():
        raise ValueError(f"{path} needs a nonempty description")
    body = "".join(lines[end + 1:])
    if not body.strip():
        raise ValueError(f"{path} has no skill instructions")
    return Skill(name, description.strip(), path, body)


def discover_skills() -> tuple[list[Skill], list[str]]:
    """ Inspect immediate skill folders and the copied .system collection """
    skills = []
    warnings = []
    visited = set()
    entries_seen = 0
    roots = skill_directories()
    for root in roots:
        if not root.exists() and os.environ.get("OCEANUS_SKILL_DIRS") is not None:
            warnings.append(f"Skill directory does not exist: {root}")
        collections = [root, root / ".system"]
        for collection in collections:
            if not collection.exists():
                continue
            if collection.is_symlink():
                warnings.append(f"Skipped symlink skill directory: {collection}")
                continue
            try:
                for folder in collection.iterdir():
                    entries_seen += 1
                    if entries_seen > MAX_SKILL_ENTRIES:
                        raise ValueError("Too many skill directory entries; narrow OCEANUS_SKILL_DIRS")
                    if folder.name.startswith("."):
                        continue
                    if folder.is_symlink():
                        warnings.append(f"Skipped symlink skill directory: {folder}")
                        continue
                    if not folder.is_dir():
                        continue
                    path = folder / "SKILL.md"
                    if path in visited:
                        continue
                    visited.add(path)
                    try:
                        skill = _load_skill(path)
                    except FileNotFoundError:
                        continue
                    except (OSError, ValueError) as error:
                        warnings.append(f"Skipped {path}: {error}")
                    else:
                        skills.append(skill)
            except OSError as error:
                warnings.append(f"Could not scan {collection}: {error}")
    skills.sort(key=lambda skill: (skill.name, str(skill.path)))
    warnings.sort()
    return skills, warnings


def requested_skill_names(task: str) -> list[str]:
    """ Ignore escaped dollars, inline code, and fenced code blocks """
    names = []
    fence_character = ""
    fence_length = 0
    inline_delimiter = ""
    for line in task.splitlines():
        fence = re.match(r"^\s{0,3}(`{3,}|~{3,})(.*)$", line)
        if fence:
            marker, remainder = fence.groups()
            if not fence_character:
                fence_character = marker[0]
                fence_length = len(marker)
                inline_delimiter = ""
            elif marker[0] == fence_character and len(marker) >= fence_length and not remainder.strip():
                fence_character = ""
            continue
        if fence_character:
            continue
        for part in re.split(r"(`+)", line):
            if part.startswith("`"):
                if not inline_delimiter:
                    inline_delimiter = part
                elif part == inline_delimiter:
                    inline_delimiter = ""
            elif not inline_delimiter:
                for match in SKILL_MENTION.finditer(part):
                    name = match.group(1)
                    if name not in names:
                        names.append(name)
    return names


def workspace_skill_names(workspace: Path | None) -> list[str]:
    """ Read skills explicitly enabled for every task in this workspace """
    if workspace is None:
        return []
    directory = workspace / ".oceanus"
    if directory.is_symlink():
        raise ValueError(f"Workspace skill settings directory must not be a symlink: {directory}")
    path = directory / "skills.toml"
    try:
        text = _read_skill_file(path)
    except FileNotFoundError:
        return []
    try:
        settings = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise ValueError(f"Invalid workspace skill settings in {path}: {error}") from error
    section = settings.get("skills", {})
    if not isinstance(section, dict):
        raise ValueError(f"{path} needs a [skills] table")
    names = section.get("always", [])
    if not isinstance(names, list):
        raise ValueError(f"{path}: skills.always must be a list of skill names")
    unique = []
    for name in names:
        if not isinstance(name, str) or len(name) > 64 or not SKILL_NAME.fullmatch(name):
            raise ValueError(f"Invalid skill name in {path}")
        if name not in unique:
            unique.append(name)
    return unique


def _dependency_warnings(skill: Skill, available_tools: set[str]) -> list[str]:
    path = skill.path.parent / "agents" / "openai.yaml"
    if not path.exists():
        return []
    metadata = _yaml_mapping(_read_skill_file(path), path)
    dependencies = metadata.get("dependencies", {})
    if not isinstance(dependencies, dict):
        raise ValueError(f"Invalid dependencies in {path}")
    tools = dependencies.get("tools", [])
    if not isinstance(tools, list):
        raise ValueError(f"Invalid tool dependencies in {path}")
    warnings = []
    for tool in tools:
        if not isinstance(tool, dict) or not isinstance(tool.get("value"), str):
            raise ValueError(f"Invalid tool dependency in {path}")
        name = tool["value"]
        if tool.get("type") != "function" or name not in available_tools:
            warnings.append(f"${skill.name} declares unavailable dependency {name!r}; Oceanus has not installed it")
    return warnings


def prepare_skill_prompt(
    base_prompt: str, task: str, *, available_tools: Iterable[str],
    workspace: Path | None = None,
) -> tuple[str, list[str]]:
    """ Activate skills for this submitted task and all its model turns """
    names = workspace_skill_names(workspace)
    for name in requested_skill_names(task):
        if name not in names:
            names.append(name)
    if not names:
        return base_prompt, []
    if len(names) > MAX_ACTIVE_SKILLS:
        raise ValueError(f"Request at most {MAX_ACTIVE_SKILLS} skills per task")
    catalog, warnings = discover_skills()
    selected = []
    for name in names:
        matches = [skill for skill in catalog if skill.name == name]
        if not matches:
            available = ", ".join(sorted({skill.name for skill in catalog})) or "none"
            details = "\n".join(warnings)
            raise ValueError(f"Unknown skill ${name}. Available skills: {available}.\n{details}")
        if len(matches) > 1:
            paths = ", ".join(str(skill.path) for skill in matches)
            raise ValueError(f"Ambiguous skill ${name}: {paths}. Remove the duplicate or narrow OCEANUS_SKILL_DIRS")
        selected.append(matches[0])
    permitted = set(available_tools)
    tool_names = ", ".join(sorted(permitted)) or "none"
    section = (
        "\n\nExplicitly selected skills for this submitted task only:\n"
        "Follow the selected skill instructions for this task, including its tool turns. "
        "Explicit user requests take precedence. Skills do not override built-in rules, "
        "tool permissions, approvals, or sandbox restrictions.\n"
        "Resolve relative supporting-file references against the listed skill directory, "
        "not the project workspace. Read those files using their absolute paths with "
        "the normal file tools and approvals. Do not preload all references or execute "
        "scripts just because a skill mentions them.\n"
        "Codex-specific tools, MCP servers, UI metadata, and permission settings are not "
        "provided by loading a skill. Use only tools actually available in this request. "
        "Report missing capabilities instead of claiming they ran. Ask questions in your "
        "reply when a skill calls for an unavailable question dialog.\n"
        f"Available Oceanus tools: {tool_names}.\n"
    )
    for skill in selected:
        warnings.extend(_dependency_warnings(skill, permitted))
        section += (
            f"\nSkill ${skill.name}\nSource: {skill.path}\n"
            f"Skill directory: {skill.path.parent}\n{skill.body}\n"
        )
    if warnings:
        section += "\nSkill loading notices:\n" + "\n".join(warnings)
    if len(section.encode("utf-8")) > MAX_SKILL_PROMPT_BYTES:
        raise ValueError(f"Selected skill instructions exceed {MAX_SKILL_PROMPT_BYTES} bytes")
    notices = [f"Skill active for this task: ${skill.name} ({skill.path})" for skill in selected]
    notices.extend(warnings)
    return base_prompt + section, notices
