#include "pyzstd.h"

/* This file has two classes:
   1, ZstdFileReader is expected to be used with io.BufferedReader.
   2, ZstdFileWriter is expected to be used with ZstdFile/SeekableZstdFile.
*/

typedef struct {
    PyObject_HEAD

    /* Decompression context */
    ZSTD_DCtx *dctx;
    /* ZstdDict object in use */
    PyObject *dict;

    /* Read chunk size, an int object. */
    PyObject *read_size;

    /* File states. On Linux/macOS/Windows, Py_off_t is signed, so
       ZstdFile/SeekableZstdFile use int64_t as file position/size. */
    PyObject *fp;   /* File object */
    int eof;        /* At EOF, 0 or 1. */
    int64_t pos;    /* Decompressed position, >= 0. */
    int64_t size;   /* File size, -1 means unknown. */

    /* Decompression states, 0 or 1. */
    int needs_input;
    int at_frame_edge;

    /* Input state, in.size/in.pos need to be initialized with 0. */
    PyObject *in_dat;
    ZSTD_inBuffer in;

    /* Lazy create forward output buffer */
    char *tmp_output;

#ifdef USE_MULTI_PHASE_INIT
    _zstd_state *module_state;
#endif
} ZstdFileReader;

typedef struct {
    PyObject_HEAD

    /* Compression context */
    ZSTD_CCtx *cctx;
    /* ZstdDict object in use */
    PyObject *dict;

    PyObject *fp;      /* File object */
    int fp_has_flush;  /* fp has .flush() method, 0 or 1. */

    /* Last mode, initialized to ZSTD_e_end. */
    int last_mode;

    /* Use multi-threaded compression, 0 or 1. */
    int use_multithread;

    /* Compression level */
    int compression_level;

    /* Write buffer */
    char *write_buffer;
    size_t write_buffer_size;

#ifdef USE_MULTI_PHASE_INIT
    _zstd_state *module_state;
#endif
} ZstdFileWriter;

/* Generate 4 functions using macro:
    1, file_set_c_parameters(ZstdFileWriter *self, PyObject *level_or_option)
    2, file_load_c_dict(ZstdFileWriter *self, PyObject *dict)
    3, file_set_d_parameters(ZstdFileReader *self, PyObject *option)
    4, file_load_d_dict(ZstdFileReader *self, PyObject *dict) */
#undef  PYZSTD_C_CLASS
#define PYZSTD_C_CLASS       ZstdFileWriter
#undef  PYZSTD_D_CLASS
#define PYZSTD_D_CLASS       ZstdFileReader
#undef  PYZSTD_FUN_PREFIX
#define PYZSTD_FUN_PREFIX(F) file_##F
#include "macro_functions.h"

/* -----------------------
     ZstdFileReader code
   ----------------------- */
static int
ZstdFileReader_init(ZstdFileReader *self, PyObject *args, PyObject *kwargs)
{
    static char *kwlist[] = {"fp", "zstd_dict", "option",
                             "read_size", NULL};
    PyObject *fp;
    PyObject *zstd_dict;
    PyObject *option;
    PyObject *read_size;

    assert(ZSTD_DStreamInSize() == 131075);

    if (!PyArg_ParseTupleAndKeywords(args, kwargs,
                                     "OOOO:ZstdFileReader.__init__", kwlist,
                                     &fp, &zstd_dict, &option, &read_size)) {
        return -1;
    }

    /* Keep this first. Set module state to self. */
    SET_STATE_TO_OBJ(Py_TYPE(self), self);

    assert(self->dctx == NULL);
    assert(self->dict == NULL);
    assert(self->read_size == NULL);
    assert(self->fp == NULL);
    assert(self->eof == 0);
    assert(self->pos == 0);
    assert(self->size == 0);
    assert(self->needs_input == 0);
    assert(self->at_frame_edge == 0);
    assert(self->in_dat == NULL);
    assert(self->in.size == 0);
    assert(self->in.pos == 0);
    assert(self->tmp_output == NULL);

    /* Read chunk size */
    {
        Py_ssize_t v = PyLong_AsSsize_t(read_size);
        if (v <= 0) {
            if (v == -1 && PyErr_Occurred()) {
                PyErr_SetString(PyExc_TypeError,
                                "read_size argument should be integer");
                goto error;
            }
            PyErr_SetString(PyExc_ValueError,
                            "read_size argument should > 0");
            goto error;
        }
    }
    Py_INCREF(read_size);
    self->read_size = read_size;

    /* File states */
    Py_INCREF(fp);
    self->fp = fp;
    self->size = -1;

    /* Decompression states */
    self->needs_input = 1;
    self->at_frame_edge = 1;

    /* Decompression context */
    self->dctx = ZSTD_createDCtx();
    if (self->dctx == NULL) {
        STATE_FROM_OBJ(self);
        PyErr_SetString(MS_MEMBER(ZstdError),
                        "Unable to create ZSTD_DCtx instance.");
        goto error;
    }

    /* Load dictionary to decompression context */
    if (zstd_dict != Py_None) {
        if (file_load_d_dict(self, zstd_dict) < 0) {
            goto error;
        }

        /* Py_INCREF the dict */
        Py_INCREF(zstd_dict);
        self->dict = zstd_dict;
    }

    /* Set option to decompression context */
    if (option != Py_None) {
        if (file_set_d_parameters(self, option) < 0) {
            goto error;
        }
    }
    return 0;
error:
    return -1;
}

