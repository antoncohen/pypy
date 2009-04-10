import py
from pypy.jit.backend.x86.runner import CPU386
from pypy.jit.metainterp.warmspot import ll_meta_interp
from pypy.jit.metainterp.test import test_basic
from pypy.jit.metainterp.policy import StopAtXPolicy
from pypy.rlib.jit import JitDriver

class Jit386Mixin(test_basic.LLJitMixin):
    type_system = 'lltype'
    CPUClass = CPU386

    def check_jumps(self, maxcount):
        pass

class TestBasic(Jit386Mixin, test_basic.BasicTests):
    # for the individual tests see
    # ====> ../../../metainterp/test/test_basic.py
    def test_bug(self):
        jitdriver = JitDriver(greens = [], reds = ['n'])
        class X(object):
            pass
        def f(n):
            while n > -100:
                jitdriver.can_enter_jit(n=n)
                jitdriver.jit_merge_point(n=n)
                x = X()
                x.arg = 5
                if n <= 0: break
                n -= x.arg
                x.arg = 6   # prevents 'x.arg' from being annotated as constant
            return n
        res = self.meta_interp(f, [31], specialize=False)
        assert res == -4
