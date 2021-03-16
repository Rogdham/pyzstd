#!/usr/bin/env python3
import fnmatch
import io
import os
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

# -------- C extension --------
def get_zstd_c_files_list():
    lst = []
    for sub_dir in ('common', 'compress', 'decompress', 'dictBuilder'):
        directory = 'lib/' + sub_dir + '/'
        l = [directory + fn for fn in os.listdir(directory) if fnmatch.fnmatch(fn, '*.[cC]')]
        lst.extend(l)
    return lst

def has_option(option):
    if option in sys.argv:
        sys.argv = [s for s in sys.argv if s != option]
        return True
    else:
        return False

DYNAMIC_LINK = has_option('--dynamic-link-zstd')
CFFI = has_option('--cffi')

if DYNAMIC_LINK:
    kwargs = {
        'include_dirs': [],    # .h directory
        'library_dirs': [],    # .lib directory
        'libraries': ['zstd'], # lib name, not filename, for the linker.
        'sources': [],
        'define_macros': [('ZSTD_MULTITHREAD', None)]
    }
else:  # statically link to zstd lib
    kwargs = {
        'include_dirs': ['lib', 'lib/dictBuilder'],
        'library_dirs': [],
        'libraries': [],
        'sources': get_zstd_c_files_list(),
        'define_macros': [('ZSTD_MULTITHREAD', None)]
    }

if CFFI:
    # binary extension
    kwargs['module_name'] = 'pyzstd.cffi._cffi_zstd'

    import build_cffi
    build_cffi.set_args(**kwargs)
    ext_module = build_cffi.ffibuilder.distutils_extension()

    # packages
    packages=['pyzstd', 'pyzstd.cffi']
else:  # C implementation
    # binary extension
    kwargs['name'] = 'pyzstd.c._zstd'
    kwargs['sources'].append('src/_zstdmodule.c')

    ext_module = Extension(**kwargs)

    # packages
    packages=['pyzstd', 'pyzstd.c']

class build_ext_compiler_check(build_ext):
    def build_extensions(self):
        if 'msvc' in self.compiler.compiler_type.lower():
            for extension in self.extensions:
                # The default is /Ox optimization
                # /Ob3 is more aggressive inlining than /Ob2:
                # https://github.com/facebook/zstd/issues/2314
                # /GF eliminates duplicate strings
                # /Gy does function level linking
                more_options = ['/Ob3', '/GF', '/Gy']
                extension.extra_compile_args.extend(more_options)
        super().build_extensions()

setup(
    name='pyzstd',
    version=module_version,
    description="Python bindings to Zstandard (zstd) compression library, the API is similar to Python's bz2/lzma/zlib module.",
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
        "License :: OSI Approved :: BSD License",
        "Topic :: System :: Archiving :: Compression",
        "Programming Language :: Python :: 3.5",
        "Programming Language :: Python :: 3.6",
        "Programming Language :: Python :: 3.7",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
    ],
    keywords='zstandard zstd compression decompression compress decompress',

    package_dir={'pyzstd': 'src'},
    packages=packages,
    package_data={'pyzstd': ['__init__.pyi', 'py.typed']},

    ext_modules=[ext_module],
    cmdclass={'build_ext': build_ext_compiler_check},

    test_suite='tests'
)
