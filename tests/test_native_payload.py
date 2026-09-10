"""Tests for native executable declared-extent validation."""

import os
import struct

import pytest

from codex_wrangler.models import CodexWranglerError
from codex_wrangler.native_payload import validate_native_payload


def test_validate_native_payload_rejects_fifo_without_blocking(tmp_path):
    """Native inspection rejects special files before opening them."""

    if not hasattr(os, "mkfifo"):
        pytest.skip("FIFO regression requires os.mkfifo")
    native_path = tmp_path / "codex"
    os.mkfifo(native_path)

    with pytest.raises(CodexWranglerError, match="not a regular file"):
        validate_native_payload(native_path)


def build_elf64(file_size: int, declared_size: int) -> bytes:
    """Return a synthetic little-endian ELF64 executable image."""

    payload = bytearray(file_size)
    identifier = b"\x7fELF\x02\x01\x01" + (b"\0" * 9)
    struct.pack_into(
        "<16sHHIQQQIHHHHHH",
        payload,
        0,
        identifier,
        2,
        0xB7,
        1,
        0,
        64,
        0,
        0,
        64,
        56,
        1,
        0,
        0,
        0,
    )
    struct.pack_into(
        "<IIQQQQQQ",
        payload,
        64,
        1,
        5,
        0,
        0,
        0,
        declared_size,
        declared_size,
        0x1000,
    )
    return bytes(payload)


def build_incident_elf64(
    file_size: int,
    segment_end: int,
    section_offset: int,
    section_count: int,
) -> bytes:
    """Return a short ELF64 prefix with the incident's declared extents."""

    payload = bytearray(build_elf64(file_size, segment_end))
    struct.pack_into("<Q", payload, 40, section_offset)
    struct.pack_into("<H", payload, 58, 64)
    struct.pack_into("<H", payload, 60, section_count)
    return bytes(payload)


def build_macho64(file_size: int, declared_size: int) -> bytes:
    """Return a synthetic little-endian thin Mach-O64 executable image."""

    payload = bytearray(file_size)
    struct.pack_into(
        "<IiiIIIII",
        payload,
        0,
        0xFEEDFACF,
        0x0100000C,
        0,
        2,
        1,
        72,
        0,
        0,
    )
    struct.pack_into(
        "<II16sQQQQiiII",
        payload,
        32,
        0x19,
        72,
        b"__TEXT\0\0\0\0\0\0\0\0\0\0",
        0,
        declared_size,
        0,
        declared_size,
        5,
        5,
        0,
        0,
    )
    return bytes(payload)


def build_fat_macho64(
    truncate_bytes: int = 0,
    *,
    uses_64_bit_entry: bool = False,
    slice_size: int = 256,
    inner_declared_size: int = 256,
    outer_padding: int = 0,
) -> bytes:
    """Return a synthetic fat Mach-O containing one Mach-O64 slice."""

    slice_offset = 64
    slice_payload = build_macho64(slice_size, inner_declared_size)
    payload = bytearray(slice_offset + len(slice_payload) + outer_padding)
    fat_magic = 0xCAFEBABF if uses_64_bit_entry else 0xCAFEBABE
    struct.pack_into(">II", payload, 0, fat_magic, 1)
    if uses_64_bit_entry:
        struct.pack_into(
            ">iiQQII",
            payload,
            8,
            0x0100000C,
            0,
            slice_offset,
            slice_size,
            2,
            0,
        )
    else:
        struct.pack_into(
            ">iiIII",
            payload,
            8,
            0x0100000C,
            0,
            slice_offset,
            slice_size,
            2,
        )
    payload[slice_offset : slice_offset + slice_size] = slice_payload
    if truncate_bytes:
        del payload[-truncate_bytes:]
    return bytes(payload)


