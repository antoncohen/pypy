
""" Register allocation scheme.
"""

from pypy.jit.metainterp.history import (Box, Const, ConstInt, ConstPtr,
                                         ResOperation, ConstAddr, BoxPtr)
from pypy.jit.backend.x86.ri386 import *
from pypy.rpython.lltypesystem import lltype, ll2ctypes, rffi, rstr
from pypy.rlib.objectmodel import we_are_translated
from pypy.rlib.unroll import unrolling_iterable
from pypy.rlib import rgc
from pypy.jit.backend.llsupport import symbolic
from pypy.jit.backend.x86.jump import remap_stack_layout
from pypy.jit.metainterp.resoperation import rop
from pypy.jit.backend.llsupport.descr import BaseFieldDescr, BaseArrayDescr
from pypy.jit.backend.llsupport.descr import BaseCallDescr

REGS = [eax, ecx, edx, ebx, esi, edi]
WORD = 4

class NoVariableToSpill(Exception):
    pass

class TempBox(Box):
    def __init__(self):
        pass

    def __repr__(self):
        return "<TempVar at %s>" % (id(self),)

class checkdict(dict):
    def __setitem__(self, key, value):
        assert isinstance(key, Box)
        dict.__setitem__(self, key, value)

def newcheckdict():
    if we_are_translated():
        return {}
    return checkdict()

def convert_to_imm(c):
    if isinstance(c, ConstInt):
        return imm(c.value)
    elif isinstance(c, ConstPtr):
        if we_are_translated() and c.value and rgc.can_move(c.value):
            print "convert_to_imm: ConstPtr needs special care"
            raise AssertionError
        return imm(rffi.cast(lltype.Signed, c.value))
    elif isinstance(c, ConstAddr):
        return imm(ll2ctypes.cast_adr_to_int(c.value))
    else:
        print "convert_to_imm: got a %s" % c
        raise AssertionError

