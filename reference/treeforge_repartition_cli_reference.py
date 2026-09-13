from __future__ import annotations

import json
from pathlib import Path

import typer

from treeforge.android.repartition import (
    RepartitionError,
    RepartitionPlan,
    TangorproRepartitionDeviceController,
    build_repartition_plan,
    build_undo_plan,
)
from treeforge.android.repartition.execution import (
    TangorproRepartitionExecutor,
)


app = typer.Typer(
    help=(
        "Plan or apply the tangorpro TreeForge "
        "A/B physical storage layout."
    ),
)


def _format_bytes(
    value: int,
) -> str:
    return (
        f"{value:,} bytes "
        f"({value / 1_000_000_000:.3f} GB / "
        f"{value / (1024 ** 3):.3f} GiB)"
    )


def _write_json(
    path: Path,
    payload: dict[str, object],
) -> None:
    path.write_text(
        json.dumps(
            payload,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def _render_plan(
    plan: RepartitionPlan,
    *,
    plan_path: Path,
) -> None:
    typer.echo(
        "=== CURRENT GEOMETRY ==="
    )
    typer.echo(
        "super start:          "
        f"{plan.current_super_start_sector:,} "
        "kernel 512-byte units"
    )
    typer.echo(
        "super size:           "
        + _format_bytes(
            plan.current_super_size_sectors
            * plan.sector_size_bytes
        )
    )
    typer.echo(
        "super end:            "
        f"{plan.current_super_end_sector:,} "
        "kernel 512-byte units"
    )
    typer.echo(
        "userdata start:       "
        f"{plan.current_userdata_start_sector:,} "
        "kernel 512-byte units"
    )
    typer.echo(
        "userdata size:        "
        + _format_bytes(
            plan.current_userdata_size_sectors
            * plan.sector_size_bytes
        )
    )
    typer.echo(
        "userdata end:         "
        f"{plan.current_userdata_end_sector:,} "
        "kernel 512-byte units"
    )
    typer.echo()

    typer.echo(
        "=== TARGET GEOMETRY ==="
    )
    typer.echo(
        "Realized super:       "
        + _format_bytes(
            plan.realized_super_size_bytes
        )
    )
    typer.echo(
        "Super action:         "
        f"{plan.action}"
    )
    typer.echo(
        "New super start:      "
        f"{plan.new_super_start_sector:,} "
        "kernel 512-byte units"
    )
    typer.echo(
        "New super end:        "
        f"{plan.new_super_end_sector:,} "
        "kernel 512-byte units"
    )
    typer.echo(
        "New userdata start:   "
        f"{plan.new_userdata_start_sector:,} "
        "kernel 512-byte units"
    )
    typer.echo(
        "New userdata size:    "
        + _format_bytes(
            plan.new_userdata_size_sectors
            * plan.sector_size_bytes
        )
    )
    typer.echo(
        "New userdata end:     "
        f"{plan.new_userdata_end_sector:,} "
        "kernel 512-byte units"
    )
    typer.echo()

    typer.echo(
        f"userdata action:      "
        f"{plan.userdata_action}"
    )
    typer.echo(
        "metadata action:      RECREATE"
    )
    typer.echo(
        "Destructive when applied: YES"
    )
    typer.echo()
    typer.echo(
        f"Plan: {plan_path}"
    )


def _destructive_confirmation(
    *,
    direction: str,
    plan: RepartitionPlan,
) -> bool:
    typer.echo()
    typer.echo(
        "============================================================"
    )
    typer.echo(
        "DESTRUCTIVE TANGORPRO REPARTITION"
    )
    typer.echo(
        "============================================================"
    )
    typer.echo()
    typer.echo(
        "THIS OPERATION WILL:"
    )
    typer.echo(
        "  - modify the physical GPT on /dev/block/sda"
    )
    typer.echo(
        "  - change the super/userdata boundary"
    )
    typer.echo(
        "  - permanently erase ALL userdata"
    )
    typer.echo(
        "  - recreate userdata"
    )
    typer.echo(
        "  - reset/recreate metadata encryption state"
    )
    typer.echo(
        "  - preserve GPT entries 1-24"
    )
    typer.echo(
        "  - create a fresh raw GPT rollback backup"
    )
    typer.echo()
    typer.echo(
        "Apps, accounts, settings, downloads, and all "
        "other data stored in /data WILL BE LOST."
    )
    typer.echo()
    typer.echo(
        f"Direction: {direction.upper()}"
    )
    typer.echo(
        "Target super: "
        + _format_bytes(
            plan.realized_super_size_bytes
        )
    )
    typer.echo(
        "Target userdata: "
        + _format_bytes(
            plan.new_userdata_size_sectors
            * plan.sector_size_bytes
        )
    )
    typer.echo()

    if not typer.confirm(
        "Continue with destructive repartition?",
        default=False,
    ):
        typer.echo(
            "Repartition cancelled."
        )
        typer.echo(
            "DEVICE MODIFIED: NO"
        )
        return False

    typed = typer.prompt(
        "Type ERASE TANGORPRO to continue"
    )

    if typed != "ERASE TANGORPRO":
        typer.echo(
            "Confirmation text did not match."
        )
        typer.echo(
            "Repartition cancelled."
        )
        typer.echo(
            "DEVICE MODIFIED: NO"
        )
        return False

    return True


def _executor(
    controller: TangorproRepartitionDeviceController,
) -> TangorproRepartitionExecutor:
    return TangorproRepartitionExecutor(
        repository_root=Path.cwd(),
        fastboot=controller.fastboot,
    )


@app.callback(
    invoke_without_command=True,
)
def repartition(
    ctx: typer.Context,
    super_size: str = typer.Option(
        "10GB",
        "--super-size",
        metavar="SIZE",
        help=(
            "Requested physical super size. "
            "Default: 10GB."
        ),
    ),
    apply: bool = typer.Option(
        False,
        "--apply",
        help=(
            "Apply the destructive repartition after "
            "preflight and explicit confirmation."
        ),
    ),
) -> None:
    if ctx.invoked_subcommand is not None:
        return

    output_root = (
        Path.cwd()
        / "output"
        / "repartition"
    ).resolve()

    output_root.mkdir(
        parents=True,
        exist_ok=True,
    )

    try:
        plan = build_repartition_plan(
            requested_super_size=super_size,
        )

        controller = (
            TangorproRepartitionDeviceController()
        )

        device = controller.probe()

        controller.validate_forward(
            device=device,
        )

    except Exception as error:
        typer.echo(
            f"Repartition preflight failed: {error}",
            err=True,
        )
        typer.echo(
            "DEVICE MODIFIED: NO",
            err=True,
        )
        raise typer.Exit(
            code=1
        ) from error

    plan_path = (
        output_root
        / "repartition-plan.json"
    )

    _write_json(
        plan_path,
        plan.to_dict(),
    )

    typer.echo(
        "TreeForge Repartition"
    )
    typer.echo(
        "Profile: tangorpro-treeforge-ab-v1"
    )
    typer.echo(
        f"Device: {device.product}"
    )
    typer.echo(
        f"Bootloader: {device.bootloader}"
    )
    typer.echo(
        f"Current slot: {device.current_slot}"
    )
    typer.echo(
        "Admission: PASS"
    )
    typer.echo(
        f"Mode: {'APPLY' if apply else 'PLAN ONLY'}"
    )
    typer.echo()

    _render_plan(
        plan,
        plan_path=plan_path,
    )

    if not apply:
        typer.echo(
            "DEVICE MODIFIED: NO"
        )
        return

    executor = _executor(
        controller
    )

    try:
        formatter = (
            executor.preflight_formatters()
        )

    except Exception as error:
        typer.echo()
        typer.echo(
            "Execution preflight failed: "
            f"{error}",
            err=True,
        )
        typer.echo(
            "DEVICE MODIFIED: NO",
            err=True,
        )
        raise typer.Exit(
            code=1
        ) from error

    typer.echo()
    typer.echo(
        "=== EXECUTION PREFLIGHT ==="
    )
    typer.echo(
        f"Temporary boot: {executor.boot_image}"
    )
    typer.echo(
        f"Filesystem evidence: "
        f"{formatter.fstab_path}"
    )
    typer.echo(
        f"userdata filesystem: "
        f"{formatter.userdata_fs}"
    )
    typer.echo(
        f"metadata filesystem: "
        f"{formatter.metadata_fs}"
    )

    for helper in formatter.helpers:
        typer.echo(
            f"Formatter helper: {helper}"
        )

    typer.echo(
        "Execution preflight: PASS"
    )

    if not _destructive_confirmation(
        direction="forward",
        plan=plan,
    ):
        return

    try:
        run_root = executor.execute(
            plan=plan,
            formatter=formatter,
        )

    except Exception as error:
        typer.echo()
        typer.echo(
            f"REPARTITION FAILED: {error}",
            err=True,
        )
        typer.echo(
            "Do not boot Android until the reported "
            "device state is understood.",
            err=True,
        )
        raise typer.Exit(
            code=1
        ) from error

    typer.echo()
    typer.echo(
        "=== REPARTITION COMPLETE ==="
    )
    typer.echo(
        "GPT target readback: PASS"
    )
    typer.echo(
        "Bootloader target geometry: PASS"
    )
    typer.echo(
        "userdata recreation: PASS"
    )
    typer.echo(
        "metadata recreation: PASS"
    )
    typer.echo(
        f"Rollback artifacts: {run_root}"
    )
    typer.echo(
        "Device left in bootloader fastboot."
    )
    typer.echo(
        "DEVICE MODIFIED: YES"
    )


@app.command(
    "undo",
)
def undo(
    from_super_size: str = typer.Option(
        "10GB",
        "--from-super-size",
        metavar="SIZE",
        help=(
            "TreeForge super size being reversed."
        ),
    ),
    apply: bool = typer.Option(
        False,
        "--apply",
        help=(
            "Apply the destructive undo after "
            "preflight and explicit confirmation."
        ),
    ),
) -> None:
    output_root = (
        Path.cwd()
        / "output"
        / "repartition"
    ).resolve()

    output_root.mkdir(
        parents=True,
        exist_ok=True,
    )

    try:
        plan = build_undo_plan(
            from_super_size=from_super_size,
        )

        controller = (
            TangorproRepartitionDeviceController()
        )

        device = controller.probe()

        controller.validate_undo(
            device=device,
            plan=plan,
        )

    except Exception as error:
        typer.echo(
            f"Undo preflight failed: {error}",
            err=True,
        )
        typer.echo(
            "DEVICE MODIFIED: NO",
            err=True,
        )
        raise typer.Exit(
            code=1
        ) from error

    plan_path = (
        output_root
        / "repartition-undo-plan.json"
    )

    _write_json(
        plan_path,
        plan.to_dict(),
    )

    typer.echo(
        "TreeForge Repartition Undo"
    )
    typer.echo(
        f"Device: {device.product}"
    )
    typer.echo(
        f"Bootloader: {device.bootloader}"
    )
    typer.echo(
        f"Mode: {'APPLY' if apply else 'PLAN ONLY'}"
    )
    typer.echo()

    _render_plan(
        plan,
        plan_path=plan_path,
    )

    if not apply:
        typer.echo(
            "DEVICE MODIFIED: NO"
        )
        return

    executor = _executor(
        controller
    )

    try:
        executor.validate_undo_state(
            plan=plan,
        )

        formatter = (
            executor.preflight_formatters()
        )

    except Exception as error:
        typer.echo(
            f"Undo execution preflight failed: {error}",
            err=True,
        )
        typer.echo(
            "DEVICE MODIFIED: NO",
            err=True,
        )
        raise typer.Exit(
            code=1
        ) from error

    typer.echo()
    typer.echo(
        "Undo super safety fingerprint: "
        "will be verified before GPT write"
    )
    typer.echo(
        "Undo execution preflight: PASS"
    )

    if not _destructive_confirmation(
        direction="undo",
        plan=plan,
    ):
        return

    try:
        run_root = executor.execute(
            plan=plan,
            formatter=formatter,
        )

    except Exception as error:
        typer.echo()
        typer.echo(
            f"UNDO FAILED: {error}",
            err=True,
        )
        raise typer.Exit(
            code=1
        ) from error

    typer.echo()
    typer.echo(
        "=== REPARTITION UNDO COMPLETE ==="
    )
    typer.echo(
        "Canonical tangorpro GPT geometry: RESTORED"
    )
    typer.echo(
        "userdata recreation: PASS"
    )
    typer.echo(
        "metadata recreation: PASS"
    )
    typer.echo(
        f"Rollback artifacts: {run_root}"
    )
    typer.echo(
        "Device left in bootloader fastboot."
    )
    typer.echo(
        "DEVICE MODIFIED: YES"
    )
