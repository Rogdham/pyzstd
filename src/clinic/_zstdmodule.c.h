/*[clinic input]
preserve
[clinic start generated code]*/

PyDoc_STRVAR(_zstd_ZstdDict___init____doc__,
"ZstdDict(dict_content)\n"
"--\n"
"\n"
"Initialize a ZstdDict object, it can used for compress/decompress.\n"
"\n"
"  dict_content\n"
"    Dictionary\'s content, a bytes-like object.");

static int
_zstd_ZstdDict___init___impl(ZstdDict *self, PyObject *dict_content);

static int
_zstd_ZstdDict___init__(PyObject *self, PyObject *args, PyObject *kwargs)
{
    int return_value = -1;
    static const char * const _keywords[] = {"dict_content", NULL};
    static _PyArg_Parser _parser = {NULL, _keywords, "ZstdDict", 0};
    PyObject *argsbuf[1];
    PyObject * const *fastargs;
    Py_ssize_t nargs = PyTuple_GET_SIZE(args);
    PyObject *dict_content;

    fastargs = _PyArg_UnpackKeywords(_PyTuple_CAST(args)->ob_item, nargs, kwargs, NULL, &_parser, 1, 1, 0, argsbuf);
    if (!fastargs) {
        goto exit;
    }
    dict_content = fastargs[0];
    return_value = _zstd_ZstdDict___init___impl((ZstdDict *)self, dict_content);

exit:
    return return_value;
}

PyDoc_STRVAR(_zstd_ZstdDict___reduce____doc__,
"__reduce__($self, /)\n"
"--\n"
"\n"
"Return state information for pickling.");

#define _ZSTD_ZSTDDICT___REDUCE___METHODDEF    \
    {"__reduce__", (PyCFunction)_zstd_ZstdDict___reduce__, METH_NOARGS, _zstd_ZstdDict___reduce____doc__},

static PyObject *
_zstd_ZstdDict___reduce___impl(ZstdDict *self);

static PyObject *
_zstd_ZstdDict___reduce__(ZstdDict *self, PyObject *Py_UNUSED(ignored))
{
    return _zstd_ZstdDict___reduce___impl(self);
}

PyDoc_STRVAR(_zstd__train_dict__doc__,
"_train_dict($module, /, dst_data, dst_data_sizes, dict_size)\n"
"--\n"
"\n"
"Internal function, train a zstd dictionary.");

#define _ZSTD__TRAIN_DICT_METHODDEF    \
    {"_train_dict", (PyCFunction)(void(*)(void))_zstd__train_dict, METH_FASTCALL|METH_KEYWORDS, _zstd__train_dict__doc__},

static PyObject *
_zstd__train_dict_impl(PyObject *module, PyBytesObject *dst_data,
                       PyObject *dst_data_sizes, Py_ssize_t dict_size);

static PyObject *
_zstd__train_dict(PyObject *module, PyObject *const *args, Py_ssize_t nargs, PyObject *kwnames)
{
    PyObject *return_value = NULL;
    static const char * const _keywords[] = {"dst_data", "dst_data_sizes", "dict_size", NULL};
    static _PyArg_Parser _parser = {NULL, _keywords, "_train_dict", 0};
    PyObject *argsbuf[3];
    PyBytesObject *dst_data;
    PyObject *dst_data_sizes;
    Py_ssize_t dict_size;

    args = _PyArg_UnpackKeywords(args, nargs, NULL, kwnames, &_parser, 3, 3, 0, argsbuf);
    if (!args) {
        goto exit;
    }
    if (!PyBytes_Check(args[0])) {
        _PyArg_BadArgument("_train_dict", "argument 'dst_data'", "bytes", args[0]);
        goto exit;
    }
    dst_data = (PyBytesObject *)args[0];
    dst_data_sizes = args[1];
    {
        Py_ssize_t ival = -1;
        PyObject *iobj = _PyNumber_Index(args[2]);
        if (iobj != NULL) {
            ival = PyLong_AsSsize_t(iobj);
            Py_DECREF(iobj);
        }
        if (ival == -1 && PyErr_Occurred()) {
            goto exit;
        }
        dict_size = ival;
    }
    return_value = _zstd__train_dict_impl(module, dst_data, dst_data_sizes, dict_size);

exit:
    return return_value;
}