static void
ZstdFileReader_dealloc(ZstdFileReader *self)
{
    /* Free decompression context */
    ZSTD_freeDCtx(self->dctx);
    /* Py_XDECREF the dict after free decompression context */
    Py_XDECREF(self->dict);

    Py_XDECREF(self->read_size);
    Py_XDECREF(self->fp);
    Py_XDECREF(self->in_dat);
    PyMem_Free(self->tmp_output);

    PyTypeObject *tp = Py_TYPE(self);
    tp->tp_free((PyObject*)self);
    Py_DECREF(tp);
}

/* If fill_full is true, fill the output buffer.
   If fill_full is false, only output once, then exit.
   On success, return 0.
   On failure, return -1. */
FORCE_INLINE int
decompress_into(ZstdFileReader *self,
                ZSTD_outBuffer *out, const int fill_full)
{
    Py_buffer buf;
    const size_t orig_pos = out->pos;
    size_t zstd_ret;

    /* Return */
    if (self->eof || out->size == out->pos) {
        return 0;
    }

    while (1) {
        if (self->in.size == self->in.pos && self->needs_input) {
            void *read_buf;
            Py_ssize_t read_len;

            /* Read */
            Py_XDECREF(self->in_dat);
            {
                STATE_FROM_OBJ(self);
                self->in_dat = invoke_method_one_arg(
                                    self->fp,
                                    MS_MEMBER(str_read),
                                    self->read_size);
                if (self->in_dat == NULL) {
                    return -1;
                }
            }

            /* Get address and length */
            if (PyObject_GetBuffer(self->in_dat, &buf, PyBUF_SIMPLE) < 0) {
                return -1;
            }
            read_buf = buf.buf;
            read_len = buf.len;
            PyBuffer_Release(&buf);

            /* EOF */
            if (read_len == 0) {
                if (self->at_frame_edge) {
                    self->eof = 1;
                    self->pos += out->pos - orig_pos;
                    self->size = self->pos;
                    return 0;
                } else {
                    PyErr_SetString(PyExc_EOFError,
                                    "Compressed file ended before the "
                                    "end-of-stream marker was reached");
                    return -1;
                }
            }
            self->in.src = read_buf;
            self->in.size = read_len;
            self->in.pos = 0;
        }

        /* Decompress */
        Py_BEGIN_ALLOW_THREADS
        zstd_ret = ZSTD_decompressStream(self->dctx, out, &self->in);
        Py_END_ALLOW_THREADS

        if (ZSTD_isError(zstd_ret)) {
            STATE_FROM_OBJ(self);
            set_zstd_error(MODULE_STATE, ERR_DECOMPRESS, zstd_ret);
            return -1;
        }

        /* Set flags */
        if (zstd_ret == 0) {
            self->needs_input = 1;
            self->at_frame_edge = 1;
        } else {
            self->needs_input = (out->size != out->pos);
            self->at_frame_edge = 0;
        }

        if (fill_full) {
            if (out->size != out->pos) {
                continue;
            } else {
                self->pos += out->pos - orig_pos;
                return 0;
            }
        } else {
            if (out->pos != orig_pos) {
                self->pos += out->pos - orig_pos;
                return 0;
            }
        }
    }
}

static PyObject *
ZstdFileReader_readinto(ZstdFileReader *self, PyObject *arg)
{
    ZSTD_outBuffer out;
    Py_buffer buf;

    if (PyObject_GetBuffer(arg, &buf, PyBUF_WRITABLE) < 0) {
        return NULL;
    }
    out.dst = buf.buf;
    out.size = buf.len;
    out.pos = 0;
    PyBuffer_Release(&buf);

    if (decompress_into(self, &out, 0) < 0) {
        return NULL;
    }
    return PyLong_FromSize_t(out.pos);
}

