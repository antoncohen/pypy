"""
The non-RPythonic part of the llgraph backend.
This contains all the code that is directly run
when executing on top of the llinterpreter.
"""

import sys
from pypy.objspace.flow.model import Variable, Constant
from pypy.annotation import model as annmodel
from pypy.jit.metainterp.history import (ConstInt, ConstPtr, ConstAddr,
                                         BoxInt, BoxPtr)
from pypy.rpython.lltypesystem import lltype, llmemory, rclass, rstr
from pypy.rpython.lltypesystem import lloperation
from pypy.rpython.ootypesystem import ootype
from pypy.rpython.module.support import LLSupport, OOSupport
from pypy.rpython.llinterp import LLException
from pypy.rpython.extregistry import ExtRegistryEntry

from pypy.jit.metainterp import heaptracker, resoperation, executor
from pypy.jit.metainterp.resoperation import rop
from pypy.jit.backend.llgraph import symbolic

from pypy.rlib.objectmodel import ComputedIntSymbolic
from pypy.rlib.rarithmetic import r_uint, intmask

import py
from pypy.tool.ansi_print import ansi_log
log = py.log.Producer('runner')
py.log.setconsumer('runner', ansi_log)


def _from_opaque(opq):
    return opq._obj.externalobj

_TO_OPAQUE = {}

def _to_opaque(value):
    return lltype.opaqueptr(_TO_OPAQUE[value.__class__], 'opaque',
                            externalobj=value)

def from_opaque_string(s):
    if isinstance(s, str):
        return s
    elif isinstance(s, ootype._string):
        return OOSupport.from_rstr(s)
    else:
        return LLSupport.from_rstr(s)

# a list of argtypes of all operations - couldn't find any and it's
# very useful
TYPES = {
    'int_add'         : (('int', 'int'), 'int'),
    'int_sub'         : (('int', 'int'), 'int'),
    'int_mul'         : (('int', 'int'), 'int'),
    'int_floordiv'    : (('int', 'int'), 'int'),
    'int_mod'         : (('int', 'int'), 'int'),
    'int_and'         : (('int', 'int'), 'int'),
    'int_or'          : (('int', 'int'), 'int'),
    'int_xor'         : (('int', 'int'), 'int'),
    'int_lshift'      : (('int', 'int'), 'int'),
    'int_rshift'      : (('int', 'int'), 'int'),
    'int_lt'          : (('int', 'int'), 'bool'),
    'int_gt'          : (('int', 'int'), 'bool'),
    'int_ge'          : (('int', 'int'), 'bool'),
    'int_le'          : (('int', 'int'), 'bool'),
    'int_eq'          : (('int', 'int'), 'bool'),
    'int_ne'          : (('int', 'int'), 'bool'),
    'int_is_true'     : (('int',), 'bool'),
    'int_neg'         : (('int',), 'int'),
    'int_invert'      : (('int',), 'int'),
    'int_add_ovf'     : (('int', 'int'), 'int'),
    'int_mod_ovf'     : (('int', 'int'), 'int'),
    'int_sub_ovf'     : (('int', 'int'), 'int'),
    'int_mul_ovf'     : (('int', 'int'), 'int'),
    'int_neg_ovf'     : (('int',), 'int'),
    'bool_not'        : (('bool',), 'bool'),
    'uint_add'        : (('int', 'int'), 'int'),
    'uint_sub'        : (('int', 'int'), 'int'),
    'uint_mul'        : (('int', 'int'), 'int'),
    'uint_lt'         : (('int', 'int'), 'bool'),
    'uint_le'         : (('int', 'int'), 'bool'),
    'uint_eq'         : (('int', 'int'), 'bool'),
    'uint_ne'         : (('int', 'int'), 'bool'),
    'uint_gt'         : (('int', 'int'), 'bool'),
    'uint_ge'         : (('int', 'int'), 'bool'),
    'new_with_vtable' : (('ptr',), 'ptr'),
    'new'             : ((), 'ptr'),
    'new_array'       : (('int',), 'ptr'),
    'oononnull'       : (('ptr',), 'bool'),
    'ooisnull'        : (('ptr',), 'bool'),
    'oois'            : (('ptr', 'ptr'), 'bool'),
    'ooisnot'         : (('ptr', 'ptr'), 'bool'),
    'setfield_gc'     : (('ptr', 'intorptr'), None),
    'getfield_gc'     : (('ptr',), 'intorptr'),
    'getfield_gc_pure': (('ptr',), 'intorptr'),
    'setfield_raw'    : (('ptr', 'intorptr'), None),
    'getfield_raw'    : (('ptr',), 'intorptr'),
    'getfield_raw_pure': (('ptr',), 'intorptr'),
    'setarrayitem_gc' : (('ptr', 'int', 'intorptr'), None),
    'getarrayitem_gc' : (('ptr', 'int'), 'intorptr'),
    'getarrayitem_gc_pure' : (('ptr', 'int'), 'intorptr'),
    'arraylen_gc'     : (('ptr',), 'int'),
    'call'            : (('ptr', 'varargs'), 'intorptr'),
    'call_pure'       : (('ptr', 'varargs'), 'intorptr'),
    'guard_true'      : (('bool',), None),
    'guard_false'     : (('bool',), None),
    'guard_value'     : (('int', 'int'), None),
    'guard_class'     : (('ptr', 'ptr'), None),
    'guard_no_exception'   : ((), None),
    'guard_exception'      : (('ptr',), 'ptr'),
    'guard_nonvirtualized' : (('ptr', 'ptr'), None),
    'newstr'          : (('int',), 'ptr'),
    'strlen'          : (('ptr',), 'int'),
    'strgetitem'      : (('ptr', 'int'), 'int'),
    'strsetitem'      : (('ptr', 'int', 'int'), None),
    #'getitem'         : (('void', 'ptr', 'int'), 'int'),
    #'setitem'         : (('void', 'ptr', 'int', 'int'), None),
    #'newlist'         : (('void', 'varargs'), 'ptr'),
    #'append'          : (('void', 'ptr', 'int'), None),
    #'insert'          : (('void', 'ptr', 'int', 'int'), None),
    #'pop'             : (('void', 'ptr',), 'int'),
    #'len'             : (('void', 'ptr',), 'int'),
    #'listnonzero'     : (('void', 'ptr',), 'int'),
}

