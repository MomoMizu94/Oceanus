import shutil
import sys
from pathlib import Path
import stat
import os
from collections.abc import Iterator
from contextlib import contextmanager
from tempfile import TemporaryDirectory

from agent_core.sensitive_paths import sensitive_path_reason


SYSTEM_PATH = "/usr/bin:/bin"
SANDBOX_HOME = "/tmp/oceanus-home"
MAX_WORKSPACE_ENTRIES = 100_000
MAX_WORKSPACE_DEPTH = 64
SYSTEM_READ_PATHS = ("/usr", "/bin", "/sbin", "/lib", "/lib64")
SHELL_SANDBOX_NOTICE = (
    "Runs in a Linux sandbox with no network access. "
    "Commands can change ordinary workspace files without a diff preview. "
    "Protected files and directories appear empty and read-only. "
    "Other personal files outside the workspace are unavailable. "
    "System tools are available read-only. "
    "Approval allows this command within these limits."
)


class ShellSandboxError(RuntimeError):
    """ Report a sandbox requirement """


def find_bubblewrap() -> str:
    """ Find system sandbox launcher without using inherited PATH """
    if sys.platform != "linux":
        raise ShellSandboxError("Shell sandboxing curretly requires Linux")

    # Search for bwrap
    executable = shutil.which("bwrap", path=SYSTEM_PATH)

    if executable is None:
        raise ShellSandboxError(
            "Bubblewrap is required for shell sandboxing. "
            "Install the bubblewrap package before running shell commands. "
        )

    return executable


def build_shell_environment() -> dict[str, str]:
    """ Create the small environment passed to the sandbox launcher """
    return {
        "PATH": SYSTEM_PATH,
        "HOME": SANDBOX_HOME,
        "TMPDIR": "/tmp",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8"
    }

def build_sandbox_base_args(working_directory: str = SANDBOX_HOME) -> list[str]:
    """ Build an isolated runtime with no workspace mounted """
    arguments = [
        # Find bubblewrap and configure isolation
        find_bubblewrap(),
        "--unshare-user",
        "--unshare-pid",
        "--unshare-net",
        "--unshare-ipc",
        "--unshare-uts",
        "--disable-userns",
        "--die-with-parent",
        "--new-session",
        "--cap-drop", "ALL"
    ]

    for directory in SYSTEM_READ_PATHS:
        if directory == "/usr" or Path(directory).is_dir():
            arguments.extend(["--ro-bind", directory, directory])

    # Create private runtime dirs
    arguments.extend([
        "--proc", "/proc",
        "--dev", "/dev",
        "--tmpfs", "/tmp",
        "--dir", SANDBOX_HOME,
        "--chdir", working_directory,
        "--clearenv",
    ])

    # Set env
    environment = build_shell_environment()
    for name, value in environment.items():
        arguments.extend(["--setenv", name, value])

    return arguments

def validate_workspace_root(workspace_root: Path) -> Path:
    """ Resolve a workspace and reject too broad dirs (like /home/) """
    requested = workspace_root.expanduser().absolute()
    root = requested.resolve(strict=True)

    # Dir requirement
    if not root.is_dir():
        raise NotADirectoryError(f"Workspace is not a directory: {root}")

    # Not sensitive requirement
    reason = sensitive_path_reason(requested) or sensitive_path_reason(root)
    if reason is not None:
        raise PermissionError(f"Can't use a protected workspace: {reason}")

    # Not home or home's parents requirement
    home = Path.home().resolve()
    if home.is_relative_to(root):
        raise PermissionError("The workspace can't be your home or its parent directories!")

    
    reserved_directories = SYSTEM_READ_PATHS + (
        "/proc", "/dev", "/sys", "/etc", "/run",
        SANDBOX_HOME,
        str(home / ".oceanus")
    )

    # Reject overlapping dirs
    for directory in reserved_directories:
        reserved = Path(directory).resolve()
        inside_reserved = root.is_relative_to(reserved)
        contains_reserved = reserved.is_relative_to(root)

        if inside_reserved or contains_reserved:
            raise PermissionError(f"Workspace overlaps a reserved directory: {reserved}")

    return root

def classify_workspace_entry(path: Path) -> str:
    """ Inspect one entry without opening it """
    info = path.lstat()
    protected = sensitive_path_reason(path) is not None

    if stat.S_ISLNK(info.st_mode):
        if protected:
            raise PermissionError(f"Protected symlink needs separate handling: {path}")
        return "symlink"

    if stat.S_ISDIR(info.st_mode):
        if protected:
            return "protected_directory"
        return "directory"

    if stat.S_ISREG(info.st_mode):
        if info.st_nlink != 1:
            raise PermissionError(f"Hard-linked diles are not supported in the sandbox: {path}")
        if protected:
            return "protected_file"
        return "file"

    raise PermissionError(f"Unsupported workspace entry type:{path}")