static PyObject *
ZstdFileReader_readall(ZstdFileReader *self)
{
    BlocksOutputBuffer buffer = {.list = NULL};
    ZSTD_outBuffer out;
    PyObject *ret;

    if (self->size >= 0) {
        /* Known file size */
        const int64_t length = self->size - self->pos;
        if (length > (int64_t)PY_SSIZE_T_MAX) {
            PyErr_SetString(PyExc_MemoryError, unable_allocate_msg);
            goto error;
        }
        if (OutputBuffer_InitWithSize(&buffer, &out, -1,
                                      (Py_ssize_t)length) < 0) {
            goto error;
        }
    } else {
        /* Unknown file size */
        if (OutputBuffer_InitAndGrow(&buffer, &out, -1) < 0) {
            goto error;
        }
    }

    while (1) {
        if (decompress_into(self, &out, 1) < 0) {
            goto error;
        }

        if (self->eof) {
            /* Finished */
            break;
        }
        if (out.size == out.pos) {
            /* Grow output buffer */
            if (OutputBuffer_Grow(&buffer, &out) < 0) {
                goto error;
            }
        }
    }
    ret = OutputBuffer_Finish(&buffer, &out);
    if (ret != NULL) {
        return ret;
    }

error:
    OutputBuffer_OnError(&buffer);
    return NULL;
}

/* If obj is None, forward to EOF.
   If obj <= 0, do nothing. */
static PyObject *
ZstdFileReader_forward(ZstdFileReader *self, PyObject *arg)
{
    ZSTD_outBuffer out;
    const size_t DStreamOutSize = ZSTD_DStreamOutSize();

    /* Lazy create forward output buffer */
    if (self->tmp_output == NULL) {
        self->tmp_output = PyMem_Malloc(DStreamOutSize);
        if (self->tmp_output == NULL) {
            PyErr_NoMemory();
            return NULL;
        }
    }
    out.dst = self->tmp_output;

    if (arg == Py_None) {
        /* Forward to EOF */
        out.size = DStreamOutSize;
        while (1) {
            out.pos = 0;
            if (decompress_into(self, &out, 1) < 0) {
                return NULL;
            }
            if (self->eof) {
                Py_RETURN_NONE;
            }
        }
    } else {
        /* Offset argument */
        int64_t offset = PyLong_AsLongLong(arg);
        if (offset == -1 && PyErr_Occurred()) {
            PyErr_SetString(PyExc_TypeError,
                            "offset argument should be int64_t integer");
            return NULL;
        }

        /* Forward to offset */
        while (offset > 0) {
            out.size = (size_t) Py_MIN((int64_t)DStreamOutSize, offset);
            out.pos = 0;
            if (decompress_into(self, &out, 1) < 0) {
                return NULL;
            }
            if (self->eof) {
                Py_RETURN_NONE;
            }
            offset -= out.pos;
        }
        Py_RETURN_NONE;
    }
}

static PyObject *
ZstdFileReader_reset_session(ZstdFileReader *self)
{
    /* Reset decompression states */
    self->needs_input = 1;
    self->at_frame_edge = 1;
    self->in.size = 0;
    self->in.pos = 0;

    /* Resetting session never fail */
    ZSTD_DCtx_reset(self->dctx, ZSTD_reset_session_only);

    Py_RETURN_NONE;
}

static PyMethodDef ZstdFileReader_methods[] = {
    {"readinto", (PyCFunction)ZstdFileReader_readinto, METH_O},
    {"readall",  (PyCFunction)ZstdFileReader_readall,  METH_NOARGS},
    {"forward",  (PyCFunction)ZstdFileReader_forward,  METH_O},
    {"reset_session", (PyCFunction)ZstdFileReader_reset_session, METH_NOARGS},
    {NULL, NULL, 0}
};

static PyMemberDef ZstdFileReader_members[] = {
    {"eof",  T_INT,      offsetof(ZstdFileReader, eof),  0},
    {"pos",  T_LONGLONG, offsetof(ZstdFileReader, pos),  0},
    {"size", T_LONGLONG, offsetof(ZstdFileReader, size), 0},
    {NULL}
};

static PyType_Slot ZstdFileReader_slots[] = {
    {Py_tp_init,    ZstdFileReader_init},
    {Py_tp_dealloc, ZstdFileReader_dealloc},
    {Py_tp_methods, ZstdFileReader_methods},
    {Py_tp_members, ZstdFileReader_members},
    {0, 0}
};

