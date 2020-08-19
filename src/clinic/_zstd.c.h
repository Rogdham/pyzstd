/*[clinic input]
preserve
[clinic start generated code]*/

PyDoc_STRVAR(_zstd_compress__doc__,
"compress($module, /, data)\n"
"--\n"
"\n"
"Returns a bytes object containing compressed data.\n"
"\n"
"  data\n"
"    Binary data to be compressed.");

#define _ZSTD_COMPRESS_METHODDEF    \
    {"compress", (PyCFunction)(void(*)(void))_zstd_compress, METH_FASTCALL|METH_KEYWORDS, _zstd_compress__doc__},

static PyObject *
_zstd_compress_impl(PyObject *module, Py_buffer *data);

static PyObject *
_zstd_compress(PyObject *module, PyObject *const *args, Py_ssize_t nargs, PyObject *kwnames)
{
    PyObject *return_value = NULL;
    static const char * const _keywords[] = {"data", NULL};
    static _PyArg_Parser _parser = {NULL, _keywords, "compress", 0};
    PyObject *argsbuf[1];
    Py_buffer data = {NULL, NULL};

    args = _PyArg_UnpackKeywords(args, nargs, NULL, kwnames, &_parser, 1, 1, 0, argsbuf);
    if (!args) {
        goto exit;
    }
    if (PyObject_GetBuffer(args[0], &data, PyBUF_SIMPLE) != 0) {
        goto exit;
    }
    if (!PyBuffer_IsContiguous(&data, 'C')) {
        _PyArg_BadArgument("compress", "argument 'data'", "contiguous buffer", args[0]);
        goto exit;
    }
    return_value = _zstd_compress_impl(module, &data);

exit:
    /* Cleanup for data */
    if (data.obj) {
       PyBuffer_Release(&data);
    }

    return return_value;
}

PyDoc_STRVAR(_zstd_decompress__doc__,
"decompress($module, /, data)\n"
"--\n"
"\n"
"Returns a bytes object containing the uncompressed data.\n"
"\n"
"  data\n"
"    Compressed data.");

#define _ZSTD_DECOMPRESS_METHODDEF    \
    {"decompress", (PyCFunction)(void(*)(void))_zstd_decompress, METH_FASTCALL|METH_KEYWORDS, _zstd_decompress__doc__},

static PyObject *
_zstd_decompress_impl(PyObject *module, Py_buffer *data);

static PyObject *
_zstd_decompress(PyObject *module, PyObject *const *args, Py_ssize_t nargs, PyObject *kwnames)
{
    PyObject *return_value = NULL;
    static const char * const _keywords[] = {"data", NULL};
    static _PyArg_Parser _parser = {NULL, _keywords, "decompress", 0};
    PyObject *argsbuf[1];
    Py_buffer data = {NULL, NULL};

    args = _PyArg_UnpackKeywords(args, nargs, NULL, kwnames, &_parser, 1, 1, 0, argsbuf);
    if (!args) {
        goto exit;
    }
    if (PyObject_GetBuffer(args[0], &data, PyBUF_SIMPLE) != 0) {
        goto exit;
    }
    if (!PyBuffer_IsContiguous(&data, 'C')) {
        _PyArg_BadArgument("decompress", "argument 'data'", "contiguous buffer", args[0]);
        goto exit;
    }
    return_value = _zstd_decompress_impl(module, &data);

exit:
    /* Cleanup for data */
    if (data.obj) {
       PyBuffer_Release(&data);
    }

    return return_value;
}
/*[clinic end generated code: output=7be63f577129dde1 input=a9049054013a1b77]*/
