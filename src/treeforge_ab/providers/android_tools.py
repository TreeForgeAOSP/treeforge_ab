from __future__ import annotations

from pathlib import Path

from treeforge_ab.providers.toolchain import (
    materialize_provider,
)


def _executable(
    provider: str,
    relative: str,
) -> str:
    path = (
        materialize_provider(
            provider
        )
        / relative
    ).resolve()

    return str(path)


def treeforge_adb_executable() -> str:
    return _executable(
        "aosp-adb",
        "bin/adb",
    )


def treeforge_fastboot_executable() -> str:
    return _executable(
        "aosp-fastboot",
        "bin/fastboot",
    )


class AndroidHostTools:
    @property
    def adb(self) -> str:
        return (
            treeforge_adb_executable()
        )

    @property
    def fastboot(self) -> str:
        return (
            treeforge_fastboot_executable()
        )

    @property
    def lpdump(self) -> str:
        return _executable(
            "android-image-tools",
            "bin/lpdump",
        )

    @property
    def lpmake(self) -> str:
        return _executable(
            "android-image-tools",
            "bin/lpmake",
        )

    @property
    def make_f2fs(self) -> str:
        return _executable(
            "android-image-tools",
            "bin/make_f2fs",
        )


__all__ = (
    "AndroidHostTools",
    "treeforge_adb_executable",
    "treeforge_fastboot_executable",
)
