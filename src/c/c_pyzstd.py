import _compression
import io
from collections import namedtuple
from enum import IntEnum
from os import PathLike
from sys import maxsize

from ._zstd import *
from . import _zstd

__all__ = ('compress', 'richmem_compress', 'decompress',
           'train_dict', 'finalize_dict',
           'ZstdCompressor', 'RichMemZstdCompressor',
           'ZstdDecompressor', 'EndlessZstdDecompressor',
           'ZstdDict', 'ZstdError', 'ZstdFile', 'open',
           'CParameter', 'DParameter', 'Strategy',
           'get_frame_info', 'get_frame_size',
           'compress_stream', 'decompress_stream',
           'zstd_version', 'zstd_version_info', 'compressionLevel_values')


_nt_values = namedtuple('values', ['default', 'min', 'max'])
compressionLevel_values = _nt_values(_zstd._ZSTD_CLEVEL_DEFAULT,
                                     _zstd._ZSTD_minCLevel,
                                     _zstd._ZSTD_maxCLevel)


_nt_frame_info = namedtuple('frame_info', ['decompressed_size', 'dictionary_id'])

def get_frame_info(frame_buffer):
    """Get zstd frame infomation from a frame header.

    Arguments
    frame_buffer: A bytes-like object. It should starts from the beginning of
                  a frame, and needs to include at least the frame header (6 to
                  18 bytes).

    Return a two-items namedtuple: (decompressed_size, dictionary_id)

    If decompressed_size is None, decompressed size is unknown.

    dictionary_id is a 32-bit unsigned integer value. 0 means dictionary ID was
    not recorded in frame header, the frame may or may not need a dictionary to
    be decoded, and the ID of such a dictionary is not specified.

    It's possible to append more items to the namedtuple in the future."""

    ret_tuple = _zstd._get_frame_info(frame_buffer)
    return _nt_frame_info(*ret_tuple)


class CParameter(IntEnum):
    """Compression parameters"""

    compressionLevel           = _zstd._ZSTD_c_compressionLevel
    windowLog                  = _zstd._ZSTD_c_windowLog
    hashLog                    = _zstd._ZSTD_c_hashLog
    chainLog                   = _zstd._ZSTD_c_chainLog
    searchLog                  = _zstd._ZSTD_c_searchLog
    minMatch                   = _zstd._ZSTD_c_minMatch
    targetLength               = _zstd._ZSTD_c_targetLength
    strategy                   = _zstd._ZSTD_c_strategy

    enableLongDistanceMatching = _zstd._ZSTD_c_enableLongDistanceMatching
    ldmHashLog                 = _zstd._ZSTD_c_ldmHashLog
    ldmMinMatch                = _zstd._ZSTD_c_ldmMinMatch
    ldmBucketSizeLog           = _zstd._ZSTD_c_ldmBucketSizeLog
    ldmHashRateLog             = _zstd._ZSTD_c_ldmHashRateLog

    contentSizeFlag            = _zstd._ZSTD_c_contentSizeFlag
    checksumFlag               = _zstd._ZSTD_c_checksumFlag
    dictIDFlag                 = _zstd._ZSTD_c_dictIDFlag

    nbWorkers                  = _zstd._ZSTD_c_nbWorkers
    jobSize                    = _zstd._ZSTD_c_jobSize
    overlapLog                 = _zstd._ZSTD_c_overlapLog

    def bounds(self):
        """Return lower and upper bounds of a parameter, both inclusive."""
        # 1 means compression parameter
        return _zstd._get_param_bounds(1, self.value)


class DParameter(IntEnum):
    """Decompression parameters"""

    windowLogMax = _zstd._ZSTD_d_windowLogMax

    def bounds(self):
        """Return lower and upper bounds of a parameter, both inclusive."""
        # 0 means decompression parameter
        return _zstd._get_param_bounds(0, self.value)


