import py
from pypy.rpython.lltypesystem import lltype, llmemory, rclass
from pypy.rpython.llinterp import LLException
from pypy.rpython.annlowlevel import cast_instance_to_base_ptr
from pypy.tool.sourcetools import func_with_new_name
from pypy.rlib.objectmodel import we_are_translated, r_dict, instantiate
from pypy.rlib.unroll import unrolling_iterable
from pypy.rlib.debug import debug_print

from pypy.jit.metainterp import history, support
from pypy.jit.metainterp.history import (Const, ConstInt, ConstPtr, Box,
                                         BoxInt, BoxPtr, Options)
from pypy.jit.metainterp.resoperation import rop
from pypy.jit.metainterp.compile import compile_new_loop, compile_new_bridge
from pypy.jit.metainterp.heaptracker import (get_vtable_for_gcstruct,
                                             populate_type_cache)
from pypy.jit.metainterp import codewriter, optimize, executor
from pypy.rlib.rarithmetic import intmask

# ____________________________________________________________

def check_args(*args):
    for arg in args:
        assert isinstance(arg, (Box, Const))


class arguments(object):
    def __init__(self, *argtypes, **kwargs):
        self.result = kwargs.pop("returns", None)
        assert not kwargs
        self.argtypes = argtypes

    def __eq__(self, other):
        if not isinstance(other, arguments):
            return NotImplemented
        return self.argtypes == other.argtypes and self.result == other.result

    def __ne__(self, other):
        if not isinstance(other, arguments):
            return NotImplemented
        return self.argtypes != other.argtypes or self.result != other.result

    def __call__(self, func):
        result = self.result
        argtypes = unrolling_iterable(self.argtypes)
        def wrapped(self, orgpc):
            args = (self, )
            for argspec in argtypes:
                if argspec == "box":
                    args += (self.load_arg(), )
                elif argspec == "constbox":
                    args += (self.load_const_arg(), )
                elif argspec == "int":
                    args += (self.load_int(), )
                elif argspec == "jumptarget":
                    args += (self.load_3byte(), )
                elif argspec == "jumptargets":
                    num = self.load_int()
                    args += ([self.load_3byte() for i in range(num)], )
                elif argspec == "bool":
                    args += (self.load_bool(), )
                elif argspec == "2byte":
                    args += (self.load_int(), )
                elif argspec == "varargs":
                    args += (self.load_varargs(), )
                elif argspec == "constargs":
                    args += (self.load_constargs(), )
                elif argspec == "bytecode":
                    bytecode = self.load_const_arg()
                    assert isinstance(bytecode, codewriter.JitCode)
                    args += (bytecode, )
                elif argspec == "orgpc":
                    args += (orgpc, )
                elif argspec == "indirectcallset":
                    indirectcallset = self.load_const_arg()
                    assert isinstance(indirectcallset,
                                      codewriter.IndirectCallset)
                    args += (indirectcallset, )
                elif argspec == "virtualizabledesc":
                    from virtualizable import VirtualizableDesc
                    virtualizabledesc = self.load_const_arg()
                    assert isinstance(virtualizabledesc, VirtualizableDesc)
                    args += (virtualizabledesc, )
                else:
                    assert 0, "unknown argtype declaration: %r" % (argspec,)
            val = func(*args)
            if result is not None:
                if result == "box":
                    self.make_result_box(val)
                else:
                    assert 0, "unknown result declaration: %r" % (result,)
                return False
            if val is None:
                val = False
            return val
        wrapped.func_name = "wrap_" + func.func_name
        wrapped.argspec = self
        return wrapped

# ____________________________________________________________


