import sys
import ctypes
from pypy.jit.backend.x86 import symbolic
from pypy.jit.metainterp.history import Const, ConstInt, Box
from pypy.rpython.lltypesystem import lltype, rffi, ll2ctypes, rstr, llmemory
from pypy.rpython.lltypesystem.rclass import OBJECT
from pypy.rpython.lltypesystem.lloperation import llop
from pypy.annotation import model as annmodel
from pypy.tool.uid import fixid
from pypy.jit.backend.x86.regalloc import (RegAlloc, FRAMESIZE, WORD, REGS,
                                      arg_pos, lower_byte, stack_pos, Perform,
                                      RETURN)
from pypy.rlib.objectmodel import we_are_translated, specialize
from pypy.jit.backend.x86 import codebuf
from pypy.jit.backend.x86.support import gc_malloc_fnaddr
from pypy.jit.backend.x86.ri386 import *
from pypy.jit.metainterp.resoperation import rop

# our calling convention - we pass three first args as edx, ecx and eax
# and the rest stays on the stack

class Assembler386(object):
    MC_SIZE = 1024*1024     # 1MB, but assumed infinite for now
    generic_return_addr = 0
    position = -1

    def __init__(self, cpu, translate_support_code=False):
        self.cpu = cpu
        self.verbose = False
        self.mc = None
        self.mc2 = None
        self.rtyper = cpu.rtyper
        self.malloc_func_addr = 0
        self._exception_data = lltype.nullptr(rffi.CArray(lltype.Signed))
        self._exception_addr = 0

    def make_sure_mc_exists(self):
        if self.mc is None:
            # we generate the loop body in 'mc'
            # 'mc2' is for guard recovery code
            if we_are_translated():
                addr = llop.get_exception_addr(llmemory.Address)
                self._exception_data = llmemory.cast_adr_to_ptr(addr, rffi.CArrayPtr(lltype.Signed))
            else:
                self._exception_data = lltype.malloc(rffi.CArray(lltype.Signed), 2,
                                                     zero=True, flavor='raw')
            self._exception_addr = self.cpu.cast_ptr_to_int(
                self._exception_data)
            # a backup, in case our exception can be somehow mangled,
            # by a handling code
            self._exception_bck = lltype.malloc(rffi.CArray(lltype.Signed), 2,
                                                zero=True, flavor='raw')
            self._exception_bck_addr = self.cpu.cast_ptr_to_int(
                self._exception_bck)
            self.mc = codebuf.MachineCodeBlock(self.MC_SIZE)
            self.mc2 = codebuf.MachineCodeBlock(self.MC_SIZE)
            self.generic_return_addr = self.assemble_generic_return()
            # the address of the function called by 'new': directly use
            # Boehm's GC_malloc function.
            if self.malloc_func_addr == 0:
                self.malloc_func_addr = gc_malloc_fnaddr() 

    def assemble(self, operations, guard_op, verbose=False):
        self.verbose = verbose
        # the last operation can be 'jump', 'return' or 'guard_pause';
        # a 'jump' can either close a loop, or end a bridge to some
        # previously-compiled code.
        self.make_sure_mc_exists()
        op0 = operations[0]
        op0.position = self.mc.tell()
        regalloc = RegAlloc(operations, guard_op, self.cpu.translate_support_code)
        if not we_are_translated():
            self._regalloc = regalloc # for debugging
        computed_ops = regalloc.computed_ops
        if guard_op is not None:
            new_rel_addr = self.mc.tell() - guard_op._jmp_from
            TP = rffi.CArrayPtr(lltype.Signed)
            ptr = rffi.cast(TP, guard_op._jmp_from - WORD)
            ptr[0] = new_rel_addr
            self.mc.redone(guard_op._jmp_from - WORD, guard_op._jmp_from)
        if self.verbose and not we_are_translated():
            import pprint
            print
            pprint.pprint(operations)
            print
            pprint.pprint(computed_ops)
            print
        for i in range(len(computed_ops)):
            op = computed_ops[i]
            if not we_are_translated():
                self.dump_op(op)
            self.position = i
            # XXX eventually change to numbers or kill
            #     alltogether
            if op.opname == 'load':
                self.regalloc_load(op)
            elif op.opname == 'store':
                self.regalloc_store(op)
            elif op.opname == 'perform_discard':
                self.regalloc_perform_discard(op)
            elif op.opname == 'perform':
                self.regalloc_perform(op)
            elif op.opname == 'perform_with_guard':
                self.regalloc_perform_with_guard(op)
            else:
                raise NotImplementedError(op.opname)
        if not we_are_translated():
            self.dump_op('')
        self.mc.done()
        self.mc2.done()

    def assemble_bootstrap_code(self, arglocs):
        self.make_sure_mc_exists()
        addr = self.mc.tell()
        self.mc.SUB(esp, imm(FRAMESIZE))
        self.mc.MOV(eax, arg_pos(1))
        for i in range(len(arglocs)):
            loc = arglocs[i]
            if not isinstance(loc, REG):
                self.mc.MOV(ecx, mem(eax, i * WORD))
                self.mc.MOV(loc, ecx)
        for i in range(len(arglocs)):
            loc = arglocs[i]
            if isinstance(loc, REG):
                self.mc.MOV(loc, mem(eax, i * WORD))
        self.mc.JMP(arg_pos(0))
        self.mc.done()
        return addr

    def dump_op(self, op):
        if not self.verbose:
            return
        _prev = Box._extended_display
        try:
            Box._extended_display = False
            print >> sys.stderr, ' 0x%x  %s' % (fixid(self.mc.tell()), op)
        finally:
            Box._extended_display = _prev

    def assemble_comeback_bootstrap(self, mp):
        entry_point_addr = self.mc2.tell()
        for i in range(len(mp.arglocs)):
            argloc = mp.arglocs[i]
            if isinstance(argloc, REG):
                self.mc2.MOV(argloc, stack_pos(mp.stacklocs[i]))
            elif not we_are_translated():
                # debug checks
                if not isinstance(argloc, (IMM8, IMM32)):
                    assert repr(argloc) == repr(stack_pos(mp.stacklocs[i]))
        self.mc2.JMP(rel32(mp.position))
        self.mc2.done()
        return entry_point_addr

    def assemble_generic_return(self):
        # generate a generic stub that just returns, taking the
        # return value from *esp (i.e. stack position 0).
        addr = self.mc.tell()
        self.mc.MOV(eax, mem(esp, 0))
        self.mc.ADD(esp, imm(FRAMESIZE))
        self.mc.RET()
        self.mc.done()
        return addr

    def copy_var_if_used(self, v, to_v):
        """ Gives new loc
        """
        loc = self.loc(v)
        if isinstance(loc, REG):
            if self.regalloc.used(v) > self.regalloc.position:
                newloc = self.regalloc.allocate_loc(v)
                self.regalloc.move(loc, newloc)
            self.regalloc.force_loc(to_v, loc)
        else:
            newloc = self.regalloc.allocate_loc(to_v, force_reg=True)
            self.mc.MOV(newloc, loc)
            loc = newloc
        return loc
            
    def next_stack_position(self):
        position = self.current_stack_depth
        self.current_stack_depth += 1
        return position

    def regalloc_load(self, op):
        self.mc.MOV(op.to_loc, op.from_loc)

    regalloc_store = regalloc_load

    def regalloc_perform(self, op):
        assert isinstance(op, Perform)
        resloc = op.result_loc
        genop_list[op.op.opnum](self, op.op, op.arglocs, resloc)

    def regalloc_perform_discard(self, op):
        genop_discard_list[op.op.opnum](self, op.op, op.arglocs)

    def regalloc_perform_with_guard(self, op):
        genop_guard_list[op.op.opnum](self, op.op, op.guard_op, op.arglocs,
                                      op.result_loc)

    def regalloc_store_to_arg(self, op):
        self.mc.MOV(arg_pos(op.pos), op.from_loc)

    def _unaryop(asmop):
        def genop_unary(self, op, arglocs, resloc):
            getattr(self.mc, asmop)(arglocs[0])
        return genop_unary

    def _binaryop(asmop, can_swap=False):
        def genop_binary(self, op, arglocs, result_loc):
            getattr(self.mc, asmop)(arglocs[0], arglocs[1])
        return genop_binary

    def _binaryop_ovf(asmop, can_swap=False):
        def genop_binary_ovf(self, op, guard_op, arglocs, result_loc):
            getattr(self.mc, asmop)(arglocs[0], arglocs[1])
            index = self.cpu.make_guard_index(guard_op)
            recovery_code_addr = self.mc2.tell()
            stacklocs = guard_op.stacklocs
            locs = arglocs[2:]
            assert len(locs) == len(stacklocs)
            for i in range(len(locs)):
                loc = locs[i]
                if isinstance(loc, REG):
                    self.mc2.MOV(stack_pos(stacklocs[i]), loc)
            ovf_error_vtable = self.cpu.cast_adr_to_int(self._ovf_error_vtable)
            self.mc2.MOV(eax, imm(ovf_error_vtable))
            self.mc2.MOV(addr_add(imm(self._exception_bck_addr), imm(0)), eax)
            ovf_error_instance = self.cpu.cast_adr_to_int(self._ovf_error_inst)
            self.mc2.MOV(eax, imm(ovf_error_instance))
            self.mc2.MOV(addr_add(imm(self._exception_bck_addr), imm(WORD)),eax)
            self.mc2.PUSH(esp)           # frame address
            self.mc2.PUSH(imm(index))    # index of guard that failed
            self.mc2.CALL(rel32(self.cpu.get_failure_recovery_func_addr()))
            self.mc2.ADD(esp, imm(8))
            self.mc2.JMP(eax)
            self.mc.JO(rel32(recovery_code_addr))
            guard_op._jmp_from = self.mc.tell()
        return genop_binary_ovf

    def _cmpop(cond, rev_cond):
        def genop_cmp(self, op, arglocs, result_loc):
            if isinstance(op.args[0], Const):
                self.mc.CMP(arglocs[1], arglocs[0])
                self.mc.MOV(result_loc, imm8(0))
                getattr(self.mc, 'SET' + rev_cond)(lower_byte(result_loc))
            else:
                self.mc.CMP(arglocs[0], arglocs[1])
                self.mc.MOV(result_loc, imm8(0))
                getattr(self.mc, 'SET' + cond)(lower_byte(result_loc))
        return genop_cmp

    def call(self, addr, args, res):
        for i in range(len(args)):
            arg = args[i]
            self.mc.PUSH(arg)
        self.mc.CALL(rel32(addr))
        self.mc.ADD(esp, imm(len(args) * WORD))
        assert res is eax

    genop_int_neg = _unaryop("NEG")
    genop_int_add = _binaryop("ADD", True)
    genop_int_sub = _binaryop("SUB")
    genop_int_mul = _binaryop("IMUL", True)
    genop_int_and = _binaryop("AND", True)

    genop_uint_add = genop_int_add
    genop_uint_sub = genop_int_sub
    genop_uint_mul = genop_int_mul
    xxx_genop_uint_and = genop_int_and

    genop_int_mul_ovf = _binaryop_ovf("IMUL", True)
    genop_int_sub_ovf = _binaryop_ovf("SUB")
    genop_int_add_ovf = _binaryop_ovf("ADD", True)

    genop_int_lt = _cmpop("L", "G")
    genop_int_le = _cmpop("LE", "GE")
    genop_int_eq = _cmpop("E", "NE")
    genop_int_ne = _cmpop("NE", "E")
    genop_int_gt = _cmpop("G", "L")
    genop_int_ge = _cmpop("GE", "LE")

    genop_uint_gt = _cmpop("A", "B")
    genop_uint_lt = _cmpop("B", "A")
    genop_uint_le = _cmpop("BE", "AE")
    genop_uint_ge = _cmpop("AE", "BE")

    # for now all chars are being considered ints, although we should make
    # a difference at some point
    xxx_genop_char_eq = genop_int_eq

    def genop_bool_not(self, op, arglocs, resloc):
        self.mc.XOR(arglocs[0], imm8(1))

    #def genop_int_lshift(self, op):
    #    self.load(eax, op.args[0])
    #    self.load(ecx, op.args[1])
    #    self.mc.SHL(eax, cl)
    #    self.mc.CMP(ecx, imm8(32))
    #    self.mc.SBB(ecx, ecx)
    #    self.mc.AND(eax, ecx)
    #    self.save(eax, op.results[0])

    def genop_int_rshift(self, op, arglocs, resloc):
        (x, y, tmp) = arglocs
        assert tmp is ecx
        yv = op.args[1]
        if isinstance(yv, ConstInt):
            intval = yv.value
            if intval < 0 or intval > 31:
                intval = 31
            self.mc.MOV(tmp, imm8(intval))
        else:
            self.mc.MOV(tmp, imm8(31)) 
            self.mc.CMP(y, tmp)
            self.mc.CMOVBE(tmp, y)
        self.mc.SAR(resloc, cl)

    def genop_int_is_true(self, op, arglocs, resloc):
        argloc = arglocs[0]
        self.mc.TEST(argloc, argloc)
        self.mc.MOV(resloc, imm8(0))
        self.mc.SETNZ(lower_byte(resloc))

    def genop_oononnull(self, op, arglocs, resloc):
        self.mc.CMP(arglocs[0], imm8(0))
        self.mc.MOV(resloc, imm8(0))
        self.mc.SETNE(lower_byte(resloc))

    def genop_ooisnull(self, op, arglocs, resloc):
        self.mc.CMP(arglocs[0], imm8(0))
        self.mc.MOV(resloc, imm8(0))
        self.mc.SETE(lower_byte(resloc))

    def genop_int_mod(self, op, arglocs, resloc):
        self.mc.CDQ()
        self.mc.IDIV(ecx)

    def genop_int_floordiv(self, op, arglocs, resloc):
        self.mc.CDQ()
        self.mc.IDIV(ecx)

    def genop_new_with_vtable(self, op, arglocs, result_loc):
        assert result_loc is eax
        loc_size, loc_vtable = arglocs
        self.mc.PUSH(loc_vtable)
        self.call(self.malloc_func_addr, [loc_size], eax)
        # xxx ignore NULL returns for now
        self.mc.POP(mem(eax, 0))

    # same as malloc varsize after all
    def genop_new(self, op, arglocs, result_loc):
        assert result_loc is eax
        loc_size = arglocs[0]
        self.call(self.malloc_func_addr, [loc_size], eax)

    def genop_getfield_gc(self, op, arglocs, resloc):
        base_loc, ofs_loc, size_loc = arglocs
        assert isinstance(size_loc, IMM32)
        size = size_loc.value
        if size == 1:
            self.mc.MOVZX(resloc, addr8_add(base_loc, ofs_loc))
        elif size == WORD:
            self.mc.MOV(resloc, addr_add(base_loc, ofs_loc))
        else:
            raise NotImplementedError("getfield size = %d" % size)

    genop_getfield_gc_pure = genop_getfield_gc

    def genop_getarrayitem_gc(self, op, arglocs, resloc):
        base_loc, ofs_loc, scale, ofs = arglocs
        assert isinstance(ofs, IMM32)
        assert isinstance(scale, IMM32)
        self.mc.MOV(resloc, addr_add(base_loc, ofs_loc, ofs.value, scale.value))

    genop_getfield_raw = genop_getfield_gc
    genop_getarrayitem_gc_pure = genop_getarrayitem_gc

    def genop_setfield_gc(self, op, arglocs):
        base_loc, ofs_loc, size_loc, value_loc = arglocs
        assert isinstance(size_loc, IMM32)
        size = size_loc.value
        if size == WORD:
            self.mc.MOV(addr_add(base_loc, ofs_loc), value_loc)
        elif size == 2:
            raise NotImplementedError("shorts and friends")
            self.mc.MOV(addr16_add(base_loc, ofs_loc), lower_2_bytes(value_loc))
        elif size == 1:
            self.mc.MOV(addr8_add(base_loc, ofs_loc), lower_byte(value_loc))
        else:
            raise NotImplementedError("Addr size %d" % size)

    def genop_setarrayitem_gc(self, op, arglocs):
        base_loc, ofs_loc, value_loc, scale_loc, baseofs = arglocs
        assert isinstance(baseofs, IMM32)
        assert isinstance(scale_loc, IMM32)
        if scale_loc.value == 2:
            self.mc.MOV(addr_add(base_loc, ofs_loc, baseofs.value,
                                 scale_loc.value), value_loc)
        elif scale_loc.value == 0:
            self.mc.MOV(addr8_add(base_loc, ofs_loc, baseofs.value,
                                 scale_loc.value), lower_byte(value_loc))
        else:
            raise NotImplementedError("scale = %d" % scale_loc.value)

    def genop_strsetitem(self, op, arglocs):
        base_loc, ofs_loc, val_loc = arglocs
        basesize, itemsize, ofs_length = symbolic.get_array_token(rstr.STR,
                                              self.cpu.translate_support_code)
        self.mc.MOV(addr8_add(base_loc, ofs_loc, basesize),
                    lower_byte(val_loc))

    genop_setfield_raw = genop_setfield_gc

    def genop_strlen(self, op, arglocs, resloc):
        base_loc = arglocs[0]
        basesize, itemsize, ofs_length = symbolic.get_array_token(rstr.STR,
                                             self.cpu.translate_support_code)
        self.mc.MOV(resloc, addr_add_const(base_loc, ofs_length))

    def genop_arraylen_gc(self, op, arglocs, resloc):
        base_loc, ofs_loc = arglocs
        self.mc.MOV(resloc, addr_add(base_loc, imm(0)))

    def genop_strgetitem(self, op, arglocs, resloc):
        base_loc, ofs_loc = arglocs
        basesize, itemsize, ofs_length = symbolic.get_array_token(rstr.STR,
                                             self.cpu.translate_support_code)
        self.mc.MOVZX(resloc, addr8_add(base_loc, ofs_loc, basesize))

    def genop_merge_point(self, op, locs):
        op.position = self.mc.tell()
        op.comeback_bootstrap_addr = self.assemble_comeback_bootstrap(op)

    genop_catch = genop_merge_point

    def genop_return(self, op, locs):
        if op.args:
            loc = locs[0]
            if loc is not eax:
                self.mc.MOV(eax, loc)
        self.mc.ADD(esp, imm(FRAMESIZE))
        # copy exception to some safe place and clean the original
        # one
        self.mc.MOV(ecx, heap(self._exception_addr))
        self.mc.MOV(heap(self._exception_bck_addr), ecx)
        self.mc.MOV(ecx, addr_add(imm(self._exception_addr), imm(WORD)))
        self.mc.MOV(addr_add(imm(self._exception_bck_addr), imm(WORD)),
                     ecx)
        # clean up the original exception, we don't want
        # to enter more rpython code with exc set
        self.mc.MOV(heap(self._exception_addr), imm(0))
        self.mc.RET()

    def genop_jump(self, op, locs):
        targetmp = op.jump_target
        self.mc.JMP(rel32(targetmp.position))

    def genop_guard_true(self, op, locs):
        loc = locs[0]
        self.mc.TEST(loc, loc)
        self.implement_guard(op, self.mc.JZ, locs[1:])

    def genop_guard_no_exception(self, op, locs):
        loc = locs[0]
        self.mc.MOV(loc, heap(self._exception_addr))
        self.mc.TEST(loc, loc)
        self.implement_guard(op, self.mc.JNZ, locs[1:])

    def genop_guard_exception(self, op, locs, resloc):
        loc = locs[0]
        loc1 = locs[1]
        self.mc.MOV(loc1, heap(self._exception_addr))
        self.mc.CMP(loc1, loc)
        self.implement_guard(op, self.mc.JNE, locs[2:])
        if resloc is not None:
            self.mc.MOV(resloc, addr_add(imm(self._exception_addr), imm(WORD)))
        self.mc.MOV(heap(self._exception_addr), imm(0))

    def genop_guard_false(self, op, locs):
        loc = locs[0]
        self.mc.TEST(loc, loc)
        self.implement_guard(op, self.mc.JNZ, locs[1:])

    def genop_guard_value(self, op, locs):
        arg0 = locs[0]
        arg1 = locs[1]
        self.mc.CMP(arg0, arg1)
        self.implement_guard(op, self.mc.JNE, locs[2:])

    def genop_guard_class(self, op, locs):
        offset = 0    # XXX for now, the vtable ptr is at the start of the obj
        self.mc.CMP(mem(locs[0], offset), locs[1])
        self.implement_guard(op, self.mc.JNE, locs[2:])

    #def genop_guard_nonvirtualized(self, op):
    #    STRUCT = op.args[0].concretetype.TO
    #    offset, size = symbolic.get_field_token(STRUCT, 'vable_rti')
    #    assert size == WORD
    #    self.load(eax, op.args[0])
    #    self.mc.CMP(mem(eax, offset), imm(0))
    #    self.implement_guard(op, self.mc.JNE)

    @specialize.arg(2)
    def implement_guard(self, guard_op, emit_jump, locs):
        # XXX add caching, as we need only one for each combination
        # of locs
        recovery_addr = self.get_recovery_code(guard_op, locs)
        emit_jump(rel32(recovery_addr))
        guard_op._jmp_from = self.mc.tell()

    def get_recovery_code(self, guard_op, locs):
        index = self.cpu.make_guard_index(guard_op)
        recovery_code_addr = self.mc2.tell()
        stacklocs = guard_op.stacklocs
        assert len(locs) == len(stacklocs)
        for i in range(len(locs)):
            loc = locs[i]
            if isinstance(loc, REG):
                self.mc2.MOV(stack_pos(stacklocs[i]), loc)
        if (guard_op.opnum == rop.GUARD_EXCEPTION or
            guard_op.opnum == rop.GUARD_NO_EXCEPTION):
            self.mc2.MOV(eax, heap(self._exception_addr))
            self.mc2.MOV(heap(self._exception_bck_addr), eax)
            self.mc2.MOV(eax, addr_add(imm(self._exception_addr), imm(WORD)))
            self.mc2.MOV(addr_add(imm(self._exception_bck_addr), imm(WORD)),
                         eax)
            # clean up the original exception, we don't want
            # to enter more rpython code with exc set
            self.mc2.MOV(heap(self._exception_addr), imm(0))
        self.mc2.PUSH(esp)           # frame address
        self.mc2.PUSH(imm(index))    # index of guard that failed
        self.mc2.CALL(rel32(self.cpu.get_failure_recovery_func_addr()))
        self.mc2.ADD(esp, imm(8))
        self.mc2.JMP(eax)
        return recovery_code_addr

    def genop_call(self, op, arglocs, resloc):
        sizeloc = arglocs[0]
        assert isinstance(sizeloc, IMM32)
        size = sizeloc.value
        arglocs = arglocs[1:]
        extra_on_stack = 0
        for i in range(len(op.args) - 1, 0, -1):
            v = op.args[i]
            loc = arglocs[i]
            if not isinstance(loc, MODRM):
                self.mc.PUSH(loc)
            else:
                # we need to add a bit, ble
                self.mc.PUSH(stack_pos(loc.position + extra_on_stack))
            extra_on_stack += 1
        if isinstance(op.args[0], Const):
            x = rel32(self.cpu.get_box_value_as_int(op.args[0]))
        else:
            # XXX add extra_on_stack?
            x = arglocs[0]
        self.mc.CALL(x)
        self.mc.ADD(esp, imm(WORD * extra_on_stack))
        if size == 1:
            self.mc.AND(eax, imm(0xff))
        elif size == 2:
            self.mc.AND(eax, imm(0xffff))

    genop_call_pure = genop_call

    def not_implemented_op_discard(self, op, arglocs):
        print "not implemented operation: %s" % op.getopname()
        raise NotImplementedError

    def not_implemented_op(self, op, arglocs, resloc):
        print "not implemented operation with res: %s" % op.getopname()
        raise NotImplementedError

    def not_implemented_op_guard(self, op, arglocs, resloc, descr):
        print "not implemented operation (guard): %s" % op.getopname()
        raise NotImplementedError

    #def genop_call__1(self, op, arglocs, resloc):
    #    self.gen_call(op, arglocs, resloc)
    #    self.mc.MOVZX(eax, al)

    #def genop_call__2(self, op, arglocs, resloc):
    #    # XXX test it test it test it
    #    self.gen_call(op, arglocs, resloc)
    #    self.mc.MOVZX(eax, eax)

