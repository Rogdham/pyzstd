import _compression
import io
from os import PathLike
from sys import maxsize

try:
    # Import C implementation
    from .c.c_pyzstd import *
    from .c.c_pyzstd import _train_dict, _finalize_dict, \
                            _ZSTD_DStreamInSize
except ImportError:
    try:
        # Import CFFI implementation
        from .cffi.cffi_pyzstd import *
        from .cffi.cffi_pyzstd import _train_dict, _finalize_dict, \
                                      _ZSTD_DStreamInSize
        CFFI_PYZSTD = True
    except ImportError:
        raise ImportError(
            "pyzstd module: Neither C implementation nor CFFI implementation "
            "can be imported. If pyzstd module is dynamically linked to zstd "
            "library, make sure not to remove zstd library, and the run-time "
            "zstd library's version can't be lower than that at compile-time.")

__version__ = '0.15.0'

__doc__ = '''\
Python bindings to Zstandard (zstd) compression library, the API is similar to
Python's bz2/lzma/zlib module.

Documentation: https://pyzstd.readthedocs.io
GitHub: https://github.com/animalize/pyzstd
PyPI: https://pypi.org/project/pyzstd'''


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


def train_dict(samples, dict_size):
    """Train a zstd dictionary, return a ZstdDict object.

    Arguments
    samples:   An iterable of samples, a sample is a bytes-like object
               represents a file.
    dict_size: The dictionary's maximum size, in bytes.
    """
    # Check parameter's type
    if not isinstance(dict_size, int):
        raise TypeError('dict_size argument should be an int object.')

    # Prepare data
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
    dict_content = _train_dict(chunks, chunk_sizes, dict_size)

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

    # Check parameters' type
    if not isinstance(zstd_dict, ZstdDict):
        raise TypeError('zstd_dict argument should be a ZstdDict object.')
    if not isinstance(dict_size, int):
        raise TypeError('dict_size argument should be an int object.')
    if not isinstance(level, int):
        raise TypeError('level argument should be an int object.')

    # Prepare data
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
    dict_content = _finalize_dict(zstd_dict.dict_content,
                                  chunks, chunk_sizes,
                                  dict_size, level)

    return ZstdDict(dict_content)


# Below code were copied from Python stdlib (_compression.py, lzma.py), except:
#
# ZstdDecompressReader.read():
#     Uses ZSTD_DStreamInSize() (131,075 in zstd v1.x) instead of
#     _compression.BUFFER_SIZE (default is 8 KiB) as read size.
# ZstdDecompressReader.seek():
#     Uses 32 KiB instead of io.DEFAULT_BUFFER_SIZE (default is 8 KiB) as
#     max_length.
# ZstdFile.__init__():
#     io.BufferedReader uses 32 KiB buffer size instead of default value
#     io.DEFAULT_BUFFER_SIZE (default is 8 KiB).
# ZstdFile.read1():
#     Use 32 KiB instead of io.DEFAULT_BUFFER_SIZE (default is 8 KiB),
#     consistent with ZstdFile.__init__().
#
# In pyzstd module's blocks output buffer, the first block is 32 KiB. It has a
# fast path for this size, if the output data is 32 KiB, it only allocates
# memory and copies data once.