PyDoc_STRVAR(_zstd_ZstdCompressor___init____doc__,
"ZstdCompressor(level_or_option=None, zstd_dict=None)\n"
"--\n"
"\n"
"Initialize a ZstdCompressor object.\n"
"\n"
"  level_or_option\n"
"    It can be an int object, in this case represents the compression\n"
"    level. It can also be a dictionary for setting various advanced\n"
"    parameters. The default value None means to use zstd\'s default\n"
"    compression level/parameters.\n"
"  zstd_dict\n"
"    Pre-trained dictionary for compression, a ZstdDict object.");

static int
_zstd_ZstdCompressor___init___impl(ZstdCompressor *self,
                                   PyObject *level_or_option,
                                   PyObject *zstd_dict);

static int
_zstd_ZstdCompressor___init__(PyObject *self, PyObject *args, PyObject *kwargs)
{
    int return_value = -1;
    static const char * const _keywords[] = {"level_or_option", "zstd_dict", NULL};
    static _PyArg_Parser _parser = {NULL, _keywords, "ZstdCompressor", 0};
    PyObject *argsbuf[2];
    PyObject * const *fastargs;
    Py_ssize_t nargs = PyTuple_GET_SIZE(args);
    Py_ssize_t noptargs = nargs + (kwargs ? PyDict_GET_SIZE(kwargs) : 0) - 0;
    PyObject *level_or_option = Py_None;
    PyObject *zstd_dict = Py_None;

    fastargs = _PyArg_UnpackKeywords(_PyTuple_CAST(args)->ob_item, nargs, kwargs, NULL, &_parser, 0, 2, 0, argsbuf);
    if (!fastargs) {
        goto exit;
    }
    if (!noptargs) {
        goto skip_optional_pos;
    }
    if (fastargs[0]) {
        level_or_option = fastargs[0];
        if (!--noptargs) {
            goto skip_optional_pos;
        }
    }
    zstd_dict = fastargs[1];
skip_optional_pos:
    return_value = _zstd_ZstdCompressor___init___impl((ZstdCompressor *)self, level_or_option, zstd_dict);

exit:
    return return_value;
}

PyDoc_STRVAR(_zstd_ZstdCompressor_compress__doc__,
"compress($self, /, data, end_directive=EndDirective.CONTINUE)\n"
"--\n"
"\n"
"Provide data to the compressor object.\n"
"\n"
"  data\n"
"    Data to be compressed, a bytes-like object.\n"
"  end_directive\n"
"    EndDirective.CONTINUE: Collect more data, encoder decides when to output\n"
"    compressed result, for optimal compression ratio. Usually used for ordinary\n"
"    streaming compression.\n"
"    EndDirective.FLUSH: Flush any remaining data, but don\'t end current frame.\n"
"    Usually used for communication, the receiver can decode the data immediately.\n"
"    EndDirective.END: Flush any remaining data _and_ close current frame.\n"
"\n"
"Returns a chunk of compressed data if possible, or b\'\' otherwise.");

#define _ZSTD_ZSTDCOMPRESSOR_COMPRESS_METHODDEF    \
    {"compress", (PyCFunction)(void(*)(void))_zstd_ZstdCompressor_compress, METH_FASTCALL|METH_KEYWORDS, _zstd_ZstdCompressor_compress__doc__},

static PyObject *
_zstd_ZstdCompressor_compress_impl(ZstdCompressor *self, Py_buffer *data,
                                   int end_directive);

