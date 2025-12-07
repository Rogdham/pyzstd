from collections.abc import Callable, Mapping
from enum import IntEnum
from io import TextIOWrapper
from os import PathLike
import sys
from typing import (
    BinaryIO,
    ClassVar,
    Literal,
    NamedTuple,
    NoReturn,
    TypeAlias,
    cast,
    overload,
)
import warnings

if sys.version_info < (3, 14):
    from backports import zstd
else:
    from compression import zstd

if sys.version_info < (3, 13):
    from typing_extensions import deprecated
else:
    from warnings import deprecated

if sys.version_info < (3, 12):
    from typing_extensions import Buffer
else:
    from collections.abc import Buffer

from pyzstd._version import __version__  # noqa: F401

__doc__ = """\
Python bindings to Zstandard (zstd) compression library, the API style is
similar to Python's bz2/lzma/zlib modules.

Command line interface of this module: python -m pyzstd --help

Documentation: https://pyzstd.readthedocs.io
GitHub: https://github.com/Rogdham/pyzstd
PyPI: https://pypi.org/project/pyzstd"""

__all__ = (
    "CParameter",
    "DParameter",
    "EndlessZstdDecompressor",
    "RichMemZstdCompressor",
    "SeekableFormatError",
    "SeekableZstdFile",
    "Strategy",
    "ZstdCompressor",
    "ZstdDecompressor",
    "ZstdDict",
    "ZstdError",
    "ZstdFile",
    "compress",
    "compress_stream",
    "compressionLevel_values",
    "decompress",
    "decompress_stream",
    "finalize_dict",
    "get_frame_info",
    "get_frame_size",
    "open",
    "richmem_compress",
    "train_dict",
    "zstd_support_multithread",
    "zstd_version",
    "zstd_version_info",
)


class _DeprecatedPlaceholder:
    def __repr__(self) -> str:
        return "<DEPRECATED>"


_DEPRECATED_PLACEHOLDER = _DeprecatedPlaceholder()


Strategy = zstd.Strategy
ZstdError = zstd.ZstdError
ZstdDict = zstd.ZstdDict
train_dict = zstd.train_dict
finalize_dict = zstd.finalize_dict
get_frame_info = zstd.get_frame_info
get_frame_size = zstd.get_frame_size
zstd_version = zstd.zstd_version
zstd_version_info = zstd.zstd_version_info


class CParameter(IntEnum):
    """Compression parameters"""

    compressionLevel = zstd.CompressionParameter.compression_level  # noqa: N815
    windowLog = zstd.CompressionParameter.window_log  # noqa: N815
    hashLog = zstd.CompressionParameter.hash_log  # noqa: N815
    chainLog = zstd.CompressionParameter.chain_log  # noqa: N815
    searchLog = zstd.CompressionParameter.search_log  # noqa: N815
    minMatch = zstd.CompressionParameter.min_match  # noqa: N815
    targetLength = zstd.CompressionParameter.target_length  # noqa: N815
    strategy = zstd.CompressionParameter.strategy
    targetCBlockSize = 130  # not part of PEP-784 # noqa: N815

    enableLongDistanceMatching = zstd.CompressionParameter.enable_long_distance_matching  # noqa: N815
    ldmHashLog = zstd.CompressionParameter.ldm_hash_log  # noqa: N815
    ldmMinMatch = zstd.CompressionParameter.ldm_min_match  # noqa: N815
    ldmBucketSizeLog = zstd.CompressionParameter.ldm_bucket_size_log  # noqa: N815
    ldmHashRateLog = zstd.CompressionParameter.ldm_hash_rate_log  # noqa: N815

    contentSizeFlag = zstd.CompressionParameter.content_size_flag  # noqa: N815
    checksumFlag = zstd.CompressionParameter.checksum_flag  # noqa: N815
    dictIDFlag = zstd.CompressionParameter.dict_id_flag  # noqa: N815

    nbWorkers = zstd.CompressionParameter.nb_workers  # noqa: N815
    jobSize = zstd.CompressionParameter.job_size  # noqa: N815
    overlapLog = zstd.CompressionParameter.overlap_log  # noqa: N815

    def bounds(self) -> tuple[int, int]:
        """Return lower and upper bounds of a compression parameter, both inclusive."""
        return zstd.CompressionParameter(self).bounds()


