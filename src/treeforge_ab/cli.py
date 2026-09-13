from __future__ import annotations

from treeforge_ab.conversion import (
    ConversionCancelled,
    run_conversion,
    run_slot_validation,
    run_undo,
)

from pathlib import Path

from treeforge_ab.runtime_source import (
    RuntimeSourceError,
    resolve_aosp_runtime_source,
)
import json

import typer

from treeforge_ab.install.input import (
    resolve_input,
)
from treeforge_ab.install.full_ab import (
    build_full_ab_plan,
)
from treeforge_ab.repartition.planning import (
    build_repartition_plan,
)
from treeforge_ab.repartition.device import (
    RepartitionError,
    TangorproRepartitionDeviceController,
)
from treeforge_ab.install.models import (
    AndroidInstallError,
)


app = typer.Typer(
    name="treeforge-ab",
    no_args_is_help=True,
    help=(
        "TreeForge standalone physical "
        "repartition and full-A/B utility."
    ),
)


@app.callback()
def main() -> None:
    """
    TreeForge standalone physical repartition
    and full-A/B utility.
    """
    pass


@app.command("check")
def check_command(
    input_path: Path = typer.Option(
        ...,
        "--input",
        help=(
            "Explicit extracted factory-image "
            "directory or compatible AOSP "
            "product output."
        ),
        exists=True,
        file_okay=False,
        dir_okay=True,
        resolve_path=True,
    ),
) -> None:
    """
    Validate one input source without modifying a device.
    """

    try:
        resolved = resolve_input(
            input_path
        )

    except (
        AndroidInstallError,
        FileNotFoundError,
        OSError,
        ValueError,
    ) as error:
        typer.echo(
            f"Input check: FAIL\n{error}",
            err=True,
        )

        raise typer.Exit(
            code=1
        ) from error

    typer.echo(
        "TreeForge A/B Input Check"
    )
    typer.echo()
    typer.echo(
        f"Input:       {resolved.root}"
    )
    typer.echo(
        f"Type:        {resolved.kind}"
    )
    typer.echo(
        f"Product:     {resolved.product}"
    )
    typer.echo(
        "Android:     "
        f"{resolved.android_release}"
    )
    typer.echo(
        f"SDK:         {resolved.sdk}"
    )
    typer.echo(
        f"Baseline:    {resolved.baseline}"
    )
    typer.echo(
        f"Bootloader:  {resolved.bootloader}"
    )
    typer.echo(
        f"Images:      {len(resolved.images)}"
    )

    if (
        resolved.factory_bootloader_image
        is not None
    ):
        typer.echo(
            "Bootloader image evidence: "
            "PRESENT / VERIFIED"
        )
    elif resolved.kind == "factory":
        typer.echo(
            "Bootloader image evidence: "
            "NOT PRESENT"
        )
        typer.echo(
            "Device bootloader admission "
            "will still be enforced before "
            "destructive conversion."
        )

    typer.echo()
    typer.echo(
        "Input identity: PASS"
    )
    typer.echo(
        "Device probed: NO"
    )
    typer.echo(
        "Device modified: NO"
    )


