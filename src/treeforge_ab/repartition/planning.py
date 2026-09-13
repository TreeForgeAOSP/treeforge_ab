from __future__ import annotations

from dataclasses import asdict, dataclass
import re


class RepartitionError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class PartitionGeometry:
    name: str
    device: str
    number: int
    start_sector: int
    size_sectors: int

    @property
    def end_sector(self) -> int:
        return (
            self.start_sector
            + self.size_sectors
        )


@dataclass(frozen=True, slots=True)
class TangorproRepartitionProfile:
    name: str
    product: str
    disk: str
    sector_size_bytes: int
    alignment_bytes: int
    super: PartitionGeometry
    userdata: PartitionGeometry


@dataclass(frozen=True, slots=True)
class RepartitionPlan:
    direction: str

    profile: str
    product: str
    disk: str

    sector_size_bytes: int
    alignment_bytes: int

    requested_super_size_bytes: int
    realized_super_size_bytes: int
    realized_super_size_sectors: int

    current_super_start_sector: int
    current_super_size_sectors: int
    current_super_end_sector: int

    new_super_start_sector: int
    new_super_end_sector: int

    current_userdata_start_sector: int
    current_userdata_size_sectors: int
    current_userdata_end_sector: int

    new_userdata_start_sector: int
    new_userdata_size_sectors: int
    new_userdata_end_sector: int

    super_delta_bytes: int
    userdata_delta_bytes: int

    action: str
    userdata_action: str

    destructive: bool
    device_modified: bool

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


TANGORPRO_PROFILE = TangorproRepartitionProfile(
    name="tangorpro-treeforge-ab-v1",
    product="tangorpro",
    disk="/dev/block/sda",
    sector_size_bytes=512,
    alignment_bytes=1024 * 1024,
    super=PartitionGeometry(
        name="super",
        device="/dev/block/sda25",
        number=25,
        start_sector=1_191_728,
        size_sectors=16_662_528,
    ),
    userdata=PartitionGeometry(
        name="userdata",
        device="/dev/block/sda26",
        number=26,
        start_sector=17_854_256,
        size_sectors=231_714_984,
    ),
)


_SIZE_RE = re.compile(
    r"^\s*"
    r"(?P<value>\d+(?:\.\d+)?)"
    r"\s*"
    r"(?P<unit>B|KB|MB|GB|TB|KiB|MiB|GiB|TiB)"
    r"\s*$",
    re.IGNORECASE,
)


_UNIT_MULTIPLIERS = {
    "b": 1,
    "kb": 1_000,
    "mb": 1_000_000,
    "gb": 1_000_000_000,
    "tb": 1_000_000_000_000,
    "kib": 1024,
    "mib": 1024 ** 2,
    "gib": 1024 ** 3,
    "tib": 1024 ** 4,
}


def parse_size_bytes(
    value: str,
) -> int:
    match = _SIZE_RE.fullmatch(
        value
    )

    if match is None:
        raise RepartitionError(
            "Invalid size. Use a value such as "
            "'20GB', '12GB', or '10GiB'."
        )

    number_text = match.group(
        "value"
    )

    unit = match.group(
        "unit"
    ).lower()

    whole, dot, fraction = (
        number_text.partition(".")
    )

    numerator = int(
        whole
    )

    denominator = 1

    if dot:
        denominator = (
            10 ** len(fraction)
        )

        numerator = (
            numerator * denominator
            + int(fraction)
        )

    multiplier = (
        _UNIT_MULTIPLIERS[
            unit
        ]
    )

    result = (
        numerator * multiplier
        + denominator - 1
    ) // denominator

    if result <= 0:
        raise RepartitionError(
            "Requested super size must "
            "be greater than zero."
        )

    return result


def _align_up(
    value: int,
    alignment: int,
) -> int:
    return (
        (value + alignment - 1)
        // alignment
        * alignment
    )


def _profile_invariants(
    profile: TangorproRepartitionProfile,
) -> None:
    if (
        profile.alignment_bytes
        % profile.sector_size_bytes
        != 0
    ):
        raise RepartitionError(
            "Profile alignment is not "
            "sector aligned."
        )

    if (
        profile.super.end_sector
        != profile.userdata.start_sector
    ):
        raise RepartitionError(
            "Tangorpro profile invariant failed: "
            "super and userdata are not adjacent."
        )