# ____________________________________________________________

class LoopOrBridge(object):
    def __init__(self):
        self.operations = []

    def __repr__(self):
        lines = ['\t' + repr(op) for op in self.operations]
        lines.insert(0, 'LoopOrBridge:')
        return '\n'.join(lines)

class Operation(object):
    def __init__(self, opnum, descr):
        self.opnum = opnum
        self.args = []
        self.result = None
        self.descr = descr
        self.livevars = []   # for guards only

    def __repr__(self):
        if self.result is not None:
            sres = repr0(self.result) + ' = '
        else:
            sres = ''
        return '{%s%s(%s)}' % (sres, self.getopname(),
                               ', '.join(map(repr0, self.args)))

    def getopname(self):
        try:
            return resoperation.opname[self.opnum]
        except KeyError:
            return '<%d>' % self.opnum

def repr0(x):
    if isinstance(x, list):
        return '[' + ', '.join(repr0(y) for y in x) + ']'
    elif isinstance(x, Constant):
        return '(' + repr0(x.value) + ')'
    elif isinstance(x, lltype._ptr):
        x = llmemory.cast_ptr_to_adr(x)
        if x.ptr:
            try:
                container = x.ptr._obj._normalizedcontainer()
                return '* %s' % (container._TYPE._short_name(),)
            except AttributeError:
                return repr(x)
        else:
            return 'NULL'
    else:
        return repr(x)

def repr_list(lst, types, memocast):
    res_l = []
    if types and types[-1] == 'varargs':
        types = types[:-1] + ('int',) * (len(lst) - len(types) + 1)
    assert len(types) == len(lst)
    for elem, tp in zip(lst, types):
        if isinstance(elem, Constant):
            res_l.append('(%s)' % repr1(elem, tp, memocast))
        else:
            res_l.append(repr1(elem, tp, memocast))
    return '[%s]' % (', '.join(res_l))

def repr1(x, tp, memocast):
    if tp == "intorptr":
        TYPE = lltype.typeOf(x)
        if isinstance(TYPE, lltype.Ptr) and TYPE.TO._gckind == 'gc':
            tp = "ptr"
        else:
            tp = "int"
    if tp == 'int':
        return str(x)
    elif tp == 'void':
        return '---'
    elif tp == 'ptr':
        if not x:
            return '(* None)'
        if isinstance(x, int):
            # XXX normalize?
            ptr = str(cast_int_to_adr(memocast, x))
        else:
            if getattr(x, '_fake', None):
                return repr(x)
            if lltype.typeOf(x) == llmemory.GCREF:
                TP = lltype.Ptr(lltype.typeOf(x._obj.container))
                ptr = lltype.cast_opaque_ptr(TP, x)
            else:
                ptr = x
        try:
            container = ptr._obj._normalizedcontainer()
            return '(* %s)' % (container._TYPE._short_name(),)
        except AttributeError:
            return '(%r)' % (ptr,)
    elif tp == 'bool':
        assert x == 0 or x == 1
        return str(bool(x))
    #elif tp == 'fieldname':
    #    return str(symbolic.TokenToField[x/2][1])
    else:
        raise NotImplementedError("tp = %s" % tp)

_variables = []

