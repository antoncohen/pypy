import sys, os
import ctypes
from pypy.jit.backend.x86 import symbolic
from pypy.jit.metainterp.history import Const, ConstInt, Box, ConstPtr, BoxPtr,\
     BoxInt, ConstAddr
from pypy.rpython.lltypesystem import lltype, rffi, ll2ctypes, rstr, llmemory
from pypy.rpython.lltypesystem.rclass import OBJECT
from pypy.rpython.lltypesystem.lloperation import llop
from pypy.annotation import model as annmodel
from pypy.tool.uid import fixid
from pypy.jit.backend.x86.regalloc import (RegAlloc, FRAMESIZE, WORD, REGS,
                                      arg_pos, lower_byte, stack_pos, RETURN)
from pypy.rlib.objectmodel import we_are_translated, specialize
from pypy.jit.backend.x86 import codebuf
from pypy.jit.backend.x86.support import gc_malloc_fnaddr
from pypy.jit.backend.x86.ri386 import *
from pypy.jit.metainterp.resoperation import rop

# our calling convention - we pass three first args as edx, ecx and eax
# and the rest stays on the stack

def repr_of_arg(memo, arg):
    try:
        mv = memo[arg]
    except KeyError:
        mv = len(memo)
        memo[arg] = mv
    if isinstance(arg, ConstInt):
        return "ci(%d,%d)" % (mv, arg.value)
    elif isinstance(arg, ConstPtr):
        return "cp(%d,%d)" % (mv, arg.get_())
    elif isinstance(arg, BoxInt):
        return "bi(%d,%d)" % (mv, arg.value)
    elif isinstance(arg, BoxPtr):
        return "bp(%d,%d)" % (mv, arg.get_())
    elif isinstance(arg, ConstAddr):
        return "ca(%d,%d)" % (mv, arg.get_())
    else:
        #raise NotImplementedError
        return "?%r" % (arg,)

class MachineCodeStack(object):
    MC_SIZE = 1024*1024     # 1MB, but assumed infinite for now

    def __init__(self):
        self.mcstack = []
        self.counter = 0

    def next_mc(self):
        if len(self.mcstack) == self.counter:
            mc = codebuf.MachineCodeBlock(self.MC_SIZE)
            self.mcstack.append(mc)
        else:
            mc = self.mcstack[self.counter]
        self.counter += 1
        return mc

    def give_mc_back(self, mc):
        assert self.mcstack[self.counter - 1] is mc
        self.counter -= 1

