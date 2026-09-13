from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


class AndroidInstallError(RuntimeError):
    pass


@dataclass(
    frozen=True,
    slots=True,
)
class AndroidInstallOperation:
    line_number: int
    raw: str
    command: str
    domain: str

    partition: str | None = None
    image_name: str | None = None

    apply_vbmeta: bool = False
    slot_other: bool = False
    conditional: bool = False


@dataclass(
    frozen=True,
    slots=True,
)
class AndroidInstallRequirement:
    line_number: int
    raw: str
    key: str
    values: tuple[str, ...]


@dataclass(
    frozen=True,
    slots=True,
)
class AndroidInstallImage:
    partition: str
    image_name: str
    path: Path

    size_bytes: int
    sha256: str

    domain: str
    apply_vbmeta: bool
    slot_other: bool


@dataclass(
    frozen=True,
    slots=True,
)
class AndroidInstallBuild:
    product_out: Path
    fastboot_info_path: Path | None
    android_info_path: Path | None

    target_device: str

    requirements: tuple[
        AndroidInstallRequirement,
        ...,
    ]

    operations: tuple[
        AndroidInstallOperation,
        ...,
    ]

    images: tuple[
        AndroidInstallImage,
        ...,
    ]

    uses_update_super: bool
    super_empty_path: Path | None

    userdata_fs_type: str | None
