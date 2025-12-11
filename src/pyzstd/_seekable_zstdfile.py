from array import array
from bisect import bisect_right
import io
from os import PathLike
from os.path import isfile
from struct import Struct
import sys
from typing import BinaryIO, ClassVar, Literal, cast
import warnings

from pyzstd import (
    _DEPRECATED_PLACEHOLDER,
    ZstdCompressor,
    ZstdDecompressor,
    _DeprecatedPlaceholder,
    _LevelOrOption,
    _Option,
    _StrOrBytesPath,
    _ZstdDict,
)

if sys.version_info < (3, 12):
    from typing_extensions import Buffer
else:
    from collections.abc import Buffer

if sys.version_info < (3, 11):
    from typing_extensions import Self
else:
    from typing import Self

__all__ = ("SeekableFormatError", "SeekableZstdFile")

_MODE_CLOSED = 0
_MODE_READ = 1
_MODE_WRITE = 2


class SeekableFormatError(Exception):
    "An error related to Zstandard Seekable Format."

    def __init__(self, msg: str) -> None:
        super().__init__("Zstandard Seekable Format error: " + msg)


__doc__ = """\
Zstandard Seekable Format (Ver 0.1.0, Apr 2017)
Square brackets are used to indicate optional fields.
All numeric fields are little-endian unless specified otherwise.
A. Seek table is a skippable frame at the end of file:
     Magic_Number  Frame_Size  [Seek_Table_Entries]  Seek_Table_Footer
     4 bytes       4 bytes     8-12 bytes each       9 bytes
     Magic_Number must be 0x184D2A5E.
B. Seek_Table_Entries:
     Compressed_Size  Decompressed_Size  [Checksum]
     4 bytes          4 bytes            4 bytes
     Checksum is optional.
C. Seek_Table_Footer:
     Number_Of_Frames  Seek_Table_Descriptor  Seekable_Magic_Number
     4 bytes           1 byte                 4 bytes
     Seekable_Magic_Number must be 0x8F92EAB1.
D. Seek_Table_Descriptor:
     Bit_number  Field_name
     7           Checksum_Flag
     6-2         Reserved_Bits  (should ensure they are set to 0)
     1-0         Unused_Bits    (should not interpret these bits)"""
__format_version__ = "0.1.0"


