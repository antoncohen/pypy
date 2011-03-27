import py
from pypy.rlib.jit import JitDriver, dont_look_inside, we_are_jitted
from pypy.jit.codewriter.policy import StopAtXPolicy
from pypy.rpython.ootypesystem import ootype
from pypy.jit.metainterp.test.test_basic import LLJitMixin, OOJitMixin


class StringTests:
    _str, _chr = str, chr

    def test_eq_residual(self):
        _str = self._str
        jitdriver = JitDriver(greens = [], reds = ['n', 'i', 's'])
        global_s = _str("hello")
        def f(n, b, s):
            if b:
                s += _str("ello")
            else:
                s += _str("allo")
            i = 0
            while n > 0:
                jitdriver.can_enter_jit(s=s, n=n, i=i)
                jitdriver.jit_merge_point(s=s, n=n, i=i)
                n -= 1 + (s == global_s)
                i += 1
            return i
        res = self.meta_interp(f, [10, True, _str('h')], listops=True)
        assert res == 5
        self.check_loops(**{self.CALL: 1, self.CALL_PURE: 0})

    def test_eq_folded(self):
        _str = self._str
        jitdriver = JitDriver(greens = ['s'], reds = ['n', 'i'])
        global_s = _str("hello")
        def f(n, b, s):
            if b:
                s += _str("ello")
            else:
                s += _str("allo")
            i = 0
            while n > 0:
                jitdriver.can_enter_jit(s=s, n=n, i=i)
                jitdriver.jit_merge_point(s=s, n=n, i=i)
                n -= 1 + (s == global_s)
                i += 1
            return i
        res = self.meta_interp(f, [10, True, _str('h')], listops=True)
        assert res == 5
        self.check_loops(**{self.CALL: 0, self.CALL_PURE: 0})

    def test_newstr(self):
        _str, _chr = self._str, self._chr
        jitdriver = JitDriver(greens = [], reds = ['n', 'm'])
        def f(n, m):
            while True:
                jitdriver.can_enter_jit(m=m, n=n)
                jitdriver.jit_merge_point(m=m, n=n)
                bytecode = _str('adlfkj') + _chr(n)
                res = bytecode[n]
                m -= 1
                if m < 0:
                    return ord(res)
        res = self.meta_interp(f, [6, 10])
        assert res == 6

    def test_char2string_pure(self):
        _str, _chr = self._str, self._chr
        jitdriver = JitDriver(greens = [], reds = ['n'])
        @dont_look_inside
        def escape(x):
            pass
        def f(n):
            while n > 0:
                jitdriver.can_enter_jit(n=n)
                jitdriver.jit_merge_point(n=n)
                s = _chr(n)
                if not we_are_jitted():
                    s += s     # forces to be a string
                if n > 100:
                    escape(s)
                n -= 1
            return 42
        self.meta_interp(f, [6])
        self.check_loops(newstr=0, strsetitem=0, strlen=0,
                         newunicode=0, unicodesetitem=0, unicodelen=0)

    def test_char2string_escape(self):
        _str, _chr = self._str, self._chr
        jitdriver = JitDriver(greens = [], reds = ['n', 'total'])
        @dont_look_inside
        def escape(x):
            return ord(x[0])
        def f(n):
            total = 0
            while n > 0:
                jitdriver.can_enter_jit(n=n, total=total)
                jitdriver.jit_merge_point(n=n, total=total)
                s = _chr(n)
                if not we_are_jitted():
                    s += s    # forces to be a string
                total += escape(s)
                n -= 1
            return total
        res = self.meta_interp(f, [6])
        assert res == 21

    def test_char2string2char(self):
        _str, _chr = self._str, self._chr
        jitdriver = JitDriver(greens = [], reds = ['m', 'total'])
        def f(m):
            total = 0
            while m > 0:
                jitdriver.can_enter_jit(m=m, total=total)
                jitdriver.jit_merge_point(m=m, total=total)
                string = _chr(m)
                if m > 100:
                    string += string    # forces to be a string
                # read back the character
                c = string[0]
                total += ord(c)
                m -= 1
            return total
        res = self.meta_interp(f, [6])
        assert res == 21
        self.check_loops(newstr=0, strgetitem=0, strsetitem=0, strlen=0,
                         newunicode=0, unicodegetitem=0, unicodesetitem=0,
                         unicodelen=0)

    def test_strconcat_pure(self):
        _str = self._str
        jitdriver = JitDriver(greens = [], reds = ['m', 'n'])
        @dont_look_inside
        def escape(x):
            pass
        mylist = [_str("abc") + _str(i) for i in range(10)]
        def f(n, m):
            while m >= 0:
                jitdriver.can_enter_jit(m=m, n=n)
                jitdriver.jit_merge_point(m=m, n=n)
                s = mylist[n] + mylist[m]
                if m > 100:
                    escape(s)
                m -= 1
            return 42
        self.meta_interp(f, [6, 7])
        self.check_loops(newstr=0, strsetitem=0,
                         newunicode=0, unicodesetitem=0,
                         call=0, call_pure=0)

    def test_strconcat_escape_str_str(self):
        _str = self._str
        jitdriver = JitDriver(greens = [], reds = ['m', 'n'])
        @dont_look_inside
        def escape(x):
            pass
        mylist = [_str("somestr") + _str(i) for i in range(10)]
        def f(n, m):
            while m >= 0:
                jitdriver.can_enter_jit(m=m, n=n)
                jitdriver.jit_merge_point(m=m, n=n)
                s = mylist[n] + mylist[m]
                escape(s)
                m -= 1
            return 42
        self.meta_interp(f, [6, 7])
        if _str is str:
            self.check_loops(newstr=1, strsetitem=0, copystrcontent=2,
                             call=1, call_pure=0)   # escape
        else:
            self.check_loops(newunicode=1, unicodesetitem=0,
                             copyunicodecontent=2,
                             call=1, call_pure=0)   # escape

    def test_strconcat_escape_str_char(self):
        _str, _chr = self._str, self._chr
        jitdriver = JitDriver(greens = [], reds = ['m', 'n'])
        @dont_look_inside
        def escape(x):
            pass
        mylist = [_str("somestr") + _str(i) for i in range(10)]
        def f(n, m):
            while m >= 0:
                jitdriver.can_enter_jit(m=m, n=n)
                jitdriver.jit_merge_point(m=m, n=n)
                s = mylist[n] + _chr(m)
                escape(s)
                m -= 1
            return 42
        self.meta_interp(f, [6, 7])
        if _str is str:
            self.check_loops(newstr=1, strsetitem=1, copystrcontent=1,
                             call=1, call_pure=0)   # escape
        else:
            self.check_loops(newunicode=1, unicodesetitem=1,
                             copyunicodecontent=1,
                             call=1, call_pure=0)   # escape

    def test_strconcat_escape_char_str(self):
        _str, _chr = self._str, self._chr
        jitdriver = JitDriver(greens = [], reds = ['m', 'n'])
        @dont_look_inside
        def escape(x):
            pass
        mylist = [_str("somestr") + _str(i) for i in range(10)]
        def f(n, m):
            while m >= 0:
                jitdriver.can_enter_jit(m=m, n=n)
                jitdriver.jit_merge_point(m=m, n=n)
                s = _chr(n) + mylist[m]
                escape(s)
                m -= 1
            return 42
        self.meta_interp(f, [6, 7])
        if _str is str:
            self.check_loops(newstr=1, strsetitem=1, copystrcontent=1,
                             call=1, call_pure=0)   # escape
        else:
            self.check_loops(newunicode=1, unicodesetitem=1,
                             copyunicodecontent=1,
                             call=1, call_pure=0)   # escape

    def test_strconcat_escape_char_char(self):
        _str, _chr = self._str, self._chr
        jitdriver = JitDriver(greens = [], reds = ['m', 'n'])
        @dont_look_inside
        def escape(x):
            pass
        def f(n, m):
            while m >= 0:
                jitdriver.can_enter_jit(m=m, n=n)
                jitdriver.jit_merge_point(m=m, n=n)
                s = _chr(n) + _chr(m)
                escape(s)
                m -= 1
            return 42
        self.meta_interp(f, [6, 7])
        if _str is str:
            self.check_loops(newstr=1, strsetitem=2, copystrcontent=0,
                             call=1, call_pure=0)   # escape
        else:
            self.check_loops(newunicode=1, unicodesetitem=2,
                             copyunicodecontent=0,
                             call=1, call_pure=0)   # escape

    def test_strconcat_escape_str_char_str(self):
        _str, _chr = self._str, self._chr
        jitdriver = JitDriver(greens = [], reds = ['m', 'n'])
        @dont_look_inside
        def escape(x):
            pass
        mylist = [_str("somestr") + _str(i) for i in range(10)]
        def f(n, m):
            while m >= 0:
                jitdriver.can_enter_jit(m=m, n=n)
                jitdriver.jit_merge_point(m=m, n=n)
                s = mylist[n] + _chr(n) + mylist[m]
                escape(s)
                m -= 1
            return 42
        self.meta_interp(f, [6, 7])
        if _str is str:
            self.check_loops(newstr=1, strsetitem=1, copystrcontent=2,
                             call=1, call_pure=0)   # escape
        else:
            self.check_loops(newunicode=1, unicodesetitem=1,
                             copyunicodecontent=2,
                             call=1, call_pure=0)   # escape

    def test_strconcat_guard_fail(self):
        _str = self._str
        jitdriver = JitDriver(greens = [], reds = ['m', 'n'])
        @dont_look_inside
        def escape(x):
            pass
        mylist = [_str("abc") + _str(i) for i in range(12)]
        def f(n, m):
            while m >= 0:
                jitdriver.can_enter_jit(m=m, n=n)
                jitdriver.jit_merge_point(m=m, n=n)
                s = mylist[n] + mylist[m]
                if m & 1:
                    escape(s)
                m -= 1
            return 42
        self.meta_interp(f, [6, 10])

    def test_strslice(self):
        _str = self._str
        longstring = _str("foobarbazetc")
        jitdriver = JitDriver(greens = [], reds = ['m', 'n'])
        @dont_look_inside
        def escape(x):
            pass
        def f(n, m):
            assert n >= 0
            while m >= 0:
                jitdriver.can_enter_jit(m=m, n=n)
                jitdriver.jit_merge_point(m=m, n=n)
                s = longstring[m:n]
                if m <= 5:
                    escape(s)
                m -= 1
            return 42
        self.meta_interp(f, [10, 10])

    def test_streq_char(self):
        _str = self._str
        longstring = _str("?abcdefg")
        somechar = _str("?")
        jitdriver = JitDriver(greens = [], reds = ['m', 'n'])
        @dont_look_inside
        def escape(x):
            pass
        def f(n, m):
            assert n >= 0
            while m >= 0:
                jitdriver.can_enter_jit(m=m, n=n)
                jitdriver.jit_merge_point(m=m, n=n)
                s = longstring[:m]
                escape(s == somechar)
                m -= 1
            return 42
        self.meta_interp(f, [6, 7])
        self.check_loops(newstr=0, newunicode=0)

    def test_str_slice_len_surviving(self):
        _str = self._str
        longstring = _str("Unrolling Trouble")
        mydriver = JitDriver(reds = ['i', 'a', 'sa'], greens = []) 
        def f(a):
            i = sa = a
            while i < len(longstring):
                mydriver.jit_merge_point(i=i, a=a, sa=sa)
                assert a >= 0 and i >= 0
                i = len(longstring[a:i+1])
                sa += i
            return sa
        assert self.meta_interp(f, [0]) == f(0)

    def test_virtual_strings_direct(self):
        _str = self._str
        fillers = _str("abcdefghijklmnopqrstuvwxyz")
        data = _str("ABCDEFGHIJKLMNOPQRSTUVWXYZ")

        mydriver = JitDriver(reds = ['line', 'noise', 'res'], greens = []) 
        def f():
            line = data
            noise = fillers
            ratio = len(line) // len(noise)
            res = data[0:0]
            while line and noise:
                mydriver.jit_merge_point(line=line, noise=noise, res=res)
                if len(line) // len(noise) > ratio:
                    c, line = line[0], line[1:]
                else:
                    c, noise = noise[0], noise[1:]
                res += c
            return res + noise + line
        s1 = self.meta_interp(f, [])
        s2 = f()
        for c1, c2 in zip(s1.chars, s2):
            assert c1==c2

    def test_virtual_strings_boxed(self):
        _str = self._str
        fillers = _str("abcdefghijklmnopqrstuvwxyz")
        data = _str("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
        class Str(object):
            def __init__(self, value):
                self.value = value
        mydriver = JitDriver(reds = ['ratio', 'line', 'noise', 'res'],
                             greens = []) 
        def f():
            line = Str(data)
            noise = Str(fillers)
            ratio = len(line.value) // len(noise.value)
            res = Str(data[0:0])
            while line.value and noise.value:
                mydriver.jit_merge_point(line=line, noise=noise, res=res,
                                         ratio=ratio)
                if len(line.value) // len(noise.value) > ratio:
                    c, line = line.value[0], Str(line.value[1:])
                else:
                    c, noise = noise.value[0], Str(noise.value[1:])
                res = Str(res.value + c)
            return res.value + noise.value + line.value
        s1 = self.meta_interp(f, [])
        s2 = f()
        for c1, c2 in zip(s1.chars, s2):
            assert c1==c2


#class TestOOtype(StringTests, OOJitMixin):
#    CALL = "oosend"
#    CALL_PURE = "oosend_pure"

class TestLLtype(StringTests, LLJitMixin):
    CALL = "call"
    CALL_PURE = "call_pure"

class TestLLtypeUnicode(TestLLtype):
    _str, _chr = unicode, unichr

    def test_str2unicode(self):
        _str = self._str
        jitdriver = JitDriver(greens = [], reds = ['m', 'n'])
        class Foo:
            pass
        @dont_look_inside
        def escape(x):
            assert x == _str("6y")
        def f(n, m):
            while m >= 0:
                jitdriver.can_enter_jit(m=m, n=n)
                jitdriver.jit_merge_point(m=m, n=n)
                foo = Foo()
                foo.y = chr(m)
                foo.y = "y"
                s = _str(str(n)) + _str(foo.y)
                escape(s)
                m -= 1
            return 42
        self.meta_interp(f, [6, 7])
        self.check_loops(call=3,    # str(), _str(), escape()
                         newunicode=1, unicodegetitem=0,
                         unicodesetitem=1, copyunicodecontent=1)
