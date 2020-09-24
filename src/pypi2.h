
#include "Python.h"

static inline int
add_constants(PyObject *module)
{
    PyObject *temp;

    /* Add zstd parameters */
    if (add_parameters(module) < 0) {
        return -1;
    }

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

    /* compressionLevel values */
    temp = PyLong_FromLong(ZSTD_CLEVEL_DEFAULT);
    if (temp == NULL) {
        goto error;
    }
    if (PyModule_AddObject(module, "_ZSTD_CLEVEL_DEFAULT", temp) < 0) {
        Py_DECREF(temp);
        goto error;
    }

    temp = PyLong_FromLong(ZSTD_minCLevel());
    if (temp == NULL) {
        goto error;
    }
    if (PyModule_AddObject(module, "_ZSTD_minCLevel", temp) < 0) {
        Py_DECREF(temp);
        goto error;
    }

    temp = PyLong_FromLong(ZSTD_maxCLevel());
    if (temp == NULL) {
        goto error;
    }
    if (PyModule_AddObject(module, "_ZSTD_maxCLevel", temp) < 0) {
        Py_DECREF(temp);
        goto error;
    }

    return 0;
error:
    return -1;
}

static PyModuleDef _zstdmodule2 = {
    PyModuleDef_HEAD_INIT,
    .m_name = "_zstd",
    .m_size = -1,
    .m_methods = _zstd_methods,
};

PyMODINIT_FUNC
PyInit__zstd(void)
{
    PyObject *module;
    PyObject *temp;
    _zstd_state *state;

    module = PyModule_Create(&_zstdmodule2);
    if (!module) {
        goto error;
    }
    state = get_zstd_state(module);

    /* Constants */
    if (add_constants(module) < 0) {
        goto error;
    }

    /* ZstdError */
    state->ZstdError = PyErr_NewExceptionWithDoc("_zstd.ZstdError", "Call to zstd failed.", NULL, NULL);
    if (state->ZstdError == NULL) {
        goto error;
    }

    Py_INCREF(state->ZstdError);
    if (PyModule_AddObject(module, "ZstdError", state->ZstdError) < 0) {
        Py_DECREF(state->ZstdError);
        goto error;
    }

    /* ZstdDict */
    temp = PyType_FromSpec(&zstddict_type_spec);
    if (temp == NULL) {
        goto error;
    }

    if (PyModule_AddObject(module, "ZstdDict", temp) < 0) {
        Py_DECREF(temp);
        goto error;
    }
    static_state.ZstdDict_type = (PyTypeObject*) temp;

    /* ZstdCompressor */
    temp = PyType_FromSpec(&zstdcompressor_type_spec);
    if (temp == NULL) {
        goto error;
    }

    if (PyModule_AddObject(module, "ZstdCompressor", temp) < 0) {
        Py_DECREF(temp);
        goto error;
    }
    static_state.ZstdCompressor_type = (PyTypeObject*) temp;

    /* Add EndDirective enum to ZstdCompressor */
    temp = PyLong_FromLong(ZSTD_e_continue);
    if (PyObject_SetAttrString((PyObject*)static_state.ZstdCompressor_type,
                               "CONTINUE", temp) < 0) {
        Py_DECREF(temp);
        goto error;
    }
    Py_DECREF(temp);

    temp = PyLong_FromLong(ZSTD_e_flush);
    if (PyObject_SetAttrString((PyObject*)static_state.ZstdCompressor_type,
                               "FLUSH_BLOCK", temp) < 0) {
        Py_DECREF(temp);
        goto error;
    }
    Py_DECREF(temp);

    temp = PyLong_FromLong(ZSTD_e_end);
    if (PyObject_SetAttrString((PyObject*)static_state.ZstdCompressor_type,
                               "FLUSH_FRAME", temp) < 0) {
        Py_DECREF(temp);
        goto error;
    }
    Py_DECREF(temp);

    /* RichMemZstdCompressor */
    temp = PyType_FromSpec(&richmem_zstdcompressor_type_spec);
    if (temp == NULL) {
        goto error;
    }

    if (PyModule_AddObject(module, "RichMemZstdCompressor", temp) < 0) {
        Py_DECREF(temp);
        goto error;
    }
    static_state.RichMemZstdCompressor_type = (PyTypeObject*) temp;

    /* ZstdDecompressor */
    temp = PyType_FromSpec(&zstddecompressor_type_spec);
    if (temp == NULL) {
        goto error;
    }

    if (PyModule_AddObject(module, "ZstdDecompressor", temp) < 0) {
        Py_DECREF(temp);
        goto error;
    }
    static_state.ZstdDecompressor_type = (PyTypeObject*) temp;

    /* zstd_version, ZSTD_versionString() requires zstd v1.3.0+ */
    if (!(temp = PyUnicode_FromString(ZSTD_versionString()))) {
        goto error;
    }
    if (PyModule_AddObject(module, "zstd_version", temp) < 0) {
        Py_DECREF(temp);
        goto error;
    }

    /* zstd_version_info */
    if (!(temp = PyTuple_New(3))) {
        goto error;
    }
    PyTuple_SET_ITEM(temp, 0, PyLong_FromLong(ZSTD_VERSION_MAJOR));
    PyTuple_SET_ITEM(temp, 1, PyLong_FromLong(ZSTD_VERSION_MINOR));
    PyTuple_SET_ITEM(temp, 2, PyLong_FromLong(ZSTD_VERSION_RELEASE));
    if (PyModule_AddObject(module, "zstd_version_info", temp) < 0) {
        Py_DECREF(temp);
        goto error;
    }

    return module;
error:
    return NULL;
}