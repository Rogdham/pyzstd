from collections import namedtuple
from enum import IntEnum
from sys import maxsize
from threading import Lock
from warnings import warn

from ._cffi_zstd import ffi, lib as m

__all__ = ('ZstdCompressor', 'RichMemZstdCompressor',
           'ZstdDecompressor', 'EndlessZstdDecompressor',
           'ZstdDict', 'ZstdError',
           'CParameter', 'DParameter', 'Strategy',
           'decompress', 'get_frame_info', 'get_frame_size',
           'compress_stream', 'decompress_stream',
           'zstd_version', 'zstd_version_info', 'compressionLevel_values')

# Used in __init__.py
_ZSTD_DStreamInSize = m.ZSTD_DStreamInSize()

zstd_version = ffi.string(m.ZSTD_versionString()).decode('ascii')
zstd_version_info = tuple(int(i) for i in zstd_version.split('.'))

_nt_values = namedtuple('values', ['default', 'min', 'max'])
compressionLevel_values = _nt_values(m.ZSTD_defaultCLevel(),
                                     m.ZSTD_minCLevel(),
                                     m.ZSTD_maxCLevel())

_new_nonzero = ffi.new_allocator(should_clear_after_alloc=False)

class ZstdError(Exception):
    "Call to the underlying zstd library failed."
    pass

def _get_param_bounds(is_compress, key):
    # Get parameter bounds
    if is_compress:
        bounds = m.ZSTD_cParam_getBounds(key)
        if m.ZSTD_isError(bounds.error):
            _set_zstd_error(_ErrorType.ERR_GET_C_BOUNDS, bounds.error)
    else:
        bounds = m.ZSTD_dParam_getBounds(key)
        if m.ZSTD_isError(bounds.error):
            _set_zstd_error(_ErrorType.ERR_GET_D_BOUNDS, bounds.error)

    return (bounds.lowerBound, bounds.upperBound)

class CParameter(IntEnum):
    """Compression parameters"""

    compressionLevel           = m.ZSTD_c_compressionLevel
    windowLog                  = m.ZSTD_c_windowLog
    hashLog                    = m.ZSTD_c_hashLog
    chainLog                   = m.ZSTD_c_chainLog
    searchLog                  = m.ZSTD_c_searchLog
    minMatch                   = m.ZSTD_c_minMatch
    targetLength               = m.ZSTD_c_targetLength
    strategy                   = m.ZSTD_c_strategy

    enableLongDistanceMatching = m.ZSTD_c_enableLongDistanceMatching
    ldmHashLog                 = m.ZSTD_c_ldmHashLog
    ldmMinMatch                = m.ZSTD_c_ldmMinMatch
    ldmBucketSizeLog           = m.ZSTD_c_ldmBucketSizeLog
    ldmHashRateLog             = m.ZSTD_c_ldmHashRateLog

    contentSizeFlag            = m.ZSTD_c_contentSizeFlag
    checksumFlag               = m.ZSTD_c_checksumFlag
    dictIDFlag                 = m.ZSTD_c_dictIDFlag

    nbWorkers                  = m.ZSTD_c_nbWorkers
    jobSize                    = m.ZSTD_c_jobSize
    overlapLog                 = m.ZSTD_c_overlapLog

    def bounds(self):
        """Return lower and upper bounds of a parameter, both inclusive."""
        # 1 means compression parameter
        return _get_param_bounds(1, self.value)

_SUPPORT_MULTITHREAD = (CParameter.nbWorkers.bounds() != (0, 0))

class DParameter(IntEnum):
    """Decompression parameters"""

    windowLogMax = m.ZSTD_d_windowLogMax

    def bounds(self):
        """Return lower and upper bounds of a parameter, both inclusive."""
        # 0 means decompression parameter
        return _get_param_bounds(0, self.value)

class Strategy(IntEnum):
    """Compression strategies, listed from fastest to strongest.

    Note : new strategies _might_ be added in the future, only the order
    (from fast to strong) is guaranteed.
    """
    fast     = m.ZSTD_fast
    dfast    = m.ZSTD_dfast
    greedy   = m.ZSTD_greedy
    lazy     = m.ZSTD_lazy
    lazy2    = m.ZSTD_lazy2
    btlazy2  = m.ZSTD_btlazy2
    btopt    = m.ZSTD_btopt
    btultra  = m.ZSTD_btultra
    btultra2 = m.ZSTD_btultra2

class _BlocksOutputBuffer:
    KB = 1024
    MB = 1024 * 1024
    BUFFER_BLOCK_SIZE = (
        # If change this list, also change:
        #   The CFFI implementation
        #   OutputBufferTestCase unittest
        # If change the first blocks's size, also change:
        #   ZstdDecompressReader.seek() method
        #   ZstdFile.__init__() method
        #   ZstdFile.read1() method
        #   FileTestCase.test_decompress_limited() test
        32*KB, 64*KB, 256*KB, 1*MB, 4*MB, 8*MB, 16*MB, 16*MB,
        32*MB, 32*MB, 32*MB, 32*MB, 64*MB, 64*MB, 128*MB, 128*MB,
        256*MB )
    MEM_ERR_MSG = "Unable to allocate output buffer."

    def initAndGrow(self, out, max_length):
        # Get block size
        if 0 <= max_length < self.BUFFER_BLOCK_SIZE[0]:
            block_size = max_length
        else:
            block_size = self.BUFFER_BLOCK_SIZE[0]

        # The first block
        block = _new_nonzero("char[]", block_size)
        if block == ffi.NULL:
            raise MemoryError

        # Create the list
        self.list = [block]

        # Set variables
        self.allocated = block_size
        self.max_length = max_length

        out.dst = block
        out.size = block_size
        out.pos = 0

    def initWithSize(self, out, max_length, init_size):
        # Get block size
        if max_length >= 0:
            block_size = min(max_length, init_size)
        else:
            block_size = init_size

        # The first block
        block = _new_nonzero("char[]", block_size)
        if block == ffi.NULL:
            raise MemoryError(self.MEM_ERR_MSG)

        # Create the list
        self.list = [block]

        # Set variables
        self.allocated = block_size
        self.max_length = max_length

        out.dst = block
        out.size = block_size
        out.pos = 0

    def grow(self, out):
        # Ensure no gaps in the data
        assert out.pos == out.size

        # Get block size
        list_len = len(self.list)
        if list_len < len(self.BUFFER_BLOCK_SIZE):
            block_size = self.BUFFER_BLOCK_SIZE[list_len]
        else:
            block_size = self.BUFFER_BLOCK_SIZE[-1]

        # Check max_length
        if self.max_length >= 0:
            # If (rest == 0), should not grow the buffer.
            rest = self.max_length - self.allocated
            assert rest > 0

            # block_size of the last block
            if block_size > rest:
                block_size = rest

        # Create the block
        b = _new_nonzero("char[]", block_size)
        if b == ffi.NULL:
            raise MemoryError(self.MEM_ERR_MSG)
        self.list.append(b)

        # Set variables
        self.allocated += block_size

        out.dst = b
        out.size = block_size
        out.pos = 0

    def reachedMaxLength(self, out):
        # Ensure (data size == allocated size)
        assert out.pos == out.size

        return self.allocated == self.max_length

    def finish(self, out):
        # Fast path for single block
        if (len(self.list) == 1 and out.pos == out.size) or \
           (len(self.list) == 2 and out.pos == 0):
            return bytes(ffi.buffer(self.list[0]))

        # Final bytes object
        data_size = self.allocated - (out.size-out.pos)
        final = _new_nonzero("char[]", data_size)
        if final == ffi.NULL:
            raise MemoryError(self.MEM_ERR_MSG)

        # Memory copy
        # Blocks except the last one
        posi = 0
        for block in self.list[:-1]:
            ffi.memmove(final+posi, block, len(block))
            posi += len(block)
        # The last block
        ffi.memmove(final+posi, self.list[-1], out.pos)

        return bytes(ffi.buffer(final))

