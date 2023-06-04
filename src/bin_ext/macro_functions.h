#include "pyzstd.h"

/*  Generate functions using macros
    PYZSTD_C_CLASS: compressor class struct
    PYZSTD_D_CLASS: decompressor class struct
    PYZSTD_FUN_PREFIX: add prefix to function names */

/* Set compressLevel or compression parameters to compression context */
static int
PYZSTD_FUN_PREFIX(set_c_parameters)(PYZSTD_C_CLASS *self, PyObject *level_or_option)
{
    size_t zstd_ret;
    STATE_FROM_OBJ(self);

    /* Integer compression level */
    if (PyLong_Check(level_or_option)) {
        const int level = _PyLong_AsInt(level_or_option);
        if (level == -1 && PyErr_Occurred()) {
            PyErr_SetString(PyExc_ValueError,
                            "Compression level should be 32-bit signed int value.");
            return -1;
        }

        /* Save for generating ZSTD_CDICT */
        self->compression_level = level;

        /* Set compressionLevel to compression context */
        zstd_ret = ZSTD_CCtx_setParameter(self->cctx,
                                          ZSTD_c_compressionLevel,
                                          level);

        /* Check error */
        if (ZSTD_isError(zstd_ret)) {
            set_zstd_error(MODULE_STATE, ERR_SET_C_LEVEL, zstd_ret);
            return -1;
        }
        return 0;
    }

    /* Options dict */
    if (PyDict_Check(level_or_option)) {
        PyObject *key, *value;
        Py_ssize_t pos = 0;

        while (PyDict_Next(level_or_option, &pos, &key, &value)) {
            /* Check key type */
            if (Py_TYPE(key) == MS_MEMBER(DParameter_type)) {
                PyErr_SetString(PyExc_TypeError,
                                "Key of compression option dict should "
                                "NOT be DParameter.");
                return -1;
            }

            /* Both key & value should be 32-bit signed int */
            const int key_v = _PyLong_AsInt(key);
            if (key_v == -1 && PyErr_Occurred()) {
                PyErr_SetString(PyExc_ValueError,
                                "Key of option dict should be 32-bit signed int value.");
                return -1;
            }

            const int value_v = _PyLong_AsInt(value);
            if (value_v == -1 && PyErr_Occurred()) {
                PyErr_SetString(PyExc_ValueError,
                                "Value of option dict should be 32-bit signed int value.");
                return -1;
            }

            if (key_v == ZSTD_c_compressionLevel) {
                /* Save for generating ZSTD_CDICT */
                self->compression_level = value_v;
            } else if (key_v == ZSTD_c_nbWorkers) {
                /* From zstd library doc:
                   1. When nbWorkers >= 1, triggers asynchronous mode when
                      used with ZSTD_compressStream2().
                   2, Default value is `0`, aka "single-threaded mode" : no
                      worker is spawned, compression is performed inside
                      caller's thread, all invocations are blocking. */
                if (value_v != 0) {
                    self->use_multithread = 1;
                }
            }

            /* Set parameter to compression context */
            zstd_ret = ZSTD_CCtx_setParameter(self->cctx, key_v, value_v);
            if (ZSTD_isError(zstd_ret)) {
                set_parameter_error(MODULE_STATE, 1, key_v, value_v);
                return -1;
            }
        }
        return 0;
    }

    /* Wrong type */
    PyErr_SetString(PyExc_TypeError, "level_or_option argument wrong type.");
    return -1;
}

/* Load dictionary or prefix to compression context */
static int
PYZSTD_FUN_PREFIX(load_c_dict)(PYZSTD_C_CLASS *self, PyObject *dict)
{
    size_t zstd_ret;
    STATE_FROM_OBJ(self);
    ZstdDict *zd;
    int type, ret;

    /* Check ZstdDict */
    ret = PyObject_IsInstance(dict, (PyObject*)MS_MEMBER(ZstdDict_type));
    if (ret < 0) {
        return -1;
    } else if (ret > 0) {
        /* When compressing, use undigested dictionary by default. */
        zd = (ZstdDict*)dict;
        type = DICT_TYPE_UNDIGESTED;
        goto load;
    }

    /* Check (ZstdDict, type) */
    if (PyTuple_CheckExact(dict) && PyTuple_GET_SIZE(dict) == 2) {
        /* Check ZstdDict */
        ret = PyObject_IsInstance(PyTuple_GET_ITEM(dict, 0),
                                  (PyObject*)MS_MEMBER(ZstdDict_type));
        if (ret < 0) {
            return -1;
        } else if (ret > 0) {
            /* type == -1 may indicate an error. */
            type = _PyLong_AsInt(PyTuple_GET_ITEM(dict, 1));
            if (type == DICT_TYPE_DIGESTED ||
                type == DICT_TYPE_UNDIGESTED ||
                type == DICT_TYPE_PREFIX)
            {
                assert(type >= 0);
                zd = (ZstdDict*)PyTuple_GET_ITEM(dict, 0);
                goto load;
            }
        }
    }

    /* Wrong type */
    PyErr_SetString(PyExc_TypeError,
                    "zstd_dict argument should be ZstdDict object.");
    return -1;

load:
    if (type == DICT_TYPE_DIGESTED) {
        /* Get ZSTD_CDict */
        ZSTD_CDict *c_dict = _get_CDict(zd, self->compression_level);
        if (c_dict == NULL) {
            return -1;
        }
        /* Reference a prepared dictionary.
           It overrides some compression context's parameters. */
        zstd_ret = ZSTD_CCtx_refCDict(self->cctx, c_dict);
    } else if (type == DICT_TYPE_UNDIGESTED) {
        /* Load a dictionary.
           It doesn't override compression context's parameters. */
        zstd_ret = ZSTD_CCtx_loadDictionary(
                            self->cctx,
                            PyBytes_AS_STRING(zd->dict_content),
                            Py_SIZE(zd->dict_content));
    } else if (type == DICT_TYPE_PREFIX) {
        /* Load a prefix */
        zstd_ret = ZSTD_CCtx_refPrefix(
                            self->cctx,
                            PyBytes_AS_STRING(zd->dict_content),
                            Py_SIZE(zd->dict_content));
    } else {
        /* Impossible code path */
        PyErr_SetString(PyExc_SystemError,
                        "load_c_dict() impossible code path");
        return -1;
    }

    /* Check error */
    if (ZSTD_isError(zstd_ret)) {
        set_zstd_error(MODULE_STATE, ERR_LOAD_C_DICT, zstd_ret);
        return -1;
    }
    return 0;
}

