
#include "Python.h"


/* Return a Python int from the object item.
   Can return an instance of int subclass.
   Raise TypeError if the result is not an int
   or if the object cannot be interpreted as an index.
*/
static PyObject *
_PyNumber_Index(PyObject *item)
{
    return PyNumber_Index(item);
}

/* _zstd_state */

static _zstd_state static_state;

static inline _zstd_state *
PyType_GetModuleState(void *arg)
{
    return &static_state;
}

static inline _zstd_state *
get_zstd_state(PyObject *module)
{
    return &static_state;
}


#if PY_VERSION_HEX < 0x03090000
int
PyModule_AddType(PyObject *module, PyTypeObject *type)
{
    return 0;
}

PyObject *
PyType_FromModuleAndSpec(PyObject *module, PyType_Spec *spec, PyObject *bases)
{
    return NULL;
}
#endif