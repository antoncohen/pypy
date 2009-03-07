import py
from pypy.rpython.lltypesystem import lltype, lloperation, rclass, llmemory
from pypy.rpython.annlowlevel import llhelper
from pypy.jit.metainterp.policy import StopAtXPolicy
from pypy.rlib.jit import JitDriver, hint
from pypy.jit.metainterp.test.test_basic import LLJitMixin, OOJitMixin
from pypy.rpython.lltypesystem.rvirtualizable2 import VABLERTIPTR
from pypy.jit.metainterp.test.test_vable_optimize import XY, xy_vtable

promote_virtualizable = lloperation.llop.promote_virtualizable
debug_print = lloperation.llop.debug_print

# ____________________________________________________________

class ExplicitVirtualizableTests:

    def _freeze_(self):
        return True

    @staticmethod
    def setup():
        xy = lltype.malloc(XY)
        xy.vable_rti = lltype.nullptr(VABLERTIPTR.TO)
        xy.parent.typeptr = xy_vtable
        return xy

    def test_preexisting_access(self):
        myjitdriver = JitDriver(greens = [], reds = ['n', 'xy'],
                                virtualizables = ['xy'])
        def f(n):
            xy = self.setup()
            xy.x = 10
            while n > 0:
                myjitdriver.can_enter_jit(xy=xy, n=n)
                myjitdriver.jit_merge_point(xy=xy, n=n)
                promote_virtualizable(lltype.Void, xy, 'x')
                x = xy.x
                xy.x = x + 1
                n -= 1
            return xy.x
        res = self.meta_interp(f, [20])
        assert res == 30
        self.check_loops(getfield_gc=0, setfield_gc=0)

    def test_preexisting_access_2(self):
        myjitdriver = JitDriver(greens = [], reds = ['n', 'xy'],
                                virtualizables = ['xy'])
        def f(n):
            xy = self.setup()
            xy.x = 100
            while n > -8:
                myjitdriver.can_enter_jit(xy=xy, n=n)
                myjitdriver.jit_merge_point(xy=xy, n=n)
                if n > 0:
                    promote_virtualizable(lltype.Void, xy, 'x')
                    x = xy.x
                    xy.x = x + 1
                else:
                    promote_virtualizable(lltype.Void, xy, 'x')
                    x = xy.x
                    xy.x = x + 10
                n -= 1
            return xy.x
        res = self.meta_interp(f, [5])
        assert res == 185
        self.check_loops(getfield_gc=0, setfield_gc=0)

    def test_two_paths_access(self):
        myjitdriver = JitDriver(greens = [], reds = ['n', 'xy'],
                                virtualizables = ['xy'])
        def f(n):
            xy = self.setup()
            xy.x = 100
            while n > 0:
                myjitdriver.can_enter_jit(xy=xy, n=n)
                myjitdriver.jit_merge_point(xy=xy, n=n)
                promote_virtualizable(lltype.Void, xy, 'x')
                x = xy.x
                if n <= 10:
                    x += 1000
                xy.x = x + 1
                n -= 1
            return xy.x
        res = self.meta_interp(f, [18])
        assert res == 10118
        self.check_loops(getfield_gc=0, setfield_gc=0)                        