class ZstdDecompressReader(_compression.DecompressReader):
    # Add .readall() method for speedup
    # https://bugs.python.org/issue41486
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

    # Copied from base class, except use ZSTD_DStreamInSize() instead of
    # BUFFER_SIZE (default is 8 KiB) as read size.
    def read(self, size=-1):
        if size < 0:
            return self.readall()

        if not size or self._eof:
            return b""
        data = None  # Default if EOF is encountered
        # Depending on the input data, our call to the decompressor may not
        # return any data. In this case, try again after reading another block.
        while True:
            if self._decompressor.eof:
                rawblock = (self._decompressor.unused_data or
                            self._fp.read(_ZSTD_DStreamInSize))
                if not rawblock:
                    break
                # Continue to next stream.
                self._decompressor = self._decomp_factory(
                    **self._decomp_args)
                try:
                    data = self._decompressor.decompress(rawblock, size)
                except self._trailing_error:
                    # Trailing data isn't a valid compressed stream; ignore it.
                    break
            else:
                if self._decompressor.needs_input:
                    rawblock = self._fp.read(_ZSTD_DStreamInSize)
                    if not rawblock:
                        raise EOFError("Compressed file ended before the "
                                       "end-of-stream marker was reached")
                else:
                    rawblock = b""
                data = self._decompressor.decompress(rawblock, size)
            if data:
                break
        if not data:
            self._eof = True
            self._size = self._pos
            return b""
        self._pos += len(data)
        return data

    # Copied from base class, except use 32 KiB instead of
    # io.DEFAULT_BUFFER_SIZE (default is 8 KiB) as max_length.
    def seek(self, offset, whence=io.SEEK_SET):
        # Recalculate offset as an absolute file position.
        if whence == io.SEEK_SET:
            pass
        elif whence == io.SEEK_CUR:
            offset = self._pos + offset
        elif whence == io.SEEK_END:
            # Seeking relative to EOF - we need to know the file's size.
            if self._size < 0:
                while self.read(32*1024):
                    pass
            offset = self._size + offset
        else:
            raise ValueError("Invalid value for whence: {}".format(whence))

        # Make it so that offset is the number of bytes to skip forward.
        if offset < self._pos:
            self._rewind()
        else:
            offset -= self._pos

        # Read and discard data until we reach the desired position.
        while offset > 0:
            data = self.read(min(32*1024, offset))
            if not data:
                break
            offset -= len(data)

        return self._pos


_MODE_CLOSED = 0
_MODE_READ   = 1
_MODE_WRITE  = 2

# Copied from lzma module, except:
# ZstdFile.__init__():
#   io.BufferedReader uses 32 KiB buffer size instead of default value
#   io.DEFAULT_BUFFER_SIZE (default is 8 KiB).
# ZstdFile.read1():
#   Uses 32 KiB instead of io.DEFAULT_BUFFER_SIZE (default is 8 KiB),
#   consistent with ZstdFile.__init__().
class ZstdFile(_compression.BaseStream):
    """A file object providing transparent zstd (de)compression.

    A ZstdFile can act as a wrapper for an existing file object, or refer
    directly to a named file on disk.

    Note that ZstdFile provides a *binary* file interface - data read is
    returned as bytes, and data to be written must be given as bytes.
    """

    def __init__(self, filename, mode="r", *,
                 level_or_option=None, zstd_dict=None):
        """Open a zstd compressed file in binary mode.

        filename can be either an actual file name (given as a str, bytes, or
        PathLike object), in which case the named file is opened, or it can be
        an existing file object to read from or write to.

        mode can be "r" for reading (default), "w" for (over)writing, "x" for
        creating exclusively, or "a" for appending. These can equivalently be
        given as "rb", "wb", "xb" and "ab" respectively.

        Arguments
        level_or_option: When it's an int object, it represents compression
            level. When it's a dict object, it contains advanced compression
            parameters. Note, in read mode (decompression), it can only be a
            dict object, that represents decompression option. It doesn't
            support int type compression level in this case.
        zstd_dict: A ZstdDict object, pre-trained dictionary for compression /
            decompression.
        """
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
            self._buffer = io.BufferedReader(raw, 32*1024)

    # Override IOBase.__iter__
    # https://bugs.python.org/issue43787
    def __iter__(self):
        self._check_can_read()
        return self._buffer.__iter__()

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
            size = 32*1024
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


# Copied from lzma module
def open(filename, mode="rb", *, level_or_option=None, zstd_dict=None,
         encoding=None, errors=None, newline=None):
    """Open a zstd compressed file in binary or text mode.

    filename can be either an actual file name (given as a str, bytes, or
    PathLike object), in which case the named file is opened, or it can be an
    existing file object to read from or write to.

    The mode argument can be "r", "rb" (default), "w", "wb", "x", "xb", "a",
    "ab" for binary mode, or "rt", "wt", "xt", "at" for text mode.

    The level_or_option and zstd_dict arguments specify the settings, as for
    ZstdCompressor, ZstdDecompressor and ZstdFile.

    When using read mode (decompression), the level_or_option argument can only
    be a dict object, that represents decompression option. It doesn't support
    int type compression level in this case.

    For binary mode, this function is equivalent to the ZstdFile constructor:
    ZstdFile(filename, mode, ...). In this case, the encoding, errors and
    newline arguments must not be provided.

    For text mode, an ZstdFile object is created, and wrapped in an
    io.TextIOWrapper instance with the specified encoding, error handling
    behavior, and line ending(s).
    """

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