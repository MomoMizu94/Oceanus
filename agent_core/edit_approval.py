def describe_edit(path: str, diff: str) -> str:
    """ Describe one edit. Only changes the display"""
    displayed = []

    for character in diff:
        if character == "\n":
            displayed.append(character)
        elif character == "\\":
            displayed.append("\\\\")
        elif character.isprintable():
            displayed.append(character)
        else:
            displayed.append(repr(character)[1:-1])

    return (
        f"Resolved path: {path!r}\n"
        "Proposed diff (backslashes and control characters are escaped):\n"
        + "".join(displayed)
        + "\nApproval applies to this edit only."
    )

def approve_edit(path: str, diff: str) -> bool:
    """ Ask for approval of one diff in CLI """
    print(describe_edit(path, diff))

    try:
        answer = input("Apply this edit? [y/N]: ")
    except EOFError:
        return False

    return answer.strip().lower() == "y"