
#include "Python.h"


static PyTypeObject ZstdDict_T = {
    PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name = "_zstd.ZstdDict",
    .tp_basicsize = sizeof(ZstdDict),
    .tp_itemsize = 0,
    .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE,
    .tp_new = _ZstdDict_new,
    .tp_dealloc = (destructor) _ZstdDict_dealloc,
    .tp_init = (initproc) _zstd_ZstdDict___init__,
    .tp_members = _ZstdDict_members,
    .tp_methods = _ZstdDict_methods,
    .tp_doc = (char*)_ZstdDict_dict_doc,
};

static PyTypeObject ZstdCompressor_T = {
    PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name = "_zstd.ZstdCompressor",
    .tp_basicsize = sizeof(ZstdCompressor),
    .tp_itemsize = 0,
    .tp_flags = Py_TPFLAGS_DEFAULT,
    .tp_new = _ZstdCompressor_new,
    .tp_dealloc = (destructor) _ZstdCompressor_dealloc,
    .tp_init = (initproc) _zstd_ZstdCompressor___init__,
    .tp_members = _ZstdCompressor_members,
    .tp_methods = _ZstdCompressor_methods,
};

static PyTypeObject ZstdDecompressor_T = {
    PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name = "_zstd.ZstdDecompressor",
    .tp_basicsize = sizeof(ZstdDecompressor),
    .tp_itemsize = 0,
    .tp_flags = Py_TPFLAGS_DEFAULT,
    .tp_new = _ZstdDecompressor_new,
    .tp_dealloc = (destructor) _ZstdDecompressor_dealloc,
    .tp_init = (initproc) _zstd_ZstdDecompressor___init__,
    .tp_members = _ZstdDecompressor_members,
    .tp_methods = _ZstdDecompressor_methods,
};

static int
add_constants(PyObject *module)
{
    /* Add zstd parameters */
    if (add_parameters(module) < 0) {
        return -1;
    }

    /* ZSTD_strategy enum */
    ADD_INT_MACRO(module, ZSTD_fast);
    ADD_INT_MACRO(module, ZSTD_dfast);
    ADD_INT_MACRO(module, ZSTD_greedy);
    ADD_INT_MACRO(module, ZSTD_lazy);
    ADD_INT_MACRO(module, ZSTD_lazy2);
    ADD_INT_MACRO(module, ZSTD_btlazy2);
    ADD_INT_MACRO(module, ZSTD_btopt);
    ADD_INT_MACRO(module, ZSTD_btultra);
    ADD_INT_MACRO(module, ZSTD_btultra2);

    /* EndDirective enum */
    ADD_INT_MACRO(module, ZSTD_e_continue);
    ADD_INT_MACRO(module, ZSTD_e_flush);
    ADD_INT_MACRO(module, ZSTD_e_end);
    
    return 0;
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
    if (PyType_Ready(&ZstdDict_T) < 0) {
        goto error;
    }

    Py_INCREF(&ZstdDict_T);
    if (PyModule_AddObject(module, "ZstdDict", (PyObject*)&ZstdDict_T) < 0) {
        Py_DECREF(&ZstdDict_T);
        goto error;
    }
    
    static_state.ZstdDict_type = &ZstdDict_T;
    
    /* ZstdCompressor */
    if (PyType_Ready(&ZstdCompressor_T) < 0) {
        goto error;
    }

    Py_INCREF(&ZstdCompressor_T);
    if (PyModule_AddObject(module, "ZstdCompressor", (PyObject*)&ZstdCompressor_T) < 0) {
        Py_DECREF(&ZstdCompressor_T);
        goto error;
    }
    
    static_state.ZstdCompressor_type = &ZstdCompressor_T;

    /* ZstdDecompressor */
    if (PyType_Ready(&ZstdDecompressor_T) < 0) {
        goto error;
    }

    Py_INCREF(&ZstdDecompressor_T);
    if (PyModule_AddObject(module, "ZstdDecompressor", (PyObject*)&ZstdDecompressor_T) < 0) {
        Py_DECREF(&ZstdDecompressor_T);
        goto error;
    }
    
    static_state.ZstdDecompressor_type = &ZstdDecompressor_T;

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

    /* compress_level_bounds */
    if (!(temp = PyTuple_New(2))) {
        goto error;
    }
    PyTuple_SET_ITEM(temp, 0, PyLong_FromLong(ZSTD_minCLevel()));
    PyTuple_SET_ITEM(temp, 1, PyLong_FromLong(ZSTD_maxCLevel()));
    if (PyModule_AddObject(module, "compress_level_bounds", temp) < 0) {
        Py_DECREF(temp);
        goto error;
    }
    return module;

error:
    return NULL;
}