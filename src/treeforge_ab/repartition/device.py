from __future__ import annotations

from dataclasses import asdict, dataclass
import subprocess

from treeforge_ab.providers.android_tools import (
    treeforge_adb_executable,
    treeforge_fastboot_executable,
)

from treeforge_ab.repartition.planning import (
    RepartitionError,
    RepartitionPlan,
    TangorproRepartitionProfile,
    TANGORPRO_PROFILE,
    build_repartition_plan_from_geometry,
)


SUPPORTED_BOOTLOADER = "tangorpro-15.2-13237001"


@dataclass(frozen=True, slots=True)
class RepartitionDevice:
    product: str
    bootloader: str
    current_slot: str
    slot_count: int
    unlocked: str
    secure: str
    is_userspace: str
    snapshot_update_status: str
    super_size_bytes: int
    userdata_size_bytes: int

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class TangorproRepartitionDeviceController:
    def __init__(self) -> None:
        self.fastboot = str(
            treeforge_fastboot_executable()
        )
        self.adb = str(
            treeforge_adb_executable()
        )

    def _fastboot_present(
        self,
    ) -> bool:
        try:
            completed = subprocess.run(
                [
                    self.fastboot,
                    "devices",
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=5,
            )

        except subprocess.TimeoutExpired:
            return False

        return bool(
            completed.stdout.strip()
        )

    def _adb_present(
        self,
    ) -> bool:
        try:
            completed = subprocess.run(
                [
                    self.adb,
                    "get-state",
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=5,
            )

        except subprocess.TimeoutExpired:
            return False

        return (
            completed.returncode == 0
            and completed.stdout.strip()
            == "device"
        )

    def _wait_for_fastboot(
        self,
    ) -> None:
        for _ in range(60):
            if self._fastboot_present():
                return

            import time
            time.sleep(1)

        raise RepartitionError(
            "Device did not enter fastboot."
        )

    def _fastboot_userspace(
        self,
    ) -> bool | None:
        if not self._fastboot_present():
            return None

        try:
            completed = subprocess.run(
                [
                    self.fastboot,
                    "getvar",
                    "is-userspace",
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=5,
            )

        except subprocess.TimeoutExpired:
            return None

        combined = (
            completed.stdout
            + "\n"
            + completed.stderr
        )

        for raw in combined.splitlines():
            line = raw.strip()

            for prefix in (
                "is-userspace:",
                "(bootloader) is-userspace:",
            ):
                if line.startswith(prefix):
                    return (
                        line[
                            len(prefix):
                        ]
                        .strip()
                        == "yes"
                    )

        return None

    def ensure_bootloader(
        self,
    ) -> None:
        if self._fastboot_present():
            userspace = (
                self._fastboot_userspace()
            )

            if userspace is not True:
                return

            completed = subprocess.run(
                [
                    self.fastboot,
                    "reboot",
                    "bootloader",
                ],
                check=False,
                timeout=15,
            )

            if completed.returncode != 0:
                raise RepartitionError(
                    "Unable to leave fastbootd "
                    "for bootloader fastboot."
                )

            self._wait_for_fastboot()
            return

        if self._adb_present():
            completed = subprocess.run(
                [
                    self.adb,
                    "reboot",
                    "bootloader",
                ],
                check=False,
                timeout=15,
            )

            if completed.returncode != 0:
                raise RepartitionError(
                    "Unable to reboot Android "
                    "to bootloader fastboot."
                )

            self._wait_for_fastboot()
            return

        raise RepartitionError(
            "No tangorpro transport detected. "
            "Connect the tablet with either ADB, "
            "bootloader fastboot, or fastbootd."
        )

    def _getvar(
        self,
        key: str,
    ) -> str:
        completed = subprocess.run(
            [
                self.fastboot,
                "getvar",
                key,
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=15,
        )

        combined = "\n".join(
            part
            for part in (
                completed.stdout,
                completed.stderr,
            )
            if part
        )

        if completed.returncode != 0:
            raise RepartitionError(
                f"fastboot getvar {key!r} failed: "
                f"{combined.strip() or '<no output>'}"
            )

        prefixes = (
            f"{key}:",
            f"(bootloader) {key}:",
        )

        for raw_line in combined.splitlines():
            line = raw_line.strip()

            for prefix in prefixes:
                if line.startswith(prefix):
                    value = (
                        line[len(prefix):]
                        .strip()
                    )

                    if value:
                        return value

        raise RepartitionError(
            f"fastboot did not report {key!r}"
        )

    @staticmethod
    def _parse_size(
        value: str,
        *,
        key: str,
    ) -> int:
        try:
            return int(
                value,
                0,
            )

        except ValueError as exc:
            raise RepartitionError(
                f"Invalid {key} value from fastboot: "
                f"{value!r}"
            ) from exc

    def probe_read_only(
        self,
    ) -> RepartitionDevice:
        """
        Inspect a device already in bootloader fastboot.

        This path never reboots the device and never changes
        transport. It is intended for standalone admission
        checks before any destructive operation is offered.
        """

        if not self._fastboot_present():
            raise RepartitionError(
                "No fastboot device detected. "
                "treeforge-ab inspect is read-only; "
                "manually place the tablet in bootloader "
                "fastboot and retry."
            )

        userspace = self._fastboot_userspace()

        if userspace is None:
            raise RepartitionError(
                "Unable to determine whether the connected "
                "fastboot device is bootloader fastboot "
                "or fastbootd."
            )

        if userspace:
            raise RepartitionError(
                "fastbootd detected. treeforge-ab inspect "
                "does not change transport; manually reboot "
                "to bootloader fastboot and retry."
            )

        product = self._getvar(
            "product"
        )

        bootloader = self._getvar(
            "version-bootloader"
        )

        current_slot = self._getvar(
            "current-slot"
        )

        try:
            slot_count = int(
                self._getvar(
                    "slot-count"
                ),
                10,
            )

        except ValueError as exc:
            raise RepartitionError(
                "Invalid slot-count reported by fastboot."
            ) from exc

        unlocked = self._getvar(
            "unlocked"
        )

        secure = self._getvar(
            "secure"
        )

        is_userspace = self._getvar(
            "is-userspace"
        )

        snapshot_update_status = self._getvar(
            "snapshot-update-status"
        )

        super_size_bytes = self._parse_size(
            self._getvar(
                "partition-size:super"
            ),
            key="partition-size:super",
        )

        userdata_size_bytes = self._parse_size(
            self._getvar(
                "partition-size:userdata"
            ),
            key="partition-size:userdata",
        )

        return RepartitionDevice(
            product=product,
            bootloader=bootloader,
            current_slot=current_slot,
            slot_count=slot_count,
            unlocked=unlocked,
            secure=secure,
            is_userspace=is_userspace,
            snapshot_update_status=(
                snapshot_update_status
            ),
            super_size_bytes=(
                super_size_bytes
            ),
            userdata_size_bytes=(
                userdata_size_bytes
            ),
        )

    def probe(self) -> RepartitionDevice:
        self.ensure_bootloader()

        product = self._getvar(
            "product"
        )

        bootloader = self._getvar(
            "version-bootloader"
        )

        current_slot = self._getvar(
            "current-slot"
        )

        try:
            slot_count = int(
                self._getvar(
                    "slot-count"
                ),
                10,
            )

        except ValueError as exc:
            raise RepartitionError(
                "Invalid slot-count reported by fastboot."
            ) from exc

        unlocked = self._getvar(
            "unlocked"
        )

        secure = self._getvar(
            "secure"
        )

        is_userspace = self._getvar(
            "is-userspace"
        )

        snapshot_update_status = self._getvar(
            "snapshot-update-status"
        )

        super_size_bytes = self._parse_size(
            self._getvar(
                "partition-size:super"
            ),
            key="partition-size:super",
        )

        userdata_size_bytes = self._parse_size(
            self._getvar(
                "partition-size:userdata"
            ),
            key="partition-size:userdata",
        )

        return RepartitionDevice(
            product=product,
            bootloader=bootloader,
            current_slot=current_slot,
            slot_count=slot_count,
            unlocked=unlocked,
            secure=secure,
            is_userspace=is_userspace,
            snapshot_update_status=snapshot_update_status,
            super_size_bytes=super_size_bytes,
            userdata_size_bytes=userdata_size_bytes,
        )

    @staticmethod
    def _validate_identity(
        device: RepartitionDevice,
        *,
        profile: TangorproRepartitionProfile,
    ) -> None:
        failures: list[str] = []

        if device.product != profile.product:
            failures.append(
                "product="
                f"{device.product!r}; "
                f"expected {profile.product!r}"
            )

        if device.bootloader != SUPPORTED_BOOTLOADER:
            failures.append(
                "bootloader="
                f"{device.bootloader!r}; "
                f"expected {SUPPORTED_BOOTLOADER!r}"
            )

        if device.current_slot not in {
            "a",
            "b",
        }:
            failures.append(
                "current-slot="
                f"{device.current_slot!r}; "
                "expected 'a' or 'b'"
            )

        if device.slot_count != 2:
            failures.append(
                "slot-count="
                f"{device.slot_count!r}; "
                "expected 2"
            )

        if device.unlocked != "yes":
            failures.append(
                "unlocked="
                f"{device.unlocked!r}; "
                "expected 'yes'"
            )

        if device.secure != "yes":
            failures.append(
                "secure="
                f"{device.secure!r}; "
                "expected 'yes'"
            )

        if device.is_userspace != "no":
            failures.append(
                "is-userspace="
                f"{device.is_userspace!r}; "
                "expected bootloader fastboot"
            )

        if device.snapshot_update_status != "none":
            failures.append(
                "snapshot-update-status="
                f"{device.snapshot_update_status!r}; "
                "expected 'none'"
            )

        if failures:
            raise RepartitionError(
                "Unsupported tangorpro repartition profile:\n  "
                + "\n  ".join(
                    failures
                )
            )

    @staticmethod
    def classify_geometry(
        device: RepartitionDevice,
        *,
        profile: TangorproRepartitionProfile = TANGORPRO_PROFILE,
    ) -> str:
        """
        Classify only exact, known tangorpro storage geometries.
        """

        stock_super = (
            profile.super.size_sectors
            * profile.sector_size_bytes
        )

        stock_userdata = (
            profile.userdata.size_sectors
            * profile.sector_size_bytes
        )

        recognized = {
            (
                stock_super,
                stock_userdata,
            ):
                "stock",

            (
                10_000_269_312,
                117_169_016_832,
            ):
                "treeforge-10gb",

            (
                20_000_538_624,
                107_168_747_520,
            ):
                "treeforge-20gb",
        }

        geometry = recognized.get(
            (
                device.super_size_bytes,
                device.userdata_size_bytes,
            )
        )

        if geometry is None:
            raise RepartitionError(
                "Unrecognized tangorpro storage geometry:\n"
                f"  super size="
                f"{device.super_size_bytes:,}\n"
                f"  userdata size="
                f"{device.userdata_size_bytes:,}\n"
                "Expected canonical stock, "
                "recognized TreeForge 10GB legacy, "
                "or canonical TreeForge 20GB geometry."
            )

        return geometry

    def validate_inspect(
        self,
        *,
        device: RepartitionDevice,
        profile: TangorproRepartitionProfile = TANGORPRO_PROFILE,
    ) -> str:
        """
        Validate device identity and classify a recognized
        storage geometry without authorizing repartition.
        """

        self._validate_identity(
            device,
            profile=profile,
        )

        return self.classify_geometry(
            device,
            profile=profile,
        )

    def build_forward_plan(
        self,
        *,
        device: RepartitionDevice,
        requested_super_size: str = "20GB",
        profile: TangorproRepartitionProfile = TANGORPRO_PROFILE,
    ) -> RepartitionPlan:
        """
        Build a forward plan from an exact recognized
        live tangorpro geometry.
        """

        self.validate_inspect(
            device=device,
            profile=profile,
        )

        return build_repartition_plan_from_geometry(
            current_super_size_bytes=(
                device.super_size_bytes
            ),
            current_userdata_size_bytes=(
                device.userdata_size_bytes
            ),
            requested_super_size=(
                requested_super_size
            ),
            profile=profile,
        )

    def validate_forward(
        self,
        *,
        device: RepartitionDevice,
        profile: TangorproRepartitionProfile = TANGORPRO_PROFILE,
    ) -> None:
        self._validate_identity(
            device,
            profile=profile,
        )

        expected_super = (
            profile.super.size_sectors
            * profile.sector_size_bytes
        )

        expected_userdata = (
            profile.userdata.size_sectors
            * profile.sector_size_bytes
        )

        failures: list[str] = []

        if device.super_size_bytes != expected_super:
            failures.append(
                "super size="
                f"{device.super_size_bytes:,}; "
                f"expected stock {expected_super:,}"
            )

        if (
            device.userdata_size_bytes
            != expected_userdata
        ):
            failures.append(
                "userdata size="
                f"{device.userdata_size_bytes:,}; "
                f"expected stock {expected_userdata:,}"
            )

        if failures:
            raise RepartitionError(
                "Forward repartition requires the exact "
                "canonical tangorpro stock geometry:\n  "
                + "\n  ".join(
                    failures
                )
            )

    def validate_undo(
        self,
        *,
        device: RepartitionDevice,
        plan: RepartitionPlan,
        profile: TangorproRepartitionProfile = TANGORPRO_PROFILE,
    ) -> None:
        self._validate_identity(
            device,
            profile=profile,
        )

        expected_super = (
            plan.current_super_size_sectors
            * plan.sector_size_bytes
        )

        expected_userdata = (
            plan.current_userdata_size_sectors
            * plan.sector_size_bytes
        )

        failures: list[str] = []

        if device.super_size_bytes != expected_super:
            failures.append(
                "super size="
                f"{device.super_size_bytes:,}; "
                "expected TreeForge layout "
                f"{expected_super:,}"
            )

        if (
            device.userdata_size_bytes
            != expected_userdata
        ):
            failures.append(
                "userdata size="
                f"{device.userdata_size_bytes:,}; "
                "expected TreeForge layout "
                f"{expected_userdata:,}"
            )

        if failures:
            raise RepartitionError(
                "Undo requires the exact recognized "
                "TreeForge tangorpro geometry:\n  "
                + "\n  ".join(
                    failures
                )
            )