static PyObject *
_zstd_ZstdCompressor_compress(ZstdCompressor *self, PyObject *const *args, Py_ssize_t nargs, PyObject *kwnames)
{
    PyObject *return_value = NULL;
    static const char * const _keywords[] = {"data", "end_directive", NULL};
    static _PyArg_Parser _parser = {NULL, _keywords, "compress", 0};
    PyObject *argsbuf[2];
    Py_ssize_t noptargs = nargs + (kwnames ? PyTuple_GET_SIZE(kwnames) : 0) - 1;
    Py_buffer data = {NULL, NULL};
    int end_directive = ZSTD_e_continue;

    args = _PyArg_UnpackKeywords(args, nargs, NULL, kwnames, &_parser, 1, 2, 0, argsbuf);
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
    if (!noptargs) {
        goto skip_optional_pos;
    }
    end_directive = _PyLong_AsInt(args[1]);
    if (end_directive == -1 && PyErr_Occurred()) {
        goto exit;
    }
skip_optional_pos:
    return_value = _zstd_ZstdCompressor_compress_impl(self, &data, end_directive);

exit:
    /* Cleanup for data */
    if (data.obj) {
       PyBuffer_Release(&data);
    }

    return return_value;
}

PyDoc_STRVAR(_zstd_ZstdCompressor_flush__doc__,
"flush($self, /, end_frame=True)\n"
"--\n"
"\n"
"Finish the compression process.\n"
"\n"
"  end_frame\n"
"    True flush data and end the frame.\n"
"    False flush data, don\'t end the frame, usually used for communication,\n"
"    the receiver can decode the data immediately.\n"
"\n"
"Returns the compressed data left in internal buffers.\n"
"\n"
"Since zstd data consists of one or more independent frames, the compressor\n"
"object can be used after this method is called.");

#define _ZSTD_ZSTDCOMPRESSOR_FLUSH_METHODDEF    \
    {"flush", (PyCFunction)(void(*)(void))_zstd_ZstdCompressor_flush, METH_FASTCALL|METH_KEYWORDS, _zstd_ZstdCompressor_flush__doc__},

static PyObject *
_zstd_ZstdCompressor_flush_impl(ZstdCompressor *self, int end_frame);

static PyObject *
_zstd_ZstdCompressor_flush(ZstdCompressor *self, PyObject *const *args, Py_ssize_t nargs, PyObject *kwnames)
{
    PyObject *return_value = NULL;
    static const char * const _keywords[] = {"end_frame", NULL};
    static _PyArg_Parser _parser = {NULL, _keywords, "flush", 0};
    PyObject *argsbuf[1];
    Py_ssize_t noptargs = nargs + (kwnames ? PyTuple_GET_SIZE(kwnames) : 0) - 0;
    int end_frame = 1;

    args = _PyArg_UnpackKeywords(args, nargs, NULL, kwnames, &_parser, 0, 1, 0, argsbuf);
    if (!args) {
        goto exit;
    }
    if (!noptargs) {
        goto skip_optional_pos;
    }
    end_frame = PyObject_IsTrue(args[0]);
    if (end_frame < 0) {
        goto exit;
    }
skip_optional_pos:
    return_value = _zstd_ZstdCompressor_flush_impl(self, end_frame);

exit:
    return return_value;
}

PyDoc_STRVAR(_zstd_ZstdCompressor___reduce____doc__,
"__reduce__($self, /)\n"
"--\n"
"\n");

#define _ZSTD_ZSTDCOMPRESSOR___REDUCE___METHODDEF    \
    {"__reduce__", (PyCFunction)_zstd_ZstdCompressor___reduce__, METH_NOARGS, _zstd_ZstdCompressor___reduce____doc__},

static PyObject *
_zstd_ZstdCompressor___reduce___impl(ZstdCompressor *self);

static PyObject *
_zstd_ZstdCompressor___reduce__(ZstdCompressor *self, PyObject *Py_UNUSED(ignored))
{
    return _zstd_ZstdCompressor___reduce___impl(self);
}

PyDoc_STRVAR(_zstd_ZstdDecompressor___init____doc__,
"ZstdDecompressor(zstd_dict=None, option=None)\n"
"--\n"
"\n"
"Initialize a ZstdDecompressor object.\n"
"\n"
"  zstd_dict\n"
"    Pre-trained dictionary for decompression, a ZstdDict object.\n"
"  option\n"
"    A dictionary for setting advanced parameters. The default\n"
"    value None means to use zstd\'s default decompression parameters.");

static int
_zstd_ZstdDecompressor___init___impl(ZstdDecompressor *self,
                                     PyObject *zstd_dict, PyObject *option);

