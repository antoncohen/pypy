from pypy.conftest import gettestobjspace
from pypy.translator.platform import platform
from pypy.translator.tool.cbuild import ExternalCompilationInfo
from pypy.module._rawffi.interp_rawffi import TYPEMAP
from pypy.module._rawffi.tracker import Tracker
from pypy.translator.platform import platform

import os, sys, py

class AppTestFfi:

    @classmethod
    def prepare_c_example(cls):
        from pypy.tool.udir import udir
        from pypy.translator.tool.cbuild import ExternalCompilationInfo
        from pypy.translator.platform import platform

        c_file = udir.ensure("test__ffi", dir=1).join("foolib.c")
        # automatically collect the C source from the docstrings of the tests
        snippets = []
        for name in dir(cls):
            if name.startswith('test_'):
                meth = getattr(cls, name)
                # the heuristic to determine it it's really C code could be
                # improved: so far we just check that there is a '{' :-)
                if meth.__doc__ is not None and '{' in meth.__doc__:
                    snippets.append(meth.__doc__)
        #
        c_file.write(py.code.Source('\n'.join(snippets)))
        eci = ExternalCompilationInfo(export_symbols=[])
        return str(platform.compile([c_file], eci, 'x', standalone=False))

    
    def setup_class(cls):
        from pypy.rpython.lltypesystem import rffi
        from pypy.rlib.libffi import get_libc_name, CDLL, types
        from pypy.rlib.test.test_libffi import get_libm_name
        space = gettestobjspace(usemodules=('_ffi',))
        cls.space = space
        cls.w_libfoo_name = space.wrap(cls.prepare_c_example())
        cls.w_libc_name = space.wrap(get_libc_name())
        libm_name = get_libm_name(sys.platform)
        cls.w_libm_name = space.wrap(libm_name)
        libm = CDLL(libm_name)
        pow = libm.getpointer('pow', [], types.void)
        pow_addr = rffi.cast(rffi.LONG, pow.funcsym)
        cls.w_pow_addr = space.wrap(pow_addr)

    def test_libload(self):
        import _ffi
        _ffi.CDLL(self.libc_name)

    def test_libload_fail(self):
        import _ffi
        raises(OSError, _ffi.CDLL, "xxxxx_this_name_does_not_exist_xxxxx")

    def test_simple_types(self):
        from _ffi import types
        assert str(types.sint) == '<ffi type sint>'
        assert str(types.uint) == '<ffi type uint>'
        
    def test_callfunc(self):
        from _ffi import CDLL, types
        libm = CDLL(self.libm_name)
        pow = libm.getfunc('pow', [types.double, types.double], types.double)
        assert pow(2, 3) == 8

    def test_getaddr(self):
        from _ffi import CDLL, types
        libm = CDLL(self.libm_name)
        pow = libm.getfunc('pow', [types.double, types.double], types.double)
        assert pow.getaddr() == self.pow_addr
        
    def test_int_args(self):
        """
            int sum_xy(int x, int y)
            {
                return x+y;
            }
        """
        from _ffi import CDLL, types
        libfoo = CDLL(self.libfoo_name)
        sum_xy = libfoo.getfunc('sum_xy', [types.sint, types.sint], types.sint)
        assert sum_xy(30, 12) == 42

    def test_void_result(self):
        """
            int dummy = 0;
            void set_dummy(int val) { dummy = val; }
            int get_dummy() { return dummy; }
        """
        from _ffi import CDLL, types
        libfoo = CDLL(self.libfoo_name)
        set_dummy = libfoo.getfunc('set_dummy', [types.sint], types.void)
        get_dummy = libfoo.getfunc('get_dummy', [], types.sint)
        assert get_dummy() == 0
        assert set_dummy(42) is None
        assert get_dummy() == 42

    def test_TypeError_numargs(self):
        from _ffi import CDLL, types
        libfoo = CDLL(self.libfoo_name)
        sum_xy = libfoo.getfunc('sum_xy', [types.sint, types.sint], types.sint)
        raises(TypeError, "sum_xy(1, 2, 3)")
        raises(TypeError, "sum_xy(1)")

    def test_TypeError_voidarg(self):
        from _ffi import CDLL, types
        libfoo = CDLL(self.libfoo_name)
        raises(TypeError, "libfoo.getfunc('sum_xy', [types.void], types.sint)")
        
    def test_OSError_loading(self):
        from _ffi import CDLL, types
        raises(OSError, "CDLL('I do not exist')")