class ZstdDict:
    """Zstd dictionary, used for compression/decompression."""

    def __init__(self, dict_content, is_raw=False) -> None:
        """Initialize a ZstdDict object.

        Arguments
        dict_content: A bytes-like object, dictionary's content.
        is_raw:       This parameter is for advanced user. True means dict_content
                      argument is a "raw content" dictionary, free of any format
                      restriction. False means dict_content argument is an ordinary
                      zstd dictionary, was created by zstd functions, follow a
                      specified format.
        """
        self.__cdicts = {}
        self.__lock = Lock()

        # Check dict_content's type
        try:
            self.__dict_content = bytes(dict_content)
        except:
            raise TypeError("dict_content argument should be bytes-like object.")

        # Both ordinary dictionary and "raw content" dictionary should
        # at least 8 bytes
        if len(dict_content) < 8:
            raise ValueError('Zstd dictionary content should at least 8 bytes.')

        # Create ZSTD_DDict instance from dictionary content, also check content
        # integrity to some degree.
        ddict = m.ZSTD_createDDict(ffi.from_buffer(dict_content), len(dict_content))
        if ddict == ffi.NULL:
            msg = ("Failed to get ZSTD_DDict instance from zstd "
                   "dictionary content. Maybe the content is corrupted.")
            raise ZstdError(msg)

        # Call ZSTD_freeDDict() when GC
        self.__ddict = ffi.gc(ddict,
                              m.ZSTD_freeDDict,
                              m.ZSTD_sizeof_DDict(ddict))

        # Get dict_id, 0 means "raw content" dictionary.
        self.__dict_id = m.ZSTD_getDictID_fromDDict(ddict)

        # Check validity for ordinary dictionary
        if not is_raw and self.__dict_id == 0:
            msg = ('The "dict_content" argument is not a valid zstd '
                   'dictionary. The first 4 bytes of a valid zstd dictionary '
                   'should be a magic number: b"\\x37\\xA4\\x30\\xEC".\n'
                   'If you are an advanced user, and can be sure that '
                   '"dict_content" is a "raw content" zstd dictionary, '
                   'set "is_raw" argument to True.')
            raise ValueError(msg)

    @property
    def dict_content(self):
        """The content of zstd dictionary, a bytes object, it's the same as dict_content
        argument in ZstdDict.__init__() method. It can be used with other programs.
        """
        return self.__dict_content

    @property
    def dict_id(self):
        """ID of zstd dictionary, a 32-bit unsigned int value.

        Non-zero means ordinary dictionary, was created by zstd functions, follow
        a specified format.

        0 means a "raw content" dictionary, free of any format restriction, used
        for advanced user.
        """
        return self.__dict_id

    def __str__(self):
        return '<ZstdDict dict_id=%d dict_size=%d>' % \
               (self.__dict_id, len(self.__dict_content))

    def __reduce__(self):
        msg = ("Intentionally not supporting pickle. If need to save zstd "
               "dictionary to disk, please save .dict_content attribute, "
               "it's a bytes object. So that the zstd dictionary can be "
               "used with other programs.")
        raise TypeError(msg)

    def _get_cdict(self, level):
        try:
            self.__lock.acquire()

            # Already cached
            if level in self.__cdicts:
                cdict = self.__cdicts[level]
            else:
                # Create ZSTD_CDict instance
                cdict = m.ZSTD_createCDict(ffi.from_buffer(self.__dict_content),
                                           len(self.__dict_content), level)
                if cdict == ffi.NULL:
                    msg = ("Failed to get ZSTD_CDict instance from zstd "
                           "dictionary content.")
                    raise ZstdError(msg)

                # Call ZSTD_freeCDict() when GC
                cdict = ffi.gc(cdict,
                               m.ZSTD_freeCDict,
                               m.ZSTD_sizeof_CDict(cdict))
                self.__cdicts[level] = cdict
            return cdict
        finally:
            self.__lock.release()

    def _get_ddict(self):
        return self.__ddict

class _ErrorType:
    ERR_DECOMPRESS=0
    ERR_COMPRESS=1

    ERR_LOAD_D_DICT=2
    ERR_LOAD_C_DICT=3

    ERR_GET_FRAME_SIZE=4
    ERR_GET_C_BOUNDS=5
    ERR_GET_D_BOUNDS=6
    ERR_SET_C_LEVEL=7

    ERR_TRAIN_DICT=8
    ERR_FINALIZE_DICT=9

    _TYPE_MSG = (
        "decompress zstd data",
        "compress zstd data",

        "load zstd dictionary for decompression",
        "load zstd dictionary for compression",

        "get the size of a zstd frame",
        "get zstd compression parameter bounds",
        "get zstd decompression parameter bounds",
        "set zstd compression level",

        "train zstd dictionary",
        "finalize zstd dictionary")

    @staticmethod
    def get_type_msg(type):
        return _ErrorType._TYPE_MSG[type]

def _set_zstd_error(type, zstd_code):
    assert m.ZSTD_isError(zstd_code)

    type_msg = _ErrorType.get_type_msg(type)
    msg = "Unable to %s: %s." % \
          (type_msg, ffi.string(m.ZSTD_getErrorName(zstd_code)).decode('utf-8'))
    raise ZstdError(msg)

def _set_parameter_error(is_compress, posi, key, value):
    COMPRESS_PARAMETERS = \
    {m.ZSTD_c_compressionLevel: "compressionLevel",
     m.ZSTD_c_windowLog:        "windowLog",
     m.ZSTD_c_hashLog:          "hashLog",
     m.ZSTD_c_chainLog:         "chainLog",
     m.ZSTD_c_searchLog:        "searchLog",
     m.ZSTD_c_minMatch:         "minMatch",
     m.ZSTD_c_targetLength:     "targetLength",
     m.ZSTD_c_strategy:         "strategy",

     m.ZSTD_c_enableLongDistanceMatching: "enableLongDistanceMatching",
     m.ZSTD_c_ldmHashLog:       "ldmHashLog",
     m.ZSTD_c_ldmMinMatch:      "ldmMinMatch",
     m.ZSTD_c_ldmBucketSizeLog: "ldmBucketSizeLog",
     m.ZSTD_c_ldmHashRateLog:   "ldmHashRateLog",

     m.ZSTD_c_contentSizeFlag:  "contentSizeFlag",
     m.ZSTD_c_checksumFlag:     "checksumFlag",
     m.ZSTD_c_dictIDFlag:       "dictIDFlag",

     m.ZSTD_c_nbWorkers:        "nbWorkers",
     m.ZSTD_c_jobSize:          "jobSize",
     m.ZSTD_c_overlapLog:       "overlapLog"}

    DECOMPRESS_PARAMETERS = {m.ZSTD_d_windowLogMax: "windowLogMax"}

    if is_compress:
        parameters = COMPRESS_PARAMETERS
        type_msg = "compression"
    else:
        parameters = DECOMPRESS_PARAMETERS
        type_msg = "decompression"

    # Find parameter's name
    name = parameters.get(key)
    if name is None:
        msg = "The %dth zstd %s parameter is invalid." % (posi, type_msg)
        raise ZstdError(msg)

    # Get parameter bounds
    if is_compress:
        bounds = m.ZSTD_cParam_getBounds(key)
    else:
        bounds = m.ZSTD_dParam_getBounds(key)
    if m.ZSTD_isError(bounds.error):
        msg = 'Error when getting bounds of zstd %s parameter "%s".' % \
              (type_msg, name)
        raise ZstdError(msg)

    # Error message
    msg = ('Error when setting zstd %s parameter "%s", it '
           'should %d <= value <= %d, provided value is %d. '
           '(zstd v%s, %s-bit build)') % \
          (type_msg, name,
           bounds.lowerBound, bounds.upperBound, value,
           zstd_version, '64' if maxsize > 2**32 else '32')
    raise ZstdError(msg)