def compile_start():
    del _variables[:]
    return _to_opaque(LoopOrBridge())

def compile_start_int_var(loop):
    loop = _from_opaque(loop)
    assert not loop.operations
    v = Variable()
    v.concretetype = lltype.Signed
    r = len(_variables)
    _variables.append(v)
    return r

def compile_start_ptr_var(loop):
    loop = _from_opaque(loop)
    assert not loop.operations
    v = Variable()
    v.concretetype = llmemory.GCREF
    r = len(_variables)
    _variables.append(v)
    return r

def compile_add(loop, opnum, descr):
    loop = _from_opaque(loop)
    loop.operations.append(Operation(opnum, descr))

def compile_add_var(loop, intvar):
    loop = _from_opaque(loop)
    op = loop.operations[-1]
    op.args.append(_variables[intvar])

def compile_add_int_const(loop, value):
    loop = _from_opaque(loop)
    const = Constant(value)
    const.concretetype = lltype.Signed
    op = loop.operations[-1]
    op.args.append(const)

def compile_add_ptr_const(loop, value):
    loop = _from_opaque(loop)
    const = Constant(value)
    const.concretetype = llmemory.GCREF
    op = loop.operations[-1]
    op.args.append(const)

def compile_add_int_result(loop):
    loop = _from_opaque(loop)
    v = Variable()
    v.concretetype = lltype.Signed
    op = loop.operations[-1]
    op.result = v
    r = len(_variables)
    _variables.append(v)
    return r

def compile_add_ptr_result(loop):
    loop = _from_opaque(loop)
    v = Variable()
    v.concretetype = llmemory.GCREF
    op = loop.operations[-1]
    op.result = v
    r = len(_variables)
    _variables.append(v)
    return r

def compile_add_jump_target(loop, loop_target, loop_target_index):
    loop = _from_opaque(loop)
    loop_target = _from_opaque(loop_target)
    op = loop.operations[-1]
    op.jump_target = loop_target
    op.jump_target_index = loop_target_index
    if op.opnum == rop.JUMP:
        if loop_target == loop and loop_target_index == 0:
            log.info("compiling new loop")
        else:
            log.info("compiling new bridge")

def compile_add_failnum(loop, failnum):
    loop = _from_opaque(loop)
    op = loop.operations[-1]
    op.failnum = failnum

def compile_add_livebox(loop, intvar):
    loop = _from_opaque(loop)
    op = loop.operations[-1]
    op.livevars.append(_variables[intvar])

def compile_from_guard(loop, guard_loop, guard_opindex):
    loop = _from_opaque(loop)
    guard_loop = _from_opaque(guard_loop)
    op = guard_loop.operations[guard_opindex]
    assert rop._GUARD_FIRST <= op.opnum <= rop._GUARD_LAST
    op.jump_target = loop
    op.jump_target_index = 0

# ------------------------------

