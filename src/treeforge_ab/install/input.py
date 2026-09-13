from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import re
import shlex

from treeforge_ab.install.models import (
    AndroidInstallBuild,
    AndroidInstallError,
    AndroidInstallImage,
    AndroidInstallOperation,
    AndroidInstallRequirement,
)


SUPPORTED_PRODUCT = "tangorpro"
SUPPORTED_ANDROID_RELEASE = "15"
SUPPORTED_SDK = "35"

VALIDATED_FACTORY_BUILD = (
    "BP1A.250505.005"
)

VALIDATED_AOSP_RELEASE = (
    "android-15.0.0_r36"
)

VALIDATED_AOSP_BUILD_ID = (
    "BP1A.250505.005.D1"
)

SUPPORTED_BOOTLOADER = (
    "tangorpro-15.2-13237001"
)

STOCK_SUPER_SIZE_BYTES = (
    8_531_214_336
)

DENIED_PARTITIONS = frozenset(
    {
        "abl",
        "bl1",
        "bl2",
        "bl31",
        "pbl",
        "tzsw",
        "gsa",
        "ldfw",
        "dpm",
        "dram_train",
    }
)

SUPPORTED_ANDROID_INFO_REQUIREMENTS = (
    frozenset(
        {
            "board",
            "product",
            "partition-exists",
            "version-bootloader",
            "version-baseband",
        }
    )
)

FACTORY_IMAGE_SHA256 = {
    "boot.img":
        "8e20b0271098e05494a356179c46721c"
        "41921da85284345cdbbd479f548e0914",

    "init_boot.img":
        "d712bca56516baaf55937bb6fd7fe4d4e"
        "77ff3a784e646d781d2198e422d5d87",

    "dtbo.img":
        "bd7f79d420fc4dc15d8cab0965f2af6b"
        "7c516eda693923368f10de42872c67d1",

    "vendor_kernel_boot.img":
        "551edbf21947ee52c5f81a5a189243147"
        "3a585e38a8982744aa1b135d5c8d338",

    "pvmfw.img":
        "a91087e2f88b2ea65d6aa8cf46ef4bae"
        "e55a615b9fbbc85a1dcc69c4eb8b3a39",

    "vendor_boot.img":
        "3c95322d15eaecc9494683d7646aa2267"
        "2e3a690e9504b1732328e02f76e7f08",

    "vbmeta.img":
        "3046e39cff13d5ca7506b3eeb8ad59cc9"
        "7f1471ebf852b6087d1f9d02e0f10ca",

    "vbmeta_system.img":
        "93867ee81cef737c8a725fc2f62f31839"
        "d471f671c7c58cf0c29b0344953c995",

    "vbmeta_vendor.img":
        "821cb4af817b9d7af96b0589e71a59885"
        "0a24c567907e3a2533086764906bed3",

    "system.img":
        "7c7345517944b25087006e00ca2e51582"
        "656c4dc113985c44d36824c12cc818c",

    "system_dlkm.img":
        "399e258ba6f903691a3bd5dfd67985c93"
        "e2de8d806312d542e8b345f61c5ff4c",

    "system_ext.img":
        "302aea0dde93154c1a2ab17ec02f58614"
        "acdb91a5fee6ac5464adb6845238789",

    "product.img":
        "2fd5b1113aa71838e16dbd6df1a527a2"
        "c0e29eeee275e66fc2e8a63298fc267e",

    "vendor.img":
        "3187f2b2ff7a569186f6d1b6c7c36da8"
        "a32f3627af1c86652bb560d5f09ba7e1",

    "vendor_dlkm.img":
        "96a92d28c4bb5353f039d3152c2681c34"
        "5b45520c5ffd06c8f93a5fbbc588b20",
}

BOOTLOADER_IMAGE_SHA256 = (
    "155eaada2732ed79a6bb9893f43054fe"
    "0db3255cbe7e267f8aa3c672b8922ab0"
)


@dataclass(
    frozen=True,
    slots=True,
)
class ResolvedInput:
    root: Path
    kind: str

    product: str
    android_release: str
    sdk: str

    baseline: str
    bootloader: str

    images: tuple[
        AndroidInstallImage,
        ...,
    ]

    build: AndroidInstallBuild

    factory_bootloader_image: Path | None
    factory_bootloader_sha256: str | None


