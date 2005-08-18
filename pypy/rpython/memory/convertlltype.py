from pypy.rpython.memory import lladdress
from pypy.rpython.memory.lltypesimulation import simulatorptr, sizeof
from pypy.rpython.memory.lltypesimulation import get_fixed_size
from pypy.rpython.memory.lltypesimulation import get_variable_size
from pypy.rpython.memory.lltypesimulation import primitive_to_fmt
from pypy.rpython.memory.lltypesimulation import get_layout
from pypy.objspace.flow.model import traverse, Link, Constant, Block
from pypy.objspace.flow.model import Constant
from pypy.rpython import lltype

from pypy.rpython.rmodel import IntegerRepr

import types, struct

FUNCTIONTYPES = (types.FunctionType, types.UnboundMethodType,
                 types.BuiltinFunctionType)

INT_SIZE = struct.calcsize("i")

class LLTypeConverter(object):
    def __init__(self, address, gc=None):
        self.type_to_typeid = {}
        self.types = []
        self.converted = {}
        self.curraddress = address
        self.constantroots = []
        self.gc = gc

    def get_typeid(self, TYPE):
        if TYPE not in self.type_to_typeid:
            index = len(self.types)
            self.type_to_typeid[TYPE] = index
            self.types.append(TYPE)
        typeid = self.type_to_typeid[TYPE]
        return typeid

    def convert(self, val_or_ptr, inline_to_addr=None, from_parent=False):
        TYPE = lltype.typeOf(val_or_ptr)
        if isinstance(TYPE, lltype.Primitive):
            if inline_to_addr is not None and TYPE != lltype.Void:
                inline_to_addr._store(primitive_to_fmt[TYPE], val_or_ptr)
            return val_or_ptr
        elif isinstance(TYPE, lltype.Array):
            return self.convert_array(val_or_ptr, inline_to_addr, from_parent)
        elif isinstance(TYPE, lltype.Struct):
            return self.convert_struct(val_or_ptr, inline_to_addr, from_parent)
        elif isinstance(TYPE, lltype.Ptr):
            return self.convert_pointer(val_or_ptr, inline_to_addr, from_parent)
        elif isinstance(TYPE, lltype.OpaqueType):
            return self.convert_object(val_or_ptr, inline_to_addr, from_parent)
        elif isinstance(TYPE, lltype.FuncType):
            return self.convert_object(val_or_ptr, inline_to_addr, from_parent)
        elif isinstance(TYPE, lltype.PyObjectType):
            return self.convert_object(val_or_ptr, inline_to_addr, from_parent)
        else:
            assert 0, "don't know about %s" % (val_or_ptr, )

    def convert_array(self, _array, inline_to_addr, from_parent):
        if _array in self.converted:
            address = self.converted[_array]
            assert inline_to_addr is None or address == inline_to_addr
            return address
        TYPE = lltype.typeOf(_array)
        arraylength = len(_array.items)
        size = sizeof(TYPE, arraylength)
        if inline_to_addr is not None:
            startaddr = inline_to_addr
        else:
            startaddr = self.curraddress
            if self.gc is not None:
                self.gc.init_gc_object(startaddr, self.get_typeid(TYPE))
                startaddr += self.gc.size_gc_header()
            self.constantroots.append(
                simulatorptr(lltype.Ptr(TYPE), startaddr))
            self.curraddress += size
            if self.gc is not None:
                self.curraddress += self.gc.size_gc_header()
        self.converted[_array] = startaddr
        startaddr.signed[0] = arraylength
        curraddr = startaddr + get_fixed_size(TYPE)
        varsize = get_variable_size(TYPE)
        for item in _array.items:
            self.convert(item, curraddr, from_parent=True)
            curraddr += varsize
        return startaddr

    def convert_struct(self, _struct, inline_to_addr, from_parent):
        if _struct in self.converted:
            address = self.converted[_struct]
            assert inline_to_addr is None or address == inline_to_addr
            return address
        parent = _struct._parentstructure()
        if parent is not None and not from_parent:
            address = self.convert(parent)
            layout = get_layout(lltype.typeOf(parent))
            return address + layout[_struct._parent_index]
        TYPE = lltype.typeOf(_struct)
        layout = get_layout(TYPE)
        if TYPE._arrayfld is not None:
            inlinedarraylength = len(getattr(_struct, TYPE._arrayfld).items)
            size = sizeof(TYPE, inlinedarraylength)
        else:
            size = sizeof(TYPE)
        if inline_to_addr is not None:
            startaddr = inline_to_addr
        else:
            startaddr = self.curraddress
            if self.gc is not None:
                self.gc.init_gc_object(startaddr, self.get_typeid(TYPE))
                startaddr += self.gc.size_gc_header()
            self.constantroots.append(
                simulatorptr(lltype.Ptr(TYPE), startaddr))
            self.curraddress += size
            if self.gc is not None:
                self.curraddress += self.gc.size_gc_header()
        self.converted[_struct] = startaddr
        for name in TYPE._flds:
            addr = startaddr + layout[name]
            self.convert(getattr(_struct, name), addr, from_parent=True)
        return startaddr

    def convert_pointer(self, _ptr, inline_to_addr, from_parent):
        TYPE = lltype.typeOf(_ptr)
        if _ptr._obj is not None:
            addr = self.convert(_ptr._obj)
        else:
            addr = lladdress.NULL
        assert isinstance(addr, lladdress.address)
        if inline_to_addr is not None:
            inline_to_addr.address[0] = addr
        return simulatorptr(TYPE, addr)

    def convert_object(self, _obj, inline_to_addr, from_parent):
        if inline_to_addr is not None:
            assert 0, "can't inline function or pyobject"
        else:
            return lladdress.get_address_of_object(_obj)

