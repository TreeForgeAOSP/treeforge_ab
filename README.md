# TreeForge A/B

TreeForge A/B converts a validated Google Pixel Tablet storage layout into a
complete independently populated A/B Android layout.

The project is experimental and currently supports one validated
device/software configuration only.

## Supported configuration

The initial supported configuration is:

- Device: Google Pixel Tablet (`tangorpro`)
- Bootloader: `tangorpro-15.2-13237001`
- Android generation: Android 15 / API 35
- Validated Google factory build: `BP1A.250505.005`
- Validated AOSP release: `android-15.0.0_r36`
- Slot count: two
- Bootloader: unlocked
- Starting storage layout: the validated stock tangorpro GPT geometry

Other bootloader versions, Android releases, Pixel Tablet software generations,
storage layouts, and devices have not yet been validated.

Destructive operations must refuse to run unless the connected device matches
the supported compatibility profile. There is no unsupported-device override
in the initial release.

## Required device setup

Device setup is performed by the user before running TreeForge A/B.

For the Google factory-image setup path, obtain the Pixel Tablet factory image
from Google's official Pixel factory-image landing page:

https://developers.google.com/android/images

For the initial supported profile, select Pixel Tablet (`tangorpro`) and use:

`BP1A.250505.005`

Follow the appropriate manual flashing procedure to place the device on the
validated Android 15 software and bootloader baseline before running this tool.

TreeForge A/B does not download or redistribute Google factory images and does
not perform the prerequisite software/bootloader downgrade.

After setup, the device must report:

`version-bootloader: tangorpro-15.2-13237001`

The tool verifies the device and storage geometry before permitting any
destructive operation.

## One-time conversion

TreeForge A/B performs a **one-time physical repartitioning and full-A/B layout conversion**.

The Android build used during the conversion is used to populate and validate the newly created A and B slots. It is **not** software that must remain installed on the device afterward.

Once the conversion has completed successfully and both slots have been validated, the device remains in the TreeForge full-A/B layout. You can replace the Android software afterward with other compatible Android builds without rerunning the physical conversion.

In practical terms:

- TreeForge A/B does not need to remain installed on the device.
- The Android build used for the initial conversion does not need to remain installed.
- Future compatible Android builds can be flashed to the converted device.
- The physical repartition/full-A/B conversion does not need to be repeated for each Android build.
- The converted A/B storage layout remains in place until something intentionally repartitions the device again.
- Restoring software that recreates the original stock partition layout may undo the TreeForge full-A/B conversion and require the conversion to be performed again.

The `undo` command exists for intentionally restoring the supported immediate predecessor layout when appropriate.

## Installation

TreeForge A/B requires Python 3.11 or newer.

Clone the repository:

```bash
git clone https://github.com/TreeForgeAOSP/treeforge_ab.git
cd treeforge_ab
```

Create the repository-local environment and install TreeForge A/B:

```bash
./setup.sh
```

After setup, run the CLI directly from the repository:

```bash
./treeforge-ab --help
./treeforge-ab inspect
```

You can also activate the repository-local environment and use the installed console command:

```bash
source .venv/bin/activate
treeforge-ab --help
```

Or invoke the package as a Python module:

```bash
.venv/bin/python -m treeforge_ab --help
```

## Installation source

After the prerequisite setup is complete, TreeForge A/B consumes a
user-prepared Android image set.

The initial release accepts either:

- the extracted images corresponding to Google factory build
  `BP1A.250505.005`; or
- a compatible Pixel Tablet AOSP product output built from
  `android-15.0.0_r36`.

The supplied image set is validated before repartitioning begins.

## Super size

The initial tangorpro profile uses `20GB` as its default requested physical
`super` size.

This is a tested default, not a universal Android or Pixel Tablet requirement.

`GB` is decimal:

`20GB = 20,000,000,000 bytes`

The requested size is rounded upward to the storage alignment required by the
device profile.

For the initial tangorpro profile:

`alignment = 1,048,576 bytes`

The calculation is:

`realized = ceil(requested / alignment) * alignment`

Therefore:

`20GB -> 20,000,538,624 bytes`

The requested and realized sizes must both be displayed before a destructive
operation.

## Full A/B conversion

The full conversion is designed to:

1. validate the connected device;
2. validate the supplied Android image set;
3. validate the starting physical storage geometry;
4. calculate and display the aligned target layout;
5. explicitly confirm the destructive repartition;
6. resize the physical `super` / `userdata` boundary;
7. construct complete logical slot A and slot B metadata;
8. mirror appropriate physical boot-chain images to A and B;
9. populate complete dynamic partitions for A and B;
10. realize shared `super` metadata exactly once; and
11. guide the user through independent boot testing of both slots.

