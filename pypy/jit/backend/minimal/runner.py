import py
from pypy.rlib.objectmodel import specialize, we_are_translated
from pypy.rpython.lltypesystem import lltype, llmemory, rffi, rstr, rclass
from pypy.jit.metainterp.history import AbstractDescr, Box, BoxInt, BoxPtr
from pypy.jit.metainterp import executor
from pypy.jit.metainterp.resoperation import rop


class CPU(object):
    has_lltype = True
    has_ootype = False    # XXX for now

    def __init__(self, rtyper, stats, translate_support_code=False,
                 mixlevelann=None):
        self.rtyper = rtyper
        self.stats = stats
        self.translate_support_code = translate_support_code
        self.setup()

    def setup(self):
        if self.rtyper is not None:   # normal case
            bk = self.rtyper.annotator.bookkeeper
            clsdef = bk.getuniqueclassdef(OverflowError)
            ovferror_repr = rclass.getclassrepr(self.rtyper, clsdef)
            ll_inst = self.rtyper.exceptiondata.get_standard_ll_exc_instance(
                self.rtyper, clsdef)
        else:
            # for tests, a random emulated ll_inst will do
            ll_inst = lltype.malloc(rclass.OBJECT)
            ll_inst.typeptr = lltype.malloc(rclass.OBJECT_VTABLE,
                                            immortal=True)
        self._ovf_error_inst = ll_inst

    def compile_operations(self, loop):
        pass

    def execute_operations(self, loop, valueboxes):
        self.clear_exception()
        self._guard_failed = False
        while True:
            env = {}
            assert len(valueboxes) == len(loop.inputargs)
            for i in range(len(valueboxes)):
                env[loop.inputargs[i]] = valueboxes[i]
            operations = loop.operations
            i = 0
            #
            while True:
                assert i < len(operations), ("reached the end without "
                                             "seeing a final op")
                op = operations[i]
                i += 1
                argboxes = []
                for box in op.args:
                    if isinstance(box, Box):
                        box = env[box]
                    argboxes.append(box)
                if op.is_final():
                    break
                if op.is_guard():
                    try:
                        resbox = self.execute_guard(op.opnum, argboxes)
                    except GuardFailed:
                        self._guard_failed = True
                        operations = op.suboperations
                        i = 0
                        continue
                else:
                    resbox = executor._execute_nonspec(self, op.opnum,
                                                       argboxes,
                                                       op.descr)
                if op.result is not None:
                    assert resbox is not None
                    env[op.result] = resbox
                else:
                    assert resbox is None
            #
            if op.opnum == rop.JUMP:
                loop = op.jump_target
                valueboxes = argboxes
                continue
            if op.opnum == rop.FAIL:
                break
            assert 0, "bad opnum"
        #
        for i in range(len(op.args)):
            box = op.args[i]
            if isinstance(box, BoxInt):
                value = env[box].getint()
                box.changevalue_int(value)
            elif isinstance(box, BoxPtr):
                value = env[box].getptr_base()
                box.changevalue_ptr(value)
        return op

    def execute_guard(self, opnum, argboxes):
        if opnum == rop.GUARD_TRUE:
            value = argboxes[0].getint()
            if not value:
                raise GuardFailed
        elif opnum == rop.GUARD_FALSE:
            value = argboxes[0].getint()
            if value:
                raise GuardFailed
        elif opnum == rop.GUARD_CLASS:
            value = argboxes[0].getptr(rclass.OBJECTPTR)
            adr = argboxes[1].getaddr(self)
            expected_class = llmemory.cast_adr_to_ptr(adr, rclass.CLASSTYPE)
            if value.typeptr != expected_class:
                raise GuardFailed
        elif opnum == rop.GUARD_VALUE:
            value = argboxes[0].getint()
            expected_value = argboxes[1].getint()
            if value != expected_value:
                raise GuardFailed
        elif opnum == rop.GUARD_NONVIRTUALIZED:
            pass    # XXX
        elif opnum == rop.GUARD_NO_EXCEPTION:
            if self.current_exc_inst:
                raise GuardFailed
        elif opnum == rop.GUARD_EXCEPTION:
            adr = argboxes[0].getaddr(self)
            expected_class = llmemory.cast_adr_to_ptr(adr, rclass.CLASSTYPE)
            assert expected_class
            exc = self.current_exc_inst
            if exc and rclass.ll_isinstance(exc, expected_class):
                raise GuardFailed
        else:
            assert 0, "unknown guard op"

    # ----------

    def sizeof(self, TYPE):
        def alloc():
            p = lltype.malloc(TYPE)
            return lltype.cast_opaque_ptr(llmemory.GCREF, p)
        return SizeDescr(alloc)

    def fielddescrof(self, STRUCT, name):
        dict2 = base_dict.copy()
        dict2['PTR'] = lltype.Ptr(STRUCT)
        FIELDTYPE = getattr(STRUCT, name)
        dict = {'name': name,
                'input': make_reader(FIELDTYPE, 'xbox', dict2),
                'result': make_writer(FIELDTYPE, 'x', dict2)}
        exec py.code.Source("""
            def getfield(pbox):
                p = reveal_ptr(PTR, pbox)
                x = getattr(p, %(name)r)
                return %(result)s
            def setfield(pbox, xbox):
                p = reveal_ptr(PTR, pbox)
                x = %(input)s
                setattr(p, %(name)r, x)
        """ % dict).compile() in dict2
        sort_key = _count_sort_key(STRUCT, name)
        return FieldDescr(dict2['getfield'], dict2['setfield'], sort_key)

    def arraydescrof(self, ARRAY):
        dict2 = base_dict.copy()
        dict2['malloc'] = lltype.malloc
        dict2['ARRAY'] = ARRAY
        dict2['PTR'] = lltype.Ptr(ARRAY)
        dict = {'input': make_reader(ARRAY.OF, 'xbox', dict2),
                'result': make_writer(ARRAY.OF, 'x', dict2)}
        exec py.code.Source("""
            def new(length):
                p = malloc(ARRAY, length)
                return cast_opaque_ptr(GCREF, p)
            def length(pbox):
                p = reveal_ptr(PTR, pbox)
                return len(p)
            def getarrayitem(pbox, index):
                p = reveal_ptr(PTR, pbox)
                x = p[index]
                return %(result)s
            def setarrayitem(pbox, index, xbox):
                p = reveal_ptr(PTR, pbox)
                x = %(input)s
                p[index] = x
        """ % dict).compile() in dict2
        return ArrayDescr(dict2['new'],
                          dict2['length'],
                          dict2['getarrayitem'],
                          dict2['setarrayitem'])

    def calldescrof(self, ARGS, RESULT):
        dict2 = base_dict.copy()
        args = []
        for i, ARG in enumerate(ARGS):
            args.append(make_reader(ARG, 'args[%d]' % i, dict2))
        dict = {'args': ', '.join(args),
                'result': make_writer(RESULT, 'res', dict2)}
        dict2.update({'rffi': rffi,
                      'FUNC': lltype.Ptr(lltype.FuncType(ARGS, RESULT)),
                      'length': len(ARGS),
                      })
        exec py.code.Source("""
            def call(function, args):
                assert len(args) == length
                function = rffi.cast(FUNC, function)
                res = function(%(args)s)
                return %(result)s
        """ % dict).compile() in dict2
        if RESULT is lltype.Void:
            errbox = None
        elif isinstance(RESULT, lltype.Ptr) and RESULT.TO._gckind == 'gc':
            errbox = BoxPtr()
        else:
            errbox = BoxInt()
        return CallDescr(dict2['FUNC'], dict2['call'], errbox)

    # ----------

    def do_new(self, args, sizedescr):
        assert isinstance(sizedescr, SizeDescr)
        assert sizedescr.alloc is not None
        p = sizedescr.alloc()
        return BoxPtr(p)

    do_new_with_vtable = do_new

    def do_getfield_gc(self, args, fielddescr):
        assert isinstance(fielddescr, FieldDescr)
        assert fielddescr.getfield is not None
        return fielddescr.getfield(args[0])

    do_getfield_raw = do_getfield_gc

    def do_setfield_gc(self, args, fielddescr):
        assert isinstance(fielddescr, FieldDescr)
        assert fielddescr.setfield is not None
        fielddescr.setfield(args[0], args[1])

    do_setfield_raw = do_setfield_gc

    def do_new_array(self, args, arraydescr):
        assert isinstance(arraydescr, ArrayDescr)
        assert arraydescr.new is not None
        p = arraydescr.new(args[0].getint())
        return BoxPtr(p)

    def do_arraylen_gc(self, args, arraydescr):
        assert isinstance(arraydescr, ArrayDescr)
        assert arraydescr.length is not None
        return BoxInt(arraydescr.length(args[0]))

    do_arraylen_raw = do_arraylen_gc

    def do_getarrayitem_gc(self, args, arraydescr):
        assert isinstance(arraydescr, ArrayDescr)
        assert arraydescr.getarrayitem is not None
        index = args[1].getint()
        return arraydescr.getarrayitem(args[0], index)
    do_getarrayitem_raw = do_getarrayitem_gc

    def do_setarrayitem_gc(self, args, arraydescr):
        assert isinstance(arraydescr, ArrayDescr)
        assert arraydescr.setarrayitem is not None
        index = args[1].getint()
        arraydescr.setarrayitem(args[0], index, args[2])

    do_setarrayitem_raw = do_setarrayitem_gc

    def do_newstr(self, args, descr=None):
        p = rstr.mallocstr(args[0].getint())
        return BoxPtr(lltype.cast_opaque_ptr(llmemory.GCREF, p))

    def do_newunicode(self, args, descr=None):
        p = rstr.mallocunicode(args[0].getint())
        return BoxPtr(lltype.cast_opaque_ptr(llmemory.GCREF, p))

    def do_strlen(self, args, descr=None):
        str = args[0].getptr(lltype.Ptr(rstr.STR))
        return BoxInt(len(str.chars))

    def do_unicodelen(self, args, descr=None):
        unicode = args[0].getptr(lltype.Ptr(rstr.UNICODE))
        return BoxInt(len(unicode.chars))

    def do_strgetitem(self, args, descr=None):
        str = args[0].getptr(lltype.Ptr(rstr.STR))
        i = args[1].getint()
        return BoxInt(ord(str.chars[i]))

    def do_unicodegetitem(self, args, descr=None):
        unicode = args[0].getptr(lltype.Ptr(rstr.UNICODE))
        i = args[1].getint()
        return BoxInt(ord(unicode.chars[i]))

    def do_strsetitem(self, args, descr=None):
        str = args[0].getptr(lltype.Ptr(rstr.STR))
        i = args[1].getint()
        str.chars[i] = chr(args[2].getint())

    def do_unicodesetitem(self, args, descr=None):
        unicode = args[0].getptr(lltype.Ptr(rstr.UNICODE))
        i = args[1].getint()
        unicode.chars[i] = unichr(args[2].getint())

    def do_cast_int_to_ptr(self, args, descr=None):
        return BoxPtr(self.cast_int_to_gcref(args[0].getint()))

    def do_cast_ptr_to_int(self, args, descr=None):
        return BoxInt(self.cast_gcref_to_int(args[0].getptr_base()))

    def do_call(self, args, calldescr):
        if not we_are_translated():
            py.test.skip("call not supported in non-translated version")
        assert isinstance(calldescr, CallDescr)
        assert calldescr.call is not None
        self.clear_exception()
        try:
            return calldescr.call(args[0].getaddr(self), args[1:])
        except Exception, e:
            from pypy.rpython.annlowlevel import cast_instance_to_base_ptr
            self.current_exc_inst = cast_instance_to_base_ptr(e)
            box = calldescr.errbox
            if box:
                box = box.clonebox()
            return box

    # ----------

    def clear_exception(self):
        self.current_exc_inst = lltype.nullptr(rclass.OBJECT)

    def get_exception(self):
        if self.current_exc_inst:
            return rffi.cast(lltype.Signed, self.current_exc_inst.typeptr)
        else:
            return 0

    def get_exc_value(self):
        return lltype.cast_opaque_ptr(llmemory.GCREF, self.current_exc_inst)

    def set_overflow_error(self):
        self.current_exc_inst = self._ovf_error_inst

    def guard_failed(self):
        return self._guard_failed

    # ----------

    def cast_gcref_to_int(self, x):
        return rffi.cast(lltype.Signed, x)

    def cast_int_to_gcref(self, x):
        return rffi.cast(llmemory.GCREF, x)

    def cast_int_to_adr(self, x):
        return rffi.cast(llmemory.Address, x)

    def cast_adr_to_int(self, x):
        return rffi.cast(lltype.Signed, x)

    @specialize.arg(1)
    def cast_int_to_ptr(self, TYPE, x):
        return rffi.cast(TYPE, x)


