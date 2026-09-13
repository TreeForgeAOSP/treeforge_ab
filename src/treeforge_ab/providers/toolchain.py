from __future__ import annotations

import hashlib
from importlib.resources import files
import json
import os
from pathlib import Path
import shutil
import tarfile
import tempfile
from typing import Any
from urllib.parse import quote
from urllib.request import Request, urlopen


class ToolchainProviderError(RuntimeError):
    pass


def _lock_payload() -> dict[str, Any]:
    resource = (
        files("treeforge_ab")
        .joinpath("data")
        .joinpath("provider-releases.json")
    )

    return json.loads(
        resource.read_text(
            encoding="utf-8"
        )
    )


def _provider_record(
    name: str,
) -> dict[str, Any]:
    payload = _lock_payload()

    providers = payload.get(
        "providers",
        {},
    )

    record = providers.get(
        name
    )

    if not isinstance(
        record,
        dict,
    ):
        raise ToolchainProviderError(
            f"unknown Android provider: {name}"
        )

    return dict(record)


def canonical_cache_root() -> Path:
    override = os.environ.get(
        "TREEFORGE_ANDROID_PROVIDER_CACHE"
    )

    if override:
        return (
            Path(override)
            .expanduser()
            .resolve()
        )

    return (
        Path.home()
        / ".cache"
        / "treeforge"
        / "android-provider"
    )


def _provider_root(
    *,
    name: str,
    record: dict[str, Any],
) -> Path:
    return (
        canonical_cache_root()
        / name
        / str(record["release"])
    )


def _sha256(
    path: Path,
) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as handle:
        while True:
            chunk = handle.read(
                1024 * 1024
            )

            if not chunk:
                break

            digest.update(chunk)

    return digest.hexdigest()


def _validate_cached_provider(
    *,
    name: str,
    record: dict[str, Any],
    root: Path,
) -> bool:
    if not root.is_dir():
        return False

    marker = (
        root
        / ".treeforge-release.json"
    )

    if not marker.is_file():
        return False

    try:
        payload = json.loads(
            marker.read_text(
                encoding="utf-8"
            )
        )
    except Exception:
        return False

    checks = {
        "provider": name,
        "release": record["release"],
        "release_tag": record["release_tag"],
        "archive_sha256": record["archive_sha256"],
        "public_repository":
            "TreeForgeAOSP/treeforge_toolchain",
    }

    for key, expected in checks.items():
        if payload.get(key) != expected:
            return False

    for relative in record.get(
        "required_files",
        (),
    ):
        path = (
            root
            / relative
        )

        if (
            not path.is_file()
            or not os.access(
                path,
                os.X_OK,
            )
        ):
            return False

    return True


def _download_url(
    *,
    record: dict[str, Any],
) -> str:
    tag = quote(
        str(
            record[
                "release_tag"
            ]
        ),
        safe="",
    )

    asset = quote(
        str(
            record[
                "asset"
            ]
        ),
        safe="",
    )

    return (
        "https://github.com/"
        "TreeForgeAOSP/"
        "treeforge_toolchain/"
        "releases/download/"
        f"{tag}/{asset}"
    )


def _safe_extract(
    *,
    archive: Path,
    destination: Path,
) -> None:
    destination = (
        destination.resolve()
    )

    with tarfile.open(
        archive,
        mode="r:xz",
    ) as handle:
        for member in handle.getmembers():
            if (
                member.issym()
                or member.islnk()
            ):
                raise ToolchainProviderError(
                    "provider archive contains "
                    "a symbolic or hard link: "
                    f"{member.name}"
                )

            target = (
                destination
                / member.name
            ).resolve()

            try:
                target.relative_to(
                    destination
                )
            except ValueError as error:
                raise ToolchainProviderError(
                    "provider archive contains "
                    "an unsafe path: "
                    f"{member.name}"
                ) from error

            if member.isdir():
                target.mkdir(
                    parents=True,
                    exist_ok=True,
                )
                continue

            if not member.isfile():
                raise ToolchainProviderError(
                    "provider archive contains "
                    "an unsupported entry: "
                    f"{member.name}"
                )

            target.parent.mkdir(
                parents=True,
                exist_ok=True,
            )

            source = (
                handle.extractfile(
                    member
                )
            )

            if source is None:
                raise ToolchainProviderError(
                    "unable to extract provider "
                    f"member: {member.name}"
                )

            with (
                source,
                target.open(
                    "wb"
                ) as output,
            ):
                shutil.copyfileobj(
                    source,
                    output,
                )

            target.chmod(
                member.mode
                & 0o777
            )