class Frame(object):
    OPHANDLERS = [None] * (rop._LAST+1)

    def __init__(self, memocast):
        self.verbose = False
        self.memocast = memocast

    def getenv(self, v):
        if isinstance(v, Constant):
            return v.value
        else:
            return self.env[v]

    def go_to_merge_point(self, loop, opindex, args):
        mp = loop.operations[opindex]
        assert len(mp.args) == len(args)
        self.loop = loop
        self.opindex = opindex
        self.env = dict(zip(mp.args, args))

    def execute(self):
        """Execute all operations in a loop,
        possibly following to other loops as well.
        """
        global _last_exception
        assert _last_exception is None, "exception left behind"
        verbose = True
        while True:
            self.opindex += 1
            op = self.loop.operations[self.opindex]
            args = [self.getenv(v) for v in op.args]
            if op.opnum == rop.MERGE_POINT:
                self.go_to_merge_point(self.loop, self.opindex, args)
                continue
            if op.opnum == rop.JUMP:
                self.go_to_merge_point(op.jump_target,
                                       op.jump_target_index,
                                       args)
                _stats.exec_jumps += 1
                continue
            try:
                result = self.execute_operation(op.opnum, args, op.descr,
                                                verbose)
                #verbose = self.verbose
                assert (result is None) == (op.result is None)
                if op.result is not None:
                    RESTYPE = op.result.concretetype
                    if RESTYPE is lltype.Signed:
                        x = self.as_int(result)
                    elif RESTYPE is llmemory.GCREF:
                        x = self.as_ptr(result)
                    else:
                        raise Exception("op.result.concretetype is %r"
                                        % (RESTYPE,))
                    self.env[op.result] = x
            except GuardFailed:
                if hasattr(op, 'jump_target'):
                    # the guard already failed once, go to the
                    # already-generated code
                    catch_op = op.jump_target.operations[0]
                    assert catch_op.opnum == rop.CATCH
                    args = []
                    it = iter(op.livevars)
                    for v in catch_op.args:
                        if isinstance(v, Variable):
                            args.append(self.getenv(it.next()))
                        else:
                            args.append(v)
                    assert list(it) == []
                    self.go_to_merge_point(op.jump_target,
                                           op.jump_target_index,
                                           args)
                else:
                    if self.verbose:
                        log.trace('failed: %s(%s)' % (
                            opname, ', '.join(map(str, args))))
                    self.failed_guard_op = op
                    return op.failnum

    def execute_operation(self, opnum, values, descr, verbose):
        """Execute a single operation.
        """
        ophandler = self.OPHANDLERS[opnum]
        if ophandler is None:
            self._define_impl(opnum)
            ophandler = self.OPHANDLERS[opnum]
            assert ophandler is not None, "missing impl for op %d" % opnum
        opname = resoperation.opname[opnum].lower()
        exec_counters = _stats.exec_counters
        exec_counters[opname] = exec_counters.get(opname, 0) + 1
        for i in range(len(values)):
            if isinstance(values[i], ComputedIntSymbolic):
                values[i] = values[i].compute_fn()
        res = ophandler(self, descr, *values)
        if verbose:
            argtypes, restype = TYPES[opname]
            if res is None:
                resdata = ''
            else:
                resdata = '-> ' + repr1(res, restype, self.memocast)
            # fish the types
            log.cpu('\t%s %s %s' % (opname, repr_list(values, argtypes,
                                                      self.memocast), resdata))
        return res

    def as_int(self, x):
        return cast_to_int(x, self.memocast)

    def as_ptr(self, x):
        return cast_to_ptr(x)

    def log_progress(self):
        count = sum(_stats.exec_counters.values())
        count_jumps = _stats.exec_jumps
        log.trace('ran %d operations, %d jumps' % (count, count_jumps))

    # ----------

    def _define_impl(self, opnum):
        opname = resoperation.opname[opnum]
        try:
            op = getattr(Frame, 'op_' + opname.lower())   # op_guard_true etc.
        except AttributeError:
            name = 'do_' + opname.lower()
            try:
                impl = globals()[name]                    # do_arraylen_gc etc.
                def op(self, descr, *args):
                    return impl(descr, *args)
                #
            except KeyError:
                from pypy.jit.backend.llgraph import llimpl
                impl = getattr(executor, name)            # do_int_add etc.
                def _op_default_implementation(self, descr, *args):
                    # for all operations implemented in execute.py
                    boxedargs = []
                    for x in args:
                        if type(x) is int:
                            boxedargs.append(BoxInt(x))
                        else:
                            boxedargs.append(BoxPtr(x))
                    # xxx this passes the 'llimpl' module as the CPU argument
                    resbox = impl(llimpl, boxedargs)
                    return resbox.value
                op = _op_default_implementation
                #
        Frame.OPHANDLERS[opnum] = op

    def op_guard_true(self, _, value):
        if not value:
            raise GuardFailed

    def op_guard_false(self, _, value):
        if value:
            raise GuardFailed

    def op_guard_class(self, _, value, expected_class):
        value = lltype.cast_opaque_ptr(rclass.OBJECTPTR, value)
        expected_class = llmemory.cast_adr_to_ptr(
            cast_int_to_adr(self.memocast, expected_class),
            rclass.CLASSTYPE)
        if value.typeptr != expected_class:
            raise GuardFailed

    def op_guard_value(self, _, value, expected_value):
        if value != expected_value:
            raise GuardFailed

    def op_guard_nonvirtualized(self, for_accessing_field,
                                value, expected_class):
        self.op_guard_class(-1, value, expected_class)
        if heaptracker.cast_vable(value).vable_rti:
            raise GuardFailed    # some other code is already in control

    def op_guard_no_exception(self, _):
        if _last_exception:
            raise GuardFailed

    def op_guard_exception(self, _, expected_exception):
        global _last_exception
        expected_exception = llmemory.cast_adr_to_ptr(
            cast_int_to_adr(self.memocast, expected_exception),
            rclass.CLASSTYPE)
        assert expected_exception
        exc = _last_exception
        if exc:
            got = exc.args[0]
            if not rclass.ll_issubclass(got, expected_exception):
                raise GuardFailed
            _last_exception = None
            return exc.args[1]
        else:
            raise GuardFailed

    # ----------
    # delegating to the builtins do_xxx() (done automatically for simple cases)

    def op_getarrayitem_gc(self, arraydescr, array, index):
        if arraydescr & 1:
            return do_getarrayitem_gc_ptr(array, index)
        else:
            return do_getarrayitem_gc_int(array, index, self.memocast)

    def op_getfield_gc(self, fielddescr, struct):
        if fielddescr & 1:
            return do_getfield_gc_ptr(struct, fielddescr)
        else:
            return do_getfield_gc_int(struct, fielddescr, self.memocast)

    def op_getfield_raw(self, fielddescr, struct):
        if fielddescr & 1:
            return do_getfield_raw_ptr(struct, fielddescr)
        else:
            return do_getfield_raw_int(struct, fielddescr, self.memocast)

    def op_new_with_vtable(self, size, vtable):
        result = do_new(size)
        value = lltype.cast_opaque_ptr(rclass.OBJECTPTR, result)
        value.typeptr = cast_from_int(rclass.CLASSTYPE, vtable, self.memocast)
        return result

    def op_setarrayitem_gc(self, arraydescr, array, index, newvalue):
        if arraydescr & 1:
            do_setarrayitem_gc_ptr(array, index, newvalue)
        else:
            do_setarrayitem_gc_int(array, index, newvalue, self.memocast)

    def op_setfield_gc(self, fielddescr, struct, newvalue):
        if fielddescr & 1:
            do_setfield_gc_ptr(struct, fielddescr, newvalue)
        else:
            do_setfield_gc_int(struct, fielddescr, newvalue, self.memocast)

    def op_setfield_raw(self, fielddescr, struct, newvalue):
        if fielddescr & 1:
            do_setfield_raw_ptr(struct, fielddescr, newvalue)
        else:
            do_setfield_raw_int(struct, fielddescr, newvalue, self.memocast)

    def op_call(self, calldescr, func, *args):
        _call_args[:] = args
        if calldescr == sys.maxint:
            err_result = None
        elif calldescr & 1:
            err_result = lltype.nullptr(llmemory.GCREF.TO)
        else:
            err_result = 0
        return _do_call_common(func, self.memocast, err_result)

