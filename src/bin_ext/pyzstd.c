#include "pyzstd.h"
#include "dict.c"
#include "compressor.c"
#include "decompressor.c"
#include "file.c"
#include "stream.c"

/* --------------------------
     Module level functions
   -------------------------- */
PyDoc_STRVAR(_get_param_bounds_doc,
"Internal function, get CParameter/DParameter bounds.");

static PyObject *
_get_param_bounds(PyObject *module, PyObject *args)
{
    int is_compress;
    int parameter;

    ZSTD_bounds bound;

    if (!PyArg_ParseTuple(args, "ii:_get_param_bounds", &is_compress, &parameter)) {
        return NULL;
    }

    if (is_compress) {
        bound = ZSTD_cParam_getBounds(parameter);
        if (ZSTD_isError(bound.error)) {
            STATE_FROM_MODULE(module);
            set_zstd_error(MODULE_STATE, ERR_GET_C_BOUNDS, bound.error);
            return NULL;
        }
    } else {
        bound = ZSTD_dParam_getBounds(parameter);
        if (ZSTD_isError(bound.error)) {
            STATE_FROM_MODULE(module);
            set_zstd_error(MODULE_STATE, ERR_GET_D_BOUNDS, bound.error);
            return NULL;
        }
    }

    return Py_BuildValue("ii", bound.lowerBound, bound.upperBound);
}

PyDoc_STRVAR(get_frame_size_doc,
"get_frame_size(frame_buffer)\n"
"----\n"
"Get the size of a zstd frame, including frame header and 4-byte checksum if it\n"
"has.\n\n"
"It will iterate all blocks' header within a frame, to accumulate the frame size.\n\n"
"Parameter\n"
"frame_buffer: A bytes-like object, it should starts from the beginning of a\n"
"              frame, and contains at least one complete frame.");

static PyObject *
get_frame_size(PyObject *module, PyObject *args)
{
    Py_buffer frame_buffer;

    size_t frame_size;
    PyObject *ret;

    if (!PyArg_ParseTuple(args, "y*:get_frame_size", &frame_buffer)) {
        return NULL;
    }

    frame_size = ZSTD_findFrameCompressedSize(frame_buffer.buf, frame_buffer.len);
    if (ZSTD_isError(frame_size)) {
        STATE_FROM_MODULE(module);
        PyErr_Format(MS_MEMBER(ZstdError),
                     "Error when finding the compressed size of a zstd frame. "
                     "Make sure the frame_buffer argument starts from the "
                     "beginning of a frame, and its length not less than this "
                     "complete frame. Zstd error message: %s.",
                     ZSTD_getErrorName(frame_size));
        goto error;
    }

    ret = PyLong_FromSize_t(frame_size);
    if (ret == NULL) {
        goto error;
    }
    goto success;

error:
    ret = NULL;
success:
    PyBuffer_Release(&frame_buffer);
    return ret;
}

PyDoc_STRVAR(_get_frame_info_doc,
"Internal function, get zstd frame infomation from a frame header.");

static PyObject *
_get_frame_info(PyObject *module, PyObject *args)
{
    Py_buffer frame_buffer;

    uint64_t decompressed_size;
    uint32_t dict_id;
    PyObject *ret = NULL;

    if (!PyArg_ParseTuple(args, "y*:_get_frame_info", &frame_buffer)) {
        return NULL;
    }

    /* ZSTD_getFrameContentSize */
    decompressed_size = ZSTD_getFrameContentSize(frame_buffer.buf,
                                                 frame_buffer.len);

    /* #define ZSTD_CONTENTSIZE_UNKNOWN (0ULL - 1)
       #define ZSTD_CONTENTSIZE_ERROR   (0ULL - 2) */
    if (decompressed_size == ZSTD_CONTENTSIZE_ERROR) {
        STATE_FROM_MODULE(module);
        PyErr_SetString(MS_MEMBER(ZstdError),
                        "Error when getting information from the header of "
                        "a zstd frame. Make sure the frame_buffer argument "
                        "starts from the beginning of a frame, and its length "
                        "not less than the frame header (6~18 bytes).");
        goto error;
    }

    /* ZSTD_getDictID_fromFrame */
    dict_id = ZSTD_getDictID_fromFrame(frame_buffer.buf, frame_buffer.len);

    /* Build tuple */
    if (decompressed_size == ZSTD_CONTENTSIZE_UNKNOWN) {
        ret = Py_BuildValue("OI", Py_None, dict_id);
    } else {
        ret = Py_BuildValue("KI", decompressed_size, dict_id);
    }

    if (ret == NULL) {
        goto error;
    }
    goto success;
error:
    Py_CLEAR(ret);
success:
    PyBuffer_Release(&frame_buffer);
    return ret;
}