class MIFrame(object):

    def __init__(self, metainterp, jitcode):
        assert isinstance(jitcode, codewriter.JitCode)
        self.metainterp = metainterp
        self.jitcode = jitcode
        self.bytecode = jitcode.code
        self.constants = jitcode.constants
        self.exception_target = -1

    # ------------------------------
    # Decoding of the JitCode

    def load_int(self):
        pc = self.pc
        result = ord(self.bytecode[pc])
        self.pc = pc + 1
        if result > 0x7F:
            result = self._load_larger_int(result)
        return result

    def _load_larger_int(self, result):    # slow path
        result = result & 0x7F
        shift = 7
        pc = self.pc
        while 1:
            byte = ord(self.bytecode[pc])
            pc += 1
            result += (byte & 0x7F) << shift
            shift += 7
            if not byte & 0x80:
                break
        self.pc = pc
        return intmask(result)
    _load_larger_int._dont_inline_ = True

    def load_3byte(self):
        pc = self.pc
        result = (((ord(self.bytecode[pc + 0])) << 16) |
                  ((ord(self.bytecode[pc + 1])) <<  8) |
                  ((ord(self.bytecode[pc + 2])) <<  0))
        self.pc = pc + 3
        return result

    def load_bool(self):
        pc = self.pc
        result = ord(self.bytecode[pc])
        self.pc = pc + 1
        return bool(result)

    def getenv(self, i):
        j = i // 2
        if i % 2:
            return self.constants[j]
        else:
            assert j < len(self.env)
            return self.env[j]

    def load_arg(self):
        return self.getenv(self.load_int())

    def load_const_arg(self):
        return self.constants[self.load_int()]

    def load_varargs(self):
        count = self.load_int()
        return [self.load_arg() for i in range(count)]

    def load_constargs(self):
        count = self.load_int()
        return [self.load_const_arg() for i in range(count)]

    def getvarenv(self, i):
        return self.env[i]

    def make_result_box(self, box):
        assert isinstance(box, Box) or isinstance(box, Const)
        self.env.append(box)

##    def starts_with_greens(self):
##        green_opcode = self.metainterp._green_opcode
##        if self.bytecode[self.pc] == green_opcode:
##            self.greens = []
##            while self.bytecode[self.pc] == green_opcode:
##                self.pc += 1
##                i = self.load_int()
##                assert isinstance(self.env[i], Const)
##                self.greens.append(i)
##        else:
##            self.greens = None

    # ------------------------------

    for _n in range(codewriter.MAX_MAKE_NEW_VARS):
        _decl = ', '.join(["'box'" for _i in range(_n)])
        _allargs = ', '.join(["box%d" % _i for _i in range(_n)])
        exec py.code.Source("""
            @arguments(%s)
            def opimpl_make_new_vars_%d(self, %s):
                ##self.greens = None
                if not we_are_translated():
                    check_args(%s)
                self.env = [%s]
        """ % (_decl, _n, _allargs, _allargs, _allargs)).compile()

    @arguments("varargs")
    def opimpl_make_new_vars(self, newenv):
        ##self.greens = None
        if not we_are_translated():
            check_args(*newenv)
        self.env = newenv

