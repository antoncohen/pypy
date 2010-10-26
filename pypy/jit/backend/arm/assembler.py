from pypy.jit.backend.arm import conditions as c
from pypy.jit.backend.arm import locations
from pypy.jit.backend.arm import registers as r
from pypy.jit.backend.arm.arch import WORD, FUNC_ALIGN
from pypy.jit.backend.arm.codebuilder import ARMv7Builder, ARMv7InMemoryBuilder
from pypy.jit.backend.arm.regalloc import ARMRegisterManager
from pypy.jit.backend.llsupport.regalloc import compute_vars_longevity
from pypy.jit.metainterp.history import ConstInt, BoxInt, Box, BasicFailDescr
from pypy.jit.metainterp.resoperation import rop
from pypy.rlib import rgc
from pypy.rpython.annlowlevel import llhelper
from pypy.rpython.lltypesystem import lltype, rffi, llmemory
# XXX Move to llsupport
from pypy.jit.backend.x86.support import values_array


class AssemblerARM(object):

    def __init__(self, cpu, failargs_limit=1000):
        self.mc = ARMv7Builder()
        self.cpu = cpu
        self.fail_boxes_int = values_array(lltype.Signed, failargs_limit)
        self._debug_asm = True

        self._gen_exit_path()
        self.align()
        self.mc._start_addr = self.mc.curraddr()


    def setup_failure_recovery(self):

        @rgc.no_collect
        def failure_recovery_func(mem_loc, stackloc):
            """mem_loc is a structure in memory describing where the values for
            the failargs are stored. stacklock is the address of the stack
            section where the registers were saved."""
            enc = rffi.cast(rffi.CCHARP, mem_loc)
            stack = rffi.cast(rffi.CCHARP, stackloc)
            return self.decode_registers_and_descr(enc, stack)

        self.failure_recovery_func = failure_recovery_func

    @rgc.no_collect
    def decode_registers_and_descr(self, enc, stack):
        """Decode locations encoded in memory at enc and write the values to
        the failboxes.
        Registers are saved on the stack
        XXX Rest to follow"""
        i = -1
        fail_index = -1
        while(True):
            i += 1
            fail_index += 1
            r = enc[i]
            if r == '\xFE':
                continue
            if r == '\xFF':
                break
            if r == '\xFD':
                # imm value
                value = self.decode32(enc, i+1)
                i += 4
            else:
                reg = ord(enc[i])
                value = self.decode32(stack, reg*WORD)

            self.fail_boxes_int.setitem(fail_index, value)

        assert enc[i] == '\xFF'
        descr = self.decode32(enc, i+1)
        self.fail_boxes_count = fail_index
        return descr

    def decode32(self, mem, index):
        highval = ord(mem[index+3])
        if highval >= 128:
            highval -= 256
        return (ord(mem[index])
                | ord(mem[index+1]) << 8
                | ord(mem[index+2]) << 16
                | highval << 24)

    def encode32(self, mem, i, n):
        mem[i] = chr(n & 0xFF)
        mem[i+1] = chr((n >> 8) & 0xFF)
        mem[i+2] = chr((n >> 16) & 0xFF)
        mem[i+3] = chr((n >> 24) & 0xFF)

    def _gen_exit_path(self):
        self.setup_failure_recovery()
        functype = lltype.Ptr(lltype.FuncType([lltype.Signed, lltype.Signed], lltype.Signed))
        decode_registers_addr = llhelper(functype, self.failure_recovery_func)
        self.mc.PUSH(range(12))     # registers r0 .. r11
        self.mc.MOV_rr(r.r0.value, r.lr.value)  # move mem block address, to r0 to pass as
                                    # parameter to next procedure call
        self.mc.MOV_rr(r.r1.value, r.sp.value)  # pass the current stack pointer as second param
        self.mc.gen_load_int(r.r2.value, rffi.cast(lltype.Signed, decode_registers_addr))
        self.mc.gen_load_int(r.lr.value, self.mc.curraddr()+self.mc.size_of_gen_load_int+WORD)
        self.mc.MOV_rr(r.pc.value, r.r2.value)
        self.mc.MOV_rr(r.ip.value, r.r0.value)
        self.mc.LDM(r.sp.value, range(12), w=1) # XXX Replace with POP instr. someday

        self.mc.MOV_rr(r.r0.value, r.ip.value)

        self.gen_func_epilog()

    def _gen_path_to_exit_path(self, op, args, regalloc, fcond=c.AL):
        box = Box()
        reg = regalloc.try_allocate_reg(box)
        # XXX free this memory
        mem = lltype.malloc(rffi.CArray(lltype.Char), (len(args)+5)*4, flavor='raw')
        i = 0
        j = 0
        while(i < len(args)):
            if args[i]:
                if not isinstance(args[i], ConstInt):
                    curreg = regalloc.try_allocate_reg(args[i])
                    mem[j] = chr(curreg.value)
                    j += 1
                else:
                    mem[j] = '\xFD'
                    self.encode32(mem, j+1, args[i].getint())
                    j += 5
            else:
                mem[j] = '\xFE'
                j += 1
            i += 1

        mem[j] = chr(0xFF)
        memaddr = rffi.cast(lltype.Signed, mem)


        n = self.cpu.get_fail_descr_number(op.getdescr())
        self.encode32(mem, j+1, n)
        self.mc.gen_load_int(r.lr.value, memaddr, cond=fcond)
        self.mc.gen_load_int(reg.value, self.mc.baseaddr(), cond=fcond)
        self.mc.MOV_rr(r.pc.value, reg.value, cond=fcond)

        # This register is used for patching when assembling a bridge
        # guards going to be patched are allways conditional
        if fcond != c.AL:
            op.getdescr()._arm_guard_reg = reg
        return memaddr

    def align(self):
        while(self.mc.curraddr() % FUNC_ALIGN != 0):
            self.mc.writechar(chr(0))

    def gen_func_epilog(self,cond=c.AL):
        self.mc.LDM(r.sp.value, [reg.value for reg in r.callee_restored_registers], cond=cond, w=1)

    def gen_func_prolog(self):
        self.mc.PUSH([reg.value for reg in r.callee_saved_registers])

    def gen_bootstrap_code(self, inputargs, regalloc, looptoken):
        regs = []
        for i in range(len(inputargs)):
            reg = regalloc.try_allocate_reg(inputargs[i])
            addr = self.fail_boxes_int.get_addr_for_num(i)
            self.mc.gen_load_int(reg.value, addr)
            self.mc.LDR_ri(reg.value, reg.value)
            regs.append(reg)
        looptoken._arm_arglocs = regs

    # cpu interface
    def assemble_loop(self, inputargs, operations, looptoken):
        longevity = compute_vars_longevity(inputargs, operations)
        regalloc = ARMRegisterManager(longevity, assembler=self.mc)
        self.align()
        loop_start=self.mc.curraddr()
        self.gen_func_prolog()
        self.gen_bootstrap_code(inputargs, regalloc, looptoken)
        loop_head=self.mc.curraddr()
        looptoken._arm_bootstrap_code = loop_start
        looptoken._arm_loop_code = loop_head
        fcond=c.AL
        for op in operations:
            opnum = op.getopnum()
            fcond = self.operations[opnum](self, op, regalloc, fcond)
        self.gen_func_epilog()
        if self._debug_asm:
            self._dump_trace('loop.asm')
        print 'Done assembling'

    def assemble_bridge(self, faildescr, inputargs, operations):
        enc = rffi.cast(rffi.CCHARP, faildescr._failure_recovery_code)
        longevity = compute_vars_longevity(inputargs, operations)
        regalloc = ARMRegisterManager(longevity, assembler=self.mc)

        regalloc.update_bindings(enc, inputargs)
        bridge_head = self.mc.curraddr()

        fcond = c.AL
        for op in operations:
            opnum = op.getopnum()
            fcond = self.operations[opnum](self, op, regalloc, fcond)
        self.gen_func_epilog()
        print 'Done building bridges'
        self.patch_trace(faildescr, bridge_head)
        print 'Done patching trace'
        if self._debug_asm:
            self._dump_trace('bridge.asm')


    def _dump_trace(self, name):
        self.mc._dump_trace(name)

    def patch_trace(self, faildescr, bridge_addr):
        # XXX make sure there is enough space at patch target
        fcond = faildescr._arm_guard_cond
        b = ARMv7InMemoryBuilder(faildescr._arm_guard_code, faildescr._arm_guard_code+100)
        reg = faildescr._arm_guard_reg
        b.gen_load_int(reg.value, bridge_addr, fcond)
        b.MOV_rr(r.pc.value, reg.value, cond=fcond)


    # Resoperations
    def emit_op_jump(self, op, regalloc, fcond):
        tmp = Box()
        tmpreg = regalloc.try_allocate_reg(tmp)
        registers = op.getdescr()._arm_arglocs
        for i in range(op.numargs()):
            reg = regalloc.try_allocate_reg(op.getarg(i))
            inpreg = registers[i]
            # XXX only if every value is in a register
            self.mc.MOV_rr(inpreg.value, reg.value)
        loop_code = op.getdescr()._arm_loop_code
        self.mc.gen_load_int(tmpreg.value, loop_code)
        self.mc.MOV_rr(r.pc.value, tmpreg.value)
        regalloc.possibly_free_var(tmpreg)
        return fcond

    def emit_op_finish(self, op, regalloc, fcond):
        self._gen_path_to_exit_path(op, op.getarglist(), regalloc, fcond)
        return fcond

    def emit_op_int_le(self, op, regalloc, fcond):
        reg = regalloc.try_allocate_reg(op.getarg(0))
        assert isinstance(op.getarg(1), ConstInt)
        self.mc.CMP(reg.value, op.getarg(1).getint())
        return c.GT

    def emit_op_int_eq(self, op, regalloc, fcond):
        reg = regalloc.try_allocate_reg(op.getarg(0))
        assert isinstance(op.getarg(1), ConstInt)
        self.mc.CMP(reg.value, op.getarg(1).getint())
        return c.EQ

    def emit_op_int_add(self, op, regalloc, fcond):
        # assuming only one argument is constant
        res = regalloc.try_allocate_reg(op.result)
        if isinstance(op.getarg(1), ConstInt):
            reg = regalloc.try_allocate_reg(op.getarg(0))
            arg1 = op.getarg(1)
            self.mc.ADD_ri(res.value, reg.value, arg1.getint())
        elif isinstance(op.getarg(0), ConstInt):
            reg = regalloc.try_allocate_reg(op.getarg(1))
            arg1 = op.getarg(0)
            self.mc.ADD_ri(res.value, reg.value, arg1.getint())
        else:
            r1 = regalloc.try_allocate_reg(op.getarg(0))
            r2 = regalloc.try_allocate_reg(op.getarg(1))
            self.mc.ADD_rr(res.value, r1.value, r2.value)

        regalloc.possibly_free_vars_for_op(op)
        return fcond

    def emit_op_int_sub(self, op, regalloc, fcond):
        # assuming only one argument is constant
        res = regalloc.try_allocate_reg(op.result)
        if isinstance(op.getarg(1), ConstInt):
            reg = regalloc.try_allocate_reg(op.getarg(0))
            arg1 = op.getarg(1)
            self.mc.SUB_ri(res.value, reg.value, arg1.getint())
        elif isinstance(op.getarg(0), ConstInt):
            reg = regalloc.try_allocate_reg(op.getarg(1))
            arg1 = op.getarg(0)
            self.mc.SUB_ri(res.value, reg.value, arg1.getint())
        else:
            r1 = regalloc.try_allocate_reg(op.getarg(0))
            r2 = regalloc.try_allocate_reg(op.getarg(1))
            self.mc.SUB_rr(res.value, r1.value, r2.value)

        regalloc.possibly_free_vars_for_op(op)
        return fcond

    # Guards
    def _emit_guard(self, op, regalloc, fcond):
        descr = op.getdescr()
        assert isinstance(descr, BasicFailDescr)
        descr._arm_guard_code = self.mc.curraddr()
        memaddr = self._gen_path_to_exit_path(op, op.getfailargs(), regalloc, fcond)
        descr._failure_recovery_code = memaddr
        descr._arm_guard_cond = fcond

    def emit_op_guard_true(self, op, regalloc, fcond):
        assert fcond == c.GT
        self._emit_guard(op, regalloc, fcond)
        return c.AL

    def emit_op_guard_false(self, op, regalloc, fcond):
        assert fcond == c.EQ
        self._emit_guard(op, regalloc, fcond)
        return c.AL


def make_operation_list():
    def notimplemented(self, op, regalloc, fcond):
        raise NotImplementedError

    operations = [None] * (rop._LAST+1)
    for key, value in rop.__dict__.items():
        key = key.lower()
        if key.startswith('_'):
            continue
        methname = 'emit_op_%s' % key
        if hasattr(AssemblerARM, methname):
            func = getattr(AssemblerARM, methname).im_func
        else:
            func = notimplemented
        operations[value] = func
    return operations

AssemblerARM.operations = make_operation_list()