def _treeforge_geometry(
    *,
    requested_super_size: str,
    profile: TangorproRepartitionProfile,
) -> tuple[
    int,
    int,
    int,
    int,
]:
    _profile_invariants(
        profile
    )

    requested_bytes = (
        parse_size_bytes(
            requested_super_size
        )
    )

    realized_bytes = _align_up(
        requested_bytes,
        profile.alignment_bytes,
    )

    if (
        realized_bytes
        % profile.sector_size_bytes
        != 0
    ):
        raise RepartitionError(
            "Realized super size is not "
            "sector aligned."
        )

    realized_sectors = (
        realized_bytes
        // profile.sector_size_bytes
    )

    new_super_end = (
        profile.super.start_sector
        + realized_sectors
    )

    new_userdata_size = (
        profile.userdata.end_sector
        - new_super_end
    )

    minimum_userdata_sectors = (
        profile.alignment_bytes
        // profile.sector_size_bytes
    )

    if (
        new_userdata_size
        < minimum_userdata_sectors
    ):
        raise RepartitionError(
            "Requested super size would leave "
            "less than one alignment unit "
            "for userdata."
        )

    return (
        requested_bytes,
        realized_bytes,
        realized_sectors,
        new_userdata_size,
    )


def build_repartition_plan_from_geometry(
    *,
    current_super_size_bytes: int,
    current_userdata_size_bytes: int,
    requested_super_size: str = "20GB",
    profile: TangorproRepartitionProfile = TANGORPRO_PROFILE,
) -> RepartitionPlan:
    """
    Build a forward repartition plan from an exact,
    already-admitted current tangorpro geometry.

    Device identity and recognition of supported starting
    layouts remain responsibilities of the device admission
    layer.
    """

    sector_size = (
        profile.sector_size_bytes
    )

    for name, value in (
        (
            "current_super_size_bytes",
            current_super_size_bytes,
        ),
        (
            "current_userdata_size_bytes",
            current_userdata_size_bytes,
        ),
    ):
        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or value <= 0
        ):
            raise RepartitionError(
                f"{name} must be a positive integer"
            )

        if value % sector_size != 0:
            raise RepartitionError(
                f"{name} is not aligned to "
                f"{sector_size}-byte sectors"
            )

    current_super_size_sectors = (
        current_super_size_bytes
        // sector_size
    )

    current_userdata_size_sectors = (
        current_userdata_size_bytes
        // sector_size
    )

    current_super_start = (
        profile.super.start_sector
    )

    current_super_end = (
        current_super_start
        + current_super_size_sectors
    )

    current_userdata_start = (
        current_super_end
    )

    current_userdata_end = (
        current_userdata_start
        + current_userdata_size_sectors
    )

    expected_userdata_end = (
        profile.userdata.end_sector
    )

    if (
        current_userdata_end
        != expected_userdata_end
    ):
        raise RepartitionError(
            "Current super/userdata geometry does not "
            "preserve the canonical tangorpro storage "
            "boundary: "
            f"observed end={current_userdata_end:,}, "
            f"expected end={expected_userdata_end:,}"
        )

    (
        requested_bytes,
        realized_bytes,
        realized_sectors,
        new_userdata_size_sectors,
    ) = _treeforge_geometry(
        requested_super_size=(
            requested_super_size
        ),
        profile=profile,
    )

    if (
        realized_bytes
        < current_super_size_bytes
    ):
        raise RepartitionError(
            "Forward repartition will not shrink super: "
            f"current={current_super_size_bytes:,}, "
            f"target={realized_bytes:,}"
        )

    new_super_start = (
        current_super_start
    )

    new_super_end = (
        new_super_start
        + realized_sectors
    )

    new_userdata_start = (
        new_super_end
    )

    new_userdata_end = (
        expected_userdata_end
    )

    new_userdata_size_bytes = (
        new_userdata_size_sectors
        * sector_size
    )

    super_delta = (
        realized_bytes
        - current_super_size_bytes
    )

    userdata_delta = (
        new_userdata_size_bytes
        - current_userdata_size_bytes
    )

    if (
        super_delta
        != -userdata_delta
    ):
        raise RepartitionError(
            "Forward repartition does not conserve "
            "the super/userdata storage boundary"
        )

    if super_delta > 0:
        action = "GROW"

    elif super_delta == 0:
        action = "UNCHANGED"

    else:
        raise RepartitionError(
            "Forward repartition unexpectedly "
            "requested a super shrink"
        )

    return RepartitionPlan(
        direction="forward",
        profile=profile.name,
        product=profile.product,
        disk=profile.disk,
        sector_size_bytes=(
            sector_size
        ),
        alignment_bytes=(
            profile.alignment_bytes
        ),
        requested_super_size_bytes=(
            requested_bytes
        ),
        realized_super_size_bytes=(
            realized_bytes
        ),
        realized_super_size_sectors=(
            realized_sectors
        ),
        current_super_start_sector=(
            current_super_start
        ),
        current_super_size_sectors=(
            current_super_size_sectors
        ),
        current_super_end_sector=(
            current_super_end
        ),
        new_super_start_sector=(
            new_super_start
        ),
        new_super_end_sector=(
            new_super_end
        ),
        current_userdata_start_sector=(
            current_userdata_start
        ),
        current_userdata_size_sectors=(
            current_userdata_size_sectors
        ),
        current_userdata_end_sector=(
            current_userdata_end
        ),
        new_userdata_start_sector=(
            new_userdata_start
        ),
        new_userdata_size_sectors=(
            new_userdata_size_sectors
        ),
        new_userdata_end_sector=(
            new_userdata_end
        ),
        super_delta_bytes=(
            super_delta
        ),
        userdata_delta_bytes=(
            userdata_delta
        ),
        action=action,
        userdata_action=(
            "RECREATE"
            if action == "GROW"
            else "UNCHANGED"
        ),
        destructive=(
            action == "GROW"
        ),
        device_modified=False,
    )


