﻿#!/usr/bin/env python3
import fnmatch
import os
import platform
import re
import sys
from setuptools import setup, Extension
from setuptools.command.build_ext import build_ext

def read_stuff():
    ROOT_PATH = os.path.dirname(os.path.realpath(__file__))

    # Read README.md
    README_PATH = os.path.join(ROOT_PATH, 'README.md')
    with open(README_PATH, 'r', encoding='utf-8') as file:
        long_description = file.read()

    # Read module version
    INIT_PATH = os.path.join(ROOT_PATH, 'src', '__init__.py')
    with open(INIT_PATH, 'r', encoding='utf-8') as file:
        file_content = file.read()
        m = re.search(r'''__version__\s*=\s*(['"])(.*?)\1''', file_content)
        module_version = m.group(2)

    # Create LICENSE_zstd
    LICENSE_ZSTD_SRC = os.path.join(ROOT_PATH, 'zstd', 'LICENSE')
    with open(LICENSE_ZSTD_SRC, 'r', encoding='utf-8') as file:
        license_zstd = file.read()
    LICENSE_ZSTD_DST = os.path.join(ROOT_PATH, 'LICENSE_zstd')
    with open(LICENSE_ZSTD_DST, 'w', encoding='utf-8') as file:
        file.write(
            "Depending on how it is build, this package may distribute the zstd library,\n"
            "partially or in its integrality, in source or binary form.\n\n"
            "Its license is reproduced below.\n\n"
            "---\n\n"
        )
        file.write(license_zstd)

    return long_description, module_version

def get_zstd_files_list():
    ret = []
    for sub_dir in ('common', 'compress', 'decompress', 'dictBuilder'):
        directory = 'zstd/lib/' + sub_dir + '/'
        dir_list = os.listdir(directory)

        # Source files
        l = [directory + fn
               for fn in dir_list
               if fnmatch.fnmatch(fn, '*.[cCsS]')]
        ret.extend(l)
    return ret

def has_option(option):
    if option in sys.argv:
        print(' * build pyzstd wheel with option:', option)
        sys.argv = [s for s in sys.argv if s != option]
        return True
    else:
        return False

class pyzstd_build_ext(build_ext):
    PYZSTD_AVX2 = False
    PYZSTD_DEBUG = False
    PYZSTD_WARNING_AS_ERROR = False
    PYZSTD_CONFIG_MSG = ''

    def build_extensions(self):
        # Print build config message in actual build
        print(self.PYZSTD_CONFIG_MSG)

        # Accept assembly files
        self.compiler.src_extensions.extend(['.s', '.S'])
        # Build debug build
        self.debug = self.PYZSTD_DEBUG

        if self.compiler.compiler_type in ('unix', 'mingw32', 'cygwin'):
            # Remove -Wunreachable-code default args based on how Python was build
            # see distutils.sysconfig.get_config_var("CFLAGS")
            # see https://github.com/facebook/zstd/issues/4308
            self.compiler.compiler = [part for part in self.compiler.compiler if part != '-Wunreachable-code']
            self.compiler.compiler_so = [part for part in self.compiler.compiler_so if part != '-Wunreachable-code']

        for extension in self.extensions:
            if self.compiler.compiler_type in ('unix', 'mingw32', 'cygwin'):
                # -g0:
                #   Level 0 produces no debug information at all. This reduces
                #   the size of GCC wheels. By default CPython won't print any
                #   C stack trace, so -g0 and -g2 are same for most users.
                # -flto:
                #   This option runs the standard link-time optimizer. To use the
                #   link-time optimizer, -flto and optimization options should be
                #   specified at compile time and during the final link.
                more_options = ['-g0', '-flto']
                if self.PYZSTD_AVX2:
                    instrs = ['-mavx2', '-mlzcnt', '-mbmi', '-mbmi2']
                    more_options.extend(instrs)
                if self.PYZSTD_WARNING_AS_ERROR:
                    more_options.append('-Werror')
                extension.extra_compile_args.extend(more_options)
                extension.extra_link_args.extend(['-g0', '-flto'])
            elif self.compiler.compiler_type == 'msvc':
                # Remove .S source files, they use gcc/clang syntax.
                extension.sources = [i for i in extension.sources
                                        if not fnmatch.fnmatch(i, '*.[sS]')]

                # /Ob3: More aggressive inlining than /Ob2.
                # /GF:  Eliminates duplicate strings.
                # /Gy:  Does function level linking.
                #   /Ob3 is a bit faster on the whole. In setuptools v56.1+,
                #   /GF and /Gy are enabled by default, they reduce the size
                #   of MSVC wheels.
                more_options = ['/Ob3', '/GF', '/Gy']
                if self.PYZSTD_AVX2:
                    more_options.append('/arch:AVX2')
                if self.PYZSTD_WARNING_AS_ERROR:
                    more_options.append('/WX')
                extension.extra_compile_args.extend(more_options)
        super().build_extensions()