class SizeDescr(AbstractDescr):
    alloc = None
    def __init__(self, alloc):
        self.alloc = alloc

class FieldDescr(AbstractDescr):
    getfield = None
    setfield = None
    _sort_key = 0
    def __init__(self, getfield, setfield, sort_key):
        self.getfield = getfield
        self.setfield = setfield
        self._sort_key = sort_key
    def sort_key(self):
        return self._sort_key

class ArrayDescr(AbstractDescr):
    new = None
    length = None
    getarrayitem = None
    setarrayitem = None
    def __init__(self, new, length, getarrayitem, setarrayitem):
        self.new = new
        self.length = length
        self.getarrayitem = getarrayitem
        self.setarrayitem = setarrayitem

class CallDescr(AbstractDescr):
    call = None
    errbox = None
    def __init__(self, FUNC, call, errbox):
        self.FUNC = FUNC    # only for debugging
        self.call = call
        self.errbox = errbox

# ____________________________________________________________


def _name(dict, obj):
    name = '_n%d' % len(dict)
    dict[name] = obj
    return name

def make_reader(TYPE, boxstr, dict):
    if TYPE is lltype.Void:
        return "None"
    elif isinstance(TYPE, lltype.Ptr) and TYPE.TO._gckind == 'gc':
        return "%s.getptr(%s)" % (boxstr, _name(dict, TYPE))
    else:
        return "cast_primitive(%s, %s.getint())" % (_name(dict, TYPE), boxstr)

