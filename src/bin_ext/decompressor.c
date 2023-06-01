#include "pyzstd.h"

/* -----------------------------
     Decompress implementation
   ----------------------------- */
typedef enum {
    TYPE_DECOMPRESSOR,          /* <D>, ZstdDecompressor class */
    TYPE_ENDLESS_DECOMPRESSOR,  /* <E>, EndlessZstdDecompressor class */
} decompress_type;

/* Decompress implementation for <D>, <E>, pseudo code:

        initialize_output_buffer
        while True:
            decompress_data
            set_object_flag   # .eof for <D>, .at_frame_edge for <E>.

            if output_buffer_exhausted:
                if output_buffer_reached_max_length:
                    finish
                grow_output_buffer
            elif input_buffer_exhausted:
                finish

    ZSTD_decompressStream()'s size_t return value:
      - 0 when a frame is completely decoded and fully flushed, zstd's internal
        buffer has no data.
      - An error code, which can be tested using ZSTD_isError().
      - Or any other value > 0, which means there is still some decoding or
        flushing to do to complete current frame.

      Note, decompressing "an empty input" in any case will make it > 0.

    <E> supports multiple frames, has an .at_frame_edge flag, it means both the
    input and output streams are at a frame edge. The flag can be set by this
    statement:

        .at_frame_edge = (zstd_ret == 0) ? 1 : 0

    But if decompressing "an empty input" at "a frame edge", zstd_ret will be
    non-zero, then .at_frame_edge will be wrongly set to false. To solve this
    problem, two AFE checks are needed to ensure that: when at "a frame edge",
    empty input will not be decompressed.

        // AFE check
        if (self->at_frame_edge && in->pos == in->size) {
            finish
        }

    In <E>, if .at_frame_edge is eventually set to true, but input stream has
    unconsumed data (in->pos < in->size), then the outer function
    stream_decompress() will set .at_frame_edge to false. In this case,
    although the output stream is at a frame edge, for the caller, the input
    stream is not at a frame edge, see below diagram. This behavior does not
    affect the next AFE check, since (in->pos < in->size).

    input stream:  --------------|---
                                    ^
    output stream: ====================|
                                       ^
*/
FORCE_INLINE PyObject *
decompress_impl(ZstdDecompressor *self, ZSTD_inBuffer *in,
                const Py_ssize_t max_length,
                const Py_ssize_t initial_size,
                const decompress_type type)
{
    size_t zstd_ret;
    ZSTD_outBuffer out;
    BlocksOutputBuffer buffer = {.list = NULL};
    PyObject *ret;

    /* The first AFE check for setting .at_frame_edge flag */
    if (type == TYPE_ENDLESS_DECOMPRESSOR) {
        if (self->at_frame_edge && in->pos == in->size) {
            STATE_FROM_OBJ(self);
            ret = MS_MEMBER(empty_bytes);
            Py_INCREF(ret);
            return ret;
        }
    }

    /* Initialize the output buffer */
    if (initial_size >= 0) {
        if (OutputBuffer_InitWithSize(&buffer, &out, max_length, initial_size) < 0) {
            goto error;
        }
    } else {
        if (OutputBuffer_InitAndGrow(&buffer, &out, max_length) < 0) {
            goto error;
        }
    }
    assert(out.pos == 0);

    while (1) {
        /* Decompress */
        Py_BEGIN_ALLOW_THREADS
        zstd_ret = ZSTD_decompressStream(self->dctx, &out, in);
        Py_END_ALLOW_THREADS

        /* Check error */
        if (ZSTD_isError(zstd_ret)) {
            STATE_FROM_OBJ(self);
            set_zstd_error(MODULE_STATE, ERR_DECOMPRESS, zstd_ret);
            goto error;
        }

        /* Set .eof/.af_frame_edge flag */
        if (type == TYPE_DECOMPRESSOR) {
            /* ZstdDecompressor class stops when a frame is decompressed */
            if (zstd_ret == 0) {
                self->eof = 1;
                break;
            }
        } else if (type == TYPE_ENDLESS_DECOMPRESSOR) {
            /* EndlessZstdDecompressor class supports multiple frames */
            self->at_frame_edge = (zstd_ret == 0) ? 1 : 0;

            /* The second AFE check for setting .at_frame_edge flag */
            if (self->at_frame_edge && in->pos == in->size) {
                break;
            }
        }

        /* Need to check out before in. Maybe zstd's internal buffer still has
           a few bytes can be output, grow the buffer and continue. */
        if (out.pos == out.size) {
            /* Output buffer exhausted */

            /* Output buffer reached max_length */
            if (OutputBuffer_ReachedMaxLength(&buffer, &out)) {
                break;
            }

            /* Grow output buffer */
            if (OutputBuffer_Grow(&buffer, &out) < 0) {
                goto error;
            }
            assert(out.pos == 0);

        } else if (in->pos == in->size) {
            /* Finished */
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

FORCE_INLINE void
decompressor_reset_session(ZstdDecompressor *self,
                           const decompress_type type)
{
    /* Reset variables */
    self->in_begin = 0;
    self->in_end = 0;

    if (type == TYPE_DECOMPRESSOR) {
        Py_CLEAR(self->unused_data);
    }

    /* Reset variables in one operation */
    self->needs_input = 1;
    self->at_frame_edge = 1;
    self->eof = 0;
    self->_unused_char_for_align = 0;

    /* Resetting session never fail */
    ZSTD_DCtx_reset(self->dctx, ZSTD_reset_session_only);
}

/* For ZstdDecompressor, EndlessZstdDecompressor. */
FORCE_INLINE PyObject *
stream_decompress(ZstdDecompressor *self, PyObject *args, PyObject *kwargs,
                  const decompress_type type)
{
    static char *kwlist[] = {"data", "max_length", NULL};
    Py_buffer data;
    Py_ssize_t max_length = -1;

    Py_ssize_t initial_buffer_size = -1;
    ZSTD_inBuffer in;
    PyObject *ret = NULL;
    int use_input_buffer;

    if (!PyArg_ParseTupleAndKeywords(args, kwargs,
                                     "y*|n:ZstdDecompressor.decompress", kwlist,
                                     &data, &max_length)) {
        return NULL;
    }

    /* Thread-safe code */
    ACQUIRE_LOCK(self);

    if (type == TYPE_DECOMPRESSOR) {
        /* Check .eof flag */
        if (self->eof) {
            PyErr_SetString(PyExc_EOFError, "Already at the end of a zstd frame.");
            assert(ret == NULL);
            goto success;
        }
    } else if (type == TYPE_ENDLESS_DECOMPRESSOR) {
        /* Fast path for the first frame */
        if (self->at_frame_edge && self->in_begin == self->in_end) {
            /* Read decompressed size */
            uint64_t decompressed_size = ZSTD_getFrameContentSize(data.buf, data.len);

            /* These two zstd constants always > PY_SSIZE_T_MAX:
                  ZSTD_CONTENTSIZE_UNKNOWN is (0ULL - 1)
                  ZSTD_CONTENTSIZE_ERROR   is (0ULL - 2)

               Use ZSTD_findFrameCompressedSize() to check complete frame,
               prevent allocating too much memory for small input chunk. */

            if (decompressed_size <= (uint64_t) PY_SSIZE_T_MAX &&
                !ZSTD_isError(ZSTD_findFrameCompressedSize(data.buf, data.len)) )
            {
                initial_buffer_size = (Py_ssize_t) decompressed_size;
            }
        }
    }

    /* Prepare input buffer w/wo unconsumed data */
    if (self->in_begin == self->in_end) {
        /* No unconsumed data */
        use_input_buffer = 0;

        in.src = data.buf;
        in.size = data.len;
        in.pos = 0;
    } else if (data.len == 0) {
        /* Has unconsumed data, fast path for b'' */
        assert(self->in_begin < self->in_end);

        use_input_buffer = 1;

        in.src = self->input_buffer + self->in_begin;
        in.size = self->in_end - self->in_begin;
        in.pos = 0;
    } else {
        /* Has unconsumed data */
        use_input_buffer = 1;

        /* Unconsumed data size in input_buffer */
        const size_t used_now = self->in_end - self->in_begin;
        assert(self->in_end > self->in_begin);

        /* Number of bytes we can append to input buffer */
        const size_t avail_now = self->input_buffer_size - self->in_end;
        assert(self->input_buffer_size >= self->in_end);

        /* Number of bytes we can append if we move existing contents to
           beginning of buffer */
        const size_t avail_total = self->input_buffer_size - used_now;
        assert(self->input_buffer_size >= used_now);

        if (avail_total < (size_t) data.len) {
            char *tmp;
            const size_t new_size = used_now + data.len;

            /* Allocate with new size */
            tmp = PyMem_Malloc(new_size);
            if (tmp == NULL) {
                PyErr_NoMemory();
                goto error;
            }

            /* Copy unconsumed data to the beginning of new buffer */
            memcpy(tmp,
                   self->input_buffer + self->in_begin,
                   used_now);

            /* Switch to new buffer */
            PyMem_Free(self->input_buffer);
            self->input_buffer = tmp;
            self->input_buffer_size = new_size;

            /* Set begin & end position */
            self->in_begin = 0;
            self->in_end = used_now;
        } else if (avail_now < (size_t) data.len) {
            /* Move unconsumed data to the beginning.
               Overlap is possible, so use memmove(). */
            memmove(self->input_buffer,
                    self->input_buffer + self->in_begin,
                    used_now);

            /* Set begin & end position */
            self->in_begin = 0;
            self->in_end = used_now;
        }

        /* Copy data to input buffer */
        memcpy(self->input_buffer + self->in_end, data.buf, data.len);
        self->in_end += data.len;

        in.src = self->input_buffer + self->in_begin;
        in.size = used_now + data.len;
        in.pos = 0;
    }
    assert(in.pos == 0);

    /* Decompress */
    ret = decompress_impl(self, &in,
                          max_length, initial_buffer_size,
                          type);
    if (ret == NULL) {
        goto error;
    }

    /* Unconsumed input data */
    if (in.pos == in.size) {
        if (type == TYPE_DECOMPRESSOR) {
            if (Py_SIZE(ret) == max_length || self->eof) {
                self->needs_input = 0;
            } else {
                self->needs_input = 1;
            }
        } else if (type == TYPE_ENDLESS_DECOMPRESSOR) {
            if (Py_SIZE(ret) == max_length && !self->at_frame_edge) {
                self->needs_input = 0;
            } else {
                self->needs_input = 1;
            }
        }

        if (use_input_buffer) {
            /* Clear input_buffer */
            self->in_begin = 0;
            self->in_end = 0;
        }
    } else {
        const size_t data_size = in.size - in.pos;

        /*if (type == DECOMPRESSOR) {
            if (self->eof) {
                self->needs_input = 0;
            } else {
                self->needs_input = 0;
            }
        } else if (type == ENDLESS_DECOMPRESSOR) {
            self->needs_input = 0;
        }*/
        self->needs_input = 0;

        if (type == TYPE_ENDLESS_DECOMPRESSOR) {
            /*if (self->at_frame_edge) {
                self->at_frame_edge = 0;
            }*/
            self->at_frame_edge = 0;
        }

        if (!use_input_buffer) {
            /* Discard buffer if it's too small
               (resizing it may needlessly copy the current contents) */
            if (self->input_buffer != NULL &&
                self->input_buffer_size < data_size)
            {
                PyMem_Free(self->input_buffer);
                self->input_buffer = NULL;
                self->input_buffer_size = 0;
            }

            /* Allocate if necessary */
            if (self->input_buffer == NULL) {
                self->input_buffer = PyMem_Malloc(data_size);
                if (self->input_buffer == NULL) {
                    PyErr_NoMemory();
                    goto error;
                }
                self->input_buffer_size = data_size;
            }

            /* Copy unconsumed data */
            memcpy(self->input_buffer, (char*)in.src + in.pos, data_size);
            self->in_begin = 0;
            self->in_end = data_size;
        } else {
            /* Use input buffer */
            self->in_begin += in.pos;
        }
    }

    goto success;

error:
    /* Reset decompressor's states/session */
    decompressor_reset_session(self, type);

    Py_CLEAR(ret);
success:
    RELEASE_LOCK(self);

    PyBuffer_Release(&data);
    return ret;
}

/* -------------------------
     ZstdDecompressor code
   ------------------------- */
static PyObject *
ZstdDecompressor_new(PyTypeObject *type, PyObject *args, PyObject *kwds)
{
    ZstdDecompressor *self;
    self = (ZstdDecompressor*)type->tp_alloc(type, 0);
    if (self == NULL) {
        goto error;
    }

    assert(self->dict == NULL);
    assert(self->input_buffer == NULL);
    assert(self->input_buffer_size == 0);
    assert(self->in_begin == 0);
    assert(self->in_end == 0);
    assert(self->unused_data == NULL);
    assert(self->eof == 0);
    assert(self->inited == 0);

    /* needs_input flag */
    self->needs_input = 1;

    /* at_frame_edge flag */
    self->at_frame_edge = 1;

    /* Keep this first. Set module state to self. */
    SET_STATE_TO_OBJ(type, self);

    /* Decompression context */
    self->dctx = ZSTD_createDCtx();
    if (self->dctx == NULL) {
        STATE_FROM_OBJ(self);
        PyErr_SetString(MS_MEMBER(ZstdError),
                        "Unable to create ZSTD_DCtx instance.");
        goto error;
    }

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
ZstdDecompressor_dealloc(ZstdDecompressor *self)
{
    /* Free decompression context */
    ZSTD_freeDCtx(self->dctx);

    /* Py_XDECREF the dict after free decompression context */
    Py_XDECREF(self->dict);

    /* Free unconsumed input data buffer */
    PyMem_Free(self->input_buffer);

    /* Free unused data */
    Py_XDECREF(self->unused_data);

    /* Free thread lock */
    if (self->lock) {
        PyThread_free_lock(self->lock);
    }

    PyTypeObject *tp = Py_TYPE(self);
    tp->tp_free((PyObject*)self);
    Py_DECREF(tp);
}

PyDoc_STRVAR(ZstdDecompressor_doc,
"A streaming decompressor, it stops after a frame is decompressed.\n"
"Thread-safe at method level.\n\n"
"ZstdDecompressor.__init__(self, zstd_dict=None, option=None)\n"
"----\n"
"Initialize a ZstdDecompressor object.\n\n"
"Parameters\n"
"zstd_dict: A ZstdDict object, pre-trained zstd dictionary.\n"
"option:    A dict object that contains advanced decompression parameters.");

static int
ZstdDecompressor_init(ZstdDecompressor *self, PyObject *args, PyObject *kwargs)
{
    static char *kwlist[] = {"zstd_dict", "option", NULL};
    PyObject *zstd_dict = Py_None;
    PyObject *option = Py_None;

    if (!PyArg_ParseTupleAndKeywords(args, kwargs,
                                     "|OO:ZstdDecompressor.__init__", kwlist,
                                     &zstd_dict, &option)) {
        return -1;
    }

    /* Only called once */
    if (self->inited) {
        PyErr_SetString(PyExc_RuntimeError, init_twice_msg);
        return -1;
    }
    self->inited = 1;

    /* Load dictionary to decompression context */
    if (zstd_dict != Py_None) {
        if (load_d_dict(self, zstd_dict) < 0) {
            return -1;
        }

        /* Py_INCREF the dict */
        Py_INCREF(zstd_dict);
        self->dict = zstd_dict;
    }

    /* Set option to decompression context */
    if (option != Py_None) {
        if (set_d_parameters(self, option) < 0) {
            return -1;
        }
    }

    return 0;
}

static PyObject *
unused_data_get(ZstdDecompressor *self, void *Py_UNUSED(ignored))
{
    PyObject *ret;

    /* Thread-safe code */
    ACQUIRE_LOCK(self);

    if (!self->eof) {
        STATE_FROM_OBJ(self);
        ret = MS_MEMBER(empty_bytes);
        Py_INCREF(ret);
    } else {
        if (self->unused_data == NULL) {
            self->unused_data = PyBytes_FromStringAndSize(
                                    self->input_buffer + self->in_begin,
                                    self->in_end - self->in_begin);
            ret = self->unused_data;
            Py_XINCREF(ret);
        } else {
            ret = self->unused_data;
            Py_INCREF(ret);
        }
    }

    RELEASE_LOCK(self);

    return ret;
}

PyDoc_STRVAR(ZstdDecompressor_decompress_doc,
"decompress(data, max_length=-1)\n"
"----\n"
"Decompress data, return a chunk of decompressed data if possible, or b''\n"
"otherwise.\n\n"
"It stops after a frame is decompressed.\n\n"
"Parameters\n"
"data:       A bytes-like object, zstd data to be decompressed.\n"
"max_length: Maximum size of returned data. When it is negative, the size of\n"
"            output buffer is unlimited. When it is nonnegative, returns at\n"
"            most max_length bytes of decompressed data.");

static PyObject *
ZstdDecompressor_decompress(ZstdDecompressor *self, PyObject *args, PyObject *kwargs)
{
    return stream_decompress(self, args, kwargs, TYPE_DECOMPRESSOR);
}

PyDoc_STRVAR(ZstdDecompressor_reset_session_doc,
"_reset_session()\n"
"----\n"
"This is an undocumented method. Reset decompressor's states/session, don't\n"
"reset parameters and dictionary.");

static PyObject *
ZstdDecompressor_reset_session(ZstdDecompressor *self)
{
    /* Thread-safe code */
    ACQUIRE_LOCK(self);
    decompressor_reset_session(self, TYPE_DECOMPRESSOR);
    RELEASE_LOCK(self);

    Py_RETURN_NONE;
}

static PyMethodDef ZstdDecompressor_methods[] = {
    {"decompress", (PyCFunction)ZstdDecompressor_decompress,
     METH_VARARGS|METH_KEYWORDS, ZstdDecompressor_decompress_doc},

    {"_reset_session", (PyCFunction)ZstdDecompressor_reset_session,
     METH_NOARGS, ZstdDecompressor_reset_session_doc},

    {"__reduce__", (PyCFunction)reduce_cannot_pickle,
     METH_NOARGS, reduce_cannot_pickle_doc},

    {NULL, NULL, 0, NULL}
};

PyDoc_STRVAR(ZstdDecompressor_eof_doc,
"True means the end of the first frame has been reached. If decompress data\n"
"after that, an EOFError exception will be raised.");

PyDoc_STRVAR(ZstdDecompressor_needs_input_doc,
"If the max_length output limit in .decompress() method has been reached, and\n"
"the decompressor has (or may has) unconsumed input data, it will be set to\n"
"False. In this case, pass b'' to .decompress() method may output further data.");

PyDoc_STRVAR(ZstdDecompressor_unused_data_doc,
"A bytes object. When ZstdDecompressor object stops after a frame is\n"
"decompressed, unused input data after the frame. Otherwise this will be b''.");

static PyMemberDef ZstdDecompressor_members[] = {
    {"eof", T_BOOL, offsetof(ZstdDecompressor, eof),
     READONLY, ZstdDecompressor_eof_doc},

    {"needs_input", T_BOOL, offsetof(ZstdDecompressor, needs_input),
     READONLY, ZstdDecompressor_needs_input_doc},

    {NULL}
};

static PyGetSetDef ZstdDecompressor_getset[] = {
    {"unused_data", (getter)unused_data_get, NULL,
     ZstdDecompressor_unused_data_doc},

    {NULL},
};

static PyType_Slot ZstdDecompressor_slots[] = {
    {Py_tp_new, ZstdDecompressor_new},
    {Py_tp_dealloc, ZstdDecompressor_dealloc},
    {Py_tp_init, ZstdDecompressor_init},
    {Py_tp_methods, ZstdDecompressor_methods},
    {Py_tp_members, ZstdDecompressor_members},
    {Py_tp_getset, ZstdDecompressor_getset},
    {Py_tp_doc, (char*)ZstdDecompressor_doc},
    {0, 0}
};

static PyType_Spec ZstdDecompressor_type_spec = {
    .name = "pyzstd.ZstdDecompressor",
    .basicsize = sizeof(ZstdDecompressor),
    .flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE,
    .slots = ZstdDecompressor_slots,
};

/* -------------------------------
     EndlessZstdDecompressor code
   ------------------------------- */
PyDoc_STRVAR(EndlessZstdDecompressor_doc,
"A streaming decompressor, accepts multiple concatenated frames.\n"
"Thread-safe at method level.\n\n"
"EndlessZstdDecompressor.__init__(self, zstd_dict=None, option=None)\n"
"----\n"
"Initialize an EndlessZstdDecompressor object.\n\n"
"Parameters\n"
"zstd_dict: A ZstdDict object, pre-trained zstd dictionary.\n"
"option:    A dict object that contains advanced decompression parameters.");

PyDoc_STRVAR(EndlessZstdDecompressor_decompress_doc,
"decompress(data, max_length=-1)\n"
"----\n"
"Decompress data, return a chunk of decompressed data if possible, or b''\n"
"otherwise.\n\n"
"Parameters\n"
"data:       A bytes-like object, zstd data to be decompressed.\n"
"max_length: Maximum size of returned data. When it is negative, the size of\n"
"            output buffer is unlimited. When it is nonnegative, returns at\n"
"            most max_length bytes of decompressed data.");

static PyObject *
EndlessZstdDecompressor_decompress(ZstdDecompressor *self, PyObject *args, PyObject *kwargs)
{
    return stream_decompress(self, args, kwargs, TYPE_ENDLESS_DECOMPRESSOR);
}

static PyObject *
EndlessZstdDecompressor_reset_session(ZstdDecompressor *self)
{
    /* Thread-safe code */
    ACQUIRE_LOCK(self);
    decompressor_reset_session(self, TYPE_ENDLESS_DECOMPRESSOR);
    RELEASE_LOCK(self);

    Py_RETURN_NONE;
}

static PyMethodDef EndlessZstdDecompressor_methods[] = {
    {"decompress", (PyCFunction)EndlessZstdDecompressor_decompress,
     METH_VARARGS|METH_KEYWORDS, EndlessZstdDecompressor_decompress_doc},

    {"_reset_session", (PyCFunction)EndlessZstdDecompressor_reset_session,
     METH_NOARGS, ZstdDecompressor_reset_session_doc},

    {"__reduce__", (PyCFunction)reduce_cannot_pickle,
     METH_NOARGS, reduce_cannot_pickle_doc},

    {NULL, NULL, 0, NULL}
};

PyDoc_STRVAR(EndlessZstdDecompressor_at_frame_edge_doc,
"True when both the input and output streams are at a frame edge, means a frame is\n"
"completely decoded and fully flushed, or the decompressor just be initialized.\n\n"
"This flag could be used to check data integrity in some cases.");

static PyMemberDef EndlessZstdDecompressor_members[] = {
    {"at_frame_edge", T_BOOL, offsetof(ZstdDecompressor, at_frame_edge),
     READONLY, EndlessZstdDecompressor_at_frame_edge_doc},

    {"needs_input", T_BOOL, offsetof(ZstdDecompressor, needs_input),
     READONLY, ZstdDecompressor_needs_input_doc},

    {NULL}
};

static PyType_Slot EndlessZstdDecompressor_slots[] = {
    {Py_tp_new, ZstdDecompressor_new},
    {Py_tp_dealloc, ZstdDecompressor_dealloc},
    {Py_tp_init, ZstdDecompressor_init},
    {Py_tp_methods, EndlessZstdDecompressor_methods},
    {Py_tp_members, EndlessZstdDecompressor_members},
    {Py_tp_doc, (char*)EndlessZstdDecompressor_doc},
    {0, 0}
};

static PyType_Spec EndlessZstdDecompressor_type_spec = {
    .name = "pyzstd.EndlessZstdDecompressor",
    .basicsize = sizeof(ZstdDecompressor),
    .flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE,
    .slots = EndlessZstdDecompressor_slots,
};

PyDoc_STRVAR(decompress_doc,
"decompress(data, zstd_dict=None, option=None)\n"
"----\n"
"Decompress a zstd data, return a bytes object.\n\n"
"Support multiple concatenated frames.\n\n"
"Parameters\n"
"data:      A bytes-like object, compressed zstd data.\n"
"zstd_dict: A ZstdDict object, pre-trained zstd dictionary.\n"
"option:    A dict object, contains advanced decompression parameters.");

static PyObject *
decompress(PyObject *module, PyObject *args, PyObject *kwargs)
{
    static char *kwlist[] = {"data", "zstd_dict", "option", NULL};
    Py_buffer data;
    PyObject *zstd_dict = Py_None;
    PyObject *option = Py_None;

    if (!PyArg_ParseTupleAndKeywords(args, kwargs,
                                     "y*|OO:decompress", kwlist,
                                     &data, &zstd_dict, &option)) {
        return NULL;
    }

    uint64_t decompressed_size;
    Py_ssize_t initial_size;
    ZstdDecompressor self = {0};
    ZSTD_inBuffer in;
    STATE_FROM_MODULE(module);
    PyObject *ret = NULL;

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

    /* Load dictionary to decompression context */
    if (zstd_dict != Py_None) {
        if (load_d_dict(&self, zstd_dict) < 0) {
            goto error;
        }
    }

    /* Set option to decompression context */
    if (option != Py_None) {
        if (set_d_parameters(&self, option) < 0) {
            goto error;
        }
    }

    /* Prepare input data */
    in.src = data.buf;
    in.size = data.len;
    in.pos = 0;

    /* Get decompressed size */
    decompressed_size = ZSTD_getFrameContentSize(data.buf, data.len);
    /* These two zstd constants always > PY_SSIZE_T_MAX:
         ZSTD_CONTENTSIZE_UNKNOWN is (0ULL - 1)
         ZSTD_CONTENTSIZE_ERROR   is (0ULL - 2) */
    if (decompressed_size <= (uint64_t) PY_SSIZE_T_MAX) {
        initial_size = (Py_ssize_t) decompressed_size;
    } else {
        initial_size = -1;
    }

    /* Decompress */
    ret = decompress_impl(&self, &in, -1, initial_size,
                          TYPE_ENDLESS_DECOMPRESSOR);
    if (ret == NULL) {
        goto error;
    }

    /* Check data integrity. at_frame_edge flag is 1 when both the input and
       output streams are at a frame edge. */
    if (self.at_frame_edge == 0) {
        char *extra_msg = (Py_SIZE(ret) == 0) ? "." :
                          ", if want to output these decompressed data, use "
                          "decompress_stream function or "
                          "EndlessZstdDecompressor class to decompress.";
        PyErr_Format(MS_MEMBER(ZstdError),
                     "Decompression failed: zstd data ends in an incomplete "
                     "frame, maybe the input data was truncated. Decompressed "
                     "data is %zd bytes%s",
                     Py_SIZE(ret), extra_msg);
        goto error;
    }

    goto success;

error:
    Py_CLEAR(ret);
success:
    /* Free decompression context */
    ZSTD_freeDCtx(self.dctx);
    /* Release data */
    PyBuffer_Release(&data);
    return ret;
}
