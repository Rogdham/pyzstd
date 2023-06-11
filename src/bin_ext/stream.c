#include "pyzstd.h"

/* Invoke callback function */
FORCE_INLINE int
invoke_callback(const _zstd_state* const state, PyObject *callback,
                ZSTD_inBuffer *in, size_t *callback_read_pos,
                ZSTD_outBuffer *out,
                const uint64_t total_input_size,
                const uint64_t total_output_size)
{
    PyObject *in_memoryview;
    PyObject *out_memoryview;
    PyObject *cb_args;
    PyObject *cb_ret;

    /* Only yield input data once */
    const size_t in_size = in->size - *callback_read_pos;
    *callback_read_pos = in->size;

    /* Don't yield empty data */
    if (in_size == 0 && out->pos == 0) {
        return 0;
    }

    /* Input memoryview */
    if (in_size != 0) {
        in_memoryview = PyMemoryView_FromMemory((char*) in->src, in_size, PyBUF_READ);
        if (in_memoryview == NULL) {
            goto error;
        }
    } else {
        in_memoryview = state->empty_readonly_memoryview;
        Py_INCREF(in_memoryview);
    }

    /* Output memoryview */
    if (out->pos != 0) {
        out_memoryview = PyMemoryView_FromMemory(out->dst, out->pos, PyBUF_READ);
        if (out_memoryview == NULL) {
            Py_DECREF(in_memoryview);
            goto error;
        }
    } else {
        out_memoryview = state->empty_readonly_memoryview;
        Py_INCREF(out_memoryview);
    }

    /* callback function arguments */
    cb_args = Py_BuildValue("KKOO",
                            total_input_size, total_output_size,
                            in_memoryview, out_memoryview);
    if (cb_args == NULL) {
        Py_DECREF(in_memoryview);
        Py_DECREF(out_memoryview);
        goto error;
    }

    /* Callback */
    cb_ret = PyObject_CallObject(callback, cb_args);
    Py_DECREF(cb_args);
    Py_DECREF(in_memoryview);
    Py_DECREF(out_memoryview);

    if (cb_ret == NULL) {
        goto error;
    }
    Py_DECREF(cb_ret);

    return 0;
error:
    return -1;
}

PyDoc_STRVAR(compress_stream_doc,
"compress_stream(input_stream, output_stream, *,\n"
"                level_or_option=None, zstd_dict=None,\n"
"                pledged_input_size=None,\n"
"                read_size=131072, write_size=131591,\n"
"                callback=None)\n"
"----\n"
"Compresses input_stream and writes the compressed data to output_stream, it\n"
"doesn't close the streams.\n\n"
"If input stream is b'', nothing will be written to output stream.\n\n"
"Return a tuple, (total_input, total_output), the items are int objects.\n\n"
"Parameters\n"
"input_stream: Input stream that has a .readinto(b) method.\n"
"output_stream: Output stream that has a .write(b) method. If use callback\n"
"    function, this parameter can be None.\n"
"level_or_option: When it's an int object, it represents the compression\n"
"    level. When it's a dict object, it contains advanced compression\n"
"    parameters.\n"
"zstd_dict: A ZstdDict object, pre-trained zstd dictionary.\n"
"pledged_input_size: If set this parameter to the size of input data, the\n"
"    size will be written into the frame header. If the actual input data\n"
"    doesn't match it, a ZstdError will be raised.\n"
"read_size: Input buffer size, in bytes.\n"
"write_size: Output buffer size, in bytes.\n"
"callback: A callback function that accepts four parameters:\n"
"    (total_input, total_output, read_data, write_data), the first two are\n"
"    int objects, the last two are readonly memoryview objects."
);