PyDoc_STRVAR(_set_parameter_types_doc,
"Internal function, set CParameter/DParameter types for validity check.");

static PyObject *
_set_parameter_types(PyObject *module, PyObject *args)
{
    PyObject *c_parameter_type;
    PyObject *d_parameter_type;
    STATE_FROM_MODULE(module);

    if (!PyArg_ParseTuple(args, "OO:_set_parameter_types", &c_parameter_type, &d_parameter_type)) {
        return NULL;
    }

    if (!PyType_Check(c_parameter_type) || !PyType_Check(d_parameter_type)) {
        PyErr_SetString(PyExc_ValueError,
                        "The two arguments should be CParameter and "
                        "DParameter types.");
        return NULL;
    }

    Py_XDECREF(MS_MEMBER(CParameter_type));
    Py_INCREF(c_parameter_type);
    MS_MEMBER(CParameter_type) = (PyTypeObject*)c_parameter_type;

    Py_XDECREF(MS_MEMBER(DParameter_type));
    Py_INCREF(d_parameter_type);
    MS_MEMBER(DParameter_type) = (PyTypeObject*)d_parameter_type;

    Py_RETURN_NONE;
}

static PyMethodDef _zstd_methods[] = {
    {"decompress", (PyCFunction)decompress, METH_VARARGS|METH_KEYWORDS, decompress_doc},
    {"_train_dict", (PyCFunction)_train_dict, METH_VARARGS, _train_dict_doc},
    {"_finalize_dict", (PyCFunction)_finalize_dict, METH_VARARGS, _finalize_dict_doc},
    {"_get_param_bounds", (PyCFunction)_get_param_bounds, METH_VARARGS, _get_param_bounds_doc},
    {"get_frame_size", (PyCFunction)get_frame_size, METH_VARARGS, get_frame_size_doc},
    {"_get_frame_info", (PyCFunction)_get_frame_info, METH_VARARGS, _get_frame_info_doc},
    {"compress_stream", (PyCFunction)compress_stream, METH_VARARGS|METH_KEYWORDS, compress_stream_doc},
    {"decompress_stream", (PyCFunction)decompress_stream, METH_VARARGS|METH_KEYWORDS, decompress_stream_doc},
    {"_set_parameter_types", (PyCFunction)_set_parameter_types, METH_VARARGS, _set_parameter_types_doc},
    {NULL}
};

/* --------------------
     Initialize code
   -------------------- */