The initial release does not automatically claim that both slots boot
successfully. Until automated diagnostic-kernel validation is available, the
tool guides the user through testing slot A and slot B and records the user's
confirmation.

## Android tooling

Android-derived executables are not stored in this repository.

Required tools are obtained from exact, verified releases of the public
`TreeForgeAOSP/treeforge_toolchain` repository.

The standalone project must not fall back to arbitrary executables found on
`PATH` or to an unrelated local AOSP build output.

## Conversion commands

`treeforge-ab convert --input <directory>` performs the complete initial
tangorpro conversion to the canonical `treeforge-full-ab-v1` layout.

Initial conversion is intentionally destructive:

- physical `super` is brought to the canonical 20 GB profile when required;
- userdata is permanently wiped and recreated;
- metadata is wiped/recreated so encryption state is reset cleanly;
- fastboot full-A/B realization retains the wipe transaction;
- the physical boot chain is populated on both A and B;
- all six dynamic partition families are populated on both A and B;
- `update-super` is executed exactly once;
- Android is not automatically rebooted;
- installation does not change the active slot.

`--aosp-root <path>` overrides temporary AOSP runtime resolution. The path
may identify either an AOSP checkout or a tangorpro product output. Without
the override, bounded TreeForge-owned AOSP/output locations are checked.

`treeforge-ab validate-slots` performs guided independent A/B Android boot
validation after conversion.

`treeforge-ab undo` restores only an immediate physically safe predecessor.
Automatic physical shrinking is intentionally refused after the full-A/B LP
layout has been realized because the logical metadata then targets the larger
20 GB physical super layout.

## Validated full-A/B baseline

The current `tangorpro` Android 15 / `android-15.0.0_r36` implementation has
completed real-device conversion and independent slot validation.

Observed acceptance result:

```text
Slot A: PASS
Slot B: PASS
Original active slot restored: YES
Automatic Android reboot: NO
```

The completed conversion provides:

- canonical physical `super` size of `20,000,538,624` bytes;
- complete physical boot-chain images on slots A and B;
- complete logical Android partition sets on slots A and B;
- exactly one shared `super` realization;
- mandatory userdata wipe and F2FS recreation;
- mandatory metadata wipe and F2FS recreation;
- no automatic Android reboot after conversion;
- no automatic active-slot change after conversion.

## Command summary

Inspect the connected device and recognized geometry:

```bash
treeforge-ab inspect
```

Validate an input without modifying the device:

```bash
treeforge-ab check --input /path/to/product/output
```

Build the canonical repartition/full-A/B plan without modifying the device:

```bash
treeforge-ab plan --input /path/to/product/output
```

Perform the destructive full-A/B conversion:

```bash
treeforge-ab convert --input /path/to/product/output
```

Independently boot and validate both slots:

```bash
treeforge-ab validate-slots
```

Perform a supported immediate-predecessor physical rollback:

```bash
treeforge-ab undo
```

## Rollback safety after full-A/B realization

Physical rollback state is generation-based. A migration chain can preserve
stock, legacy TreeForge 10 GB, and canonical TreeForge 20 GB predecessors.

Automatic physical shrinking is intentionally blocked after the complete
`treeforge-full-ab-v1` logical layout has been realized. At that point the LP
metadata and logical partition contents may depend on the larger physical
`super` allocation, so shrinking it without first restoring a compatible
logical layout could truncate valid data.

The super-prefix safety fingerprint must not be weakened or bypassed.

## Input and proprietary-content policy

TreeForge A/B does not redistribute Google factory images, Pixel vendor
images, or other proprietary Google partition payloads.

Supported operation uses user-controlled local inputs such as a compatible
`tangorpro` AOSP product output or a validated user-prepared image set.

An explicit AOSP runtime may be supplied with `--aosp-root`. Invalid explicit
runtime overrides fail rather than silently falling back to another source.

## Generated-data policy

Generated conversion state belongs under repository-local `output/` and is
never source material. This includes GPT backups, repartition state, generated
LP metadata, device preflight reports, conversion reports, and staged runtime
provider files.

These artifacts, Android images, provider caches, local virtual environments,
signing material, credentials, and proprietary payloads must never be
committed to the development repository.

## Release channel

Current release: `1.0.2`

Python package version: `1.0.2`

TreeForge A/B `1.0.2` is published as a stable GitHub Release and immutable tag.

The public release is published from `TreeForgeAOSP/treeforge_ab`, with the matching development release maintained in `TreeForgeDEV/treeforge_ab`.
