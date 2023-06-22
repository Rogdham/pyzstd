from .common import m, ffi, ZstdError, _new_nonzero, _nbytes, \
                    _set_c_parameters, _set_d_parameters, \
                    _write_to_fp, _ZSTD_DStreamSizes, \
                    _set_zstd_error, _ErrorType
from .dict import _load_c_dict, _load_d_dict
from .output_buffer import _BlocksOutputBuffer

_ZSTD_DStreamOutSize = _ZSTD_DStreamSizes[1]

class ZstdFileReader:
    def __init__(self, fp, zstd_dict, option, read_size):
        if read_size <= 0:
            raise ValueError("read_size argument should > 0")
        self._read_size = read_size

        # File states, the last three are public attributes.
        self._fp = fp
        self.eof = False
        self.pos = 0     # Decompressed position
        self.size = -1   # File size, -1 means unknown.

        # Decompression states
        self._needs_input = True
        self._at_frame_edge = True

        # Input state, need to be initialized with 0.
        self._in_buf = ffi.new("ZSTD_inBuffer *")
        if self._in_buf == ffi.NULL:
            raise MemoryError
        # Output state
        self._out_buf = _new_nonzero("ZSTD_outBuffer *")
        if self._out_buf == ffi.NULL:
            raise MemoryError
        # Lazy create forward output buffer
        self._tmp_output = ffi.NULL

        # Decompression context
        self._dctx = m.ZSTD_createDCtx()
        if self._dctx == ffi.NULL:
            raise ZstdError("Unable to create ZSTD_DCtx instance.")

        # Load dictionary to decompression context
        if zstd_dict is not None:
            _load_d_dict(self._dctx, zstd_dict)
            self.__dict = zstd_dict

        # Set option to decompression context
        if option is not None:
            _set_d_parameters(self._dctx, option)

    def __del__(self):
        try:
            m.ZSTD_freeDCtx(self._dctx)
            self._dctx = ffi.NULL
        except AttributeError:
            pass

    def _decompress_into(self, out_b, fill_full):
        # Return
        if self.eof or out_b.size == out_b.pos:
            return

        in_b = self._in_buf
        orig_pos = out_b.pos
        while True:
            if in_b.size == in_b.pos and self._needs_input:
                # Read
                self._in_dat = self._fp.read(self._read_size)
                # EOF
                if not self._in_dat:
                    if self._at_frame_edge:
                        self.eof = True
                        self.pos += out_b.pos - orig_pos
                        self.size = self.pos
                        return
                    else:
                        raise EOFError("Compressed file ended before the "
                                       "end-of-stream marker was reached")
                in_b.src = ffi.from_buffer(self._in_dat)
                in_b.size = _nbytes(self._in_dat)
                in_b.pos = 0

            # Decompress
            zstd_ret = m.ZSTD_decompressStream(self._dctx, out_b, in_b)
            if m.ZSTD_isError(zstd_ret):
                _set_zstd_error(_ErrorType.ERR_DECOMPRESS, zstd_ret)

            # Set flags
            if zstd_ret == 0:
                self._needs_input = True
                self._at_frame_edge = True
            else:
                self._needs_input = (out_b.size != out_b.pos)
                self._at_frame_edge = False

            if fill_full:
                if out_b.size != out_b.pos:
                    continue
                else:
                    self.pos += out_b.pos - orig_pos
                    return
            else:
                if out_b.pos != orig_pos:
                    self.pos += out_b.pos - orig_pos
                    return

    def readinto(self, b):
        out_b = self._out_buf
        out_b.dst = ffi.from_buffer(b)
        out_b.size = _nbytes(b)
        out_b.pos = 0

        self._decompress_into(out_b, False)
        return out_b.pos

    def readall(self):
        out_b = self._out_buf
        out = _BlocksOutputBuffer()
        if self.size >= 0:
            # Known file size
            out.initWithSize(out_b, -1, self.size - self.pos)
        else:
            # Unknown file size
            out.initAndGrow(out_b, -1)

        while True:
            self._decompress_into(out_b, True)
            if self.eof:
                # Finished
                return out.finish(out_b)
            if out_b.size == out_b.pos:
                # Grow output buffer
                out.grow(out_b)

    # If obj is None, forward to EOF.
    # If obj <= 0, do nothing.
    def forward(self, offset):
        # Lazy create forward output buffer
        if self._tmp_output == ffi.NULL:
            # ZSTD_outBuffer struct
            self._out_tmp = _new_nonzero("ZSTD_outBuffer *")
            if self._out_tmp == ffi.NULL:
                raise MemoryError
            # Forward output buffer
            self._tmp_output = _new_nonzero("char[]", _ZSTD_DStreamOutSize)
            if self._tmp_output == ffi.NULL:
                raise MemoryError
            # ZSTD_outBuffer.dst
            self._out_tmp.dst = self._tmp_output
        out_b = self._out_tmp

        # Forward to EOF
        if offset is None:
            out_b.size = _ZSTD_DStreamOutSize
            while True:
                out_b.pos = 0
                self._decompress_into(out_b, True)
                if self.eof:
                    return

        # Forward to offset
        while offset > 0:
            out_b.size = min(_ZSTD_DStreamOutSize, offset)
            out_b.pos = 0
            self._decompress_into(out_b, True)

            if self.eof:
                return
            offset -= out_b.pos

    def reset_session(self):
        # Reset decompression states
        self._needs_input = True
        self._at_frame_edge = True
        self._in_buf.size = 0
        self._in_buf.pos = 0

        # Resetting session never fail
        m.ZSTD_DCtx_reset(self._dctx, m.ZSTD_reset_session_only)