def _sha256(
    path: Path,
) -> str:
    digest = sha256()

    with path.open("rb") as handle:
        while True:
            chunk = handle.read(
                1024 * 1024
            )

            if not chunk:
                break

            digest.update(chunk)

    return digest.hexdigest()


def _file_identity(
    path: Path,
) -> tuple[int, str]:
    if not path.is_file():
        raise AndroidInstallError(
            "required input artifact is "
            f"unavailable: {path}"
        )

    return (
        path.stat().st_size,
        _sha256(path),
    )


def _safe_relative_image_name(
    value: str,
) -> str:
    candidate = Path(value)

    if candidate.is_absolute():
        raise AndroidInstallError(
            "fastboot-info.txt contains "
            "an absolute image path: "
            f"{value}"
        )

    if ".." in candidate.parts:
        raise AndroidInstallError(
            "fastboot-info.txt image path "
            f"escapes the input: {value}"
        )

    return candidate.as_posix()


def _parse_fastboot_info(
    product_out: Path,
) -> tuple[
    tuple[
        AndroidInstallOperation,
        ...,
    ],
    bool,
]:
    path = (
        product_out
        / "fastboot-info.txt"
    )

    if not path.is_file():
        raise AndroidInstallError(
            f"missing fastboot-info.txt: {path}"
        )

    text = path.read_text(
        encoding="utf-8",
        errors="replace",
    )

    operations = []
    domain = "bootloader-fastboot"

    version_seen = False
    uses_update_super = False

    for line_number, raw in enumerate(
        text.splitlines(),
        1,
    ):
        line = raw.strip()

        if (
            not line
            or line.startswith("#")
        ):
            continue

        fields = shlex.split(line)

        if not fields:
            continue

        command = fields[0]

        if command == "version":
            if fields != [
                "version",
                "1",
            ]:
                raise AndroidInstallError(
                    "unsupported fastboot-info "
                    f"version: {line}"
                )

            version_seen = True

            operations.append(
                AndroidInstallOperation(
                    line_number=line_number,
                    raw=line,
                    command="version",
                    domain="metadata",
                )
            )

            continue

        if command == "flash":
            args = fields[1:]

            apply_vbmeta = (
                "--apply-vbmeta"
                in args
            )

            slot_other = (
                "--slot-other"
                in args
            )

            positional = tuple(
                value
                for value in args
                if not value.startswith("--")
            )

            if not positional:
                raise AndroidInstallError(
                    "malformed flash operation "
                    f"on line {line_number}: "
                    f"{line}"
                )

            partition = positional[0]

            if partition in DENIED_PARTITIONS:
                raise AndroidInstallError(
                    "input manifest requests "
                    "a denied bootloader/firmware "
                    f"partition: {partition}"
                )

            image_name = (
                positional[1]
                if len(positional) >= 2
                else f"{partition}.img"
            )

            image_name = (
                _safe_relative_image_name(
                    image_name
                )
            )

            operations.append(
                AndroidInstallOperation(
                    line_number=line_number,
                    raw=line,
                    command="flash",
                    domain=domain,
                    partition=partition,
                    image_name=image_name,
                    apply_vbmeta=apply_vbmeta,
                    slot_other=slot_other,
                )
            )

            continue

        if command == "reboot":
            if (
                len(fields) != 2
                or fields[1]
                not in {
                    "fastboot",
                    "bootloader",
                }
            ):
                raise AndroidInstallError(
                    "unsupported reboot "
                    f"operation: {line}"
                )

            target = fields[1]

            operations.append(
                AndroidInstallOperation(
                    line_number=line_number,
                    raw=line,
                    command=(
                        f"reboot-{target}"
                    ),
                    domain="transition",
                )
            )

            domain = (
                "fastbootd"
                if target == "fastboot"
                else "bootloader-fastboot"
            )

            continue

        if command == "update-super":
            if domain != "fastbootd":
                raise AndroidInstallError(
                    "update-super appears "
                    "outside fastbootd phase"
                )

            uses_update_super = True

            operations.append(
                AndroidInstallOperation(
                    line_number=line_number,
                    raw=line,
                    command="update-super",
                    domain="fastbootd",
                )
            )

            continue

        if command == "if-wipe":
            if (
                len(fields) != 3
                or fields[1] != "erase"
                or fields[2]
                not in {
                    "userdata",
                    "metadata",
                }
            ):
                raise AndroidInstallError(
                    "unsupported conditional "
                    f"operation: {line}"
                )

            operations.append(
                AndroidInstallOperation(
                    line_number=line_number,
                    raw=line,
                    command=(
                        f"erase-{fields[2]}"
                    ),
                    domain="conditional",
                    partition=fields[2],
                    conditional=True,
                )
            )

            continue

        raise AndroidInstallError(
            "unsupported fastboot-info "
            f"command on line "
            f"{line_number}: {line}"
        )

    if not version_seen:
        raise AndroidInstallError(
            "fastboot-info.txt does not "
            "declare version 1"
        )

    if not any(
        operation.command == "flash"
        for operation in operations
    ):
        raise AndroidInstallError(
            "fastboot-info.txt contains "
            "no flash operations"
        )

    return (
        tuple(operations),
        uses_update_super,
    )


