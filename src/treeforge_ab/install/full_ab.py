from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

from treeforge_ab.providers.android_tools import AndroidHostTools
import re
import shlex
import struct
import subprocess

from treeforge_ab.install.models import (
    AndroidInstallBuild,
    AndroidInstallError,
    AndroidInstallImage,
)


FULL_AB_PROFILE = "tangorpro-treeforge-ab-v1"

STOCK_SUPER_SIZE_BYTES = 8_531_214_336

DYNAMIC_PARTITIONS = (
    "system",
    "system_dlkm",
    "system_ext",
    "product",
    "vendor",
    "vendor_dlkm",
)

TREEFORGE_GROUP_A = "treeforge_dynamic_partitions_a"
TREEFORGE_GROUP_B = "treeforge_dynamic_partitions_b"

BLOCK_SIZE = 4096

#
# Deliberately leave more room than the current LP metadata
# requires. Group maxima do not have to consume the entire
# physical super device.
#
SUPER_UNALLOCATED_RESERVE_BYTES = 8 * 1024 * 1024


@dataclass(
    frozen=True,
    slots=True,
)
class FullAbPartition:
    base_name: str
    image: AndroidInstallImage
    logical_size_bytes: int
    realized_size_bytes: int


@dataclass(
    frozen=True,
    slots=True,
)
class FullAbPlan:
    profile: str
    super_size_bytes: int

    metadata_size_bytes: int
    metadata_slots: int
    preserve_virtual_ab_flag: bool

    group_a: str
    group_b: str
    group_size_bytes: int

    slot_payload_bytes: int
    slot_headroom_bytes: int

    partitions: tuple[
        FullAbPartition,
        ...,
    ]

    super_empty_path: Path
    lpmake_path: Path
    lpdump_path: Path

    lpmake_command: tuple[str, ...]

    def to_dict(
        self,
    ) -> dict[str, object]:
        return {
            "schema_version": 1,
            "profile": self.profile,
            "super_size_bytes":
                self.super_size_bytes,
            "metadata_size_bytes":
                self.metadata_size_bytes,
            "metadata_slots":
                self.metadata_slots,
            "preserve_virtual_ab_flag":
                self.preserve_virtual_ab_flag,
            "groups": {
                "a": {
                    "name": self.group_a,
                    "size_bytes":
                        self.group_size_bytes,
                },
                "b": {
                    "name": self.group_b,
                    "size_bytes":
                        self.group_size_bytes,
                },
            },
            "slot_payload_bytes":
                self.slot_payload_bytes,
            "slot_headroom_bytes":
                self.slot_headroom_bytes,
            "partitions": [
                {
                    "base_name":
                        partition.base_name,
                    "image_name":
                        partition.image.image_name,
                    "image_path":
                        str(partition.image.path),
                    "image_file_size_bytes":
                        partition.image.size_bytes,
                    "logical_size_bytes":
                        partition.logical_size_bytes,
                    "realized_size_bytes":
                        partition.realized_size_bytes,
                    "a_name":
                        f"{partition.base_name}_a",
                    "b_name":
                        f"{partition.base_name}_b",
                }
                for partition in self.partitions
            ],
            "super_empty":
                str(self.super_empty_path),
            "lpmake":
                str(self.lpmake_path),
            "lpdump":
                str(self.lpdump_path),
            "lpmake_command":
                list(self.lpmake_command),
        }


def _align_up(
    value: int,
    alignment: int,
) -> int:
    return (
        (
            value
            + alignment
            - 1
        )
        // alignment
    ) * alignment


def _align_down(
    value: int,
    alignment: int,
) -> int:
    return (
        value
        // alignment
    ) * alignment




def _android_sparse_logical_size(
    path: Path,
) -> int:
    #
    # Android sparse image header:
    #
    # uint32 magic
    # uint16 major
    # uint16 minor
    # uint16 file_hdr_sz
    # uint16 chunk_hdr_sz
    # uint32 blk_sz
    # uint32 total_blks
    # uint32 total_chunks
    # uint32 image_checksum
    #
    with path.open(
        "rb",
    ) as handle:
        header = handle.read(
            28
        )

    if len(header) >= 28:
        (
            magic,
            _major,
            _minor,
            _file_header_size,
            _chunk_header_size,
            block_size,
            total_blocks,
            _total_chunks,
            _checksum,
        ) = struct.unpack(
            "<IHHHHIIII",
            header[:28],
        )

        if magic == 0xED26FF3A:
            if (
                block_size <= 0
                or total_blocks <= 0
            ):
                raise AndroidInstallError(
                    f"invalid sparse image geometry: {path}"
                )

            return (
                block_size
                * total_blocks
            )

    #
    # Raw image or normal sparse host file.
    # st_size is its logical byte length.
    #
    return path.stat().st_size


