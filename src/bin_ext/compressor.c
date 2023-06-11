#include "pyzstd.h"

/* -----------------------
     ZstdCompressor code
   ----------------------- */
static PyObject *
ZstdCompressor_new(PyTypeObject *type, PyObject *args, PyObject *kwds)
{
    ZstdCompressor *self;
    self = (ZstdCompressor*)type->tp_alloc(type, 0);
    if (self == NULL) {
        goto error;
    }

    assert(self->dict == NULL);
    assert(self->use_multithread == 0);
    assert(self->compression_level == 0);
    assert(self->inited == 0);

    /* Keep this first. Set module state to self. */
    SET_STATE_TO_OBJ(type, self);

    /* Compression context */
    self->cctx = ZSTD_createCCtx();
    if (self->cctx == NULL) {
        STATE_FROM_OBJ(self);
        PyErr_SetString(MS_MEMBER(ZstdError),
                        "Unable to create ZSTD_CCtx instance.");
        goto error;
    }

    /* Last mode */
    self->last_mode = ZSTD_e_end;

    /* Thread lock */
    self->lock = PyThread_allocate_lock();
    if (self->lock == NULL) {
        PyErr_NoMemory();
        goto error;
    }
    return (PyObject*)self;

error:
    Py_XDECREF(self);
    return NULL;
}

static void
ZstdCompressor_dealloc(ZstdCompressor *self)
{
    /* Free compression context */
    ZSTD_freeCCtx(self->cctx);

    /* Py_XDECREF the dict after free the compression context */
    Py_XDECREF(self->dict);

    /* Thread lock */
    if (self->lock) {
        PyThread_free_lock(self->lock);
    }

    PyTypeObject *tp = Py_TYPE(self);
    tp->tp_free((PyObject*)self);
    Py_DECREF(tp);
}

PyDoc_STRVAR(ZstdCompressor_doc,
"A streaming compressor. Thread-safe at method level.\n\n"
"ZstdCompressor.__init__(self, level_or_option=None, zstd_dict=None)\n"
"----\n"
"Initialize a ZstdCompressor object.\n\n"
"Parameters\n"
"level_or_option: When it's an int object, it represents the compression level.\n"
"                 When it's a dict object, it contains advanced compression\n"
"                 parameters.\n"
"zstd_dict:       A ZstdDict object, pre-trained zstd dictionary.");

static int
ZstdCompressor_init(ZstdCompressor *self, PyObject *args, PyObject *kwargs)
{
    static char *kwlist[] = {"level_or_option", "zstd_dict", NULL};
    PyObject *level_or_option = Py_None;
    PyObject *zstd_dict = Py_None;

    if (!PyArg_ParseTupleAndKeywords(args, kwargs,
                                     "|OO:ZstdCompressor.__init__", kwlist,
                                     &level_or_option, &zstd_dict)) {
        return -1;
    }

    /* Only called once */
    if (self->inited) {
        PyErr_SetString(PyExc_RuntimeError, init_twice_msg);
        return -1;
    }
    self->inited = 1;

    /* Set compressLevel/option to compression context */
    if (level_or_option != Py_None) {
        if (set_c_parameters(self, level_or_option) < 0) {
            return -1;
        }
    }

    /* Load dictionary to compression context */
    if (zstd_dict != Py_None) {
        if (load_c_dict(self, zstd_dict) < 0) {
            return -1;
        }

        /* Py_INCREF the dict */
        Py_INCREF(zstd_dict);
        self->dict = zstd_dict;
    }

    return 0;
}