def _load_misc_info(
    product_out: Path,
) -> dict[str, str]:
    path = (
        product_out
        / "misc_info.txt"
    )

    if not path.is_file():
        return {}

    values = {}

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

        values[
            key.strip()
        ] = value.strip()

    return values


def _parse_android_info_requirements(
    path: Path,
) -> tuple[
    AndroidInstallRequirement,
    ...,
]:
    requirements = []

    for line_number, raw in enumerate(
        path.read_text(
            encoding="utf-8",
            errors="replace",
        ).splitlines(),
        1,
    ):
        line = raw.strip()

        if (
            not line
            or line.startswith("#")
        ):
            continue

        if not line.startswith(
            "require "
        ):
            raise AndroidInstallError(
                "unsupported android-info "
                f"directive on line "
                f"{line_number}: {line}"
            )

        payload = (
            line[len("require "):]
            .strip()
        )

        if "=" not in payload:
            raise AndroidInstallError(
                "malformed android-info "
                f"requirement: {line}"
            )

        key, value = payload.split(
            "=",
            1,
        )

        key = key.strip()

        values = tuple(
            item.strip()
            for item in value.split("|")
            if item.strip()
        )

        if (
            not key
            or not values
        ):
            raise AndroidInstallError(
                "malformed android-info "
                f"requirement: {line}"
            )

        if (
            key
            not in
            SUPPORTED_ANDROID_INFO_REQUIREMENTS
        ):
            raise AndroidInstallError(
                "unsupported android-info "
                f"requirement kind: {key}"
            )

        requirements.append(
            AndroidInstallRequirement(
                line_number=line_number,
                raw=line,
                key=key,
                values=values,
            )
        )

    if not requirements:
        raise AndroidInstallError(
            "android-info.txt contains "
            "no admission requirements"
        )

    return tuple(requirements)


def _target_device(
    requirements: tuple[
        AndroidInstallRequirement,
        ...,
    ],
    *,
    fallback: str,
) -> str:
    for key in (
        "product",
        "board",
    ):
        for requirement in requirements:
            if requirement.key != key:
                continue

            if fallback in requirement.values:
                return fallback

            if requirement.values:
                return requirement.values[0]

    return fallback


def _load_properties(
    path: Path,
) -> dict[str, str]:
    values = {}

    if not path.is_file():
        return values

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

        values[
            key.strip()
        ] = value.strip()

    return values