class DParameter(IntEnum):
    """Decompression parameters"""

    windowLogMax = zstd.DecompressionParameter.window_log_max  # noqa: N815

    def bounds(self) -> tuple[int, int]:
        """Return lower and upper bounds of a decompression parameter, both inclusive."""
        return zstd.DecompressionParameter(self).bounds()


_LevelOrOption: TypeAlias = int | Mapping[int, int] | None
_Option: TypeAlias = Mapping[int, int] | None
_ZstdDict: TypeAlias = ZstdDict | tuple[ZstdDict, int] | None
_StrOrBytesPath: TypeAlias = str | bytes | PathLike[str] | PathLike[bytes]


def _convert_level_or_option(
    level_or_option: _LevelOrOption | _Option, mode: str
) -> Mapping[int, int] | None:
    """Transform pyzstd params into PEP-784 `options` param"""
    if not isinstance(mode, str):
        raise TypeError(f"Invalid mode type: {mode}")
    read_mode = mode.startswith("r")
    if isinstance(level_or_option, int):
        if read_mode:
            raise TypeError(
                "In read mode (decompression), level_or_option argument "
                "should be a dict object, that represents decompression "
                "option. It doesn't support int type compression level "
                "in this case."
            )
        return {
            CParameter.compressionLevel: level_or_option,
        }
    if level_or_option is not None:
        invalid_class = CParameter if read_mode else DParameter
        for key in level_or_option:
            if isinstance(key, invalid_class):
                raise TypeError(
                    "Key of compression option dict should "
                    f"NOT be {invalid_class.__name__}."
                )
    return level_or_option


class ZstdCompressor:
    """A streaming compressor. Thread-safe at method level."""

    CONTINUE: ClassVar[Literal[0]] = zstd.ZstdCompressor.CONTINUE
    """Used for mode parameter in .compress() method.

    Collect more data, encoder decides when to output compressed result, for optimal
    compression ratio. Usually used for traditional streaming compression.
    """

    FLUSH_BLOCK: ClassVar[Literal[1]] = zstd.ZstdCompressor.FLUSH_BLOCK
    """Used for mode parameter in .compress(), .flush() methods.

    Flush any remaining data, but don't close the current frame. Usually used for
    communication scenarios.

    If there is data, it creates at least one new block, that can be decoded
    immediately on reception. If no remaining data, no block is created, return b''.

    Note: Abuse of this mode will reduce compression ratio. Use it only when
    necessary.
    """

    FLUSH_FRAME: ClassVar[Literal[2]] = zstd.ZstdCompressor.FLUSH_FRAME
    """Used for mode parameter in .compress(), .flush() methods.

    Flush any remaining data, and close the current frame. Usually used for
    traditional flush.

    Since zstd data consists of one or more independent frames, data can still be
    provided after a frame is closed.

    Note: Abuse of this mode will reduce compression ratio, and some programs can
    only decompress single frame data. Use it only when necessary.
    """

    def __init__(
        self, level_or_option: _LevelOrOption = None, zstd_dict: _ZstdDict = None
    ) -> None:
        """Initialize a ZstdCompressor object.

        Parameters
        level_or_option: When it's an int object, it represents the compression level.
                         When it's a dict object, it contains advanced compression
                         parameters.
        zstd_dict:       A ZstdDict object, pre-trained zstd dictionary.
        """
        zstd_dict = cast(
            "ZstdDict | None", zstd_dict
        )  # https://github.com/python/typeshed/pull/15113
        self._compressor = zstd.ZstdCompressor(
            options=_convert_level_or_option(level_or_option, "w"), zstd_dict=zstd_dict
        )

    def compress(
        self, data: Buffer, mode: Literal[0, 1, 2] = zstd.ZstdCompressor.CONTINUE
    ) -> bytes:
        """Provide data to the compressor object.
        Return a chunk of compressed data if possible, or b'' otherwise.

        Parameters
        data: A bytes-like object, data to be compressed.
        mode: Can be these 3 values .CONTINUE, .FLUSH_BLOCK, .FLUSH_FRAME.
        """
        return self._compressor.compress(data, mode)

    def flush(self, mode: Literal[1, 2] = zstd.ZstdCompressor.FLUSH_FRAME) -> bytes:
        """Flush any remaining data in internal buffer.

        Since zstd data consists of one or more independent frames, the compressor
        object can still be used after this method is called.

        Parameter
        mode: Can be these 2 values .FLUSH_FRAME, .FLUSH_BLOCK.
        """
        return self._compressor.flush(mode)

    def _set_pledged_input_size(self, size: int | None) -> None:
        """*This is an undocumented method, because it may be used incorrectly.*

        Set uncompressed content size of a frame, the size will be written into the
        frame header.
        1, If called when (.last_mode != .FLUSH_FRAME), a RuntimeError will be raised.
        2, If the actual size doesn't match the value, a ZstdError will be raised, and
           the last compressed chunk is likely to be lost.
        3, The size is only valid for one frame, then it restores to "unknown size".

        Parameter
        size: Uncompressed content size of a frame, None means "unknown size".
        """
        return self._compressor.set_pledged_input_size(size)

    @property
    def last_mode(self) -> Literal[0, 1, 2]:
        """The last mode used to this compressor object, its value can be .CONTINUE,
        .FLUSH_BLOCK, .FLUSH_FRAME. Initialized to .FLUSH_FRAME.

        It can be used to get the current state of a compressor, such as, data flushed,
        a frame ended.
        """
        return self._compressor.last_mode

    def __reduce__(self) -> NoReturn:
        raise TypeError(f"Cannot pickle {type(self)} object.")