def _check_int32_value(value, name):
    try:
        if value > 2147483647 or value < -2147483648:
            raise Exception
    except:
        raise ValueError("%s should be 32-bit signed int value." % name)

def _clamp_compression_level(level):
    # In zstd v1.4.6-, lower bound is not clamped.
    if m.ZSTD_versionNumber() < 10407:
        if level < m.ZSTD_minCLevel():
            return m.ZSTD_minCLevel()
    return level

def _set_c_parameters(cctx, level_or_option):
    level = 0  # 0 means use zstd's default compression level
    use_multithread = False

    if isinstance(level_or_option, int):
        _check_int32_value(level_or_option, "Compression level")
        level = _clamp_compression_level(level_or_option)

        # Set compression level
        zstd_ret = m.ZSTD_CCtx_setParameter(cctx, m.ZSTD_c_compressionLevel, level)
        if m.ZSTD_isError(zstd_ret):
            _set_zstd_error(_ErrorType.ERR_SET_C_LEVEL, zstd_ret)

        return level, use_multithread

    if isinstance(level_or_option, dict):
        for posi, (key, value) in enumerate(level_or_option.items(), 1):
            _check_int32_value(key, "Key of option dict")
            _check_int32_value(value, "Value of option dict")

            if key == m.ZSTD_c_compressionLevel:
                level = value = _clamp_compression_level(value)
            elif key == m.ZSTD_c_nbWorkers:
                if value > 1:
                    use_multithread = True
                elif value == 1:
                    value = 0

            # Zstd lib doesn't support MT compression
            if (not _SUPPORT_MULTITHREAD
                  and key in (m.ZSTD_c_nbWorkers, m.ZSTD_c_jobSize, m.ZSTD_c_overlapLog)
                  and value > 0):
                value = 0
                if key == m.ZSTD_c_nbWorkers:
                    use_multithread = False
                    msg = ("The underlying zstd library doesn't support "
                           "multi-threaded compression, it was built "
                           "without this feature. Pyzstd module will "
                           "perform single-threaded compression instead.")
                    warn(msg, RuntimeWarning, 2)

            # Set parameter
            zstd_ret = m.ZSTD_CCtx_setParameter(cctx, key, value)
            if m.ZSTD_isError(zstd_ret):
                _set_parameter_error(True, posi, key, value)

        return level, use_multithread

    raise TypeError("level_or_option argument wrong type.")

def _set_d_parameters(dctx, option):
    if not isinstance(option, dict):
        raise TypeError("option argument should be dict object.")

    for posi, (key, value) in enumerate(option.items(), 1):
        _check_int32_value(key, "Key of option dict")
        _check_int32_value(value, "Value of option dict")

        # Set parameter
        zstd_ret = m.ZSTD_DCtx_setParameter(dctx, key, value)
        if m.ZSTD_isError(zstd_ret):
            _set_parameter_error(False, posi, key, value)

def _load_c_dict(cctx, zstd_dict, level):
    # Check dict type
    if not isinstance(zstd_dict, ZstdDict):
        raise TypeError("zstd_dict argument should be ZstdDict object.")

    # Get ZSTD_CDict
    c_dict = zstd_dict._get_cdict(level)

    # Reference a prepared dictionary
    zstd_ret = m.ZSTD_CCtx_refCDict(cctx, c_dict)
    if m.ZSTD_isError(zstd_ret):
        _set_zstd_error(_ErrorType.ERR_LOAD_C_DICT, zstd_ret)

def _load_d_dict(dctx, zstd_dict):
    # Check dict type
    if not isinstance(zstd_dict, ZstdDict):
        raise TypeError("zstd_dict argument should be ZstdDict object.")

    # Get ZSTD_DDict
    d_dict = zstd_dict._get_ddict()

    # Reference a prepared dictionary
    zstd_ret = m.ZSTD_DCtx_refDDict(dctx, d_dict)
    if m.ZSTD_isError(zstd_ret):
        _set_zstd_error(_ErrorType.ERR_LOAD_D_DICT, zstd_ret)

class _Compressor:
    def __init__(self, level_or_option=None, zstd_dict=None):
        self._use_multithreaded = False
        self._lock = Lock()
        level = 0  # 0 means use zstd's default compression level

        # Compression context
        cctx = m.ZSTD_createCCtx()
        if cctx == ffi.NULL:
            raise ZstdError("Unable to create ZSTD_CCtx instance.")
        # Call ZSTD_freeCCtx() when GC
        self._cctx = ffi.gc(cctx,
                            m.ZSTD_freeCCtx,
                            m.ZSTD_sizeof_CCtx(cctx))

        # Set compressLevel/option to compression context
        if level_or_option is not None:
            level, self._use_multithreaded = _set_c_parameters(self._cctx,
                                                               level_or_option)

        # Load dictionary to compression context
        if zstd_dict is not None:
            _load_c_dict(self._cctx, zstd_dict, level)
            self.__dict = zstd_dict

    def _compress_impl(self, data, end_directive, rich_mem):
        # Input buffer
        in_buf = _new_nonzero("ZSTD_inBuffer *")
        if in_buf == ffi.NULL:
            raise MemoryError
        in_buf.src = ffi.from_buffer(data)
        in_buf.size = len(data)
        in_buf.pos = 0

        # Output buffer
        out_buf = _new_nonzero("ZSTD_outBuffer *")
        if out_buf == ffi.NULL:
            raise MemoryError
        out = _BlocksOutputBuffer()

        # Initialize output buffer
        if rich_mem:
            init_size = m.ZSTD_compressBound(len(data))
            out.initWithSize(out_buf, -1, init_size)
        else:
            out.initAndGrow(out_buf, -1)

        while True:
            # Compress
            zstd_ret = m.ZSTD_compressStream2(self._cctx, out_buf, in_buf, end_directive)
            if m.ZSTD_isError(zstd_ret):
                _set_zstd_error(_ErrorType.ERR_COMPRESS, zstd_ret)

            # Finished
            if zstd_ret == 0:
                return out.finish(out_buf)

            # Output buffer should be exhausted, grow the buffer.
            if out_buf.pos == out_buf.size:
                out.grow(out_buf)

    def _compress_mt_continue_impl(self, data):
        # Input buffer
        in_buf = _new_nonzero("ZSTD_inBuffer *")
        if in_buf == ffi.NULL:
            raise MemoryError
        in_buf.src = ffi.from_buffer(data)
        in_buf.size = len(data)
        in_buf.pos = 0

        # Output buffer
        out_buf = _new_nonzero("ZSTD_outBuffer *")
        if out_buf == ffi.NULL:
            raise MemoryError
        out = _BlocksOutputBuffer()
        out.initAndGrow(out_buf, -1)

        while True:
            # Compress
            while True:
                zstd_ret = m.ZSTD_compressStream2(self._cctx,
                                                  out_buf, in_buf,
                                                  m.ZSTD_e_continue)
                if (out_buf.pos == out_buf.size
                      or in_buf.pos == in_buf.size
                      or m.ZSTD_isError(zstd_ret)):
                    break

            # Check error
            if m.ZSTD_isError(zstd_ret):
                _set_zstd_error(_ErrorType.ERR_COMPRESS, zstd_ret)

            # Finished
            if in_buf.pos == in_buf.size:
                return out.finish(out_buf)

            # Output buffer should be exhausted, grow the buffer.
            if out_buf.pos == out_buf.size:
                out.grow(out_buf)

    def __reduce__(self):
        msg = "Cannot pickle %s object." % type(self)
        raise TypeError(msg)