##    @arguments("int")
##    def opimpl_green(self, green):
##        assert isinstance(self.env[green], Const)
##        if not self.greens:
##            self.greens = []
##        self.greens.append(green)
##        #if not we_are_translated():
##        #    history.log.green(self.env[green])

    for _opimpl in ['int_add', 'int_sub', 'int_mul', 'int_floordiv', 'int_mod',
                    'int_lt', 'int_le', 'int_eq',
                    'int_ne', 'int_gt', 'int_ge',
                    'int_and', 'int_or', 'int_xor',
                    'int_rshift', 'int_lshift',
                    'uint_add', 'uint_sub', 'uint_mul',
                    'uint_lt', 'uint_le', 'uint_eq',
                    'uint_ne', 'uint_gt', 'int_ge',
                    ]:
        exec py.code.Source('''
            @arguments("box", "box")
            def opimpl_%s(self, b1, b2):
                self.execute(rop.%s, [b1, b2])
        ''' % (_opimpl, _opimpl.upper())).compile()

    for _opimpl in ['int_add_ovf', 'int_sub_ovf', 'int_mul_ovf', 'int_mod_ovf',
                    ]:
        exec py.code.Source('''
            @arguments("box", "box")
            def opimpl_%s(self, b1, b2):
                return self.execute_with_exc(rop.%s, [b1, b2])
        ''' % (_opimpl, _opimpl.upper())).compile()

    for _opimpl in ['int_is_true', 'int_neg', 'int_invert', 'bool_not',
                    ]:
        exec py.code.Source('''
            @arguments("box")
            def opimpl_%s(self, b):
                self.execute(rop.%s, [b])
        ''' % (_opimpl, _opimpl.upper())).compile()

    for _opimpl in ['int_neg_ovf',
                    ]:
        exec py.code.Source('''
            @arguments("box")
            def opimpl_%s(self, b):
                return self.execute_with_exc(rop.%s, [b])
        ''' % (_opimpl, _opimpl.upper())).compile()

    @arguments()
    def opimpl_return(self):
        assert len(self.env) == 1
        return self.metainterp.finishframe(self.env[0])

    @arguments()
    def opimpl_void_return(self):
        assert len(self.env) == 0
        return self.metainterp.finishframe(None)

    @arguments("jumptarget")
    def opimpl_goto(self, target):
        self.pc = target

    @arguments("box", "jumptarget")
    def opimpl_goto_if_not(self, box, target):
        switchcase = box.getint()
        if switchcase:
            currentpc = self.pc
            targetpc = target
            opnum = rop.GUARD_TRUE
            const_if_fail = history.CONST_FALSE
        else:
            currentpc = target
            targetpc = self.pc
            opnum = rop.GUARD_FALSE
            const_if_fail = history.CONST_TRUE
        self.generate_guard(targetpc, opnum, box, ignore_box=box,
                                                  const_if_fail=const_if_fail)
        self.pc = currentpc

    @arguments("orgpc", "box", "constargs", "jumptargets")
    def opimpl_switch(self, pc, valuebox, constargs, jumptargets):
        box = self.implement_guard_value(pc, valuebox)
        for i in range(len(constargs)):
            casebox = constargs[i]
            if box.equals(casebox):
                self.pc = jumptargets[i]
                break

    @arguments("orgpc", "box", "constbox")
    def opimpl_switch_dict(self, pc, valuebox, switchdict):
        box = self.implement_guard_value(pc, valuebox)
        search_value = box.getint()
        assert isinstance(switchdict, codewriter.SwitchDict)
        try:
            self.pc = switchdict.dict[search_value]
        except KeyError:
            pass

    @arguments("int")
    def opimpl_new(self, size):
        self.execute(rop.NEW, [], descr=size)

    @arguments("int", "constbox")
    def opimpl_new_with_vtable(self, size, vtablebox):
        self.execute(rop.NEW_WITH_VTABLE, [vtablebox], descr=size)

    @arguments("int", "box")
    def opimpl_new_array(self, itemsize, countbox):
        self.execute(rop.NEW_ARRAY, [countbox], descr=itemsize)

    @arguments("box", "int", "box")
    def opimpl_getarrayitem_gc(self, arraybox, arraydesc, indexbox):
        self.execute(rop.GETARRAYITEM_GC, [arraybox, indexbox],
                     descr=arraydesc)

    @arguments("box", "int", "box")
    def opimpl_getarrayitem_gc_pure(self, arraybox, arraydesc, indexbox):
        self.execute(rop.GETARRAYITEM_GC_PURE, [arraybox, indexbox],
                     descr=arraydesc)

    @arguments("box", "int", "box", "box")
    def opimpl_setarrayitem_gc(self, arraybox, arraydesc, indexbox, itembox):
        self.execute(rop.SETARRAYITEM_GC, [arraybox, indexbox, itembox],
                     descr=arraydesc)

    @arguments("box", "int")
    def opimpl_arraylen_gc(self, arraybox, arraydesc):
        self.execute(rop.ARRAYLEN_GC, [arraybox], descr=arraydesc)

    @arguments("orgpc", "box", "int", "box")
    def opimpl_check_neg_index(self, pc, arraybox, arraydesc, indexbox):
        negbox = self.metainterp.execute_and_record(
            rop.INT_LT, [indexbox, ConstInt(0)])
        negbox = self.implement_guard_value(pc, negbox)
        if negbox.getint():
            # the index is < 0; add the array length to it
            lenbox = self.metainterp.execute_and_record(
                rop.ARRAYLEN_GC, [arraybox], descr=arraydesc)
            indexbox = self.metainterp.execute_and_record(
                rop.INT_ADD, [indexbox, lenbox])
        self.make_result_box(indexbox)

    @arguments("box")
    def opimpl_ptr_nonzero(self, box):
        self.execute(rop.OONONNULL, [box])

    @arguments("box")
    def opimpl_ptr_iszero(self, box):
        self.execute(rop.OOISNULL, [box])

    @arguments("box", "box")
    def opimpl_ptr_eq(self, box1, box2):
        self.execute(rop.OOIS, [box1, box2])

    @arguments("box", "box")
    def opimpl_ptr_ne(self, box1, box2):
        self.execute(rop.OOISNOT, [box1, box2])


    @arguments("box", "int")
    def opimpl_getfield_gc(self, box, fielddesc):
        self.execute(rop.GETFIELD_GC, [box], descr=fielddesc)
    @arguments("box", "int")
    def opimpl_getfield_gc_pure(self, box, fielddesc):
        self.execute(rop.GETFIELD_GC_PURE, [box], descr=fielddesc)
    @arguments("box", "int", "box")
    def opimpl_setfield_gc(self, box, fielddesc, valuebox):
        self.execute(rop.SETFIELD_GC, [box, valuebox], descr=fielddesc)

    @arguments("box", "int")
    def opimpl_getfield_raw(self, box, fielddesc):
        self.execute(rop.GETFIELD_RAW, [box], descr=fielddesc)
    @arguments("box", "int")
    def opimpl_getfield_raw_pure(self, box, fielddesc):
        self.execute(rop.GETFIELD_RAW_PURE, [box], descr=fielddesc)
    @arguments("box", "int", "box")
    def opimpl_setfield_raw(self, box, fielddesc, valuebox):
        self.execute(rop.SETFIELD_RAW, [box, valuebox], descr=fielddesc)

    @arguments("bytecode", "varargs")
    def opimpl_call(self, callee, varargs):
        f = self.metainterp.newframe(callee)
        f.setup_call(varargs)
        return True

    @arguments("int", "varargs")
    def opimpl_residual_call(self, calldescr, varargs):
        return self.execute_with_exc(rop.CALL, varargs, descr=calldescr)

    @arguments("int", "varargs")
    def opimpl_residual_call_pure(self, calldescr, varargs):
        self.execute(rop.CALL_PURE, varargs, descr=calldescr)