class ZstdDecompressor:
    """A streaming decompressor, it stops after a frame is decompressed.
    Thread-safe at method level."""

    def __init__(self, zstd_dict: _ZstdDict = None, option: _Option = None) -> None:
        """Initialize a ZstdDecompressor object.

        Parameters
        zstd_dict: A ZstdDict object, pre-trained zstd dictionary.
        option:    A dict object that contains advanced decompression parameters.
        """
        zstd_dict = cast(
            "ZstdDict | None", zstd_dict
        )  # https://github.com/python/typeshed/pull/15113
        self._decompressor = zstd.ZstdDecompressor(
            zstd_dict=zstd_dict, options=_convert_level_or_option(option, "r")
        )

    def decompress(self, data: Buffer, max_length: int = -1) -> bytes:
        """Decompress data, return a chunk of decompressed data if possible, or b''
        otherwise.

        It stops after a frame is decompressed.

        Parameters
        data:       A bytes-like object, zstd data to be decompressed.
        max_length: Maximum size of returned data. When it is negative, the size of
                    output buffer is unlimited. When it is nonnegative, returns at
                    most max_length bytes of decompressed data.
        """
        return self._decompressor.decompress(data, max_length)

    @property
    def eof(self) -> bool:
        """True means the end of the first frame has been reached. If decompress data
        after that, an EOFError exception will be raised."""
        return self._decompressor.eof

    @property
    def needs_input(self) -> bool:
        """If the max_length output limit in .decompress() method has been reached, and
        the decompressor has (or may has) unconsumed input data, it will be set to
        False. In this case, pass b'' to .decompress() method may output further data.
        """
        return self._decompressor.needs_input

    @property
    def unused_data(self) -> bytes:
        """A bytes object. When ZstdDecompressor object stops after a frame is
        decompressed, unused input data after the frame. Otherwise this will be b''."""
        return self._decompressor.unused_data

    def __reduce__(self) -> NoReturn:
        raise TypeError(f"Cannot pickle {type(self)} object.")