class ZstdCompressor(_Compressor):
    """A streaming compressor. Thread-safe at method level."""
    CONTINUE = m.ZSTD_e_continue
    FLUSH_BLOCK = m.ZSTD_e_flush
    FLUSH_FRAME = m.ZSTD_e_end

    def __init__(self, level_or_option=None, zstd_dict=None):
        """Initialize a ZstdCompressor object.

        Arguments
        level_or_option: When it's an int object, it represents the compression level.
                         When it's a dict object, it contains advanced compression
                         parameters.
        zstd_dict:       A ZstdDict object, pre-trained zstd dictionary.
        """
        super().__init__(level_or_option=level_or_option, zstd_dict=zstd_dict)
        self.__last_mode = m.ZSTD_e_end

    def compress(self, data, mode=CONTINUE):
        """Provide data to the compressor object.
        Return a chunk of compressed data if possible, or b'' otherwise.

        Arguments
        data: A bytes-like object, data to be compressed.
        mode: Can be these 3 values: .CONTINUE, .FLUSH_BLOCK, .FLUSH_FRAME.
        """
        if mode not in (ZstdCompressor.CONTINUE,
                        ZstdCompressor.FLUSH_BLOCK,
                        ZstdCompressor.FLUSH_FRAME):
            msg = ("mode argument wrong value, it should be one of "
                   "ZstdCompressor.CONTINUE, ZstdCompressor.FLUSH_BLOCK, "
                   "ZstdCompressor.FLUSH_FRAME.")
            raise ValueError(msg)

        try:
            self._lock.acquire()

            if self._use_multithreaded and mode == ZstdCompressor.CONTINUE:
                ret = self._compress_mt_continue_impl(data)
            else:
                ret = self._compress_impl(data, mode, False)
            self.__last_mode = mode
            return ret
        except:
            self.__last_mode = m.ZSTD_e_end
            # Resetting cctx's session never fail
            m.ZSTD_CCtx_reset(self._cctx, m.ZSTD_reset_session_only)
            raise
        finally:
            self._lock.release()

    def flush(self, mode=FLUSH_FRAME):
        """Flush any remaining data in internal buffer.

        Since zstd data consists of one or more independent frames, the compressor
        object can still be used after this method is called.

        Arguments
        mode: Can be these 2 values: .FLUSH_FRAME, .FLUSH_BLOCK.
        """
        if mode not in (ZstdCompressor.FLUSH_FRAME, ZstdCompressor.FLUSH_BLOCK):
            msg = ("mode argument wrong value, it should be "
                   "ZstdCompressor.FLUSH_FRAME or ZstdCompressor.FLUSH_BLOCK.")
            raise ValueError(msg)

        try:
            self._lock.acquire()

            ret = self._compress_impl(b"", mode, False)
            self.__last_mode = mode
            return ret
        except:
            self.__last_mode = m.ZSTD_e_end
            # Resetting cctx's session never fail
            m.ZSTD_CCtx_reset(self._cctx, m.ZSTD_reset_session_only)
            raise
        finally:
            self._lock.release()

    @property
    def last_mode(self):
        """The last mode used to this compressor object, its value can be .CONTINUE,
        .FLUSH_BLOCK, .FLUSH_FRAME. Initialized to .FLUSH_FRAME.

        It can be used to get the current state of a compressor, such as, a block
        ends, a frame ends.
        """
        return self.__last_mode

class RichMemZstdCompressor(_Compressor):
    """A compressor use rich memory mode. It is designed to allocate more memory,
    but faster in some cases.
    """

    def __init__(self, level_or_option=None, zstd_dict=None):
        """Initialize a RichMemZstdCompressor object.

        Arguments
        level_or_option: When it's an int object, it represents the compression level.
                         When it's a dict object, it contains advanced compression
                         parameters.
        zstd_dict:       A ZstdDict object, pre-trained zstd dictionary.
        """
        super().__init__(level_or_option=level_or_option, zstd_dict=zstd_dict)

        if self._use_multithreaded:
            msg = ('Currently "rich memory mode" has no effect on '
                   'zstd multi-threaded compression (set '
                   '"CParameter.nbWorkers" > 1), it will allocate '
                   'unnecessary memory.')
            warn(msg, ResourceWarning, 1)

    def compress(self, data):
        """Compress data using rich memory mode, return a single zstd frame.

        Compressing b'' will get an empty content frame (9 bytes or more).

        Arguments
        data: A bytes-like object, data to be compressed.
        """
        try:
            self._lock.acquire()

            ret = self._compress_impl(data, m.ZSTD_e_end, True)
            return ret
        except:
            # Resetting cctx's session never fail
            m.ZSTD_CCtx_reset(self._cctx, m.ZSTD_reset_session_only)
            raise
        finally:
            self._lock.release()

_TYPE_DEC         = 0
_TYPE_ENDLESS_DEC = 1