class Assembler386(object):
    generic_return_addr = 0
    log_fd = -1
    mc = None
    mc2 = None

    def __init__(self, cpu, translate_support_code=False):
        self.cpu = cpu
        self.verbose = False
        self.rtyper = cpu.rtyper
        self.malloc_func_addr = 0
        self._exception_data = lltype.nullptr(rffi.CArray(lltype.Signed))
        self._exception_addr = 0
        self.mcstack = MachineCodeStack()
        
    def _get_log(self):
        s = os.environ.get('PYPYJITLOG')
        if not s:
            return -1
        s += '.ops'
        try:
            flags = os.O_WRONLY|os.O_CREAT|os.O_TRUNC
            log_fd = os.open(s, flags, 0666)
        except OSError:
            os.write(2, "could not create log file\n")
            return -1
        return log_fd

    def make_sure_mc_exists(self):
        if self.mc is None:
            self._log_fd = self._get_log()
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
            self.mc = self.mcstack.next_mc()
            self.mc2 = self.mcstack.next_mc()
            self.generic_return_addr = self.assemble_generic_return()
            # the address of the function called by 'new': directly use
            # Boehm's GC_malloc function.
            if self.malloc_func_addr == 0:
                self.malloc_func_addr = gc_malloc_fnaddr()

    def eventually_log_operations(self, operations):
        if self._log_fd == -1:
            return
        xxx
        memo = {}
        os.write(self._log_fd, "<<<<<<<<<<\n")
        if guard_op is not None:
            os.write(self._log_fd, "GO(%d)\n" % guard_op._jmp_from)
        for op in operations:
            args = ",".join([repr_of_arg(memo, arg) for arg in op.args])
            os.write(self._log_fd, "%s %s\n" % (op.getopname(), args))
            if op.result is not None:
                os.write(self._log_fd, "  => %s\n" % repr_of_arg(memo,
                                                                 op.result))
            if op.is_guard():
                liveboxes_s = ",".join([repr_of_arg(memo, arg) for arg in
                                        op.liveboxes])
                os.write(self._log_fd, "  .. %s\n" % liveboxes_s)
        os.write(self._log_fd, ">>>>>>>>>>\n")

    def log_failure_recovery(self, gf, guard_index):
        if self._log_fd == -1:
            return
        xxx
        os.write(self._log_fd, 'xxxxxxxxxx\n')
        memo = {}
        reprs = []
        for j in range(len(gf.guard_op.liveboxes)):
            valuebox = gf.cpu.getvaluebox(gf.frame, gf.guard_op, j)
            reprs.append(repr_of_arg(memo, valuebox))
        jmp = gf.guard_op._jmp_from
        os.write(self._log_fd, "%d %d %s\n" % (guard_index, jmp,
                                               ",".join(reprs)))
        os.write(self._log_fd, 'xxxxxxxxxx\n')

    def log_call(self, name, valueboxes):
        if self._log_fd == -1:
            return
        xxx
        memo = {}
        args_s = ','.join([repr_of_arg(memo, box) for box in valueboxes])
        os.write(self._log_fd, "CALL\n")
        os.write(self._log_fd, "%s %s\n" % (name, args_s))

    def assemble(self, tree):
        # the last operation can be 'jump', 'return' or 'guard_pause';
        # a 'jump' can either close a loop, or end a bridge to some
        # previously-compiled code.
        self.make_sure_mc_exists()
        inputargs = tree.inputargs
        op0 = tree.operations[0]
        op0.position = self.mc.tell()
        self.eventually_log_operations(tree)
        regalloc = RegAlloc(self, tree, self.cpu.translate_support_code)
        if not we_are_translated():
            self._regalloc = regalloc # for debugging
        if self.verbose and not we_are_translated():
            import pprint
            print
            pprint.pprint(operations)
            print
            #pprint.pprint(computed_ops)
            #print
        regalloc.walk_operations(tree)
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

    def dump(self, text):
        if not self.verbose:
            return
        _prev = Box._extended_display
        try:
            Box._extended_display = False
            print >> sys.stderr, ' 0x%x  %s' % (fixid(self.mc.tell()), text)
        finally:
            Box._extended_display = _prev

    def assemble_comeback_bootstrap(self, position, arglocs, stacklocs):
        entry_point_addr = self.mc2.tell()
        for i in range(len(arglocs)):
            argloc = arglocs[i]
            if isinstance(argloc, REG):
                self.mc2.MOV(argloc, stack_pos(stacklocs[i]))
            elif not we_are_translated():
                # debug checks
                if not isinstance(argloc, (IMM8, IMM32)):
                    assert repr(argloc) == repr(stack_pos(stacklocs[i]))
        self.mc2.JMP(rel32(position))
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

    def regalloc_load(self, from_loc, to_loc):
        self.mc.MOV(to_loc, from_loc)

    regalloc_store = regalloc_load

    def regalloc_perform(self, op, arglocs, resloc):
        genop_list[op.opnum](self, op, arglocs, resloc)

    def regalloc_perform_discard(self, op, arglocs):
        genop_discard_list[op.opnum](self, op, arglocs)

    def regalloc_perform_with_guard(self, op, guard_op, regalloc,
                                    arglocs, resloc):
        addr = self.implement_guard_recovery(guard_op, regalloc, arglocs)
        xxx
        genop_guard_list[op.opnum](self, op, guard_op, arglocs, resloc)

    def _unaryop(asmop):
        def genop_unary(self, op, arglocs, resloc):
            getattr(self.mc, asmop)(arglocs[0])
        return genop_unary

    def _binaryop(asmop, can_swap=False):
        def genop_binary(self, op, arglocs, result_loc):
            getattr(self.mc, asmop)(arglocs[0], arglocs[1])
        return genop_binary

    def _binaryop_ovf(asmop, can_swap=False, is_mod=False):
        def genop_binary_ovf(self, op, guard_op, arglocs, result_loc):
            if is_mod:
                self.mc.CDQ()
                self.mc.IDIV(ecx)
            else:
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

    def _cmpop_guard(cond, rev_cond, false_cond, false_rev_cond):
        def genop_cmp_guard(self, op, guard_op, arglocs, result_loc):
            if isinstance(op.args[0], Const):
                self.mc.CMP(arglocs[1], arglocs[0])
                if guard_op.opnum == rop.GUARD_FALSE:
                    name = 'J' + rev_cond
                    self.implement_guard(guard_op, getattr(self.mc, name),
                                         arglocs[2:])
                else:
                    name = 'J' + false_rev_cond
                    self.implement_guard(guard_op, getattr(self.mc, name),
                                         arglocs[2:])
            else:
                self.mc.CMP(arglocs[0], arglocs[1])
                if guard_op.opnum == rop.GUARD_FALSE:
                    self.implement_guard(guard_op, getattr(self.mc, 'J' + cond),
                                         arglocs[2:])
                else:
                    name = 'J' + false_cond
                    self.implement_guard(guard_op, getattr(self.mc, name),
                                         arglocs[2:])
        return genop_cmp_guard
            

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
    genop_int_or  = _binaryop("OR", True)
    genop_int_xor = _binaryop("XOR", True)

    genop_uint_add = genop_int_add
    genop_uint_sub = genop_int_sub
    genop_uint_mul = genop_int_mul
    xxx_genop_uint_and = genop_int_and

    genop_guard_int_mul_ovf = _binaryop_ovf("IMUL", True)
    genop_guard_int_sub_ovf = _binaryop_ovf("SUB")
    genop_guard_int_add_ovf = _binaryop_ovf("ADD", True)
    genop_guard_int_mod_ovf = _binaryop_ovf("IDIV", is_mod=True)

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

    genop_guard_int_lt = _cmpop_guard("L", "G", "GE", "LE")
    genop_guard_int_le = _cmpop_guard("LE", "GE", "G", "L")
    genop_guard_int_eq = _cmpop_guard("E", "NE", "NE", "E")
    genop_guard_int_ne = _cmpop_guard("NE", "E", "E", "NE")
    genop_guard_int_gt = _cmpop_guard("G", "L", "LE", "GE")
    genop_guard_int_ge = _cmpop_guard("GE", "LE", "L", "G")

    genop_guard_uint_gt = _cmpop_guard("A", "B", "BE", "AE")
    genop_guard_uint_lt = _cmpop_guard("B", "A", "AE", "BE")
    genop_guard_uint_le = _cmpop_guard("BE", "AE", "A", "B")
    genop_guard_uint_ge = _cmpop_guard("AE", "BE", "B", "A")

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

    def genop_discard_setfield_gc(self, op, arglocs):
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

    def genop_discard_setarrayitem_gc(self, op, arglocs):
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

    def genop_discard_strsetitem(self, op, arglocs):
        base_loc, ofs_loc, val_loc = arglocs
        basesize, itemsize, ofs_length = symbolic.get_array_token(rstr.STR,
                                              self.cpu.translate_support_code)
        self.mc.MOV(addr8_add(base_loc, ofs_loc, basesize),
                    lower_byte(val_loc))

    genop_discard_setfield_raw = genop_discard_setfield_gc

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

    def make_merge_point(self, tree, locs, stacklocs):
        pos = self.mc.tell()
        tree.position = pos
        tree.comeback_bootstrap_addr = self.assemble_comeback_bootstrap(pos,
                                                        locs, stacklocs)

    def genop_discard_return(self, op, locs):
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

    def genop_discard_jump(self, op, locs):
        targetmp = op.jump_target
        self.mc.JMP(rel32(targetmp.position))

    def genop_discard_guard_true(self, op, locs):
        loc = locs[0]
        self.mc.TEST(loc, loc)
        self.implement_guard(op, self.mc.JZ, locs[1:])

    def genop_discard_guard_no_exception(self, op, locs):
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

    def genop_discard_guard_false(self, op, locs):
        loc = locs[0]
        self.mc.TEST(loc, loc)
        self.implement_guard(op, self.mc.JNZ, locs[1:])

    def genop_discard_guard_value(self, op, locs):
        arg0 = locs[0]
        arg1 = locs[1]
        self.mc.CMP(arg0, arg1)
        self.implement_guard(op, self.mc.JNE, locs[2:])

    def genop_discard_guard_class(self, op, locs):
        offset = 0    # XXX for now, the vtable ptr is at the start of the obj
        self.mc.CMP(mem(locs[0], offset), locs[1])
        self.implement_guard(op, self.mc.JNE, locs[2:])

    #def genop_discard_guard_nonvirtualized(self, op):
    #    STRUCT = op.args[0].concretetype.TO
    #    offset, size = symbolic.get_field_token(STRUCT, 'vable_rti')
    #    assert size == WORD
    #    self.load(eax, op.args[0])
    #    self.mc.CMP(mem(eax, offset), imm(0))
    #    self.implement_guard(op, self.mc.JNE)

    def implement_guard_recovery(self, guard_op, locs, regalloc):
        oldmc = self.mc
        self.mc = self.mc2
        self.mc2 = self.mcstack.next_mc()
        regalloc._walk_operations(guard_op.suboperations)
        xxx
        self.mcstack.give_mc_back(self.mc2)
        self.mc2 = self.mc
        self.mc = oldmc

    @specialize.arg(2)
    def implement_guard(self, guard_op, emit_jump, locs):
        xxx
        # XXX add caching, as we need only one for each combination
        # of locs
        recovery_addr = self.get_recovery_code(guard_op, locs)
        emit_jump(rel32(recovery_addr))
        guard_op._jmp_from = self.mc.tell()

    def get_recovery_code(self, guard_op, locs):
        xxx
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
    if name.startswith('genop_discard_'):
        opname = name[len('genop_discard_'):]
        if opname == 'return':
            num = RETURN
        else:
            num = getattr(rop, opname.upper())
        genop_discard_list[num] = value
    elif name.startswith('genop_guard_') and name != 'genop_guard_exception': 
        opname = name[len('genop_guard_'):]
        num = getattr(rop, opname.upper())
        genop_guard_list[num] = value
    elif name.startswith('genop_'):
        opname = name[len('genop_'):]
        num = getattr(rop, opname.upper())
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