class EndlessZstdDecompressor:
    """A streaming decompressor, accepts multiple concatenated frames.
    Thread-safe at method level."""

    def __init__(self, zstd_dict: _ZstdDict = None, option: _Option = None) -> None:
        """Initialize an EndlessZstdDecompressor object.

        Parameters
        zstd_dict: A ZstdDict object, pre-trained zstd dictionary.
        option:    A dict object that contains advanced decompression parameters.
        """
        self._zstd_dict = cast(
            "ZstdDict | None", zstd_dict
        )  # https://github.com/python/typeshed/pull/15113
        self._options = _convert_level_or_option(option, "r")
        self._reset()

    def _reset(self, data: bytes = b"") -> None:
        self._decompressor = zstd.ZstdDecompressor(
            zstd_dict=self._zstd_dict, options=self._options
        )
        self._buffer = data
        self._at_frame_edge = not data

    def decompress(self, data: Buffer, max_length: int = -1) -> bytes:
        """Decompress data, return a chunk of decompressed data if possible, or b''
        otherwise.

        Parameters
        data:       A bytes-like object, zstd data to be decompressed.
        max_length: Maximum size of returned data. When it is negative, the size of
                    output buffer is unlimited. When it is nonnegative, returns at
                    most max_length bytes of decompressed data.
        """
        if not isinstance(data, bytes) or not isinstance(max_length, int):
            raise TypeError
        self._buffer += data
        self._at_frame_edge &= not self._buffer
        out = b""
        while True:
            try:
                out += self._decompressor.decompress(self._buffer, max_length)
            except ZstdError:
                self._reset()
                raise
            if self._decompressor.eof:
                self._reset(self._decompressor.unused_data)
                max_length -= len(out)
            else:
                self._buffer = b""
                break
        return out

    @property
    def at_frame_edge(self) -> bool:
        """True when both the input and output streams are at a frame edge, means a
        frame is completely decoded and fully flushed, or the decompressor just be
        initialized.

        This flag could be used to check data integrity in some cases.
        """
        return self._at_frame_edge

    @property
    def needs_input(self) -> bool:
        """If the max_length output limit in .decompress() method has been reached, and
        the decompressor has (or may has) unconsumed input data, it will be set to
        False. In this case, pass b'' to .decompress() method may output further data.
        """
        return not self._buffer and (
            self._at_frame_edge or self._decompressor.needs_input
        )

    def __reduce__(self) -> NoReturn:
        raise TypeError(f"Cannot pickle {type(self)} object.")


def compress(
    data: Buffer, level_or_option: _LevelOrOption = None, zstd_dict: _ZstdDict = None
) -> bytes:
    """Compress a block of data, return a bytes object.

    Compressing b'' will get an empty content frame (9 bytes or more).

    Parameters
    data:            A bytes-like object, data to be compressed.
    level_or_option: When it's an int object, it represents compression level.
                     When it's a dict object, it contains advanced compression
                     parameters.
    zstd_dict:       A ZstdDict object, pre-trained dictionary for compression.
    """
    zstd_dict = cast(
        "ZstdDict | None", zstd_dict
    )  # https://github.com/python/typeshed/pull/15113
    return zstd.compress(
        data,
        options=_convert_level_or_option(level_or_option, "w"),
        zstd_dict=zstd_dict,
    )


def decompress(
    data: Buffer, zstd_dict: _ZstdDict = None, option: _Option = None
) -> bytes:
    """Decompress a zstd data, return a bytes object.

    Support multiple concatenated frames.

    Parameters
    data:      A bytes-like object, compressed zstd data.
    zstd_dict: A ZstdDict object, pre-trained zstd dictionary.
    option:    A dict object, contains advanced decompression parameters.
    """
    zstd_dict = cast(
        "ZstdDict | None", zstd_dict
    )  # https://github.com/python/typeshed/pull/15113
    return zstd.decompress(
        data, options=_convert_level_or_option(option, "r"), zstd_dict=zstd_dict
    )


@deprecated(
    "See https://pyzstd.readthedocs.io/en/stable/deprecated.html for alternatives to pyzstd.RichMemZstdCompressor"
)
class RichMemZstdCompressor:
    def __init__(
        self, level_or_option: _LevelOrOption = None, zstd_dict: _ZstdDict = None
    ) -> None:
        self._options = _convert_level_or_option(level_or_option, "w")
        self._zstd_dict = cast(
            "ZstdDict | None", zstd_dict
        )  # https://github.com/python/typeshed/pull/15113

    def compress(self, data: Buffer) -> bytes:
        return zstd.compress(data, options=self._options, zstd_dict=self._zstd_dict)

    def __reduce__(self) -> NoReturn:
        raise TypeError(f"Cannot pickle {type(self)} object.")


