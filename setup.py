﻿#!/usr/bin/env python3
import fnmatch
import io
import os
import platform
import re
import sys
from setuptools import setup, Extension
from setuptools.command.build_ext import build_ext

# -------- read stuff --------
ROOT_PATH = os.path.dirname(os.path.realpath(__file__))

# read README.rst
README_PATH = os.path.join(ROOT_PATH, 'README.rst')
with io.open(README_PATH, 'r', encoding='utf-8') as file:
    long_description = file.read()

# read module version
INIT_PATH = os.path.join(ROOT_PATH, 'src', '__init__.py')
with io.open(INIT_PATH, 'r', encoding='utf-8') as file:
    file_content = file.read()
    m = re.search(r'''__version__\s*=\s*(['"])(.*?)\1''', file_content)
    module_version = m.group(2)

# -------- binary extension --------
def get_zstd_files_list():
    # Currently Linux/macOS can use assembly implementation
    if sys.platform != 'win32':
        ZSTD_FILE_EXTENSION = '*.[cCsS]'
    else:
        ZSTD_FILE_EXTENSION = '*.[cC]'

    lst = []
    for sub_dir in ('common', 'compress', 'decompress', 'dictBuilder'):
        directory = 'lib/' + sub_dir + '/'
        l = [directory + fn
               for fn in os.listdir(directory)
               if fnmatch.fnmatch(fn, ZSTD_FILE_EXTENSION)]
        lst.extend(l)
    return lst

def has_option(option):
    if option in sys.argv:
        sys.argv = [s for s in sys.argv if s != option]
        return True
    else:
        return False

# setup.py options
AVX2 = has_option('--avx2')
WARNING_AS_ERROR = has_option('--warning-as-error')
DYNAMIC_LINK = has_option('--dynamic-link-zstd')
CFFI = has_option('--cffi') or \
       platform.python_implementation() == 'PyPy'

if DYNAMIC_LINK:
    kwargs = {
        'include_dirs': [],    # .h directory
        'library_dirs': [],    # .lib directory
        'libraries': ['zstd'], # lib name, not filename, for the linker.
        'sources': [],
        'define_macros': []
    }
else:  # statically link to zstd lib
    kwargs = {
        'include_dirs': ['lib', 'lib/dictBuilder'],
        'library_dirs': [],
        'libraries': [],
        'sources': get_zstd_files_list(),
        'define_macros': [('ZSTD_MULTITHREAD', None)]
    }

if CFFI:
    # packages
    packages = ['pyzstd', 'pyzstd.cffi']

    # binary extension
    kwargs['module_name'] = 'pyzstd.cffi._cffi_zstd'

    sys.path.append('src/bin_ext')
    import build_cffi
    binary_extension = build_cffi.get_extension(**kwargs)
else:  # C implementation
    # packages
    packages = ['pyzstd', 'pyzstd.c']

    # binary extension
    kwargs['name'] = 'pyzstd.c._zstd'
    kwargs['sources'].append('src/bin_ext/_zstdmodule.c')

    binary_extension = Extension(**kwargs)

class build_ext_compiler_check(build_ext):
    def build_extensions(self):
        # Accept assembly files
        self.compiler.src_extensions.extend(['.s', '.S'])

        for extension in self.extensions:
            if self.compiler.compiler_type.lower() in ('unix', 'mingw32'):
                if AVX2:
                    instrs = ['-mavx2', '-mbmi', '-mbmi2', '-mlzcnt']
                    extension.extra_compile_args.extend(instrs)
                if WARNING_AS_ERROR:
                    extension.extra_compile_args.append('-Werror')
            elif self.compiler.compiler_type.lower() == 'msvc':
                # /Ob3 is more aggressive inlining than /Ob2
                # /GF eliminates duplicate strings
                # /Gy does function level linking
                more_options = ['/Ob3', '/GF', '/Gy']
                if AVX2:
                    more_options.append('/arch:AVX2')
                if WARNING_AS_ERROR:
                    more_options.append('/WX')
                extension.extra_compile_args.extend(more_options)
        super().build_extensions()

setup(
    name='pyzstd',
    version=module_version,
    description="Python bindings to Zstandard (zstd) compression library, the API is similar to Python's bz2/lzma/zlib modules.",
    long_description=long_description,
    long_description_content_type='text/x-rst',
    author='Ma Lin',
    author_email='malincns@163.com',
    url='https://github.com/animalize/pyzstd',
    license='The 3-Clause BSD License',
    python_requires='>=3.5',

    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "Topic :: System :: Archiving :: Compression",
        "License :: OSI Approved :: BSD License",
        "Programming Language :: Python :: Implementation :: CPython",
        "Programming Language :: Python :: Implementation :: PyPy",
        "Programming Language :: Python :: 3.5",
        "Programming Language :: Python :: 3.6",
        "Programming Language :: Python :: 3.7",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
    ],
    keywords='zstandard zstd compression decompression compress decompress',

    package_dir={'pyzstd': 'src'},
    packages=packages,
    package_data={'pyzstd': ['__init__.pyi', 'py.typed']},

    ext_modules=[binary_extension],
    cmdclass={'build_ext': build_ext_compiler_check},

    test_suite='tests'
)
