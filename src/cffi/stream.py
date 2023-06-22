from .common import m, ffi, ZstdError, _new_nonzero, \
                    _set_c_parameters, _set_d_parameters, \
                    _set_zstd_error, _ErrorType, \
                    _write_to_fp
from .dict import _load_c_dict, _load_d_dict

def _invoke_callback(callback, in_mv, in_buf, callback_read_pos,
                     out_mv, out_buf, total_input_size, total_output_size):
    # Only yield input data once
    in_size = in_buf.size - callback_read_pos
    callback_read_pos = in_buf.size

    # Don't yield empty data
    if in_size == 0 and out_buf.pos == 0:
        return callback_read_pos

    # memoryview
    in_memoryview = in_mv[:in_size]
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
    write_size: Output buffer size, in bytes.
    callback: A callback function that accepts four parameters:
        (total_input, total_output, read_data, write_data), the first two are
        int objects, the last two are readonly memoryview objects.
    """
    level = 0  # 0 means use zstd's default compression level
    use_multithread = False
    total_input_size = 0
    total_output_size = 0

    # Check arguments
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
            level, use_multithread = \
                _set_c_parameters(cctx, level_or_option)
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
            if read_bytes < 0 or read_bytes > read_size:
                msg = ("input_stream.readinto() returned invalid length "
                       "%d (should be 0 <= value <= %d)") % \
                       (read_bytes, read_size)
                raise ValueError(msg)

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
                if use_multithread and end_directive == m.ZSTD_e_continue:
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
                    _write_to_fp("output_stream.write()", output_stream,
                                 out_mv, out_buf)

                # Invoke callback
                if callback is not None:
                    callback_read_pos = _invoke_callback(
                                     callback, in_mv, in_buf, callback_read_pos,
                                     out_mv, out_buf, total_input_size, total_output_size)

                # Finished
                if use_multithread and end_directive == m.ZSTD_e_continue:
                    if in_buf.pos == in_buf.size and \
                       out_buf.pos != out_buf.size:
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
    at_frame_edge = True
    total_input_size = 0
    total_output_size = 0

    # Check arguments
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
            if read_bytes < 0 or read_bytes > read_size:
                msg = ("input_stream.readinto() returned invalid length "
                       "%d (should be 0 <= value <= %d)") % \
                       (read_bytes, read_size)
                raise ValueError(msg)

            total_input_size += read_bytes

            in_buf.size = read_bytes
            in_buf.pos = 0
            callback_read_pos = 0

            # Decompress & write
            while True:
                # AFE check for setting .at_frame_edge flag, search "AFE" in
                # decompressor.c to see details.
                if at_frame_edge and in_buf.pos == in_buf.size:
                    break

                # Output position
                out_buf.pos = 0

                # Decompress
                zstd_ret = m.ZSTD_decompressStream(dctx, out_buf, in_buf)
                if m.ZSTD_isError(zstd_ret):
                    _set_zstd_error(_ErrorType.ERR_DECOMPRESS, zstd_ret)

                # Set .af_frame_edge flag
                at_frame_edge = (zstd_ret == 0)

                # Accumulate output bytes
                total_output_size += out_buf.pos

                # Write all output to output_stream
                if output_stream is not None:
                    _write_to_fp("output_stream.write()", output_stream,
                                 out_mv, out_buf)

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
                # Check data integrity. at_frame_edge flag is 1 when both the
                # input and output streams are at a frame edge.
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