genop_discard_list = [Assembler386.not_implemented_op_discard] * (RETURN + 1)
genop_list = [Assembler386.not_implemented_op] * rop._LAST
genop_guard_list = [Assembler386.not_implemented_op_guard] * rop._LAST

for name, value in Assembler386.__dict__.iteritems():
    if name.startswith('genop_'):
        opname = name[len('genop_'):]
        if opname == 'return':
            num = RETURN
        else:
            num = getattr(rop, opname.upper())
        if value.func_code.co_argcount == 3:
            genop_discard_list[num] = value
        elif value.func_code.co_argcount == 5:
            genop_guard_list[num] = value
        else:
            genop_list[num] = value

def addr_add(reg_or_imm1, reg_or_imm2, offset=0, scale=0):
    if isinstance(reg_or_imm1, IMM32):
        if isinstance(reg_or_imm2, IMM32):
            return heap(reg_or_imm1.value + offset +
                        (reg_or_imm2.value << scale))
        else:
            return mem(reg_or_imm2, (reg_or_imm1.value << scale) + offset)
    else:
        if isinstance(reg_or_imm2, IMM32):
            return mem(reg_or_imm1, offset + (reg_or_imm2.value << scale))
        else:
            return memSIB(reg_or_imm1, reg_or_imm2, scale, offset)