# ____________________________________________________________

def cast_to_int(x, memocast):
    TP = lltype.typeOf(x)
    if isinstance(TP, lltype.Ptr):
        assert TP.TO._gckind == 'raw'
        return cast_adr_to_int(memocast, llmemory.cast_ptr_to_adr(x))
    if TP == llmemory.Address:
        return cast_adr_to_int(memocast, x)
    return lltype.cast_primitive(lltype.Signed, x)

def cast_from_int(TYPE, x, memocast):
    if isinstance(TYPE, lltype.Ptr):
        assert TYPE.TO._gckind == 'raw'
        return llmemory.cast_adr_to_ptr(cast_int_to_adr(memocast, x), TYPE)
    elif TYPE == llmemory.Address:
        return cast_int_to_adr(memocast, x)
    else:
        return lltype.cast_primitive(TYPE, x)

def cast_to_ptr(x):
    assert isinstance(lltype.typeOf(x), lltype.Ptr)
    return lltype.cast_opaque_ptr(llmemory.GCREF, x)

def cast_from_ptr(TYPE, x):
    return lltype.cast_opaque_ptr(TYPE, x)


def new_frame(memocast):
    frame = Frame(memocast)
    return _to_opaque(frame)

def frame_clear(frame, loop, opindex):
    frame = _from_opaque(frame)
    loop = _from_opaque(loop)
    frame.loop = loop
    frame.opindex = opindex
    frame.env = {}

def frame_add_int(frame, value):
    frame = _from_opaque(frame)
    i = len(frame.env)
    mp = frame.loop.operations[0]
    frame.env[mp.args[i]] = value

def frame_add_ptr(frame, value):
    frame = _from_opaque(frame)
    i = len(frame.env)
    mp = frame.loop.operations[0]
    frame.env[mp.args[i]] = value

def frame_execute(frame):
    frame = _from_opaque(frame)
    if frame.verbose:
        mp = frame.loop.operations[0]
        values = [frame.env[v] for v in mp.args]
        log.trace('Entering CPU frame <- %r' % (values,))
    try:
        result = frame.execute()
        if frame.verbose:
            log.trace('Leaving CPU frame -> #%d' % (result,))
            frame.log_progress()
    except Exception, e:
        log.ERROR('%s in CPU frame: %s' % (e.__class__.__name__, e))
        import sys, pdb; pdb.post_mortem(sys.exc_info()[2])
        raise
    return result

def frame_int_getvalue(frame, num):
    frame = _from_opaque(frame)
    return frame.env[frame.failed_guard_op.livevars[num]]

def frame_ptr_getvalue(frame, num):
    frame = _from_opaque(frame)
    return frame.env[frame.failed_guard_op.livevars[num]]

def frame_int_setvalue(frame, num, value):
    frame = _from_opaque(frame)
    frame.env[frame.loop.operations[0].args[num]] = value

def frame_ptr_setvalue(frame, num, value):
    frame = _from_opaque(frame)
    frame.env[frame.loop.operations[0].args[num]] = value