def build_pe32_plus(file_size: int, declared_size: int) -> bytes:
    """Return a synthetic little-endian PE32+ executable image."""

    payload = bytearray(file_size)
    payload[:2] = b"MZ"
    struct.pack_into("<I", payload, 60, 64)
    struct.pack_into(
        "<4sHHIIIHH",
        payload,
        64,
        b"PE\0\0",
        0xAA64,
        1,
        0,
        0,
        0,
        240,
        0x0022,
    )
    optional_offset = 88
    struct.pack_into("<H", payload, optional_offset, 0x20B)
    struct.pack_into("<I", payload, optional_offset + 60, 512)
    struct.pack_into("<I", payload, optional_offset + 108, 16)
    section_offset = optional_offset + 240
    payload[section_offset : section_offset + 8] = b".text\0\0\0"
    struct.pack_into("<I", payload, section_offset + 8, declared_size - 512)
    struct.pack_into("<I", payload, section_offset + 12, 0x1000)
    struct.pack_into("<I", payload, section_offset + 16, declared_size - 512)
    struct.pack_into("<I", payload, section_offset + 20, 512)
    return bytes(payload)


def build_elf64_with_sections(
    file_size: int,
    section_offset: int,
    section_count: int,
) -> bytes:
    """Return an ELF64 image with a caller-sized section-header table."""

    payload = bytearray(build_elf64(file_size, file_size))
    struct.pack_into("<Q", payload, 40, section_offset)
    struct.pack_into("<H", payload, 58, 64)
    struct.pack_into("<H", payload, 60, section_count)
    return bytes(payload)


def build_macho64_with_code_signature(
    file_size: int,
    signature_offset: int,
    signature_size: int,
) -> bytes:
    """Return a Mach-O64 image with a code-signature load command."""

    payload = bytearray(build_macho64(file_size, file_size))
    struct.pack_into("<II", payload, 16, 2, 88)
    struct.pack_into(
        "<IIII",
        payload,
        104,
        0x1D,
        16,
        signature_offset,
        signature_size,
    )
    return bytes(payload)


@pytest.mark.parametrize(
    ("builder", "format_name"),
    [
        (lambda: build_elf64(256, 256), "ELF64"),
        (lambda: build_macho64(256, 256), "Mach-O64"),
        (build_fat_macho64, "fat Mach-O64"),
        (
            lambda: build_fat_macho64(uses_64_bit_entry=True),
            "fat Mach-O64",
        ),
        (lambda: build_pe32_plus(1024, 1024), "PE32+"),
    ],
)
def test_validate_native_payload_accepts_complete_images(
    tmp_path,
    builder,
    format_name,
):
    """Accept supported images whose declared file regions are present."""

    path = tmp_path / "native-payload"
    path.write_bytes(builder())

    result = validate_native_payload(path)

    assert result.format_name == format_name
    assert result.declared_extent <= result.file_size


@pytest.mark.parametrize(
    "payload",
    [
        build_elf64(128, 256),
        build_macho64(128, 256),
        build_fat_macho64(truncate_bytes=1),
        build_pe32_plus(768, 1024),
    ],
)
def test_validate_native_payload_rejects_truncated_images(tmp_path, payload):
    """Reject supported images with format-declared bytes beyond EOF."""

    path = tmp_path / "truncated-payload"
    path.write_bytes(payload)

    with pytest.raises(CodexWranglerError, match="beyond"):
        validate_native_payload(path)


@pytest.mark.parametrize(
    ("segment_end", "section_offset", "section_count"),
    [
        (214_319_772, 219_551_160, 20),
        (61_163_908, 63_249_176, 22),
    ],
)
def test_validate_native_payload_rejects_incident_elf_prefixes(
    tmp_path,
    segment_end,
    section_offset,
    section_count,
):
    """Reject small stand-ins carrying the affected binaries' ELF metadata."""

    path = tmp_path / "incident-elf-prefix"
    path.write_bytes(
        build_incident_elf64(512, segment_end, section_offset, section_count)
    )

    with pytest.raises(CodexWranglerError, match=str(segment_end)):
        validate_native_payload(path)


