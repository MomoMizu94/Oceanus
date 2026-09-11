# Require manual y/n confirmation before executing run_shell

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