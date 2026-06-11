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


# ---------------------------------------------------------------------
# Contrast guards — the TUI maps banner_dim → muted text, session_border
# → frame lines, etc. (ui-tui/src/theme.ts fromSkin). The first version
# of this skin used ~25%-luminance teals for those roles and they were
# unreadable on dark terminals. These tests pin the fix.
# ---------------------------------------------------------------------


def _luminance(hex_color: str) -> float:
    """WCAG relative luminance of an #RRGGBB color, 0..1."""
    r, g, b = (int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5))

    def lin(c: float) -> float:
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def _contrast(a: str, b: str) -> float:
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def test_text_layers_are_readable_on_dark_background(skin):
    """Foreground roles must clear WCAG-ish contrast against the darkest
    surface the skin uses (the menu/status background)."""
    colors = skin["colors"]
    bg = colors["completion_menu_bg"]
    # (role, minimum contrast ratio) — 4.5 is WCAG AA for body text;
    # secondary/dim roles get a relaxed-but-visible 3.0.
    for key, minimum in [
        ("banner_text", 7.0),     # TUI body text
        ("prompt", 7.0),
        ("banner_dim", 3.0),      # TUI muted — the original sin
        ("session_border", 3.0),
        ("status_bar_dim", 3.0),
        ("status_bar_text", 4.5),
        ("ui_label", 4.5),
        ("banner_title", 4.5),
    ]:
        ratio = _contrast(colors[key], bg)
        assert ratio >= minimum, (
            f"{key} ({colors[key]}) on {bg}: contrast {ratio:.2f} < {minimum}"
        )


def test_highlight_surfaces_are_distinguishable(skin):
    """Selected/current-row backgrounds must sit clearly above the base
    menu background — tone-on-tone teal was the 'colors too close' bug."""
    colors = skin["colors"]
    base = colors["completion_menu_bg"]
    for key in ("completion_menu_current_bg", "selection_bg"):
        ratio = _contrast(colors[key], base)
        assert ratio >= 1.8, (
            f"{key} ({colors[key]}) vs menu bg {base}: only {ratio:.2f}× apart"
        )