FORCE_INLINE PyObject *
compress_impl(ZstdCompressor *self, Py_buffer *data,
              const ZSTD_EndDirective end_directive, const int rich_mem)
{
    ZSTD_inBuffer in;
    ZSTD_outBuffer out;
    BlocksOutputBuffer buffer = {.list = NULL};
    size_t zstd_ret;
    PyObject *ret;

    /* Prepare input & output buffers */
    if (data != NULL) {
        in.src = data->buf;
        in.size = data->len;
        in.pos = 0;
    } else {
        in.src = &in;
        in.size = 0;
        in.pos = 0;
    }

    if (rich_mem) {
        /* Calculate output buffer's size */
        size_t output_buffer_size = ZSTD_compressBound(in.size);
        if (output_buffer_size > (size_t) PY_SSIZE_T_MAX) {
            PyErr_NoMemory();
            goto error;
        }

        if (OutputBuffer_InitWithSize(&buffer, &out, -1,
                                      (Py_ssize_t) output_buffer_size) < 0) {
            goto error;
        }
    } else {
        if (OutputBuffer_InitAndGrow(&buffer, &out, -1) < 0) {
            goto error;
        }
    }

    /* zstd stream compress */
    while (1) {
        Py_BEGIN_ALLOW_THREADS
        zstd_ret = ZSTD_compressStream2(self->cctx, &out, &in, end_directive);
        Py_END_ALLOW_THREADS

        /* Check error */
        if (ZSTD_isError(zstd_ret)) {
            STATE_FROM_OBJ(self);
            set_zstd_error(MODULE_STATE, ERR_COMPRESS, zstd_ret);
            goto error;
        }

        /* Finished */
        if (zstd_ret == 0) {
            break;
        }

        /* Output buffer should be exhausted, grow the buffer. */
        assert(out.pos == out.size);
        if (out.pos == out.size) {
            if (OutputBuffer_Grow(&buffer, &out) < 0) {
                goto error;
            }
        }
    }

    /* Return a bytes object */
    ret = OutputBuffer_Finish(&buffer, &out);
    if (ret != NULL) {
        return ret;
    }

error:
    OutputBuffer_OnError(&buffer);
    return NULL;
}

static PyObject *
compress_mt_continue_impl(ZstdCompressor *self, Py_buffer *data)
{
    ZSTD_inBuffer in;
    ZSTD_outBuffer out;
    BlocksOutputBuffer buffer = {.list = NULL};
    size_t zstd_ret;
    PyObject *ret;

    /* Prepare input & output buffers */
    in.src = data->buf;
    in.size = data->len;
    in.pos = 0;

    if (OutputBuffer_InitAndGrow(&buffer, &out, -1) < 0) {
        goto error;
    }

    /* zstd stream compress */
    while (1) {
        Py_BEGIN_ALLOW_THREADS
        do {
            zstd_ret = ZSTD_compressStream2(self->cctx, &out, &in, ZSTD_e_continue);
        } while (out.pos != out.size && in.pos != in.size && !ZSTD_isError(zstd_ret));
        Py_END_ALLOW_THREADS

        /* Check error */
        if (ZSTD_isError(zstd_ret)) {
            STATE_FROM_OBJ(self);
            set_zstd_error(MODULE_STATE, ERR_COMPRESS, zstd_ret);
            goto error;
        }

        /* Like compress_impl(), output as much as possible. */
        if (out.pos == out.size) {
            if (OutputBuffer_Grow(&buffer, &out) < 0) {
                goto error;
            }
        } else if (in.pos == in.size) {
            /* Finished */
            assert(mt_continue_should_break(&in, &out));
            break;
        }
    }

    /* Return a bytes object */
    ret = OutputBuffer_Finish(&buffer, &out);
    if (ret != NULL) {
        return ret;
    }

error:
    OutputBuffer_OnError(&buffer);
    return NULL;
}

PyDoc_STRVAR(ZstdCompressor_compress_doc,
"compress(data, mode=ZstdCompressor.CONTINUE)\n"
"----\n"
"Provide data to the compressor object.\n"
"Return a chunk of compressed data if possible, or b'' otherwise.\n\n"
"Parameters\n"
"data: A bytes-like object, data to be compressed.\n"
"mode: Can be these 3 values .CONTINUE, .FLUSH_BLOCK, .FLUSH_FRAME.");