def _source_lp_metadata(
    *,
    build: AndroidInstallBuild,
    lpdump: Path,
) -> tuple[
    int,
    int,
    bool,
    str,
]:
    if (
        not build.uses_update_super
        or build.super_empty_path is None
    ):
        raise AndroidInstallError(
            "full-A/B projection requires a source "
            "super_empty.img"
        )

    completed = subprocess.run(
        (
            str(lpdump),
            str(build.super_empty_path),
        ),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )

    if completed.returncode != 0:
        raise AndroidInstallError(
            "unable to inspect source super_empty.img:\n"
            + completed.stdout
            + completed.stderr
        )

    text = (
        completed.stdout
        + "\n"
        + completed.stderr
    )

    metadata_size_match = re.search(
        r"(?m)^Metadata max size:\s*"
        r"([0-9]+)\s+bytes\s*$",
        text,
    )

    metadata_slots_match = re.search(
        r"(?m)^Metadata slot count:\s*"
        r"([0-9]+)\s*$",
        text,
    )

    header_flags_match = re.search(
        r"(?m)^Header flags:\s*(.*?)\s*$",
        text,
    )

    if (
        metadata_size_match is None
        or metadata_slots_match is None
        or header_flags_match is None
    ):
        raise AndroidInstallError(
            "unable to derive source LP metadata "
            "parameters from lpdump"
        )

    metadata_size = int(
        metadata_size_match.group(1),
        10,
    )

    metadata_slots = int(
        metadata_slots_match.group(1),
        10,
    )

    if (
        metadata_size <= 0
        or metadata_slots <= 0
    ):
        raise AndroidInstallError(
            "source LP metadata parameters are invalid"
        )

    header_flags = (
        header_flags_match.group(1)
        .strip()
    )

    virtual_ab = (
        "virtual_ab_device"
        in {
            value.strip()
            for value
            in header_flags.split(",")
        }
    )

    return (
        metadata_size,
        metadata_slots,
        virtual_ab,
        text,
    )


def _primary_dynamic_images(
    build: AndroidInstallBuild,
) -> tuple[
    FullAbPartition,
    ...,
]:
    primary: dict[
        str,
        AndroidInstallImage,
    ] = {}

    for image in build.images:
        if image.domain != "fastbootd":
            continue

        if image.slot_other:
            #
            # The TreeForge full-A/B policy intentionally
            # seeds both slots from the complete primary
            # known-good image set.
            #
            continue

        if image.partition not in DYNAMIC_PARTITIONS:
            raise AndroidInstallError(
                "full-A/B projection encountered an "
                "unexpected primary dynamic partition: "
                f"{image.partition}"
            )

        if image.partition in primary:
            raise AndroidInstallError(
                "full-A/B projection encountered duplicate "
                "primary dynamic image for "
                f"{image.partition}"
            )

        primary[
            image.partition
        ] = image

    missing = (
        set(DYNAMIC_PARTITIONS)
        - set(primary)
    )

    extra = (
        set(primary)
        - set(DYNAMIC_PARTITIONS)
    )

    if missing or extra:
        raise AndroidInstallError(
            "full-A/B dynamic image set mismatch: "
            f"missing={sorted(missing)}, "
            f"extra={sorted(extra)}"
        )

    result = []

    for name in DYNAMIC_PARTITIONS:
        image = primary[name]

        logical_size = (
            _android_sparse_logical_size(
                image.path
            )
        )

        realized_size = (
            _align_up(
                logical_size,
                BLOCK_SIZE,
            )
        )

        result.append(
            FullAbPartition(
                base_name=name,
                image=image,
                logical_size_bytes=logical_size,
                realized_size_bytes=realized_size,
            )
        )

    return tuple(
        result
    )


