import py
from pypy.rlib.jit import JitDriver
from pypy.jit.metainterp.test.test_basic import LLJitMixin, OOJitMixin
from pypy.jit.metainterp import simple_optimize
from pypy.jit.metainterp.policy import StopAtXPolicy
from pypy.rpython.annlowlevel import hlstr
from pypy.jit.metainterp.warmspot import CannotInlineCanEnterJit

class RecursiveTests:

    def test_simple_recursion(self):
        myjitdriver = JitDriver(greens=[], reds=['n', 'm'])
        def f(n):
            m = n - 2
            while True:
                myjitdriver.jit_merge_point(n=n, m=m)
                n -= 1
                if m == n:
                    return main(n) * 2
                myjitdriver.can_enter_jit(n=n, m=m)
        def main(n):
            if n > 0:
                return f(n+1)
            else:
                return 1
        res = self.meta_interp(main, [20], optimizer=simple_optimize)
        assert res == main(20)

    def test_simple_recursion_with_exc(self):
        myjitdriver = JitDriver(greens=[], reds=['n', 'm'])
        class Error(Exception):
            pass
        
        def f(n):
            m = n - 2
            while True:
                myjitdriver.jit_merge_point(n=n, m=m)
                n -= 1
                if n == 10:
                    raise Error
                if m == n:
                    try:
                        return main(n) * 2
                    except Error:
                        return 2
                myjitdriver.can_enter_jit(n=n, m=m)
        def main(n):
            if n > 0:
                return f(n+1)
            else:
                return 1
        res = self.meta_interp(main, [20], optimizer=simple_optimize)
        assert res == main(20)

    def test_recursion_three_times(self):
        myjitdriver = JitDriver(greens=[], reds=['n', 'm', 'total'])
        def f(n):
            m = n - 3
            total = 0
            while True:
                myjitdriver.jit_merge_point(n=n, m=m, total=total)
                n -= 1
                total += main(n)
                if m == n:
                    return total + 5
                myjitdriver.can_enter_jit(n=n, m=m, total=total)
        def main(n):
            if n > 0:
                return f(n)
            else:
                return 1
        print
        for i in range(1, 11):
            print '%3d %9d' % (i, f(i))
        res = self.meta_interp(main, [10], optimizer=simple_optimize)
        assert res == main(10)
        self.check_enter_count_at_most(10)

    def test_bug_1(self):
        myjitdriver = JitDriver(greens=[], reds=['n', 'i', 'stack'])
        def opaque(n, i):
            if n == 1 and i == 19:
                for j in range(20):
                    res = f(0)      # recurse repeatedly, 20 times
                    assert res == 0
        def f(n):
            stack = [n]
            i = 0
            while i < 20:
                myjitdriver.can_enter_jit(n=n, i=i, stack=stack)
                myjitdriver.jit_merge_point(n=n, i=i, stack=stack)
                opaque(n, i)
                i += 1
            return stack.pop()
        res = self.meta_interp(f, [1], optimizer=simple_optimize, repeat=2,
                               policy=StopAtXPolicy(opaque))
        assert res == 1

    def get_interpreter(self, codes, always_inline=False):
        ADD = "0"
        JUMP_BACK = "1"
        CALL = "2"
        EXIT = "3"

        if always_inline:
            def can_inline(*args):
                return True
        else:
            def can_inline(code, i):
                code = hlstr(code)
                return not JUMP_BACK in code

        jitdriver = JitDriver(greens = ['code', 'i'], reds = ['n'],
                              can_inline = can_inline)
 
        def interpret(codenum, n, i):
            code = codes[codenum]
            while i < len(code):
                jitdriver.jit_merge_point(n=n, i=i, code=code)
                op = code[i]
                if op == ADD:
                    n += 1
                    i += 1
                elif op == CALL:
                    n = interpret(1, n, 1)
                    i += 1
                elif op == JUMP_BACK:
                    if n > 20:
                        return 42
                    i -= 2
                    jitdriver.can_enter_jit(n=n, i=i, code=code)
                elif op == EXIT:
                    return n
                else:
                    raise NotImplementedError
            return n

        return interpret

    def test_inline(self):
        code = "021"
        subcode = "00"

        codes = [code, subcode]
        f = self.get_interpreter(codes)

        assert self.meta_interp(f, [0, 0, 0], optimizer=simple_optimize) == 42
        self.check_loops(int_add = 1, call = 1)
        assert self.meta_interp(f, [0, 0, 0], optimizer=simple_optimize,
                                inline=True) == 42
        self.check_loops(int_add = 2, call = 0, guard_no_exception = 0)

    def test_inline_jitdriver_check(self):
        code = "021"
        subcode = "100"
        codes = [code, subcode]

        f = self.get_interpreter(codes)

        assert self.meta_interp(f, [0, 0, 0], optimizer=simple_optimize,
                                inline=True) == 42
        self.check_loops(call = 1)

    def test_inline_faulty_can_inline(self):
        code = "021"
        subcode = "301"
        codes = [code, subcode]

        f = self.get_interpreter(codes, always_inline=True)

        try:
            self.meta_interp(f, [0, 0, 0], optimizer=simple_optimize,
                             inline=True)
        except CannotInlineCanEnterJit:
            pass
        else:
            py.test.fail("DID NOT RAISE")

    def test_inner_failure(self):
        from pypy.rpython.annlowlevel import hlstr
        def p(code, pc):
            code = hlstr(code)
            return "%s %d %s" % (code, pc, code[pc])
        myjitdriver = JitDriver(greens=['code', 'pc'], reds=['n'], get_printable_location=p)
        def f(code, n):
            pc = 0
            while pc < len(code):

                myjitdriver.jit_merge_point(n=n, code=code, pc=pc)
                op = code[pc]
                if op == "-":
                    n -= 1
                elif op == "c":
                    n = f("---i---", n)
                elif op == "i":
                    if n % 5 == 1:
                        return n
                elif op == "l":
                    if n > 0:
                        myjitdriver.can_enter_jit(n=n, code=code, pc=0)
                        pc = 0
                        continue
                else:
                    assert 0
                pc += 1
            return n
        def main(n):
            return f("c-l", n)
        print main(100)
        res = self.meta_interp(main, [100], optimizer=simple_optimize)
        assert res == 0


class TestLLtype(RecursiveTests, LLJitMixin):
    pass

class TestOOtype(RecursiveTests, OOJitMixin):
    def test_simple_recursion_with_exc(self):
        py.test.skip("Fails")
