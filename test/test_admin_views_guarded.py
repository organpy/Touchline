"""Every admin view must re-check admin rights on each click."""

import importlib
import inspect
import pkgutil
from pathlib import Path

import discord
import pytest

from utils.permissions import AdminOnlyView

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = ("cogs.admin", "cogs.dashboard")


def _modules():
    for package_name in PACKAGES:
        package = importlib.import_module(package_name)
        for info in pkgutil.iter_modules(package.__path__):
            yield importlib.import_module(f"{package_name}.{info.name}")


def test_every_admin_view_class_re_checks_admin_rights():
    unguarded = []
    for module in _modules():
        for name, cls in inspect.getmembers(module, inspect.isclass):
            if cls.__module__ != module.__name__ or not issubclass(cls, discord.ui.View):
                continue
            if issubclass(cls, discord.ui.Modal):
                continue  # a modal is only reachable from a guarded view
            if not issubclass(cls, AdminOnlyView):
                unguarded.append(f"{module.__name__}.{name}")
    assert unguarded == []


@pytest.mark.parametrize("package", ["cogs/admin", "cogs/dashboard"])
def test_admin_code_never_builds_a_plain_view(package):
    offenders = [
        str(path.relative_to(ROOT))
        for path in (ROOT / package).glob("*.py")
        if "discord.ui.View(" in path.read_text(encoding="utf-8-sig")
    ]
    assert offenders == []