class RegAlloc(object):
    max_stack_depth = 0
    exc = False
    
    def __init__(self, assembler, tree, translate_support_code=False,
                 guard_op=None):
        # variables that have place in register
        self.assembler = assembler
        self.translate_support_code = translate_support_code
        cpu = self.assembler.cpu
        self.reg_bindings = newcheckdict()
        self.stack_bindings = newcheckdict()
        self.tree = tree
        if guard_op is not None:
            locs = guard_op._x86_faillocs
            cpu.gc_ll_descr.rewrite_assembler(cpu, guard_op.suboperations)
            inpargs = [arg for arg in guard_op._fail_op.args if
                       isinstance(arg, Box)]
            self._compute_vars_longevity(inpargs, guard_op.suboperations)
            self.inputargs = inpargs
            self.position = -1
            self._update_bindings(locs, inpargs)
            self.current_stack_depth = guard_op._x86_current_stack_depth
            self.loop_consts = {}
        else:
            cpu.gc_ll_descr.rewrite_assembler(cpu, tree.operations)
            self._compute_vars_longevity(tree.inputargs, tree.operations)
            # compute longevity of variables
            jump = tree.operations[-1]
            loop_consts = self._compute_loop_consts(tree.inputargs, jump)
            self.loop_consts = loop_consts
            self.current_stack_depth = 0
            self.free_regs = REGS[:]

    def _update_bindings(self, locs, args):
        newlocs = []
        for loc in locs:
            if not isinstance(loc, IMM8) and not isinstance(loc, IMM32):
                newlocs.append(loc)
        locs = newlocs
        assert len(locs) == len(args)
        used = {}
        for i in range(len(locs)):
            v = args[i]
            loc = locs[i]
            if isinstance(loc, REG) and self.longevity[v][1] > -1:
                self.reg_bindings[v] = loc
                used[loc] = None
            else:
                self.stack_bindings[v] = loc
        self.free_regs = []
        for reg in REGS:
            if reg not in used:
                self.free_regs.append(reg)
        self._check_invariants()

    def _compute_loop_consts(self, inputargs, jump):
        if jump.opnum != rop.JUMP or jump.jump_target is not self.tree:
            loop_consts = {}
        else:
            loop_consts = {}
            for i in range(len(inputargs)):
                if inputargs[i] is jump.args[i]:
                    loop_consts[inputargs[i]] = i
        return loop_consts

    def _check_invariants(self):
        if not we_are_translated():
            # make sure no duplicates
            assert len(dict.fromkeys(self.reg_bindings.values())) == len(self.reg_bindings)
            assert (len(dict.fromkeys([str(i) for i in self.stack_bindings.values()]
                                      )) == len(self.stack_bindings))
            rev_regs = dict.fromkeys(self.reg_bindings.values())
            for reg in self.free_regs:
                assert reg not in rev_regs
            assert len(rev_regs) + len(self.free_regs) == len(REGS)
        else:
            assert len(self.reg_bindings) + len(self.free_regs) == len(REGS)
        if self.longevity:
            for v in self.reg_bindings:
                assert self.longevity[v][1] > self.position

    def Load(self, v, from_loc, to_loc):
        if not we_are_translated():
            self.assembler.dump('%s <- %s(%s)' % (to_loc, v, from_loc))
        self.assembler.regalloc_load(from_loc, to_loc)

    def Store(self, v, from_loc, to_loc):
        if not we_are_translated():
            self.assembler.dump('%s(%s) -> %s' % (v, from_loc, to_loc))
        self.assembler.regalloc_store(from_loc, to_loc)

    def Perform(self, op, arglocs, result_loc):
        if not we_are_translated():
            self.assembler.dump('%s <- %s(%s)' % (result_loc, op, arglocs))
        self.assembler.regalloc_perform(op, arglocs, result_loc)

    def perform_with_guard(self, op, guard_op, locs, arglocs, result_loc):
        guard_op._x86_current_stack_depth = self.current_stack_depth
        if not we_are_translated():
            self.assembler.dump('%s <- %s(%s) [GUARDED]' % (result_loc, op,
                                                            arglocs))
        self.assembler.regalloc_perform_with_guard(op, guard_op, locs,
                                                   arglocs, result_loc)

    def perform_guard(self, op, locs, arglocs, result_loc):
        op._x86_current_stack_depth = self.current_stack_depth
        if not we_are_translated():
            if result_loc is not None:
                self.assembler.dump('%s <- %s(%s)' % (result_loc, op, arglocs))
            else:
                self.assembler.dump('%s(%s)' % (op, arglocs))
        self.assembler.regalloc_perform_guard(op, locs, arglocs, result_loc)

    def PerformDiscard(self, op, arglocs):
        if not we_are_translated():
            self.assembler.dump('%s(%s)' % (op, arglocs))
        self.assembler.regalloc_perform_discard(op, arglocs)

    def can_optimize_cmp_op(self, op, i, operations):
        if not (op.is_comparison() or op.opnum == rop.OOISNULL or
                op.opnum == rop.OONONNULL):
            return False
        if (operations[i + 1].opnum != rop.GUARD_TRUE and
            operations[i + 1].opnum != rop.GUARD_FALSE):
            return False
        if operations[i + 1].args[0] is not op.result:
            return False
        if (self.longevity[op.result][1] > i + 1 or
            op.result in operations[i + 1].suboperations[0].args):
            return False
        return True

    def walk_operations(self, tree):
        # first pass - walk along the operations in order to find
        # load/store places
        self.position = -1
        operations = tree.operations
        self.process_inputargs(tree)
        self._walk_operations(operations)

    #def walk_guard_ops(self, inputargs, operations, exc):
    #    self.exc = exc
    #    old_regalloc = self.assembler._regalloc
    #    self.assembler._regalloc = self
    #    self._walk_operations(operations)
    #    self.assembler._regalloc = old_regalloc

    def _walk_operations(self, operations):
        i = 0
        self.operations = operations
        while i < len(operations):
            op = operations[i]
            self.position = i
            if op.has_no_side_effect() and op.result not in self.longevity:
                i += 1
                self.eventually_free_vars(op.args)
                continue
            if self.can_optimize_cmp_op(op, i, operations):
                oplist[op.opnum](self, op, operations[i + 1])
                i += 1
            else:
                oplist[op.opnum](self, op, None)
            self.eventually_free_var(op.result)
            self._check_invariants()
            i += 1
        assert not self.reg_bindings
        jmp = operations[-1]
        self.max_stack_depth = max(self.current_stack_depth,
                                   self.max_stack_depth)

    def _compute_vars_longevity(self, inputargs, operations):
        # compute a dictionary that maps variables to index in
        # operations that is a "last-time-seen"
        longevity = {}
        start_live = {}
        for inputarg in inputargs:
            start_live[inputarg] = 0
        for i in range(len(operations)):
            op = operations[i]
            if op.result is not None:
                start_live[op.result] = i
            for arg in op.args:
                if isinstance(arg, Box):
                    if arg not in start_live:
                        print "Bogus arg in operation %d at %d" % (op.opnum, i)
                        raise AssertionError
                    longevity[arg] = (start_live[arg], i)
            if op.is_guard():
                for arg in op.suboperations[0].args:
                    if isinstance(arg, Box):
                        if arg not in start_live:
                            print "Bogus arg in guard %d at %d" % (op.opnum, i)
                            raise AssertionError
                        longevity[arg] = (start_live[arg], i)
        for arg in inputargs:
            if arg not in longevity:
                longevity[arg] = (-1, -1)
        for arg in longevity:
            assert isinstance(arg, Box)
        self.longevity = longevity

