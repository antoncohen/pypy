from pypy.tool import udir
from pypy.translator.cli.rte import Target
from pypy.translator.cli.silverpython import DllDef
from pypy.translator.cli.test.runtest import CliFunctionWrapper, CliTest

TEMPLATE = """
using System;
class SilveRPytonTest {
    public static void Main() {
        %s
    }
}
"""

class TestSilveRPython(CliTest):
    
    def _csharp(self, reference, source):
        tmpfile = udir.udir.join('tmp.cs')
        tmpfile.write(TEMPLATE % source)
        if reference is None:
            flags = []
        else:
            flags = ['/r:%s' % reference]

        class MyTarget(Target):
            SOURCES = [str(tmpfile)]
            FLAGS = flags
            OUTPUT = 'tmp.exe'
            SRC_DIR = str(udir.udir)

        func = CliFunctionWrapper(MyTarget.get())
        return func()


    def test_compilation(self):
        res = self._csharp(None, 'Console.WriteLine(42);')
        assert res == 42

    def test_func_namespace(self):
        def foo(x):
            return x+1
        def bar(x):
            return foo(x)
        foo._namespace_ = 'MyNamespace.MyClass'
        bar._namespace_ = 'MyClass'
        res = self.interpret(bar, [41], backendopt=False)
        assert res == 42

    def test_simple_functions(self):
        def foo(x):
            return x+1
        def bar(x):
            return x*2
        dll = DllDef('test', 'Test', [(foo, [int]),
                                      (bar, [int])])
        dll.compile()
        res = self._csharp('test', 'Console.WriteLine("{0}, {1}", Test.foo(42), Test.bar(42));')
        assert res == (43, 84)