static PyObject *
compress_stream(PyObject *module, PyObject *args, PyObject *kwargs)
{
    static char *kwlist[] = {"input_stream", "output_stream",
                             "level_or_option", "zstd_dict",
                             "pledged_input_size", "read_size", "write_size",
                             "callback", NULL};
    PyObject *input_stream;
    PyObject *output_stream;
    PyObject *level_or_option = Py_None;
    PyObject *zstd_dict = Py_None;
    PyObject *pledged_input_size = Py_None;
    Py_ssize_t read_size = ZSTD_CStreamInSize();
    Py_ssize_t write_size = ZSTD_CStreamOutSize();
    PyObject *callback = Py_None;

    /* If fails, modify value in __init__.pyi and doc. */
    assert(read_size == 131072);
    assert(write_size == 131591);

    size_t zstd_ret;
    PyObject *temp;
    ZstdCompressor self = {0};
    uint64_t pledged_size_value = ZSTD_CONTENTSIZE_UNKNOWN;
    ZSTD_inBuffer in = {.src = NULL};
    ZSTD_outBuffer out = {.dst = NULL};
    PyObject *in_memoryview = NULL;
    uint64_t total_input_size = 0;
    uint64_t total_output_size = 0;
    STATE_FROM_MODULE(module);
    PyObject *ret = NULL;

    if (!PyArg_ParseTupleAndKeywords(args, kwargs,
                                     "OO|$OOOnnO:compress_stream", kwlist,
                                     &input_stream, &output_stream,
                                     &level_or_option, &zstd_dict,
                                     &pledged_input_size, &read_size, &write_size,
                                     &callback)) {
        return NULL;
    }

    /* Check arguments */
    if (!PyObject_HasAttr(input_stream, MS_MEMBER(str_readinto))) {
        PyErr_SetString(PyExc_TypeError,
                        "input_stream argument should have a .readinto(b) method.");
        return NULL;
    }

    if (output_stream != Py_None) {
        if (!PyObject_HasAttr(output_stream, MS_MEMBER(str_write))) {
            PyErr_SetString(PyExc_TypeError,
                            "output_stream argument should have a .write(b) method.");
            return NULL;
        }
    } else {
        if (callback == Py_None) {
            PyErr_SetString(PyExc_TypeError,
                            "At least one of output_stream argument and "
                            "callback argument should be non-None.");
            return NULL;
        }
    }

    if (pledged_input_size != Py_None) {
        pledged_size_value = PyLong_AsUnsignedLongLong(pledged_input_size);
        if (pledged_size_value == (uint64_t)-1 && PyErr_Occurred()) {
            PyErr_SetString(PyExc_ValueError,
                            "pledged_input_size argument should be 64-bit "
                            "unsigned integer value.");
            return NULL;
        }
    }

    if (read_size <= 0 || write_size <= 0) {
        PyErr_SetString(PyExc_ValueError,
                        "read_size argument and write_size argument should "
                        "be positive numbers.");
        return NULL;
    }

    /* Initialize & set ZstdCompressor */
    self.cctx = ZSTD_createCCtx();
    if (self.cctx == NULL) {
        PyErr_SetString(MS_MEMBER(ZstdError),
                        "Unable to create ZSTD_CCtx instance.");
        goto error;
    }
#ifdef USE_MULTI_PHASE_INIT
    self.module_state = MODULE_STATE;
#endif

    if (level_or_option != Py_None) {
        if (set_c_parameters(&self, level_or_option) < 0) {
            goto error;
        }
    }

    if (zstd_dict != Py_None) {
        if (load_c_dict(&self, zstd_dict) < 0) {
            goto error;
        }
    }

    if (pledged_size_value != ZSTD_CONTENTSIZE_UNKNOWN) {
        zstd_ret = ZSTD_CCtx_setPledgedSrcSize(self.cctx, pledged_size_value);
        if (ZSTD_isError(zstd_ret)) {
            set_zstd_error(MODULE_STATE, ERR_COMPRESS, zstd_ret);
            goto error;
        }
    }

    /* Input buffer, in.size and in.pos will be set later. */
    in.src = PyMem_Malloc(read_size);
    if (in.src == NULL) {
        PyErr_NoMemory();
        goto error;
    }
    in_memoryview = PyMemoryView_FromMemory((char*) in.src, read_size, PyBUF_WRITE);
    if (in_memoryview == NULL) {
        goto error;
    }

    /* Output buffer, out.pos will be set later. */
    out.dst = PyMem_Malloc(write_size);
    if (out.dst == NULL) {
        PyErr_NoMemory();
        goto error;
    }
    out.size = write_size;

    /* Read */
    while (1) {
        Py_ssize_t read_bytes;
        size_t callback_read_pos;
        ZSTD_EndDirective end_directive;

        /* Invoke .readinto() method */
        temp = invoke_method_one_arg(input_stream,
                                     MS_MEMBER(str_readinto),
                                     in_memoryview);
        read_bytes = check_and_get_fp_ret("input_stream.readinto()",
                                          temp, 0, read_size);
        if (read_bytes < 0) {
            goto error;
        }

        /* Don't generate empty frame */
        if (read_bytes == 0 && total_input_size == 0) {
            break;
        }
        total_input_size += (size_t) read_bytes;

        in.size = (size_t) read_bytes;
        in.pos = 0;
        callback_read_pos = 0;
        end_directive = (read_bytes == 0) ? ZSTD_e_end : ZSTD_e_continue;

        /* Compress & write */
        while (1) {
            /* Output position */
            out.pos = 0;

            /* Compress */
            Py_BEGIN_ALLOW_THREADS
            if (self.use_multithread && end_directive == ZSTD_e_continue) {
                do {
                    zstd_ret = ZSTD_compressStream2(self.cctx, &out, &in, ZSTD_e_continue);
                } while (out.pos != out.size && in.pos != in.size && !ZSTD_isError(zstd_ret));
            } else {
                zstd_ret = ZSTD_compressStream2(self.cctx, &out, &in, end_directive);
            }
            Py_END_ALLOW_THREADS

            if (ZSTD_isError(zstd_ret)) {
                set_zstd_error(MODULE_STATE, ERR_COMPRESS, zstd_ret);
                goto error;
            }

            /* Accumulate output bytes */
            total_output_size += out.pos;

            /* Write all output to output_stream */
            if (output_stream != Py_None) {
                if (write_to_fp(MODULE_STATE, "output_stream.write()",
                                output_stream, &out) < 0) {
                    goto error;
                }
            }

            /* Invoke callback */
            if (callback != Py_None) {
                if (invoke_callback(MODULE_STATE, callback, &in, &callback_read_pos,
                                    &out, total_input_size, total_output_size) < 0) {
                    goto error;
                }
            }

            /* Finished */
            if (self.use_multithread && end_directive == ZSTD_e_continue) {
                if (mt_continue_should_break(&in, &out)) {
                    break;
                }
            } else {
                if (zstd_ret == 0) {
                    break;
                }
            }
        } /* Compress & write loop */

        /* Input stream ended */
        if (read_bytes == 0) {
            break;
        }
    } /* Read loop */

    /* Return value */
    ret = Py_BuildValue("KK", total_input_size, total_output_size);
    if (ret == NULL) {
        goto error;
    }

    goto success;

error:
    Py_CLEAR(ret);

success:
    ZSTD_freeCCtx(self.cctx);

    Py_XDECREF(in_memoryview);
    PyMem_Free((void*) in.src);
    PyMem_Free(out.dst);

    return ret;
}