def test_validate_native_payload_rejects_elf_section_table_past_eof(tmp_path):
    """Reject an ELF section table overrun even when its segments fit."""

    path = tmp_path / "elf-section-table-overrun"
    path.write_bytes(build_elf64_with_sections(512, 480, 1))

    with pytest.raises(CodexWranglerError, match="section header table"):
        validate_native_payload(path)


def test_validate_native_payload_accepts_elf_nobits_section_past_eof(tmp_path):
    """Ignore an ELF NOBITS section because it has no file-backed extent."""

    payload = bytearray(build_elf64_with_sections(512, 384, 2))
    struct.pack_into(
        "<IIQQQQIIQQ",
        payload,
        448,
        0,
        8,
        0,
        0,
        100_000,
        200_000,
        0,
        0,
        1,
        0,
    )
    path = tmp_path / "elf-nobits"
    path.write_bytes(payload)

    result = validate_native_payload(path)

    assert result.format_name == "ELF64"


def test_validate_native_payload_accepts_elf_extended_counts(tmp_path):
    """Read extended ELF program and section counts from section header zero."""

    payload = bytearray(build_elf64_with_sections(512, 448, 1))
    struct.pack_into("<H", payload, 56, 0xFFFF)
    struct.pack_into("<H", payload, 60, 0)
    struct.pack_into(
        "<IIQQQQIIQQ",
        payload,
        448,
        0,
        0,
        0,
        0,
        0,
        1,
        0,
        1,
        0,
        0,
    )
    path = tmp_path / "elf-extended-counts"
    path.write_bytes(payload)

    result = validate_native_payload(path)

    assert result.declared_extent == 512


def test_validate_native_payload_rejects_macho_segment_past_fat_slice(tmp_path):
    """Reject a Mach-O segment beyond its fat slice even if outer bytes exist."""

    path = tmp_path / "fat-inner-overrun"
    path.write_bytes(
        build_fat_macho64(
            slice_size=200,
            inner_declared_size=220,
            outer_padding=64,
        )
    )

    with pytest.raises(CodexWranglerError, match="Mach-O slice"):
        validate_native_payload(path)


def test_validate_native_payload_rejects_undersized_macho_section_table(tmp_path):
    """Reject LC_SEGMENT_64 when cmdsize cannot contain its declared sections."""

    payload = bytearray(build_macho64(256, 256))
    struct.pack_into("<I", payload, 96, 1)
    path = tmp_path / "macho-undersized-sections"
    path.write_bytes(payload)

    with pytest.raises(CodexWranglerError, match="too small for its section table"):
        validate_native_payload(path)


def test_validate_native_payload_rejects_macho_code_signature_past_eof(tmp_path):
    """Reject a Mach-O code-signature blob whose declared bytes are absent."""

    path = tmp_path / "macho-signature-overrun"
    path.write_bytes(build_macho64_with_code_signature(256, 240, 32))

    with pytest.raises(CodexWranglerError, match="code signature"):
        validate_native_payload(path)


def test_validate_native_payload_rejects_pe_certificate_table_past_eof(tmp_path):
    """Reject a PE certificate table whose declared bytes are absent."""

    payload = bytearray(build_pe32_plus(1024, 1024))
    struct.pack_into("<II", payload, 232, 960, 128)
    path = tmp_path / "pe-certificate-overrun"
    path.write_bytes(payload)

    with pytest.raises(CodexWranglerError, match="certificate table"):
        validate_native_payload(path)


def test_validate_native_payload_accepts_pe_zero_raw_bss_section(tmp_path):
    """Ignore a PE section without raw on-disk data."""

    payload = bytearray(build_pe32_plus(512, 512))
    struct.pack_into("<II", payload, 344, 0, 100_000)
    path = tmp_path / "pe-bss"
    path.write_bytes(payload)

    result = validate_native_payload(path)

    assert result.format_name == "PE32+"


def test_validate_native_payload_rejects_unknown_format(tmp_path):
    """Reject an expected native path that does not contain a native image."""

    path = tmp_path / "not-native"
    path.write_bytes(b"not a native executable")

    with pytest.raises(CodexWranglerError, match="unsupported or invalid"):
        validate_native_payload(path)