richmem_compress = deprecated(
    "See https://pyzstd.readthedocs.io/en/stable/deprecated.html for alternatives to pyzstd.richmem_compress"
)(compress)


class ZstdFile(zstd.ZstdFile):
    """A file object providing transparent zstd (de)compression.

    A ZstdFile can act as a wrapper for an existing file object, or refer
    directly to a named file on disk.

    Note that ZstdFile provides a *binary* file interface - data read is
    returned as bytes, and data to be written should be an object that
    supports the Buffer Protocol.
    """

    def __init__(
        self,
        filename: _StrOrBytesPath | BinaryIO,
        mode: Literal["r", "rb", "w", "wb", "x", "xb", "a", "ab"] = "r",
        *,
        level_or_option: _LevelOrOption | _Option = None,
        zstd_dict: _ZstdDict = None,
        read_size: int | _DeprecatedPlaceholder = _DEPRECATED_PLACEHOLDER,
        write_size: int | _DeprecatedPlaceholder = _DEPRECATED_PLACEHOLDER,
    ) -> None:
        """Open a zstd compressed file in binary mode.

        filename can be either an actual file name (given as a str, bytes, or
        PathLike object), in which case the named file is opened, or it can be
        an existing file object to read from or write to.

        mode can be "r" for reading (default), "w" for (over)writing, "x" for
        creating exclusively, or "a" for appending. These can equivalently be
        given as "rb", "wb", "xb" and "ab" respectively.

        Parameters
        level_or_option: When it's an int object, it represents compression
            level. When it's a dict object, it contains advanced compression
            parameters. Note, in read mode (decompression), it can only be a
            dict object, that represents decompression option. It doesn't
            support int type compression level in this case.
        zstd_dict: A ZstdDict object, pre-trained dictionary for compression /
            decompression.
        """
        if read_size != _DEPRECATED_PLACEHOLDER:
            warnings.warn(
                "pyzstd.ZstdFile()'s read_size parameter is deprecated",
                DeprecationWarning,
                stacklevel=2,
            )
        if write_size != _DEPRECATED_PLACEHOLDER:
            warnings.warn(
                "pyzstd.ZstdFile()'s write_size parameter is deprecated",
                DeprecationWarning,
                stacklevel=2,
            )
        zstd_dict = cast(
            "ZstdDict | None", zstd_dict
        )  # https://github.com/python/typeshed/pull/15113
        super().__init__(
            filename,
            mode,
            options=_convert_level_or_option(level_or_option, mode),
            zstd_dict=zstd_dict,
        )


@overload
def open(  # noqa: A001
    filename: _StrOrBytesPath | BinaryIO,
    mode: Literal["r", "rb", "w", "wb", "a", "ab", "x", "xb"] = "rb",
    *,
    level_or_option: _LevelOrOption | _Option = None,
    zstd_dict: _ZstdDict = None,
    encoding: None = None,
    errors: None = None,
    newline: None = None,
) -> zstd.ZstdFile: ...


@overload
def open(  # noqa: A001
    filename: _StrOrBytesPath | BinaryIO,
    mode: Literal["rt", "wt", "at", "xt"],
    *,
    level_or_option: _LevelOrOption | _Option = None,
    zstd_dict: _ZstdDict = None,
    encoding: str | None = None,
    errors: str | None = None,
    newline: str | None = None,
) -> TextIOWrapper: ...