class _SeekTable:
    _s_2uint32 = Struct("<II")
    _s_3uint32 = Struct("<III")
    _s_footer = Struct("<IBI")

    # read_mode is True for read mode, False for write/append modes.
    def __init__(self, *, read_mode: bool) -> None:
        self._read_mode = read_mode
        self._clear_seek_table()

    def _clear_seek_table(self) -> None:
        self._has_checksum = False
        # The seek table frame size, used for append mode.
        self._seek_frame_size = 0
        # The file size, used for seeking to EOF.
        self._file_size = 0

        self._frames_count = 0
        self._full_c_size = 0
        self._full_d_size = 0

        if self._read_mode:
            # Item: cumulated_size
            # Length: frames_count + 1
            # q is int64_t. On Linux/macOS/Windows, Py_off_t is signed, so
            # ZstdFile/SeekableZstdFile use int64_t as file position/size.
            self._cumulated_c_size = array("q", [0])
            self._cumulated_d_size = array("q", [0])
        else:
            # Item: (c_size1, d_size1,
            #        c_size2, d_size2,
            #        c_size3, d_size3,
            #        ...)
            # Length: frames_count * 2
            # I is uint32_t.
            self._frames = array("I")

    def append_entry(self, compressed_size: int, decompressed_size: int) -> None:
        if compressed_size == 0:
            if decompressed_size == 0:
                # (0, 0) frame is no sense
                return
            # Impossible frame
            raise ValueError

        self._frames_count += 1
        self._full_c_size += compressed_size
        self._full_d_size += decompressed_size

        if self._read_mode:
            self._cumulated_c_size.append(self._full_c_size)
            self._cumulated_d_size.append(self._full_d_size)
        else:
            self._frames.append(compressed_size)
            self._frames.append(decompressed_size)

    # seek_to_0 is True or False.
    # In read mode, seeking to 0 is necessary.
    def load_seek_table(self, fp: BinaryIO, seek_to_0: bool) -> None:  # noqa: FBT001
        # Get file size
        fsize = fp.seek(0, 2)  # 2 is SEEK_END
        if fsize == 0:
            return
        if fsize < 17:  # 17=4+4+9
            msg = (
                "File size is less than the minimal size "
                "(17 bytes) of Zstandard Seekable Format."
            )
            raise SeekableFormatError(msg)

        # Read footer
        fp.seek(-9, 2)  # 2 is SEEK_END
        footer = fp.read(9)
        frames_number, descriptor, magic_number = self._s_footer.unpack(footer)
        # Check format
        if magic_number != 0x8F92EAB1:
            msg = (
                "The last 4 bytes of the file is not Zstandard Seekable "
                'Format Magic Number (b"\\xb1\\xea\\x92\\x8f)". '
                "SeekableZstdFile class only supports Zstandard Seekable "
                "Format file or 0-size file. To read a zstd file that is "
                "not in Zstandard Seekable Format, use ZstdFile class."
            )
            raise SeekableFormatError(msg)

        # Seek_Table_Descriptor
        self._has_checksum = descriptor & 0b10000000
        if descriptor & 0b01111100:
            msg = (
                f"In Zstandard Seekable Format version {__format_version__}, the "
                "Reserved_Bits in Seek_Table_Descriptor must be 0."
            )
            raise SeekableFormatError(msg)

        # Frame size
        entry_size = 12 if self._has_checksum else 8
        skippable_frame_size = 17 + frames_number * entry_size
        if fsize < skippable_frame_size:
            raise SeekableFormatError(
                "File size is less than expected size of the seek table frame."
            )

        # Read seek table
        fp.seek(-skippable_frame_size, 2)  # 2 is SEEK_END
        skippable_frame = fp.read(skippable_frame_size)
        skippable_magic_number, content_size = self._s_2uint32.unpack_from(
            skippable_frame, 0
        )

        # Check format
        if skippable_magic_number != 0x184D2A5E:
            msg = "Seek table frame's Magic_Number is wrong."
            raise SeekableFormatError(msg)
        if content_size != skippable_frame_size - 8:
            msg = "Seek table frame's Frame_Size is wrong."
            raise SeekableFormatError(msg)

        # No more fp operations
        if seek_to_0:
            fp.seek(0)

        # Parse seek table
        offset = 8
        for idx in range(frames_number):
            if self._has_checksum:
                compressed_size, decompressed_size, _ = self._s_3uint32.unpack_from(
                    skippable_frame, offset
                )
                offset += 12
            else:
                compressed_size, decompressed_size = self._s_2uint32.unpack_from(
                    skippable_frame, offset
                )
                offset += 8

            # Check format
            if compressed_size == 0 and decompressed_size != 0:
                msg = (
                    f"Wrong seek table. The index {idx} frame (0-based) "
                    "is 0 size, but decompressed size is non-zero, "
                    "this is impossible."
                )
                raise SeekableFormatError(msg)

            # Append to seek table
            self.append_entry(compressed_size, decompressed_size)

            # Check format
            if self._full_c_size > fsize - skippable_frame_size:
                msg = (
                    f"Wrong seek table. Since index {idx} frame (0-based), "
                    "the cumulated compressed size is greater than "
                    "file size."
                )
                raise SeekableFormatError(msg)

        # Check format
        if self._full_c_size != fsize - skippable_frame_size:
            raise SeekableFormatError("The cumulated compressed size is wrong")

        # Parsed successfully, save for future use.
        self._seek_frame_size = skippable_frame_size
        self._file_size = fsize

    # Find frame index by decompressed position
    def index_by_dpos(self, pos: int) -> int | None:
        # Array's first item is 0, so need this.
        pos = max(pos, 0)

        i = bisect_right(self._cumulated_d_size, pos)
        if i != self._frames_count + 1:
            return i
        # None means >= EOF
        return None

    def get_frame_sizes(self, i: int) -> tuple[int, int]:
        return (self._cumulated_c_size[i - 1], self._cumulated_d_size[i - 1])

    def get_full_c_size(self) -> int:
        return self._full_c_size

    def get_full_d_size(self) -> int:
        return self._full_d_size

    # Merge the seek table to max_frames frames.
    # The format allows up to 0xFFFF_FFFF frames. When frames
    # number exceeds, use this method to merge.
    def _merge_frames(self, max_frames: int) -> None:
        if self._frames_count <= max_frames:
            return

        # Clear the table
        arr = self._frames
        a, b = divmod(self._frames_count, max_frames)
        self._clear_seek_table()

        # Merge frames
        pos = 0
        for i in range(max_frames):
            # Slice length
            length = (a + (1 if i < b else 0)) * 2

            # Merge
            c_size = 0
            d_size = 0
            for j in range(pos, pos + length, 2):
                c_size += arr[j]
                d_size += arr[j + 1]
            self.append_entry(c_size, d_size)

            pos += length

    def write_seek_table(self, fp: BinaryIO) -> None:
        # Exceeded format limit
        if self._frames_count > 0xFFFFFFFF:
            # Emit a warning
            warnings.warn(
                f"SeekableZstdFile's seek table has {self._frames_count} entries, "
                "which exceeds the maximal value allowed by "
                "Zstandard Seekable Format (0xFFFFFFFF). The "
                "entries will be merged into 0xFFFFFFFF entries, "
                "this may reduce seeking performance.",
                RuntimeWarning,
                3,
            )

            # Merge frames
            self._merge_frames(0xFFFFFFFF)

        # The skippable frame
        offset = 0
        size = 17 + 8 * self._frames_count
        ba = bytearray(size)

        # Header
        self._s_2uint32.pack_into(ba, offset, 0x184D2A5E, size - 8)
        offset += 8
        # Entries
        iter_frames = iter(self._frames)
        for frame_c, frame_d in zip(iter_frames, iter_frames, strict=True):
            self._s_2uint32.pack_into(ba, offset, frame_c, frame_d)
            offset += 8
        # Footer
        self._s_footer.pack_into(ba, offset, self._frames_count, 0, 0x8F92EAB1)

        # Write
        fp.write(ba)

    @property
    def seek_frame_size(self) -> int:
        return self._seek_frame_size

    @property
    def file_size(self) -> int:
        return self._file_size

    def __len__(self) -> int:
        return self._frames_count

    def get_info(self) -> tuple[int, int, int]:
        return (self._frames_count, self._full_c_size, self._full_d_size)