@app.command("plan")
def plan_command(
    input_path: Path = typer.Option(
        ...,
        "--input",
        help=(
            "Explicit validated factory-image "
            "directory or compatible AOSP "
            "product output."
        ),
        exists=True,
        file_okay=False,
        dir_okay=True,
        resolve_path=True,
    ),
    super_size: str = typer.Option(
        "20GB",
        "--super-size",
        help=(
            "Requested physical super size. "
            "GB is decimal; the realized size "
            "is rounded upward to the device "
            "alignment."
        ),
    ),
    output: Path = typer.Option(
        Path("treeforge-ab-output"),
        "--output",
        help=(
            "Directory for local planning "
            "artifacts."
        ),
        file_okay=False,
        dir_okay=True,
    ),
) -> None:
    """
    Build the repartition and full-A/B plan without modifying a device.
    """

    try:
        resolved = resolve_input(
            input_path
        )

        repartition_plan = (
            build_repartition_plan(
                requested_super_size=(
                    super_size
                )
            )
        )

        output = (
            output
            .expanduser()
            .resolve()
        )

        repartition_root = (
            output
            / "repartition"
        )

        full_ab_root = (
            output
            / "full-ab"
        )

        repartition_root.mkdir(
            parents=True,
            exist_ok=True,
        )

        (
            repartition_root
            / "repartition-plan.json"
        ).write_text(
            json.dumps(
                repartition_plan.to_dict(),
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

        full_ab_plan = (
            build_full_ab_plan(
                repository_root=(
                    Path.cwd()
                ),
                build=resolved.build,
                super_size_bytes=(
                    repartition_plan
                    .realized_super_size_bytes
                ),
                output_root=(
                    full_ab_root
                ),
            )
        )

        combined = {
            "schema_version": 1,
            "input": {
                "root":
                    str(resolved.root),
                "kind":
                    resolved.kind,
                "product":
                    resolved.product,
                "android_release":
                    resolved.android_release,
                "sdk":
                    resolved.sdk,
                "baseline":
                    resolved.baseline,
                "required_bootloader":
                    resolved.bootloader,
            },
            "repartition":
                repartition_plan.to_dict(),
            "full_ab":
                full_ab_plan.to_dict(),
            "execution_policy": {
                "device_modified":
                    False,
                "destructive_if_applied":
                    True,
                "automatic_android_reboot":
                    False,
                "automatic_post_install_set_active":
                    False,
            },
        }

        combined_path = (
            output
            / "conversion-plan.json"
        )

        combined_path.write_text(
            json.dumps(
                combined,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

    except (
        AndroidInstallError,
        FileNotFoundError,
        OSError,
        ValueError,
    ) as error:
        typer.echo(
            "Plan: FAIL\n"
            f"{error}",
            err=True,
        )

        raise typer.Exit(
            code=1
        ) from error

    typer.echo(
        "TreeForge A/B Conversion Plan"
    )
    typer.echo()
    typer.echo(
        f"Input type:            "
        f"{resolved.kind}"
    )
    typer.echo(
        f"Product:               "
        f"{resolved.product}"
    )
    typer.echo(
        f"Baseline:              "
        f"{resolved.baseline}"
    )
    typer.echo(
        f"Required bootloader:   "
        f"{resolved.bootloader}"
    )
    typer.echo()

    typer.echo(
        "=== PHYSICAL REPARTITION ==="
    )
    typer.echo(
        "Requested super:       "
        f"{repartition_plan.requested_super_size_bytes:,} bytes"
    )
    typer.echo(
        "Realized super:        "
        f"{repartition_plan.realized_super_size_bytes:,} bytes"
    )
    typer.echo(
        "Alignment:             "
        f"{repartition_plan.alignment_bytes:,} bytes"
    )
    typer.echo(
        "Super action:          "
        f"{repartition_plan.action}"
    )
    typer.echo(
        "New userdata start:    "
        f"{repartition_plan.new_userdata_start_sector:,}"
    )
    typer.echo(
        "New userdata size:     "
        f"{repartition_plan.new_userdata_size_sectors * repartition_plan.sector_size_bytes:,} bytes"
    )
    typer.echo()

    typer.echo(
        "=== FULL A/B LOGICAL LAYOUT ==="
    )
    typer.echo(
        "Metadata size:         "
        f"{full_ab_plan.metadata_size_bytes:,} bytes"
    )
    typer.echo(
        "Metadata slots:        "
        f"{full_ab_plan.metadata_slots}"
    )
    typer.echo(
        "Virtual A/B flag:      "
        + (
            "PRESERVED"
            if full_ab_plan.preserve_virtual_ab_flag
            else "NOT SET"
        )
    )
    typer.echo(
        "Group A size:          "
        f"{full_ab_plan.group_size_bytes:,} bytes"
    )
    typer.echo(
        "Group B size:          "
        f"{full_ab_plan.group_size_bytes:,} bytes"
    )
    typer.echo(
        "One-slot payload:      "
        f"{full_ab_plan.slot_payload_bytes:,} bytes"
    )
    typer.echo(
        "Per-slot headroom:     "
        f"{full_ab_plan.slot_headroom_bytes:,} bytes"
    )
    typer.echo(
        "Logical partitions:    "
        f"{len(full_ab_plan.partitions) * 2}"
    )

    for partition in (
        full_ab_plan.partitions
    ):
        typer.echo(
            "  "
            f"{partition.base_name}_a + "
            f"{partition.base_name}_b: "
            f"{partition.realized_size_bytes:,} bytes each"
        )

    typer.echo()
    typer.echo(
        f"Plan:                  "
        f"{combined_path}"
    )
    typer.echo(
        "Destructive if applied: YES"
    )
    typer.echo(
        "Device probed:          NO"
    )
    typer.echo(
        "Device modified:        NO"
    )


@app.command("inspect")
def inspect_command(
    aosp_root: Path | None = typer.Option(
        None,
        "--aosp-root",
        help=(
            "Override the AOSP runtime source. "
            "May point to an AOSP checkout or directly "
            "to a tangorpro product-output directory."
        ),
    ),
) -> None:
    """
    Inspect device compatibility and show the exact
    forward migration plan without applying it.
    """

    controller = (
        TangorproRepartitionDeviceController()
    )

    reboot_requested = False
    transport_changed = False

    try:
        source_transport = None

        if controller._fastboot_present():
            userspace = (
                controller._fastboot_userspace()
            )

            if userspace is None:
                raise RepartitionError(
                    "Unable to determine whether "
                    "the connected fastboot device "
                    "is bootloader fastboot or "
                    "fastbootd."
                )

            if userspace:
                source_transport = "fastbootd"

        elif controller._adb_present():
            source_transport = "Android / ADB"

        else:
            raise RepartitionError(
                "No supported device transport "
                "detected. Connect the tablet in "
                "Android with ADB enabled, "
                "bootloader fastboot, or fastbootd."
            )

        if source_transport is not None:
            typer.echo(
                "TreeForge A/B Device Inspection"
            )
            typer.echo()
            typer.echo(
                f"Current transport: "
                f"{source_transport}"
            )
            typer.echo(
                "Bootloader fastboot is required "
                "for admission."
            )
            typer.echo()

            if not typer.confirm(
                "Reboot to bootloader now?",
                default=True,
            ):
                typer.echo()
                typer.echo(
                    "Inspection cancelled."
                )
                typer.echo(
                    "Persistent device modification: NO"
                )
                typer.echo(
                    "Transport changed: NO"
                )

                raise typer.Exit(
                    code=1
                )

            reboot_requested = True

            controller.ensure_bootloader()

            transport_changed = True

        device = (
            controller.probe_read_only()
        )

        geometry = (
            controller.validate_inspect(
                device=device
            )
        )

        migration = (
            controller.build_forward_plan(
                device=device,
                requested_super_size="20GB",
            )
        )

        runtime_source = (
            resolve_aosp_runtime_source(
                aosp_root,
            )
        )

    except typer.Exit:
        raise

    except (
        RepartitionError,
        RuntimeSourceError,
        FileNotFoundError,
        OSError,
        ValueError,
    ) as error:
        typer.echo()
        typer.echo(
            "TreeForge A/B Device Inspection"
        )
        typer.echo()
        typer.echo(
            "Admission: FAIL"
        )
        typer.echo(
            str(error),
            err=True,
        )
        typer.echo()
        typer.echo(
            "Persistent device modification: NO"
        )
        typer.echo(
            "Reboot requested: "
            + (
                "YES"
                if reboot_requested
                else "NO"
            )
        )
        typer.echo(
            "Transport changed: "
            + (
                "YES"
                if transport_changed
                else "NO"
            )
        )

        raise typer.Exit(
            code=1
        ) from error

    geometry_labels = {
        "stock":
            "STOCK / VERIFIED",

        "treeforge-10gb":
            (
                "TREEFORGE 10GB / VERIFIED / "
                "UPGRADE TO 20GB REQUIRED"
            ),

        "treeforge-20gb":
            (
                "TREEFORGE 20GB / VERIFIED / "
                "CANONICAL"
            ),
    }

    migration_required = (
        migration.super_delta_bytes > 0
    )

    typer.echo()
    typer.echo(
        "TreeForge A/B Device Inspection"
    )
    typer.echo()

    typer.echo(
        f"Product:                 "
        f"{device.product}"
    )
    typer.echo(
        f"Bootloader:              "
        f"{device.bootloader}"
    )
    typer.echo(
        f"Current slot:            "
        f"{device.current_slot}"
    )
    typer.echo(
        f"Slot count:              "
        f"{device.slot_count}"
    )
    typer.echo(
        f"Unlocked:                "
        f"{device.unlocked}"
    )
    typer.echo(
        f"Secure:                  "
        f"{device.secure}"
    )
    typer.echo(
        f"Userspace fastboot:      "
        f"{device.is_userspace}"
    )
    typer.echo(
        f"Snapshot update status:  "
        f"{device.snapshot_update_status}"
    )

    typer.echo()
    typer.echo(
        "=== STORAGE GEOMETRY ==="
    )
    typer.echo(
        f"Current super:           "
        f"{device.super_size_bytes:,} bytes"
    )
    typer.echo(
        f"Current userdata:        "
        f"{device.userdata_size_bytes:,} bytes"
    )
    typer.echo(
        f"Starting geometry:       "
        f"{geometry_labels[geometry]}"
    )

    typer.echo()
    typer.echo(
        "=== CANONICAL TARGET ==="
    )
    typer.echo(
        f"Target super:            "
        f"{migration.realized_super_size_bytes:,} bytes"
    )
    typer.echo(
        f"Target userdata:         "
        f"{migration.new_userdata_size_sectors * migration.sector_size_bytes:,} bytes"
    )
    typer.echo(
        f"Super action:            "
        f"{migration.action}"
    )
    typer.echo(
        f"Super delta:             "
        f"{migration.super_delta_bytes:+,} bytes"
    )
    typer.echo(
        f"Userdata delta:          "
        f"{migration.userdata_delta_bytes:+,} bytes"
    )
    typer.echo(
        f"Userdata action:         "
        f"{migration.userdata_action}"
    )
    typer.echo(
        "Migration required:      "
        + (
            "YES"
            if migration_required
            else "NO"
        )
    )

    typer.echo()
    typer.echo(
        "=== AOSP RUNTIME SOURCE ==="
    )
    typer.echo(
        f"Origin:                  "
        f"{runtime_source.origin}"
    )
    typer.echo(
        f"Product output:          "
        f"{runtime_source.product_out}"
    )
    typer.echo(
        f"Temporary boot:          "
        f"{runtime_source.boot_image}"
    )
    typer.echo(
        f"Filesystem evidence:     "
        f"{runtime_source.fstab_path}"
    )

    typer.echo()
    typer.echo(
        "Admission: PASS"
    )
    typer.echo(
        "Migration plan: VERIFIED"
    )
    typer.echo(
        "Persistent device modification: NO"
    )
    typer.echo(
        "Reboot requested: "
        + (
            "YES"
            if reboot_requested
            else "NO"
        )
    )
    typer.echo(
        "Transport changed: "
        + (
            "YES"
            if transport_changed
            else "NO"
        )
    )


@app.command("convert")
def convert_command(
    input_path: Path = typer.Option(
        ...,
        "--input",
        file_okay=False,
        dir_okay=True,
        resolve_path=True,
        help=(
            "Validated extracted factory-image "
            "directory or compatible AOSP product "
            "output."
        ),
    ),
    super_size: str = typer.Option(
        "20GB",
        "--super-size",
        help=(
            "Physical super target. v0.1 requires "
            "the canonical 20GB layout."
        ),
    ),
    aosp_root: Path | None = typer.Option(
        None,
        "--aosp-root",
        file_okay=False,
        dir_okay=True,
        resolve_path=True,
        help=(
            "Optional AOSP runtime override. May "
            "identify an AOSP checkout or product "
            "output."
        ),
    ),
    output_root: Path | None = typer.Option(
        None,
        "--output",
        file_okay=False,
        dir_okay=True,
        resolve_path=True,
        help=(
            "Optional conversion artifact directory."
        ),
    ),
) -> None:
    """
    Perform complete destructive tangorpro full-A/B conversion.

    Userdata and metadata are always wiped.
    Android is never automatically rebooted.
    """

    try:
        run_conversion(
            input_path=input_path,
            super_size=super_size,
            aosp_root=aosp_root,
            output_root=output_root,
        )

    except ConversionCancelled as error:
        typer.echo(str(error))
        raise typer.Exit(
            code=1
        ) from error

    except typer.Exit:
        raise

    except Exception as error:
        typer.echo()
        typer.echo(
            f"CONVERSION FAILED: {error}",
            err=True,
        )
        raise typer.Exit(
            code=1
        ) from error


@app.command("undo")
def undo_command(
    aosp_root: Path | None = typer.Option(
        None,
        "--aosp-root",
        file_okay=False,
        dir_okay=True,
        resolve_path=True,
        help=(
            "Optional AOSP runtime override."
        ),
    ),
) -> None:
    """
    Restore the immediate safe physical predecessor.

    Userdata and metadata are wiped/recreated.
    """

    try:
        run_undo(
            aosp_root=aosp_root,
        )

    except ConversionCancelled as error:
        typer.echo(str(error))
        raise typer.Exit(
            code=1
        ) from error

    except typer.Exit:
        raise

    except Exception as error:
        typer.echo()
        typer.echo(
            f"UNDO FAILED: {error}",
            err=True,
        )
        raise typer.Exit(
            code=1
        ) from error


@app.command("validate-slots")
def validate_slots_command() -> None:
    """
    Guided independent Android boot validation for A and B.
    """

    try:
        run_slot_validation()

    except ConversionCancelled as error:
        typer.echo(str(error))
        raise typer.Exit(
            code=1
        ) from error

    except typer.Exit:
        raise

    except Exception as error:
        typer.echo()
        typer.echo(
            f"SLOT VALIDATION FAILED: {error}",
            err=True,
        )
        raise typer.Exit(
            code=1
        ) from error

if __name__ == "__main__":
    app()