def build_repartition_plan(
    *,
    requested_super_size: str = "20GB",
    profile: TangorproRepartitionProfile = TANGORPRO_PROFILE,
) -> RepartitionPlan:
    """
    Build the canonical offline plan from stock geometry.
    """

    current_super_size_bytes = (
        profile.super.size_sectors
        * profile.sector_size_bytes
    )

    current_userdata_size_bytes = (
        profile.userdata.size_sectors
        * profile.sector_size_bytes
    )

    return build_repartition_plan_from_geometry(
        current_super_size_bytes=(
            current_super_size_bytes
        ),
        current_userdata_size_bytes=(
            current_userdata_size_bytes
        ),
        requested_super_size=(
            requested_super_size
        ),
        profile=profile,
    )


def build_undo_plan(
    *,
    from_super_size: str = "20GB",
    profile: TangorproRepartitionProfile = TANGORPRO_PROFILE,
) -> RepartitionPlan:
    (
        requested_bytes,
        realized_bytes,
        realized_sectors,
        treeforge_userdata_size,
    ) = _treeforge_geometry(
        requested_super_size=from_super_size,
        profile=profile,
    )

    sector_size = (
        profile.sector_size_bytes
    )

    treeforge_super_end = (
        profile.super.start_sector
        + realized_sectors
    )

    stock_super_bytes = (
        profile.super.size_sectors
        * sector_size
    )

    treeforge_userdata_bytes = (
        treeforge_userdata_size
        * sector_size
    )

    stock_userdata_bytes = (
        profile.userdata.size_sectors
        * sector_size
    )

    return RepartitionPlan(
        direction="undo",
        profile=profile.name,
        product=profile.product,
        disk=profile.disk,
        sector_size_bytes=sector_size,
        alignment_bytes=profile.alignment_bytes,
        requested_super_size_bytes=requested_bytes,
        realized_super_size_bytes=stock_super_bytes,
        realized_super_size_sectors=profile.super.size_sectors,
        current_super_start_sector=profile.super.start_sector,
        current_super_size_sectors=realized_sectors,
        current_super_end_sector=treeforge_super_end,
        new_super_start_sector=profile.super.start_sector,
        new_super_end_sector=profile.super.end_sector,
        current_userdata_start_sector=treeforge_super_end,
        current_userdata_size_sectors=treeforge_userdata_size,
        current_userdata_end_sector=profile.userdata.end_sector,
        new_userdata_start_sector=profile.userdata.start_sector,
        new_userdata_size_sectors=profile.userdata.size_sectors,
        new_userdata_end_sector=profile.userdata.end_sector,
        super_delta_bytes=(
            stock_super_bytes
            - realized_bytes
        ),
        userdata_delta_bytes=(
            stock_userdata_bytes
            - treeforge_userdata_bytes
        ),
        action="SHRINK",
        userdata_action="RECREATE",
        destructive=True,
        device_modified=False,
    )

