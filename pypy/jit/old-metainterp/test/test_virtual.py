import py
from pypy.rlib.jit import JitDriver, hint
from pypy.jit.metainterp.policy import StopAtXPolicy
from pypy.jit.metainterp.test.test_basic import LLJitMixin, OOJitMixin
from pypy.rpython.lltypesystem import lltype, rclass
from pypy.jit.metainterp import heaptracker

class VirtualTests:
    def _freeze_(self):
        return True

    def test_virtualized(self):
        myjitdriver = JitDriver(greens = [], reds = ['n', 'node'])
        def f(n):
            node = self._new()
            node.value = 0
            node.extra = 0
            while n > 0:
                myjitdriver.can_enter_jit(n=n, node=node)
                myjitdriver.jit_merge_point(n=n, node=node)
                next = self._new()
                next.value = node.value + n
                next.extra = node.extra + 1
                node = next
                n -= 1
            return node.value * node.extra
        assert f(10) == 55 * 10
        res = self.meta_interp(f, [10])
        assert res == 55 * 10
        self.check_loop_count(1)
        self.check_loops(new=0, new_with_vtable=0,
                                getfield_gc=0, setfield_gc=0)

    def test_virtualized_2(self):
        myjitdriver = JitDriver(greens = [], reds = ['n', 'node'])
        def f(n):
            node = self._new()
            node.value = 0
            node.extra = 0
            while n > 0:
                myjitdriver.can_enter_jit(n=n, node=node)
                myjitdriver.jit_merge_point(n=n, node=node)
                next = self._new()
                next.value = node.value
                next.value += n
                next.extra = node.extra
                next.extra += 1
                next.extra += 1
                next.extra += 1
                node = next
                n -= 1
            return node.value * node.extra
        res = self.meta_interp(f, [10])
        assert res == 55 * 30
        self.check_loop_count(1)
        self.check_loops(new=0, new_with_vtable=0,
                                getfield_gc=0, setfield_gc=0)

    def test_nonvirtual_obj_delays_loop(self):
        myjitdriver = JitDriver(greens = [], reds = ['n', 'node'])
        node0 = self._new()
        node0.value = 10
        def f(n):
            node = node0
            while True:
                myjitdriver.can_enter_jit(n=n, node=node)
                myjitdriver.jit_merge_point(n=n, node=node)
                i = node.value
                if i >= n:
                    break
                node = self._new()
                node.value = i * 2
            return node.value
        res = self.meta_interp(f, [500])
        assert res == 640
        self.check_loop_count(1)
        self.check_loops(new=0, new_with_vtable=0,
                                getfield_gc=0, setfield_gc=0)

    def test_two_loops_with_virtual(self):
        myjitdriver = JitDriver(greens = [], reds = ['n', 'node'])
        def f(n):
            node = self._new()
            node.value = 0
            node.extra = 0
            while n > 0:
                myjitdriver.can_enter_jit(n=n, node=node)
                myjitdriver.jit_merge_point(n=n, node=node)
                next = self._new()
                next.value = node.value + n
                next.extra = node.extra + 1
                if next.extra == 4:
                    next.value += 100
                    next.extra = 0
                node = next
                n -= 1
            return node.value
        res = self.meta_interp(f, [10])
        assert res == f(10)
        self.check_loop_count(2)
        self.check_loops(new=0, new_with_vtable=0,
                                getfield_gc=0, setfield_gc=0)

    def test_two_loops_with_escaping_virtual(self):
        myjitdriver = JitDriver(greens = [], reds = ['n', 'node'])
        def externfn(node):
            return node.value * 2
        def f(n):
            node = self._new()
            node.value = 0
            node.extra = 0
            while n > 0:
                myjitdriver.can_enter_jit(n=n, node=node)
                myjitdriver.jit_merge_point(n=n, node=node)
                next = self._new()
                next.value = node.value + n
                next.extra = node.extra + 1
                if next.extra == 4:
                    next.value = externfn(next)
                    next.extra = 0
                node = next
                n -= 1
            return node.value
        res = self.meta_interp(f, [10], policy=StopAtXPolicy(externfn))
        assert res == f(10)
        self.check_loop_count(2)
        self.check_loops(**{self._new_op: 1})
        self.check_loops(int_mul=0, call__4=1)

    def test_two_virtuals(self):
        myjitdriver = JitDriver(greens = [], reds = ['n', 'prev'])
        class Foo(object):
            def __init__(self, x, y):
                self.x = x
                self.y = y

        def f(n):
            prev = Foo(n, 0)
            n -= 1
            while n >= 0:
                myjitdriver.can_enter_jit(n=n, prev=prev)
                myjitdriver.jit_merge_point(n=n, prev=prev)
                foo = Foo(n, 0)
                foo.x += prev.x
                prev = foo
                n -= 1
            return prev.x

        res = self.meta_interp(f, [12])
        assert res == 78
        self.check_loops(new_with_vtable=0, new=0)

    def test_both_virtual_and_field_variable(self):
        myjitdriver = JitDriver(greens = [], reds = ['n'])
        class Foo(object):
            pass
        def f(n):
            while n >= 0:
                myjitdriver.can_enter_jit(n=n)
                myjitdriver.jit_merge_point(n=n)
                foo = Foo()
                foo.n = n
                if n < 10:
                    break
                n = foo.n - 1
            return n

        res = self.meta_interp(f, [20])
        assert res == 9

##class TestOOtype(VirtualTests, OOJitMixin):
##    _new = staticmethod(ootype.new)

# ____________________________________________________________
# Run 1: all the tests instantiate a real RPython class

class MyClass:
    pass

class TestLLtype_Instance(VirtualTests, LLJitMixin):
    _new_op = 'new_with_vtable'
    @staticmethod
    def _new():
        return MyClass()

# ____________________________________________________________
# Run 2: all the tests use lltype.malloc to make a NODE

NODE = lltype.GcStruct('NODE', ('value', lltype.Signed),
                               ('extra', lltype.Signed))

class TestLLtype_NotObject(VirtualTests, LLJitMixin):
    _new_op = 'new'

    def setup_class(cls):
        py.test.skip("not supported yet")
    
    @staticmethod
    def _new():
        return lltype.malloc(NODE)

# ____________________________________________________________
# Run 3: all the tests use lltype.malloc to make a NODE2
# (same as Run 2 but it is part of the OBJECT hierarchy)

NODE2 = lltype.GcStruct('NODE2', ('parent', rclass.OBJECT),
                                 ('value', lltype.Signed),
                                 ('extra', lltype.Signed))

vtable2 = lltype.malloc(rclass.OBJECT_VTABLE, immortal=True)
heaptracker.set_testing_vtable_for_gcstruct(NODE2, vtable2)

class TestLLtype_Object(VirtualTests, LLJitMixin):
    _new_op = 'new_with_vtable'
    @staticmethod
    def _new():
        p = lltype.malloc(NODE2)
        p.parent.typeptr = vtable2
        return p

# ____________________________________________________________