##    @arguments("fixedlist", "box", "box")
##    def opimpl_list_getitem(self, descr, listbox, indexbox):
##        args = [descr.getfunc, listbox, indexbox]
##        return self.execute_with_exc(rop.LIST_GETITEM, args, descr.tp)

##    @arguments("fixedlist", "box", "box", "box")
##    def opimpl_list_setitem(self, descr, listbox, indexbox, newitembox):
##        args = [descr.setfunc, listbox, indexbox, newitembox]
##        return self.execute_with_exc(rop.LIST_SETITEM, args, 'void')

##    @arguments("builtin", "varargs")
##    def opimpl_getitem_foldable(self, descr, varargs):
##        args = [descr.getfunc] + varargs
##        return self.execute_with_exc('getitem', args, descr.tp, True)

##    @arguments("builtin", "varargs")
##    def opimpl_setitem_foldable(self, descr, varargs):
##        args = [descr.setfunc] + varargs
##        return self.execute_with_exc('setitem', args, 'void', True)

##    @arguments("fixedlist", "box", "box")
##    def opimpl_newlist(self, descr, countbox, defaultbox):
##        args = [descr.malloc_func, countbox, defaultbox]
##        return self.execute_with_exc(rop.NEWLIST, args, 'ptr')

##    @arguments("builtin", "varargs")
##    def opimpl_append(self, descr, varargs):
##        args = [descr.append_func] + varargs
##        return self.execute_with_exc('append', args, 'void')

##    @arguments("builtin", "varargs")
##    def opimpl_insert(self, descr, varargs):
##        args = [descr.insert_func] + varargs
##        return self.execute_with_exc('insert', args, 'void')

##    @arguments("builtin", "varargs")
##    def opimpl_pop(self, descr, varargs):
##        args = [descr.pop_func] + varargs
##        return self.execute_with_exc('pop', args, descr.tp)

##    @arguments("builtin", "varargs")
##    def opimpl_len(self, descr, varargs):
##        args = [descr.len_func] + varargs
##        return self.execute_with_exc('len', args, 'int')

##    @arguments("builtin", "varargs")
##    def opimpl_listnonzero(self, descr, varargs):
##        args = [descr.nonzero_func] + varargs
##        return self.execute_with_exc('listnonzero', args, 'int')


    @arguments("orgpc", "indirectcallset", "box", "varargs")
    def opimpl_indirect_call(self, pc, indirectcallset, box, varargs):
        box = self.implement_guard_value(pc, box)
        cpu = self.metainterp.cpu
        jitcode = indirectcallset.bytecode_for_address(box.getaddr(cpu))
        f = self.metainterp.newframe(jitcode)
        f.setup_call(varargs)
        return True

    @arguments("box")
    def opimpl_strlen(self, str):
        self.execute(rop.STRLEN, [str])

    @arguments("box", "box")
    def opimpl_strgetitem(self, str, index):
        self.execute(rop.STRGETITEM, [str, index])

    @arguments("box", "box", "box")
    def opimpl_strsetitem(self, str, index, newchar):
        self.execute(rop.STRSETITEM, [str, index, newchar])

    @arguments("box")
    def opimpl_newstr(self, length):
        self.execute(rop.NEWSTR, [length])

    @arguments("orgpc", "box", returns="box")
    def opimpl_guard_value(self, pc, box):
        return self.implement_guard_value(pc, box)

    @arguments("orgpc", "box", returns="box")
    def opimpl_guard_class(self, pc, box):
        clsbox = self.cls_of_box(box)
        if isinstance(box, Box):
            self.generate_guard(pc, rop.GUARD_CLASS, box, [clsbox])
        return clsbox