def frame_int_getresult(frame):
    frame = _from_opaque(frame)
    return frame.returned_value

def frame_ptr_getresult(frame):
    frame = _from_opaque(frame)
    return frame.returned_value

_last_exception = None

def get_exception():
    if _last_exception:
        return llmemory.cast_ptr_to_adr(_last_exception.args[0])
    else:
        return llmemory.NULL

def get_exc_value():
    if _last_exception:
        return lltype.cast_opaque_ptr(llmemory.GCREF, _last_exception.args[1])
    else:
        return lltype.nullptr(llmemory.GCREF.TO)

def clear_exception():
    global _last_exception
    _last_exception = None

def set_overflow_error():
    global _last_exception
    llframe = _llinterp.frame_class(None, None, _llinterp)
    try:
        llframe.make_llexception(OverflowError())
    except LLException, e:
        _last_exception = e
    else:
        assert 0, "should have raised"

class MemoCast(object):
    def __init__(self):
        self.addresses = [llmemory.NULL]
        self.rev_cache = {}

def new_memo_cast():
    memocast = MemoCast()
    return _to_opaque(memocast)

def cast_adr_to_int(memocast, adr):
    # xxx slow
    assert lltype.typeOf(adr) == llmemory.Address
    memocast = _from_opaque(memocast)
    addresses = memocast.addresses
    for i in xrange(len(addresses)-1, -1, -1):
        if addresses[i] == adr:
            return i
    i = len(addresses)
    addresses.append(adr)
    return i

def cast_int_to_adr(memocast, int):
    memocast = _from_opaque(memocast)
    assert 0 <= int < len(memocast.addresses)
    return memocast.addresses[int]

class GuardFailed(Exception):
    pass

# ____________________________________________________________


def do_arraylen_gc(arraydescr, array):
    array = array._obj.container
    return array.getlength()

def do_strlen(_, string):
    str = lltype.cast_opaque_ptr(lltype.Ptr(rstr.STR), string)
    return len(str.chars)

def do_strgetitem(_, string, index):
    str = lltype.cast_opaque_ptr(lltype.Ptr(rstr.STR), string)
    return ord(str.chars[index])

def do_getarrayitem_gc_int(array, index, memocast):
    array = array._obj.container
    return cast_to_int(array.getitem(index), memocast)

def do_getarrayitem_gc_ptr(array, index):
    array = array._obj.container
    return cast_to_ptr(array.getitem(index))

def do_getfield_gc_int(struct, fielddesc, memocast):
    STRUCT, fieldname = symbolic.TokenToField[fielddesc/2]
    ptr = lltype.cast_opaque_ptr(lltype.Ptr(STRUCT), struct)
    x = getattr(ptr, fieldname)
    return cast_to_int(x, memocast)

def do_getfield_gc_ptr(struct, fielddesc):
    STRUCT, fieldname = symbolic.TokenToField[fielddesc/2]
    ptr = lltype.cast_opaque_ptr(lltype.Ptr(STRUCT), struct)
    x = getattr(ptr, fieldname)
    return cast_to_ptr(x)

def do_getfield_raw_int(struct, fielddesc, memocast):
    STRUCT, fieldname = symbolic.TokenToField[fielddesc/2]
    ptr = llmemory.cast_adr_to_ptr(struct, lltype.Ptr(STRUCT))
    x = getattr(ptr, fieldname)
    return cast_to_int(x, memocast)

def do_getfield_raw_ptr(struct, fielddesc):
    STRUCT, fieldname = symbolic.TokenToField[fielddesc/2]
    ptr = llmemory.cast_adr_to_ptr(struct, lltype.Ptr(STRUCT))
    x = getattr(ptr, fieldname)
    return cast_to_ptr(x)

def do_new(size):
    TYPE = symbolic.Size2Type[size]
    x = lltype.malloc(TYPE)
    return cast_to_ptr(x)

def do_new_array(arraydesc, count):
    TYPE = symbolic.Size2Type[arraydesc/2]
    x = lltype.malloc(TYPE, count)
    return cast_to_ptr(x)

def do_setarrayitem_gc_int(array, index, newvalue, memocast):
    array = array._obj.container
    ITEMTYPE = lltype.typeOf(array).OF
    newvalue = cast_from_int(ITEMTYPE, newvalue, memocast)
    array.setitem(index, newvalue)

def do_setarrayitem_gc_ptr(array, index, newvalue):
    array = array._obj.container
    ITEMTYPE = lltype.typeOf(array).OF
    newvalue = cast_from_ptr(ITEMTYPE, newvalue)
    array.setitem(index, newvalue)