class _EOFSuccess(EOFError):  # noqa: N818
    pass


class _SeekableDecompressReader(io.RawIOBase):
    def __init__(
        self, fp: BinaryIO, zstd_dict: _ZstdDict, option: _Option, read_size: int
    ) -> None:
        # Check fp readable/seekable
        if not hasattr(fp, "readable") or not hasattr(fp, "seekable"):
            raise TypeError(
                "In SeekableZstdFile's reading mode, the file object should "
                "have .readable()/.seekable() methods."
            )
        if not fp.readable():
            raise TypeError(
                "In SeekableZstdFile's reading mode, the file object should "
                "be readable."
            )
        if not fp.seekable():
            raise TypeError(
                "In SeekableZstdFile's reading mode, the file object should "
                "be seekable. If the file object is not seekable, it can be "
                "read sequentially using ZstdFile class."
            )

        self._fp = fp
        self._zstd_dict = zstd_dict
        self._option = option
        self._read_size = read_size

        # Load seek table
        self._seek_table = _SeekTable(read_mode=True)
        self._seek_table.load_seek_table(fp, seek_to_0=True)
        self._size = self._seek_table.get_full_d_size()

        self._pos = 0
        self._decompressor: ZstdDecompressor | None = ZstdDecompressor(
            self._zstd_dict, self._option
        )

    def close(self) -> None:
        self._decompressor = None
        return super().close()

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self._pos

    def _decompress(self, size: int) -> bytes:
        """
        Decompress up to size bytes.
        May return b"", in which case try again.
        Raises _EOFSuccess if EOF is reached at frame edge.
        Raises EOFError if EOF is reached elsewhere.
        """
        if self._decompressor is None:  # frame edge
            data = self._fp.read(self._read_size)
            if not data:  # EOF
                raise _EOFSuccess
        elif self._decompressor.needs_input:
            data = self._fp.read(self._read_size)
            if not data:  # EOF
                raise EOFError(
                    "Compressed file ended before the end-of-stream marker was reached"
                )
        else:
            data = self._decompressor.unused_data
            if self._decompressor.eof:  # frame edge
                self._decompressor = None
                if not data:  # may not be at EOF
                    return b""
        if self._decompressor is None:
            self._decompressor = ZstdDecompressor(self._zstd_dict, self._option)
        out = self._decompressor.decompress(data, size)
        self._pos += len(out)
        return out

    def readinto(self, b: Buffer) -> int:
        with memoryview(b) as view, view.cast("B") as byte_view:
            try:
                while True:
                    if out := self._decompress(byte_view.nbytes):
                        byte_view[: len(out)] = out
                        return len(out)
            except _EOFSuccess:
                return 0

    # If the new position is within BufferedReader's buffer,
    # this method may not be called.
    def seek(self, offset: int, whence: int = 0) -> int:
        # offset is absolute file position
        if whence == 0:  # SEEK_SET
            pass
        elif whence == 1:  # SEEK_CUR
            offset = self._pos + offset
        elif whence == 2:  # SEEK_END
            offset = self._size + offset
        else:
            raise ValueError(f"Invalid value for whence: {whence}")

        # Get new frame index
        new_frame = self._seek_table.index_by_dpos(offset)
        # offset >= EOF
        if new_frame is None:
            self._pos = self._size
            self._decompressor = None
            self._fp.seek(self._seek_table.file_size)
            return self._pos

        # Prepare to jump
        old_frame = self._seek_table.index_by_dpos(self._pos)
        c_pos, d_pos = self._seek_table.get_frame_sizes(new_frame)

        # If at P1, seeking to P2 will unnecessarily read the skippable
        # frame. So check self._fp position to skip the skippable frame.
        #       |--data1--|--skippable--|--data2--|
        # cpos:             ^P1
        # dpos:           ^P1             ^P2
        if new_frame == old_frame and offset >= self._pos and self._fp.tell() >= c_pos:
            pass
        else:
            # Jump
            self._pos = d_pos
            self._decompressor = None
            self._fp.seek(c_pos)

        # offset is bytes number to skip forward
        offset -= self._pos
        while offset > 0:
            offset -= len(self._decompress(offset))

        return self._pos

    def get_seek_table_info(self) -> tuple[int, int, int]:
        return self._seek_table.get_info()


