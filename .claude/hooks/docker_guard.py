"""Claude Code PreToolUse hook: refuse Docker commands that delete volumes.

The object store's files and the catalog's Postgres database live in named Docker volumes.
These commands delete volumes, so an assistant may not run them in this repo:

    docker compose down -v / --volumes     docker volume rm
    docker volume prune                    docker system prune

A command is refused when it names docker anywhere and one of those forms appears anywhere
in it: after other commands, with docker called by its full path, or held in a variable.
That is broad on purpose, so a command that only mentions one of them (a grep, a commit
message) is refused too.

Reads the hook payload (JSON) on stdin. Exit 0 allows the command; exit 2 refuses it and
the text on stderr is shown to the assistant. It applies to the assistant's Bash and
PowerShell calls only, not to what a person types in their own terminal.

Check it: uv run pytest tests/test_docker_guard.py
"""

import json
import re
import sys

# Stay inside one command: stop at ; & | and at a line end, so `down` in one command and
# `-v` in the next (`docker compose down; docker run -v ...`) do not add up to a match.
SAME_COMMAND = r"[^;&|\n]*"
# -v alone or bundled with other short flags, or --volumes, as a whole word.
VOLUMES_FLAG = r"(?<![\w-])(?:-[a-z]*v[a-z]*|--volumes)(?![\w-])"

RULES = [
    (
        "docker compose down with -v / --volumes",
        re.compile(rf"\bcompose\b{SAME_COMMAND}\bdown\b{SAME_COMMAND}{VOLUMES_FLAG}"),
    ),
    ("docker volume rm", re.compile(r"\bvolume\s+(?:rm|remove)\b")),
    ("docker volume prune", re.compile(r"\bvolume\s+prune\b")),
    ("docker system prune", re.compile(r"\bsystem\s+prune\b")),
]


def refused(command: str) -> str | None:
    """Return the name of the rule the command breaks, or None when it may run."""
    text = command.lower()
    text = re.sub(r"[\\`]\r?\n", " ", text)  # a line continued with \ (sh) or ` (PowerShell)
    text = text.replace('"', "").replace("'", "")  # "docker.exe" "volume" "rm"
    if "docker" not in text:
        return None
    for name, pattern in RULES:
        if pattern.search(text):
            return name
    return None


def main() -> int:
    try:
        command = json.load(sys.stdin)["tool_input"]["command"]
    except (ValueError, KeyError, TypeError) as error:
        print(f"docker_guard: could not read the hook payload ({error!r})", file=sys.stderr)
        return 2  # refuse rather than let a command through unread
    rule = refused(str(command))
    if rule is None:
        return 0
    print(
        f"Refused by .claude/hooks/docker_guard.py: {rule}. It deletes Docker volumes, "
        "which hold the object store and the catalog database. Do not rephrase the command "
        "to get past this. If the deletion is intended, the user runs it in their own "
        "terminal.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
