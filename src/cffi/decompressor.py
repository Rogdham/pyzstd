from threading import Lock

from .common import m, ffi, ZstdError, \
                    _new_nonzero, _set_d_parameters, \
                    _set_zstd_error, _ErrorType
from .dict import _load_d_dict
from .output_buffer import _BlocksOutputBuffer

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

        self._singleton_in_buf = _new_nonzero("ZSTD_inBuffer *")
        if self._singleton_in_buf == ffi.NULL:
            raise MemoryError

        self._singleton_out_buf = _new_nonzero("ZSTD_outBuffer *")
        if self._singleton_out_buf == ffi.NULL:
            raise MemoryError

        # Decompression context
        self._dctx = m.ZSTD_createDCtx()
        if self._dctx == ffi.NULL:
            raise ZstdError("Unable to create ZSTD_DCtx instance.")

        # Load dictionary to compression context
        if zstd_dict is not None:
            _load_d_dict(self._dctx, zstd_dict)
            self.__dict = zstd_dict

        # Set compressLevel/option to compression context
        if option is not None:
            _set_d_parameters(self._dctx, option)

    def __del__(self):
        try:
            m.ZSTD_freeDCtx(self._dctx)
            self._dctx = ffi.NULL
        except AttributeError:
            pass

    @property
    def needs_input(self):
        """If the max_length output limit in .decompress() method has been reached, and
        the decompressor has (or may has) unconsumed input data, it will be set to
        False. In this case, pass b'' to .decompress() method may output further data.
        """
        return self._needs_input

    def _decompress_impl(self, in_buf, max_length, initial_size):
        # The first AFE check for setting .at_frame_edge flag, search "AFE" in
        # decompressor.c to see details.
        if self._type == _TYPE_ENDLESS_DEC:
            if self._at_frame_edge and in_buf.pos == in_buf.size:
                return b""

        # Output buffer
        out_buf = self._singleton_out_buf
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
                self._at_frame_edge = (zstd_ret == 0)

                # The second AFE check for setting .at_frame_edge flag, search
                # "AFE" in decompressor.c to see details.
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
        self._lock.acquire()
        try:
            initial_buffer_size = -1
            in_buf = self._singleton_in_buf

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
            # Reset decompressor's states/session
            self.__reset_session()
            raise
        finally:
            self._lock.release()

    def __reset_session(self):
        # Reset variables
        self._in_begin = 0
        self._in_end = 0

        self._needs_input = True
        if self._type == _TYPE_DEC:
            self._eof = False
            self._unused_data = ffi.NULL
        else:
            self._at_frame_edge = True

        # Resetting session never fail
        m.ZSTD_DCtx_reset(self._dctx, m.ZSTD_reset_session_only)

    def _reset_session(self):
        """This is an undocumented method. Reset decompressor's states/session, don't
        reset parameters and dictionary.
        """
        with self._lock:
            self.__reset_session()

    def __reduce__(self):
        msg = "Cannot pickle %s object." % type(self)
        raise TypeError(msg)

class ZstdDecompressor(_Decompressor):
    """A streaming decompressor, it stops after a frame is decompressed.
    Thread-safe at method level."""

    def __init__(self, zstd_dict=None, option=None):
        """Initialize a ZstdDecompressor object.

        Parameters
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

        Parameters
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
        with self._lock:
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

class EndlessZstdDecompressor(_Decompressor):
    """A streaming decompressor, accepts multiple concatenated frames.
    Thread-safe at method level."""

    def __init__(self, zstd_dict=None, option=None):
        """Initialize an EndlessZstdDecompressor object.

        Parameters
        zstd_dict: A ZstdDict object, pre-trained zstd dictionary.
        option:    A dict object that contains advanced decompression parameters.
        """
        super().__init__(zstd_dict, option)
        self._at_frame_edge = True
        self._type = _TYPE_ENDLESS_DEC

    def decompress(self, data, max_length=-1):
        """Decompress data, return a chunk of decompressed data if possible, or b''
        otherwise.

        Parameters
        data:       A bytes-like object, zstd data to be decompressed.
        max_length: Maximum size of returned data. When it is negative, the size of
                    output buffer is unlimited. When it is nonnegative, returns at
                    most max_length bytes of decompressed data.
        """
        return self._stream_decompress(data, max_length)

    @property
    def at_frame_edge(self):
        """True when both the input and output streams are at a frame edge, means a
        frame is completely decoded and fully flushed, or the decompressor just be
        initialized.

        This flag could be used to check data integrity in some cases.
        """
        return self._at_frame_edge

def decompress(data, zstd_dict=None, option=None):
    """Decompress a zstd data, return a bytes object.

    Support multiple concatenated frames.

    Parameters
    data:      A bytes-like object, compressed zstd data.
    zstd_dict: A ZstdDict object, pre-trained zstd dictionary.
    option:    A dict object, contains advanced decompression parameters.
    """
    # EndlessZstdDecompressor
    decomp = EndlessZstdDecompressor(zstd_dict, option)

    # Prepare input data
    in_buf = decomp._singleton_in_buf
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

    # Check data integrity. at_frame_edge flag is True when the both the input
    # and output streams are at a frame edge.
    if not decomp._at_frame_edge:
        extra_msg = "." if (len(ret) == 0) \
                        else (", if want to output these decompressed data, use "
                              "decompress_stream function or "
                              "EndlessZstdDecompressor class to decompress.")
        msg = ("Decompression failed: zstd data ends in an incomplete "
               "frame, maybe the input data was truncated. Decompressed "
               "data is %d bytes%s") % (len(ret), extra_msg)
        raise ZstdError(msg)

    return ret