/* Set decompression parameters to decompression context */
static int
PYZSTD_FUN_PREFIX(set_d_parameters)(PYZSTD_D_CLASS *self, PyObject *option)
{
    size_t zstd_ret;
    PyObject *key, *value;
    Py_ssize_t pos;
    STATE_FROM_OBJ(self);

    if (!PyDict_Check(option)) {
        PyErr_SetString(PyExc_TypeError,
                        "option argument should be dict object.");
        return -1;
    }

    pos = 0;
    while (PyDict_Next(option, &pos, &key, &value)) {
        /* Check key type */
        if (Py_TYPE(key) == MS_MEMBER(CParameter_type)) {
            PyErr_SetString(PyExc_TypeError,
                            "Key of decompression option dict should "
                            "NOT be CParameter.");
            return -1;
        }

        /* Both key & value should be 32-bit signed int */
        const int key_v = _PyLong_AsInt(key);
        if (key_v == -1 && PyErr_Occurred()) {
            PyErr_SetString(PyExc_ValueError,
                            "Key of option dict should be 32-bit signed integer value.");
            return -1;
        }

        const int value_v = _PyLong_AsInt(value);
        if (value_v == -1 && PyErr_Occurred()) {
            PyErr_SetString(PyExc_ValueError,
                            "Value of option dict should be 32-bit signed integer value.");
            return -1;
        }

        /* Set parameter to compression context */
        zstd_ret = ZSTD_DCtx_setParameter(self->dctx, key_v, value_v);

        /* Check error */
        if (ZSTD_isError(zstd_ret)) {
            set_parameter_error(MODULE_STATE, 0, key_v, value_v);
            return -1;
        }
    }
    return 0;
}

/* Load dictionary or prefix to decompression context */
static int
PYZSTD_FUN_PREFIX(load_d_dict)(PYZSTD_D_CLASS *self, PyObject *dict)
{
    size_t zstd_ret;
    STATE_FROM_OBJ(self);
    ZstdDict *zd;
    int type, ret;

    /* Check ZstdDict */
    ret = PyObject_IsInstance(dict, (PyObject*)MS_MEMBER(ZstdDict_type));
    if (ret < 0) {
        return -1;
    } else if (ret > 0) {
        /* When decompressing, use digested dictionary by default. */
        zd = (ZstdDict*)dict;
        type = DICT_TYPE_DIGESTED;
        goto load;
    }

    /* Check (ZstdDict, type) */
    if (PyTuple_CheckExact(dict) && PyTuple_GET_SIZE(dict) == 2) {
        /* Check ZstdDict */
        ret = PyObject_IsInstance(PyTuple_GET_ITEM(dict, 0),
                                  (PyObject*)MS_MEMBER(ZstdDict_type));
        if (ret < 0) {
            return -1;
        } else if (ret > 0) {
            /* type == -1 may indicate an error. */
            type = _PyLong_AsInt(PyTuple_GET_ITEM(dict, 1));
            if (type == DICT_TYPE_DIGESTED ||
                type == DICT_TYPE_UNDIGESTED ||
                type == DICT_TYPE_PREFIX)
            {
                assert(type >= 0);
                zd = (ZstdDict*)PyTuple_GET_ITEM(dict, 0);
                goto load;
            }
        }
    }

    /* Wrong type */
    PyErr_SetString(PyExc_TypeError,
                    "zstd_dict argument should be ZstdDict object.");
    return -1;

load:
    if (type == DICT_TYPE_DIGESTED) {
        /* Get ZSTD_DDict */
        ZSTD_DDict *d_dict = _get_DDict(zd);
        if (d_dict == NULL) {
            return -1;
        }
        /* Reference a prepared dictionary */
        zstd_ret = ZSTD_DCtx_refDDict(self->dctx, d_dict);
    } else if (type == DICT_TYPE_UNDIGESTED) {
        /* Load a dictionary */
        zstd_ret = ZSTD_DCtx_loadDictionary(
                            self->dctx,
                            PyBytes_AS_STRING(zd->dict_content),
                            Py_SIZE(zd->dict_content));
    } else if (type == DICT_TYPE_PREFIX) {
        /* Load a prefix */
        zstd_ret = ZSTD_DCtx_refPrefix(
                            self->dctx,
                            PyBytes_AS_STRING(zd->dict_content),
                            Py_SIZE(zd->dict_content));
    } else {
        /* Impossible code path */
        PyErr_SetString(PyExc_SystemError,
                        "load_d_dict() impossible code path");
        return -1;
    }

    /* Check error */
    if (ZSTD_isError(zstd_ret)) {
        set_zstd_error(MODULE_STATE, ERR_LOAD_D_DICT, zstd_ret);
        return -1;
    }
    return 0;
}