class _Decompressor:
    def __init__(self, zstd_dict=None, option=None):
        self._lock = Lock()
        self._needs_input = True
        self._input_buffer = ffi.NULL
        self._input_buffer_size = 0
        self._in_begin = 0
        self._in_end = 0

        # Decompression context
        dctx = m.ZSTD_createDCtx()
        if dctx == ffi.NULL:
            raise ZstdError("Unable to create ZSTD_DCtx instance.")
        # Call ZSTD_freeDCtx() when GC
        self._dctx = ffi.gc(dctx,
                            m.ZSTD_freeDCtx,
                            m.ZSTD_sizeof_DCtx(dctx))

        # Load dictionary to compression context
        if zstd_dict is not None:
            _load_d_dict(self._dctx, zstd_dict)
            self.__dict = zstd_dict

        # Set compressLevel/option to compression context
        if option is not None:
            _set_d_parameters(self._dctx, option)

    @property
    def needs_input(self):
        """If the max_length output limit in .decompress() method has been reached, and
        the decompressor has (or may has) unconsumed input data, it will be set to
        False. In this case, pass b'' to .decompress() method may output further data.
        """
        return self._needs_input

    def _decompress_impl(self, in_buf, max_length, initial_size):
        # The first AFE check for setting .at_frame_edge flag, search "AFE" in
        # _zstdmodule.c to see details.
        if self._type == _TYPE_ENDLESS_DEC:
            if self._at_frame_edge and in_buf.pos == in_buf.size:
                return b""

        # Output buffer
        out_buf = _new_nonzero("ZSTD_outBuffer *")
        if out_buf == ffi.NULL:
            raise MemoryError
        out = _BlocksOutputBuffer()
        if initial_size >= 0:
            out.initWithSize(out_buf, max_length, initial_size)
        else:
            out.initAndGrow(out_buf, max_length)

        while True:
            # Decompress
            zstd_ret = m.ZSTD_decompressStream(self._dctx, out_buf, in_buf)
            if m.ZSTD_isError(zstd_ret):
                _set_zstd_error(_ErrorType.ERR_DECOMPRESS, zstd_ret)

            # Set .eof/.af_frame_edge flag
            if self._type == _TYPE_DEC:
                # ZstdDecompressor class stops when a frame is decompressed
                if zstd_ret == 0:
                    self._eof = True
                    break
            else:
                # EndlessZstdDecompressor class supports multiple frames
                self._at_frame_edge = True if (zstd_ret == 0) else False

                # The second AFE check for setting .at_frame_edge flag, search
                # "AFE" in _zstdmodule.c to see details.
                if self._at_frame_edge and in_buf.pos == in_buf.size:
                    break

            # Need to check out before in. Maybe zstd's internal buffer still has
            # a few bytes can be output, grow the buffer and continue.
            if out_buf.pos == out_buf.size:
                # Output buffer exhausted

                # Output buffer reached max_length
                if out.reachedMaxLength(out_buf):
                    break

                # Grow output buffer
                out.grow(out_buf)
            elif in_buf.pos == in_buf.size:
                # Finished
                break

        return out.finish(out_buf)

    def _stream_decompress(self, data, max_length=-1):
        try:
            self._lock.acquire()

            initial_buffer_size = -1

            in_buf = _new_nonzero("ZSTD_inBuffer *")
            if in_buf == ffi.NULL:
                raise MemoryError

            if self._type == _TYPE_DEC:
                # Check .eof flag
                if self._eof:
                    raise EOFError("Already at the end of a zstd frame.")
            else:
                # Fast path for the first frame
                if self._at_frame_edge and self._in_begin == self._in_end:
                    # Read decompressed size
                    decompressed_size = m.ZSTD_getFrameContentSize(ffi.from_buffer(data),
                                                                   len(data))

                    # Use ZSTD_findFrameCompressedSize() to check complete frame,
                    # prevent allocating too much memory for small input chunk.
                    if (decompressed_size not in (m.ZSTD_CONTENTSIZE_UNKNOWN,
                                                  m.ZSTD_CONTENTSIZE_ERROR) \
                          and \
                          not m.ZSTD_isError(m.ZSTD_findFrameCompressedSize(ffi.from_buffer(data),
                                                                            len(data))) ):
                        initial_buffer_size = decompressed_size

            # Prepare input buffer w/wo unconsumed data
            if self._in_begin == self._in_end:
                # No unconsumed data
                use_input_buffer = False

                in_buf.src = ffi.from_buffer(data)
                in_buf.size = len(data)
                in_buf.pos = 0
            elif len(data) == 0:
                # Has unconsumed data, fast path for b"".
                assert self._in_begin < self._in_end
                use_input_buffer = True

                in_buf.src = self._input_buffer + self._in_begin
                in_buf.size = self._in_end - self._in_begin
                in_buf.pos = 0
            else:
                # Has unconsumed data
                use_input_buffer = True

                # Unconsumed data size in input_buffer
                used_now = self._in_end - self._in_begin
                # Number of bytes we can append to input buffer
                avail_now = self._input_buffer_size - self._in_end
                # Number of bytes we can append if we move existing
                # contents to beginning of buffer
                avail_total = self._input_buffer_size - used_now

                assert (used_now > 0
                        and avail_now >= 0
                        and avail_total >= 0)

                if avail_total < len(data):
                    new_size = used_now + len(data)
                    # Allocate with new size
                    tmp = _new_nonzero("char[]", new_size)
                    if tmp == ffi.NULL:
                        raise MemoryError

                    # Copy unconsumed data to the beginning of new buffer
                    ffi.memmove(tmp,
                                self._input_buffer+self._in_begin,
                                used_now)

                    # Switch to new buffer
                    self._input_buffer = tmp
                    self._input_buffer_size = new_size

                    # Set begin & end position
                    self._in_begin = 0
                    self._in_end = used_now
                elif avail_now < len(data):
                    # Move unconsumed data to the beginning
                    ffi.memmove(self._input_buffer,
                                self._input_buffer+self._in_begin,
                                used_now)

                    # Set begin & end position
                    self._in_begin = 0
                    self._in_end = used_now

                # Copy data to input buffer
                ffi.memmove(self._input_buffer+self._in_end,
                            ffi.from_buffer(data), len(data))
                self._in_end += len(data)

                in_buf.src = self._input_buffer + self._in_begin
                in_buf.size = used_now + len(data)
                in_buf.pos = 0
            # Now in_buf.pos == 0

            ret = self._decompress_impl(in_buf, max_length, initial_buffer_size)

            # Unconsumed input data
            if in_buf.pos == in_buf.size:
                if self._type == _TYPE_DEC:
                    if len(ret) == max_length or self._eof:
                        self._needs_input = False
                    else:
                        self._needs_input = True
                else:
                    if len(ret) == max_length and not self._at_frame_edge:
                        self._needs_input = False
                    else:
                        self._needs_input = True

                if use_input_buffer:
                    # Clear input_buffer
                    self._in_begin = 0
                    self._in_end = 0
            else:
                data_size = in_buf.size - in_buf.pos

                self._needs_input = False
                if self._type == _TYPE_ENDLESS_DEC:
                    self._at_frame_edge = False

                if not use_input_buffer:
                    # Discard buffer if it's too small
                    if (self._input_buffer == ffi.NULL
                          or self._input_buffer_size < data_size):
                        # Create new buffer
                        self._input_buffer = _new_nonzero("char[]", data_size)
                        if self._input_buffer == ffi.NULL:
                            self._input_buffer_size = 0
                            raise MemoryError
                        # Set buffer size
                        self._input_buffer_size = data_size

                    # Copy unconsumed data
                    ffi.memmove(self._input_buffer, in_buf.src+in_buf.pos, data_size)
                    self._in_begin = 0
                    self._in_end = data_size
                else:
                    # Use input buffer
                    self._in_begin += in_buf.pos

            return ret
        except:
            # Reset variables
            self._in_begin = 0
            self._in_end = 0

            self._needs_input = True
            if self._type == _TYPE_DEC:
                self._eof = False
            else:
                self._at_frame_edge = True

            # Resetting session never fail
            m.ZSTD_DCtx_reset(self._dctx, m.ZSTD_reset_session_only)
            raise
        finally:
            self._lock.release()

    def __reduce__(self):
        msg = "Cannot pickle %s object." % type(self)
        raise TypeError(msg)