#     def _compute_inpargs(self, guard):
#         operations = guard.suboperations
#         longevity = {}
#         end = {}
#         for i in range(len(operations)-1, -1, -1):
#             op = operations[i]
#             if op.is_guard():
#                 for arg in op.suboperations[0].args:
#                     if isinstance(arg, Box) and arg not in end:
#                         end[arg] = i
#             for arg in op.args:
#                 if isinstance(arg, Box) and arg not in end:
#                     end[arg] = i
#             if op.result:
#                 if op.result in end:
#                     longevity[op.result] = (i, end[op.result])
#                     del end[op.result]
#                 # otherwise this var is never ever used
#         for v, e in end.items():
#             longevity[v] = (0, e)
#         inputargs = end.keys()
#         for arg in longevity:
#             assert isinstance(arg, Box)
#         for arg in inputargs:
#             assert isinstance(arg, Box)
#         return inputargs, longevity

    def try_allocate_reg(self, v, selected_reg=None, need_lower_byte=False):
        assert not isinstance(v, Const)
        if selected_reg is not None:
            res = self.reg_bindings.get(v, None)
            if res:
                if res is selected_reg:
                    return res
                else:
                    del self.reg_bindings[v]
                    self.free_regs.append(res)
            if selected_reg in self.free_regs:
                self.free_regs = [reg for reg in self.free_regs
                                  if reg is not selected_reg]
                self.reg_bindings[v] = selected_reg
                return selected_reg
            return None
        if need_lower_byte:
            loc = self.reg_bindings.get(v, None)
            if loc is not None and loc is not edi and loc is not esi:
                return loc
            for i in range(len(self.free_regs)):
                reg = self.free_regs[i]
                if reg is not edi and reg is not esi:
                    if loc is not None:
                        self.free_regs[i] = loc
                    else:
                        del self.free_regs[i]
                    self.reg_bindings[v] = reg
                    return reg
            return None
        try:
            return self.reg_bindings[v]
        except KeyError:
            if self.free_regs:
                loc = self.free_regs.pop()
                self.reg_bindings[v] = loc
                return loc

    def return_constant(self, v, forbidden_vars, selected_reg=None,
                        imm_fine=True):
        assert isinstance(v, Const)
        if selected_reg or not imm_fine:
            # this means we cannot have it in IMM, eh
            if selected_reg in self.free_regs:
                self.Load(v, convert_to_imm(v), selected_reg)
                return selected_reg
            if selected_reg is None and self.free_regs:
                loc = self.free_regs.pop()
                self.Load(v, convert_to_imm(v), loc)
                return loc
            loc = self._spill_var(v, forbidden_vars, selected_reg)
            self.free_regs.append(loc)
            self.Load(v, convert_to_imm(v), loc)
            return loc
        return convert_to_imm(v)

    def force_allocate_reg(self, v, forbidden_vars, selected_reg=None,
                           need_lower_byte=False):
        if isinstance(v, TempBox):
            self.longevity[v] = (self.position, self.position)
        loc = self.try_allocate_reg(v, selected_reg,
                                    need_lower_byte=need_lower_byte)
        if loc:
            return loc
        loc = self._spill_var(v, forbidden_vars, selected_reg,
                              need_lower_byte=need_lower_byte)
        prev_loc = self.reg_bindings.get(v, None)
        if prev_loc is not None:
            self.free_regs.append(prev_loc)
        self.reg_bindings[v] = loc
        return loc

    def _spill_var(self, v, forbidden_vars, selected_reg,
                   need_lower_byte=False):
        v_to_spill = self.pick_variable_to_spill(v, forbidden_vars,
                               selected_reg, need_lower_byte=need_lower_byte)
        loc = self.reg_bindings[v_to_spill]
        del self.reg_bindings[v_to_spill]
        if v_to_spill not in self.stack_bindings:
            newloc = self.stack_loc(v_to_spill)
            self.Store(v_to_spill, loc, newloc)
        return loc

    def stack_loc(self, v):
        try:
            res = self.stack_bindings[v]
        except KeyError:
            newloc = stack_pos(self.current_stack_depth)
            self.stack_bindings[v] = newloc
            self.current_stack_depth += 1
            res = newloc
        assert isinstance(res, MODRM)
        return res

    def make_sure_var_in_reg(self, v, forbidden_vars, selected_reg=None,
                             imm_fine=True, need_lower_byte=False):
        if isinstance(v, Const):
            return self.return_constant(v, forbidden_vars, selected_reg,
                                        imm_fine)
        
        prev_loc = self.loc(v)
        loc = self.force_allocate_reg(v, forbidden_vars, selected_reg,
                                      need_lower_byte=need_lower_byte)
        if prev_loc is not loc:
            self.Load(v, prev_loc, loc)
        return loc

    def reallocate_from_to(self, from_v, to_v):
        reg = self.reg_bindings[from_v]
        del self.reg_bindings[from_v]
        self.reg_bindings[to_v] = reg

    def eventually_free_var(self, v):
        if isinstance(v, Const) or v not in self.reg_bindings:
            return
        if v not in self.longevity or self.longevity[v][1] <= self.position:
            self.free_regs.append(self.reg_bindings[v])
            del self.reg_bindings[v]

    def eventually_free_vars(self, vlist):
        for v in vlist:
            self.eventually_free_var(v)

    def loc(self, v):
        if isinstance(v, Const):
            return convert_to_imm(v)
        try:
            return self.reg_bindings[v]
        except KeyError:
            return self.stack_bindings[v]

    def _compute_next_usage(self, v, pos):
        for i in range(pos, len(self.operations)):
            if v in self.operations[i].args:
                return i
            if i > self.longevity[v][1]:
                return -1
        return -1

    def pick_variable_to_spill(self, v, forbidden_vars, selected_reg=None,
                               need_lower_byte=False):
        candidates = []
        for next in self.reg_bindings:
            reg = self.reg_bindings[next]
            if next in forbidden_vars:
                continue
            if selected_reg is not None:
                if reg is selected_reg:
                    return next
                else:
                    continue
            if need_lower_byte and (reg is esi or reg is edi):
                continue
            return next
        raise NoVariableToSpill
        # below is the slightly better (even optimal, under certain
        # assumptions) algorithm, which is slow. Just go with the
        # first hit
        #if len(candidates) == 1:
        #    return candidates[0]
        #max = 0
        #chosen = None
        #for one in candidates:
        #    next_usage = self._compute_next_usage(one, self.position)
        #    if next_usage == -1:
        #        return one
        #    elif next_usage > max:
        #        next_usage = max
        #        chosen = one
        #return chosen

    def move_variable_away(self, v, prev_loc):
        reg = None
        if self.free_regs:
            loc = self.free_regs.pop()
            self.reg_bindings[v] = loc
            self.Load(v, prev_loc, loc)
        else:
            loc = self.stack_loc(v)
            self.Store(v, prev_loc, loc)

    def force_result_in_reg(self, result_v, v, forbidden_vars):
        """ Make sure that result is in the same register as v
        and v is copied away if it's further used
        """
        if isinstance(v, Const):
            loc = self.make_sure_var_in_reg(v, forbidden_vars,
                                            imm_fine=False)
            assert not isinstance(loc, IMM8)
            self.reg_bindings[result_v] = loc
            self.free_regs = [reg for reg in self.free_regs if reg is not loc]
            return loc
        if v not in self.reg_bindings:
            prev_loc = self.stack_bindings[v]
            loc = self.force_allocate_reg(v, forbidden_vars)
            self.Load(v, prev_loc, loc)
        assert v in self.reg_bindings
        if self.longevity[v][1] > self.position:
            # we need to find a new place for variable v and
            # store result in the same place
            loc = self.reg_bindings[v]
            del self.reg_bindings[v]
            if v not in self.stack_bindings:
                self.move_variable_away(v, loc)
            self.reg_bindings[result_v] = loc
        else:
            self.reallocate_from_to(v, result_v)
            loc = self.reg_bindings[result_v]
        return loc

    def locs_for_fail(self, guard_op):
        assert len(guard_op.suboperations) == 1
        return [self.loc(v) for v in guard_op.suboperations[0].args]

    def process_inputargs(self, tree):
        # XXX we can sort out here by longevity if we need something
        # more optimal
        inputargs = tree.inputargs
        locs = [None] * len(inputargs)
        # Don't use REGS[0] for passing arguments around a loop.
        # Must be kept in sync with consider_jump().
        tmpreg = self.free_regs.pop(0)
        assert tmpreg == REGS[0]
        for i in range(len(inputargs)):
            arg = inputargs[i]
            assert not isinstance(arg, Const)
            reg = None
            if arg not in self.loop_consts and self.longevity[arg][1] > -1:
                reg = self.try_allocate_reg(arg)
            if reg:
                locs[i] = reg
            else:
                loc = self.stack_loc(arg)
                locs[i] = loc
            # otherwise we have it saved on stack, so no worry
        self.free_regs.insert(0, tmpreg)
        assert tmpreg not in locs
        tree.arglocs = locs
        self.assembler.make_merge_point(tree, locs)
        self.eventually_free_vars(inputargs)

    def _consider_guard(self, op, ignored):
        loc = self.make_sure_var_in_reg(op.args[0], [])
        locs = self.locs_for_fail(op)
        self.perform_guard(op, locs, [loc], None)
        self.eventually_free_var(op.args[0])
        self.eventually_free_vars(op.suboperations[0].args)

    consider_guard_true = _consider_guard
    consider_guard_false = _consider_guard

    def consider_fail(self, op, ignored):
        # make sure all vars are on stack
        locs = [self.loc(arg) for arg in op.args]
        self.assembler.generate_failure(self.assembler.mc, op, locs, self.exc)
        self.eventually_free_vars(op.args)

    def consider_guard_no_exception(self, op, ignored):
        faillocs = self.locs_for_fail(op)
        self.perform_guard(op, faillocs, [], None)
        self.eventually_free_vars(op.suboperations[0].args)

    def consider_guard_exception(self, op, ignored):
        loc = self.make_sure_var_in_reg(op.args[0], [])
        box = TempBox()
        loc1 = self.force_allocate_reg(box, op.args)
        if op.result in self.longevity:
            # this means, is it ever used
            resloc = self.force_allocate_reg(op.result, op.args + [box])
        else:
            resloc = None
        faillocs = self.locs_for_fail(op)
        self.perform_guard(op, faillocs, [loc, loc1], resloc)
        self.eventually_free_vars(op.suboperations[0].args)
        self.eventually_free_vars(op.args)
        self.eventually_free_var(box)

    consider_guard_no_overflow = consider_guard_no_exception
    consider_guard_overflow    = consider_guard_no_exception

    def consider_guard_value(self, op, ignored):
        x = self.make_sure_var_in_reg(op.args[0], [])
        y = self.loc(op.args[1])
        faillocs = self.locs_for_fail(op)
        self.perform_guard(op, faillocs, [x, y], None)
        self.eventually_free_vars(op.suboperations[0].args)
        self.eventually_free_vars(op.args)

    def consider_guard_class(self, op, ignored):
        assert isinstance(op.args[0], Box)
        x = self.make_sure_var_in_reg(op.args[0], [])
        y = self.loc(op.args[1])
        faillocs = self.locs_for_fail(op)
        self.perform_guard(op, faillocs, [x, y], None)
        self.eventually_free_vars(op.suboperations[0].args)
        self.eventually_free_vars(op.args)
    
    def _consider_binop_part(self, op, ignored):
        x = op.args[0]
        argloc = self.loc(op.args[1])
        loc = self.force_result_in_reg(op.result, x, op.args)
        self.eventually_free_var(op.args[1])
        return loc, argloc

    def _consider_binop(self, op, ignored):
        loc, argloc = self._consider_binop_part(op, ignored)
        self.Perform(op, [loc, argloc], loc)

    consider_int_add = _consider_binop
    consider_int_mul = _consider_binop
    consider_int_sub = _consider_binop
    consider_int_and = _consider_binop
    consider_int_or  = _consider_binop
    consider_int_xor = _consider_binop

    consider_int_mul_ovf = _consider_binop
    consider_int_sub_ovf = _consider_binop
    consider_int_add_ovf = _consider_binop

    def consider_int_neg(self, op, ignored):
        res = self.force_result_in_reg(op.result, op.args[0], [])
        self.Perform(op, [res], res)

    consider_int_invert = consider_int_neg
    consider_bool_not = consider_int_neg

    def consider_int_lshift(self, op, ignored):
        if isinstance(op.args[1], Const):
            loc2 = convert_to_imm(op.args[1])
        else:
            loc2 = self.make_sure_var_in_reg(op.args[1], [], ecx)
        loc1 = self.force_result_in_reg(op.result, op.args[0], op.args)
        self.Perform(op, [loc1, loc2], loc1)
        self.eventually_free_vars(op.args)

    consider_int_rshift  = consider_int_lshift
    consider_uint_rshift = consider_int_lshift

    def _consider_int_div_or_mod(self, op, resultreg, trashreg):
        l0 = self.make_sure_var_in_reg(op.args[0], [], eax)
        l1 = self.make_sure_var_in_reg(op.args[1], [], ecx)
        l2 = self.force_allocate_reg(op.result, [], resultreg)
        # the register (eax or edx) not holding what we are looking for
        # will be just trash after that operation
        tmpvar = TempBox()
        self.force_allocate_reg(tmpvar, [], trashreg)
        assert (l0, l1, l2) == (eax, ecx, resultreg)
        self.eventually_free_vars(op.args + [tmpvar])

    def consider_int_mod(self, op, ignored):
        self._consider_int_div_or_mod(op, edx, eax)
        self.Perform(op, [eax, ecx], edx)

    def consider_int_floordiv(self, op, ignored):
        self._consider_int_div_or_mod(op, eax, edx)
        self.Perform(op, [eax, ecx], eax)

    def _consider_compop(self, op, guard_op):
        vx = op.args[0]
        vy = op.args[1]
        arglocs = [self.loc(vx), self.loc(vy)]
        if (vx in self.reg_bindings or vy in self.reg_bindings or
            isinstance(vx, Const) or isinstance(vy, Const)):
            pass
        else:
            arglocs[0] = self.make_sure_var_in_reg(vx, [])
        self.eventually_free_var(vx)
        self.eventually_free_var(vy)
        if guard_op is None:
            loc = self.force_allocate_reg(op.result, op.args,
                                          need_lower_byte=True)
            self.Perform(op, arglocs, loc)
        else:
            faillocs = self.locs_for_fail(guard_op)
            self.position += 1
            self.perform_with_guard(op, guard_op, faillocs, arglocs, None)
            self.eventually_free_var(op.result)
            self.eventually_free_vars(guard_op.suboperations[0].args)

    consider_int_lt = _consider_compop
    consider_int_gt = _consider_compop
    consider_int_ge = _consider_compop
    consider_int_le = _consider_compop
    consider_int_ne = _consider_compop
    consider_int_eq = _consider_compop
    consider_uint_gt = _consider_compop
    consider_uint_lt = _consider_compop
    consider_uint_le = _consider_compop
    consider_uint_ge = _consider_compop
    consider_oois = _consider_compop
    consider_ooisnot = _consider_compop

    def sync_var(self, v):
        if v not in self.stack_bindings:
            reg = self.reg_bindings[v]
            self.Store(v, reg, self.stack_loc(v))
        # otherwise it's clean

    def _call(self, op, arglocs, force_store=[]):
        # we need to store all variables which are now
        # in registers eax, ecx and edx
        for v, reg in self.reg_bindings.items():
            if v not in force_store and self.longevity[v][1] <= self.position:
                # variable dies
                del self.reg_bindings[v]
                self.free_regs.append(reg)
                continue
            if reg is ebx or reg is esi or reg is edi:
                # we don't need to
                continue
            self.sync_var(v)
            del self.reg_bindings[v]
            self.free_regs.append(reg)
        if op.result is not None:
            self.reg_bindings[op.result] = eax
            self.free_regs = [reg for reg in self.free_regs if reg is not eax]
        self.Perform(op, arglocs, eax)

    def consider_call(self, op, ignored):
        calldescr = op.descr
        assert isinstance(calldescr, BaseCallDescr)
        assert len(calldescr.arg_classes) == len(op.args) - 1
        size = calldescr.get_result_size(self.translate_support_code)
        self._call(op, [imm(size)] + [self.loc(arg) for arg in op.args])

    consider_call_pure = consider_call

    def consider_cond_call_gc_wb(self, op, ignored):
        assert op.result is None
        arglocs = [self.loc(arg) for arg in op.args]
        # add eax, ecx and edx as extra "arguments" to ensure they are
        # saved and restored.
        for v, reg in self.reg_bindings.items():
            if ((reg is eax or reg is ecx or reg is edx)
                and self.longevity[v][1] > self.position
                and reg not in arglocs[3:]):
                arglocs.append(reg)
        self.PerformDiscard(op, arglocs)
        self.eventually_free_vars(op.args)

    def consider_new(self, op, ignored):
        args = self.assembler.cpu.gc_ll_descr.args_for_new(op.descr)
        arglocs = [imm(x) for x in args]
        return self._call(op, arglocs)

    def consider_new_with_vtable(self, op, ignored):
        classint = op.args[0].getint()
        descrsize = self.assembler.cpu.class_sizes[classint]
        args = self.assembler.cpu.gc_ll_descr.args_for_new(descrsize)
        arglocs = [imm(x) for x in args]
        arglocs.append(self.loc(op.args[0]))
        return self._call(op, arglocs)

    def consider_newstr(self, op, ignored):
        gc_ll_descr = self.assembler.cpu.gc_ll_descr
        if gc_ll_descr.get_funcptr_for_newstr is not None:
            # framework GC
            loc = self.loc(op.args[0])
            return self._call(op, [loc])
        # boehm GC (XXX kill the following code at some point)
        ofs_items, itemsize, ofs = symbolic.get_array_token(rstr.STR, self.translate_support_code)
        assert itemsize == 1
        return self._malloc_varsize(ofs_items, ofs, 0, op.args[0],
                                    op.result)

    def consider_newunicode(self, op, ignored):
        gc_ll_descr = self.assembler.cpu.gc_ll_descr
        if gc_ll_descr.get_funcptr_for_newunicode is not None:
            # framework GC
            loc = self.loc(op.args[0])
            return self._call(op, [loc])
        # boehm GC (XXX kill the following code at some point)
        ofs_items, itemsize, ofs = symbolic.get_array_token(rstr.UNICODE, self.translate_support_code)
        if itemsize == 4:
            return self._malloc_varsize(ofs_items, ofs, 2, op.args[0],
                                        op.result)
        elif itemsize == 2:
            return self._malloc_varsize(ofs_items, ofs, 1, op.args[0],
                                        op.result)
        else:
            assert False, itemsize

    def _malloc_varsize(self, ofs_items, ofs_length, scale, v, res_v):
        # XXX kill this function at some point
        if isinstance(v, Box):
            loc = self.make_sure_var_in_reg(v, [v])
            other_loc = self.force_allocate_reg(TempBox(), [v])
            self.assembler.load_effective_addr(loc, ofs_items,scale, other_loc)
        else:
            other_loc = imm(ofs_items + (v.getint() << scale))
        self._call(ResOperation(rop.NEW, [v], res_v),
                   [other_loc], [v])
        loc = self.make_sure_var_in_reg(v, [res_v])
        assert self.loc(res_v) == eax
        # now we have to reload length to some reasonable place
        self.eventually_free_var(v)
        self.PerformDiscard(ResOperation(rop.SETFIELD_GC, [], None),
                            [eax, imm(ofs_length), imm(WORD), loc])

    def consider_new_array(self, op, ignored):
        gc_ll_descr = self.assembler.cpu.gc_ll_descr
        if gc_ll_descr.get_funcptr_for_newarray is not None:
            # framework GC
            args = self.assembler.cpu.gc_ll_descr.args_for_new_array(op.descr)
            arglocs = [imm(x) for x in args]
            arglocs.append(self.loc(op.args[0]))
            return self._call(op, arglocs)
        # boehm GC (XXX kill the following code at some point)
        scale_of_field, basesize, _ = self._unpack_arraydescr(op.descr)
        return self._malloc_varsize(basesize, 0, scale_of_field, op.args[0],
                                    op.result)

    def _unpack_arraydescr(self, arraydescr):
        assert isinstance(arraydescr, BaseArrayDescr)
        ofs = arraydescr.get_base_size(self.translate_support_code)
        size = arraydescr.get_item_size(self.translate_support_code)
        ptr = arraydescr.is_array_of_pointers()
        scale = 0
        while (1 << scale) < size:
            scale += 1
        assert (1 << scale) == size
        return scale, ofs, ptr

    def _unpack_fielddescr(self, fielddescr):
        assert isinstance(fielddescr, BaseFieldDescr)
        ofs = fielddescr.offset
        size = fielddescr.get_field_size(self.translate_support_code)
        ptr = fielddescr.is_pointer_field()
        return imm(ofs), imm(size), ptr

    def consider_setfield_gc(self, op, ignored):
        ofs_loc, size_loc, ptr = self._unpack_fielddescr(op.descr)
        assert isinstance(size_loc, IMM32)
        if size_loc.value == 1:
            need_lower_byte = True
        else:
            need_lower_byte = False
        base_loc = self.make_sure_var_in_reg(op.args[0], op.args)
        value_loc = self.make_sure_var_in_reg(op.args[1], op.args,
                                              need_lower_byte=need_lower_byte)
        self.eventually_free_vars(op.args)
        self.PerformDiscard(op, [base_loc, ofs_loc, size_loc, value_loc])

    consider_setfield_raw = consider_setfield_gc

    def consider_strsetitem(self, op, ignored):
        base_loc = self.make_sure_var_in_reg(op.args[0], op.args)
        ofs_loc = self.make_sure_var_in_reg(op.args[1], op.args)
        value_loc = self.make_sure_var_in_reg(op.args[2], op.args,
                                              need_lower_byte=True)
        self.eventually_free_vars([op.args[0], op.args[1], op.args[2]])
        self.PerformDiscard(op, [base_loc, ofs_loc, value_loc])

    consider_unicodesetitem = consider_strsetitem

    def consider_setarrayitem_gc(self, op, ignored):
        scale, ofs, ptr = self._unpack_arraydescr(op.descr)
        base_loc  = self.make_sure_var_in_reg(op.args[0], op.args)
        if scale == 0:
            need_lower_byte = True
        else:
            need_lower_byte = False
        value_loc = self.make_sure_var_in_reg(op.args[2], op.args,
                                              need_lower_byte=need_lower_byte)
        ofs_loc = self.make_sure_var_in_reg(op.args[1], op.args)
        self.eventually_free_vars(op.args)
        self.PerformDiscard(op, [base_loc, ofs_loc, value_loc,
                                 imm(scale), imm(ofs)])

    consider_setarrayitem_raw = consider_setarrayitem_gc

    def consider_getfield_gc(self, op, ignored):
        ofs_loc, size_loc, _ = self._unpack_fielddescr(op.descr)
        base_loc = self.make_sure_var_in_reg(op.args[0], op.args)
        self.eventually_free_vars(op.args)
        result_loc = self.force_allocate_reg(op.result, [])
        self.Perform(op, [base_loc, ofs_loc, size_loc], result_loc)

    consider_getfield_gc_pure = consider_getfield_gc

    def consider_getarrayitem_gc(self, op, ignored):
        scale, ofs, _ = self._unpack_arraydescr(op.descr)
        base_loc = self.make_sure_var_in_reg(op.args[0], op.args)
        ofs_loc = self.make_sure_var_in_reg(op.args[1], op.args)
        self.eventually_free_vars(op.args)
        result_loc = self.force_allocate_reg(op.result, [])
        self.Perform(op, [base_loc, ofs_loc, imm(scale), imm(ofs)], result_loc)

    consider_getfield_raw = consider_getfield_gc
    consider_getarrayitem_gc_pure = consider_getarrayitem_gc


    def _consider_nullity(self, op, guard_op):
        # doesn't need a register in arg
        if guard_op is not None:
            argloc = self.make_sure_var_in_reg(op.args[0], [])
            self.eventually_free_var(op.args[0])
            faillocs = self.locs_for_fail(guard_op)
            self.position += 1
            self.perform_with_guard(op, guard_op, faillocs, [argloc], None)
            self.eventually_free_var(op.result)
            self.eventually_free_vars(guard_op.suboperations[0].args)
        else:
            argloc = self.loc(op.args[0])
            self.eventually_free_var(op.args[0])
            resloc = self.force_allocate_reg(op.result, [],
                                             need_lower_byte=True)
            self.Perform(op, [argloc], resloc)

    consider_int_is_true = _consider_nullity
    consider_ooisnull = _consider_nullity
    consider_oononnull = _consider_nullity

    def consider_same_as(self, op, ignored):
        argloc = self.loc(op.args[0])
        self.eventually_free_var(op.args[0])
        resloc = self.force_allocate_reg(op.result, [])
        self.Perform(op, [argloc], resloc)
    consider_cast_ptr_to_int = consider_same_as

    def consider_strlen(self, op, ignored):
        base_loc = self.make_sure_var_in_reg(op.args[0], op.args)
        self.eventually_free_vars(op.args)
        result_loc = self.force_allocate_reg(op.result, [])
        self.Perform(op, [base_loc], result_loc)

    consider_unicodelen = consider_strlen

    def consider_arraylen_gc(self, op, ignored):
        arraydescr = op.descr
        assert isinstance(arraydescr, BaseArrayDescr)
        ofs = arraydescr.get_ofs_length(self.translate_support_code)
        base_loc = self.make_sure_var_in_reg(op.args[0], op.args)
        self.eventually_free_vars(op.args)
        result_loc = self.force_allocate_reg(op.result, [])
        self.Perform(op, [base_loc, imm(ofs)], result_loc)

    def consider_strgetitem(self, op, ignored):
        base_loc = self.make_sure_var_in_reg(op.args[0], op.args)
        ofs_loc = self.make_sure_var_in_reg(op.args[1], op.args)
        self.eventually_free_vars([op.args[0], op.args[1]])
        result_loc = self.force_allocate_reg(op.result, [])
        self.Perform(op, [base_loc, ofs_loc], result_loc)

    consider_unicodegetitem = consider_strgetitem

    def consider_jump(self, op, ignored):
        loop = op.jump_target
        # compute 'tmploc' to be REGS[0] by spilling what is there
        box = TempBox()
        tmploc = self.force_allocate_reg(box, [], selected_reg=REGS[0])
        src_locations = [self.loc(arg) for arg in op.args]
        dst_locations = loop.arglocs
        assert tmploc not in dst_locations
        remap_stack_layout(self.assembler, src_locations,
                                           dst_locations, tmploc)
        self.eventually_free_var(box)
        self.eventually_free_vars(op.args)
        self.max_stack_depth = op.jump_target._x86_stack_depth    
        self.PerformDiscard(op, [])

    def consider_debug_merge_point(self, op, ignored):
        pass

    def get_mark_gc_roots(self, gcrootmap):
        shape = gcrootmap.get_basic_shape()
        for v, val in self.stack_bindings.items():
            if (isinstance(v, BoxPtr) and
                self.longevity[v][1] > self.position):
                assert isinstance(val, MODRM)
                gcrootmap.add_ebp_offset(shape, get_ebp_ofs(val.position))
        for v, reg in self.reg_bindings.items():
            if (isinstance(v, BoxPtr) and
                self.longevity[v][1] > self.position):
                if reg is ebx:
                    gcrootmap.add_ebx(shape)
                elif reg is esi:
                    gcrootmap.add_esi(shape)
                elif reg is edi:
                    gcrootmap.add_edi(shape)
                else:
                    assert reg is eax     # ok to ignore this one
        return gcrootmap.compress_callshape(shape)

    def not_implemented_op(self, op, ignored):
        print "[regalloc] Not implemented operation: %s" % op.getopname()
        raise NotImplementedError

oplist = [RegAlloc.not_implemented_op] * rop._LAST

for name, value in RegAlloc.__dict__.iteritems():
    if name.startswith('consider_'):
        name = name[len('consider_'):]
        num = getattr(rop, name.upper())
        oplist[num] = value

def get_ebp_ofs(position):
    # Argument is a stack position (0, 1, 2...).
    # Returns (ebp-16), (ebp-20), (ebp-24)...
    # This depends on the fact that our function prologue contains
    # exactly 4 PUSHes.
    return -WORD * (4 + position)

def stack_pos(i):
    res = mem(ebp, get_ebp_ofs(i))
    res.position = i
    return res

def lower_byte(reg):
    # argh, kill, use lowest8bits instead
    if isinstance(reg, MODRM):
        return reg
    if isinstance(reg, IMM32):
        return imm8(reg.value)
    if reg is eax:
        return al
    elif reg is ebx:
        return bl
    elif reg is ecx:
        return cl
    elif reg is edx:
        return dl
    else:
        raise NotImplementedError()