def _aosp_identity(
    product_out: Path,
) -> tuple[
    str,
    str,
    str,
]:
    system_candidates = (
        product_out
        / "system"
        / "build.prop",
        product_out
        / "system"
        / "system"
        / "build.prop",
    )

    system = {}

    for candidate in system_candidates:
        if candidate.is_file():
            system = _load_properties(
                candidate
            )
            break

    vendor = _load_properties(
        product_out
        / "vendor"
        / "build.prop"
    )

    build_id = (
        system.get(
            "ro.build.id"
        )
        or system.get(
            "ro.system.build.id"
        )
    )

    sdk = system.get(
        "ro.build.version.sdk"
    )

    release = system.get(
        "ro.build.version.release"
    )

    device = (
        vendor.get(
            "ro.product.vendor.device"
        )
        or system.get(
            "ro.product.device"
        )
    )

    if device != SUPPORTED_PRODUCT:
        raise AndroidInstallError(
            "unsupported AOSP product: "
            f"{device!r}; expected "
            f"{SUPPORTED_PRODUCT!r}"
        )

    if release != SUPPORTED_ANDROID_RELEASE:
        raise AndroidInstallError(
            "unsupported Android release: "
            f"{release!r}; expected "
            f"{SUPPORTED_ANDROID_RELEASE!r}"
        )

    if sdk != SUPPORTED_SDK:
        raise AndroidInstallError(
            "unsupported Android SDK: "
            f"{sdk!r}; expected "
            f"{SUPPORTED_SDK!r}"
        )

    if build_id != VALIDATED_AOSP_BUILD_ID:
        raise AndroidInstallError(
            "unsupported AOSP build identity: "
            f"{build_id!r}; expected "
            f"{VALIDATED_AOSP_BUILD_ID!r}"
        )

    return (
        device,
        release,
        sdk,
    )


def _resolve_aosp(
    root: Path,
) -> ResolvedInput:
    operations, uses_update_super = (
        _parse_fastboot_info(
            root
        )
    )

    android_info_path = (
        root
        / "android-info.txt"
    )

    if not android_info_path.is_file():
        raise AndroidInstallError(
            "AOSP input is missing "
            f"android-info.txt: "
            f"{android_info_path}"
        )

    requirements = (
        _parse_android_info_requirements(
            android_info_path
        )
    )

    target_device = _target_device(
        requirements,
        fallback=root.name,
    )

    if (
        target_device
        != SUPPORTED_PRODUCT
    ):
        raise AndroidInstallError(
            "unsupported AOSP target: "
            f"{target_device!r}"
        )

    product, release, sdk = (
        _aosp_identity(
            root
        )
    )

    misc = _load_misc_info(
        root
    )

    super_size = misc.get(
        "super_partition_size"
    )

    if (
        super_size is not None
        and int(
            super_size,
            0,
        )
        != STOCK_SUPER_SIZE_BYTES
    ):
        raise AndroidInstallError(
            "unsupported AOSP super geometry: "
            f"{super_size}; expected "
            f"{STOCK_SUPER_SIZE_BYTES}"
        )

    userdata_fs_type = misc.get(
        "userdata_fs_type"
    )

    if userdata_fs_type is not None:
        userdata_fs_type = (
            userdata_fs_type
            .strip()
            .lower()
        )

    images = []

    seen_paths = set()

    for operation in operations:
        if operation.command != "flash":
            continue

        assert (
            operation.partition
            is not None
        )

        assert (
            operation.image_name
            is not None
        )

        path = (
            root
            / operation.image_name
        ).resolve()

        try:
            path.relative_to(root)
        except ValueError as error:
            raise AndroidInstallError(
                "resolved image escapes "
                f"the input: {path}"
            ) from error

        if path in seen_paths:
            continue

        seen_paths.add(path)

        size_bytes, digest = (
            _file_identity(path)
        )

        images.append(
            AndroidInstallImage(
                partition=(
                    operation.partition
                ),
                image_name=(
                    operation.image_name
                ),
                path=path,
                size_bytes=size_bytes,
                sha256=digest,
                domain=operation.domain,
                apply_vbmeta=(
                    operation.apply_vbmeta
                ),
                slot_other=(
                    operation.slot_other
                ),
            )
        )

    required = set(
        FACTORY_IMAGE_SHA256
    )

    available = {
        image.image_name
        for image in images
    }

    missing = sorted(
        required - available
    )

    if missing:
        raise AndroidInstallError(
            "AOSP product output is missing "
            "required full-A/B images: "
            + ", ".join(missing)
        )

    super_empty_path = None

    if uses_update_super:
        super_empty_path = (
            root
            / "super_empty.img"
        )

        if not (
            super_empty_path
            .is_file()
        ):
            raise AndroidInstallError(
                "fastboot-info.txt requires "
                "update-super but "
                "super_empty.img is missing"
            )

    build = AndroidInstallBuild(
        product_out=root,
        fastboot_info_path=(
            root
            / "fastboot-info.txt"
        ),
        android_info_path=(
            android_info_path
        ),
        target_device=(
            target_device
        ),
        requirements=requirements,
        operations=operations,
        images=tuple(images),
        uses_update_super=(
            uses_update_super
        ),
        super_empty_path=(
            super_empty_path
        ),
        userdata_fs_type=(
            userdata_fs_type
        ),
    )

    return ResolvedInput(
        root=root,
        kind="aosp-r36",
        product=product,
        android_release=release,
        sdk=sdk,
        baseline=(
            VALIDATED_AOSP_RELEASE
        ),
        bootloader=(
            SUPPORTED_BOOTLOADER
        ),
        images=tuple(images),
        build=build,
        factory_bootloader_image=None,
        factory_bootloader_sha256=None,
    )


