
from rpython.jit.metainterp.test.test_recursive import RecursiveTests
from rpython.jit.backend.x86.test.test_basic import Jit386Mixin
from rpython.jit.backend.llsupport.codemap import unpack_traceback
from rpython.jit.backend.x86.arch import WORD

class TestRecursive(Jit386Mixin, RecursiveTests):
    # for the individual tests see
    # ====> ../../../metainterp/test/test_recursive.py
    def check_get_unique_id(self, codemaps):
        if WORD == 4:
            return # this is 64 bit only check

        assert len(codemaps) == 3
        codemaps.sort(lambda a, b: cmp(a[1], b[1]))
        # biggest is the big loop, smallest is the bridge
        assert unpack_traceback(codemaps[1][0]) == [2]
        # XXX very specific ASM addresses, very fragile test, but what we can
        #     do, really? 64bit only so far
        assert unpack_traceback(codemaps[0][0]) == [2]
        assert unpack_traceback(codemaps[1][0] + 100) == [2]
        assert unpack_traceback(codemaps[2][0] + 100) == [4]
        assert unpack_traceback(codemaps[2][0] + 200) == [4, 2]
        assert unpack_traceback(codemaps[2][0] + 500) == [4, 2]
        assert unpack_traceback(codemaps[2][0] + 380) == [4]
