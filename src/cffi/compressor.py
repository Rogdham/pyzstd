from threading import Lock
from warnings import warn

from .common import m, ffi, _new_nonzero, _nbytes, \
                    ZstdError, _set_c_parameters, \
                    _set_zstd_error, _ErrorType
from .dict import _load_c_dict
from .output_buffer import _BlocksOutputBuffer

class _Compressor:
    def __init__(self, level_or_option=None, zstd_dict=None):
        self._use_multithread = False
        self._lock = Lock()
        level = 0  # 0 means use zstd's default compression level

        self._singleton_in_buf = _new_nonzero("ZSTD_inBuffer *")
        if self._singleton_in_buf == ffi.NULL:
            raise MemoryError

        self._singleton_out_buf = _new_nonzero("ZSTD_outBuffer *")
        if self._singleton_out_buf == ffi.NULL:
            raise MemoryError

        # Compression context
        self._cctx = m.ZSTD_createCCtx()
        if self._cctx == ffi.NULL:
            raise ZstdError("Unable to create ZSTD_CCtx instance.")

        # Set compressLevel/option to compression context
        if level_or_option is not None:
            level, self._use_multithread = \
                  _set_c_parameters(self._cctx, level_or_option)

        # Load dictionary to compression context
        if zstd_dict is not None:
            _load_c_dict(self._cctx, zstd_dict, level)
            self.__dict = zstd_dict

    def __del__(self):
        try:
            m.ZSTD_freeCCtx(self._cctx)
            self._cctx = ffi.NULL
        except AttributeError:
            pass

    def _compress_impl(self, data, end_directive, rich_mem):
        # Input buffer
        in_buf = self._singleton_in_buf
        in_buf.src = ffi.from_buffer(data)
        in_buf.size = _nbytes(data)
        in_buf.pos = 0

        # Output buffer
        out_buf = self._singleton_out_buf
        out = _BlocksOutputBuffer()

        # Initialize output buffer
        if rich_mem:
            init_size = m.ZSTD_compressBound(_nbytes(data))
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
        in_buf = self._singleton_in_buf
        in_buf.src = ffi.from_buffer(data)
        in_buf.size = _nbytes(data)
        in_buf.pos = 0

        # Output buffer
        out_buf = self._singleton_out_buf
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

            # Like ._compress_impl(), output as much as possible.
            if out_buf.pos == out_buf.size:
                out.grow(out_buf)
            elif in_buf.pos == in_buf.size:
                # Finished
                return out.finish(out_buf)

    def __reduce__(self):
        msg = "Cannot pickle %s object." % type(self)
        raise TypeError(msg)