def _validate_generated_super(
    *,
    plan: FullAbPlan,
) -> str:
    completed = subprocess.run(
        (
            str(plan.lpdump_path),
            str(plan.super_empty_path),
        ),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )

    if completed.returncode != 0:
        raise AndroidInstallError(
            "lpdump rejected generated TreeForge "
            "full-A/B super metadata:\n"
            + completed.stdout
            + completed.stderr
        )

    text = (
        completed.stdout
        + "\n"
        + completed.stderr
    )

    required_names = {
        f"{name}_{slot}"
        for name in DYNAMIC_PARTITIONS
        for slot in (
            "a",
            "b",
        )
    }

    observed_names = set(
        re.findall(
            r"(?m)^\s*Name:\s*(\S+)\s*$",
            text,
        )
    )

    missing = (
        required_names
        - observed_names
    )

    if missing:
        raise AndroidInstallError(
            "generated super metadata is missing "
            "logical partitions: "
            + ", ".join(
                sorted(
                    missing
                )
            )
        )

    for group in (
        plan.group_a,
        plan.group_b,
    ):
        if group not in text:
            raise AndroidInstallError(
                "generated super metadata is missing "
                f"group {group}"
            )

    size_pattern = (
        r"(?m)^\s*Size:\s*"
        + re.escape(
            str(
                plan.super_size_bytes
            )
        )
        + r"\s+bytes\s*$"
    )

    if re.search(
        size_pattern,
        text,
    ) is None:
        raise AndroidInstallError(
            "generated super metadata does not report "
            "the requested physical super size"
        )

    metadata_size_pattern = (
        r"(?m)^Metadata max size:\s*"
        + re.escape(
            str(
                plan.metadata_size_bytes
            )
        )
        + r"\s+bytes\s*$"
    )

    if re.search(
        metadata_size_pattern,
        text,
    ) is None:
        raise AndroidInstallError(
            "generated super metadata changed metadata "
            "maximum size unexpectedly"
        )

    slots_pattern = (
        r"(?m)^Metadata slot count:\s*"
        + re.escape(
            str(
                plan.metadata_slots
            )
        )
        + r"\s*$"
    )

    if re.search(
        slots_pattern,
        text,
    ) is None:
        raise AndroidInstallError(
            "generated super metadata changed metadata "
            "slot count unexpectedly"
        )

    return text