static PyObject *
ZstdCompressor_compress(ZstdCompressor *self, PyObject *args, PyObject *kwargs)
{
    static char *kwlist[] = {"data", "mode", NULL};
    Py_buffer data;
    int mode = ZSTD_e_continue;

    PyObject *ret;

    if (!PyArg_ParseTupleAndKeywords(args, kwargs,
                                     "y*|i:ZstdCompressor.compress", kwlist,
                                     &data, &mode)) {
        return NULL;
    }

    /* Check mode value */
    if (mode != ZSTD_e_continue &&
        mode != ZSTD_e_flush &&
        mode != ZSTD_e_end)
    {
        PyErr_SetString(PyExc_ValueError,
                        "mode argument wrong value, it should be one of "
                        "ZstdCompressor.CONTINUE, ZstdCompressor.FLUSH_BLOCK, "
                        "ZstdCompressor.FLUSH_FRAME.");
        PyBuffer_Release(&data);
        return NULL;
    }

    /* Thread-safe code */
    ACQUIRE_LOCK(self);

    /* Compress */
    if (self->use_multithread && mode == ZSTD_e_continue) {
        ret = compress_mt_continue_impl(self, &data);
    } else {
        ret = compress_impl(self, &data, mode, 0);
    }

    if (ret) {
        self->last_mode = mode;
    } else {
        self->last_mode = ZSTD_e_end;

        /* Resetting cctx's session never fail */
        ZSTD_CCtx_reset(self->cctx, ZSTD_reset_session_only);
    }
    RELEASE_LOCK(self);

    PyBuffer_Release(&data);
    return ret;
}

PyDoc_STRVAR(ZstdCompressor_flush_doc,
"flush(mode=ZstdCompressor.FLUSH_FRAME)\n"
"----\n"
"Flush any remaining data in internal buffer.\n\n"
"Since zstd data consists of one or more independent frames, the compressor\n"
"object can still be used after this method is called.\n\n"
"Parameter\n"
"mode: Can be these 2 values .FLUSH_FRAME, .FLUSH_BLOCK.");

static PyObject *
ZstdCompressor_flush(ZstdCompressor *self, PyObject *args, PyObject *kwargs)
{
    static char *kwlist[] = {"mode", NULL};
    int mode = ZSTD_e_end;

    PyObject *ret;

    if (!PyArg_ParseTupleAndKeywords(args, kwargs,
                                     "|i:ZstdCompressor.flush", kwlist,
                                     &mode)) {
        return NULL;
    }

    /* Check mode value */
    if (mode != ZSTD_e_end && mode != ZSTD_e_flush) {
        PyErr_SetString(PyExc_ValueError,
                        "mode argument wrong value, it should be "
                        "ZstdCompressor.FLUSH_FRAME or "
                        "ZstdCompressor.FLUSH_BLOCK.");
        return NULL;
    }

    /* Thread-safe code */
    ACQUIRE_LOCK(self);
    ret = compress_impl(self, NULL, mode, 0);

    if (ret) {
        self->last_mode = mode;
    } else {
        self->last_mode = ZSTD_e_end;

        /* Resetting cctx's session never fail */
        ZSTD_CCtx_reset(self->cctx, ZSTD_reset_session_only);
    }
    RELEASE_LOCK(self);

    return ret;
}

PyDoc_STRVAR(ZstdCompressor_set_pledged_input_size_doc,
"_set_pledged_input_size(size)\n"
"----\n"
"*This is an undocumented method, because it may be used incorrectly.*\n\n"
"Set uncompressed content size of a frame, the size will be written into the\n"
"frame header.\n"
"1, If called when (.last_mode != .FLUSH_FRAME), a RuntimeError will be raised.\n"
"2, If the actual size doesn't match the value, a ZstdError will be raised, and\n"
"   the last compressed chunk is likely to be lost.\n"
"3, The size is only valid for one frame, then it restores to \"unknown size\".\n\n"
"Parameter\n"
"size: Uncompressed content size of a frame, None means \"unknown size\".");

