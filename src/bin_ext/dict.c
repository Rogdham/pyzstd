#include "pyzstd.h"

/* -----------------
     ZstdDict code
   ----------------- */
static PyObject *
ZstdDict_new(PyTypeObject *type, PyObject *args, PyObject *kwds)
{
    ZstdDict *self;
    self = (ZstdDict*)type->tp_alloc(type, 0);
    if (self == NULL) {
        goto error;
    }

    assert(self->dict_content == NULL);
    assert(self->dict_id == 0);
    assert(self->d_dict == NULL);
    assert(self->inited == 0);

    /* Keep this first. Set module state to self. */
    SET_STATE_TO_OBJ(type, self);

    /* ZSTD_CDict dict */
    self->c_dicts = PyDict_New();
    if (self->c_dicts == NULL) {
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
ZstdDict_dealloc(ZstdDict *self)
{
    /* Free ZSTD_CDict instances */
    Py_XDECREF(self->c_dicts);

    /* Free ZSTD_DDict instance */
    ZSTD_freeDDict(self->d_dict);

    /* Release dict_content after Free ZSTD_CDict/ZSTD_DDict instances */
    Py_XDECREF(self->dict_content);

    /* Free thread lock */
    if (self->lock) {
        PyThread_free_lock(self->lock);
    }

    PyTypeObject *tp = Py_TYPE(self);
    tp->tp_free((PyObject*)self);
    Py_DECREF(tp);
}

static int
ZstdDict_init(ZstdDict *self, PyObject *args, PyObject *kwargs)
{
    static char *kwlist[] = {"dict_content", "is_raw", NULL};
    PyObject *dict_content;
    int is_raw = 0;

    if (!PyArg_ParseTupleAndKeywords(args, kwargs,
                                     "O|p:ZstdDict.__init__", kwlist,
                                     &dict_content, &is_raw)) {
        return -1;
    }

    /* Only called once */
    if (self->inited) {
        PyErr_SetString(PyExc_RuntimeError, init_twice_msg);
        return -1;
    }
    self->inited = 1;

    /* Check dict_content's type */
    self->dict_content = PyBytes_FromObject(dict_content);
    if (self->dict_content == NULL) {
        PyErr_SetString(PyExc_TypeError,
                        "dict_content argument should be bytes-like object.");
        return -1;
    }

    /* Both ordinary dictionary and "raw content" dictionary should
       at least 8 bytes */
    if (Py_SIZE(self->dict_content) < 8) {
        PyErr_SetString(PyExc_ValueError,
                        "Zstd dictionary content should at least 8 bytes.");
        return -1;
    }

    /* Get dict_id, 0 means "raw content" dictionary. */
    self->dict_id = ZSTD_getDictID_fromDict(PyBytes_AS_STRING(self->dict_content),
                                            Py_SIZE(self->dict_content));

    /* Check validity for ordinary dictionary */
    if (!is_raw && self->dict_id == 0) {
        char *msg = "The dict_content argument is not a valid zstd "
                    "dictionary. The first 4 bytes of a valid zstd dictionary "
                    "should be a magic number: b'\\x37\\xA4\\x30\\xEC'.\n"
                    "If you are an advanced user, and can be sure that "
                    "dict_content argument is a \"raw content\" zstd "
                    "dictionary, set is_raw parameter to True.";
        PyErr_SetString(PyExc_ValueError, msg);
        return -1;
    }

    return 0;
}

static PyObject *
ZstdDict_reduce(ZstdDict *self)
{
    /* return Py_BuildValue("O(On)", Py_TYPE(self),
                            self->dict_content,
                            self->dict_id == 0);
       v0.15.7 added .as_* attributes, pickle will cause more confusion. */
    PyErr_SetString(PyExc_TypeError,
                    "ZstdDict object intentionally doesn't support pickle. If need "
                    "to save zstd dictionary to disk, please save .dict_content "
                    "attribute, it's a bytes object. So that the zstd dictionary "
                    "can be used with other programs.");
    return NULL;
}

static PyMethodDef ZstdDict_methods[] = {
    {"__reduce__", (PyCFunction)ZstdDict_reduce,
     METH_NOARGS, reduce_cannot_pickle_doc},

    {NULL, NULL, 0, NULL}
};

PyDoc_STRVAR(ZstdDict_dict_doc,
"Zstd dictionary, used for compression/decompression.\n\n"
"ZstdDict.__init__(self, dict_content, is_raw=False)\n"
"----\n"
"Initialize a ZstdDict object.\n\n"
"Parameters\n"
"dict_content: A bytes-like object, dictionary's content.\n"
"is_raw:       This parameter is for advanced user. True means dict_content\n"
"              argument is a \"raw content\" dictionary, free of any format\n"
"              restriction. False means dict_content argument is an ordinary\n"
"              zstd dictionary, was created by zstd functions, follow a\n"
"              specified format.");

PyDoc_STRVAR(ZstdDict_dictid_doc,
"ID of zstd dictionary, a 32-bit unsigned int value.\n\n"
"Non-zero means ordinary dictionary, was created by zstd functions, follow\n"
"a specified format.\n\n"
"0 means a \"raw content\" dictionary, free of any format restriction, used\n"
"for advanced user.");

PyDoc_STRVAR(ZstdDict_dictcontent_doc,
"The content of zstd dictionary, a bytes object, it's the same as dict_content\n"
"argument in ZstdDict.__init__() method. It can be used with other programs.");

static PyObject *
ZstdDict_str(ZstdDict *dict)
{
    char buf[64];
    PyOS_snprintf(buf, sizeof(buf),
                  "<ZstdDict dict_id=%u dict_size=%zd>",
                  dict->dict_id, Py_SIZE(dict->dict_content));

    return PyUnicode_FromString(buf);
}

static PyMemberDef ZstdDict_members[] = {
    {"dict_id", T_UINT, offsetof(ZstdDict, dict_id), READONLY, ZstdDict_dictid_doc},
    {"dict_content", T_OBJECT_EX, offsetof(ZstdDict, dict_content), READONLY, ZstdDict_dictcontent_doc},
    {NULL}
};

PyDoc_STRVAR(ZstdDict_as_digested_dict_doc,
"Load as a digested dictionary to compressor, by passing this attribute as\n"
"zstd_dict argument: compress(dat, zstd_dict=zd.as_digested_dict)\n"
"1, Some advanced compression parameters of compressor may be overridden\n"
"   by parameters of digested dictionary.\n"
"2, ZstdDict has a digested dictionaries cache for each compression level.\n"
"   It's faster when loading again a digested dictionary with the same\n"
"   compression level.\n"
"3, No need to use this for decompression.");

static PyObject *
ZstdDict_as_digested_dict_get(ZstdDict *self, void *Py_UNUSED(ignored))
{
    return Py_BuildValue("Oi", self, DICT_TYPE_DIGESTED);
}

PyDoc_STRVAR(ZstdDict_as_undigested_dict_doc,
"Load as an undigested dictionary to compressor, by passing this attribute as\n"
"zstd_dict argument: compress(dat, zstd_dict=zd.as_undigested_dict)\n"
"1, The advanced compression parameters of compressor will not be overridden.\n"
"2, Loading an undigested dictionary is costly. If load an undigested dictionary\n"
"   multiple times, consider reusing a compressor object.\n"
"3, No need to use this for decompression.");

static PyObject *
ZstdDict_as_undigested_dict_get(ZstdDict *self, void *Py_UNUSED(ignored))
{
    return Py_BuildValue("Oi", self, DICT_TYPE_UNDIGESTED);
}

PyDoc_STRVAR(ZstdDict_as_prefix_doc,
"Load as a prefix to compressor/decompressor, by passing this attribute as\n"
"zstd_dict argument: compress(dat, zstd_dict=zd.as_prefix)\n"
"1, Prefix is compatible with long distance matching, while dictionary is not.\n"
"2, It only works for the first frame, then the compressor/decompressor will\n"
"   return to no prefix state.\n"
"3, When decompressing, must use the same prefix as when compressing.");

static PyObject *
ZstdDict_as_prefix_get(ZstdDict *self, void *Py_UNUSED(ignored))
{
    return Py_BuildValue("Oi", self, DICT_TYPE_PREFIX);
}

static PyGetSetDef ZstdDict_getset[] = {
    {"as_digested_dict", (getter)ZstdDict_as_digested_dict_get,
     NULL, ZstdDict_as_digested_dict_doc},

    {"as_undigested_dict", (getter)ZstdDict_as_undigested_dict_get,
     NULL, ZstdDict_as_undigested_dict_doc},

    {"as_prefix", (getter)ZstdDict_as_prefix_get,
     NULL, ZstdDict_as_prefix_doc},

    {NULL},
};

static Py_ssize_t
ZstdDict_length(ZstdDict *self)
{
    assert(PyBytes_Check(self->dict_content));
    return Py_SIZE(self->dict_content);
}

static PyType_Slot zstddict_slots[] = {
    {Py_tp_methods, ZstdDict_methods},
    {Py_tp_members, ZstdDict_members},
    {Py_tp_getset, ZstdDict_getset},
    {Py_tp_new, ZstdDict_new},
    {Py_tp_dealloc, ZstdDict_dealloc},
    {Py_tp_init, ZstdDict_init},
    {Py_tp_str, ZstdDict_str},
    {Py_tp_doc, (char*)ZstdDict_dict_doc},
    {Py_sq_length, ZstdDict_length},
    {0, 0}
};

static PyType_Spec zstddict_type_spec = {
    .name = "pyzstd.ZstdDict",
    .basicsize = sizeof(ZstdDict),
    .flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE,
    .slots = zstddict_slots,
};

/* -------------------------
     Train dictionary code
   ------------------------- */
PyDoc_STRVAR(_train_dict_doc,
"Internal function, train a zstd dictionary.");

static PyObject *
_train_dict(PyObject *module, PyObject *args)
{
    PyBytesObject *samples_bytes;
    PyObject *samples_size_list;
    Py_ssize_t dict_size;

    Py_ssize_t chunks_number;
    size_t *chunk_sizes = NULL;
    PyObject *dst_dict_bytes = NULL;
    size_t zstd_ret;
    Py_ssize_t sizes_sum;
    Py_ssize_t i;

    if (!PyArg_ParseTuple(args, "SOn:_train_dict",
                          &samples_bytes, &samples_size_list, &dict_size)) {
        return NULL;
    }

    /* Check arguments */
    if (dict_size <= 0) {
        PyErr_SetString(PyExc_ValueError, "dict_size argument should be positive number.");
        return NULL;
    }

    if (!PyList_Check(samples_size_list)) {
        PyErr_SetString(PyExc_TypeError,
                        "samples_size_list argument should be a list.");
        return NULL;
    }

    chunks_number = Py_SIZE(samples_size_list);
    if ((size_t) chunks_number > UINT32_MAX) {
        PyErr_SetString(PyExc_ValueError,
                        "The number of samples should <= UINT32_MAX.");
        return NULL;
    }

    /* Prepare chunk_sizes */
    chunk_sizes = PyMem_Malloc(chunks_number * sizeof(size_t));
    if (chunk_sizes == NULL) {
        PyErr_NoMemory();
        goto error;
    }

    sizes_sum = 0;
    for (i = 0; i < chunks_number; i++) {
        PyObject *size = PyList_GET_ITEM(samples_size_list, i);
        chunk_sizes[i] = PyLong_AsSize_t(size);
        if (chunk_sizes[i] == (size_t)-1 && PyErr_Occurred()) {
            PyErr_SetString(PyExc_ValueError,
                            "Items in samples_size_list should be an int "
                            "object, with a size_t value.");
            goto error;
        }
        sizes_sum += chunk_sizes[i];
    }

    if (sizes_sum != Py_SIZE(samples_bytes)) {
        PyErr_SetString(PyExc_ValueError,
                        "The samples size list doesn't match the concatenation's size.");
        goto error;
    }

    /* Allocate dict buffer */
    dst_dict_bytes = PyBytes_FromStringAndSize(NULL, dict_size);
    if (dst_dict_bytes == NULL) {
        goto error;
    }

    /* Train the dictionary */
    Py_BEGIN_ALLOW_THREADS
    zstd_ret = ZDICT_trainFromBuffer(PyBytes_AS_STRING(dst_dict_bytes), dict_size,
                                     PyBytes_AS_STRING(samples_bytes),
                                     chunk_sizes, (uint32_t)chunks_number);
    Py_END_ALLOW_THREADS

    /* Check zstd dict error */
    if (ZDICT_isError(zstd_ret)) {
        STATE_FROM_MODULE(module);
        set_zstd_error(MODULE_STATE, ERR_TRAIN_DICT, zstd_ret);
        goto error;
    }

    /* Resize dict_buffer */
    if (_PyBytes_Resize(&dst_dict_bytes, zstd_ret) < 0) {
        goto error;
    }

    goto success;

error:
    Py_CLEAR(dst_dict_bytes);

success:
    PyMem_Free(chunk_sizes);
    return dst_dict_bytes;
}

PyDoc_STRVAR(_finalize_dict_doc,
"Internal function, finalize a zstd dictionary.");

static PyObject *
_finalize_dict(PyObject *module, PyObject *args)
{
#if ZSTD_VERSION_NUMBER < 10405
    PyErr_Format(PyExc_NotImplementedError,
                 "_finalize_dict function only available when the underlying "
                 "zstd library's version is greater than or equal to v1.4.5. "
                 "At pyzstd module's compile-time, zstd version < v1.4.5. At "
                 "pyzstd module's run-time, zstd version is v%s.",
                 ZSTD_versionString());
    return NULL;
#else
    if (ZSTD_versionNumber() < 10405) {
        /* Must be dynamically linked */
        PyErr_Format(PyExc_NotImplementedError,
                "_finalize_dict function only available when the underlying "
                "zstd library's version is greater than or equal to v1.4.5. "
                "At pyzstd module's compile-time, zstd version >= v1.4.5. At "
                "pyzstd module's run-time, zstd version is v%s.",
                ZSTD_versionString());
        return NULL;
    }

    PyBytesObject *custom_dict_bytes;
    PyBytesObject *samples_bytes;
    PyObject *samples_size_list;
    Py_ssize_t dict_size;
    int compression_level;

    Py_ssize_t chunks_number;
    size_t *chunk_sizes = NULL;
    PyObject *dst_dict_bytes = NULL;
    size_t zstd_ret;
    ZDICT_params_t params;
    Py_ssize_t sizes_sum;
    Py_ssize_t i;

    if (!PyArg_ParseTuple(args, "SSOni:_finalize_dict",
                          &custom_dict_bytes, &samples_bytes, &samples_size_list,
                          &dict_size, &compression_level)) {
        return NULL;
    }

    /* Check arguments */
    if (dict_size <= 0) {
        PyErr_SetString(PyExc_ValueError, "dict_size argument should be positive number.");
        return NULL;
    }

    if (!PyList_Check(samples_size_list)) {
        PyErr_SetString(PyExc_TypeError,
                        "samples_size_list argument should be a list.");
        return NULL;
    }

    chunks_number = Py_SIZE(samples_size_list);
    if ((size_t) chunks_number > UINT32_MAX) {
        PyErr_SetString(PyExc_ValueError,
                        "The number of samples should <= UINT32_MAX.");
        return NULL;
    }

    /* Prepare chunk_sizes */
    chunk_sizes = PyMem_Malloc(chunks_number * sizeof(size_t));
    if (chunk_sizes == NULL) {
        PyErr_NoMemory();
        goto error;
    }

    sizes_sum = 0;
    for (i = 0; i < chunks_number; i++) {
        PyObject *size = PyList_GET_ITEM(samples_size_list, i);
        chunk_sizes[i] = PyLong_AsSize_t(size);
        if (chunk_sizes[i] == (size_t)-1 && PyErr_Occurred()) {
            PyErr_SetString(PyExc_ValueError,
                            "Items in samples_size_list should be an int "
                            "object, with a size_t value.");
            goto error;
        }
        sizes_sum += chunk_sizes[i];
    }

    if (sizes_sum != Py_SIZE(samples_bytes)) {
        PyErr_SetString(PyExc_ValueError,
                        "The samples size list doesn't match the concatenation's size.");
        goto error;
    }

    /* Allocate dict buffer */
    dst_dict_bytes = PyBytes_FromStringAndSize(NULL, dict_size);
    if (dst_dict_bytes == NULL) {
        goto error;
    }

    /* Parameters */

    /* Optimize for a specific zstd compression level, 0 means default. */
    params.compressionLevel = compression_level;
    /* Write log to stderr, 0 = none. */
    params.notificationLevel = 0;
    /* Force dictID value, 0 means auto mode (32-bits random value). */
    params.dictID = 0;

    /* Finalize the dictionary */
    Py_BEGIN_ALLOW_THREADS
    zstd_ret = ZDICT_finalizeDictionary(
                        PyBytes_AS_STRING(dst_dict_bytes), dict_size,
                        PyBytes_AS_STRING(custom_dict_bytes), Py_SIZE(custom_dict_bytes),
                        PyBytes_AS_STRING(samples_bytes), chunk_sizes,
                        (uint32_t)chunks_number, params);
    Py_END_ALLOW_THREADS

    /* Check zstd dict error */
    if (ZDICT_isError(zstd_ret)) {
        STATE_FROM_MODULE(module);
        set_zstd_error(MODULE_STATE, ERR_FINALIZE_DICT, zstd_ret);
        goto error;
    }

    /* Resize dict_buffer */
    if (_PyBytes_Resize(&dst_dict_bytes, zstd_ret) < 0) {
        goto error;
    }

    goto success;

error:
    Py_CLEAR(dst_dict_bytes);

success:
    PyMem_Free(chunk_sizes);
    return dst_dict_bytes;
#endif
}