class ZstdFileWriter:
    def __init__(self, fp, level_or_option, zstd_dict, write_size):
        # File object
        self._fp = fp
        self._fp_has_flush = hasattr(fp, "flush")

        # States
        self._last_mode = m.ZSTD_e_end
        self._use_multithread = False
        level = 0  # 0 means use zstd's default compression level

        # Write buffer
        if write_size <= 0:
            raise ValueError("write_size argument should > 0")
        self._write_buffer_size = write_size

        self._write_buffer = _new_nonzero("char[]", write_size)
        if self._write_buffer == ffi.NULL:
            raise MemoryError

        # Singleton buffer objects
        self._in_buf = _new_nonzero("ZSTD_inBuffer *")
        if self._in_buf == ffi.NULL:
            raise MemoryError

        self._out_buf = _new_nonzero("ZSTD_outBuffer *")
        if self._out_buf == ffi.NULL:
            raise MemoryError

        self._out_mv = memoryview(ffi.buffer(self._write_buffer))

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

    def write(self, data):
        # Output size
        output_size = 0

        # Input buffer
        in_b = self._in_buf
        in_b.src = ffi.from_buffer(data)
        in_b.size = _nbytes(data)
        in_b.pos = 0

        # Output buffer, out.pos will be set later.
        out_b = self._out_buf
        out_b.dst = self._write_buffer
        out_b.size = self._write_buffer_size

        # State
        self._last_mode = m.ZSTD_e_continue

        while True:
            # Output position
            out_b.pos = 0

            # Compress
            if not self._use_multithread:
                zstd_ret = m.ZSTD_compressStream2(self._cctx, out_b, in_b,
                                                  m.ZSTD_e_continue)
            else:
                while True:
                    zstd_ret = m.ZSTD_compressStream2(self._cctx, out_b, in_b,
                                                      m.ZSTD_e_continue)
                    if (out_b.pos == out_b.size
                           or in_b.pos == in_b.size
                           or m.ZSTD_isError(zstd_ret)):
                        break

            if m.ZSTD_isError(zstd_ret):
                _set_zstd_error(_ErrorType.ERR_COMPRESS, zstd_ret)

            # Accumulate output bytes
            output_size += out_b.pos

            # Write output to fp
            _write_to_fp("self._fp.write()", self._fp,
                         self._out_mv, out_b)

            # Finished
            if not self._use_multithread:
                # Single-thread compression + .CONTINUE mode
                if zstd_ret == 0:
                    break
            else:
                # Multi-thread compression + .CONTINUE mode
                if in_b.size == in_b.pos and \
                   out_b.size != out_b.pos:
                    break

        return (in_b.size, output_size)

    def flush(self, mode):
        # Mode argument
        if mode not in (m.ZSTD_e_flush, m.ZSTD_e_end):
            msg = ("mode argument wrong value, it should be "
                   "ZstdFile.FLUSH_BLOCK or ZstdFile.FLUSH_FRAME.")
            raise ValueError(msg)

        # Don't generate empty content frame
        if mode == self._last_mode:
            return (0, 0)

        # Output size
        output_size = 0

        # Input buffer
        in_b = self._in_buf
        in_b.src = self._write_buffer
        in_b.size = 0
        in_b.pos = 0

        # Output buffer, out.pos will be set later.
        out_b = self._out_buf
        out_b.dst = self._write_buffer
        out_b.size = self._write_buffer_size

        # State
        self._last_mode = mode

        while True:
            # Output position
            out_b.pos = 0

            # Compress
            zstd_ret = m.ZSTD_compressStream2(self._cctx, out_b, in_b, mode)
            if m.ZSTD_isError(zstd_ret):
                _set_zstd_error(_ErrorType.ERR_COMPRESS, zstd_ret)

            # Accumulate output bytes
            output_size += out_b.pos

            # Write output to fp
            _write_to_fp("self._fp.write()", self._fp,
                         self._out_mv, out_b)

            # Finished
            if zstd_ret == 0:
                break

        # Flush
        if self._fp_has_flush:
            self._fp.flush()

        return (0, output_size)