static int
_zstd_ZstdDecompressor___init__(PyObject *self, PyObject *args, PyObject *kwargs)
{
    int return_value = -1;
    static const char * const _keywords[] = {"zstd_dict", "option", NULL};
    static _PyArg_Parser _parser = {NULL, _keywords, "ZstdDecompressor", 0};
    PyObject *argsbuf[2];
    PyObject * const *fastargs;
    Py_ssize_t nargs = PyTuple_GET_SIZE(args);
    Py_ssize_t noptargs = nargs + (kwargs ? PyDict_GET_SIZE(kwargs) : 0) - 0;
    PyObject *zstd_dict = Py_None;
    PyObject *option = Py_None;

    fastargs = _PyArg_UnpackKeywords(_PyTuple_CAST(args)->ob_item, nargs, kwargs, NULL, &_parser, 0, 2, 0, argsbuf);
    if (!fastargs) {
        goto exit;
    }
    if (!noptargs) {
        goto skip_optional_pos;
    }
    if (fastargs[0]) {
        zstd_dict = fastargs[0];
        if (!--noptargs) {
            goto skip_optional_pos;
        }
    }
    option = fastargs[1];
skip_optional_pos:
    return_value = _zstd_ZstdDecompressor___init___impl((ZstdDecompressor *)self, zstd_dict, option);

exit:
    return return_value;
}

PyDoc_STRVAR(_zstd_ZstdDecompressor_decompress__doc__,
"decompress($self, /, data, max_length=-1)\n"
"--\n"
"\n"
"Decompress *data*, returning uncompressed data as bytes.\n"
"\n"
"  data\n"
"    Data to be decompressed, a bytes-like object.\n"
"  max_length\n"
"    If max_length is nonnegative, returns at most max_length bytes of\n"
"    decompressed data. If this limit is reached and further output can be\n"
"    produced, the needs_input attribute will be set to False. In this case,\n"
"    the next call to decompress() may provide data as b\'\' to obtain more of\n"
"    the output.\n"
"\n"
"If all of the input data was decompressed and returned (either because this\n"
"was less than *max_length *bytes, or because *max_length *was negative),\n"
"*self.needs_input *will be set to True.");

#define _ZSTD_ZSTDDECOMPRESSOR_DECOMPRESS_METHODDEF    \
    {"decompress", (PyCFunction)(void(*)(void))_zstd_ZstdDecompressor_decompress, METH_FASTCALL|METH_KEYWORDS, _zstd_ZstdDecompressor_decompress__doc__},

static PyObject *
_zstd_ZstdDecompressor_decompress_impl(ZstdDecompressor *self,
                                       Py_buffer *data,
                                       Py_ssize_t max_length);

static PyObject *
_zstd_ZstdDecompressor_decompress(ZstdDecompressor *self, PyObject *const *args, Py_ssize_t nargs, PyObject *kwnames)
{
    PyObject *return_value = NULL;
    static const char * const _keywords[] = {"data", "max_length", NULL};
    static _PyArg_Parser _parser = {NULL, _keywords, "decompress", 0};
    PyObject *argsbuf[2];
    Py_ssize_t noptargs = nargs + (kwnames ? PyTuple_GET_SIZE(kwnames) : 0) - 1;
    Py_buffer data = {NULL, NULL};
    Py_ssize_t max_length = -1;

    args = _PyArg_UnpackKeywords(args, nargs, NULL, kwnames, &_parser, 1, 2, 0, argsbuf);
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
    if (!noptargs) {
        goto skip_optional_pos;
    }
    {
        Py_ssize_t ival = -1;
        PyObject *iobj = _PyNumber_Index(args[1]);
        if (iobj != NULL) {
            ival = PyLong_AsSsize_t(iobj);
            Py_DECREF(iobj);
        }
        if (ival == -1 && PyErr_Occurred()) {
            goto exit;
        }
        max_length = ival;
    }
skip_optional_pos:
    return_value = _zstd_ZstdDecompressor_decompress_impl(self, &data, max_length);

exit:
    /* Cleanup for data */
    if (data.obj) {
       PyBuffer_Release(&data);
    }

    return return_value;
}