#define ADD_INT_PREFIX_MACRO(module, macro)                           \
    do {                                                              \
        if (PyModule_AddIntConstant(module, "_" #macro, macro) < 0) { \
            return -1;                                                \
        }                                                             \
    } while(0)

static int
add_parameters(PyObject *module)
{
    /* If add new parameters, please also add to cp_list/dp_list above. */

    /* Compression parameters */
    ADD_INT_PREFIX_MACRO(module, ZSTD_c_compressionLevel);
    ADD_INT_PREFIX_MACRO(module, ZSTD_c_windowLog);
    ADD_INT_PREFIX_MACRO(module, ZSTD_c_hashLog);
    ADD_INT_PREFIX_MACRO(module, ZSTD_c_chainLog);
    ADD_INT_PREFIX_MACRO(module, ZSTD_c_searchLog);
    ADD_INT_PREFIX_MACRO(module, ZSTD_c_minMatch);
    ADD_INT_PREFIX_MACRO(module, ZSTD_c_targetLength);
    ADD_INT_PREFIX_MACRO(module, ZSTD_c_strategy);

    ADD_INT_PREFIX_MACRO(module, ZSTD_c_enableLongDistanceMatching);
    ADD_INT_PREFIX_MACRO(module, ZSTD_c_ldmHashLog);
    ADD_INT_PREFIX_MACRO(module, ZSTD_c_ldmMinMatch);
    ADD_INT_PREFIX_MACRO(module, ZSTD_c_ldmBucketSizeLog);
    ADD_INT_PREFIX_MACRO(module, ZSTD_c_ldmHashRateLog);

    ADD_INT_PREFIX_MACRO(module, ZSTD_c_contentSizeFlag);
    ADD_INT_PREFIX_MACRO(module, ZSTD_c_checksumFlag);
    ADD_INT_PREFIX_MACRO(module, ZSTD_c_dictIDFlag);

    ADD_INT_PREFIX_MACRO(module, ZSTD_c_nbWorkers);
    ADD_INT_PREFIX_MACRO(module, ZSTD_c_jobSize);
    ADD_INT_PREFIX_MACRO(module, ZSTD_c_overlapLog);

    /* Decompression parameters */
    ADD_INT_PREFIX_MACRO(module, ZSTD_d_windowLogMax);

    /* ZSTD_strategy enum */
    ADD_INT_PREFIX_MACRO(module, ZSTD_fast);
    ADD_INT_PREFIX_MACRO(module, ZSTD_dfast);
    ADD_INT_PREFIX_MACRO(module, ZSTD_greedy);
    ADD_INT_PREFIX_MACRO(module, ZSTD_lazy);
    ADD_INT_PREFIX_MACRO(module, ZSTD_lazy2);
    ADD_INT_PREFIX_MACRO(module, ZSTD_btlazy2);
    ADD_INT_PREFIX_MACRO(module, ZSTD_btopt);
    ADD_INT_PREFIX_MACRO(module, ZSTD_btultra);
    ADD_INT_PREFIX_MACRO(module, ZSTD_btultra2);

    return 0;
}

static inline PyObject *
get_zstd_version_info(void)
{
    const uint32_t ver = ZSTD_versionNumber();
    uint32_t major, minor, release;

    major = ver / 10000;
    minor = (ver / 100) % 100;
    release = ver % 100;

    return Py_BuildValue("III", major, minor, release);
}

static inline int
add_vars_to_module(PyObject *module)
{
    PyObject *obj;

    /* zstd_version, a str. */
    if (PyModule_AddStringConstant(module, "zstd_version",
                                   ZSTD_versionString()) < 0) {
        return -1;
    }

    /* zstd_version_info, a tuple. */
    obj = get_zstd_version_info();
    if (PyModule_AddObject(module, "zstd_version_info", obj) < 0) {
        Py_XDECREF(obj);
        return -1;
    }

    /* Add zstd parameters */
    if (add_parameters(module) < 0) {
        return -1;
    }

    /* _compressionLevel_values: (default, min, max)
       ZSTD_defaultCLevel() was added in zstd v1.5.0 */
    obj = Py_BuildValue("iii",
#if ZSTD_VERSION_NUMBER < 10500
                        ZSTD_CLEVEL_DEFAULT,
#else
                        ZSTD_defaultCLevel(),
#endif
                        ZSTD_minCLevel(),
                        ZSTD_maxCLevel());
    if (PyModule_AddObject(module,
                           "_compressionLevel_values",
                           obj) < 0) {
        Py_XDECREF(obj);
        return -1;
    }

    /* _ZSTD_CStreamSizes */
    obj = Py_BuildValue("II",
                        (uint32_t)ZSTD_CStreamInSize(),
                        (uint32_t)ZSTD_CStreamOutSize());
    if (PyModule_AddObject(module, "_ZSTD_CStreamSizes", obj) < 0) {
        Py_XDECREF(obj);
        return -1;
    }

    /* _ZSTD_DStreamSizes */
    obj = Py_BuildValue("II",
                        (uint32_t)ZSTD_DStreamInSize(),
                        (uint32_t)ZSTD_DStreamOutSize());
    if (PyModule_AddObject(module, "_ZSTD_DStreamSizes", obj) < 0) {
        Py_XDECREF(obj);
        return -1;
    }

    /* PYZSTD_CONFIG */
    obj = Py_BuildValue("isOO", 8*(int)sizeof(Py_ssize_t), "c",
/* Statically link to zstd lib */
#ifdef PYZSTD_STATIC_LINK
                        Py_True,
#else
                        Py_False,
#endif
/* Use multi-phase initialization */
#ifdef USE_MULTI_PHASE_INIT
                        Py_True
#else
                        Py_False
#endif
                        );
    if (PyModule_AddObject(module, "PYZSTD_CONFIG", obj) < 0) {
        Py_XDECREF(obj);
        return -1;
    }

    return 0;
}

static inline int
add_type_to_module(PyObject *module, const char *name,
                   PyType_Spec *type_spec, PyTypeObject **dest)
{
    PyObject *temp;

#ifdef USE_MULTI_PHASE_INIT
    temp = PyType_FromModuleAndSpec(module, type_spec, NULL);
#else
    temp = PyType_FromSpec(type_spec);
#endif

    if (PyModule_AddObject(module, name, temp) < 0) {
        Py_XDECREF(temp);
        return -1;
    }

    Py_INCREF(temp);
    *dest = (PyTypeObject*) temp;

    return 0;
}

static inline int
add_constant_to_type(PyTypeObject *type, const char *name, const long value)
{
    PyObject *temp;

    temp = PyLong_FromLong(value);
    if (temp == NULL) {
        return -1;
    }

    if (PyObject_SetAttrString((PyObject*) type, name, temp) < 0) {
        Py_DECREF(temp);
        return -1;
    }
    Py_DECREF(temp);

    return 0;
}

#define ADD_STR_TO_STATE_MACRO(STR)                        \
    do {                                                   \
        MS_MEMBER(str_##STR) = PyUnicode_FromString(#STR); \
        if (MS_MEMBER(str_##STR) == NULL) {                \
            return -1;                                     \
        }                                                  \
    } while(0)

static int _zstd_exec(PyObject *module) {
    STATE_FROM_MODULE(module);

    /* Reusable objects & variables */
    MS_MEMBER(empty_bytes) = PyBytes_FromStringAndSize(NULL, 0);
    if (MS_MEMBER(empty_bytes) == NULL) {
        return -1;
    }

    MS_MEMBER(empty_readonly_memoryview) =
                PyMemoryView_FromMemory((char*)MODULE_STATE, 0, PyBUF_READ);
    if (MS_MEMBER(empty_readonly_memoryview) == NULL) {
        return -1;
    }

    /* Add str to module state */
    ADD_STR_TO_STATE_MACRO(read);
    ADD_STR_TO_STATE_MACRO(readinto);
    ADD_STR_TO_STATE_MACRO(write);
    ADD_STR_TO_STATE_MACRO(flush);

    MS_MEMBER(CParameter_type) = NULL;
    MS_MEMBER(DParameter_type) = NULL;

    /* Add variables to module */
    if (add_vars_to_module(module) < 0) {
        return -1;
    }

    /* ZstdError */
    MS_MEMBER(ZstdError) = PyErr_NewExceptionWithDoc(
                                  "pyzstd.ZstdError",
                                  "Call to the underlying zstd library failed.",
                                  NULL, NULL);
    if (MS_MEMBER(ZstdError) == NULL) {
        return -1;
    }

    Py_INCREF(MS_MEMBER(ZstdError));
    if (PyModule_AddObject(module, "ZstdError", MS_MEMBER(ZstdError)) < 0) {
        Py_DECREF(MS_MEMBER(ZstdError));
        return -1;
    }

    /* ZstdDict */
    if (add_type_to_module(module,
                           "ZstdDict",
                           &zstddict_type_spec,
                           &MS_MEMBER(ZstdDict_type)) < 0) {
        return -1;
    }

    /* ZstdCompressor */
    if (add_type_to_module(module,
                           "ZstdCompressor",
                           &zstdcompressor_type_spec,
                           &MS_MEMBER(ZstdCompressor_type)) < 0) {
        return -1;
    }

    /* Add EndDirective enum to ZstdCompressor */
    if (add_constant_to_type(MS_MEMBER(ZstdCompressor_type),
                             "CONTINUE",
                             ZSTD_e_continue) < 0) {
        return -1;
    }

    if (add_constant_to_type(MS_MEMBER(ZstdCompressor_type),
                             "FLUSH_BLOCK",
                             ZSTD_e_flush) < 0) {
        return -1;
    }

    if (add_constant_to_type(MS_MEMBER(ZstdCompressor_type),
                             "FLUSH_FRAME",
                             ZSTD_e_end) < 0) {
        return -1;
    }

    /* RichMemZstdCompressor */
    if (add_type_to_module(module,
                           "RichMemZstdCompressor",
                           &richmem_zstdcompressor_type_spec,
                           &MS_MEMBER(RichMemZstdCompressor_type)) < 0) {
        return -1;
    }

    /* ZstdDecompressor */
    if (add_type_to_module(module,
                           "ZstdDecompressor",
                           &ZstdDecompressor_type_spec,
                           &MS_MEMBER(ZstdDecompressor_type)) < 0) {
        return -1;
    }

    /* EndlessZstdDecompressor */
    if (add_type_to_module(module,
                           "EndlessZstdDecompressor",
                           &EndlessZstdDecompressor_type_spec,
                           &MS_MEMBER(EndlessZstdDecompressor_type)) < 0) {
        return -1;
    }

    /* ZstdFileReader */
    if (add_type_to_module(module,
                           "ZstdFileReader",
                           &ZstdFileReader_type_spec,
                           &MS_MEMBER(ZstdFileReader_type)) < 0) {
        return -1;
    }

    /* ZstdFileWriter */
    if (add_type_to_module(module,
                           "ZstdFileWriter",
                           &ZstdFileWriter_type_spec,
                           &MS_MEMBER(ZstdFileWriter_type)) < 0) {
        return -1;
    }

    return 0;
}

static int
_zstd_traverse(PyObject *module, visitproc visit, void *arg)
{
    STATE_FROM_MODULE(module);

    Py_VISIT(MS_MEMBER(empty_bytes));
    Py_VISIT(MS_MEMBER(empty_readonly_memoryview));
    Py_VISIT(MS_MEMBER(str_read));
    Py_VISIT(MS_MEMBER(str_readinto));
    Py_VISIT(MS_MEMBER(str_write));
    Py_VISIT(MS_MEMBER(str_flush));

    Py_VISIT(MS_MEMBER(ZstdDict_type));
    Py_VISIT(MS_MEMBER(ZstdCompressor_type));
    Py_VISIT(MS_MEMBER(RichMemZstdCompressor_type));
    Py_VISIT(MS_MEMBER(ZstdDecompressor_type));
    Py_VISIT(MS_MEMBER(EndlessZstdDecompressor_type));
    Py_VISIT(MS_MEMBER(ZstdFileReader_type));
    Py_VISIT(MS_MEMBER(ZstdFileWriter_type));
    Py_VISIT(MS_MEMBER(ZstdError));

    Py_VISIT(MS_MEMBER(CParameter_type));
    Py_VISIT(MS_MEMBER(DParameter_type));
    return 0;
}

static int
_zstd_clear(PyObject *module)
{
    STATE_FROM_MODULE(module);

    Py_CLEAR(MS_MEMBER(empty_bytes));
    Py_CLEAR(MS_MEMBER(empty_readonly_memoryview));
    Py_CLEAR(MS_MEMBER(str_read));
    Py_CLEAR(MS_MEMBER(str_readinto));
    Py_CLEAR(MS_MEMBER(str_write));
    Py_CLEAR(MS_MEMBER(str_flush));

    Py_CLEAR(MS_MEMBER(ZstdDict_type));
    Py_CLEAR(MS_MEMBER(ZstdCompressor_type));
    Py_CLEAR(MS_MEMBER(RichMemZstdCompressor_type));
    Py_CLEAR(MS_MEMBER(ZstdDecompressor_type));
    Py_CLEAR(MS_MEMBER(EndlessZstdDecompressor_type));
    Py_CLEAR(MS_MEMBER(ZstdFileReader_type));
    Py_CLEAR(MS_MEMBER(ZstdFileWriter_type));
    Py_CLEAR(MS_MEMBER(ZstdError));

    Py_CLEAR(MS_MEMBER(CParameter_type));
    Py_CLEAR(MS_MEMBER(DParameter_type));
    return 0;
}

static void
_zstd_free(void *module)
{
    _zstd_clear((PyObject *)module);
}

#ifdef USE_MULTI_PHASE_INIT
static PyModuleDef_Slot _zstd_slots[] = {
    {Py_mod_exec, _zstd_exec},
    {0, NULL}
};
#endif

static PyModuleDef _zstdmodule = {
    PyModuleDef_HEAD_INIT,
    .m_name = "_zstd",
#ifdef USE_MULTI_PHASE_INIT
    .m_size = sizeof(_zstd_state),
    .m_slots = _zstd_slots,
#else
    .m_size = -1,
#endif
    .m_methods = _zstd_methods,
    .m_traverse = _zstd_traverse,
    .m_clear = _zstd_clear,
    .m_free = _zstd_free
};

#ifdef USE_MULTI_PHASE_INIT
/* For forward declaration of _zstdmodule */
static inline PyModuleDef* _get_zstd_PyModuleDef()
{
    return &_zstdmodule;
}
#endif

PyMODINIT_FUNC
PyInit__zstd(void)
{
#ifdef USE_MULTI_PHASE_INIT
    return PyModuleDef_Init(&_zstdmodule);
#else
    PyObject *module;
    module = PyModule_Create(&_zstdmodule);
    if (module == NULL) {
        return NULL;
    }
    if (_zstd_exec(module) != 0) {
        Py_DECREF(module);
        return NULL;
    }
    return module;
#endif
}
