import py
from pypy.rlib.jit import JitDriver
from pypy.jit.metainterp.warmspot import ll_meta_interp, get_stats
from pypy.rpython.lltypesystem import lltype
from pypy.jit.metainterp.test.test_basic import LLJitMixin
from pypy.jit.metainterp.policy import StopAtXPolicy

class TestLoop(LLJitMixin):
    specialize = False

    def meta_interp(self, f, args, policy=None):
        return ll_meta_interp(f, args, specialize=self.specialize,
                              policy=policy)

    def test_simple_loop(self):
        myjitdriver = JitDriver(greens = [], reds = ['x', 'y', 'res'])
        def f(x, y):
            res = 0
            while y > 0:
                myjitdriver.can_enter_jit(x=x, y=y, res=res)
                myjitdriver.jit_merge_point(x=x, y=y, res=res)
                res += x
                y -= 1
            return res * 2
        res = self.meta_interp(f, [6, 7])
        assert res == 84
        self.check_loop_count(1)

    def test_loop_with_delayed_setfield(self):
        myjitdriver = JitDriver(greens = [], reds = ['x', 'y', 'res', 'a'])
        class A(object):
            def __init__(self):
                self.x = 3
        
        def f(x, y):
            res = 0
            a = A()
            while y > 0:
                myjitdriver.can_enter_jit(x=x, y=y, res=res, a=a)
                myjitdriver.jit_merge_point(x=x, y=y, res=res, a=a)
                a.x = y
                if y < 3:
                    return a.x
                res += a.x
                y -= 1
            return res * 2
        res = self.meta_interp(f, [6, 13])
        assert res == f(6, 13)
        self.check_loop_count(1)
        if self.specialize:
            self.check_loops(getfield_gc = 0, setfield_gc = 1)

    def test_loop_with_two_paths(self):
        from pypy.rpython.lltypesystem import lltype
        from pypy.rpython.lltypesystem.lloperation import llop
        myjitdriver = JitDriver(greens = [], reds = ['x', 'y', 'res'])

        def l(y, x, t):
            llop.debug_print(lltype.Void, y, x, t)
        
        def g(y, x, r):
            if y <= 12:
                res = x - 2
            else:
                res = x
            l(y, x, r)
            return res

        def f(x, y):
            res = 0
            while y > 0:
                myjitdriver.can_enter_jit(x=x, y=y, res=res)
                myjitdriver.jit_merge_point(x=x, y=y, res=res)
                res += g(y, x, res)
                y -= 1
            return res * 2
        res = self.meta_interp(f, [6, 33], policy=StopAtXPolicy(l))
        assert res == f(6, 33)
        self.check_loop_count(2)

    def test_alternating_loops(self):
        myjitdriver = JitDriver(greens = [], reds = ['pattern'])
        def f(pattern):
            while pattern > 0:
                myjitdriver.can_enter_jit(pattern=pattern)
                myjitdriver.jit_merge_point(pattern=pattern)
                if pattern & 1:
                    pass
                else:
                    pass
                pattern >>= 1
        self.meta_interp(f, [0xF0F0])
        self.check_loop_count(2)

    def test_interp_simple(self):
        myjitdriver = JitDriver(greens = ['i'], reds = ['x', 'y'])
        bytecode = "bedca"
        def f(x, y):
            i = 0
            while i < len(bytecode):
                myjitdriver.can_enter_jit(i=i, x=x, y=y)
                myjitdriver.jit_merge_point(i=i, x=x, y=y)
                op = bytecode[i]
                if op == 'a':
                    x += 3
                elif op == 'b':
                    x += 1
                elif op == 'c':
                    x -= y
                elif op == 'd':
                    y += y
                else:
                    y += 1
                i += 1
            return x
        res = self.meta_interp(f, [100, 30])
        assert res == 42
        self.check_loop_count(0)

    def test_green_prevents_loop(self):
        myjitdriver = JitDriver(greens = ['i'], reds = ['x', 'y'])
        bytecode = "+--+++++----"
        def f(x, y):
            i = 0
            while i < len(bytecode):
                myjitdriver.can_enter_jit(i=i, x=x, y=y)
                myjitdriver.jit_merge_point(i=i, x=x, y=y)
                op = bytecode[i]
                if op == '+':
                    x += y
                else:
                    y += 1
                i += 1
            return x
        res = self.meta_interp(f, [100, 5])
        assert res == f(100, 5)
        self.check_loop_count(0)

    def test_interp_single_loop(self):
        myjitdriver = JitDriver(greens = ['i'], reds = ['x', 'y'])
        bytecode = "abcd"
        def f(x, y):
            i = 0
            while i < len(bytecode):
                myjitdriver.jit_merge_point(i=i, x=x, y=y)
                op = bytecode[i]
                if op == 'a':
                    x += y
                elif op == 'b':
                    y -= 1
                elif op == 'c':
                    if y:
                        i = 0
                        myjitdriver.can_enter_jit(i=i, x=x, y=y)
                        continue
                else:
                    x += 1
                i += 1
            return x
        res = self.meta_interp(f, [5, 8])
        assert res == 42
        self.check_loop_count(1)
        # the 'char_eq' and following 'guard' should be constant-folded
        self.check_loops(char_eq=0, guard_true=1, guard_false=0)

    def test_interp_many_paths(self):
        myjitdriver = JitDriver(greens = ['i'], reds = ['x', 'node'])
        NODE = lltype.GcForwardReference()
        NODE.become(lltype.GcStruct('NODE', ('value', lltype.Signed),
                                            ('next', lltype.Ptr(NODE))))
        bytecode = "xxxxxxxb"
        def f(node):
            x = 0
            i = 0
            while i < len(bytecode):
                myjitdriver.jit_merge_point(i=i, x=x, node=node)
                op = bytecode[i]
                if op == 'x':
                    if not node:
                        break
                    if node.value < 100:   # a pseudo-random choice
                        x += 1
                    node = node.next
                elif op == 'b':
                    i = 0
                    myjitdriver.can_enter_jit(i=i, x=x, node=node)
                    continue
                i += 1
            return x

        node1 = lltype.nullptr(NODE)
        for i in range(300):
            prevnode = lltype.malloc(NODE)
            prevnode.value = pow(47, i, 199)
            prevnode.next = node1
            node1 = prevnode

        expected = f(node1)
        res = self.meta_interp(f, [node1])
        assert res == expected
        self.check_loop_count_at_most(19)

    def test_nested_loops(self):
        myjitdriver = JitDriver(greens = ['i'], reds = ['x', 'y'])
        bytecode = "abc<de"
        def f(x, y):
            i = 0
            op = '-'
            while True:
                myjitdriver.jit_merge_point(i=i, x=x, y=y)
                op = bytecode[i]
                if op == 'a':
                    x += 1
                elif op == 'b':
                    x += y
                elif op == 'c':
                    y -= 1
                elif op == '<':
                    if y:
                        i -= 2
                        myjitdriver.can_enter_jit(i=i, x=x, y=y)
                        continue
                elif op == 'd':
                    y = x
                elif op == 'e':
                    if x > 1000:
                        break
                    else:
                        i = 0
                        myjitdriver.can_enter_jit(i=i, x=x, y=y)
                        continue
                i += 1
            return x

        expected = f(2, 3)
        res = self.meta_interp(f, [2, 3])
        assert res == expected

    def test_three_nested_loops(self):
        myjitdriver = JitDriver(greens = ['i'], reds = ['x'])
        bytecode = ".+357"
        def f(x):
            assert x >= 0
            i = 0
            while i < len(bytecode):
                myjitdriver.jit_merge_point(i=i, x=x)
                op = bytecode[i]
                if op == '+':
                    x += 1
                elif op == '.':
                    pass
                elif op == '3':
                    if x % 3 != 0:
                        i -= 1
                        myjitdriver.can_enter_jit(i=i, x=x)
                        continue
                elif op == '5':
                    if x % 5 != 0:
                        i -= 2
                        myjitdriver.can_enter_jit(i=i, x=x)
                        continue
                elif op == '7':
                    if x % 7 != 0:
                        i -= 4
                        myjitdriver.can_enter_jit(i=i, x=x)
                        continue
                i += 1
            return x

        expected = f(0)
        assert expected == 3*5*7
        res = self.meta_interp(f, [0])
        assert res == expected

    def test_unused_loop_constant(self):
        myjitdriver = JitDriver(greens = [], reds = ['x', 'y', 'z'])
        def f(x, y, z):
            while z > 0:
                myjitdriver.can_enter_jit(x=x, y=y, z=z)
                myjitdriver.jit_merge_point(x=x, y=y, z=z)
                x += z
                z -= 1
            return x * y
        expected = f(2, 6, 30)
        res = self.meta_interp(f, [2, 6, 30])
        assert res == expected

    def test_adapt_bridge_to_merge_point(self):
        myjitdriver = JitDriver(greens = [], reds = ['x', 'z'])

        class Z(object):
            def __init__(self, elem):
                self.elem = elem

        def externfn(z):
            pass

        def f(x, y):
            z = Z(y)
            while x > 0:
                myjitdriver.can_enter_jit(x=x, z=z)
                myjitdriver.jit_merge_point(x=x, z=z)
                if x % 5 != 0:
                    externfn(z)
                z = Z(z.elem + 1)
                x -= 1
            return z.elem
                
        expected = f(100, 5)
        res = self.meta_interp(f, [100, 5], policy=StopAtXPolicy(externfn))
        assert res == expected
        
        loops = get_stats().loops
        assert loops[0].operations[0].opname == 'merge_point'
        assert loops[1].operations[0].opname == 'catch'

    def test_example(self):
        myjitdriver = JitDriver(greens = ['i'],
                                reds = ['res', 'a'])
        CO_INCREASE = 0
        CO_JUMP_BACK_3 = 1
        CO_DECREASE = 2
        
        code = [CO_INCREASE, CO_INCREASE, CO_INCREASE,
                CO_JUMP_BACK_3, CO_INCREASE, CO_DECREASE]
        
        def add(res, a):
            return res + a

        def sub(res, a):
            return res - a
        
        def main_interpreter_loop(a):
            i = 0
            res = 0
            c = len(code)
            while i < c:
                myjitdriver.jit_merge_point(res=res, i=i, a=a)
                elem = code[i]
                if elem == CO_INCREASE:
                    res = add(res, a)
                elif elem == CO_DECREASE:
                    res = sub(res, a)
                else:
                    if res > 100:
                        pass
                    else:
                        i = i - 3
                        myjitdriver.can_enter_jit(res=res, i=i, a=a)
                        continue
                i = i + 1
            return res

        res = self.meta_interp(main_interpreter_loop, [1])
        assert res == 102
        self.check_loop_count(1)
        self.check_loops({'merge_point' : 1, 'int_add' : 3, 'int_gt' : 1,
                          'guard_false' : 1, 'jump' : 1})

    def test_automatic_promotion(self):
        myjitdriver = JitDriver(greens = ['i'],
                                reds = ['res', 'a'])
        CO_INCREASE = 0
        CO_JUMP_BACK_3 = 1
        
        code = [CO_INCREASE, CO_INCREASE, CO_INCREASE,
                CO_JUMP_BACK_3, CO_INCREASE]
        
        def add(res, a):
            return res + a

        def sub(res, a):
            return res - a
        
        def main_interpreter_loop(a):
            i = 0
            res = 0
            c = len(code)
            while True:
                myjitdriver.jit_merge_point(res=res, i=i, a=a)
                if i >= c:
                    break
                elem = code[i]
                if elem == CO_INCREASE:
                    i += a
                    res += a
                else:
                    if res > 100:
                        i += 1
                    else:
                        i = i - 3
                        myjitdriver.can_enter_jit(res=res, i=i, a=a)
            return res

        res = self.meta_interp(main_interpreter_loop, [1])
        assert res == main_interpreter_loop(1)
        self.check_loop_count(1)
        # XXX maybe later optimize guard_value away
        self.check_loops({'merge_point' : 1, 'int_add' : 6, 'int_gt' : 1,
                          'guard_false' : 1, 'jump' : 1, 'guard_value' : 3})