def do_setfield_gc_int(struct, fielddesc, newvalue, memocast):
    STRUCT, fieldname = symbolic.TokenToField[fielddesc/2]
    ptr = lltype.cast_opaque_ptr(lltype.Ptr(STRUCT), struct)
    FIELDTYPE = getattr(STRUCT, fieldname)
    newvalue = cast_from_int(FIELDTYPE, newvalue, memocast)
    setattr(ptr, fieldname, newvalue)

def do_setfield_gc_ptr(struct, fielddesc, newvalue):
    STRUCT, fieldname = symbolic.TokenToField[fielddesc/2]
    ptr = lltype.cast_opaque_ptr(lltype.Ptr(STRUCT), struct)
    FIELDTYPE = getattr(STRUCT, fieldname)
    newvalue = cast_from_ptr(FIELDTYPE, newvalue)
    setattr(ptr, fieldname, newvalue)

def do_setfield_raw_int(struct, fielddesc, newvalue, memocast):
    STRUCT, fieldname = symbolic.TokenToField[fielddesc/2]
    ptr = llmemory.cast_adr_to_ptr(struct, lltype.Ptr(STRUCT))
    FIELDTYPE = getattr(STRUCT, fieldname)
    newvalue = cast_from_int(FIELDTYPE, newvalue, memocast)
    setattr(ptr, fieldname, newvalue)

def do_setfield_raw_ptr(struct, fielddesc, newvalue):
    STRUCT, fieldname = symbolic.TokenToField[fielddesc/2]
    ptr = llmemory.cast_adr_to_ptr(struct, lltype.Ptr(STRUCT))
    FIELDTYPE = getattr(STRUCT, fieldname)
    newvalue = cast_from_ptr(FIELDTYPE, newvalue)
    setattr(ptr, fieldname, newvalue)

def do_newstr(_, length):
    x = rstr.mallocstr(length)
    return cast_to_ptr(x)

def do_strsetitem(_, string, index, newvalue):
    str = lltype.cast_opaque_ptr(lltype.Ptr(rstr.STR), string)
    str.chars[index] = chr(newvalue)

# ---------- call ----------

_call_args = []

def do_call_pushint(x):
    _call_args.append(x)

def do_call_pushptr(x):
    _call_args.append(x)

def _do_call_common(f, memocast, err_result=None):
    global _last_exception
    assert _last_exception is None, "exception left behind"
    ptr = cast_int_to_adr(memocast, f).ptr
    FUNC = lltype.typeOf(ptr).TO
    ARGS = FUNC.ARGS
    args = []
    nextitem = iter(_call_args).next
    for TYPE in ARGS:
        if TYPE is lltype.Void:
            x = None
        else:
            x = nextitem()
            if isinstance(TYPE, lltype.Ptr) and TYPE.TO._gckind == 'gc':
                x = cast_from_ptr(TYPE, x)
            else:
                x = cast_from_int(TYPE, x, memocast)
        args.append(x)
    del _call_args[:]
    assert len(ARGS) == len(args)
    if hasattr(ptr._obj, 'graph'):
        llinterp = _llinterp      # it's a global set here by CPU.__init__()
        try:
            result = llinterp.eval_graph(ptr._obj.graph, args)
        except LLException, e:
            _last_exception = e
            result = err_result
    else:
        result = ptr._obj._callable(*args)  # no exception support in this case
    return result

def do_call_void(f, memocast):
    _do_call_common(f, memocast)

def do_call_int(f, memocast):
    x = _do_call_common(f, memocast, 0)
    return cast_to_int(x, memocast)

def do_call_ptr(f, memocast):
    x = _do_call_common(f, memocast, lltype.nullptr(llmemory.GCREF.TO))
    return cast_to_ptr(x)

# ____________________________________________________________


def setannotation(func, annotation, specialize_as_constant=False):

    class Entry(ExtRegistryEntry):
        "Annotation and specialization for calls to 'func'."
        _about_ = func

        if annotation is None or isinstance(annotation, annmodel.SomeObject):
            s_result_annotation = annotation
        else:
            def compute_result_annotation(self, *args_s):
                return annotation(*args_s)

        if specialize_as_constant:
            def specialize_call(self, hop):
                llvalue = func(hop.args_s[0].const)
                return hop.inputconst(lltype.typeOf(llvalue), llvalue)
        else:
            # specialize as direct_call
            def specialize_call(self, hop):
                ARGS = [r.lowleveltype for r in hop.args_r]
                RESULT = hop.r_result.lowleveltype
                if hop.rtyper.type_system.name == 'lltypesystem':
                    FUNCTYPE = lltype.FuncType(ARGS, RESULT)
                    funcptr = lltype.functionptr(FUNCTYPE, func.__name__,
                                                 _callable=func, _debugexc=True)
                    cfunc = hop.inputconst(lltype.Ptr(FUNCTYPE), funcptr)
                else:
                    FUNCTYPE = ootype.StaticMethod(ARGS, RESULT)
                    sm = ootype._static_meth(FUNCTYPE, _name=func.__name__, _callable=func)
                    cfunc = hop.inputconst(FUNCTYPE, sm)
                args_v = hop.inputargs(*hop.args_r)
                return hop.genop('direct_call', [cfunc] + args_v, hop.r_result)


