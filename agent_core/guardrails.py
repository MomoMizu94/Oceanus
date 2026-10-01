from pathlib import Path
from collections.abc import Callable


def approve_shell(command: str, working_directory: str) -> bool:
    """ Approve command only when user enters 'y' """
    print("\nShell command requested:")
    print(command)
    print("Working directory: ", working_directory)

    try:
        answer = input("Allow this command? [y/N]: ")
    except EOFError:
        # Deny execution when input is unavailable
        return False

    return answer.strip().lower() == "y"

def approve_file(operation: str, path: str) -> bool:
    """ Approve one external file operation through CLI """
    if operation not in ("read", "write", "list"):
        return False

    print("\nExternal file access requested:")
    print("Operation:", operation)
    print("Resolved path:", path)

    if operation == "write":
        print("This may create a file or overwrite its entire contents.")

    if operation == "list":
        print(
            "Lists immediate child names and types only. "
            "Does not read file contents or enter subdirectories."
        )
        
    try:
        answer = input("Allow this file operation? [y/N]: ")
    except EOFError:
        return False

    return answer.strip().lower() == "y"

def resolve_workspace_path(
        path: str,
        workspace_root: Path, *,
        operation: str = "read",
        on_file_approval: Callable[[str, str], bool] | None = None
        ) -> Path:
    """ Resolve a file path and require access approval outside the workspace. """
    root = workspace_root.resolve()
    target = Path(path).expanduser()

    if not target.is_absolute():
        target = root / target

    target = target.resolve()

    if not target.is_relative_to(root):
        if on_file_approval is None:
            raise PermissionError(f"External {operation} requires approval: {target}")
        if on_file_approval(operation, str(target)) is not True:
            raise PermissionError(f"External {operation} denied: {target}")
        # symlink catching while approval is pending
        if target.resolve() != target:
            raise PermissionError("Path changed while awaiting approval. Request access again.")

    return target