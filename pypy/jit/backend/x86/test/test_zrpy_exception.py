
import py
from pypy.jit.metainterp.test import test_zrpy_exception
from pypy.jit.backend.x86.test.test_zrpy_slist import Jit386Mixin
from pypy.jit.backend.x86.support import c_meta_interp

class TestException(Jit386Mixin, test_zrpy_exception.TestLLExceptions):
    # for the individual tests see
    # ====> ../../../metainterp/test/test_exception.py
    pass