class Strategy(IntEnum):
    """Compression strategies, listed from fastest to strongest.

    Note : new strategies _might_ be added in the future, only the order
    (from fast to strong) is guaranteed.
    """
    fast     = _zstd._ZSTD_fast
    dfast    = _zstd._ZSTD_dfast
    greedy   = _zstd._ZSTD_greedy
    lazy     = _zstd._ZSTD_lazy
    lazy2    = _zstd._ZSTD_lazy2
    btlazy2  = _zstd._ZSTD_btlazy2
    btopt    = _zstd._ZSTD_btopt
    btultra  = _zstd._ZSTD_btultra
    btultra2 = _zstd._ZSTD_btultra2


def compress(data, level_or_option=None, zstd_dict=None):
    """Compress a block of data, return a bytes object.

    Compressing b'' will get an empty content frame (9 bytes or more).

    Arguments
    data:            A bytes-like object, data to be compressed.
    level_or_option: When it's an int object, it represents compression level.
                     When it's a dict object, it contains advanced compression
                     parameters.
    zstd_dict:       A ZstdDict object, pre-trained dictionary for compression.
    """
    comp = ZstdCompressor(level_or_option, zstd_dict)
    return comp.compress(data, ZstdCompressor.FLUSH_FRAME)


def richmem_compress(data, level_or_option=None, zstd_dict=None):
    """Compress a block of data, return a bytes object.

    Use rich memory mode, it's faster than compress() in some cases, but
    allocates more memory.

    Compressing b'' will get an empty content frame (9 bytes or more).

    Arguments
    data:            A bytes-like object, data to be compressed.
    level_or_option: When it's an int object, it represents compression level.
                     When it's a dict object, it contains advanced compression
                     parameters.
    zstd_dict:       A ZstdDict object, pre-trained dictionary for compression.
    """
    comp = RichMemZstdCompressor(level_or_option, zstd_dict)
    return comp.compress(data)


def decompress(data, zstd_dict=None, option=None):
    """Decompress a zstd data, return a bytes object.

    Support multiple concatenated frames.

    Arguments
    data:      A bytes-like object, compressed zstd data.
    zstd_dict: A ZstdDict object, pre-trained zstd dictionary.
    option:    A dict object, contains advanced decompression parameters.
    """
    decomp = EndlessZstdDecompressor(zstd_dict, option)
    ret = decomp.decompress(data)

    if not decomp.at_frame_edge:
        extra_msg = '.' if len(ret) == 0 else \
                    (', if want to output these decompressed data, use '
                     'an EndlessZstdDecompressor object to decompress.')
        msg = ('Decompression failed: zstd data ends in an incomplete '
               'frame, maybe the input data was truncated. Decompressed '
               'data is %s bytes%s') % (format(len(ret), ','), extra_msg)
        raise ZstdError(msg)

    return ret


def train_dict(samples, dict_size):
    """Train a zstd dictionary, return a ZstdDict object.

    Arguments
    samples:   An iterable of samples, a sample is a bytes-like object
               represents a file.
    dict_size: The dictionary's maximum size, in bytes.
    """
    chunks = []
    chunk_sizes = []
    for chunk in samples:
        chunks.append(chunk)
        chunk_sizes.append(len(chunk))

    chunks = b''.join(chunks)
    if not chunks:
        raise ValueError("The samples are empty content, can't train dictionary.")

    # samples_bytes: samples be stored concatenated in a single flat buffer.
    # samples_size_list: a list of each sample's size.
    # dict_size: size of the dictionary, in bytes.
    dict_content = _zstd._train_dict(chunks, chunk_sizes, dict_size)

    return ZstdDict(dict_content)