class ZstdDecompressor(_Decompressor):
    """A streaming decompressor, it stops after a frame is decompressed.
    Thread-safe at method level."""

    def __init__(self, zstd_dict=None, option=None):
        """Initialize a ZstdDecompressor object.

        Arguments
        zstd_dict: A ZstdDict object, pre-trained zstd dictionary.
        option:    A dict object that contains advanced decompression parameters.
        """
        super().__init__(zstd_dict, option)
        self._eof = False
        self._unused_data = ffi.NULL
        self._type = _TYPE_DEC

    def decompress(self, data, max_length=-1):
        """Decompress data, return a chunk of decompressed data if possible, or b''
        otherwise.

        It stops after a frame is decompressed.

        Arguments
        data:       A bytes-like object, zstd data to be decompressed.
        max_length: Maximum size of returned data. When it is negative, the size of
                    output buffer is unlimited. When it is nonnegative, returns at
                    most max_length bytes of decompressed data.
        """
        return self._stream_decompress(data, max_length)

    @property
    def eof(self):
        """True means the end of the first frame has been reached. If decompress data
        after that, an EOFError exception will be raised."""
        return self._eof

    @property
    def unused_data(self):
        """A bytes object. When ZstdDecompressor object stops after a frame is
        decompressed, unused input data after the frame. Otherwise this will be b''."""
        try:
            self._lock.acquire()

            if not self._eof:
                return b""
            else:
                if self._unused_data == ffi.NULL:
                    if self._input_buffer == ffi.NULL:
                        self._unused_data = b""
                    else:
                        self._unused_data = \
                            ffi.buffer(self._input_buffer)[self._in_begin:self._in_end]
                return self._unused_data
        finally:
            self._lock.release()

class EndlessZstdDecompressor(_Decompressor):
    """A streaming decompressor, accepts multiple concatenated frames.
    Thread-safe at method level."""

    def __init__(self, zstd_dict=None, option=None):
        """Initialize an EndlessZstdDecompressor object.

        Arguments
        zstd_dict: A ZstdDict object, pre-trained zstd dictionary.
        option:    A dict object that contains advanced decompression parameters.
        """
        super().__init__(zstd_dict, option)
        self._at_frame_edge = True
        self._type = _TYPE_ENDLESS_DEC

    def decompress(self, data, max_length=-1):
        """Decompress data, return a chunk of decompressed data if possible, or b''
        otherwise.

        Arguments
        data:       A bytes-like object, zstd data to be decompressed.
        max_length: Maximum size of returned data. When it is negative, the size of
                    output buffer is unlimited. When it is nonnegative, returns at
                    most max_length bytes of decompressed data.
        """
        return self._stream_decompress(data, max_length)

    @property
    def at_frame_edge(self):
        """True when both input and output streams are at a frame edge, means a frame is
        completely decoded and fully flushed, or the decompressor just be initialized.

        This flag could be used to check data integrity in some cases.
        """
        return self._at_frame_edge

def decompress(data, zstd_dict=None, option=None):
    """Decompress a zstd data, return a bytes object.

    Support multiple concatenated frames.

    Arguments
    data:      A bytes-like object, compressed zstd data.
    zstd_dict: A ZstdDict object, pre-trained zstd dictionary.
    option:    A dict object, contains advanced decompression parameters.
    """
    # Initialize & set ZstdDecompressor
    decomp = _Decompressor(zstd_dict, option)
    decomp._at_frame_edge = True
    decomp._type = _TYPE_ENDLESS_DEC

    # Prepare input data
    in_buf = _new_nonzero("ZSTD_inBuffer *")
    if in_buf == ffi.NULL:
        raise MemoryError

    in_buf.src = ffi.from_buffer(data)
    in_buf.size = len(data)
    in_buf.pos = 0

    # Get decompressed size
    decompressed_size = m.ZSTD_getFrameContentSize(ffi.from_buffer(data), len(data))
    if decompressed_size not in (m.ZSTD_CONTENTSIZE_UNKNOWN,
                                 m.ZSTD_CONTENTSIZE_ERROR):
        initial_size = decompressed_size
    else:
        initial_size = -1

    # Decompress
    ret = decomp._decompress_impl(in_buf, -1, initial_size)

    # Check data integrity. at_frame_edge flag is True when the both input and
    # output streams are at a frame edge.
    if not decomp._at_frame_edge:
        extra_msg = "." if (len(ret) == 0) \
                        else (", if want to output these decompressed data, use "
                              "an EndlessZstdDecompressor object to decompress.")
        msg = ("Decompression failed: zstd data ends in an incomplete "
               "frame, maybe the input data was truncated. Decompressed "
               "data is %d bytes%s") % (len(ret), extra_msg)
        raise ZstdError(msg)

    return ret

def _write_to_output(output_stream, out_mv, out_buf):
    write_pos = 0

    while write_pos < out_buf.pos:
        left_bytes = out_buf.pos - write_pos

        write_bytes = output_stream.write(out_mv[write_pos:out_buf.pos])
        if write_bytes is None:
            # The raw stream is set not to block and no single
            # byte could be readily written to it
            continue
        else:
            if write_bytes < 0 or write_bytes > left_bytes:
                msg = ("output_stream.write() returned invalid length %d "
+                      "(should be 0 <= value <= %d)")
                raise ValueError(msg % (write_bytes, left_bytes))
            write_pos += write_bytes

def _invoke_callback(callback, in_mv, in_buf, callback_read_pos,
                     out_mv, out_buf, total_input_size, total_output_size):
    # Input memoryview
    in_size = in_buf.size - callback_read_pos
    # Only yield read data once
    callback_read_pos = in_buf.size
    in_memoryview = in_mv[:in_size]

    # Output memoryview
    out_memoryview = out_mv[:out_buf.pos]

    # Callback
    callback(total_input_size, total_output_size,
             in_memoryview, out_memoryview)

    return callback_read_pos

