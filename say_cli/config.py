"""The user's speech preferences, read anew for each invocation."""

import os
import stat
import tempfile
from pathlib import Path

import tomlkit
from tomlkit.exceptions import ParseError


def config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME")
    if base and not Path(base).is_absolute():
        raise ValueError("XDG_CONFIG_HOME must be an absolute path")
    return (Path(base) if base else Path.home() / ".config") / "say" / "config.toml"


def validate_output(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or not value.isprintable():
        raise ValueError("Output must be a nonempty device name or 'default'")
    return value


def read_config(path: Path):
    try:
        return tomlkit.parse(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return tomlkit.document()
    except (ParseError, UnicodeError) as error:
        raise ValueError(f"Cannot read {path}: {error}") from error


def saved_output() -> str:
    path = config_path()
    try:
        return validate_output(read_config(path).get("output", "default"))
    except ValueError as error:
        raise ValueError(f"Invalid output configuration in {path}: {error}") from error


def selected_output(override: str | None) -> str | None:
    value = validate_output(override) if override is not None else saved_output()
    return None if value == "default" else value


def save_output(name: str) -> Path:
    validate_output(name)
    # Follow dotfile symlinks without replacing the symlink itself. A Nix-managed
    # target naturally fails as read-only, preserving declarative configuration.
    path = config_path().resolve()
    document = read_config(path)
    document["output"] = name
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=".config-", delete=False
        ) as stream:
            temporary = Path(stream.name)
            if path.exists():
                os.fchmod(stream.fileno(), stat.S_IMODE(path.stat().st_mode))
            stream.write(tomlkit.dumps(document))
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return config_path()
