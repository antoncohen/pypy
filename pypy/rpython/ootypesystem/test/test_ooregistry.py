from pypy.annotation.annrpython import RPythonAnnotator
from pypy.annotation import model as annmodel
from pypy.rpython.ootypesystem import ooregistry # side effects
from pypy.rpython.ootypesystem import ootype
from pypy.rpython.test.test_llinterp import interpret

def test_oostring2_annotation():
    def oof():
        return ootype.oostring2

    a = RPythonAnnotator()
    s = a.build_types(oof, [])
    assert isinstance(s, annmodel.SomeBuiltin)

def test_oostring2_result_annotation():
    def oof():
        return ootype.oostring2(42, -1)

    a = RPythonAnnotator()
    s = a.build_types(oof, [])
    assert isinstance(s, annmodel.SomeOOInstance) and s.ootype is ootype.String

def test_oostring2_call():
    def const(c):
        return c
    
    def oof(ch):
        return ootype.oostring2(ch, const(-1))

    ch = 'a'
    res = interpret(oof, [ch], type_system='ootype')
    assert res._str == 'a'