def open(  # noqa: A001
    filename: _StrOrBytesPath | BinaryIO,
    mode: Literal[
        "r", "rb", "w", "wb", "a", "ab", "x", "xb", "rt", "wt", "at", "xt"
    ] = "rb",
    *,
    level_or_option: _LevelOrOption | _Option = None,
    zstd_dict: _ZstdDict = None,
    encoding: str | None = None,
    errors: str | None = None,
    newline: str | None = None,
) -> zstd.ZstdFile | TextIOWrapper:
    """Open a zstd compressed file in binary or text mode.

    filename can be either an actual file name (given as a str, bytes, or
    PathLike object), in which case the named file is opened, or it can be an
    existing file object to read from or write to.

    The mode parameter can be "r", "rb" (default), "w", "wb", "x", "xb", "a",
    "ab" for binary mode, or "rt", "wt", "xt", "at" for text mode.

    The level_or_option and zstd_dict parameters specify the settings, as for
    ZstdCompressor, ZstdDecompressor and ZstdFile.

    When using read mode (decompression), the level_or_option parameter can
    only be a dict object, that represents decompression option. It doesn't
    support int type compression level in this case.

    For binary mode, this function is equivalent to the ZstdFile constructor:
    ZstdFile(filename, mode, ...). In this case, the encoding, errors and
    newline parameters must not be provided.

    For text mode, an ZstdFile object is created, and wrapped in an
    io.TextIOWrapper instance with the specified encoding, error handling
    behavior, and line ending(s).
    """
    zstd_dict = cast(
        "ZstdDict | None", zstd_dict
    )  # https://github.com/python/typeshed/pull/15113
    return zstd.open(
        filename,
        mode,
        options=_convert_level_or_option(level_or_option, mode),
        zstd_dict=zstd_dict,
        encoding=encoding,
        errors=errors,
        newline=newline,
    )


def _create_callback(
    output_stream: BinaryIO | None,
    callback: Callable[[int, int, memoryview, memoryview], None] | None,
) -> Callable[[int, int, bytes, bytes], None]:
    if output_stream is None:
        if callback is None:
            raise TypeError(
                "At least one of output_stream argument and callback argument should be non-None."
            )

        def cb(
            total_input: int, total_output: int, data_in: bytes, data_out: bytes
        ) -> None:
            callback(
                total_input, total_output, memoryview(data_in), memoryview(data_out)
            )

    elif callback is None:

        def cb(
            total_input: int,  # noqa: ARG001
            total_output: int,  # noqa: ARG001
            data_in: bytes,  # noqa: ARG001
            data_out: bytes,
        ) -> None:
            output_stream.write(data_out)

    else:

        def cb(
            total_input: int, total_output: int, data_in: bytes, data_out: bytes
        ) -> None:
            output_stream.write(data_out)
            callback(
                total_input, total_output, memoryview(data_in), memoryview(data_out)
            )

    return cb


@deprecated(
    "See https://pyzstd.readthedocs.io/en/stable/deprecated.html for alternatives to pyzstd.compress_stream"
)
def compress_stream(
    input_stream: BinaryIO,
    output_stream: BinaryIO | None,
    *,
    level_or_option: _LevelOrOption = None,
    zstd_dict: _ZstdDict = None,
    pledged_input_size: int | None = None,
    read_size: int = 131_072,
    write_size: int | _DeprecatedPlaceholder = _DEPRECATED_PLACEHOLDER,  # noqa: ARG001
    callback: Callable[[int, int, memoryview, memoryview], None] | None = None,
) -> tuple[int, int]:
    """Compresses input_stream and writes the compressed data to output_stream, it
    doesn't close the streams.

    ----
    DEPRECATION NOTICE
    The (de)compress_stream are deprecated and will be removed in a future version.
    See https://pyzstd.readthedocs.io/en/stable/deprecated.html for alternatives
    ----

    If input stream is b'', nothing will be written to output stream.

    Return a tuple, (total_input, total_output), the items are int objects.

    Parameters
    input_stream: Input stream that has a .readinto(b) method.
    output_stream: Output stream that has a .write(b) method. If use callback
        function, this parameter can be None.
    level_or_option: When it's an int object, it represents the compression
        level. When it's a dict object, it contains advanced compression
        parameters.
    zstd_dict: A ZstdDict object, pre-trained zstd dictionary.
    pledged_input_size: If set this parameter to the size of input data, the
        size will be written into the frame header. If the actual input data
        doesn't match it, a ZstdError will be raised.
    read_size: Input buffer size, in bytes.
    callback: A callback function that accepts four parameters:
        (total_input, total_output, read_data, write_data), the first two are
        int objects, the last two are readonly memoryview objects.
    """
    if not hasattr(input_stream, "read"):
        raise TypeError("input_stream argument should have a .read() method.")
    if output_stream is not None and not hasattr(output_stream, "write"):
        raise TypeError("output_stream argument should have a .write() method.")
    if read_size < 1:
        raise ValueError("read_size argument should be a positive number.")
    callback = _create_callback(output_stream, callback)
    total_input = 0
    total_output = 0
    compressor = ZstdCompressor(level_or_option, zstd_dict)
    if pledged_input_size is not None and pledged_input_size != 2**64 - 1:
        compressor._set_pledged_input_size(pledged_input_size)  # noqa: SLF001
    while data_in := input_stream.read(read_size):
        total_input += len(data_in)
        data_out = compressor.compress(data_in)
        total_output += len(data_out)
        callback(total_input, total_output, data_in, data_out)
    if not total_input:
        return total_input, total_output
    data_out = compressor.flush()
    total_output += len(data_out)
    callback(total_input, total_output, b"", data_out)
    return total_input, total_output


