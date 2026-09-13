def _run_treeforge_full_ab_install(
    *,
    build: AndroidInstallBuild,
    controller: AndroidInstallDeviceController,
    device,
    super_size_bytes: int,
    install_root: Path,
    plan_path: Path,
    manifest_path: Path,
    preflight_path: Path,
    report_path: Path,
) -> None:
    typer.echo()
    typer.echo(
        "=== TREEFORGE FULL A/B INSTALL ==="
    )
    typer.echo(
        f"Layout: {TREEFORGE_LAYOUT}"
    )
    typer.echo(
        f"Physical super: {super_size_bytes:,} bytes"
    )
    typer.echo(
        "Physical boot-chain: MIRROR A + B"
    )
    typer.echo(
        "Dynamic logical partitions: COMPLETE A + B"
    )
    typer.echo(
        "Post-install active-slot change: NO"
    )
    typer.echo(
        "Post-install Android reboot: ASK"
    )

    wipe_userdata = typer.confirm(
        "Wipe userdata and metadata?",
        default=False,
    )

    if wipe_userdata:
        conditional_wipe_partitions = {
            operation.partition
            for operation in build.operations
            if (
                operation.conditional
                and operation.partition
                is not None
            )
        }

        required = {
            "userdata",
            "metadata",
        }

        missing = (
            required
            - conditional_wipe_partitions
        )

        if missing:
            raise AndroidInstallError(
                "selected source does not declare "
                "required conditional wipe operations "
                "for: "
                + ", ".join(
                    sorted(missing)
                )
            )

    selection = _install_selection(
        device=device,
        wipe_userdata=wipe_userdata,
    )

    slot_applicability: dict[
        str,
        bool | None,
    ] = {}

    for image in build.images:
        if (
            image.partition
            in slot_applicability
        ):
            continue

        slot_applicability[
            image.partition
        ] = controller.has_slot(
            serial=device.serial,
            partition=image.partition,
        )

    full_ab_root = (
        install_root
        / "full-ab-v1"
    )

    plan = build_full_ab_plan(
        repository_root=Path.cwd(),
        build=build,
        super_size_bytes=super_size_bytes,
        output_root=full_ab_root,
    )

    preflight = validate_full_ab_preflight(
        build=build,
        device=device,
        slot_applicability=(
            slot_applicability
        ),
        plan=plan,
    )

    fastboot_info_text = (
        render_full_ab_fastboot_info(
            build=build,
            slot_applicability=(
                slot_applicability
            ),
        )
    )

    manifest_validation = (
        validate_full_ab_manifest(
            build=build,
            slot_applicability=(
                slot_applicability
            ),
            fastboot_info_text=(
                fastboot_info_text
            ),
        )
    )

    preflight[
        "manifest_validation"
    ] = manifest_validation

    manifest_path.write_text(
        fastboot_info_text,
        encoding="utf-8",
    )

    preflight_path.write_text(
        render_full_ab_preflight_report(
            build=build,
            device=device,
            selection=selection,
            slot_applicability=(
                slot_applicability
            ),
            preflight=preflight,
            plan=plan,
        ),
        encoding="utf-8",
    )

    _write_json(
        plan_path,
        build_full_ab_install_plan_payload(
            build=build,
            device=device,
            selection=selection,
            slot_applicability=(
                slot_applicability
            ),
            preflight=preflight,
            plan=plan,
            manifest_path=manifest_path,
        ),
    )

    data_action = (
        "WIPE"
        if wipe_userdata
        else "PRESERVE"
    )

    typer.echo()
    typer.echo(
        "=== FINAL INSTALL PLAN ==="
    )
    typer.echo(
        "Storage layout: TREEFORGE FULL A/B V1"
    )
    typer.echo(
        f"Super: {plan.super_size_bytes:,} bytes"
    )
    typer.echo(
        f"Group A: {plan.group_size_bytes:,} bytes"
    )
    typer.echo(
        f"Group B: {plan.group_size_bytes:,} bytes"
    )
    typer.echo(
        f"Per-slot payload: "
        f"{plan.slot_payload_bytes:,} bytes"
    )
    typer.echo(
        f"Per-slot headroom: "
        f"{plan.slot_headroom_bytes:,} bytes"
    )
    typer.echo(
        "Full dynamic slot A: YES"
    )
    typer.echo(
        "Full dynamic slot B: YES"
    )
    typer.echo(
        "Physical slotted images: A + B"
    )
    typer.echo(
        "Shared super realizations: 1"
    )
    typer.echo(
        f"Userdata: {data_action}"
    )
    typer.echo(
        f"Metadata: {data_action}"
    )
    typer.echo(
        "Post-install active-slot change: NO"
    )
    typer.echo(
        "Post-install Android reboot: ASK"
    )

    typer.echo()
    typer.echo(
        "Dynamic images:"
    )

    for partition in plan.partitions:
        typer.echo(
            "  "
            f"{partition.base_name}_a + "
            f"{partition.base_name}_b <- "
            f"{partition.image.image_name}"
        )

    typer.echo()
    typer.echo(
        "Projection validation:"
    )
    typer.echo(
        "  Snapshot state safe: YES"
    )
    typer.echo(
        "  Full logical partitions: "
        f"{preflight['full_dynamic_partition_count']}"
    )
    typer.echo(
        "  Synthetic secondary images: "
        f"{preflight['synthetic_secondary_dynamic_images']}"
    )
    typer.echo(
        "  Mirrored physical images: "
        f"{preflight['mirrored_physical_image_count']}"
    )
    typer.echo(
        "  update-super operations: 1"
    )
    typer.echo(
        "  Source flash semantics: PASS"
    )

    typer.echo()
    typer.echo(
        "Execution topology:"
    )
    typer.echo(
        "  1. Flash selected-slot physical images"
    )
    typer.echo(
        "  2. Mirror slotted physical images to other slot"
    )
    typer.echo(
        "  3. Enter fastbootd"
    )
    typer.echo(
        "  4. Apply TreeForge full-A/B super metadata once"
    )
    typer.echo(
        "  5. Flash six complete logical images to slot A"
    )
    typer.echo(
        "  6. Flash six complete logical images to slot B"
    )
    typer.echo(
        "  7. Stop in fastboot/fastbootd"
    )
    typer.echo(
        "  8. Offer Android reboot"
    )

    typer.echo()
    typer.echo(
        f"Generated manifest: {manifest_path}"
    )
    typer.echo(
        f"Generated super metadata: "
        f"{plan.super_empty_path}"
    )
    typer.echo(
        f"Plan: {plan_path}"
    )
    typer.echo(
        f"Preflight: {preflight_path}"
    )

    confirmed = typer.confirm(
        "Proceed with TreeForge full A/B flash?",
        default=False,
    )

    if not confirmed:
        report_path.write_text(
            (
                "TreeForge Install\n\n"
                "Status: CANCELLED\n"
                f"Mode: {TREEFORGE_LAYOUT}\n"
                "Flash performed: NO\n"
                f"Wipe requested: "
                f"{'YES' if wipe_userdata else 'NO'}\n"
                "Automatic Android reboot: NO\n"
            ),
            encoding="utf-8",
        )

        typer.echo(
            "Install cancelled. "
            "No partitions were flashed."
        )
        return

    #
    # The executor already knows how to stage super_empty.img,
    # execute flashall, preserve slot selection, and traverse
    # bootloader-fastboot <-> fastbootd.
    #
    # Substitute only TreeForge's generated LP metadata.
    #
    execution_build = replace(
        build,
        super_empty_path=(
            plan.super_empty_path
        ),
    )

    typer.echo()
    typer.echo(
        "Starting TreeForge full A/B install..."
    )

    try:
        result = controller.execute(
            build=execution_build,
            device=device,
            selection=selection,
            fastboot_info_text=(
                fastboot_info_text
            ),
        )

    except Exception as error:
        report_path.write_text(
            (
                "TreeForge Install\n\n"
                "Status: FAILED\n"
                f"Mode: {TREEFORGE_LAYOUT}\n"
                f"Build: {build.product_out}\n"
                f"Device: {device.serial}\n"
                f"Error: {error}\n"
                f"Wipe requested: "
                f"{'YES' if wipe_userdata else 'NO'}\n"
                "Automatic Android reboot: NO\n"
            ),
            encoding="utf-8",
        )

        typer.echo()
        typer.echo(
            f"Install failed: {error}",
            err=True,
        )
        typer.echo(
            "Device intentionally left in the "
            "fastboot domain where execution stopped.",
            err=True,
        )

        raise typer.Exit(
            code=1
        ) from error

    typer.echo()
    typer.echo(
        "TreeForge full A/B install "
        "completed successfully."
    )
    typer.echo(
        "Physical slotted images mirrored: A + B"
    )
    typer.echo(
        "Full logical slot A populated: YES"
    )
    typer.echo(
        "Full logical slot B populated: YES"
    )
    typer.echo(
        "Active slot preserved: "
        f"{result.final_active_slot.upper()}"
    )

    reboot_requested = typer.confirm(
        "Reboot to Android now?",
        default=False,
    )

    reboot_status = "NOT REQUESTED"
    reboot_error = None

    if reboot_requested:
        try:
            controller.reboot_android(
                serial=device.serial,
            )

            reboot_status = "REQUESTED"

        except Exception as error:
            reboot_status = "FAILED"
            reboot_error = str(error)

    report_lines = [
        "TreeForge Install",
        "",
        "Status: SUCCESS",
        f"Mode: {TREEFORGE_LAYOUT}",
        f"Build: {build.product_out}",
        f"Device: {device.serial}",
        (
            "Physical slotted images mirrored: YES"
        ),
        "Full dynamic slot A populated: YES",
        "Full dynamic slot B populated: YES",
        (
            "Independent A/B logical population: YES"
        ),
        (
            "Userdata preserved: "
            + (
                "NO"
                if wipe_userdata
                else "YES"
            )
        ),
        (
            "Metadata preserved: "
            + (
                "NO"
                if wipe_userdata
                else "YES"
            )
        ),
        (
            "Post-install active-slot change: NO"
        ),
        (
            "Active slot preserved: "
            f"{result.final_active_slot}"
        ),
        "Automatic Android reboot: NO",
        (
            "Post-install Android reboot requested: "
            + (
                "YES"
                if reboot_requested
                else "NO"
            )
        ),
        (
            "Post-install Android reboot command: "
            f"{reboot_status}"
        ),
    ]

    if reboot_error is not None:
        report_lines.append(
            "Post-install Android reboot error: "
            + reboot_error
        )

    report_path.write_text(
        "\n".join(
            report_lines
        )
        + "\n",
        encoding="utf-8",
    )

    if reboot_error is not None:
        typer.echo(
            f"Android reboot failed: {reboot_error}",
            err=True,
        )
        raise typer.Exit(
            code=1
        )

    if not reboot_requested:
        typer.echo(
            "Device remains in fastboot/fastbootd."
        )

    typer.echo(
        f"Report: {report_path}"
    )

