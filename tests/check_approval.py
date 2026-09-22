from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import Mock, patch

from agent_core.guardrails import approve_shell
from agent_core.tools.shell import run_shell


command = "printf hello"

with patch("agent_core.tools.shell.subprocess.run") as process:
    # No approval function means no execution.
    result = run_shell(command)
    assert "denied" in result.lower()
    process.assert_not_called()

    # Explicit denial also prevents execution.
    with patch("builtins.input", return_value="n"):
        with redirect_stdout(StringIO()):
            result = run_shell(command, on_approval=approve_shell)

    assert "denied" in result.lower()
    process.assert_not_called()

    # Unavailable terminal input must deny execution.
    with patch("builtins.input", side_effect=EOFError):
        with redirect_stdout(StringIO()):
            result = run_shell(command, on_approval=approve_shell)

    assert "denied" in result.lower()
    process.assert_not_called()

    # Approval reaches subprocess.run, which is mocked here.
    process.return_value = Mock(
        returncode=0,
        stdout="hello",
        stderr="",
    )

    with patch("builtins.input", return_value="y"):
        with redirect_stdout(StringIO()):
            result = run_shell(command, on_approval=approve_shell)

    process.assert_called_once()
    assert process.call_args.args == (command,)
    assert "Exit code: 0" in result
    assert "hello" in result

print("Approval checks passed.")