PyDoc_STRVAR(_zstd_ZstdDecompressor___reduce____doc__,
"__reduce__($self, /)\n"
"--\n"
"\n");

#define _ZSTD_ZSTDDECOMPRESSOR___REDUCE___METHODDEF    \
    {"__reduce__", (PyCFunction)_zstd_ZstdDecompressor___reduce__, METH_NOARGS, _zstd_ZstdDecompressor___reduce____doc__},

static PyObject *
_zstd_ZstdDecompressor___reduce___impl(ZstdDecompressor *self);

static PyObject *
_zstd_ZstdDecompressor___reduce__(ZstdDecompressor *self, PyObject *Py_UNUSED(ignored))
{
    return _zstd_ZstdDecompressor___reduce___impl(self);
}

PyDoc_STRVAR(_zstd__get_cparam_bounds__doc__,
"_get_cparam_bounds($module, /, cParam)\n"
"--\n"
"\n"
"Internal funciton, get cParameter bounds.");

#define _ZSTD__GET_CPARAM_BOUNDS_METHODDEF    \
    {"_get_cparam_bounds", (PyCFunction)(void(*)(void))_zstd__get_cparam_bounds, METH_FASTCALL|METH_KEYWORDS, _zstd__get_cparam_bounds__doc__},

static PyObject *
_zstd__get_cparam_bounds_impl(PyObject *module, int cParam);

static PyObject *
_zstd__get_cparam_bounds(PyObject *module, PyObject *const *args, Py_ssize_t nargs, PyObject *kwnames)
{
    PyObject *return_value = NULL;
    static const char * const _keywords[] = {"cParam", NULL};
    static _PyArg_Parser _parser = {NULL, _keywords, "_get_cparam_bounds", 0};
    PyObject *argsbuf[1];
    int cParam;

    args = _PyArg_UnpackKeywords(args, nargs, NULL, kwnames, &_parser, 1, 1, 0, argsbuf);
    if (!args) {
        goto exit;
    }
    cParam = _PyLong_AsInt(args[0]);
    if (cParam == -1 && PyErr_Occurred()) {
        goto exit;
    }
    return_value = _zstd__get_cparam_bounds_impl(module, cParam);

exit:
    return return_value;
}

PyDoc_STRVAR(_zstd__get_dparam_bounds__doc__,
"_get_dparam_bounds($module, /, dParam)\n"
"--\n"
"\n"
"Internal funciton, get dParameter bounds.");

#define _ZSTD__GET_DPARAM_BOUNDS_METHODDEF    \
    {"_get_dparam_bounds", (PyCFunction)(void(*)(void))_zstd__get_dparam_bounds, METH_FASTCALL|METH_KEYWORDS, _zstd__get_dparam_bounds__doc__},

static PyObject *
_zstd__get_dparam_bounds_impl(PyObject *module, int dParam);

static PyObject *
_zstd__get_dparam_bounds(PyObject *module, PyObject *const *args, Py_ssize_t nargs, PyObject *kwnames)
{
    PyObject *return_value = NULL;
    static const char * const _keywords[] = {"dParam", NULL};
    static _PyArg_Parser _parser = {NULL, _keywords, "_get_dparam_bounds", 0};
    PyObject *argsbuf[1];
    int dParam;

    args = _PyArg_UnpackKeywords(args, nargs, NULL, kwnames, &_parser, 1, 1, 0, argsbuf);
    if (!args) {
        goto exit;
    }
    dParam = _PyLong_AsInt(args[0]);
    if (dParam == -1 && PyErr_Occurred()) {
        goto exit;
    }
    return_value = _zstd__get_dparam_bounds_impl(module, dParam);

exit:
    return return_value;
}

PyDoc_STRVAR(_zstd_get_frame_info__doc__,
"get_frame_info($module, /, frame_buffer)\n"
"--\n"
"\n"
"Get zstd frame infomation from a frame header.\n"
"\n"
"  frame_buffer\n"
"    A bytes-like object. It should starts from the beginning of a frame, and\n"
"    needs to include at least the frame header (2 to 14 bytes).\n"
"\n"
"Return a two-items tuple: (decompressed_size, dictinary_id). If decompressed\n"
"size is unknown (generated by stream compression), it will be None. If no\n"
"dictionary, dictinary_id will be 0.");