@deprecated(
    "See https://pyzstd.readthedocs.io/en/stable/deprecated.html for alternatives to pyzstd.decompress_stream"
)
def decompress_stream(
    input_stream: BinaryIO,
    output_stream: BinaryIO | None,
    *,
    zstd_dict: _ZstdDict = None,
    option: _Option = None,
    read_size: int = 131_075,
    write_size: int = 131_072,
    callback: Callable[[int, int, memoryview, memoryview], None] | None = None,
) -> tuple[int, int]:
    """Decompresses input_stream and writes the decompressed data to output_stream,
    it doesn't close the streams.

    ----
    DEPRECATION NOTICE
    The (de)compress_stream are deprecated and will be removed in a future version.
    See https://pyzstd.readthedocs.io/en/stable/deprecated.html for alternatives
    ----

    Supports multiple concatenated frames.

    Return a tuple, (total_input, total_output), the items are int objects.

    Parameters
    input_stream: Input stream that has a .readinto(b) method.
    output_stream: Output stream that has a .write(b) method. If use callback
        function, this parameter can be None.
    zstd_dict: A ZstdDict object, pre-trained zstd dictionary.
    option: A dict object, contains advanced decompression parameters.
    read_size: Input buffer size, in bytes.
    write_size: Output buffer size, in bytes.
    callback: A callback function that accepts four parameters:
        (total_input, total_output, read_data, write_data), the first two are
        int objects, the last two are readonly memoryview objects.
    """
    if not hasattr(input_stream, "read"):
        raise TypeError("input_stream argument should have a .read() method.")
    if output_stream is not None and not hasattr(output_stream, "write"):
        raise TypeError("output_stream argument should have a .write() method.")
    if read_size < 1 or write_size < 1:
        raise ValueError(
            "read_size argument and write_size argument should be positive numbers."
        )
    callback = _create_callback(output_stream, callback)
    total_input = 0
    total_output = 0
    decompressor = EndlessZstdDecompressor(zstd_dict, option)
    while True:
        if decompressor.needs_input:
            data_in = input_stream.read(read_size)
            if not data_in:
                break
        else:
            data_in = b""
        total_input += len(data_in)
        data_out = decompressor.decompress(data_in, write_size)
        total_output += len(data_out)
        callback(total_input, total_output, data_in, data_out)
    if not decompressor.at_frame_edge:
        raise ZstdError(
            "Decompression failed: zstd data ends in an incomplete frame,"
            " maybe the input data was truncated."
            f" Total input {total_input} bytes, total output {total_output} bytes."
        )
    return total_input, total_output


class CompressionValues(NamedTuple):
    default: int
    min: int
    max: int


compressionLevel_values = CompressionValues(  # noqa: N816
    zstd.COMPRESSION_LEVEL_DEFAULT, *CParameter.compressionLevel.bounds()
)
zstd_support_multithread = CParameter.nbWorkers.bounds() != (0, 0)


# import here to avoid circular dependency issues
from ._seekable_zstdfile import SeekableFormatError, SeekableZstdFile  # noqa: E402
