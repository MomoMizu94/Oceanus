def describe_sensitive_access(operation: str, path: str, reason: str) -> str:
    """ Describe one protected opetaion without reading the target """
    if operation not in ("read", "write", "list"):
        raise ValueError("Expected a read, write or list opeation")
    if not isinstance(path, str) or not path:
        raise ValueError("Path is required!")
    if not isinstance(reason, str) or not reason:
        raise ValueError("Protection reason is required!")

    effects = {
        "read": "Read protected file contents.",
        "write": "Create or overwrite a protected file. This may change credentials.",
        "list": (
            "List immediate child names and types. "
            "This does not approve reading the listed files."
        )
    }

    return (
        "Sensitive filesystem access requested.\n"
        f"Operation: {operation}\n"
        f"Resolved path: {path!r}\n"
        f"Protection reason: {reason!r}\n"
        f"Effect: {effects[operation]}\n"
        "Information returned by this operation may be sent to the selected "
        "model provider and saved in conversation history.\n"
        "Approval applies to this operation and path only, not future requests.\n"
        "Any required external-access or edit approval remains separate."
    )

def approve_sensitive_access(operation: str, path: str, reason: str) -> bool:
    """ Prints out the explanation former by describe_sensitive_access() and asks for a decision """
    print(describe_sensitive_access(operation, path, reason))

    try:
        answer = input("Allow this sensitive operation? [y/N]: ")
    except EOFError:
        return False
    return answer.strip().lower() == "y"