static PyType_Spec ZstdFileReader_type_spec = {
    .name = "pyzstd.zstdfile.ZstdFileReader",
    .basicsize = sizeof(ZstdFileReader),
    .flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE,
    .slots = ZstdFileReader_slots,
};

/* -----------------------
     ZstdFileWriter code
   ----------------------- */
static int
ZstdFileWriter_init(ZstdFileWriter *self, PyObject *args, PyObject *kwargs)
{
    static char *kwlist[] = {"fp", "level_or_option", "zstd_dict",
                             "write_size", NULL};
    PyObject *fp;
    PyObject *level_or_option;
    PyObject *zstd_dict;
    Py_ssize_t write_size;

    assert(ZSTD_CStreamOutSize() == 131591);

    if (!PyArg_ParseTupleAndKeywords(args, kwargs,
                                     "OOOn:ZstdFileWriter.__init__", kwlist,
                                     &fp, &level_or_option,
                                     &zstd_dict, &write_size)) {
        return -1;
    }

    /* Keep this first. Set module state to self. */
    SET_STATE_TO_OBJ(Py_TYPE(self), self);
    STATE_FROM_OBJ(self);

    assert(self->cctx == NULL);
    assert(self->dict == NULL);
    assert(self->fp == NULL);
    assert(self->fp_has_flush == 0);
    assert(self->last_mode == 0);
    assert(self->use_multithread == 0);
    assert(self->compression_level == 0);
    assert(self->write_buffer == NULL);
    assert(self->write_buffer_size == 0);

    /* File object */
    Py_INCREF(fp);
    self->fp = fp;
    self->fp_has_flush = PyObject_HasAttr(fp, MS_MEMBER(str_flush));

    /* Last mode */
    self->last_mode = ZSTD_e_end;

    /* Write buffer */
    if (write_size <= 0) {
        PyErr_SetString(PyExc_ValueError,
                        "write_size argument should > 0");
        goto error;
    }
    self->write_buffer_size = (size_t)write_size;

    self->write_buffer = PyMem_Malloc(write_size);
    if (self->write_buffer == NULL) {
        PyErr_NoMemory();
        goto error;
    }

    /* Compression context */
    self->cctx = ZSTD_createCCtx();
    if (self->cctx == NULL) {
        PyErr_SetString(MS_MEMBER(ZstdError),
                        "Unable to create ZSTD_CCtx instance.");
        goto error;
    }

    /* Set compressLevel/option to compression context */
    if (level_or_option != Py_None) {
        if (file_set_c_parameters(self, level_or_option) < 0) {
            goto error;
        }
    }

    /* Load dictionary to compression context */
    if (zstd_dict != Py_None) {
        if (file_load_c_dict(self, zstd_dict) < 0) {
            goto error;
        }

        /* Py_INCREF the dict */
        Py_INCREF(zstd_dict);
        self->dict = zstd_dict;
    }
    return 0;
error:
    return -1;
}

static void
ZstdFileWriter_dealloc(ZstdFileWriter *self)
{
    /* Free compression context */
    ZSTD_freeCCtx(self->cctx);
    /* Py_XDECREF the dict after free the compression context */
    Py_XDECREF(self->dict);

    Py_XDECREF(self->fp);
    PyMem_Free(self->write_buffer);

    PyTypeObject *tp = Py_TYPE(self);
    tp->tp_free((PyObject*)self);
    Py_DECREF(tp);
}