#define _ZSTD_GET_FRAME_INFO_METHODDEF    \
    {"get_frame_info", (PyCFunction)(void(*)(void))_zstd_get_frame_info, METH_FASTCALL|METH_KEYWORDS, _zstd_get_frame_info__doc__},

static PyObject *
_zstd_get_frame_info_impl(PyObject *module, Py_buffer *frame_buffer);

static PyObject *
_zstd_get_frame_info(PyObject *module, PyObject *const *args, Py_ssize_t nargs, PyObject *kwnames)
{
    PyObject *return_value = NULL;
    static const char * const _keywords[] = {"frame_buffer", NULL};
    static _PyArg_Parser _parser = {NULL, _keywords, "get_frame_info", 0};
    PyObject *argsbuf[1];
    Py_buffer frame_buffer = {NULL, NULL};

    args = _PyArg_UnpackKeywords(args, nargs, NULL, kwnames, &_parser, 1, 1, 0, argsbuf);
    if (!args) {
        goto exit;
    }
    if (PyObject_GetBuffer(args[0], &frame_buffer, PyBUF_SIMPLE) != 0) {
        goto exit;
    }
    if (!PyBuffer_IsContiguous(&frame_buffer, 'C')) {
        _PyArg_BadArgument("get_frame_info", "argument 'frame_buffer'", "contiguous buffer", args[0]);
        goto exit;
    }
    return_value = _zstd_get_frame_info_impl(module, &frame_buffer);

exit:
    /* Cleanup for frame_buffer */
    if (frame_buffer.obj) {
       PyBuffer_Release(&frame_buffer);
    }

    return return_value;
}

PyDoc_STRVAR(_zstd_get_frame_size__doc__,
"get_frame_size($module, /, frame_buffer)\n"
"--\n"
"\n"
"Get the size of a zstd frame.\n"
"\n"
"  frame_buffer\n"
"    A bytes-like object. It should starts from the beginning of a frame,\n"
"    and needs to contain at least one complete frame.\n"
"\n"
"It will iterate all blocks\' header within a frame, to get the size of the\n"
"frame.");

#define _ZSTD_GET_FRAME_SIZE_METHODDEF    \
    {"get_frame_size", (PyCFunction)(void(*)(void))_zstd_get_frame_size, METH_FASTCALL|METH_KEYWORDS, _zstd_get_frame_size__doc__},

static PyObject *
_zstd_get_frame_size_impl(PyObject *module, Py_buffer *frame_buffer);

static PyObject *
_zstd_get_frame_size(PyObject *module, PyObject *const *args, Py_ssize_t nargs, PyObject *kwnames)
{
    PyObject *return_value = NULL;
    static const char * const _keywords[] = {"frame_buffer", NULL};
    static _PyArg_Parser _parser = {NULL, _keywords, "get_frame_size", 0};
    PyObject *argsbuf[1];
    Py_buffer frame_buffer = {NULL, NULL};

    args = _PyArg_UnpackKeywords(args, nargs, NULL, kwnames, &_parser, 1, 1, 0, argsbuf);
    if (!args) {
        goto exit;
    }
    if (PyObject_GetBuffer(args[0], &frame_buffer, PyBUF_SIMPLE) != 0) {
        goto exit;
    }
    if (!PyBuffer_IsContiguous(&frame_buffer, 'C')) {
        _PyArg_BadArgument("get_frame_size", "argument 'frame_buffer'", "contiguous buffer", args[0]);
        goto exit;
    }
    return_value = _zstd_get_frame_size_impl(module, &frame_buffer);

exit:
    /* Cleanup for frame_buffer */
    if (frame_buffer.obj) {
       PyBuffer_Release(&frame_buffer);
    }

    return return_value;
}
/*[clinic end generated code: output=b00033ade3952e4c input=a9049054013a1b77]*/