LOOPORBRIDGE = lltype.Ptr(lltype.OpaqueType("LoopOrBridge"))
FRAME = lltype.Ptr(lltype.OpaqueType("Frame"))
MEMOCAST = lltype.Ptr(lltype.OpaqueType("MemoCast"))

_TO_OPAQUE[LoopOrBridge] = LOOPORBRIDGE.TO
_TO_OPAQUE[Frame] = FRAME.TO
_TO_OPAQUE[MemoCast] = MEMOCAST.TO

s_LoopOrBridge = annmodel.SomePtr(LOOPORBRIDGE)
s_Frame = annmodel.SomePtr(FRAME)
s_MemoCast = annmodel.SomePtr(MEMOCAST)

setannotation(compile_start, s_LoopOrBridge)
setannotation(compile_start_int_var, annmodel.SomeInteger())
setannotation(compile_start_ptr_var, annmodel.SomeInteger())
setannotation(compile_add, annmodel.s_None)
setannotation(compile_add_var, annmodel.s_None)
setannotation(compile_add_int_const, annmodel.s_None)
setannotation(compile_add_ptr_const, annmodel.s_None)
setannotation(compile_add_int_result, annmodel.SomeInteger())
setannotation(compile_add_ptr_result, annmodel.SomeInteger())
setannotation(compile_add_jump_target, annmodel.s_None)
setannotation(compile_add_failnum, annmodel.s_None)
setannotation(compile_from_guard, annmodel.s_None)
setannotation(compile_add_livebox, annmodel.s_None)

setannotation(new_frame, s_Frame)
setannotation(frame_clear, annmodel.s_None)
setannotation(frame_add_int, annmodel.s_None)
setannotation(frame_add_ptr, annmodel.s_None)
setannotation(frame_execute, annmodel.SomeInteger())
setannotation(frame_int_getvalue, annmodel.SomeInteger())
setannotation(frame_ptr_getvalue, annmodel.SomePtr(llmemory.GCREF))
setannotation(frame_int_setvalue, annmodel.s_None)
setannotation(frame_ptr_setvalue, annmodel.s_None)
setannotation(frame_int_getresult, annmodel.SomeInteger())
setannotation(frame_ptr_getresult, annmodel.SomePtr(llmemory.GCREF))

setannotation(get_exception, annmodel.SomeAddress())
setannotation(get_exc_value, annmodel.SomePtr(llmemory.GCREF))
setannotation(clear_exception, annmodel.s_None)
setannotation(set_overflow_error, annmodel.s_None)

setannotation(new_memo_cast, s_MemoCast)
setannotation(cast_adr_to_int, annmodel.SomeInteger())
setannotation(cast_int_to_adr, annmodel.SomeAddress())

setannotation(do_arraylen_gc, annmodel.SomeInteger())
setannotation(do_strlen, annmodel.SomeInteger())
setannotation(do_strgetitem, annmodel.SomeInteger())
setannotation(do_getarrayitem_gc_int, annmodel.SomeInteger())
setannotation(do_getarrayitem_gc_ptr, annmodel.SomePtr(llmemory.GCREF))
setannotation(do_getfield_gc_int, annmodel.SomeInteger())
setannotation(do_getfield_gc_ptr, annmodel.SomePtr(llmemory.GCREF))
setannotation(do_getfield_raw_int, annmodel.SomeInteger())
setannotation(do_getfield_raw_ptr, annmodel.SomePtr(llmemory.GCREF))
setannotation(do_new, annmodel.SomePtr(llmemory.GCREF))
setannotation(do_new_array, annmodel.SomePtr(llmemory.GCREF))
setannotation(do_setarrayitem_gc_int, annmodel.s_None)
setannotation(do_setarrayitem_gc_ptr, annmodel.s_None)
setannotation(do_setfield_gc_int, annmodel.s_None)
setannotation(do_setfield_gc_ptr, annmodel.s_None)
setannotation(do_setfield_raw_int, annmodel.s_None)
setannotation(do_setfield_raw_ptr, annmodel.s_None)
setannotation(do_newstr, annmodel.SomePtr(llmemory.GCREF))
setannotation(do_strsetitem, annmodel.s_None)
setannotation(do_call_pushint, annmodel.s_None)
setannotation(do_call_pushptr, annmodel.s_None)
setannotation(do_call_int, annmodel.SomeInteger())
setannotation(do_call_ptr, annmodel.SomePtr(llmemory.GCREF))
setannotation(do_call_void, annmodel.s_None)
