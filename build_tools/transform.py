# Automatically transform from CPython code

import re
import os
from shutil import copy

CPYTHON_DIR = r'E:\dev\cpython'
PYZSTD_DIR = os.path.dirname(os.path.dirname(__file__))

py_list = (
    (r'(from )(_zstd import \*)', r'\1.\2'),
    (r'import _zstd', r'from . import _zstd')
)

c_list = (
    (r'#include <zstd\.h>', r'#include "../lib/zstd.h"'),
    (r'#include <zdict\.h>',
     r'#include "../lib/dictBuilder/zdict.h"'),
    (r'(\n#include "clinic/_zstdmodule.c.h")', r'\n#include "pypi1.h"\1'),
    (r'get_zstd_state\(PyObject \*module\)',
     r'get_zstd_state_NOUSE(PyObject *module)'),

    (r"""PyMODINIT_FUNC
PyInit__zstd\(void\)
{
    return PyModuleDef_Init\(&_zstdmodule\);
}""", \
"""#include "pypi2.h"

// PyMODINIT_FUNC
// PyInit__zstd(void)
// {
//     return PyModuleDef_Init(&_zstdmodule);
// }"""),
)

def copy_and_transform(file1, file2, re_list):
    path1 = os.path.join(CPYTHON_DIR, file1)
    path2 = os.path.join(PYZSTD_DIR, file2)
    
    try:
       copy(path1, path2)
    except:
       print("Unable to copy file. %s" % file1)
       raise

    if re_list:
        if os.path.isdir(path2):
            dir_name, file_name = os.path.split(file1)
            path2 = os.path.join(path2, file_name)

        with open(path2, encoding='utf-8') as f:
            text = f.read()
        
        for pattern, repl in re_list:
            text = re.sub(pattern, repl, text)
            
        with open(path2, 'w', encoding='utf-8') as f:
            f.write(text)


copy_and_transform(r'Lib\zstd.py', r'src\pyzstd.py', py_list)
copy_and_transform(r'Modules\_zstdmodule.c', r'src', c_list)
copy_and_transform(r'Modules\clinic\_zstdmodule.c.h', r'src\clinic', [])