class ImplicitVirtualizableTests:

    def test_simple_implicit(self):
        myjitdriver = JitDriver(greens = [], reds = ['frame'],
                                virtualizables = ['frame'])

        class Frame(object):
            _virtualizable2_ = True
            def __init__(self, x, y):
                self.x = x
                self.y = y

        class SomewhereElse:
            pass
        somewhere_else = SomewhereElse()

        def f(n):
            frame = Frame(n, 0)
            somewhere_else.top_frame = frame        # escapes
            while frame.x > 0:
                myjitdriver.can_enter_jit(frame=frame)
                myjitdriver.jit_merge_point(frame=frame)
                frame.y += frame.x
                frame.x -= 1
            return somewhere_else.top_frame.y

        res = self.meta_interp(f, [10])
        assert res == 55
        self.check_loops(getfield_gc=0, setfield_gc=0)


    def test_virtualizable_with_virtual_list(self):
        myjitdriver = JitDriver(greens = [], reds = ['n', 'frame', 'x'],
                                virtualizables = ['frame'])


        class Frame(object):
            _virtualizable2_ = True
            def __init__(self, l, s):
                self.l = l
                self.s = s
        
        def f(n):
            frame = Frame([1,2,3,4], 0)
            x = 0
            while n > 0:
                myjitdriver.can_enter_jit(frame=frame, n=n, x=x)
                myjitdriver.jit_merge_point(frame=frame, n=n, x=x)
                frame.s = hint(frame.s, promote=True)
                n -= 1
                x += frame.l[frame.s]
                frame.s += 1
                x += frame.l[frame.s]
                frame.s -= 1
            return x

        res = self.meta_interp(f, [10])
        assert res == f(10)

    def test_virtual_on_virtualizable(self):
        myjitdriver = JitDriver(greens = [], reds = ['frame', 'n'],
                                virtualizables = ['frame'])

        class Stuff(object):
            def __init__(self, x):
                self.x = x

        class Stuff2(Stuff):
            pass

        class Frame(object):
            _virtualizable2_ = True
            def __init__(self, x):
                self.stuff = Stuff(x)

        def f(n):
            frame = Frame(3)
            while n > 0:
                myjitdriver.can_enter_jit(frame=frame, n=n)
                myjitdriver.jit_merge_point(frame=frame, n=n)
                if isinstance(frame.stuff, Stuff2):
                    return 2
                n -= frame.stuff.x
            return n

        res = self.meta_interp(f, [30])
        assert res == f(30)

    def test_unequal_list_lengths_cannot_be_virtual(self):
        jitdriver = JitDriver(greens = [], reds = ['frame', 'n'],
                              virtualizables = ['frame'])

        class Frame(object):
            _virtualizable2_ = True
            def __init__(self):
                self.l = []

        def f(n):
            frame = Frame()
            while n > 0:
                jitdriver.can_enter_jit(n=n, frame=frame)
                jitdriver.jit_merge_point(n=n, frame=frame)
                frame.l.append(n)
                n -= 1
            sum = 0
            for i in range(len(frame.l)):
                sum += frame.l[i]
            return sum

        res = self.meta_interp(f, [20])
        assert res == f(20)

    def test_external_read(self):
        py.test.skip("Fails")
        class Frame(object):
            _virtualizable2_ = True
        class SomewhereElse:
            pass
        somewhere_else = SomewhereElse()

        def g():
            result = somewhere_else.top_frame.y     # external read
            debug_print(lltype.Void, '-+-+-+-+- external read:', result)
            return result

        def f(n):
            frame = Frame()
            frame.x = n
            frame.y = 10
            somewhere_else.top_frame = frame
            while frame.x > 0:
                frame.x -= g()
                frame.y += 1
            return frame.x

        res = self.meta_interp(f, [123], exceptions=False,
                               policy=StopAtXPolicy(g))
        assert res == f(123)
        self.check_loops(getfield_gc=0, setfield_gc=0)

    def test_external_write(self):
        py.test.skip("Fails")
        class Frame(object):
            _virtualizable2_ = True
        class SomewhereElse:
            pass
        somewhere_else = SomewhereElse()

        def g():
            result = somewhere_else.top_frame.y + 1
            debug_print(lltype.Void, '-+-+-+-+- external write:', result)
            somewhere_else.top_frame.y = result      # external read/write

        def f(n):
            frame = Frame()
            frame.x = n
            frame.y = 10
            somewhere_else.top_frame = frame
            while frame.x > 0:
                g()
                frame.x -= frame.y
            return frame.y

        res = self.meta_interp(f, [240], exceptions=False,
                               policy=StopAtXPolicy(g))
        assert res == f(240)
        self.check_loops(getfield_gc=0, setfield_gc=0)

    def test_list_implicit(self):
        py.test.skip("in-progress")
        class Frame(object):
            _virtualizable2_ = True

        def f(n):
            frame = Frame()
            while n > 0:
                frame.lst = []
                frame.lst.append(n - 10)
                n = frame.lst[-1]
            return n + len(frame.lst)

        res = self.meta_interp(f, [53], exceptions=False)
        assert res == -6
        self.check_loops(getfield_gc=0, setfield_gc=0, call__4=0)

    def test_single_list_implicit(self):
        py.test.skip("in-progress")
        class Frame(object):
            _virtualizable2_ = True

        def f(n):
            frame = Frame()
            frame.lst = [100, n]
            while n > 0:
                n = frame.lst.pop()
                frame.lst.append(n - 10)
            return frame.lst.pop()

        res = self.meta_interp(f, [53], exceptions=False)
        assert res == -17
        self.check_loops(getfield_gc=0, setfield_gc=0, call__4=0)


##class TestOOtype(ExplicitVirtualizableTests,
##                 ImplicitVirtualizableTests,
##                 OOJitMixin):
##    pass

class TestLLtype(ExplicitVirtualizableTests,
                 ImplicitVirtualizableTests,
                 LLJitMixin):
    pass
