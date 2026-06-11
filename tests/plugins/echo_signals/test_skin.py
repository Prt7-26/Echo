"""Validate the Echo skin YAML against Hermes' real skin engine.

The skin ships inside the plugin (plugins/echo_signals/skin/echo.yaml)
and is installed to <hermes_home>/skins/ by the ./echo launcher. These
tests make sure the file keeps parsing and that every field it sets is
one the engine actually consumes — a typo'd color key would otherwise
fail silently (skin_engine merges over defaults and ignores unknowns).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

SKIN_PATH = (
    Path(__file__).resolve().parents[3]
    / "plugins" / "echo_signals" / "skin" / "echo.yaml"
)

HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


@pytest.fixture(scope="module")
def skin() -> dict:
    return yaml.safe_load(SKIN_PATH.read_text(encoding="utf-8"))


def test_skin_file_exists_and_parses(skin):
    assert skin["name"] == "echo"
    assert "powered by Hermes" in skin["description"]


def test_all_colors_are_valid_hex(skin):
    for key, value in skin["colors"].items():
        assert HEX_RE.match(value), f"colors.{key} = {value!r} is not #RRGGBB"


def test_color_keys_are_known_to_the_engine(skin):
    """Every color key must appear in the engine's documented schema —
    unknown keys are silently ignored, which would be a stealth no-op."""
    from hermes_cli import skin_engine

    documented = set(re.findall(r"^\s{6}(\w+):", skin_engine.__doc__, re.M))
    # The builtin skins exercise more keys than the docstring lists;
    # union them in as also-known.
    for builtin in skin_engine._BUILTIN_SKINS.values():
        documented.update(builtin.get("colors", {}).keys())

    unknown = set(skin["colors"]) - documented
    assert not unknown, f"color keys not recognised by skin_engine: {sorted(unknown)}"


def test_build_skin_config_accepts_it(skin):
    from hermes_cli.skin_engine import _build_skin_config

    cfg = _build_skin_config(skin)
    assert cfg.name == "echo"
    assert cfg.branding["agent_name"] == "Echo"
    assert cfg.branding["prompt_symbol"] == "∿"
    assert cfg.tool_prefix == "┆"
    # Banner present and balanced: every [tag] line ends with [/].
    assert "powered by Hermes" in cfg.banner_logo
    for line in cfg.banner_logo.splitlines():
        if line.strip():
            assert line.rstrip().endswith("[/]"), f"unbalanced Rich markup: {line!r}"


def test_spinner_lists_are_nonempty_strings(skin):
    spinner = skin["spinner"]
    for key in ("waiting_faces", "thinking_faces", "thinking_verbs"):
        assert spinner[key], f"spinner.{key} is empty"
        assert all(isinstance(x, str) and x for x in spinner[key])
    for left, right in spinner["wings"]:
        assert isinstance(left, str) and isinstance(right, str)


def test_branding_keeps_hermes_attribution(skin):
    """Echo-as-primary branding, but the Hermes provenance must stay
    visible somewhere in the banner (maintainer's dual-attribution call)."""
    assert skin["branding"]["agent_name"] == "Echo"
    assert "powered by Hermes" in skin["banner_logo"]