def build_repartition_undo_plan_from_geometry(
    *,
    current_super_size_bytes: int,
    current_userdata_size_bytes: int,
    target_super_size_bytes: int,
    target_userdata_size_bytes: int,
    profile: TangorproRepartitionProfile = TANGORPRO_PROFILE,
) -> RepartitionPlan:
    """
    Build an undo plan between two exact, previously
    validated tangorpro geometries.

    Undo may only shrink super and return exactly the same
    number of bytes to userdata.
    """

    sector_size = (
        profile.sector_size_bytes
    )

    values = (
        (
            "current_super_size_bytes",
            current_super_size_bytes,
        ),
        (
            "current_userdata_size_bytes",
            current_userdata_size_bytes,
        ),
        (
            "target_super_size_bytes",
            target_super_size_bytes,
        ),
        (
            "target_userdata_size_bytes",
            target_userdata_size_bytes,
        ),
    )

    for name, value in values:
        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or value <= 0
        ):
            raise RepartitionError(
                f"{name} must be a positive integer"
            )

        if value % sector_size != 0:
            raise RepartitionError(
                f"{name} is not aligned to "
                f"{sector_size}-byte sectors"
            )

    for name, value in (
        (
            "current_super_size_bytes",
            current_super_size_bytes,
        ),
        (
            "target_super_size_bytes",
            target_super_size_bytes,
        ),
    ):
        if (
            value
            % profile.alignment_bytes
            != 0
        ):
            raise RepartitionError(
                f"{name} is not aligned to the "
                f"{profile.alignment_bytes:,}-byte "
                "tangorpro profile alignment"
            )

    current_super_sectors = (
        current_super_size_bytes
        // sector_size
    )

    current_userdata_sectors = (
        current_userdata_size_bytes
        // sector_size
    )

    target_super_sectors = (
        target_super_size_bytes
        // sector_size
    )

    target_userdata_sectors = (
        target_userdata_size_bytes
        // sector_size
    )

    super_start = (
        profile.super.start_sector
    )

    current_super_end = (
        super_start
        + current_super_sectors
    )

    current_userdata_start = (
        current_super_end
    )

    current_userdata_end = (
        current_userdata_start
        + current_userdata_sectors
    )

    target_super_end = (
        super_start
        + target_super_sectors
    )

    target_userdata_start = (
        target_super_end
    )

    target_userdata_end = (
        target_userdata_start
        + target_userdata_sectors
    )

    canonical_end = (
        profile.userdata.end_sector
    )

    if (
        current_userdata_end
        != canonical_end
    ):
        raise RepartitionError(
            "Current undo geometry does not preserve "
            "the canonical tangorpro disk boundary: "
            f"{current_userdata_end:,} != "
            f"{canonical_end:,}"
        )

    if (
        target_userdata_end
        != canonical_end
    ):
        raise RepartitionError(
            "Undo target geometry does not preserve "
            "the canonical tangorpro disk boundary: "
            f"{target_userdata_end:,} != "
            f"{canonical_end:,}"
        )

    if (
        target_super_size_bytes
        >= current_super_size_bytes
    ):
        raise RepartitionError(
            "Undo must restore a smaller immediate "
            "predecessor super geometry."
        )

    if (
        target_userdata_size_bytes
        <= current_userdata_size_bytes
    ):
        raise RepartitionError(
            "Undo must return the released super "
            "capacity to userdata."
        )

    super_delta = (
        target_super_size_bytes
        - current_super_size_bytes
    )

    userdata_delta = (
        target_userdata_size_bytes
        - current_userdata_size_bytes
    )

    if super_delta != -userdata_delta:
        raise RepartitionError(
            "Undo does not conserve the physical "
            "super/userdata boundary."
        )

    return RepartitionPlan(
        direction="undo",
        profile=profile.name,
        product=profile.product,
        disk=profile.disk,

        sector_size_bytes=(
            sector_size
        ),

        alignment_bytes=(
            profile.alignment_bytes
        ),

        requested_super_size_bytes=(
            target_super_size_bytes
        ),

        realized_super_size_bytes=(
            target_super_size_bytes
        ),

        realized_super_size_sectors=(
            target_super_sectors
        ),

        current_super_start_sector=(
            super_start
        ),

        current_super_size_sectors=(
            current_super_sectors
        ),

        current_super_end_sector=(
            current_super_end
        ),

        new_super_start_sector=(
            super_start
        ),

        new_super_end_sector=(
            target_super_end
        ),

        current_userdata_start_sector=(
            current_userdata_start
        ),

        current_userdata_size_sectors=(
            current_userdata_sectors
        ),

        current_userdata_end_sector=(
            current_userdata_end
        ),

        new_userdata_start_sector=(
            target_userdata_start
        ),

        new_userdata_size_sectors=(
            target_userdata_sectors
        ),

        new_userdata_end_sector=(
            target_userdata_end
        ),

        super_delta_bytes=(
            super_delta
        ),

        userdata_delta_bytes=(
            userdata_delta
        ),

        action="SHRINK",
        userdata_action="RECREATE",
        destructive=True,
        device_modified=False,
    )