def addr8_add(reg_or_imm1, reg_or_imm2, offset=0, scale=0):
    if isinstance(reg_or_imm1, IMM32):
        if isinstance(reg_or_imm2, IMM32):
            return heap8(reg_or_imm1.value + (offset << scale) +
                         reg_or_imm2.value)
        else:
            return mem8(reg_or_imm2, reg_or_imm1.value + (offset << scale))
    else:
        if isinstance(reg_or_imm2, IMM32):
            return mem8(reg_or_imm1, (offset << scale) + reg_or_imm2.value)
        else:
            return memSIB8(reg_or_imm1, reg_or_imm2, scale, offset)

def addr16_add(reg_or_imm1, reg_or_imm2, offset=0, scale=0):
    if isinstance(reg_or_imm1, IMM32):
        if isinstance(reg_or_imm2, IMM32):
            return heap16(reg_or_imm1.value + (offset << scale) +
                         reg_or_imm2.value)
        else:
            return mem16(reg_or_imm2, reg_or_imm1.value + (offset << scale))
    else:
        if isinstance(reg_or_imm2, IMM32):
            return mem16(reg_or_imm1, (offset << scale) + reg_or_imm2.value)
        else:
            return memSIB16(reg_or_imm1, reg_or_imm2, scale, offset)

def addr_add_const(reg_or_imm1, offset):
    if isinstance(reg_or_imm1, IMM32):
        return heap(reg_or_imm1.value + offset)
    else:
        return mem(reg_or_imm1, offset)