static PyObject *
ZstdCompressor_set_pledged_input_size(ZstdCompressor *self, PyObject *size)
{
    uint64_t pledged_size;
    size_t zstd_ret;
    PyObject *ret;

    /* Get size value */
    if (size == Py_None) {
        pledged_size = ZSTD_CONTENTSIZE_UNKNOWN;
    } else {
        pledged_size = PyLong_AsUnsignedLongLong(size);
        if (pledged_size == (uint64_t)-1 && PyErr_Occurred()) {
            PyErr_SetString(PyExc_ValueError,
                            "size argument should be 64-bit unsigned integer "
                            "value, or None.");
            return NULL;
        }
    }

    /* Thread-safe code */
    ACQUIRE_LOCK(self);

    /* Check the current mode */
    if (self->last_mode != ZSTD_e_end) {
        PyErr_SetString(PyExc_RuntimeError,
                        "._set_pledged_input_size() method must be called "
                        "when (.last_mode == .FLUSH_FRAME).");
        goto error;
    }

    /* Set pledged content size */
    zstd_ret = ZSTD_CCtx_setPledgedSrcSize(self->cctx, pledged_size);
    if (ZSTD_isError(zstd_ret)) {
        STATE_FROM_OBJ(self);
        set_zstd_error(MODULE_STATE, ERR_SET_PLEDGED_INPUT_SIZE, zstd_ret);
        goto error;
    }

    /* Return None */
    ret = Py_None;
    Py_INCREF(ret);
    goto success;

error:
    ret = NULL;
success:
    RELEASE_LOCK(self);
    return ret;
}

static PyMethodDef ZstdCompressor_methods[] = {
    {"compress", (PyCFunction)ZstdCompressor_compress,
     METH_VARARGS|METH_KEYWORDS, ZstdCompressor_compress_doc},

    {"flush", (PyCFunction)ZstdCompressor_flush,
     METH_VARARGS|METH_KEYWORDS, ZstdCompressor_flush_doc},

    {"_set_pledged_input_size", (PyCFunction)ZstdCompressor_set_pledged_input_size,
     METH_O, ZstdCompressor_set_pledged_input_size_doc},

    {"__reduce__", (PyCFunction)reduce_cannot_pickle,
     METH_NOARGS, reduce_cannot_pickle_doc},

    {NULL, NULL, 0, NULL}
};

PyDoc_STRVAR(ZstdCompressor_last_mode_doc,
"The last mode used to this compressor object, its value can be .CONTINUE,\n"
".FLUSH_BLOCK, .FLUSH_FRAME. Initialized to .FLUSH_FRAME.\n\n"
"It can be used to get the current state of a compressor, such as, data flushed,\n"
"a frame ended.");

static PyMemberDef ZstdCompressor_members[] = {
    {"last_mode", T_INT, offsetof(ZstdCompressor, last_mode),
      READONLY, ZstdCompressor_last_mode_doc},
    {NULL}
};

static PyType_Slot zstdcompressor_slots[] = {
    {Py_tp_new, ZstdCompressor_new},
    {Py_tp_dealloc, ZstdCompressor_dealloc},
    {Py_tp_init, ZstdCompressor_init},
    {Py_tp_methods, ZstdCompressor_methods},
    {Py_tp_members, ZstdCompressor_members},
    {Py_tp_doc, (char*)ZstdCompressor_doc},
    {0, 0}
};

static PyType_Spec zstdcompressor_type_spec = {
    .name = "pyzstd.ZstdCompressor",
    .basicsize = sizeof(ZstdCompressor),
    .flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE,
    .slots = zstdcompressor_slots,
};

/* ------------------------------
     RichMemZstdCompressor code
   ------------------------------ */
