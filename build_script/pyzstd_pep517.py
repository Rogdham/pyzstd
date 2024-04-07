from setuptools import build_meta as _orig
from setuptools.build_meta import *

def get_requires_for_build_wheel(config_settings=None):
    requires = []
    if isinstance(config_settings, dict) and '--build-option' in config_settings:
        v = config_settings['--build-option']
        if isinstance(v, (str, list)) and '--cffi' in v:
            requires.append('cffi')
    return _orig.get_requires_for_build_wheel(config_settings) + requires
