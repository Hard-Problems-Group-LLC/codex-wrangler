"""Validate that supported native executable payloads are not truncated."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import stat
import struct
from typing import BinaryIO

from .models import CodexWranglerError


@dataclass(frozen=True)
class NativePayloadValidation:
    """Describe one structurally complete native executable payload."""

    format_name: str
    file_size: int
    declared_extent: int


_MACHO64_MAGICS = {
    b"\xcf\xfa\xed\xfe": "<",
    b"\xfe\xed\xfa\xcf": ">",
}
_FAT_MACHO_MAGICS = {
    b"\xca\xfe\xba\xbe": (">", False),
    b"\xca\xfe\xba\xbf": (">", True),
}


def _require_extent(
    path: Path,
    file_size: int,
    offset: int,
    length: int,
    label: str,
) -> int:
    """Return an extent end or reject a region that reaches past EOF."""

    end = offset + length
    if offset < 0 or length < 0 or end > file_size:
        raise CodexWranglerError(
            "Native executable {} declares {} ending at byte {}, beyond its "
            "{}-byte file.".format(path, label, end, file_size)
        )
    return end


def _read_exact(
    handle: BinaryIO,
    path: Path,
    file_size: int,
    offset: int,
    length: int,
    label: str,
) -> bytes:
    """Read a bounded executable region and reject concurrent truncation."""

    _require_extent(path, file_size, offset, length, label)
    handle.seek(offset)
    payload = handle.read(length)
    if len(payload) != length:
        raise CodexWranglerError(
            "Native executable {} changed while reading {}.".format(path, label)
        )
    return payload


def _validate_elf64(
    handle: BinaryIO,
    path: Path,
    file_size: int,
) -> NativePayloadValidation:
    """Validate ELF64 header tables and file-backed segment and section extents."""

    header = _read_exact(handle, path, file_size, 0, 64, "ELF64 header")
    if header[4] != 2:
        raise CodexWranglerError(
            "Native executable {} is not an ELF64 payload.".format(path)
        )
    if header[5] == 1:
        byte_order = "<"
    elif header[5] == 2:
        byte_order = ">"
    else:
        raise CodexWranglerError(
            "Native executable {} has an unsupported ELF byte order.".format(path)
        )

    fields = struct.unpack(byte_order + "16sHHIQQQIHHHHHH", header)
    program_offset = fields[5]
    section_offset = fields[6]
    header_size = fields[8]
    program_entry_size = fields[9]
    program_count = fields[10]
    section_entry_size = fields[11]
    section_count = fields[12]

    if header_size < 64:
        raise CodexWranglerError(
            "Native executable {} has an invalid ELF64 header size.".format(path)
        )
    declared_extent = _require_extent(path, file_size, 0, header_size, "ELF64 header")
    section_zero = None
    if program_count == 0xFFFF or (section_offset != 0 and section_count == 0):
        if section_offset == 0 or section_entry_size < 64:
            raise CodexWranglerError(
                "Native executable {} has unreadable extended ELF64 header "
                "counts.".format(path)
            )
        section_zero = struct.unpack(
            byte_order + "IIQQQQIIQQ",
            _read_exact(
                handle,
                path,
                file_size,
                section_offset,
                64,
                "ELF64 section header 0",
            ),
        )
        if program_count == 0xFFFF:
            program_count = section_zero[7]
        if section_count == 0:
            section_count = section_zero[5]

    if program_count == 0:
        raise CodexWranglerError(
            "Native executable {} has no ELF64 program headers.".format(path)
        )
    if program_entry_size < 56:
        raise CodexWranglerError(
            "Native executable {} has undersized ELF64 program headers.".format(path)
        )

    program_table_end = _require_extent(
        path,
        file_size,
        program_offset,
        program_entry_size * program_count,
        "ELF64 program header table",
    )
    declared_extent = max(declared_extent, program_table_end)
    for index in range(program_count):
        entry_offset = program_offset + (index * program_entry_size)
        entry = _read_exact(
            handle,
            path,
            file_size,
            entry_offset,
            56,
            "ELF64 program header {}".format(index),
        )
        segment_type, _, segment_offset, _, _, segment_size, _, _ = struct.unpack(
            byte_order + "IIQQQQQQ", entry
        )
        if segment_type != 0 and segment_size:
            declared_extent = max(
                declared_extent,
                _require_extent(
                    path,
                    file_size,
                    segment_offset,
                    segment_size,
                    "ELF64 program segment {}".format(index),
                ),
            )

    if section_offset == 0 and section_count == 0:
        return NativePayloadValidation("ELF64", file_size, declared_extent)
    if section_offset == 0 or section_count == 0:
        raise CodexWranglerError(
            "Native executable {} has inconsistent ELF64 section metadata.".format(path)
        )
    if section_entry_size < 64:
        raise CodexWranglerError(
            "Native executable {} has undersized ELF64 section headers.".format(path)
        )

    section_table_end = _require_extent(
        path,
        file_size,
        section_offset,
        section_entry_size * section_count,
        "ELF64 section header table",
    )
    declared_extent = max(declared_extent, section_table_end)
    for index in range(section_count):
        entry_offset = section_offset + (index * section_entry_size)
        entry = _read_exact(
            handle,
            path,
            file_size,
            entry_offset,
            64,
            "ELF64 section header {}".format(index),
        )
        fields = struct.unpack(byte_order + "IIQQQQIIQQ", entry)
        section_type = fields[1]
        section_file_offset = fields[4]
        section_size = fields[5]
        if section_type not in (0, 8) and section_size:
            declared_extent = max(
                declared_extent,
                _require_extent(
                    path,
                    file_size,
                    section_file_offset,
                    section_size,
                    "ELF64 section {}".format(index),
                ),
            )
    return NativePayloadValidation("ELF64", file_size, declared_extent)


def _require_slice_extent(
    path: Path,
    slice_size: int,
    offset: int,
    length: int,
    label: str,
) -> int:
    """Validate a file region relative to one Mach-O architecture slice."""

    end = offset + length
    if offset < 0 or length < 0 or end > slice_size:
        raise CodexWranglerError(
            "Native executable {} declares {} ending at slice byte {}, beyond "
            "its {}-byte Mach-O slice.".format(path, label, end, slice_size)
        )
    return end


def _validate_macho64_slice(
    handle: BinaryIO,
    path: Path,
    file_size: int,
    slice_offset: int,
    slice_size: int,
) -> int:
    """Validate one thin Mach-O64 image and return its relative declared extent."""

    _require_extent(
        path, file_size, slice_offset, slice_size, "Mach-O architecture slice"
    )
    header = _read_exact(
        handle,
        path,
        file_size,
        slice_offset,
        32,
        "Mach-O64 header",
    )
    byte_order = _MACHO64_MAGICS.get(header[:4])
    if byte_order is None:
        raise CodexWranglerError(
            "Native executable {} contains a non-Mach-O64 architecture "
            "slice.".format(path)
        )
    command_count, command_bytes = struct.unpack_from(byte_order + "II", header, 16)
    commands_end = _require_slice_extent(
        path, slice_size, 32, command_bytes, "Mach-O64 load command table"
    )
    if command_count == 0 or command_count > command_bytes // 8:
        raise CodexWranglerError(
            "Native executable {} has invalid Mach-O64 load command "
            "metadata.".format(path)
        )

    declared_extent = commands_end
    command_offset = 32
    segment_count = 0
    for index in range(command_count):
        command_header = _read_exact(
            handle,
            path,
            file_size,
            slice_offset + command_offset,
            8,
            "Mach-O64 load command {}".format(index),
        )
        command, command_size = struct.unpack(byte_order + "II", command_header)
        if command_size < 8 or command_size % 8 != 0:
            raise CodexWranglerError(
                "Native executable {} has an invalid Mach-O64 load command "
                "size at command {}.".format(path, index)
            )
        command_end = _require_slice_extent(
            path,
            commands_end,
            command_offset,
            command_size,
            "Mach-O64 load command {}".format(index),
        )
        if command == 0x19:
            if command_size < 72:
                raise CodexWranglerError(
                    "Native executable {} has an undersized Mach-O64 segment "
                    "command.".format(path)
                )
            segment = _read_exact(
                handle,
                path,
                file_size,
                slice_offset + command_offset,
                72,
                "Mach-O64 segment command {}".format(index),
            )
            section_count = struct.unpack_from(byte_order + "I", segment, 64)[0]
            minimum_command_size = 72 + (section_count * 80)
            if command_size < minimum_command_size:
                raise CodexWranglerError(
                    "Native executable {} has a Mach-O64 segment command too "
                    "small for its section table.".format(path)
                )
            segment_offset, segment_size = struct.unpack_from(
                byte_order + "QQ", segment, 40
            )
            if segment_size:
                declared_extent = max(
                    declared_extent,
                    _require_slice_extent(
                        path,
                        slice_size,
                        segment_offset,
                        segment_size,
                        "Mach-O64 segment {}".format(index),
                    ),
                )
            segment_count += 1
        elif command == 0x1D:
            if command_size < 16:
                raise CodexWranglerError(
                    "Native executable {} has an undersized Mach-O64 code "
                    "signature command.".format(path)
                )
            command_payload = _read_exact(
                handle,
                path,
                file_size,
                slice_offset + command_offset,
                16,
                "Mach-O64 code signature command",
            )
            signature_offset, signature_size = struct.unpack_from(
                byte_order + "II", command_payload, 8
            )
            if signature_size:
                declared_extent = max(
                    declared_extent,
                    _require_slice_extent(
                        path,
                        slice_size,
                        signature_offset,
                        signature_size,
                        "Mach-O64 code signature",
                    ),
                )
        command_offset = command_end

    if command_offset != commands_end or segment_count == 0:
        raise CodexWranglerError(
            "Native executable {} has inconsistent Mach-O64 load commands.".format(path)
        )
    return declared_extent


def _validate_macho64(
    handle: BinaryIO,
    path: Path,
    file_size: int,
    prefix: bytes,
) -> NativePayloadValidation:
    """Validate a thin or fat Mach-O file containing only 64-bit images."""

    if prefix in _MACHO64_MAGICS:
        declared_extent = _validate_macho64_slice(handle, path, file_size, 0, file_size)
        return NativePayloadValidation("Mach-O64", file_size, declared_extent)

    byte_order, uses_64_bit_entries = _FAT_MACHO_MAGICS[prefix]
    fat_header = _read_exact(handle, path, file_size, 0, 8, "fat Mach-O header")
    architecture_count = struct.unpack_from(byte_order + "I", fat_header, 4)[0]
    entry_size = 32 if uses_64_bit_entries else 20
    if architecture_count == 0:
        raise CodexWranglerError(
            "Native executable {} has no fat Mach-O architectures.".format(path)
        )
    table_end = _require_extent(
        path,
        file_size,
        8,
        entry_size * architecture_count,
        "fat Mach-O architecture table",
    )
    declared_extent = table_end
    for index in range(architecture_count):
        entry = _read_exact(
            handle,
            path,
            file_size,
            8 + (index * entry_size),
            entry_size,
            "fat Mach-O architecture {}".format(index),
        )
        if uses_64_bit_entries:
            architecture_offset, architecture_size = struct.unpack_from(
                byte_order + "QQ", entry, 8
            )
        else:
            architecture_offset, architecture_size = struct.unpack_from(
                byte_order + "II", entry, 8
            )
        if architecture_size == 0:
            raise CodexWranglerError(
                "Native executable {} has an empty fat Mach-O architecture "
                "{}.".format(path, index)
            )
        architecture_end = _require_extent(
            path,
            file_size,
            architecture_offset,
            architecture_size,
            "fat Mach-O architecture {}".format(index),
        )
        slice_extent = _validate_macho64_slice(
            handle,
            path,
            file_size,
            architecture_offset,
            architecture_size,
        )
        declared_extent = max(
            declared_extent,
            architecture_end,
            architecture_offset + slice_extent,
        )
    return NativePayloadValidation("fat Mach-O64", file_size, declared_extent)


def _validate_pe32_plus(
    handle: BinaryIO,
    path: Path,
    file_size: int,
) -> NativePayloadValidation:
    """Validate PE32+ headers, raw sections, and certificate table extents."""

    dos_header = _read_exact(handle, path, file_size, 0, 64, "PE DOS header")
    pe_offset = struct.unpack_from("<I", dos_header, 60)[0]
    coff_header = _read_exact(
        handle, path, file_size, pe_offset, 24, "PE signature and COFF header"
    )
    if coff_header[:4] != b"PE\0\0":
        raise CodexWranglerError(
            "Native executable {} has an invalid PE signature.".format(path)
        )
    section_count = struct.unpack_from("<H", coff_header, 6)[0]
    optional_header_size = struct.unpack_from("<H", coff_header, 20)[0]
    if section_count == 0 or optional_header_size < 112:
        raise CodexWranglerError(
            "Native executable {} has invalid PE32+ header metadata.".format(path)
        )

    optional_offset = pe_offset + 24
    optional_header = _read_exact(
        handle,
        path,
        file_size,
        optional_offset,
        optional_header_size,
        "PE32+ optional header",
    )
    if struct.unpack_from("<H", optional_header, 0)[0] != 0x20B:
        raise CodexWranglerError(
            "Native executable {} is not a PE32+ payload.".format(path)
        )

    directory_count = struct.unpack_from("<I", optional_header, 108)[0]
    directory_end = 112 + (directory_count * 8)
    if directory_end > optional_header_size:
        raise CodexWranglerError(
            "Native executable {} has a truncated PE32+ data directory.".format(path)
        )

    section_table_offset = optional_offset + optional_header_size
    section_table_end = _require_extent(
        path,
        file_size,
        section_table_offset,
        40 * section_count,
        "PE32+ section table",
    )
    size_of_headers = struct.unpack_from("<I", optional_header, 60)[0]
    if size_of_headers < section_table_end:
        raise CodexWranglerError(
            "Native executable {} has a PE32+ SizeOfHeaders smaller than its "
            "section table.".format(path)
        )
    declared_extent = max(
        section_table_end,
        _require_extent(path, file_size, 0, size_of_headers, "PE32+ headers"),
    )
    for index in range(section_count):
        section = _read_exact(
            handle,
            path,
            file_size,
            section_table_offset + (index * 40),
            40,
            "PE32+ section header {}".format(index),
        )
        raw_size, raw_offset = struct.unpack_from("<II", section, 16)
        if raw_size:
            declared_extent = max(
                declared_extent,
                _require_extent(
                    path,
                    file_size,
                    raw_offset,
                    raw_size,
                    "PE32+ section {}".format(index),
                ),
            )

    if directory_count > 4:
        certificate_offset, certificate_size = struct.unpack_from(
            "<II", optional_header, 112 + (4 * 8)
        )
        if certificate_size:
            declared_extent = max(
                declared_extent,
                _require_extent(
                    path,
                    file_size,
                    certificate_offset,
                    certificate_size,
                    "PE32+ certificate table",
                ),
            )
    return NativePayloadValidation("PE32+", file_size, declared_extent)


def validate_native_payload(path: Path) -> NativePayloadValidation:
    """Validate one regular 64-bit executable without blocking on special files."""

    descriptor = -1
    try:
        mode = os.lstat(path).st_mode
        if not stat.S_ISREG(mode):
            raise CodexWranglerError(
                "Native executable path is not a regular file: {}".format(path)
            )
        flags = (
            os.O_RDONLY
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
        )
        descriptor = os.open(path, flags)
        file_stat = os.fstat(descriptor)
        if not stat.S_ISREG(file_stat.st_mode):
            raise CodexWranglerError(
                "Native executable path is not a regular file: {}".format(path)
            )
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = -1
            file_size = file_stat.st_size
            prefix = _read_exact(
                handle, path, file_size, 0, 4, "native executable magic"
            )
            if prefix == b"\x7fELF":
                return _validate_elf64(handle, path, file_size)
            if prefix in _MACHO64_MAGICS or prefix in _FAT_MACHO_MAGICS:
                return _validate_macho64(handle, path, file_size, prefix)
            if prefix[:2] == b"MZ":
                return _validate_pe32_plus(handle, path, file_size)
    except OSError as exc:
        raise CodexWranglerError(
            "Failed to read native executable {}: {}".format(path, exc)
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)

    raise CodexWranglerError(
        "Native executable {} has an unsupported or invalid file format.".format(path)
    )
