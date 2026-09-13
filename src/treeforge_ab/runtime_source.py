from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


class RuntimeSourceError(RuntimeError):
    pass


@dataclass(
    frozen=True,
    slots=True,
)
class AospRuntimeSource:
    origin: str
    product_out: Path
    boot_image: Path
    fstab_path: Path


_PRODUCT_NAMES = (
    "tangorpro",
    "treeforge_tangorpro",
)


def _unique_paths(
    paths: list[Path],
) -> tuple[Path, ...]:
    result: list[Path] = []
    seen: set[Path] = set()

    for path in paths:
        resolved = path.expanduser().resolve()

        if resolved in seen:
            continue

        seen.add(resolved)
        result.append(resolved)

    return tuple(result)


def _candidate_product_outs(
    root: Path,
) -> tuple[Path, ...]:
    """
    Expand one explicit/bounded root into known Android
    product-output shapes without recursively scanning it.
    """

    root = root.expanduser().resolve()

    candidates: list[Path] = []

    # The override may itself be PRODUCT_OUT.
    candidates.append(root)

    for product in _PRODUCT_NAMES:
        candidates.extend(
            (
                root
                / "out"
                / "target"
                / "product"
                / product,

                root
                / "target"
                / "product"
                / product,

                root
                / "aosp"
                / "out"
                / "target"
                / "product"
                / product,

                root
                / "aosp"
                / "target"
                / "product"
                / product,
            )
        )

    return _unique_paths(
        candidates
    )


def _fstab_for_product_out(
    product_out: Path,
) -> Path | None:
    candidates = (
        product_out
        / "vendor_ramdisk"
        / "first_stage_ramdisk"
        / "system"
        / "etc"
        / "fstab.gs201",

        product_out
        / "recovery"
        / "root"
        / "system"
        / "etc"
        / "recovery.fstab",
    )

    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()

    return None


def _read_properties(
    path: Path,
) -> dict[str, str]:
    result: dict[str, str] = {}

    if not path.is_file():
        return result

    for raw in path.read_text(
        encoding="utf-8",
        errors="replace",
    ).splitlines():
        line = raw.strip()

        if (
            not line
            or line.startswith("#")
            or "=" not in line
        ):
            continue

        key, value = line.split(
            "=",
            1,
        )

        result[
            key.strip()
        ] = value.strip()

    return result


def _validate_android15_r36(
    product_out: Path,
) -> None:
    """
    Keep the temporary repartition runtime on the exact
    Android 15 r36 family validated for tangorpro v0.1.
    """

    system_props = {}

    for candidate in (
        product_out / "system/build.prop",
        product_out / "system/system/build.prop",
    ):
        system_props.update(
            _read_properties(
                candidate
            )
        )

    vendor_props = _read_properties(
        product_out
        / "vendor"
        / "build.prop"
    )

    if not system_props:
        raise RuntimeSourceError(
            "AOSP runtime source is missing expanded "
            "system build properties: "
            f"{product_out}"
        )

    release = system_props.get(
        "ro.build.version.release"
    )

    sdk = system_props.get(
        "ro.build.version.sdk"
    )

    build_id = system_props.get(
        "ro.build.id"
    )

    if release != "15":
        raise RuntimeSourceError(
            "AOSP runtime source has unsupported "
            f"Android release {release!r}; expected '15'."
        )

    if sdk != "35":
        raise RuntimeSourceError(
            "AOSP runtime source has unsupported "
            f"SDK {sdk!r}; expected '35'."
        )

    if (
        build_id is not None
        and build_id
        != "BP1A.250505.005.D1"
    ):
        raise RuntimeSourceError(
            "AOSP runtime source has unsupported "
            f"build ID {build_id!r}; expected "
            "'BP1A.250505.005.D1'."
        )

    vendor_device = vendor_props.get(
        "ro.product.vendor.device"
    )

    if (
        vendor_device is not None
        and vendor_device != "tangorpro"
    ):
        raise RuntimeSourceError(
            "AOSP runtime source vendor device is "
            f"{vendor_device!r}; expected 'tangorpro'."
        )


def _resolve_product_out(
    *,
    roots: tuple[
        tuple[str, Path],
        ...,
    ],
) -> AospRuntimeSource:
    attempted: list[Path] = []

    for origin, root in roots:
        for product_out in (
            _candidate_product_outs(
                root
            )
        ):
            attempted.append(
                product_out
            )

            boot_image = (
                product_out
                / "boot.img"
            )

            if not boot_image.is_file():
                continue

            fstab_path = (
                _fstab_for_product_out(
                    product_out
                )
            )

            if fstab_path is None:
                continue

            _validate_android15_r36(
                product_out
            )

            return AospRuntimeSource(
                origin=origin,
                product_out=(
                    product_out.resolve()
                ),
                boot_image=(
                    boot_image.resolve()
                ),
                fstab_path=fstab_path,
            )

    rendered = "\n".join(
        f"  {path}"
        for path in attempted
    )

    raise RuntimeSourceError(
        "Unable to resolve a validated tangorpro "
        "Android 15 r36 AOSP runtime source.\n"
        "Checked:\n"
        + rendered
    )


def resolve_aosp_runtime_source(
    override: Path | None = None,
    *,
    standalone_root: Path | None = None,
) -> AospRuntimeSource:
    """
    Resolve the temporary repartition runtime.

    An explicit override is authoritative. Without one,
    only bounded TreeForge-owned development/output paths
    are considered.
    """

    if standalone_root is None:
        standalone_root = (
            Path(__file__)
            .resolve()
            .parents[2]
        )

    standalone_root = (
        standalone_root
        .expanduser()
        .resolve()
    )

    if override is not None:
        return _resolve_product_out(
            roots=(
                (
                    "explicit",
                    override,
                ),
            )
        )

    treeforge_root = (
        standalone_root.parent
        / "treeforge"
    ).resolve()

    roots = (
        (
            "treeforge-aosp",
            treeforge_root
            / "aosp",
        ),
        (
            "treeforge-output",
            treeforge_root
            / "output",
        ),
        (
            "treeforge-output-aosp",
            treeforge_root
            / "output"
            / "aosp",
        ),
        (
            "treeforge-output-android",
            treeforge_root
            / "output"
            / "android",
        ),
        (
            "treeforge-output-build",
            treeforge_root
            / "output"
            / "build",
        ),
    )

    return _resolve_product_out(
        roots=roots
    )
