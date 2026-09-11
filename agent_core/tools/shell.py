# Implement run_shell and return command output after manual approval
import subprocess
from pathlib import Path

from agent_core.guardrails import approve_shell

def run_shell(command: str) -> str:
    """ Run approved shell commmand and return output + status """
    working_directory = str(Path.cwd())

    if not approve_shell(command, working_directory):
        return "Command denied by the user. Nothing was executed."

    try:
        completed = subprocess.run(
            command,
            shell=True,
            cwd=working_directory,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=60,
            check=False
        )
    except subprocess.TimeoutExpired:
        return "Command timed out after 1 minute."

    return(
        f"Exit code: {completed.returncode}\n"
        f"STDOUT:\n{completed.stdout}\n"
        f"STDERR:\n{completed.stderr}"
    )