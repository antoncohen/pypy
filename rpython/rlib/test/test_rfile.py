
from rpython.rtyper.test.tool import BaseRtypingTest
from rpython.tool.udir import udir
from rpython.rlib import rfile

class TestFile(BaseRtypingTest):
    def setup_class(cls):
        cls.tmpdir = udir.join('test_rfile')
        cls.tmpdir.ensure(dir=True)

    def test_open(self):
        fname = str(self.tmpdir.join('file_1'))

        def f():
            f = open(fname, "w")
            f.write("dupa")
            f.close()

        self.interpret(f, [])
        assert open(fname, "r").read() == "dupa"

    def test_read_write(self):
        fname = str(self.tmpdir.join('file_2'))

        def f():
            f = open(fname, "w")
            f.write("dupa")
            f.close()
            f2 = open(fname)
            dupa = f2.read()
            assert dupa == "dupa"
            f2.close()

        self.interpret(f, [])

    def test_read_sequentially(self):
        fname = self.tmpdir.join('file_3')
        fname.write("dupa")
        fname = str(fname)

        def f():
            f = open(fname)
            a = f.read(1)
            b = f.read(1)
            c = f.read(1)
            d = f.read(1)
            e = f.read()
            f.close()
            assert a == "d"
            assert b == "u"
            assert c == "p"
            assert d == "a"
            assert e == ""

        self.interpret(f, [])