def finalize_dict(zstd_dict, samples, dict_size, level):
    """Finalize a zstd dictionary, return a ZstdDict object.

    Given a custom content as a basis for dictionary, and a set of samples,
    finalize dictionary by adding headers and statistics according to the zstd
    dictionary format.

    You may compose an effective dictionary content by hand, which is used as
    basis dictionary, and use some samples to finalize a dictionary. The basis
    dictionary can be a "raw content" dictionary, see is_raw argument in
    ZstdDict.__init__ method.

    Arguments
    zstd_dict: A ZstdDict object, basis dictionary.
    samples:   An iterable of samples, a sample is a bytes-like object
               represents a file.
    dict_size: The dictionary's maximum size, in bytes.
    level:     The compression level expected to use in production. The
               statistics for each compression level differ, so tuning the
               dictionary for the compression level can help quite a bit.
    """
    if zstd_version_info < (1, 4, 5):
        msg = ("This function only available when the underlying zstd "
               "library's version is greater than or equal to v1.4.5, "
               "the current underlying zstd library's version is v%s.") % zstd_version
        raise NotImplementedError(msg)

    if not isinstance(zstd_dict, ZstdDict):
        raise TypeError('zstd_dict argument should be a ZstdDict object.')

    chunks = []
    chunk_sizes = []
    for chunk in samples:
        chunks.append(chunk)
        chunk_sizes.append(len(chunk))

    chunks = b''.join(chunks)
    if not chunks:
        raise ValueError("The samples are empty content, can't finalize dictionary.")

    # custom_dict_bytes: existing dictionary.
    # samples_bytes: samples be stored concatenated in a single flat buffer.
    # samples_size_list: a list of each sample's size.
    # dict_size: maximal size of the dictionary, in bytes.
    # compression_level: compression level expected to use in production.
    dict_content = _zstd._finalize_dict(zstd_dict.dict_content,
                                        chunks, chunk_sizes,
                                        dict_size, level)

    return ZstdDict(dict_content)


class ZstdDecompressReader(_compression.DecompressReader):
    def readall(self):
        chunks = []
        while True:
            # sys.maxsize means the max length of output buffer is unlimited,
            # so that the whole input buffer can be decompressed within one
            # .decompress() call.
            data = self.read(maxsize)
            if not data:
                break
            chunks.append(data)
        return b''.join(chunks)


_MODE_CLOSED = 0
_MODE_READ   = 1
_MODE_WRITE  = 2