##    @arguments("orgpc", "box", "builtin")
##    def opimpl_guard_builtin(self, pc, box, builtin):
##        self.generate_guard(pc, "guard_builtin", box, [builtin])

##    @arguments("orgpc", "box", "builtin")
##    def opimpl_guard_len(self, pc, box, builtin):
##        intbox = self.metainterp.cpu.execute_operation(
##            'len', [builtin.len_func, box], 'int')
##        self.generate_guard(pc, "guard_len", box, [intbox])

    @arguments("orgpc", "box", "virtualizabledesc", "int")
    def opimpl_guard_nonvirtualized(self, pc, box, vdesc, guard_field):
        clsbox = self.cls_of_box(box)
        op = self.generate_guard(pc, rop.GUARD_NONVIRTUALIZED, box,
                                 [clsbox])
        if op:
            op.vdesc = vdesc
            op.descr = guard_field
        
    @arguments("box")
    def opimpl_keepalive(self, box):
        pass     # xxx?

    def generate_merge_point(self, pc, varargs):
        if isinstance(self.metainterp.history, history.BlackHole):
            raise self.metainterp.ContinueRunningNormally(varargs)
        num_green_args = self.metainterp.num_green_args
        for i in range(num_green_args):
            varargs[i] = self.implement_guard_value(pc, varargs[i])

    @arguments("orgpc", "varargs")
    def opimpl_can_enter_jit(self, pc, varargs):
        self.generate_merge_point(pc, varargs)
        raise GenerateMergePoint(varargs)

    @arguments("orgpc")
    def opimpl_jit_merge_point(self, pc):
        self.generate_merge_point(pc, self.env)

    @arguments("jumptarget")
    def opimpl_setup_exception_block(self, exception_target):
        self.exception_target = exception_target

    @arguments()
    def opimpl_teardown_exception_block(self):
        self.exception_target = -1

    @arguments("constbox", "jumptarget")
    def opimpl_goto_if_exception_mismatch(self, vtableref, next_exc_target):
        assert isinstance(self.exception_box, Const)    # XXX
        adr = vtableref.getaddr(self.metainterp.cpu)
        bounding_class = llmemory.cast_adr_to_ptr(adr, rclass.CLASSTYPE)
        adr = self.exception_box.getaddr(self.metainterp.cpu)
        real_class = llmemory.cast_adr_to_ptr(adr, rclass.CLASSTYPE)
        if not rclass.ll_issubclass(real_class, bounding_class):
            self.pc = next_exc_target

    @arguments("int")
    def opimpl_put_last_exception(self, index):
        assert index >= 0
        self.env.insert(index, self.exception_box)

    @arguments("int")
    def opimpl_put_last_exc_value(self, index):
        assert index >= 0
        self.env.insert(index, self.exc_value_box)

    @arguments()
    def opimpl_raise(self):
        assert len(self.env) == 2
        return self.metainterp.finishframe_exception(self.env[0], self.env[1])

    @arguments()
    def opimpl_reraise(self):
        return self.metainterp.finishframe_exception(self.exception_box,
                                                     self.exc_value_box)

    # ------------------------------

    def setup_call(self, argboxes):
        if not we_are_translated():
            check_args(*argboxes)
        self.pc = 0
        self.env = argboxes
        if not we_are_translated():
            self.metainterp._debug_history[-1][-1] = argboxes
        #self.starts_with_greens()
        #assert len(argboxes) == len(self.graph.getargs())

    def setup_resume_at_op(self, pc, envlength, liveboxes, lbindex,
                           exception_target):
        if not we_are_translated():
            check_args(*liveboxes)
        self.pc = pc
        self.env = liveboxes[lbindex:lbindex+envlength]
        self.exception_target = exception_target
        assert len(self.env) == envlength
        return lbindex + envlength

    def run_one_step(self):
        # Execute the frame forward.  This method contains a loop that leaves
        # whenever the 'opcode_implementations' (which is one of the 'opimpl_'
        # methods) returns True.  This is the case when the current frame
        # changes, due to a call or a return.
        while True:
            pc = self.pc
            op = ord(self.bytecode[pc])
            #print self.metainterp.opcode_names[op]
            self.pc = pc + 1
            stop = self.metainterp.opcode_implementations[op](self, pc)
            #self.metainterp.most_recent_mp = None
            if stop:
                break

    def generate_guard(self, pc, opnum, box, extraargs=[], ignore_box=None,
                       const_if_fail=None):
        if isinstance(box, Const):    # no need for a guard
            return
        if isinstance(self.metainterp.history, history.BlackHole):
            return
        liveboxes = []
        for frame in self.metainterp.framestack:
            for framebox in frame.env:
                assert framebox is not None
                if framebox is not ignore_box:
                    liveboxes.append(framebox)
                else:
                    assert const_if_fail is not None
                    liveboxes.append(const_if_fail)
        if box is not None:
            extraargs = [box] + extraargs
        guard_op = self.metainterp.history.record(opnum, extraargs, None)
        guard_op.liveboxes = liveboxes
        saved_pc = self.pc
        self.pc = pc
        guard_op.key = self.metainterp.record_state()
        self.pc = saved_pc
        return guard_op

    def implement_guard_value(self, pc, box):
        if isinstance(box, Box):
            promoted_box = box.constbox()
            self.generate_guard(pc, rop.GUARD_VALUE, box, [promoted_box])
            return promoted_box
        else:
            return box     # no promotion needed, already a Const

    def cls_of_box(self, box):
        obj = box.getptr(lltype.Ptr(rclass.OBJECT))
        cls = llmemory.cast_ptr_to_adr(obj.typeptr)
        return ConstInt(self.metainterp.cpu.cast_adr_to_int(cls))

    def execute(self, opnum, argboxes, descr=0):
        resbox = self.metainterp.execute_and_record(opnum, argboxes, descr)
        if resbox is not None:
            self.make_result_box(resbox)
    execute._annspecialcase_ = 'specialize:arg(1)'

    def execute_with_exc(self, opnum, argboxes, descr=0):
        cpu = self.metainterp.cpu
        resbox = executor.execute(cpu, opnum, argboxes, descr)
        if not we_are_translated():
            self.metainterp._debug_history.append(['call',
                                                  argboxes[0], argboxes[1:]])
        # record the operation in the history
        self.metainterp.history.record(opnum, argboxes, resbox, descr)
        if resbox is not None:
            self.make_result_box(resbox)
        return self.metainterp.handle_exception()