# Compared to ZstdFile class, it's important to handle the seekable
# of underlying file object carefully. Need to check seekable in
# each situation. For example, there may be a CD-R file system that
# is seekable when reading, but not seekable when appending.
class SeekableZstdFile(io.BufferedIOBase):
    """This class can only create/write/read Zstandard Seekable Format file,
    or read 0-size file.
    It provides relatively fast seeking ability in read mode.
    """

    # The format uses uint32_t for compressed/decompressed sizes. If flush
    # block a lot, compressed_size may exceed the limit, so set a max size.
    FRAME_MAX_C_SIZE: ClassVar[int] = 2 * 1024 * 1024 * 1024
    # Zstd seekable format's example code also use 1GiB as max content size.
    FRAME_MAX_D_SIZE: ClassVar[int] = 1 * 1024 * 1024 * 1024

    FLUSH_BLOCK: ClassVar[Literal[1]] = ZstdCompressor.FLUSH_BLOCK
    FLUSH_FRAME: ClassVar[Literal[2]] = ZstdCompressor.FLUSH_FRAME

    def __init__(
        self,
        filename: _StrOrBytesPath | BinaryIO,
        mode: Literal["r", "rb", "w", "wb", "a", "ab", "x", "xb"] = "r",
        *,
        level_or_option: _LevelOrOption | _Option = None,
        zstd_dict: _ZstdDict = None,
        read_size: int | _DeprecatedPlaceholder = _DEPRECATED_PLACEHOLDER,  # type: ignore[has-type]
        write_size: int | _DeprecatedPlaceholder = _DEPRECATED_PLACEHOLDER,  # type: ignore[has-type]
        max_frame_content_size: int = 1024 * 1024 * 1024,
    ) -> None:
        """Open a Zstandard Seekable Format file in binary mode. In read mode,
        the file can be 0-size file.

        filename can be either an actual file name (given as a str, bytes, or
        PathLike object), in which case the named file is opened, or it can be
        an existing file object to read from or write to.

        mode can be "r" for reading (default), "w" for (over)writing, "x" for
        creating exclusively, or "a" for appending. These can equivalently be
        given as "rb", "wb", "xb" and "ab" respectively.

        In append mode ("a" or "ab"), filename argument can't be a file object,
        please use file path.

        Parameters
        level_or_option: When it's an int object, it represents compression
            level. When it's a dict object, it contains advanced compression
            parameters. Note, in read mode (decompression), it can only be a
            dict object, that represents decompression option. It doesn't
            support int type compression level in this case.
        zstd_dict: A ZstdDict object, pre-trained dictionary for compression /
            decompression.
        max_frame_content_size: In write/append modes (compression), when
            the uncompressed data size reaches max_frame_content_size, a frame
            is generated automatically. If the size is small, it will increase
            seeking speed, but reduce compression ratio. If the size is large,
            it will reduce seeking speed, but increase compression ratio. You
            can also manually generate a frame using f.flush(f.FLUSH_FRAME).
        """
        if read_size == _DEPRECATED_PLACEHOLDER:
            read_size = 131075
        else:
            warnings.warn(
                "pyzstd.SeekableZstdFile()'s read_size parameter is deprecated",
                DeprecationWarning,
                stacklevel=2,
            )
            read_size = cast("int", read_size)
        if write_size == _DEPRECATED_PLACEHOLDER:
            write_size = 131591
        else:
            warnings.warn(
                "pyzstd.SeekableZstdFile()'s write_size parameter is deprecated",
                DeprecationWarning,
                stacklevel=2,
            )
            write_size = cast("int", write_size)

        self._fp: BinaryIO | None = None
        self._close_fp = False
        self._mode = _MODE_CLOSED
        self._buffer = None

        if not isinstance(mode, str):
            raise TypeError("mode must be a str")
        mode = mode.removesuffix("b")  # type: ignore[assignment]  # handle rb, wb, xb, ab

        # Read or write mode
        if mode == "r":
            if not isinstance(level_or_option, (type(None), dict)):
                raise TypeError(
                    "In read mode (decompression), level_or_option argument "
                    "should be a dict object, that represents decompression "
                    "option. It doesn't support int type compression level "
                    "in this case."
                )
            if read_size <= 0:
                raise ValueError("read_size argument should > 0")
            if write_size != 131591:
                raise ValueError("write_size argument is only valid in write modes.")
            # Specified max_frame_content_size argument
            if max_frame_content_size != 1024 * 1024 * 1024:
                raise ValueError(
                    "max_frame_content_size argument is only "
                    "valid in write modes (compression)."
                )
            mode_code = _MODE_READ

        elif mode in {"w", "a", "x"}:
            if not isinstance(level_or_option, (type(None), int, dict)):
                raise TypeError(
                    "level_or_option argument should be int or dict object."
                )
            if read_size != 131075:
                raise ValueError("read_size argument is only valid in read mode.")
            if write_size <= 0:
                raise ValueError("write_size argument should > 0")
            if not (0 < max_frame_content_size <= self.FRAME_MAX_D_SIZE):
                raise ValueError(
                    "max_frame_content_size argument should be "
                    f"0 < value <= {self.FRAME_MAX_D_SIZE}, "
                    f"provided value is {max_frame_content_size}."
                )

            # For seekable format
            self._max_frame_content_size = max_frame_content_size
            self._reset_frame_sizes()
            self._seek_table: _SeekTable | None = _SeekTable(read_mode=False)

            mode_code = _MODE_WRITE
            self._compressor: ZstdCompressor | None = ZstdCompressor(
                level_or_option=level_or_option, zstd_dict=zstd_dict
            )
            self._pos = 0

            # Load seek table in append mode
            if mode == "a":
                if not isinstance(filename, (str, bytes, PathLike)):
                    raise TypeError(
                        "In append mode ('a', 'ab'), "
                        "SeekableZstdFile.__init__() method can't "
                        "accept file object as filename argument. "
                        "Please use file path (str/bytes/PathLike)."
                    )

                # Load seek table if file exists
                if isfile(filename):
                    with open(filename, "rb") as f:
                        if not hasattr(f, "seekable") or not f.seekable():
                            raise TypeError(
                                "In SeekableZstdFile's append mode "
                                "('a', 'ab'), the opened 'rb' file "
                                "object should be seekable."
                            )
                        self._seek_table.load_seek_table(f, seek_to_0=False)

        else:
            raise ValueError(f"Invalid mode: {mode!r}")

        # File object
        if isinstance(filename, (str, bytes, PathLike)):
            self._fp = cast("BinaryIO", open(filename, mode + "b"))  # noqa: SIM115
            self._close_fp = True
        elif hasattr(filename, "read") or hasattr(filename, "write"):
            self._fp = filename
        else:
            raise TypeError("filename must be a str, bytes, file or PathLike object")

        self._mode = mode_code

        if self._mode == _MODE_READ:
            raw = _SeekableDecompressReader(
                self._fp,
                zstd_dict=zstd_dict,
                option=cast("_Option", level_or_option),  # checked earlier on
                read_size=read_size,
            )
            self._buffer = io.BufferedReader(raw)

        elif mode == "a":
            if self._fp.seekable():
                self._fp.seek(self._seek_table.get_full_c_size())  # type: ignore[union-attr]
                # Necessary if the current table has many (0, 0) entries
                self._fp.truncate()
            else:
                # Add the seek table frame
                self._seek_table.append_entry(self._seek_table.seek_frame_size, 0)  # type: ignore[union-attr]
                # Emit a warning
                warnings.warn(
                    (
                        "SeekableZstdFile is opened in append mode "
                        "('a', 'ab'), but the underlying file object "
                        "is not seekable. Therefore the seek table (a "
                        "zstd skippable frame) at the end of the file "
                        "can't be overwritten. Each time open such file "
                        "in append mode, it will waste some storage "
                        f"space. {self._seek_table.seek_frame_size} bytes "  # type: ignore[union-attr]
                        "were wasted this time."
                    ),
                    RuntimeWarning,
                    2,
                )

    def _reset_frame_sizes(self) -> None:
        self._current_c_size = 0
        self._current_d_size = 0
        self._left_d_size = self._max_frame_content_size

    def _check_not_closed(self) -> None:
        if self.closed:
            raise ValueError("I/O operation on closed file")

    def _check_can_read(self) -> None:
        if not self.readable():
            raise io.UnsupportedOperation("File not open for reading")

    def _check_can_write(self) -> None:
        if not self.writable():
            raise io.UnsupportedOperation("File not open for writing")

    def close(self) -> None:
        """Flush and close the file.

        May be called more than once without error. Once the file is
        closed, any other operation on it will raise a ValueError.
        """
        if self._mode == _MODE_CLOSED:
            return

        if self._fp is None:
            return
        try:
            if self._mode == _MODE_READ:
                if getattr(self, "_buffer", None):
                    self._buffer.close()  # type: ignore[union-attr]
                    self._buffer = None
            elif self._mode == _MODE_WRITE:
                self.flush(self.FLUSH_FRAME)
                self._seek_table.write_seek_table(self._fp)  # type: ignore[union-attr]
                self._compressor = None
        finally:
            self._mode = _MODE_CLOSED
            self._seek_table = None
            try:
                if self._close_fp:
                    self._fp.close()
            finally:
                self._fp = None
                self._close_fp = False

    def write(self, data: Buffer) -> int:
        """Write a bytes-like object to the file.

        Returns the number of uncompressed bytes written, which is
        always the length of data in bytes. Note that due to buffering,
        the file on disk may not reflect the data written until .flush()
        or .close() is called.
        """
        self._check_can_write()
        # Accept any data that supports the buffer protocol.
        # And memoryview's subview is faster than slice.
        with memoryview(data) as view, view.cast("B") as byte_view:
            nbytes = byte_view.nbytes
            pos = 0

            while nbytes > 0:
                # Write size
                write_size = min(nbytes, self._left_d_size)

                # Compress & write
                compressed = self._compressor.compress(  # type: ignore[union-attr]
                    byte_view[pos : pos + write_size]
                )
                output_size = self._fp.write(compressed)  # type: ignore[union-attr]
                self._pos += write_size

                pos += write_size
                nbytes -= write_size

                # Cumulate
                self._current_c_size += output_size
                self._current_d_size += write_size
                self._left_d_size -= write_size

                # Should flush a frame
                if (
                    self._left_d_size == 0
                    or self._current_c_size >= self.FRAME_MAX_C_SIZE
                ):
                    self.flush(self.FLUSH_FRAME)

            return pos

    def flush(self, mode: Literal[1, 2] = ZstdCompressor.FLUSH_BLOCK) -> None:
        """Flush remaining data to the underlying stream.

        The mode argument can be ZstdFile.FLUSH_BLOCK, ZstdFile.FLUSH_FRAME.
        Abuse of this method will reduce compression ratio, use it only when
        necessary.

        If the program is interrupted afterwards, all data can be recovered.
        To ensure saving to disk, also need to use os.fsync(fd).

        This method does nothing in reading mode.
        """
        if self._mode == _MODE_READ:
            return

        self._check_not_closed()
        if mode not in {self.FLUSH_BLOCK, self.FLUSH_FRAME}:
            raise ValueError(
                "Invalid mode argument, expected either "
                "ZstdFile.FLUSH_FRAME or "
                "ZstdFile.FLUSH_BLOCK"
            )

        if self._compressor.last_mode != mode:  # type: ignore[union-attr]
            # Flush zstd block/frame, and write.
            compressed = self._compressor.flush(mode)  # type: ignore[union-attr]
            output_size = self._fp.write(compressed)  # type: ignore[union-attr]
            if hasattr(self._fp, "flush"):
                self._fp.flush()  # type: ignore[union-attr]

            # Cumulate
            self._current_c_size += output_size
            # self._current_d_size += 0
            # self._left_d_size -= 0

        if mode == self.FLUSH_FRAME and self._current_c_size != 0:
            # Add an entry to seek table
            self._seek_table.append_entry(self._current_c_size, self._current_d_size)  # type: ignore[union-attr]
            self._reset_frame_sizes()

    def read(self, size: int | None = -1) -> bytes:
        """Read up to size uncompressed bytes from the file.

        If size is negative or omitted, read until EOF is reached.
        Returns b"" if the file is already at EOF.
        """
        if size is None:
            size = -1
        self._check_can_read()
        return self._buffer.read(size)  # type: ignore[union-attr]

    def read1(self, size: int = -1) -> bytes:
        """Read up to size uncompressed bytes, while trying to avoid
        making multiple reads from the underlying stream. Reads up to a
        buffer's worth of data if size is negative.

        Returns b"" if the file is at EOF.
        """
        self._check_can_read()
        if size < 0:
            size = io.DEFAULT_BUFFER_SIZE
        return self._buffer.read1(size)  # type: ignore[union-attr]

    def readinto(self, b: Buffer) -> int:
        """Read bytes into b.

        Returns the number of bytes read (0 for EOF).
        """
        self._check_can_read()
        return self._buffer.readinto(b)  # type: ignore[union-attr]

    def readinto1(self, b: Buffer) -> int:
        """Read bytes into b, while trying to avoid making multiple reads
        from the underlying stream.

        Returns the number of bytes read (0 for EOF).
        """
        self._check_can_read()
        return self._buffer.readinto1(b)  # type: ignore[union-attr]

    def readline(self, size: int | None = -1) -> bytes:
        """Read a line of uncompressed bytes from the file.

        The terminating newline (if present) is retained. If size is
        non-negative, no more than size bytes will be read (in which
        case the line may be incomplete). Returns b'' if already at EOF.
        """
        self._check_can_read()
        return self._buffer.readline(size)  # type: ignore[union-attr]

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        """Change the file position.

        The new position is specified by offset, relative to the
        position indicated by whence. Possible values for whence are:

            0: start of stream (default): offset must not be negative
            1: current stream position
            2: end of stream; offset must not be positive

        Returns the new file position.

        Note that seeking is emulated, so depending on the arguments,
        this operation may be extremely slow.
        """
        self._check_can_read()
        return self._buffer.seek(offset, whence)  # type: ignore[union-attr]

    def peek(self, size: int = -1) -> bytes:
        """Return buffered data without advancing the file position.

        Always returns at least one byte of data, unless at EOF.
        The exact number of bytes returned is unspecified.
        """
        self._check_can_read()
        return self._buffer.peek(size)  # type: ignore[union-attr]

    def __iter__(self) -> Self:
        self._check_can_read()
        return self

    def __next__(self) -> bytes:
        self._check_can_read()
        if ret := self._buffer.readline():  # type: ignore[union-attr]
            return ret
        raise StopIteration

    def tell(self) -> int:
        """Return the current file position."""
        self._check_not_closed()
        if self._mode == _MODE_READ:
            return self._buffer.tell()  # type: ignore[union-attr]
        if self._mode == _MODE_WRITE:
            return self._pos
        raise RuntimeError  # impossible code path

    def fileno(self) -> int:
        """Return the file descriptor for the underlying file."""
        self._check_not_closed()
        return self._fp.fileno()  # type: ignore[union-attr]

    @property
    def name(self) -> str:
        """Return the file name for the underlying file."""
        self._check_not_closed()
        return self._fp.name  # type: ignore[union-attr]

    @property
    def closed(self) -> bool:
        """True if this file is closed."""
        return self._mode == _MODE_CLOSED

    def writable(self) -> bool:
        """Return whether the file was opened for writing."""
        self._check_not_closed()
        return self._mode == _MODE_WRITE

    def readable(self) -> bool:
        """Return whether the file was opened for reading."""
        self._check_not_closed()
        return self._mode == _MODE_READ

    def seekable(self) -> bool:
        """Return whether the file supports seeking."""
        return self.readable() and self._buffer.seekable()  # type: ignore[union-attr]

    @property
    def seek_table_info(self) -> tuple[int, int, int] | None:
        """A tuple: (frames_number, compressed_size, decompressed_size)
        1, Frames_number and compressed_size don't count the seek table
           frame (a zstd skippable frame at the end of the file).
        2, In write modes, the part of data that has not been flushed to
           frames is not counted.
        3, If the SeekableZstdFile object is closed, it's None.
        """
        if self._mode == _MODE_WRITE:
            return self._seek_table.get_info()  # type: ignore[union-attr]
        if self._mode == _MODE_READ:
            return self._buffer.raw.get_seek_table_info()  # type: ignore[union-attr]
        return None

    @staticmethod
    def is_seekable_format_file(filename: _StrOrBytesPath | BinaryIO) -> bool:
        """Check if a file is Zstandard Seekable Format file or 0-size file.

        It parses the seek table at the end of the file, returns True if no
        format error.

        filename can be either a file path (str/bytes/PathLike), or can be an
        existing file object in reading mode.
        """
        # Check argument
        if isinstance(filename, (str, bytes, PathLike)):
            fp: BinaryIO = open(filename, "rb")  # noqa: SIM115
            is_file_path = True
        elif (
            hasattr(filename, "readable")
            and filename.readable()
            and hasattr(filename, "seekable")
            and filename.seekable()
        ):
            fp = filename
            is_file_path = False
            orig_pos = fp.tell()
        else:
            raise TypeError(
                "filename argument should be a str/bytes/PathLike object, "
                "or a file object that is readable and seekable."
            )

        # Write mode uses less RAM
        table = _SeekTable(read_mode=False)
        try:
            # Read/Parse the seek table
            table.load_seek_table(fp, seek_to_0=False)
        except SeekableFormatError:
            ret = False
        else:
            ret = True
        finally:
            if is_file_path:
                fp.close()
            else:
                fp.seek(orig_pos)

        return ret