static int
RichMemZstdCompressor_init(ZstdCompressor *self, PyObject *args, PyObject *kwargs)
{
    static char *kwlist[] = {"level_or_option", "zstd_dict", NULL};
    PyObject *level_or_option = Py_None;
    PyObject *zstd_dict = Py_None;

    if (!PyArg_ParseTupleAndKeywords(args, kwargs,
                                     "|OO:RichMemZstdCompressor.__init__", kwlist,
                                     &level_or_option, &zstd_dict)) {
        return -1;
    }

    /* Only called once */
    if (self->inited) {
        PyErr_SetString(PyExc_RuntimeError, init_twice_msg);
        return -1;
    }
    self->inited = 1;

    /* Set compressLevel/option to compression context */
    if (level_or_option != Py_None) {
        if (set_c_parameters(self, level_or_option) < 0) {
            return -1;
        }
    }

    /* Check effective condition */
    if (self->use_multithread) {
        char *msg = "Currently \"rich memory mode\" has no effect on "
                    "zstd multi-threaded compression (set "
                    "\"CParameter.nbWorkers\" >= 1), it will allocate "
                    "unnecessary memory.";
        if (PyErr_WarnEx(PyExc_ResourceWarning, msg, 1) < 0) {
            return -1;
        }
    }

    /* Load dictionary to compression context */
    if (zstd_dict != Py_None) {
        if (load_c_dict(self, zstd_dict) < 0) {
            return -1;
        }

        /* Py_INCREF the dict */
        Py_INCREF(zstd_dict);
        self->dict = zstd_dict;
    }

    return 0;
}

PyDoc_STRVAR(RichMemZstdCompressor_compress_doc,
"compress(data)\n"
"----\n"
"Compress data using rich memory mode, return a single zstd frame.\n\n"
"Compressing b'' will get an empty content frame (9 bytes or more).\n\n"
"Parameter\n"
"data: A bytes-like object, data to be compressed.");

static PyObject *
RichMemZstdCompressor_compress(ZstdCompressor *self, PyObject *args, PyObject *kwargs)
{
    static char *kwlist[] = {"data", NULL};
    Py_buffer data;

    PyObject *ret;

    if (!PyArg_ParseTupleAndKeywords(args, kwargs,
                                     "y*:RichMemZstdCompressor.compress", kwlist,
                                     &data)) {
        return NULL;
    }

    /* Thread-safe code */
    ACQUIRE_LOCK(self);

    ret = compress_impl(self, &data, ZSTD_e_end, 1);
    if (ret == NULL) {
        /* Resetting cctx's session never fail */
        ZSTD_CCtx_reset(self->cctx, ZSTD_reset_session_only);
    }

    RELEASE_LOCK(self);

    PyBuffer_Release(&data);
    return ret;
}

static PyMethodDef RichMem_ZstdCompressor_methods[] = {
    {"compress", (PyCFunction)RichMemZstdCompressor_compress,
     METH_VARARGS|METH_KEYWORDS, RichMemZstdCompressor_compress_doc},

    {"__reduce__", (PyCFunction)reduce_cannot_pickle,
     METH_NOARGS, reduce_cannot_pickle_doc},

    {NULL, NULL, 0, NULL}
};

PyDoc_STRVAR(RichMemZstdCompressor_doc,
"A compressor use rich memory mode. It is designed to allocate more memory,\n"
"but faster in some cases.\n\n"
"RichMemZstdCompressor.__init__(self, level_or_option=None, zstd_dict=None)\n"
"----\n"
"Initialize a RichMemZstdCompressor object.\n\n"
"Parameters\n"
"level_or_option: When it's an int object, it represents the compression level.\n"
"                 When it's a dict object, it contains advanced compression\n"
"                 parameters.\n"
"zstd_dict:       A ZstdDict object, pre-trained zstd dictionary.");

static PyType_Slot richmem_zstdcompressor_slots[] = {
    {Py_tp_new, ZstdCompressor_new},
    {Py_tp_dealloc, ZstdCompressor_dealloc},
    {Py_tp_init, RichMemZstdCompressor_init},
    {Py_tp_methods, RichMem_ZstdCompressor_methods},
    {Py_tp_doc, (char*)RichMemZstdCompressor_doc},
    {0, 0}
};

static PyType_Spec richmem_zstdcompressor_type_spec = {
    .name = "pyzstd.RichMemZstdCompressor",
    .basicsize = sizeof(ZstdCompressor),
    .flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE,
    .slots = richmem_zstdcompressor_slots,
};