# ____________________________________________________________


class OOMetaInterp(object):
    num_green_args = 0

    def __init__(self, portal_graph, graphs, cpu, stats, options):
        self.portal_graph = portal_graph
        self.cpu = cpu
        self.stats = stats
        self.options = options
        self.compiled_merge_points = r_dict(history.mp_eq, history.mp_hash)
                 # { greenkey: list-of-MergePoints }

        self.opcode_implementations = []
        self.opcode_names = []
        self.opname_to_index = {}
        self.class_sizes = populate_type_cache(graphs, self.cpu)

        self._virtualizabledescs = {}
        self._debug_history = []

    def generate_bytecode(self, policy):
        self._codewriter = codewriter.CodeWriter(self, policy)
        self.portal_code = self._codewriter.make_portal_bytecode(
            self.portal_graph)
        self.cpu.set_meta_interp(self)
        self.delete_history()

    def enable_stats(self):
        return not we_are_translated()

    def newframe(self, jitcode):
        if not we_are_translated():
            self._debug_history.append(['enter', jitcode, None])
        f = MIFrame(self, jitcode)
        self.framestack.append(f)
        return f

    def finishframe(self, resultbox):
        frame = self.framestack.pop()
        if not we_are_translated():
            self._debug_history.append(['leave', frame.jitcode, None])
        if self.framestack:
            if resultbox is not None:
                self.framestack[-1].make_result_box(resultbox)
            return True
        else:
            raise self.DoneWithThisFrame(resultbox)

    def finishframe_exception(self, exceptionbox, excvaluebox):
        while self.framestack:
            frame = self.framestack[-1]
            if frame.exception_target >= 0:
                frame.pc = frame.exception_target
                frame.exception_target = -1
                frame.exception_box = exceptionbox
                frame.exc_value_box = excvaluebox
                return True
            if not we_are_translated():
                self._debug_history.append(['leave_exc', frame.jitcode, None])
            self.framestack.pop()
        raise self.ExitFrameWithException(exceptionbox, excvaluebox)

    def create_empty_history(self):
        self.history = history.History(self.cpu)
        if self.enable_stats():
            self.stats.history_graph.operations = self.history.operations

    def delete_history(self):
        # XXX call me again later
        self.history = None
        self.framestack = None

    def _all_constants(self, boxes):
        for box in boxes:
            if not isinstance(box, Const):
                return False
        return True

    def execute_and_record(self, opnum, argboxes, descr=0):
        # execute the operation first
        resbox = executor.execute(self.cpu, opnum, argboxes, descr)
        # check if the operation can be constant-folded away
        canfold = False
        if rop._ALWAYS_PURE_FIRST <= opnum <= rop._ALWAYS_PURE_LAST:
            # this part disappears if execute() is specialized for an
            # opnum that is not within the range
            canfold = self._all_constants(argboxes)
            if canfold:
                resbox = resbox.constbox()       # ensure it is a Const
            else:
                resbox = resbox.nonconstbox()    # ensure it is a Box
        else:
            assert resbox is None or isinstance(resbox, Box)
        # record the operation if not constant-folded away
        if not canfold:
            self.history.record(opnum, argboxes, resbox, descr)
        return resbox
    execute_and_record._annspecialcase_ = 'specialize:arg(1)'

    def interpret(self):
        # Execute the frames forward until we raise a DoneWithThisFrame,
        # a ContinueRunningNormally, or a GenerateMergePoint exception.
        if isinstance(self.history, history.BlackHole):
            text = ' (BlackHole)'
        else:
            text = ''
        if not we_are_translated():
            history.log.event('ENTER' + text)
        else:
            debug_print('ENTER' + text)
        try:
            while True:
                self.framestack[-1].run_one_step()
        finally:
            if not we_are_translated():
                history.log.event('LEAVE' + text)
            else:
                debug_print('LEAVE' + text)

    def compile_and_run(self, *args):
        orig_boxes = self.initialize_state_from_start(*args)
        try:
            self.interpret()
            assert False, "should always raise"
        except GenerateMergePoint, gmp:
            compiled_loop = self.compile(orig_boxes, gmp.argboxes)
            return self.designate_target_loop(gmp, compiled_loop)

    def handle_guard_failure(self, guard_failure):
        orig_boxes = self.initialize_state_from_guard_failure(guard_failure)
        try:
            if guard_failure.guard_op.opnum in (rop.GUARD_EXCEPTION,
                                                rop.GUARD_NO_EXCEPTION):
                self.handle_exception()
            self.interpret()
            assert False, "should always raise"
        except GenerateMergePoint, gmp:
            compiled_bridge = self.compile_bridge(guard_failure, orig_boxes,
                                                  gmp.argboxes)
            loop, resargs = self.designate_target_loop(gmp,
                                                       compiled_bridge.jump_to)
            self.jump_after_guard_failure(guard_failure, loop, resargs)

    def designate_target_loop(self, gmp, loop):
        num_green_args = self.num_green_args
        residual_args = self.get_residual_args(loop,
                                               gmp.argboxes[num_green_args:])
        return (loop, residual_args)

    def jump_after_guard_failure(self, guard_failure, loop, residual_args):
        guard_failure.make_ready_for_continuing_at(loop.operations[0])
        for i in range(len(residual_args)):
            self.cpu.setvaluebox(guard_failure.frame, loop.operations[0],
                                 i, residual_args[i])

    def compile(self, original_boxes, live_arg_boxes):
        num_green_args = self.num_green_args
        for i in range(num_green_args):
            box1 = original_boxes[i]
            box2 = live_arg_boxes[i]
            if not box1.equals(box2):
                # not a valid loop
                raise self.ContinueRunningNormally(live_arg_boxes)
        mp = history.ResOperation(rop.MERGE_POINT,
                                  original_boxes[num_green_args:], None)
        mp.greenkey = original_boxes[:num_green_args]
        self.history.operations.insert(0, mp)
        old_loops = self.compiled_merge_points.setdefault(mp.greenkey, [])
        loop = compile_new_loop(self, old_loops,
                                live_arg_boxes[num_green_args:])
        if not loop:
            raise self.ContinueRunningNormally(live_arg_boxes)
        if not we_are_translated():
            loop._call_history = self._debug_history
            self.debug_history = []
        return loop

    def compile_bridge(self, guard_failure, original_boxes, live_arg_boxes):
        num_green_args = self.num_green_args
        mp = history.ResOperation(rop.CATCH, original_boxes, None)
        mp.coming_from = guard_failure.guard_op
        self.history.operations.insert(0, mp)
        try:
            old_loops = self.compiled_merge_points[
                live_arg_boxes[:num_green_args]]
        except KeyError:
            bridge = None
        else:
            bridge = compile_new_bridge(self, old_loops,
                                        live_arg_boxes[num_green_args:])
        if bridge is None:
            raise self.ContinueRunningNormally(live_arg_boxes)
        if not we_are_translated():
            bridge._call_history = self._debug_history
            self.debug_history = []
        guard_failure.guard_op.jump_target = bridge.operations[0]
        return bridge

    def get_residual_args(self, loop, args):
        mp = loop.operations[0]
        if mp.specnodes is None:     # it is None only for tests
            return args
        assert len(mp.specnodes) == len(args)
        expanded_args = []
        for i in range(len(mp.specnodes)):
            specnode = mp.specnodes[i]
            specnode.extract_runtime_data(self.cpu, args[i], expanded_args)
        return expanded_args

    def _initialize_from_start(self, original_boxes, num_green_args, *args):
        if args:
            value = args[0]
            if isinstance(lltype.typeOf(value), lltype.Ptr):
                value = lltype.cast_opaque_ptr(llmemory.GCREF, value)
                if num_green_args > 0:
                    cls = ConstPtr
                else:
                    cls = BoxPtr
            else:
                if num_green_args > 0:
                    cls = ConstInt
                else:
                    cls = BoxInt
                value = intmask(value)
            box = cls(value)
            original_boxes.append(box)
            self._initialize_from_start(original_boxes, num_green_args-1,
                                        *args[1:])

    def initialize_state_from_start(self, *args):
        self.create_empty_history()
        num_green_args = self.num_green_args
        original_boxes = []
        self._initialize_from_start(original_boxes, num_green_args, *args)
        # ----- make a new frame -----
        self.framestack = []
        f = self.newframe(self.portal_code)
        f.pc = 0
        f.env = original_boxes[:]
        return original_boxes

    def initialize_state_from_guard_failure(self, guard_failure):
        # guard failure: rebuild a complete MIFrame stack
        if self.state.must_compile_from_failure(guard_failure):
            self.history = history.History(self.cpu)
        else:
            self.history = history.BlackHole(self.cpu)
        guard_op = guard_failure.guard_op
        boxes_from_frame = []
        index = 0
        for box in guard_op.liveboxes:
            if isinstance(box, Box):
                newbox = self.cpu.getvaluebox(guard_failure.frame,
                                              guard_op, index)
                index += 1
            else:
                newbox = box
            boxes_from_frame.append(newbox)
        if guard_op.storage_info is not None:
            newboxes = optimize.rebuild_boxes_from_guard_failure(
                guard_op, self, boxes_from_frame)
        else:
            # xxx for tests only
            newboxes = boxes_from_frame
        self.rebuild_state_after_failure(guard_op.key, newboxes)
        return boxes_from_frame

    def handle_exception(self):
        etype = self.cpu.get_exception()
        evalue = self.cpu.get_exc_value()
        self.cpu.clear_exception()
        frame = self.framestack[-1]
        if etype:
            exception_box = ConstInt(etype)
            exc_value_box = BoxPtr(evalue)
            op = frame.generate_guard(frame.pc, rop.GUARD_EXCEPTION,
                                      None, [exception_box])
            if op:
                op.result = exc_value_box
            return self.finishframe_exception(exception_box, exc_value_box)
        else:
            frame.generate_guard(frame.pc, rop.GUARD_NO_EXCEPTION, None, [])
            return False

    def rebuild_state_after_failure(self, key, newboxes):
        if not we_are_translated():
            self._debug_history.append(['guard_failure', None, None])
        self.framestack = []
        nbindex = 0
        for jitcode, pc, envlength, exception_target in key:
            f = self.newframe(jitcode)
            nbindex = f.setup_resume_at_op(pc, envlength, newboxes, nbindex,
                                           exception_target)
        assert nbindex == len(newboxes), "too many newboxes!"

    def record_compiled_merge_point(self, mp):
        pass
        #mplist = self.compiled_merge_points.setdefault(mp.greenkey, [])
        #mplist.append(mp)

    def record_state(self):
        # XXX this whole function should do a sharing
        key = []
        for frame in self.framestack:
            key.append((frame.jitcode, frame.pc, len(frame.env),
                        frame.exception_target))
        return key

    # ____________________________________________________________
    # construction-time interface

    def _register_opcode(self, opname):
        assert len(self.opcode_implementations) < 256, \
               "too many implementations of opcodes!"
        name = "opimpl_" + opname
        self.opname_to_index[opname] = len(self.opcode_implementations)
        self.opcode_names.append(opname)
        self.opcode_implementations.append(getattr(MIFrame, name).im_func)

    def find_opcode(self, name):
        try:
            return self.opname_to_index[name]
        except KeyError:
            self._register_opcode(name)
            return self.opname_to_index[name]


class GenerateMergePoint(Exception):
    def __init__(self, args):
        self.argboxes = args
