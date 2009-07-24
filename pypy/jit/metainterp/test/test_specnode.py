from pypy.rpython.lltypesystem import lltype, llmemory
from pypy.jit.metainterp.history import AbstractDescr, BoxPtr, ConstInt
from pypy.jit.metainterp.specnode import prebuiltNotSpecNode
from pypy.jit.metainterp.specnode import VirtualInstanceSpecNode
from pypy.jit.metainterp.specnode import equals_specnodes
from pypy.jit.metainterp.test.test_optimizefindnode import LLtypeMixin

def _get_vspecnode(classnum=123):
    return VirtualInstanceSpecNode(ConstInt(classnum),
                         [(LLtypeMixin.valuedescr, prebuiltNotSpecNode),
                          (LLtypeMixin.nextdescr,  prebuiltNotSpecNode)])

def test_equals_specnodes():
    assert equals_specnodes([prebuiltNotSpecNode, prebuiltNotSpecNode],
                            [prebuiltNotSpecNode, prebuiltNotSpecNode])
    vspecnode1 = _get_vspecnode(1)
    vspecnode2 = _get_vspecnode(2)
    assert equals_specnodes([vspecnode1], [vspecnode1])
    assert not equals_specnodes([vspecnode1], [vspecnode2])
    assert not equals_specnodes([vspecnode1], [prebuiltNotSpecNode])
    assert not equals_specnodes([prebuiltNotSpecNode], [vspecnode2])

def test_extract_runtime_data_1():
    res = []
    prebuiltNotSpecNode.extract_runtime_data("cpu", "box1", res)
    prebuiltNotSpecNode.extract_runtime_data("cpu", "box2", res)
    assert res == ["box1", "box2"]

def test_extract_runtime_data_2():
    structure = lltype.malloc(LLtypeMixin.NODE)
    structure.value = 515
    structure.next = lltype.malloc(LLtypeMixin.NODE)
    structbox = BoxPtr(lltype.cast_opaque_ptr(llmemory.GCREF, structure))
    vspecnode = _get_vspecnode()
    res = []
    vspecnode.extract_runtime_data(LLtypeMixin.cpu, structbox, res)
    assert len(res) == 2
    assert res[0].value == structure.value
    assert res[1].value._obj.container._as_ptr() == structure.next