class FlowGraphConstantConverter(object):
    def __init__(self, flowgraphs, gc=None):
        self.flowgraphs = flowgraphs
        self.memory = lladdress.NULL
        self.cvter = None
        self.total_size = 0
        self.gc = gc

    def collect_constants(self):
        constants = {}
        def collect_args(args):
            for arg in args:
                if (isinstance(arg, Constant) and
                    arg.concretetype is not lltype.Void):
                    constants[arg] = None
        def visit(obj):
            if isinstance(obj, Link):
                collect_args(obj.args)
                if hasattr(obj, "llexitcase"):
                    if isinstance(obj.llexitcase, IntegerRepr):
                        assert 0
                    constants[Constant(obj.llexitcase)] = None
            elif isinstance(obj, Block):
                for op in obj.operations:
                    collect_args(op.args)
        for graph in self.flowgraphs.itervalues():
            traverse(visit, graph)
        self.constants = constants

    def calculate_size(self):
        total_size = 0
        seen = {}
        candidates = [const.value for const in self.constants.iterkeys()]
        while candidates:
            cand = candidates.pop()
            if isinstance(cand, lltype._ptr):
                if cand:
                    candidates.append(cand._obj)
                continue
            elif isinstance(cand, lltype.LowLevelType):
                continue
            elif isinstance(cand, FUNCTIONTYPES):
                continue
            elif isinstance(cand, str):
                continue
            elif isinstance(lltype.typeOf(cand), lltype.Primitive):
                continue
            elif cand in seen:
                continue
            elif isinstance(cand, lltype._array):
                seen[cand] = True
                length = len(cand.items)
                total_size += sizeof(cand._TYPE, length)
                if self.gc is not None:
                    total_size += self.gc.size_gc_header()
                for item in cand.items:
                    candidates.append(item)
            elif isinstance(cand, lltype._struct):
                seen[cand] = True
                parent = cand._parentstructure()
                if parent is not None:
                    has_parent = True
                    candidates.append(parent)
                else:
                    has_parent = False
                TYPE = cand._TYPE
                if not has_parent:
                    if TYPE._arrayfld is not None:
                        total_size += sizeof(
                            TYPE, len(getattr(cand, TYPE._arrayfld).items))
                    else:
                        total_size += sizeof(TYPE)
                    if self.gc is not None:
                        total_size += self.gc.size_gc_header()
                for name in TYPE._flds:
                    candidates.append(getattr(cand, name))
            elif isinstance(cand, lltype._opaque):
                total_size += struct.calcsize("i")
            elif isinstance(cand, lltype._func):
                total_size += struct.calcsize("i")
            elif isinstance(cand, lltype._pyobject):
                total_size += struct.calcsize("i")
            else:
                assert 0, "don't know about %s %s" % (cand, cand.__class__)
        self.total_size = total_size

    def convert_constants(self):
        self.memory = lladdress.raw_malloc(self.total_size)
        self.cvter = LLTypeConverter(self.memory, self.gc)
        for constant in self.constants.iterkeys():
            if isinstance(constant.value, lltype.LowLevelType):
                self.constants[constant] = constant.value
            elif isinstance(constant.value, str):
                self.constants[constant] = constant.value
            elif isinstance(constant.value, FUNCTIONTYPES):
                self.constants[constant] = constant.value
            else:
                self.constants[constant] = self.cvter.convert(constant.value)

    def patch_graphs(self):
        def patch_consts(args):
            for arg in args:
                if isinstance(arg, Constant) and arg in self.constants:
                    arg.value = self.constants[arg]
        def visit(obj):
            if isinstance(obj, Link):
                patch_consts(obj.args)
                if (hasattr(obj, "llexitcase") and
                    Constant(obj.llexitcase) in self.constants):
                    obj.llexitcase = self.constants[Constant(obj.llexitcase)]
            elif isinstance(obj, Block):
                for op in obj.operations:
                    patch_consts(op.args)
        for graph in self.flowgraphs.itervalues():
            traverse(visit, graph)

    def convert(self):
        self.collect_constants()
        self.calculate_size()
        self.convert_constants()
        self.patch_graphs()
