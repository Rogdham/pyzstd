from setuptools import build_meta as _orig
from setuptools.build_meta import *

def get_requires_for_build_wheel(cfg=None):
    requires = []
    if isinstance(cfg, dict) and '--build-option' in cfg:
        v = cfg['--build-option']
        if isinstance(v, (str, list)) and '--cffi' in v:
            requires.append('cffi')
    return _orig.get_requires_for_build_wheel(cfg) + requires