def make_writer(TYPE, str, dict):
    if TYPE is lltype.Void:
        return "None"
    elif isinstance(TYPE, lltype.Ptr) and TYPE.TO._gckind == 'gc':
        return "BoxPtr(cast_opaque_ptr(GCREF, %s))" % (str,)
    else:
        return "BoxInt(cast_primitive(Signed, %s))" % (str,)

def _count_sort_key(STRUCT, name):
    i = list(STRUCT._names).index(name)
    while True:
        _, STRUCT = STRUCT._first_struct()
        if not STRUCT:
            return i
        i += len(STRUCT._names) + 1

@specialize.arg(0)
def reveal_ptr(PTR, box):
    if PTR.TO._gckind == 'gc':
        return box.getptr(PTR)
    else:
        adr = rffi.cast(llmemory.Address, box.getint())
        return llmemory.cast_adr_to_ptr(adr, PTR)

base_dict = {
    'cast_primitive': lltype.cast_primitive,
    'cast_opaque_ptr': lltype.cast_opaque_ptr,
    'reveal_ptr': reveal_ptr,
    'GCREF': llmemory.GCREF,
    'Signed': lltype.Signed,
    'BoxInt': BoxInt,
    'BoxPtr': BoxPtr,
    }

class GuardFailed(Exception):
    pass

import pypy.jit.metainterp.executor
pypy.jit.metainterp.executor.make_execute_list(CPU)
