from pypy.jit.metainterp.warmspot import ll_meta_interp
from pypy.rlib.jit import JitDriver

class Exit(Exception):
    def __init__(self, result):
        self.result = result


class WarmspotTests(object):
    def meta_interp(self, *args, **kwds):
        return ll_meta_interp(*args, **kwds)
    
    def test_basic(self):
        mydriver = JitDriver(reds=['a'],
                             greens=['i'])
        CODE_INCREASE = 0
        CODE_JUMP = 1
        lst = [CODE_INCREASE, CODE_INCREASE, CODE_JUMP]
        def interpreter_loop(a):
            i = 0
            while True:
                mydriver.jit_merge_point(i=i, a=a)
                if i >= len(lst):
                    break
                elem = lst[i]
                if elem == CODE_INCREASE:
                    a = a + 1
                    i += 1
                elif elem == CODE_JUMP:
                    if a < 20:
                        i = 0
                        mydriver.can_enter_jit(i=i, a=a)
                    else:
                        i += 1
                else:
                    pass
            raise Exit(a)

        def main(a):
            try:
                interpreter_loop(a)
            except Exit, e:
                return e.result

        res = self.meta_interp(main, [1])
        assert res == 21

    def test_reentry(self):
        mydriver = JitDriver(reds = ['n'], greens = [])

        def f(n):
            while n > 0:
                mydriver.can_enter_jit(n=n)
                mydriver.jit_merge_point(n=n)
                if n % 20 == 0:
                    n -= 2
                n -= 1

        res = self.meta_interp(f, [60])
        assert res == f(30)


class TestLLWarmspot(WarmspotTests):
    pass