def build_full_ab_plan(
    *,
    repository_root: Path,
    build: AndroidInstallBuild,
    super_size_bytes: int,
    output_root: Path,
) -> FullAbPlan:
    repository_root = (
        repository_root
        .expanduser()
        .resolve()
    )

    output_root = (
        output_root
        .expanduser()
        .resolve()
    )

    if build.target_device != "tangorpro":
        raise AndroidInstallError(
            "treeforge-full-ab-v1 currently supports "
            "only tangorpro"
        )

    if super_size_bytes <= STOCK_SUPER_SIZE_BYTES:
        raise AndroidInstallError(
            "treeforge-full-ab-v1 requires a super "
            "partition larger than canonical stock"
        )

    if (
        super_size_bytes
        % BLOCK_SIZE
        != 0
    ):
        raise AndroidInstallError(
            "full-A/B physical super size is not "
            "4096-byte aligned"
        )

    host_tools = AndroidHostTools()

    lpmake = Path(
        host_tools.lpmake
    ).resolve()

    lpdump = Path(
        host_tools.lpdump
    ).resolve()

    (
        metadata_size,
        metadata_slots,
        virtual_ab,
        _source_dump,
    ) = _source_lp_metadata(
        build=build,
        lpdump=lpdump,
    )

    partitions = (
        _primary_dynamic_images(
            build
        )
    )

    slot_payload = sum(
        partition.realized_size_bytes
        for partition in partitions
    )

    available_for_groups = (
        super_size_bytes
        - SUPER_UNALLOCATED_RESERVE_BYTES
    )

    if available_for_groups <= 0:
        raise AndroidInstallError(
            "physical super is too small after "
            "TreeForge safety reserve"
        )

    group_size = (
        _align_down(
            available_for_groups
            // 2,
            BLOCK_SIZE,
        )
    )

    if slot_payload > group_size:
        raise AndroidInstallError(
            "full-A/B slot payload does not fit its "
            "TreeForge group: "
            f"payload={slot_payload:,}, "
            f"group={group_size:,}"
        )

    slot_headroom = (
        group_size
        - slot_payload
    )

    output_root.mkdir(
        parents=True,
        exist_ok=True,
    )

    super_empty = (
        output_root
        / "super_empty-treeforge-full-ab-v1.img"
    )

    command: list[str] = [
        str(lpmake),
        "--metadata-size",
        str(metadata_size),
        "--metadata-slots",
        str(metadata_slots),
        "--device-size",
        str(super_size_bytes),
        "--super-name",
        "super",
        "--block-size",
        str(BLOCK_SIZE),
        "--group",
        f"{TREEFORGE_GROUP_A}:{group_size}",
        "--group",
        f"{TREEFORGE_GROUP_B}:{group_size}",
    ]

    for partition in partitions:
        command.extend(
            (
                "--partition",
                (
                    f"{partition.base_name}_a:"
                    f"readonly:"
                    f"{partition.realized_size_bytes}:"
                    f"{TREEFORGE_GROUP_A}"
                ),
            )
        )

        command.extend(
            (
                "--partition",
                (
                    f"{partition.base_name}_b:"
                    f"readonly:"
                    f"{partition.realized_size_bytes}:"
                    f"{TREEFORGE_GROUP_B}"
                ),
            )
        )

    if virtual_ab:
        #
        # Preserve the source userspace/device capability
        # flag for now. TreeForge's physical full-A/B
        # topology does not require snapshot updates, but
        # removing this compatibility flag is a separate
        # Android userspace policy change.
        #
        command.append(
            "--virtual-ab"
        )

    command.extend(
        (
            "--sparse",
            "--output",
            str(super_empty),
        )
    )

    completed = subprocess.run(
        tuple(command),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )

    if completed.returncode != 0:
        raise AndroidInstallError(
            "lpmake failed to create TreeForge "
            "full-A/B super metadata:\n"
            + completed.stdout
            + completed.stderr
        )

    if not super_empty.is_file():
        raise AndroidInstallError(
            "lpmake reported success but the TreeForge "
            "super_empty image does not exist"
        )

    plan = FullAbPlan(
        profile=FULL_AB_PROFILE,
        super_size_bytes=super_size_bytes,
        metadata_size_bytes=metadata_size,
        metadata_slots=metadata_slots,
        preserve_virtual_ab_flag=virtual_ab,
        group_a=TREEFORGE_GROUP_A,
        group_b=TREEFORGE_GROUP_B,
        group_size_bytes=group_size,
        slot_payload_bytes=slot_payload,
        slot_headroom_bytes=slot_headroom,
        partitions=partitions,
        super_empty_path=super_empty,
        lpmake_path=lpmake,
        lpdump_path=lpdump,
        lpmake_command=tuple(
            command
        ),
    )

    dump = (
        _validate_generated_super(
            plan=plan,
        )
    )

    (
        output_root
        / "full-ab-lpdump.txt"
    ).write_text(
        dump,
        encoding="utf-8",
    )

    (
        output_root
        / "full-ab-plan.json"
    ).write_text(
        json.dumps(
            plan.to_dict(),
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    return plan


STOCK_LAYOUT = "stock-virtual-ab"
TREEFORGE_LAYOUT = "treeforge-full-ab-v1"


def detect_install_layout(
    *,
    repository_root: Path,
    super_size_bytes: int,
) -> str:
    """
    Classify only exact layouts TreeForge understands.

    Stock tangorpro remains on the existing source-faithful
    Virtual A/B installer.

    A TreeForge-expanded super is accepted only when it
    matches TreeForge's persisted repartition state.
    """
    if super_size_bytes == STOCK_SUPER_SIZE_BYTES:
        return STOCK_LAYOUT

    state_path = (
        repository_root
        / "output"
        / "repartition"
        / "repartition-state.json"
    )

    if not state_path.is_file():
        raise AndroidInstallError(
            "non-stock tangorpro super geometry detected, "
            "but TreeForge repartition state is unavailable"
        )

    state = json.loads(
        state_path.read_text(
            encoding="utf-8"
        )
    )

    if state.get("profile") != FULL_AB_PROFILE:
        raise AndroidInstallError(
            "repartition state belongs to an unsupported "
            f"profile: {state.get('profile')!r}"
        )

    status = state.get(
        "status"
    )

    if status != "applied":
        raise AndroidInstallError(
            "TreeForge repartition state is not in an "
            "installable state: "
            f"{status!r}"
        )

    expected = state.get(
        "target_super_size_bytes"
    )

    if (
        not isinstance(
            expected,
            int,
        )
        or expected != super_size_bytes
    ):
        raise AndroidInstallError(
            "physical super size does not match the "
            "TreeForge repartition state: "
            f"device={super_size_bytes:,}, "
            f"state={expected!r}"
        )

    return TREEFORGE_LAYOUT


def validate_full_ab_preflight(
    *,
    build: AndroidInstallBuild,
    device,
    slot_applicability: dict[
        str,
        bool | None,
    ],
    plan: FullAbPlan,
) -> dict[str, object]:
    if (
        device.slot_count != 2
        or device.current_slot
        not in {
            "a",
            "b",
        }
    ):
        raise AndroidInstallError(
            "full-A/B install requires exactly "
            "two A/B slots"
        )

    snapshot_status = (
        device.snapshot_update_status
        or "none"
    ).strip().lower()

    if snapshot_status not in {
        "",
        "none",
    }:
        raise AndroidInstallError(
            "full-A/B install refuses an active "
            "snapshot/update state: "
            f"{device.snapshot_update_status}"
        )

    if plan.profile != FULL_AB_PROFILE:
        raise AndroidInstallError(
            "unexpected full-A/B profile"
        )

    update_super_count = sum(
        1
        for operation in build.operations
        if operation.command == "update-super"
    )

    if update_super_count != 1:
        raise AndroidInstallError(
            "full-A/B install requires exactly one "
            "source update-super transition; "
            f"found {update_super_count}"
        )

    primary_dynamic = {
        image.partition: image
        for image in build.images
        if (
            image.domain == "fastbootd"
            and not image.slot_other
        )
    }

    if (
        set(primary_dynamic)
        != set(DYNAMIC_PARTITIONS)
    ):
        raise AndroidInstallError(
            "full-A/B primary dynamic image set "
            "does not match the tangorpro profile: "
            f"{sorted(primary_dynamic)}"
        )

    for partition in DYNAMIC_PARTITIONS:
        applicable = (
            slot_applicability.get(
                partition
            )
        )

        if applicable is not True:
            raise AndroidInstallError(
                "full-A/B install requires dynamic "
                f"partition {partition!r} to be slotted; "
                f"fastboot reports {applicable!r}"
            )

    physical_primary = tuple(
        image
        for image in build.images
        if (
            image.domain
            == "bootloader-fastboot"
            and not image.slot_other
        )
    )

    mirrored_physical = tuple(
        image
        for image in physical_primary
        if slot_applicability.get(
            image.partition
        )
        is True
    )

    source_secondary_dynamic = tuple(
        image
        for image in build.images
        if (
            image.domain == "fastbootd"
            and image.slot_other
        )
    )

    return {
        "snapshot_state_safe": True,
        "layout":
            TREEFORGE_LAYOUT,
        "update_super_count":
            update_super_count,
        "dynamic_topology":
            "treeforge-full-ab-v1",
        "dynamic_primary_image_count":
            len(primary_dynamic),
        "source_secondary_dynamic_image_count":
            len(source_secondary_dynamic),
        "synthetic_secondary_dynamic_images":
            len(primary_dynamic),
        "full_dynamic_partition_count":
            len(primary_dynamic) * 2,
        "physical_primary_image_count":
            len(physical_primary),
        "mirrored_physical_image_count":
            len(mirrored_physical),
        "slot_payload_bytes":
            plan.slot_payload_bytes,
        "group_size_bytes":
            plan.group_size_bytes,
        "slot_headroom_bytes":
            plan.slot_headroom_bytes,
        "independent_other_dynamic_slot_bootability":
            "planned",
        "automatic_android_reboot":
            False,
    }


def render_full_ab_fastboot_info(
    *,
    build: AndroidInstallBuild,
    slot_applicability: dict[
        str,
        bool | None,
    ],
) -> str:
    """
    Project the source build into TreeForge full physical A/B.

    Physical boot-chain behavior remains the existing
    TreeForge mirror-A+B policy.

    Every complete primary dynamic image is flashed to both
    logical slots after applying TreeForge's full-A/B
    super_empty metadata.

    Source --slot-other dynamic images are intentionally
    superseded by the complete primary known-good image set.
    """
    lines = [
        "version 1",
        "# Generated by TreeForge Install.",
        "# Layout: treeforge-full-ab-v1.",
        "# Physical slotted boot-chain images: mirror A + B.",
        "# Dynamic logical partitions: complete A + complete B.",
        "# Both logical slots seeded from the complete primary image set.",
        "# Source secondary dynamic images are superseded.",
        "# Shared super metadata realized exactly once.",
        "# Userdata and metadata erases remain explicit CLI policy.",
        "# No final Android reboot.",
    ]

    update_super_count = 0

    def flash_line(
        operation,
        *,
        other: bool,
    ) -> str:
        assert (
            operation.partition
            is not None
        )
        assert (
            operation.image_name
            is not None
        )

        fields = [
            "flash",
        ]

        if other:
            fields.append(
                "--slot-other"
            )

        if operation.apply_vbmeta:
            fields.append(
                "--apply-vbmeta"
            )

        fields.extend(
            (
                operation.partition,
                operation.image_name,
            )
        )

        return " ".join(
            fields
        )

    for operation in build.operations:
        if operation.command == "version":
            continue

        if operation.command == "flash":
            assert (
                operation.partition
                is not None
            )

            applicable = (
                slot_applicability.get(
                    operation.partition
                )
            )

            if applicable is None:
                raise AndroidInstallError(
                    "cannot establish slot applicability "
                    f"for {operation.partition!r}"
                )

            #
            # Existing physical A/B policy remains intact.
            #
            if (
                operation.domain
                == "bootloader-fastboot"
            ):
                if operation.slot_other:
                    #
                    # The source already requested the
                    # secondary operation. Preserve it once.
                    #
                    lines.append(
                        flash_line(
                            operation,
                            other=True,
                        )
                    )
                    continue

                lines.append(
                    flash_line(
                        operation,
                        other=False,
                    )
                )

                if applicable is True:
                    lines.append(
                        flash_line(
                            operation,
                            other=True,
                        )
                    )

                continue

            if operation.domain == "fastbootd":
                #
                # Ignore incomplete source secondary
                # Virtual-A/B payloads. TreeForge seeds
                # both logical slots from each complete
                # primary image.
                #
                if operation.slot_other:
                    lines.append(
                        "# superseded source secondary: "
                        + operation.raw
                    )
                    continue

                if (
                    operation.partition
                    not in DYNAMIC_PARTITIONS
                ):
                    raise AndroidInstallError(
                        "unexpected dynamic partition in "
                        "full-A/B projection: "
                        f"{operation.partition}"
                    )

                if applicable is not True:
                    raise AndroidInstallError(
                        "full-A/B projection requires "
                        f"{operation.partition} to be slotted"
                    )

                lines.append(
                    flash_line(
                        operation,
                        other=False,
                    )
                )

                lines.append(
                    flash_line(
                        operation,
                        other=True,
                    )
                )

                continue

            raise AndroidInstallError(
                "flash operation appears in unsupported "
                f"domain {operation.domain!r}: "
                f"{operation.raw}"
            )

        if operation.command == "reboot-fastboot":
            lines.append(
                "reboot fastboot"
            )
            continue

        if operation.command == "reboot-bootloader":
            lines.append(
                "reboot bootloader"
            )
            continue

        if operation.command == "update-super":
            update_super_count += 1

            lines.append(
                "update-super"
            )
            continue

        if operation.conditional:
            lines.append(
                "# omitted destructive conditional: "
                + operation.raw
            )
            continue

        raise AndroidInstallError(
            "cannot project source install operation: "
            f"{operation.raw}"
        )

    if update_super_count != 1:
        raise AndroidInstallError(
            "full-A/B manifest expected exactly one "
            "update-super operation; "
            f"found {update_super_count}"
        )

    return (
        "\n".join(
            lines
        )
        + "\n"
    )


def build_full_ab_install_plan_payload(
    *,
    build: AndroidInstallBuild,
    device,
    selection,
    slot_applicability: dict[str, bool | None],
    preflight: dict[str, object],
    plan: FullAbPlan,
    manifest_path: Path,
) -> dict[str, object]:
    other_slot = (
        "b"
        if device.current_slot == "a"
        else "a"
    )

    return {
        "schema_version": 1,
        "execution_policy": {
            "mode": TREEFORGE_LAYOUT,
            "physical_slotted_images":
                "mirror-a-and-b",
            "dynamic_partition_layout":
                TREEFORGE_LAYOUT,
            "dynamic_slot_a":
                "complete",
            "dynamic_slot_b":
                "complete",
            "dynamic_primary_slot":
                device.current_slot,
            "dynamic_secondary_slot":
                other_slot,
            "shared_super_realizations": 1,
            "preserve_userdata":
                not selection.wipe_userdata,
            "preserve_metadata":
                not selection.wipe_userdata,
            "automatic_post_install_set_active":
                False,
            "automatic_android_reboot":
                False,
            "independent_other_dynamic_slot_bootability":
                "planned-and-populated",
        },
        "preflight": preflight,
        "full_ab": plan.to_dict(),
        "generated_fastboot_info":
            str(manifest_path),
        "build": {
            "product_out":
                str(build.product_out),
            "source_fastboot_info":
                str(build.fastboot_info_path),
            "android_info":
                str(build.android_info_path),
            "target_device":
                build.target_device,
            "userdata_fs_type":
                build.userdata_fs_type,
            "source_super_empty":
                (
                    str(build.super_empty_path)
                    if build.super_empty_path
                    is not None
                    else None
                ),
            "treeforge_super_empty":
                str(plan.super_empty_path),
        },
        "device": {
            "serial": device.serial,
            "initial_mode": device.initial_mode,
            "final_mode": device.final_mode,
            "product": device.product,
            "current_slot": device.current_slot,
            "slot_count": device.slot_count,
            "unlocked": device.unlocked,
            "secure": device.secure,
            "snapshot_update_status":
                device.snapshot_update_status,
        },
        "selection": {
            "slot_argument":
                selection.slot_argument,
            "display_name":
                selection.display_name,
            "final_active_slot":
                selection.final_active_slot,
            "wipe_userdata":
                selection.wipe_userdata,
        },
        "slot_applicability":
            slot_applicability,
    }


def render_full_ab_preflight_report(
    *,
    build: AndroidInstallBuild,
    device,
    selection,
    slot_applicability: dict[str, bool | None],
    preflight: dict[str, object],
    plan: FullAbPlan,
) -> str:
    lines = [
        "TreeForge Install Device Preflight",
        "",
        f"Layout: {TREEFORGE_LAYOUT}",
        f"Profile: {plan.profile}",
        f"Product output: {build.product_out}",
        f"Target device: {build.target_device}",
        f"Device serial: {device.serial}",
        f"Device product: {device.product}",
        f"Current slot: {device.current_slot}",
        f"Slot count: {device.slot_count}",
        f"Unlocked: {device.unlocked}",
        (
            "Snapshot update status: "
            f"{device.snapshot_update_status}"
        ),
        "",
        (
            "Physical super bytes: "
            f"{plan.super_size_bytes}"
        ),
        (
            "Group A: "
            f"{plan.group_a} "
            f"({plan.group_size_bytes} bytes)"
        ),
        (
            "Group B: "
            f"{plan.group_b} "
            f"({plan.group_size_bytes} bytes)"
        ),
        (
            "Per-slot payload: "
            f"{plan.slot_payload_bytes} bytes"
        ),
        (
            "Per-slot headroom: "
            f"{plan.slot_headroom_bytes} bytes"
        ),
        "",
        "Dynamic logical partitions:",
    ]

    for partition in plan.partitions:
        lines.append(
            "  "
            f"{partition.base_name}_a + "
            f"{partition.base_name}_b <- "
            f"{partition.image.image_name} "
            f"({partition.realized_size_bytes} bytes each)"
        )

    lines.extend(
        [
            "",
            "Projection validation:",
            "  Snapshot state safe: YES",
            "  update-super operations: 1",
            (
                "  Primary dynamic images: "
                f"{preflight['dynamic_primary_image_count']}"
            ),
            (
                "  Synthetic secondary dynamic images: "
                f"{preflight['synthetic_secondary_dynamic_images']}"
            ),
            (
                "  Full logical partition count: "
                f"{preflight['full_dynamic_partition_count']}"
            ),
            (
                "  Mirrored physical images: "
                f"{preflight['mirrored_physical_image_count']}"
            ),
            "",
            "Safety boundary:",
            "  Product identity checked: YES",
            "  Snapshot state checked: YES",
            "  Exact TreeForge super geometry checked: YES",
            "  TreeForge repartition state checked: YES",
            "  Source flash/image semantics checked: YES",
            "  Full A logical set planned: YES",
            "  Full B logical set planned: YES",
            "  No flash has occurred yet: YES",
            "  Explicit confirmation still required: YES",
            "  Automatic Android reboot: NO",
            "",
        ]
    )

    return (
        "\n".join(lines)
        + "\n"
    )


def validate_full_ab_manifest(
    *,
    build: AndroidInstallBuild,
    slot_applicability: dict[str, bool | None],
    fastboot_info_text: str,
) -> dict[str, object]:
    """
    Validate the generated TreeForge full-A/B transaction
    against the parsed source manifest.

    The generated transaction must preserve the complete
    primary image identity and per-operation flags exactly
    while duplicating eligible partitions across A and B.
    """

    required_physical = (
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

    source_primary = {}

    for operation in build.operations:
        if (
            operation.command != "flash"
            or operation.slot_other
        ):
            continue

        if (
            operation.partition is None
            or operation.image_name is None
        ):
            raise AndroidInstallError(
                "source primary flash operation is incomplete"
            )

        key = (
            operation.domain,
            operation.partition,
        )

        if key in source_primary:
            raise AndroidInstallError(
                "duplicate source primary flash operation: "
                f"{operation.domain}:"
                f"{operation.partition}"
            )

        source_primary[key] = operation


    for partition in DYNAMIC_PARTITIONS:
        if (
            "fastbootd",
            partition,
        ) not in source_primary:
            raise AndroidInstallError(
                "full-A/B source is missing primary "
                f"dynamic image for {partition}"
            )


    for partition in required_physical:
        if (
            "bootloader-fastboot",
            partition,
        ) not in source_primary:
            raise AndroidInstallError(
                "full-A/B source is missing required "
                f"physical image for {partition}"
            )


    domain = "bootloader-fastboot"
    generated_flashes = []
    update_super_count = 0

    for raw in fastboot_info_text.splitlines():
        line = raw.strip()

        if (
            not line
            or line.startswith("#")
        ):
            continue

        fields = shlex.split(
            line
        )

        if not fields:
            continue

        command = fields[0]

        if command == "version":
            if fields != [
                "version",
                "1",
            ]:
                raise AndroidInstallError(
                    "generated full-A/B manifest has "
                    f"unsupported version line: {line}"
                )

            continue


        if command == "reboot":
            if fields == [
                "reboot",
                "fastboot",
            ]:
                domain = "fastbootd"
                continue

            if fields == [
                "reboot",
                "bootloader",
            ]:
                domain = "bootloader-fastboot"
                continue

            raise AndroidInstallError(
                "generated full-A/B manifest contains "
                f"an unsafe reboot operation: {line}"
            )


        if command == "update-super":
            if (
                fields != [
                    "update-super",
                ]
                or domain != "fastbootd"
            ):
                raise AndroidInstallError(
                    "generated update-super is outside "
                    "the expected fastbootd transaction"
                )

            update_super_count += 1
            continue


        if command in {
            "erase",
            "format",
            "set_active",
            "set-active",
        }:
            raise AndroidInstallError(
                "generated full-A/B manifest contains "
                f"a forbidden operation: {line}"
            )


        if command != "flash":
            raise AndroidInstallError(
                "generated full-A/B manifest contains "
                f"an unsupported operation: {line}"
            )


        args = fields[1:]

        slot_other = (
            "--slot-other"
            in args
        )

        apply_vbmeta = (
            "--apply-vbmeta"
            in args
        )

        positional = [
            value
            for value in args
            if not value.startswith("--")
        ]

        if len(positional) != 2:
            raise AndroidInstallError(
                "generated full-A/B flash operation "
                f"is malformed: {line}"
            )

        partition, image_name = positional

        source = source_primary.get(
            (
                domain,
                partition,
            )
        )

        if source is None:
            raise AndroidInstallError(
                "generated full-A/B manifest introduced "
                "a flash not present in the complete "
                "source-primary transaction: "
                f"{domain}:{partition}"
            )

        if image_name != source.image_name:
            raise AndroidInstallError(
                "generated full-A/B manifest changed "
                f"image identity for {partition}: "
                f"{source.image_name!r} -> "
                f"{image_name!r}"
            )

        if apply_vbmeta != source.apply_vbmeta:
            raise AndroidInstallError(
                "generated full-A/B manifest changed "
                "--apply-vbmeta semantics for "
                f"{partition}: "
                f"source={source.apply_vbmeta}, "
                f"generated={apply_vbmeta}"
            )

        generated_flashes.append(
            {
                "domain": domain,
                "partition": partition,
                "image_name": image_name,
                "slot_other": slot_other,
                "apply_vbmeta": apply_vbmeta,
            }
        )


    if update_super_count != 1:
        raise AndroidInstallError(
            "full-A/B manifest must contain exactly "
            "one update-super operation; "
            f"found {update_super_count}"
        )


    def matching(
        *,
        domain_name: str,
        partition: str,
        other: bool,
    ) -> list[dict[str, object]]:
        return [
            operation
            for operation in generated_flashes
            if (
                operation["domain"] == domain_name
                and operation["partition"] == partition
                and operation["slot_other"] is other
            )
        ]


    for partition in DYNAMIC_PARTITIONS:
        applicable = (
            slot_applicability.get(
                partition
            )
        )

        if applicable is not True:
            raise AndroidInstallError(
                "full-A/B manifest validation requires "
                f"{partition} to be slotted"
            )

        primary = matching(
            domain_name="fastbootd",
            partition=partition,
            other=False,
        )

        secondary = matching(
            domain_name="fastbootd",
            partition=partition,
            other=True,
        )

        if (
            len(primary) != 1
            or len(secondary) != 1
        ):
            raise AndroidInstallError(
                "full-A/B manifest does not contain "
                "exactly one A and one B flash for "
                f"{partition}: primary={len(primary)}, "
                f"secondary={len(secondary)}"
            )


    for partition in required_physical:
        applicable = (
            slot_applicability.get(
                partition
            )
        )

        if applicable is not True:
            raise AndroidInstallError(
                "TreeForge full-A/B tangorpro profile "
                "requires physical boot-chain partition "
                f"{partition} to be slotted"
            )

        primary = matching(
            domain_name="bootloader-fastboot",
            partition=partition,
            other=False,
        )

        secondary = matching(
            domain_name="bootloader-fastboot",
            partition=partition,
            other=True,
        )

        if (
            len(primary) != 1
            or len(secondary) != 1
        ):
            raise AndroidInstallError(
                "full-A/B manifest does not contain "
                "exactly one A and one B physical flash "
                f"for {partition}: "
                f"primary={len(primary)}, "
                f"secondary={len(secondary)}"
            )


    source_secondary_dynamic = {
        (
            image.partition,
            image.image_name,
        )
        for image in build.images
        if (
            image.domain == "fastbootd"
            and image.slot_other
        )
    }

    for operation in generated_flashes:
        candidate = (
            operation["partition"],
            operation["image_name"],
        )

        if (
            candidate in source_secondary_dynamic
            and source_primary[
                (
                    operation["domain"],
                    operation["partition"],
                )
            ].image_name
            != operation["image_name"]
        ):
            raise AndroidInstallError(
                "source Virtual-A/B secondary payload "
                "remains executable in TreeForge "
                "full-A/B transaction: "
                f"{operation['image_name']}"
            )


    return {
        "validated": True,
        "generated_flash_count":
            len(generated_flashes),
        "dynamic_a_flashes":
            len(DYNAMIC_PARTITIONS),
        "dynamic_b_flashes":
            len(DYNAMIC_PARTITIONS),
        "physical_a_flashes":
            len(required_physical),
        "physical_b_flashes":
            len(required_physical),
        "update_super_count": 1,
        "source_image_identity_preserved":
            True,
        "source_apply_vbmeta_semantics_preserved":
            True,
        "source_secondary_dynamic_payloads_executed":
            False,
        "automatic_final_reboot":
            False,
        "forbidden_destructive_commands":
            False,
    }
