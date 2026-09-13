from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path

from treeforge_ab.providers.android_tools import (
    AndroidHostTools,
    treeforge_fastboot_executable,
)


def _sha256(
    path: Path,
) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as stream:
        while True:
            chunk = stream.read(
                1024 * 1024
            )

            if not chunk:
                break

            digest.update(chunk)

    return digest.hexdigest()


def _copy_exact(
    *,
    source: Path,
    target: Path,
) -> str:
    source = source.resolve()

    if not source.is_file():
        raise RuntimeError(
            f"Provider runtime file missing: {source}"
        )

    expected = _sha256(
        source
    )

    target.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    if (
        not target.is_file()
        or _sha256(target) != expected
    ):
        temporary = target.with_name(
            target.name + ".tmp"
        )

        if temporary.exists():
            temporary.unlink()

        shutil.copy2(
            source,
            temporary,
        )

        os.chmod(
            temporary,
            source.stat().st_mode,
        )

        temporary.replace(
            target
        )

    actual = _sha256(
        target
    )

    if actual != expected:
        raise RuntimeError(
            "Staged provider identity mismatch: "
            f"{target}"
        )

    return actual


def stage_fastboot_f2fs_runtime(
    *,
    repository_root: Path,
    fastboot: Path | None = None,
    make_f2fs: Path | None = None,
) -> Path:
    """
    Build a disposable TreeForge-owned runtime containing:

      fastboot
      make_f2fs wrapper
      bin/make_f2fs
      lib64/<exact provider runtime closure>

    Android fastboot searches for make_f2fs beside its own
    executable. The wrapper supplies the provider lib64
    directory and then executes the exact provider binary.

    Provider cache contents are never modified.
    """

    repository_root = (
        repository_root
        .expanduser()
        .resolve()
    )

    tools = AndroidHostTools()

    if fastboot is None:
        fastboot = Path(
            treeforge_fastboot_executable()
        )

    if make_f2fs is None:
        make_f2fs = Path(
            tools.make_f2fs
        )

    fastboot = (
        fastboot
        .expanduser()
        .resolve()
    )

    make_f2fs = (
        make_f2fs
        .expanduser()
        .resolve()
    )

    if not fastboot.is_file():
        raise RuntimeError(
            f"fastboot provider missing: {fastboot}"
        )

    if not make_f2fs.is_file():
        raise RuntimeError(
            "make_f2fs provider missing: "
            f"{make_f2fs}"
        )

    #
    # Canonical android-image-tools shape:
    #
    #   PROVIDER/bin/make_f2fs
    #   PROVIDER/lib64/*.so
    #
    image_tools_root = (
        make_f2fs
        .parent
        .parent
    )

    provider_lib64 = (
        image_tools_root
        / "lib64"
    )

    if not provider_lib64.is_dir():
        raise RuntimeError(
            "android-image-tools provider is missing "
            f"its runtime library directory: "
            f"{provider_lib64}"
        )

    runtime_root = (
        repository_root
        / "output"
        / "runtime-tools"
        / "fastboot-f2fs"
    )

    runtime_bin = (
        runtime_root
        / "bin"
    )

    runtime_lib64 = (
        runtime_root
        / "lib64"
    )

    runtime_bin.mkdir(
        parents=True,
        exist_ok=True,
    )

    runtime_lib64.mkdir(
        parents=True,
        exist_ok=True,
    )

    staged_fastboot = (
        runtime_root
        / "fastboot"
    )

    staged_real_make_f2fs = (
        runtime_bin
        / "make_f2fs"
    )

    fastboot_sha = _copy_exact(
        source=fastboot,
        target=staged_fastboot,
    )

    make_f2fs_sha = _copy_exact(
        source=make_f2fs,
        target=staged_real_make_f2fs,
    )

    runtime_libraries = {}

    for source in sorted(
        provider_lib64.glob("*.so")
    ):
        target = (
            runtime_lib64
            / source.name
        )

        runtime_libraries[
            source.name
        ] = _copy_exact(
            source=source,
            target=target,
        )

    if "libc++.so" not in runtime_libraries:
        raise RuntimeError(
            "android-image-tools provider runtime "
            "does not contain libc++.so"
        )

    #
    # fastboot itself invokes:
    #
    #   dirname(fastboot)/make_f2fs
    #
    # Make that path a TreeForge wrapper. It establishes
    # only the exact provider library directory and then
    # execs the byte-identical provider make_f2fs.
    #
    wrapper = (
        runtime_root
        / "make_f2fs"
    )

    wrapper.write_text(
        """#!/bin/sh
HERE="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
export LD_LIBRARY_PATH="$HERE/lib64"
exec "$HERE/bin/make_f2fs" "$@"
""",
        encoding="utf-8",
    )

    os.chmod(
        wrapper,
        0o755,
    )

    (
        runtime_root
        / "runtime.json"
    ).write_text(
        json.dumps(
            {
                "schema_version": 2,

                "fastboot": {
                    "source":
                        str(fastboot),
                    "staged":
                        str(staged_fastboot),
                    "sha256":
                        fastboot_sha,
                },

                "make_f2fs": {
                    "source":
                        str(make_f2fs),
                    "staged_binary":
                        str(
                            staged_real_make_f2fs
                        ),
                    "fastboot_sibling_wrapper":
                        str(wrapper),
                    "sha256":
                        make_f2fs_sha,
                },

                "runtime_library_source":
                    str(provider_lib64),

                "runtime_libraries":
                    runtime_libraries,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    return staged_fastboot.resolve()