def compress_stream(input_stream, output_stream, *,
                    level_or_option = None, zstd_dict = None,
                    pledged_input_size = None,
                    read_size = m.ZSTD_CStreamInSize(),
                    write_size = m.ZSTD_CStreamOutSize(),
                    callback = None):
    """Compresses input_stream and writes the compressed data to output_stream, it
    doesn't close the streams.

    If input stream is b'', nothing will be written to output stream.

    Return a tuple, (total_input, total_output), the items are int objects.

    Arguments
    input_stream: Input stream that has a .readinto(b) method.
    output_stream: Output stream that has a .write(b) method. If use callback
        function, this argument can be None.
    level_or_option: When it's an int object, it represents the compression
        level. When it's a dict object, it contains advanced compression
        parameters.
    zstd_dict: A ZstdDict object, pre-trained zstd dictionary.
    pledged_input_size: If set this argument to the size of input data, the size
        will be written into frame header. If the actual input data doesn't match
        it, a ZstdError will be raised.
    read_size: Input buffer size, in bytes.
    write_size: Output buffer size, in bytes.
    callback: A callback function that accepts four parameters:
        (total_input, total_output, read_data, write_data), the first two are
        int objects, the last two are readonly memoryview objects.
    """
    level = 0  # 0 means use zstd's default compression level
    use_multithreaded = False
    total_input_size = 0
    total_output_size = 0

    # Check parameters
    if not hasattr(input_stream, "readinto"):
        raise TypeError("input_stream argument should have a .readinto(b) method.")
    if output_stream is not None:
        if not hasattr(output_stream, "write"):
            raise TypeError("output_stream argument should have a .write(b) method.")
    else:
        if callback is None:
            msg = ("At least one of output_stream argument and "
                   "callback argument should be non-None.")
            raise TypeError(msg)

    try:
        if read_size <= 0 or write_size <= 0:
            raise Exception
    except:
        msg = ("read_size argument and write_size argument should "
               "be positive numbers.")
        raise ValueError(msg)

    if pledged_input_size is not None:
        try:
            if pledged_input_size < 0 or pledged_input_size > 2**64-1:
                raise Exception
        except:
            msg = ("pledged_input_size argument should be 64-bit "
                   "unsigned integer value.")
            raise ValueError(msg)

    try:
        # Initialize & set ZstdCompressor
        cctx = m.ZSTD_createCCtx()
        if cctx == ffi.NULL:
            raise ZstdError("Unable to create ZSTD_CCtx instance.")

        if level_or_option is not None:
            level, use_multithreaded = _set_c_parameters(cctx, level_or_option)
        if zstd_dict is not None:
            _load_c_dict(cctx, zstd_dict, level)

        if pledged_input_size is not None:
            zstd_ret = m.ZSTD_CCtx_setPledgedSrcSize(cctx, pledged_input_size)
            if m.ZSTD_isError(zstd_ret):
                _set_zstd_error(_ErrorType.ERR_COMPRESS, zstd_ret)

        # Input buffer, in.size and in.pos will be set later.
        in_buf = _new_nonzero("ZSTD_inBuffer *")
        if in_buf == ffi.NULL:
            raise MemoryError

        _input_block = ffi.buffer(_new_nonzero("char[]", read_size))
        in_mv = memoryview(_input_block)
        in_buf.src = ffi.from_buffer(_input_block)

        # Output buffer, out.pos will be set later.
        out_buf = _new_nonzero("ZSTD_outBuffer *")
        if out_buf == ffi.NULL:
            raise MemoryError

        _output_block = ffi.buffer(_new_nonzero("char[]", write_size))
        out_mv = memoryview(_output_block)
        out_buf.dst = ffi.from_buffer(_output_block)
        out_buf.size = write_size

        # Read
        while True:
            # Invoke .readinto() method
            read_bytes = input_stream.readinto(_input_block)
            if read_bytes is None:
                # Non-blocking mode and no bytes are available
                continue
            else:
                if read_bytes < 0 or read_bytes > read_size:
                    msg = ("input_stream.readinto() returned invalid length "
                           "%d (should be 0 <= value <= %d)")
                    raise ValueError(msg % (read_bytes, read_size))

                # Don't generate empty frame
                if read_bytes == 0 and total_input_size == 0:
                    break
                total_input_size += read_bytes

            in_buf.size = read_bytes
            in_buf.pos = 0
            callback_read_pos = 0
            end_directive = m.ZSTD_e_end \
                            if (read_bytes == 0) \
                            else m.ZSTD_e_continue

            # Compress & write
            while True:
                # Output position
                out_buf.pos = 0

                # Compress
                if use_multithreaded and end_directive == m.ZSTD_e_continue:
                    while True:
                        zstd_ret = m.ZSTD_compressStream2(cctx, out_buf, in_buf, m.ZSTD_e_continue)
                        if (out_buf.pos == out_buf.size
                              or in_buf.pos == in_buf.size
                              or m.ZSTD_isError(zstd_ret)):
                            break
                else:
                    zstd_ret = m.ZSTD_compressStream2(cctx, out_buf, in_buf, end_directive)

                if m.ZSTD_isError(zstd_ret):
                    _set_zstd_error(_ErrorType.ERR_COMPRESS, zstd_ret)

                # Accumulate output bytes
                total_output_size += out_buf.pos

                # Write all output to output_stream
                if output_stream is not None:
                    _write_to_output(output_stream, out_mv, out_buf)

                # Invoke callback
                if callback is not None:
                    callback_read_pos = _invoke_callback(
                                     callback, in_mv, in_buf, callback_read_pos,
                                     out_mv, out_buf, total_input_size, total_output_size)

                # Finished
                if end_directive == m.ZSTD_e_continue:
                    if in_buf.pos == in_buf.size:
                        break
                else:
                    if zstd_ret == 0:
                        break

            # Input stream ended
            if read_bytes == 0:
                break

        return (total_input_size, total_output_size)
    finally:
        m.ZSTD_freeCCtx(cctx)

def decompress_stream(input_stream, output_stream, *,
                      zstd_dict = None, option = None,
                      read_size = m.ZSTD_DStreamInSize(),
                      write_size = m.ZSTD_DStreamOutSize(),
                      callback = None):
    """Decompresses input_stream and writes the decompressed data to output_stream,
    it doesn't close the streams.

    Supports multiple concatenated frames.

    Return a tuple, (total_input, total_output), the items are int objects.

    Arguments
    input_stream: Input stream that has a .readinto(b) method.
    output_stream: Output stream that has a .write(b) method. If use callback
        function, this argument can be None.
    zstd_dict: A ZstdDict object, pre-trained zstd dictionary.
    option: A dict object, contains advanced decompression parameters.
    read_size: Input buffer size, in bytes.
    write_size: Output buffer size, in bytes.
    callback: A callback function that accepts four parameters:
        (total_input, total_output, read_data, write_data), the first two are
        int objects, the last two are readonly memoryview objects.
    """
    at_frame_edge = True
    total_input_size = 0
    total_output_size = 0

    # Check parameters
    if not hasattr(input_stream, "readinto"):
        raise TypeError("input_stream argument should have a .readinto(b) method.")
    if output_stream is not None:
        if not hasattr(output_stream, "write"):
            raise TypeError("output_stream argument should have a .write(b) method.")
    else:
        if callback is None:
            msg = ("At least one of output_stream argument and "
                   "callback argument should be non-None.")
            raise TypeError(msg)

    try:
        if read_size <= 0 or write_size <= 0:
            raise Exception
    except:
        msg = ("read_size argument and write_size argument should "
               "be positive numbers.")
        raise ValueError(msg)

    try:
        # Initialize & set ZstdDecompressor
        dctx = m.ZSTD_createDCtx()
        if dctx == ffi.NULL:
            raise ZstdError("Unable to create ZSTD_DCtx instance.")

        if zstd_dict is not None:
            _load_d_dict(dctx, zstd_dict)
        if option is not None:
            _set_d_parameters(dctx, option)

        # Input buffer, in.size and in.pos will be set later.
        in_buf = _new_nonzero("ZSTD_inBuffer *")
        if in_buf == ffi.NULL:
            raise MemoryError

        _input_block = ffi.buffer(_new_nonzero("char[]", read_size))
        in_mv = memoryview(_input_block)
        in_buf.src = ffi.from_buffer(_input_block)

        # Output buffer, out.pos will be set later.
        out_buf = _new_nonzero("ZSTD_outBuffer *")
        if out_buf == ffi.NULL:
            raise MemoryError

        _output_block = ffi.buffer(_new_nonzero("char[]", write_size))
        out_mv = memoryview(_output_block)
        out_buf.dst = ffi.from_buffer(_output_block)
        out_buf.size = write_size

        # Read
        while True:
            # Invoke .readinto() method
            read_bytes = input_stream.readinto(_input_block)
            if read_bytes is None:
                # Non-blocking mode and no bytes are available
                continue
            else:
                if read_bytes < 0 or read_bytes > read_size:
                    msg = ("input_stream.readinto() returned invalid length "
                           "%d (should be 0 <= value <= %d)")
                    raise ValueError(msg % (read_bytes, read_size))

                total_input_size += read_bytes

            in_buf.size = read_bytes
            in_buf.pos = 0
            callback_read_pos = 0

            # Decompress & write
            while True:
                # AFE check for setting .at_frame_edge flag, search "AFE" in
                # _zstdmodule.c to see details.
                if at_frame_edge and in_buf.pos == in_buf.size:
                    break

                # Output position
                out_buf.pos = 0

                # Decompress
                zstd_ret = m.ZSTD_decompressStream(dctx, out_buf, in_buf)
                if m.ZSTD_isError(zstd_ret):
                    _set_zstd_error(_ErrorType.ERR_DECOMPRESS, zstd_ret)

                # Set .af_frame_edge flag
                at_frame_edge = True if (zstd_ret == 0) else False

                # Accumulate output bytes
                total_output_size += out_buf.pos

                # Write all output to output_stream
                if output_stream is not None:
                    _write_to_output(output_stream, out_mv, out_buf)

                # Invoke callback
                if callback is not None:
                    callback_read_pos = _invoke_callback(
                                     callback, in_mv, in_buf, callback_read_pos,
                                     out_mv, out_buf, total_input_size, total_output_size)

                # Finished. When a frame is fully decoded, but not fully flushed,
                # the last byte is kept as hostage, it will be released when all
                # output is flushed.
                if in_buf.pos == in_buf.size:
                    # If input stream ends in an incomplete frame, output as
                    # much as possible.
                    if (read_bytes == 0
                          and not at_frame_edge
                          and out_buf.pos == out_buf.size):
                        continue

                    break

            # Input stream ended
            if read_bytes == 0:
                # Check data integrity. at_frame_edge flag is 1 when both input
                # and output streams are at a frame edge.
                if not at_frame_edge:
                    msg = ("Decompression failed: zstd data ends in an "
                           "incomplete frame, maybe the input data was "
                           "truncated. Total input %d bytes, total output "
                           "%d bytes.") % (total_input_size, total_output_size)
                    raise ZstdError(msg)
                break

        return (total_input_size, total_output_size)
    finally:
        m.ZSTD_freeDCtx(dctx)