def do_setup():
    # Read stuff
    long_description, module_version = read_stuff()

    # Parse options
    pyzstd_build_ext.PYZSTD_AVX2 = has_option('--avx2')
    pyzstd_build_ext.PYZSTD_DEBUG = has_option('--debug')
    pyzstd_build_ext.PYZSTD_WARNING_AS_ERROR = has_option('--warning-as-error')

    DYNAMIC_LINK = has_option('--dynamic-link-zstd')
    CFFI = has_option('--cffi') or platform.python_implementation() == 'PyPy'
    MULTI_PHASE_INIT = has_option('--multi-phase-init')
    NO_MREMAP = has_option('--no-mremap')

    # Build config message
    pyzstd_build_ext.PYZSTD_CONFIG_MSG = \
               ('+--------------------------------------------+\n'
                '|             Pyzstd build config            |\n'
                '+-------------------------+------------------+\n'
                '| Pyzstd version          | {!s:<16} |\n'
                '+-------------------------+------------------+\n'
                '| Implementation          | {!s:<16} |\n'
                '+-------------------------+------------------+\n'
                '| Link to zstd library    | {!s:<16} |\n'
                '+-------------------------+------------------+\n'
                '| Enable AVX2/BMI2        | {!s:<16} |\n'
                '+-------------------------+------------------+\n'
                '| Debug build             | {!s:<16} |\n'
                '+-------------------------+------------------+\n'
                '| Warning as error        | {!s:<16} |\n'
                '+-------------------------+------------------+').format(
                    module_version,
                    'CFFI' if CFFI else 'C',
                    'Dynamically link' if DYNAMIC_LINK else 'Statically link',
                    pyzstd_build_ext.PYZSTD_AVX2,
                    pyzstd_build_ext.PYZSTD_DEBUG,
                    pyzstd_build_ext.PYZSTD_WARNING_AS_ERROR)

    if DYNAMIC_LINK:
        kwargs = {
            'include_dirs': [],    # .h directory
            'library_dirs': [],    # .lib directory
            'libraries': ['zstd'], # lib name, not filename, for the linker.
            'sources': [],
            'define_macros': []
        }
    else:  # Statically link to zstd lib
        kwargs = {
            'include_dirs': ['zstd/lib',
                             # For zstd 1.4.x:
                             'zstd/lib/common',
                             'zstd/lib/dictBuilder'],
            'library_dirs': [],
            'libraries': [],
            'sources': get_zstd_files_list(),
            'define_macros': [('PYZSTD_STATIC_LINK', None),
                              # Enable multi-threaded compression
                              ('ZSTD_MULTITHREAD', None)]
        }

    if CFFI:
        # Packages
        packages = ['pyzstd', 'pyzstd._cffi']

        # Binary extension
        kwargs['module_name'] = 'pyzstd._cffi._cffi_zstd'

        sys.path.append('build_script')
        import pyzstd_build_cffi
        binary_extension = pyzstd_build_cffi.get_extension(**kwargs)
    else:  # C implementation
        # Packages
        packages = ['pyzstd', 'pyzstd._c']

        # Binary extension
        kwargs['name'] = 'pyzstd._c._zstd'
        kwargs['sources'].append('src/bin_ext/pyzstd.c')
        if MULTI_PHASE_INIT:
            # Use multi-phase initialization (PEP-489) on CPython 3.11.
            # On CPython 3.12+, it's always enabled.
            kwargs['define_macros'].append(('USE_MULTI_PHASE_INIT', None))
        if NO_MREMAP:
            # Disable mremap output buffer on Linux
            kwargs['define_macros'].append(('PYZSTD_NO_MREMAP', None))

        binary_extension = Extension(**kwargs)

    setup(
        name='pyzstd',
        version=module_version,
        description=("Python bindings to Zstandard (zstd) compression library."),
        long_description=long_description,
        long_description_content_type='text/markdown',
        author='Ma Lin',
        author_email='malincns@163.com',
        maintainer="Rogdham",
        maintainer_email="contact@rogdham.net",
        url='https://github.com/Rogdham/pyzstd',
        license='BSD-3-Clause',
        python_requires='>=3.5',
        install_requires=["typing-extensions>=4.13.2 ; python_version<'3.13'"],

        classifiers=[
            "Development Status :: 5 - Production/Stable",
            "Intended Audience :: Developers",
            "Topic :: System :: Archiving :: Compression",
            "License :: OSI Approved :: BSD License",
            "Programming Language :: Python :: Implementation :: CPython",
            "Programming Language :: Python :: Implementation :: PyPy",
            "Programming Language :: Python :: 3.9",
            "Programming Language :: Python :: 3.10",
            "Programming Language :: Python :: 3.11",
            "Programming Language :: Python :: 3.12",
            "Programming Language :: Python :: 3.13",
        ],
        keywords='zstandard zstd zst compress decompress tar file seekable format',
        package_dir={'pyzstd': 'src'},
        packages=packages,
        package_data={'pyzstd': ['__init__.pyi', 'py.typed']},

        ext_modules=[binary_extension],
        cmdclass={'build_ext': pyzstd_build_ext},

        test_suite='tests'
    )

    if sys.version_info < (3, 9):
        print()
        print("WARNING")
        print("    Python {} has reached end of life.".format(platform.python_version()))
        print("    This version of Python will not be supported in future versions of pyzstd.")
        print()

if __name__ == '__main__':
    do_setup()