static PyObject *
ZstdFileWriter_write(ZstdFileWriter *self, PyObject *arg)
{
    ZSTD_inBuffer in;
    ZSTD_outBuffer out;
    uint64_t output_size = 0;
    Py_buffer buf;
    size_t zstd_ret;
    PyObject *ret;
    STATE_FROM_OBJ(self);

    /* Input buffer */
    if (PyObject_GetBuffer(arg, &buf, PyBUF_SIMPLE) < 0) {
        goto error;
    }
    in.src = buf.buf;
    in.size = buf.len;
    in.pos = 0;
    PyBuffer_Release(&buf);

    /* Output buffer, out.pos will be set later. */
    out.dst = self->write_buffer;
    out.size = self->write_buffer_size;

    /* State */
    self->last_mode = ZSTD_e_continue;

    /* Compress & write */
    while (1) {
        /* Output position */
        out.pos = 0;

        /* Compress */
        Py_BEGIN_ALLOW_THREADS
        if (!self->use_multithread) {
            zstd_ret = ZSTD_compressStream2(self->cctx, &out, &in, ZSTD_e_continue);
        } else {
            do {
                zstd_ret = ZSTD_compressStream2(self->cctx, &out, &in, ZSTD_e_continue);
            } while (out.pos != out.size && in.pos != in.size && !ZSTD_isError(zstd_ret));
        }
        Py_END_ALLOW_THREADS

        if (ZSTD_isError(zstd_ret)) {
            set_zstd_error(MODULE_STATE, ERR_COMPRESS, zstd_ret);
            goto error;
        }

        /* Accumulate output bytes */
        output_size += out.pos;

        /* Write output to fp */
        if (write_to_fp(MODULE_STATE, "self._fp.write()",
                        self->fp, &out) < 0) {
            goto error;
        }

        /* Finished */
        if (!self->use_multithread) {
            /* Single-thread compression + .CONTINUE mode */
            if (zstd_ret == 0) {
                break;
            }
        } else {
            /* Multi-thread compression + .CONTINUE mode */
            if (mt_continue_should_break(&in, &out)) {
                break;
            }
        }
    }

    ret = Py_BuildValue("KK", (uint64_t)in.size, output_size);
    if (ret != NULL) {
        return ret;
    }
error:
    return NULL;
}

static PyObject *
ZstdFileWriter_flush(ZstdFileWriter *self, PyObject *arg)
{
    int mode;
    ZSTD_inBuffer in;
    ZSTD_outBuffer out;
    uint64_t output_size = 0;
    size_t zstd_ret;
    PyObject *ret;
    STATE_FROM_OBJ(self);

    /* Mode argument */
    mode = _PyLong_AsInt(arg);

    assert(ZSTD_e_flush == 1 && ZSTD_e_end == 2);
    if (mode != ZSTD_e_flush && mode != ZSTD_e_end) {
        /* Wrong type */
        if (mode == -1 && PyErr_Occurred()) {
            PyErr_SetString(PyExc_TypeError, "mode should be int type");
            goto error;
        }
        /* Wrong value */
        PyErr_SetString(PyExc_ValueError,
                        "mode argument wrong value, it should be "
                        "ZstdFile.FLUSH_BLOCK or ZstdFile.FLUSH_FRAME.");
        goto error;
    }

    /* Don't generate empty content frame */
    if (mode == self->last_mode) {
        goto finish;
    }

    /* Input buffer */
    in.src = &in;
    in.size = 0;
    in.pos = 0;

    /* Output buffer, out.pos will be set later. */
    out.dst = self->write_buffer;
    out.size = self->write_buffer_size;

    /* State */
    self->last_mode = mode;

    /* Compress & write */
    while (1) {
        /* Output position */
        out.pos = 0;

        /* Compress */
        Py_BEGIN_ALLOW_THREADS
        zstd_ret = ZSTD_compressStream2(self->cctx, &out, &in, mode);
        Py_END_ALLOW_THREADS

        if (ZSTD_isError(zstd_ret)) {
            set_zstd_error(MODULE_STATE, ERR_COMPRESS, zstd_ret);
            goto error;
        }

        /* Accumulate output bytes */
        output_size += out.pos;

        /* Write output to fp */
        if (write_to_fp(MODULE_STATE, "self._fp.write()",
                        self->fp, &out) < 0) {
            goto error;
        }

        /* Finished */
        if (zstd_ret == 0) {
            break;
        }
    }

    /* Flush */
    if (self->fp_has_flush) {
        ret = invoke_method_no_arg(self->fp, MS_MEMBER(str_flush));
        if (ret == NULL) {
            goto error;
        }
        Py_DECREF(ret);
    }

finish:
    ret = Py_BuildValue("IK", (uint32_t)0, output_size);
    if (ret != NULL) {
        return ret;
    }
error:
    return NULL;
}

static PyMethodDef ZstdFileWriter_methods[] = {
    {"write", (PyCFunction)ZstdFileWriter_write, METH_O},
    {"flush", (PyCFunction)ZstdFileWriter_flush, METH_O},
    {NULL, NULL, 0}
};

static PyType_Slot ZstdFileWriter_slots[] = {
    {Py_tp_init,    ZstdFileWriter_init},
    {Py_tp_dealloc, ZstdFileWriter_dealloc},
    {Py_tp_methods, ZstdFileWriter_methods},
    {0, 0}
};

static PyType_Spec ZstdFileWriter_type_spec = {
    .name = "pyzstd.zstdfile.ZstdFileWriter",
    .basicsize = sizeof(ZstdFileWriter),
    .flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE,
    .slots = ZstdFileWriter_slots,
};