def _logical_image_name(
    filename: str,
) -> str | None:
    for logical in (
        FACTORY_IMAGE_SHA256
    ):
        if (
            filename == logical
            or filename.endswith(
                "-" + logical
            )
        ):
            return logical

    return None


def _factory_candidate_files(
    root: Path,
) -> tuple[Path, ...]:
    files = []

    for path in root.iterdir():
        if path.is_file():
            files.append(path)

        elif path.is_dir():
            for child in path.iterdir():
                if child.is_file():
                    files.append(child)

    return tuple(files)


def _resolve_factory(
    root: Path,
) -> ResolvedInput:
    candidates = (
        _factory_candidate_files(
            root
        )
    )

    mapped = {}

    for path in candidates:
        logical = _logical_image_name(
            path.name
        )

        if logical is None:
            continue

        mapped.setdefault(
            logical,
            [],
        ).append(path)

    missing = []
    ambiguous = []

    for logical in FACTORY_IMAGE_SHA256:
        matches = mapped.get(
            logical,
            [],
        )

        if not matches:
            missing.append(logical)

        elif len(matches) != 1:
            ambiguous.append(logical)

    if missing:
        raise AndroidInstallError(
            "factory input is missing "
            "required images: "
            + ", ".join(
                sorted(missing)
            )
        )

    if ambiguous:
        raise AndroidInstallError(
            "factory input contains "
            "ambiguous image identities: "
            + ", ".join(
                sorted(ambiguous)
            )
        )

    images = []

    for logical, expected in (
        FACTORY_IMAGE_SHA256.items()
    ):
        path = mapped[
            logical
        ][0]

        size_bytes, actual = (
            _file_identity(path)
        )

        if actual != expected:
            raise AndroidInstallError(
                "factory image identity "
                f"mismatch for {logical}: "
                f"expected {expected}, "
                f"got {actual}"
            )

        partition = logical[
            :-len(".img")
        ]

        domain = (
            "fastbootd"
            if partition in {
                "system",
                "system_dlkm",
                "system_ext",
                "product",
                "vendor",
                "vendor_dlkm",
            }
            else "bootloader-fastboot"
        )

        images.append(
            AndroidInstallImage(
                partition=partition,
                image_name=logical,
                path=path.resolve(),
                size_bytes=size_bytes,
                sha256=actual,
                domain=domain,
                apply_vbmeta=(
                    partition
                    == "vbmeta"
                ),
                slot_other=False,
            )
        )

    bootloader_candidates = [
        path
        for path in candidates
        if (
            "bootloader-tangorpro-"
            + SUPPORTED_BOOTLOADER
            + ".img"
        )
        in path.name
    ]

    bootloader_path = None
    bootloader_sha = None

    if len(
        bootloader_candidates
    ) > 1:
        raise AndroidInstallError(
            "factory input contains "
            "multiple matching bootloader "
            "images"
        )

    if bootloader_candidates:
        bootloader_path = (
            bootloader_candidates[0]
            .resolve()
        )

        bootloader_sha = _sha256(
            bootloader_path
        )

        if (
            bootloader_sha
            != BOOTLOADER_IMAGE_SHA256
        ):
            raise AndroidInstallError(
                "factory bootloader image "
                "identity mismatch"
            )

    super_empty_matches = [
        candidate
        for candidate in candidates
        if (
            candidate.name
            == "super_empty.img"
            or candidate.name.endswith(
                "-super_empty.img"
            )
        )
    ]

    if not super_empty_matches:
        raise AndroidInstallError(
            "factory input is missing "
            "super_empty.img"
        )

    if len(super_empty_matches) != 1:
        raise AndroidInstallError(
            "factory input contains "
            "multiple super_empty.img "
            "candidates"
        )

    super_empty_path = (
        super_empty_matches[0]
        .resolve()
    )

    physical_order = (
        "boot",
        "init_boot",
        "dtbo",
        "vendor_kernel_boot",
        "pvmfw",
        "vendor_boot",
        "vbmeta",
        "vbmeta_system",
        "vbmeta_vendor",
    )

    dynamic_order = (
        "system",
        "system_dlkm",
        "system_ext",
        "product",
        "vendor",
        "vendor_dlkm",
    )

    image_by_partition = {
        image.partition: image
        for image in images
    }

    operations = [
        AndroidInstallOperation(
            line_number=0,
            raw="version 1",
            command="version",
            domain="metadata",
        )
    ]

    for partition in physical_order:
        image = image_by_partition[
            partition
        ]

        operations.append(
            AndroidInstallOperation(
                line_number=0,
                raw=(
                    "generated factory flash "
                    f"{partition}"
                ),
                command="flash",
                domain=(
                    "bootloader-fastboot"
                ),
                partition=partition,
                image_name=(
                    image.image_name
                ),
                apply_vbmeta=(
                    image.apply_vbmeta
                ),
                slot_other=False,
            )
        )

    operations.append(
        AndroidInstallOperation(
            line_number=0,
            raw="generated reboot fastboot",
            command="reboot-fastboot",
            domain="transition",
        )
    )

    operations.append(
        AndroidInstallOperation(
            line_number=0,
            raw="generated update-super",
            command="update-super",
            domain="fastbootd",
        )
    )

    for partition in dynamic_order:
        image = image_by_partition[
            partition
        ]

        operations.append(
            AndroidInstallOperation(
                line_number=0,
                raw=(
                    "generated factory flash "
                    f"{partition}"
                ),
                command="flash",
                domain="fastbootd",
                partition=partition,
                image_name=(
                    image.image_name
                ),
                apply_vbmeta=False,
                slot_other=False,
            )
        )

    requirements = (
        AndroidInstallRequirement(
            line_number=0,
            raw=(
                "generated factory "
                "require board=tangorpro"
            ),
            key="board",
            values=(
                SUPPORTED_PRODUCT,
            ),
        ),
    )

    build = AndroidInstallBuild(
        product_out=root,
        fastboot_info_path=None,
        android_info_path=None,
        target_device=(
            SUPPORTED_PRODUCT
        ),
        requirements=requirements,
        operations=tuple(
            operations
        ),
        images=tuple(images),
        uses_update_super=True,
        super_empty_path=(
            super_empty_path
        ),
        userdata_fs_type="f2fs",
    )

    return ResolvedInput(
        root=root,
        kind="factory",
        product=SUPPORTED_PRODUCT,
        android_release=(
            SUPPORTED_ANDROID_RELEASE
        ),
        sdk=SUPPORTED_SDK,
        baseline=(
            VALIDATED_FACTORY_BUILD
        ),
        bootloader=(
            SUPPORTED_BOOTLOADER
        ),
        images=tuple(images),
        build=build,
        factory_bootloader_image=(
            bootloader_path
        ),
        factory_bootloader_sha256=(
            bootloader_sha
        ),
    )


def resolve_input(
    input_path: Path,
) -> ResolvedInput:
    root = (
        input_path
        .expanduser()
        .resolve()
    )

    if not root.is_dir():
        raise AndroidInstallError(
            "input must be an extracted "
            f"directory: {root}"
        )

    if (
        (root / "fastboot-info.txt").is_file()
        and
        (root / "android-info.txt").is_file()
    ):
        return _resolve_aosp(root)

    return _resolve_factory(root)


__all__ = (
    "ResolvedInput",
    "resolve_input",
)