PyDoc_STRVAR(decompress_stream_doc,
"decompress_stream(input_stream, output_stream, *,\n"
"                  zstd_dict=None, option=None,\n"
"                  read_size=131075, write_size=131072,\n"
"                  callback=None)\n"
"----\n"
"Decompresses input_stream and writes the decompressed data to output_stream,\n"
"it doesn't close the streams.\n\n"
"Supports multiple concatenated frames.\n\n"
"Return a tuple, (total_input, total_output), the items are int objects.\n\n"
"Parameters\n"
"input_stream: Input stream that has a .readinto(b) method.\n"
"output_stream: Output stream that has a .write(b) method. If use callback\n"
"    function, this parameter can be None.\n"
"zstd_dict: A ZstdDict object, pre-trained zstd dictionary.\n"
"option: A dict object, contains advanced decompression parameters.\n"
"read_size: Input buffer size, in bytes.\n"
"write_size: Output buffer size, in bytes.\n"
"callback: A callback function that accepts four parameters:\n"
"    (total_input, total_output, read_data, write_data), the first two are\n"
"    int objects, the last two are readonly memoryview objects."
);

static PyObject *
decompress_stream(PyObject *module, PyObject *args, PyObject *kwargs)
{
    static char *kwlist[] = {"input_stream", "output_stream",
                             "zstd_dict", "option",
                             "read_size", "write_size",
                             "callback", NULL};
    PyObject *input_stream;
    PyObject *output_stream;
    PyObject *zstd_dict = Py_None;
    PyObject *option = Py_None;
    Py_ssize_t read_size = ZSTD_DStreamInSize();
    Py_ssize_t write_size = ZSTD_DStreamOutSize();
    PyObject *callback = Py_None;

    /* If fails, modify value in __init__.pyi and doc. */
    assert(read_size == 131075);
    assert(write_size == 131072);

    size_t zstd_ret;
    PyObject *temp;
    ZstdDecompressor self = {0};
    ZSTD_inBuffer in = {.src = NULL};
    ZSTD_outBuffer out = {.dst = NULL};
    PyObject *in_memoryview = NULL;
    uint64_t total_input_size = 0;
    uint64_t total_output_size = 0;
    STATE_FROM_MODULE(module);
    PyObject *ret = NULL;

    if (!PyArg_ParseTupleAndKeywords(args, kwargs,
                                     "OO|$OOnnO:decompress_stream", kwlist,
                                     &input_stream, &output_stream,
                                     &zstd_dict, &option,
                                     &read_size, &write_size,
                                     &callback)) {
        return NULL;
    }

    /* Check arguments */
    if (!PyObject_HasAttr(input_stream, MS_MEMBER(str_readinto))) {
        PyErr_SetString(PyExc_TypeError,
                        "input_stream argument should have a .readinto(b) method.");
        return NULL;
    }

    if (output_stream != Py_None) {
        if (!PyObject_HasAttr(output_stream, MS_MEMBER(str_write))) {
            PyErr_SetString(PyExc_TypeError,
                            "output_stream argument should have a .write(b) method.");
            return NULL;
        }
    } else {
        if (callback == Py_None) {
            PyErr_SetString(PyExc_TypeError,
                            "At least one of output_stream argument and "
                            "callback argument should be non-None.");
            return NULL;
        }
    }

    if (read_size <= 0 || write_size <= 0) {
        PyErr_SetString(PyExc_ValueError,
                        "read_size argument and write_size argument should "
                        "be positive numbers.");
        return NULL;
    }

    /* Initialize & set ZstdDecompressor */
    self.dctx = ZSTD_createDCtx();
    if (self.dctx == NULL) {
        PyErr_SetString(MS_MEMBER(ZstdError),
                        "Unable to create ZSTD_DCtx instance.");
        goto error;
    }
    self.at_frame_edge = 1;
#ifdef USE_MULTI_PHASE_INIT
    self.module_state = MODULE_STATE;
#endif

    if (zstd_dict != Py_None) {
        if (load_d_dict(&self, zstd_dict) < 0) {
            goto error;
        }
    }

    if (option != Py_None) {
        if (set_d_parameters(&self, option) < 0) {
            goto error;
        }
    }

    /* Input buffer, in.size and in.pos will be set later. */
    in.src = PyMem_Malloc(read_size);
    if (in.src == NULL) {
        PyErr_NoMemory();
        goto error;
    }
    in_memoryview = PyMemoryView_FromMemory((char*) in.src, read_size, PyBUF_WRITE);
    if (in_memoryview == NULL) {
        goto error;
    }

    /* Output buffer, out.pos will be set later. */
    out.dst = PyMem_Malloc(write_size);
    if (out.dst == NULL) {
        PyErr_NoMemory();
        goto error;
    }
    out.size = write_size;

    /* Read */
    while (1) {
        Py_ssize_t read_bytes;
        size_t callback_read_pos;

        /* Invoke .readinto() method */
        temp = invoke_method_one_arg(input_stream,
                                     MS_MEMBER(str_readinto),
                                     in_memoryview);
        read_bytes = check_and_get_fp_ret("input_stream.readinto()",
                                          temp, 0, read_size);
        if (read_bytes < 0) {
            goto error;
        }

        total_input_size += (size_t) read_bytes;

        in.size = (size_t) read_bytes;
        in.pos = 0;
        callback_read_pos = 0;

        /* Decompress & write */
        while (1) {
            /* AFE check for setting .at_frame_edge flag, search "AFE check" in
               this file to see details. */
            if (self.at_frame_edge && in.pos == in.size) {
                break;
            }

            /* Output position */
            out.pos = 0;

            /* Decompress */
            Py_BEGIN_ALLOW_THREADS
            zstd_ret = ZSTD_decompressStream(self.dctx, &out, &in);
            Py_END_ALLOW_THREADS

            if (ZSTD_isError(zstd_ret)) {
                set_zstd_error(MODULE_STATE, ERR_DECOMPRESS, zstd_ret);
                goto error;
            }

            /* Set .af_frame_edge flag */
            self.at_frame_edge = (zstd_ret == 0) ? 1 : 0;

            /* Accumulate output bytes */
            total_output_size += out.pos;

            /* Write all output to output_stream */
            if (output_stream != Py_None) {
                if (write_to_fp(MODULE_STATE, "output_stream.write()",
                                output_stream, &out) < 0) {
                    goto error;
                }
            }

            /* Invoke callback */
            if (callback != Py_None) {
                if (invoke_callback(MODULE_STATE, callback, &in, &callback_read_pos,
                                    &out, total_input_size, total_output_size) < 0) {
                    goto error;
                }
            }

            /* Finished. When a frame is fully decoded, but not fully flushed,
               the last byte is kept as hostage, it will be released when all
               output is flushed. */
            if (in.pos == in.size) {
                /* If input stream ends in an incomplete frame, output as much
                   as possible. */
                if (read_bytes == 0 &&
                    self.at_frame_edge == 0 &&
                    out.pos == out.size)
                {
                    continue;
                }

                break;
            }
        } /* Decompress & write loop */

        /* Input stream ended */
        if (read_bytes == 0) {
            /* Check data integrity. at_frame_edge flag is 1 when both the
               input and output streams are at a frame edge. */
            if (self.at_frame_edge == 0) {
                PyErr_Format(MS_MEMBER(ZstdError),
                             "Decompression failed: zstd data ends in an "
                             "incomplete frame, maybe the input data was "
                             "truncated. Total input %llu bytes, total output "
                             "%llu bytes.",
                             total_input_size, total_output_size);
                goto error;
            }
            break;
        }
    } /* Read loop */

    /* Return value */
    ret = Py_BuildValue("KK", total_input_size, total_output_size);
    if (ret == NULL) {
        goto error;
    }

    goto success;

error:
    Py_CLEAR(ret);

success:
    ZSTD_freeDCtx(self.dctx);

    Py_XDECREF(in_memoryview);
    PyMem_Free((void*) in.src);
    PyMem_Free(out.dst);

    return ret;
}