def _normalized_payload_root(
    staging: Path,
) -> Path:
    if (
        staging
        / "bin"
    ).is_dir():
        return staging

    children = tuple(
        path
        for path in staging.iterdir()
        if path.name
        not in {
            ".",
            "..",
        }
    )

    if (
        len(children) == 1
        and children[0].is_dir()
        and (
            children[0]
            / "bin"
        ).is_dir()
    ):
        return children[0]

    return staging


def materialize_provider(
    name: str,
) -> Path:
    record = _provider_record(
        name
    )

    root = _provider_root(
        name=name,
        record=record,
    )

    if _validate_cached_provider(
        name=name,
        record=record,
        root=root,
    ):
        return root

    if root.exists():
        raise ToolchainProviderError(
            "cached Android provider exists "
            "but does not match the exact "
            f"release lock: {root}"
        )

    cache_root = (
        canonical_cache_root()
    )

    cache_root.mkdir(
        parents=True,
        exist_ok=True,
    )

    with tempfile.TemporaryDirectory(
        prefix=(
            f".treeforge-ab-"
            f"{name}-"
        ),
        dir=cache_root,
    ) as temporary:
        temporary_root = Path(
            temporary
        )

        archive = (
            temporary_root
            / str(
                record["asset"]
            )
        )

        request = Request(
            _download_url(
                record=record
            ),
            headers={
                "User-Agent":
                    "TreeForge-AB/0.1",
            },
        )

        with (
            urlopen(
                request,
                timeout=60,
            ) as response,
            archive.open(
                "wb"
            ) as output,
        ):
            shutil.copyfileobj(
                response,
                output,
            )

        actual = _sha256(
            archive
        )

        expected = str(
            record[
                "archive_sha256"
            ]
        )

        if actual != expected:
            raise ToolchainProviderError(
                "Android provider archive "
                "SHA-256 mismatch for "
                f"{name}: expected "
                f"{expected}, got {actual}"
            )

        staging = (
            temporary_root
            / "payload"
        )

        staging.mkdir(
            parents=True,
            exist_ok=True,
        )

        _safe_extract(
            archive=archive,
            destination=staging,
        )

        payload_root = (
            _normalized_payload_root(
                staging
            )
        )

        for relative in record.get(
            "required_files",
            (),
        ):
            candidate = (
                payload_root
                / relative
            )

            if not candidate.is_file():
                raise ToolchainProviderError(
                    "provider archive is missing "
                    f"required file {relative}: "
                    f"{name}"
                )

        root.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        materialized = (
            temporary_root
            / "materialized"
        )

        shutil.copytree(
            payload_root,
            materialized,
        )

        marker = {
            "schema": 1,
            "name": name,
            "provider": name,
            "public_repository":
                "TreeForgeAOSP/treeforge_toolchain",
            "source_repository":
                "TreeForgeDEV/treeforge_toolchain",
            "release":
                record["release"],
            "release_tag":
                record["release_tag"],
            "asset":
                record["asset"],
            "archive_sha256":
                record["archive_sha256"],
            "source_commit":
                record["source_commit"],
        }

        (
            materialized
            / ".treeforge-release.json"
        ).write_text(
            json.dumps(
                marker,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

        os.replace(
            materialized,
            root,
        )

    if not _validate_cached_provider(
        name=name,
        record=record,
        root=root,
    ):
        raise ToolchainProviderError(
            "materialized Android provider "
            "failed final validation: "
            f"{name}"
        )

    return root