def inspect_workspace(workspace_root: Path) -> dict:
    """ Collect paths needed for sandbox setup or fail if unsuccessful """
    root = validate_workspace_root(workspace_root)
    protected_files = []
    protected_dirs = []
    symlinks = []
    pending = [(root, 0)]
    scanned_entries = 0

    while pending:
        directory, depth = pending.pop()

        if classify_workspace_entry(directory) != "directory":
            raise PermissionError(f"Workspace directory changed during inspection: {directory}")

        with os.scandir(directory) as entries:
            for entry in entries:
                if scanned_entries >= MAX_WORKSPACE_ENTRIES:
                    raise ValueError(
                        f"Workspace inspection exceeds {MAX_WORKSPACE_ENTRIES} entries."
                    )
                scanned_entries += 1

                path = Path(entry.path)
                kind = classify_workspace_entry(path)

                if kind == "protected_file":
                    protected_files.append(path)
                elif kind == "protected_directory":
                    protected_dirs.append(path)
                elif kind == "symlink":
                    symlinks.append(path)
                elif kind == "directory":
                    if depth >= MAX_WORKSPACE_DEPTH:
                        raise ValueError(
                            f"Workspace inspection exceeds {MAX_WORKSPACE_DEPTH} "
                            "directory levels."
                        )
                    pending.append((path, depth + 1))

    protected_files.sort()
    protected_dirs.sort()
    symlinks.sort()

    return {
        "root": root,
        "protected_files": protected_files,
        "protected_directories": protected_dirs,
        "symlinks": symlinks,
    }

def validate_workspace_symlink(path: Path, workspace_root: Path) -> Path:
    """ Check link's current target """
    root = validate_workspace_root(workspace_root)

    # Interpret relative paths from the workspace
    if not path.is_absolute():
        path = root / path

    if sensitive_path_reason(path) is not None:
        raise PermissionError(f"Protected symlink is not supported: {path}")

    parent = path.parent.resolve(strict=True)
    link = parent / path.name

    if not link.is_relative_to(root):
        raise PermissionError(f"Symlink is outside the workspace: {link}")

    if classify_workspace_entry(link) != "symlink":
        raise ValueError(f"Expected a workspace symlink: {link}")

    # Follow link, missing targets -> raise an error
    target = link.resolve(strict=True)

    # Check target and its containing dirs
    target_paths = [target]
    target_paths.extend(target.parents)

    for target_path in target_paths:
        if sensitive_path_reason(target_path) is not None:
            raise PermissionError(f"Symlink targets a protected path: {link}")

    info = target.lstat()
    regular_file = stat.S_ISREG(info.st_mode)
    directory = stat.S_ISDIR(info.st_mode)

    if not regular_file and not directory:
        raise PermissionError(f"Symlink targets an unsupported entry: {link}")

     # Normal Workspace files and directories are allowed
    if target.is_relative_to(root):
        if regular_file and info.st_nlink != 1:
            raise PermissionError(
                f"Symlink targets a hard-linked workspace file: {link}"
            )
        return target

    # System targets must belong to the read-only sys paths
    for directory_name in SYSTEM_READ_PATHS:
        system_directory = Path(directory_name)

        if system_directory.is_dir():
            system_root = system_directory.resolve(strict=True)

            if target.is_relative_to(system_root):
                return target

    raise PermissionError(f"Symlink target is outside the allowed paths: {link}")

@contextmanager
def workspace_mount_args(workspace_root: Path) -> Iterator[dict]:
    """ Prepare workspace mounts and keep empty placeholders alive """

    root = validate_workspace_root(workspace_root)

    # Keep a handle to this directory, even if its pathname is replaced later.
    # O_NOFOLLOW rejects a symlink at the final path component.
    # O_CLOEXEC prevents accidental inheritance; capture passes this handle explicitly.
    flags = os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    root_fd = os.open(root, flags)

    try:
        inspection = inspect_workspace(root)

        # Stop if inspection ended with a different directory at the root path.
        # This catches replacement during the scan, but does not freeze its contents.
        opened = os.fstat(root_fd)
        current = root.lstat()
        if (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino):
            raise ShellSandboxError(
                "Workspace changed during inspection. Run the command again."
            )

        for link in inspection["symlinks"]:
            validate_workspace_symlink(link, root)

        # These empty placeholders contain no user data and stay outside the project.
        with TemporaryDirectory(prefix="oceanus-masks-", dir="/tmp") as temporary:
            staging = Path(temporary)
            empty_file = staging / "empty-file"
            empty_file.touch()
            empty_directory = staging / "empty-directory"
            empty_directory.mkdir()

            # Bubblewrap mounts the open directory and verifies its identity.
            # It closes its copy of the handle before starting the command.
            arguments = ["--bind-fd", str(root_fd), str(root)]

            # Protected files get an empty, read-only view inside the sandbox.
            for path in inspection["protected_files"]:
                if classify_workspace_entry(path) != "protected_file":
                    raise PermissionError(
                        f"Protected file changed during setup: {path}"
                    )
                arguments.extend(["--ro-bind", str(empty_file), str(path)])

            # Cover protected directories without opening their contents.
            for path in inspection["protected_directories"]:
                if classify_workspace_entry(path) != "protected_directory":
                    raise PermissionError(
                        f"Protected directory changed during setup: {path}"
                    )
                arguments.extend(["--ro-bind", str(empty_directory), str(path)])

            # The caller needs both the mount arguments and the handle to pass.
            # Keep all resources alive until capture and process cleanup finish.
            yield {"arguments": arguments, "pass_fds": (root_fd,)}
    finally:
        # Close our own handle after success or any setup/capture failure.
        os.close(root_fd)