class ZstdFile(_compression.BaseStream):
    def __init__(self, filename, mode="r", *,
                 level_or_option=None, zstd_dict=None):
        self._fp = None
        self._closefp = False
        self._mode = _MODE_CLOSED

        if not isinstance(zstd_dict, (type(None), ZstdDict)):
            raise TypeError("zstd_dict argument should be a ZstdDict object.")

        if mode in ("r", "rb"):
            if not isinstance(level_or_option, (type(None), dict)):
                msg = ("In read mode (decompression), level_or_option argument "
                       "should be a dict object, that represents decompression "
                       "option. It doesn't support int type compression level "
                       "in this case.")
                raise TypeError(msg)
            mode_code = _MODE_READ
        elif mode in ("w", "wb", "a", "ab", "x", "xb"):
            if not isinstance(level_or_option, (type(None), int, dict)):
                msg = "level_or_option argument should be int or dict object."
                raise TypeError(msg)
            mode_code = _MODE_WRITE
            self._compressor = ZstdCompressor(level_or_option, zstd_dict)
            self._pos = 0
        else:
            raise ValueError("Invalid mode: {!r}".format(mode))

        if isinstance(filename, (str, bytes, PathLike)):
            if "b" not in mode:
                mode += "b"
            self._fp = io.open(filename, mode)
            self._closefp = True
            self._mode = mode_code
        elif hasattr(filename, "read") or hasattr(filename, "write"):
            self._fp = filename
            self._mode = mode_code
        else:
            raise TypeError("filename must be a str, bytes, file or PathLike object")

        if self._mode == _MODE_READ:
            raw = ZstdDecompressReader(self._fp, ZstdDecompressor,
                                       trailing_error=ZstdError,
                                       zstd_dict=zstd_dict, option=level_or_option)
            self._buffer = io.BufferedReader(raw)

    def close(self):
        """Flush and close the file.

        May be called more than once without error. Once the file is
        closed, any other operation on it will raise a ValueError.
        """
        if self._mode == _MODE_CLOSED:
            return
        try:
            if self._mode == _MODE_READ and hasattr(self, '_buffer'):
                self._buffer.close()
                self._buffer = None
            elif self._mode == _MODE_WRITE:
                self._fp.write(self._compressor.flush())
                self._compressor = None
        finally:
            try:
                if self._closefp:
                    self._fp.close()
            finally:
                self._fp = None
                self._closefp = False
                self._mode = _MODE_CLOSED

    @property
    def closed(self):
        """True if this file is closed."""
        return self._mode == _MODE_CLOSED

    def fileno(self):
        """Return the file descriptor for the underlying file."""
        self._check_not_closed()
        return self._fp.fileno()

    def seekable(self):
        """Return whether the file supports seeking."""
        return self.readable() and self._buffer.seekable()

    def readable(self):
        """Return whether the file was opened for reading."""
        self._check_not_closed()
        return self._mode == _MODE_READ

    def writable(self):
        """Return whether the file was opened for writing."""
        self._check_not_closed()
        return self._mode == _MODE_WRITE

    def peek(self, size=-1):
        """Return buffered data without advancing the file position.

        Always returns at least one byte of data, unless at EOF.
        The exact number of bytes returned is unspecified.
        """
        self._check_can_read()
        # Relies on the undocumented fact that BufferedReader.peek() always
        # returns at least one byte (except at EOF)
        return self._buffer.peek(size)

    def read(self, size=-1):
        """Read up to size uncompressed bytes from the file.

        If size is negative or omitted, read until EOF is reached.
        Returns b"" if the file is already at EOF.
        """
        self._check_can_read()
        return self._buffer.read(size)

    def read1(self, size=-1):
        """Read up to size uncompressed bytes, while trying to avoid
        making multiple reads from the underlying stream. Reads up to a
        buffer's worth of data if size is negative.

        Returns b"" if the file is at EOF.
        """
        self._check_can_read()
        if size < 0:
            size = _compression.BUFFER_SIZE
        return self._buffer.read1(size)

    def readline(self, size=-1):
        """Read a line of uncompressed bytes from the file.

        The terminating newline (if present) is retained. If size is
        non-negative, no more than size bytes will be read (in which
        case the line may be incomplete). Returns b'' if already at EOF.
        """
        self._check_can_read()
        return self._buffer.readline(size)

    def write(self, data):
        """Write a bytes object to the file.

        Returns the number of uncompressed bytes written, which is
        always len(data). Note that due to buffering, the file on disk
        may not reflect the data written until close() is called.
        """
        self._check_can_write()
        compressed = self._compressor.compress(data)
        self._fp.write(compressed)
        self._pos += len(data)
        return len(data)

    def seek(self, offset, whence=io.SEEK_SET):
        """Change the file position.

        The new position is specified by offset, relative to the
        position indicated by whence. Possible values for whence are:

            0: start of stream (default): offset must not be negative
            1: current stream position
            2: end of stream; offset must not be positive

        Returns the new file position.

        Note that seeking is emulated, so depending on the parameters,
        this operation may be extremely slow.
        """
        self._check_can_seek()
        return self._buffer.seek(offset, whence)

    def tell(self):
        """Return the current file position."""
        self._check_not_closed()
        if self._mode == _MODE_READ:
            return self._buffer.tell()
        return self._pos


def open(filename, mode="rb", *, level_or_option=None, zstd_dict=None,
         encoding=None, errors=None, newline=None):
    if "t" in mode:
        if "b" in mode:
            raise ValueError("Invalid mode: %r" % (mode,))
    else:
        if encoding is not None:
            raise ValueError("Argument 'encoding' not supported in binary mode")
        if errors is not None:
            raise ValueError("Argument 'errors' not supported in binary mode")
        if newline is not None:
            raise ValueError("Argument 'newline' not supported in binary mode")

    zstd_mode = mode.replace("t", "")
    binary_file = ZstdFile(filename, zstd_mode,
                           level_or_option=level_or_option, zstd_dict=zstd_dict)

    if "t" in mode:
        return io.TextIOWrapper(binary_file, encoding, errors, newline)
    else:
        return binary_file