class ZstdCompressor(_Compressor):
    """A streaming compressor. Thread-safe at method level."""

    CONTINUE = m.ZSTD_e_continue
    """Used for mode parameter in .compress() method.

    Collect more data, encoder decides when to output compressed result, for optimal
    compression ratio. Usually used for traditional streaming compression.
    """

    FLUSH_BLOCK = m.ZSTD_e_flush
    """Used for mode parameter in .compress(), .flush() methods.

    Flush any remaining data, but don't close the current frame. Usually used for
    communication scenarios.

    If there is data, it creates at least one new block, that can be decoded
    immediately on reception. If no remaining data, no block is created, return b''.

    Note: Abuse of this mode will reduce compression ratio. Use it only when
    necessary.
    """

    FLUSH_FRAME = m.ZSTD_e_end
    """Used for mode parameter in .compress(), .flush() methods.

    Flush any remaining data, and close the current frame. Usually used for
    traditional flush.

    Since zstd data consists of one or more independent frames, data can still be
    provided after a frame is closed.

    Note: Abuse of this mode will reduce compression ratio, and some programs can
    only decompress single frame data. Use it only when necessary.
    """

    def __init__(self, level_or_option=None, zstd_dict=None):
        """Initialize a ZstdCompressor object.

        Parameters
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

        Parameters
        data: A bytes-like object, data to be compressed.
        mode: Can be these 3 values .CONTINUE, .FLUSH_BLOCK, .FLUSH_FRAME.
        """
        if mode not in (ZstdCompressor.CONTINUE,
                        ZstdCompressor.FLUSH_BLOCK,
                        ZstdCompressor.FLUSH_FRAME):
            msg = ("mode argument wrong value, it should be one of "
                   "ZstdCompressor.CONTINUE, ZstdCompressor.FLUSH_BLOCK, "
                   "ZstdCompressor.FLUSH_FRAME.")
            raise ValueError(msg)

        with self._lock:
            try:
                if self._use_multithread and mode == ZstdCompressor.CONTINUE:
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

    def flush(self, mode=FLUSH_FRAME):
        """Flush any remaining data in internal buffer.

        Since zstd data consists of one or more independent frames, the compressor
        object can still be used after this method is called.

        Parameter
        mode: Can be these 2 values .FLUSH_FRAME, .FLUSH_BLOCK.
        """
        if mode not in (ZstdCompressor.FLUSH_FRAME, ZstdCompressor.FLUSH_BLOCK):
            msg = ("mode argument wrong value, it should be "
                   "ZstdCompressor.FLUSH_FRAME or ZstdCompressor.FLUSH_BLOCK.")
            raise ValueError(msg)

        with self._lock:
            try:
                ret = self._compress_impl(b"", mode, False)
                self.__last_mode = mode
                return ret
            except:
                self.__last_mode = m.ZSTD_e_end
                # Resetting cctx's session never fail
                m.ZSTD_CCtx_reset(self._cctx, m.ZSTD_reset_session_only)
                raise

    def _set_pledged_input_size(self, size):
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
        # Get size value
        if size is None:
            size = m.ZSTD_CONTENTSIZE_UNKNOWN
        else:
            try:
                if size < 0 or size > 2**64-1:
                    raise Exception
            except:
                msg = ("size argument should be 64-bit unsigned integer "
                       "value, or None.")
                raise ValueError(msg)

        with self._lock:
            # Check the current mode
            if self.__last_mode != m.ZSTD_e_end:
                msg = ("._set_pledged_input_size() method must be called "
                       "when (.last_mode == .FLUSH_FRAME).")
                raise RuntimeError(msg)

            # Set pledged content size
            zstd_ret = m.ZSTD_CCtx_setPledgedSrcSize(self._cctx, size)
            if m.ZSTD_isError(zstd_ret):
                _set_zstd_error(_ErrorType.ERR_SET_PLEDGED_INPUT_SIZE, zstd_ret)

    @property
    def last_mode(self):
        """The last mode used to this compressor object, its value can be .CONTINUE,
        .FLUSH_BLOCK, .FLUSH_FRAME. Initialized to .FLUSH_FRAME.

        It can be used to get the current state of a compressor, such as, data flushed,
        a frame ended.
        """
        return self.__last_mode

class RichMemZstdCompressor(_Compressor):
    """A compressor use rich memory mode. It is designed to allocate more memory,
    but faster in some cases.
    """

    def __init__(self, level_or_option=None, zstd_dict=None):
        """Initialize a RichMemZstdCompressor object.

        Parameters
        level_or_option: When it's an int object, it represents the compression level.
                         When it's a dict object, it contains advanced compression
                         parameters.
        zstd_dict:       A ZstdDict object, pre-trained zstd dictionary.
        """
        super().__init__(level_or_option=level_or_option, zstd_dict=zstd_dict)

        if self._use_multithread:
            msg = ('Currently "rich memory mode" has no effect on '
                   'zstd multi-threaded compression (set '
                   '"CParameter.nbWorkers" >= 1), it will allocate '
                   'unnecessary memory.')
            warn(msg, ResourceWarning, 1)

    def compress(self, data):
        """Compress data using rich memory mode, return a single zstd frame.

        Compressing b'' will get an empty content frame (9 bytes or more).

        Parameter
        data: A bytes-like object, data to be compressed.
        """
        with self._lock:
            try:
                ret = self._compress_impl(data, m.ZSTD_e_end, True)
                return ret
            except:
                # Resetting cctx's session never fail
                m.ZSTD_CCtx_reset(self._cctx, m.ZSTD_reset_session_only)
                raise
