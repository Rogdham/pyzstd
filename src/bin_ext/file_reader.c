#include "pyzstd.h"

/* -----------------------
     ZstdFileReader code
   ----------------------- */
typedef struct {
    PyObject_HEAD

    ZSTD_DCtx *dctx;
    PyObject *dict;

    /* File states. On Windows and Linux, Py_off_t is signed, so
       ZstdFile/SeekableZstdFile use int64_t as file position/size. */
    PyObject *fp;
    int eof;
    int64_t pos;    /* Decompressed position */
    int64_t size;   /* File size, -1 means unknown. */

    /* Decompression states */
    int needs_input;
    int at_frame_edge;

    /* Lazy create forward output buffer */
    PyObject *tmp_output;
    /* Input state, need to be initialized with 0. */
    PyObject *in_dat;
    ZSTD_inBuffer in;

#ifdef USE_MULTI_PHASE_INIT
    _zstd_state *module_state;
#endif
} ZstdFileReader;

/* Generate two functions using macro:
    1, filereader_set_d_parameters(ZstdFileReader *self, PyObject *option)
    2, filereader_load_d_dict(ZstdFileReader *self, PyObject *dict) */
#undef  PYZSTD_DECOMPRESSOR_CLASS
#define PYZSTD_DECOMPRESSOR_CLASS     ZstdFileReader
#undef  PYZSTD_DECOMPRESSOR_PREFIX
#define PYZSTD_DECOMPRESSOR_PREFIX(F) filereader_##F
#include "macro_functions.h"

static int
ZstdFileReader_init(ZstdFileReader *self, PyObject *args, PyObject *kwargs)
{
    static char *kwlist[] = {"fp", "zstd_dict", "option", NULL};
    PyObject *fp;
    PyObject *zstd_dict;
    PyObject *option;

    if (!PyArg_ParseTupleAndKeywords(args, kwargs,
                                     "OOO:ZstdFileReader.__init__", kwlist,
                                     &fp, &zstd_dict, &option)) {
        return -1;
    }

    /* Keep this first. Set module state to self. */
    SET_STATE_TO_OBJ(Py_TYPE(self), self);

    /* File states */
    assert(self->eof == 0);
    assert(self->pos == 0);

    Py_INCREF(fp);
    self->fp = fp;
    self->size = -1;

    /* Decompression states */
    self->needs_input = 1;
    self->at_frame_edge = 1;

    assert(self->tmp_output == NULL);
    assert(self->in_dat == NULL);
    assert(self->in.size == 0);
    assert(self->in.pos == 0);

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
        if (filereader_load_d_dict(self, zstd_dict) < 0) {
            goto error;
        }

        /* Py_INCREF the dict */
        Py_INCREF(zstd_dict);
        self->dict = zstd_dict;
    }

    /* Set option to decompression context */
    if (option != Py_None) {
        if (filereader_set_d_parameters(self, option) < 0) {
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

    Py_XDECREF(self->fp);
    Py_XDECREF(self->tmp_output);
    Py_XDECREF(self->in_dat);

    PyTypeObject *tp = Py_TYPE(self);
    tp->tp_free((PyObject*)self);
    Py_DECREF(tp);
}

FORCE_INLINE int
decompress_into(ZstdFileReader *self,
                ZSTD_outBuffer *out, const int fill_full)
{
    Py_buffer buf;
    size_t zstd_ret;

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
                                MS_MEMBER(int_ZSTD_DStreamInSize));
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
                    self->pos += out->pos;
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
        zstd_ret = ZSTD_decompressStream(self->dctx, out, &self->in);
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

        if (fill_full && out->size != out->pos) {
            continue;
        }
        if (out->pos != 0) {
            self->pos += out->pos;
            return 0;
        }
    }
}

static PyObject *
ZstdFileReader_readinto(ZstdFileReader *self, PyObject *arg)
{
    ZSTD_outBuffer out;
    Py_buffer buf;

    if (PyObject_GetBuffer(arg, &buf, PyBUF_SIMPLE) < 0) {
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

    if (OutputBuffer_InitAndGrow(&buffer, &out, -1) < 0) {
        goto error;
    }

    while (1) {
        if (decompress_into(self, &out, 1) < 0) {
            goto error;
        }

        if (out.size == out.pos) {
            /* Grow output buffer */
            if (OutputBuffer_Grow(&buffer, &out) < 0) {
                goto error;
            }
        } else {
            /* Finished */
            break;
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
        self->tmp_output = PyByteArray_FromStringAndSize(
                                NULL, DStreamOutSize);
        if (self->tmp_output == NULL) {
            return NULL;
        }
    }
    out.dst = PyByteArray_AS_STRING(self->tmp_output);

    if (arg == Py_None) {
        /* Forward to EOF */
        out.size = DStreamOutSize;
        while (1) {
            out.pos = 0;
            if (decompress_into(self, &out, 1) < 0) {
                return NULL;
            }
            if (out.pos == 0) {
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
            if (out.pos == 0) {
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
    {NULL, NULL, 0, NULL}
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