def _train_dict(chunks, chunk_sizes, dict_size):
    # C code
    if dict_size <= 0:
        raise ValueError("dict_size argument should be positive number.")

    # Prepare chunk_sizes
    _chunks_number = len(chunk_sizes)
    _sizes = _new_nonzero("size_t[]", _chunks_number)
    if _sizes == ffi.NULL:
        raise MemoryError

    for i, size in enumerate(chunk_sizes):
        _sizes[i] = size

    # Allocate dict buffer
    _dst_dict_bytes = _new_nonzero("char[]", dict_size)
    if _dst_dict_bytes == ffi.NULL:
        raise MemoryError

    # Train
    zstd_ret = m.ZDICT_trainFromBuffer(_dst_dict_bytes, dict_size,
                                       ffi.from_buffer(chunks),
                                       _sizes, _chunks_number)
    if m.ZDICT_isError(zstd_ret):
        _set_zstd_error(_ErrorType.ERR_TRAIN_DICT, zstd_ret)

    # Resize dict_buffer
    b = ffi.buffer(_dst_dict_bytes)[:zstd_ret]
    return b

def _finalize_dict(custom_dict_bytes,
                   samples_bytes, samples_size_list,
                   dict_size, compression_level):
    # If m.ZSTD_VERSION_NUMBER < 10405, m.ZDICT_finalizeDictionary() is an
    # empty function defined in build_cffi.py.
    # If m.ZSTD_versionNumber() < 10405, m.ZDICT_finalizeDictionary() doesn't
    # exist in run-time zstd library.
    if (m.ZSTD_VERSION_NUMBER < 10405          # compile-time version
          or m.ZSTD_versionNumber() < 10405):  # run-time version
        msg = ("finalize_dict function only available when the underlying "
               "zstd library's version is greater than or equal to v1.4.5. "
               "At pyzstd module's compile-time, zstd version is %d. At "
               "pyzstd module's run-time, zstd version is %d.") % \
               (m.ZSTD_VERSION_NUMBER, m.ZSTD_versionNumber())
        raise NotImplementedError(msg)

    # C code
    if dict_size <= 0:
        raise ValueError("dict_size argument should be positive number.")

    # Prepare chunk_sizes
    _chunks_number = len(samples_size_list)
    _sizes = _new_nonzero("size_t[]", _chunks_number)
    if _sizes == ffi.NULL:
        raise MemoryError

    for i, size in enumerate(samples_size_list):
        _sizes[i] = size

    # Allocate dict buffer
    _dst_dict_bytes = _new_nonzero("char[]", dict_size)
    if _dst_dict_bytes == ffi.NULL:
        raise MemoryError

    # Parameters
    params = _new_nonzero("ZDICT_params_t *")
    if params == ffi.NULL:
        raise MemoryError
    # Optimize for a specific zstd compression level, 0 means default.
    params.compressionLevel = compression_level
    # Write log to stderr, 0 = none.
    params.notificationLevel = 0
    # Force dictID value, 0 means auto mode (32-bits random value).
    params.dictID = 0

    # Finalize
    zstd_ret = m.ZDICT_finalizeDictionary(
                   _dst_dict_bytes, dict_size,
                   ffi.from_buffer(custom_dict_bytes), len(custom_dict_bytes),
                   ffi.from_buffer(samples_bytes), _sizes, _chunks_number,
                   params[0])
    if m.ZDICT_isError(zstd_ret):
        _set_zstd_error(_ErrorType.ERR_FINALIZE_DICT, zstd_ret)

    # Resize dict_buffer
    b = ffi.buffer(_dst_dict_bytes)[:zstd_ret]
    return b

_nt_frame_info = namedtuple('frame_info',
                            ['decompressed_size', 'dictionary_id'])

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

    It's possible to append more items to the namedtuple in the future.
    """

    content_size = m.ZSTD_getFrameContentSize(
                      ffi.from_buffer(frame_buffer), len(frame_buffer))
    if content_size == m.ZSTD_CONTENTSIZE_UNKNOWN:
        content_size = None
    elif content_size == m.ZSTD_CONTENTSIZE_ERROR:
        msg = ("Error when getting a zstd frame's decompressed size, "
               "make sure the frame_buffer argument starts from the "
               "beginning of a frame and its size larger than the "
               "frame header (6~18 bytes).")
        raise ZstdError(msg)

    dict_id = m.ZSTD_getDictID_fromFrame(
                  ffi.from_buffer(frame_buffer), len(frame_buffer))

    ret = _nt_frame_info(content_size, dict_id)
    return ret

def get_frame_size(frame_buffer):
    """Get the size of a zstd frame, including frame header and 4-byte checksum if it
    has.

    It will iterate all blocks' header within a frame, to accumulate the frame
    size.

    Arguments
    frame_buffer: A bytes-like object, it should starts from the beginning of a
                  frame, and contains at least one complete frame.
    """

    frame_size = m.ZSTD_findFrameCompressedSize(
                     ffi.from_buffer(frame_buffer), len(frame_buffer))
    if m.ZSTD_isError(frame_size):
        _set_zstd_error(_ErrorType.ERR_GET_FRAME_SIZE, frame_size)

    return frame_size
