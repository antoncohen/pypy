import py
from pypy.rpython.lltypesystem import llmemory
from pypy.rpython.ootypesystem import ootype
from pypy.rlib.objectmodel import we_are_translated, r_dict
from pypy.rlib.unroll import unrolling_iterable
from pypy.rlib.debug import debug_print

from pypy.jit.metainterp import history, compile, resume
from pypy.jit.metainterp.history import Const, ConstInt, Box
from pypy.jit.metainterp.resoperation import rop
from pypy.jit.metainterp import codewriter, executor
from pypy.rlib.rarithmetic import intmask
from pypy.rlib.objectmodel import specialize

# ____________________________________________________________

def check_args(*args):
    for arg in args:
        assert isinstance(arg, (Box, Const))

# debug level: 0 off, 1 normal, 2 detailed
DEBUG = 1

# translate.py overrides DEBUG with the --jit-debug=xxx option
_DEBUG_LEVEL = {"off":      0,
                "profile":  0,
                "steps":    1,
                "detailed": 2}

def log(msg):
    if not we_are_translated():
        history.log.info(msg)
    elif DEBUG:
        debug_print(msg)

class arguments(object):
    def __init__(self, *argtypes):
        self.argtypes = argtypes

    def __eq__(self, other):
        if not isinstance(other, arguments):
            return NotImplemented
        return self.argtypes == other.argtypes

    def __ne__(self, other):
        if not isinstance(other, arguments):
            return NotImplemented
        return self.argtypes != other.argtypes

    def __call__(self, func, DEBUG=DEBUG):
        argtypes = unrolling_iterable(self.argtypes)
        def wrapped(self, orgpc):
            args = (self, )
            if DEBUG >= 2:
                s = '%s:%d\t%s' % (self.jitcode.name, orgpc, name)
            else:
                s = ''
            for argspec in argtypes:
                if argspec == "box":
                    box = self.load_arg()
                    args += (box, )
                    if DEBUG >= 2:
                        s += '\t' + box.repr_rpython()
                elif argspec == "constbox":
                    args += (self.load_const_arg(), )
                elif argspec == "int":
                    args += (self.load_int(), )
                elif argspec == "jumptarget":
                    args += (self.load_3byte(), )
                elif argspec == "jumptargets":
                    num = self.load_int()
                    args += ([self.load_3byte() for i in range(num)], )
                elif argspec == "varargs":
                    args += (self.load_varargs(), )
                elif argspec == "constargs":
                    args += (self.load_constargs(), )
                elif argspec == "descr":
                    descr = self.load_const_arg()
                    assert isinstance(descr, history.AbstractDescr)
                    args += (descr, )
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
                elif argspec == "methdescr":
                    methdescr = self.load_const_arg()
                    assert isinstance(methdescr,
                                      history.AbstractMethDescr)
                    args += (methdescr, )
                else:
                    assert 0, "unknown argtype declaration: %r" % (argspec,)
            if DEBUG >= 2:
                debug_print(s)
            val = func(*args)
            if DEBUG >= 2:
                reprboxes = ' '.join([box.repr_rpython() for box in self.env])
                debug_print('  \x1b[34menv=[%s]\x1b[0m' % (reprboxes,))
            if val is None:
                val = False
            return val
        name = func.func_name
        wrapped.func_name = "wrap_" + name
        wrapped.argspec = self
        return wrapped

# ____________________________________________________________


class MIFrame(object):
    exception_box = None
    exc_value_box = None

    def __init__(self, metainterp, jitcode):
        assert isinstance(jitcode, codewriter.JitCode)
        self.metainterp = metainterp
        self.jitcode = jitcode
        self.bytecode = jitcode.code
        self.constants = jitcode.constants
        self.exception_target = -1
        self.name = jitcode.name # purely for having name attribute

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
        assert i >= 0
        j = i >> 1
        if i & 1:
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

    def ignore_varargs(self):
        count = self.load_int()
        for i in range(count):
            self.load_int()

    def getvarenv(self, i):
        return self.env[i]

    def make_result_box(self, box):
        assert isinstance(box, Box) or isinstance(box, Const)
        self.env.append(box)

    # ------------------------------

    for _n in range(codewriter.MAX_MAKE_NEW_VARS):
        _decl = ', '.join(["'box'" for _i in range(_n)])
        _allargs = ', '.join(["box%d" % _i for _i in range(_n)])
        exec py.code.Source("""
            @arguments(%s)
            def opimpl_make_new_vars_%d(self, %s):
                if not we_are_translated():
                    check_args(%s)
                self.env = [%s]
        """ % (_decl, _n, _allargs, _allargs, _allargs)).compile()

    @arguments("varargs")
    def opimpl_make_new_vars(self, newenv):
        if not we_are_translated():
            check_args(*newenv)
        self.env = newenv

    for _opimpl in ['int_add', 'int_sub', 'int_mul', 'int_floordiv', 'int_mod',
                    'int_lt', 'int_le', 'int_eq',
                    'int_ne', 'int_gt', 'int_ge',
                    'int_and', 'int_or', 'int_xor',
                    'int_rshift', 'int_lshift', 'uint_rshift',
                    'uint_lt', 'uint_le', 'uint_gt', 'uint_ge',
                    ]:
        exec py.code.Source('''
            @arguments("box", "box")
            def opimpl_%s(self, b1, b2):
                self.execute(rop.%s, [b1, b2])
        ''' % (_opimpl, _opimpl.upper())).compile()

    for _opimpl in ['int_add_ovf', 'int_sub_ovf', 'int_mul_ovf']:
        exec py.code.Source('''
            @arguments("box", "box")
            def opimpl_%s(self, b1, b2):
                self.execute(rop.%s, [b1, b2])
                return self.metainterp.handle_overflow_error()
        ''' % (_opimpl, _opimpl.upper())).compile()

    for _opimpl in ['int_is_true', 'int_neg', 'int_invert', 'bool_not',
                    'cast_ptr_to_int', 'cast_int_to_ptr',
                    ]:
        exec py.code.Source('''
            @arguments("box")
            def opimpl_%s(self, b):
                self.execute(rop.%s, [b])
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

    @arguments("orgpc", "jumptarget", "box", "varargs")
    def opimpl_goto_if_not(self, pc, target, box, livelist):
        switchcase = box.getint()
        if switchcase:
            opnum = rop.GUARD_TRUE
        else:
            self.pc = target
            opnum = rop.GUARD_FALSE
        self.env = livelist
        self.generate_guard(pc, opnum, box)
        # note about handling self.env explicitly here: it is done in
        # such a way that the 'box' on which we generate the guard is
        # typically not included in the livelist.

    def follow_jump(self):
        _op_goto_if_not = self.metainterp.staticdata._op_goto_if_not
        assert ord(self.bytecode[self.pc]) == _op_goto_if_not
        self.pc += 1          # past the bytecode for 'goto_if_not'
        target = self.load_3byte()  # load the 'target' argument
        self.pc = target      # jump

    def dont_follow_jump(self):
        _op_goto_if_not = self.metainterp.staticdata._op_goto_if_not
        assert ord(self.bytecode[self.pc]) == _op_goto_if_not
        self.pc += 1          # past the bytecode for 'goto_if_not'
        self.load_3byte()     # past the 'target' argument
        self.load_int()       # past the 'box' argument
        self.ignore_varargs() # past the 'livelist' argument

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

    @arguments("descr")
    def opimpl_new(self, size):
        self.execute(rop.NEW, [], descr=size)

    @arguments("constbox")
    def opimpl_new_with_vtable(self, vtablebox):
        self.execute(rop.NEW_WITH_VTABLE, [vtablebox])

    @arguments("box")
    def opimpl_runtimenew(self, classbox):
        self.execute(rop.RUNTIMENEW, [classbox])

    @arguments("box", "descr")
    def opimpl_instanceof(self, box, typedescr):
        self.execute(rop.INSTANCEOF, [box], descr=typedescr)

    @arguments("box", "box")
    def opimpl_subclassof(self, box1, box2):
        self.execute(rop.SUBCLASSOF, [box1, box2], descr=None)

    @arguments("box")
    def opimpl_ooidentityhash(self, box):
        self.execute(rop.OOIDENTITYHASH, [box], descr=None)

    @arguments("descr", "box")
    def opimpl_new_array(self, itemsize, countbox):
        self.execute(rop.NEW_ARRAY, [countbox], descr=itemsize)

    @arguments("box", "descr", "box")
    def opimpl_getarrayitem_gc(self, arraybox, arraydesc, indexbox):
        self.execute(rop.GETARRAYITEM_GC, [arraybox, indexbox],
                     descr=arraydesc)

    @arguments("box", "descr", "box")
    def opimpl_getarrayitem_gc_pure(self, arraybox, arraydesc, indexbox):
        self.execute(rop.GETARRAYITEM_GC_PURE, [arraybox, indexbox],
                     descr=arraydesc)

    @arguments("box", "descr", "box", "box")
    def opimpl_setarrayitem_gc(self, arraybox, arraydesc, indexbox, itembox):
        self.execute(rop.SETARRAYITEM_GC, [arraybox, indexbox, itembox],
                     descr=arraydesc)

    @arguments("box", "descr")
    def opimpl_arraylen_gc(self, arraybox, arraydesc):
        self.execute(rop.ARRAYLEN_GC, [arraybox], descr=arraydesc)

    @arguments("orgpc", "box", "descr", "box")
    def opimpl_check_neg_index(self, pc, arraybox, arraydesc, indexbox):
        negbox = self.metainterp.execute_and_record(
            rop.INT_LT, [indexbox, ConstInt(0)])
        # xxx inefficient
        negbox = self.implement_guard_value(pc, negbox)
        if negbox.getint():
            # the index is < 0; add the array length to it
            lenbox = self.metainterp.execute_and_record(
                rop.ARRAYLEN_GC, [arraybox], descr=arraydesc)
            indexbox = self.metainterp.execute_and_record(
                rop.INT_ADD, [indexbox, lenbox])
        self.make_result_box(indexbox)

    @arguments("descr", "descr", "descr", "descr", "box")
    def opimpl_newlist(self, structdescr, lengthdescr, itemsdescr, arraydescr,
                       sizebox):
        sbox = self.metainterp.execute_and_record(rop.NEW, [],
                                                  descr=structdescr)
        self.metainterp.execute_and_record(rop.SETFIELD_GC, [sbox, sizebox],
                                           descr=lengthdescr)
        abox = self.metainterp.execute_and_record(rop.NEW_ARRAY, [sizebox],
                                                  descr=arraydescr)
        self.metainterp.execute_and_record(rop.SETFIELD_GC, [sbox, abox],
                                           descr=itemsdescr)
        self.make_result_box(sbox)

    @arguments("box", "descr", "descr", "box")
    def opimpl_getlistitem_gc(self, listbox, itemsdescr, arraydescr, indexbox):
        arraybox = self.metainterp.execute_and_record(rop.GETFIELD_GC,
                                          [listbox], descr=itemsdescr)
        self.execute(rop.GETARRAYITEM_GC, [arraybox, indexbox],
                     descr=arraydescr)

    @arguments("box", "descr", "descr", "box", "box")
    def opimpl_setlistitem_gc(self, listbox, itemsdescr, arraydescr, indexbox,
                              valuebox):
        arraybox = self.metainterp.execute_and_record(rop.GETFIELD_GC,
                                          [listbox], descr=itemsdescr) 
        self.execute(rop.SETARRAYITEM_GC, [arraybox, indexbox, valuebox],
                     descr=arraydescr)

    @arguments("orgpc", "box", "descr", "box")
    def opimpl_check_resizable_neg_index(self, pc, listbox, lengthdesc,
                                         indexbox):
        negbox = self.metainterp.execute_and_record(
            rop.INT_LT, [indexbox, ConstInt(0)])
        # xxx inefficient
        negbox = self.implement_guard_value(pc, negbox)
        if negbox.getint():
            # the index is < 0; add the array length to it
            lenbox = self.metainterp.execute_and_record(
                rop.GETFIELD_GC, [listbox], descr=lengthdesc)
            indexbox = self.metainterp.execute_and_record(
                rop.INT_ADD, [indexbox, lenbox])
        self.make_result_box(indexbox)

    @arguments("orgpc", "box")
    def opimpl_check_zerodivisionerror(self, pc, box):
        nonzerobox = self.metainterp.execute_and_record(
            rop.INT_NE, [box, ConstInt(0)])
        # xxx inefficient
        nonzerobox = self.implement_guard_value(pc, nonzerobox)
        if nonzerobox.getint():
            return False
        else:
            # division by zero!
            return self.metainterp.raise_zero_division_error()

    @arguments("orgpc", "box", "box")
    def opimpl_check_div_overflow(self, pc, box1, box2):
        # detect the combination "box1 = -sys.maxint-1, box2 = -1".
        import sys
        tmp1 = self.metainterp.execute_and_record(    # combination to detect:
            rop.INT_ADD, [box1, ConstInt(sys.maxint)])    # tmp1=-1, box2=-1
        tmp2 = self.metainterp.execute_and_record(
            rop.INT_AND, [tmp1, box2])                    # tmp2=-1
        tmp3 = self.metainterp.execute_and_record(
            rop.INT_EQ, [tmp2, ConstInt(-1)])             # tmp3?
        # xxx inefficient
        tmp4 = self.implement_guard_value(pc, tmp3)       # tmp4?
        if not tmp4.getint():
            return False
        else:
            # division overflow!
            return self.metainterp.raise_overflow_error()

    @arguments()
    def opimpl_overflow_error(self):
        return self.metainterp.raise_overflow_error()

    @arguments("orgpc", "box")
    def opimpl_int_abs(self, pc, box):
        nonneg = self.metainterp.execute_and_record(
            rop.INT_GE, [box, ConstInt(0)])
        # xxx inefficient
        nonneg = self.implement_guard_value(pc, nonneg)
        if nonneg.getint():
            self.make_result_box(box)
        else:
            self.execute(rop.INT_NEG, [box])

    @arguments("box")
    def opimpl_ptr_nonzero(self, box):
        self.execute(rop.OONONNULL, [box])

    @arguments("box")
    def opimpl_ptr_iszero(self, box):
        self.execute(rop.OOISNULL, [box])

    @arguments("box")
    def opimpl_oononnull(self, box):
        self.execute(rop.OONONNULL, [box])

    @arguments("box", "box")
    def opimpl_ptr_eq(self, box1, box2):
        self.execute(rop.OOIS, [box1, box2])

    @arguments("box", "box")
    def opimpl_ptr_ne(self, box1, box2):
        self.execute(rop.OOISNOT, [box1, box2])

    opimpl_oois = opimpl_ptr_eq

    @arguments("box", "descr")
    def opimpl_getfield_gc(self, box, fielddesc):
        self.execute(rop.GETFIELD_GC, [box], descr=fielddesc)
    @arguments("box", "descr")
    def opimpl_getfield_gc_pure(self, box, fielddesc):
        self.execute(rop.GETFIELD_GC_PURE, [box], descr=fielddesc)
    @arguments("box", "descr", "box")
    def opimpl_setfield_gc(self, box, fielddesc, valuebox):
        self.execute(rop.SETFIELD_GC, [box, valuebox], descr=fielddesc)

    @arguments("box", "descr")
    def opimpl_getfield_raw(self, box, fielddesc):
        self.execute(rop.GETFIELD_RAW, [box], descr=fielddesc)
    @arguments("box", "descr")
    def opimpl_getfield_raw_pure(self, box, fielddesc):
        self.execute(rop.GETFIELD_RAW_PURE, [box], descr=fielddesc)
    @arguments("box", "descr", "box")
    def opimpl_setfield_raw(self, box, fielddesc, valuebox):
        self.execute(rop.SETFIELD_RAW, [box, valuebox], descr=fielddesc)

    def _nonstandard_virtualizable(self, pc, box):
        # returns True if 'box' is actually not the "standard" virtualizable
        # that is stored in metainterp.virtualizable_boxes[-1]
        standard_box = self.metainterp.virtualizable_boxes[-1]
        if standard_box is box:
            return False
        eqbox = self.metainterp.execute_and_record(rop.OOIS,
                                                   [box, standard_box])
        eqbox = self.implement_guard_value(pc, eqbox)
        return not eqbox.getint()

    def _get_virtualizable_field_descr(self, index):
        vinfo = self.metainterp.staticdata.virtualizable_info
        return vinfo.static_field_descrs[index]

    def _get_virtualizable_array_field_descr(self, index):
        vinfo = self.metainterp.staticdata.virtualizable_info
        return vinfo.array_field_descrs[index]

    def _get_virtualizable_array_descr(self, index):
        vinfo = self.metainterp.staticdata.virtualizable_info
        return vinfo.array_descrs[index]

    @arguments("orgpc", "box", "int")
    def opimpl_getfield_vable(self, pc, basebox, index):
        if self._nonstandard_virtualizable(pc, basebox):
            self.execute(rop.GETFIELD_GC, [basebox],
                         descr=self._get_virtualizable_field_descr(index))
            return
        self.metainterp.check_synchronized_virtualizable()
        resbox = self.metainterp.virtualizable_boxes[index]
        self.make_result_box(resbox)
    @arguments("orgpc", "box", "int", "box")
    def opimpl_setfield_vable(self, pc, basebox, index, valuebox):
        if self._nonstandard_virtualizable(pc, basebox):
            self.execute(rop.SETFIELD_GC, [basebox, valuebox],
                         descr=self._get_virtualizable_field_descr(index))
            return
        self.metainterp.virtualizable_boxes[index] = valuebox
        self.metainterp.synchronize_virtualizable()
        # XXX only the index'th field needs to be synchronized, really

    def _get_arrayitem_vable_index(self, pc, arrayindex, indexbox):
        indexbox = self.implement_guard_value(pc, indexbox)
        vinfo = self.metainterp.staticdata.virtualizable_info
        virtualizable_box = self.metainterp.virtualizable_boxes[-1]
        virtualizable = vinfo.unwrap_virtualizable_box(virtualizable_box)
        index = indexbox.getint()
        if index < 0:
            index += vinfo.get_array_length(virtualizable, arrayindex)
        assert 0 <= index < vinfo.get_array_length(virtualizable, arrayindex)
        return vinfo.get_index_in_array(virtualizable, arrayindex, index)

    @arguments("orgpc", "box", "int", "box")
    def opimpl_getarrayitem_vable(self, pc, basebox, arrayindex, indexbox):
        if self._nonstandard_virtualizable(pc, basebox):
            arraybox = self.metainterp.execute_and_record(
                rop.GETFIELD_GC, [basebox],
                descr=self._get_virtualizable_array_field_descr(arrayindex))
            self.execute(
                rop.GETARRAYITEM_GC, [arraybox, indexbox],
                descr=self._get_virtualizable_array_descr(arrayindex))
            return
        self.metainterp.check_synchronized_virtualizable()
        index = self._get_arrayitem_vable_index(pc, arrayindex, indexbox)
        resbox = self.metainterp.virtualizable_boxes[index]
        self.make_result_box(resbox)
    @arguments("orgpc", "box", "int", "box", "box")
    def opimpl_setarrayitem_vable(self, pc, basebox, arrayindex, indexbox,
                                  valuebox):
        if self._nonstandard_virtualizable(pc, basebox):
            arraybox = self.metainterp.execute_and_record(
                rop.GETFIELD_GC, [basebox],
                descr=self._get_virtualizable_array_field_descr(arrayindex))
            self.execute(
                rop.SETARRAYITEM_GC, [arraybox, indexbox, valuebox],
                descr=self._get_virtualizable_array_descr(arrayindex))
            return
        index = self._get_arrayitem_vable_index(pc, arrayindex, indexbox)
        self.metainterp.virtualizable_boxes[index] = valuebox
        self.metainterp.synchronize_virtualizable()
        # XXX only the index'th field needs to be synchronized, really
    @arguments("orgpc", "box", "int")
    def opimpl_arraylen_vable(self, pc, basebox, arrayindex):
        if self._nonstandard_virtualizable(pc, basebox):
            arraybox = self.metainterp.execute_and_record(
                rop.GETFIELD_GC, [basebox],
                descr=self._get_virtualizable_array_field_descr(arrayindex))
            self.execute(
                rop.ARRAYLEN_GC, [arraybox],
                descr=self._get_virtualizable_array_descr(arrayindex))
            return
        vinfo = self.metainterp.staticdata.virtualizable_info
        virtualizable_box = self.metainterp.virtualizable_boxes[-1]
        virtualizable = vinfo.unwrap_virtualizable_box(virtualizable_box)
        result = vinfo.get_array_length(virtualizable, arrayindex)
        self.make_result_box(ConstInt(result))

    def perform_call(self, jitcode, varargs):
        if (self.metainterp.is_blackholing() and
            jitcode.calldescr is not None):
            # when producing only a BlackHole, we can implement this by
            # calling the subfunction directly instead of interpreting it
            staticdata = self.metainterp.staticdata
            globaldata = staticdata.globaldata
            vi = staticdata.virtualizable_info
            if vi:
                globaldata.blackhole_virtualizable = vi.unwrap_virtualizable_box(self.metainterp.virtualizable_boxes[-1])
            if jitcode.cfnptr is not None:
                # for non-oosends
                varargs = [jitcode.cfnptr] + varargs
                res = self.execute_with_exc(rop.CALL, varargs,
                                             descr=jitcode.calldescr)
            else:
                # for oosends (ootype only): calldescr is a MethDescr
                res = self.execute_with_exc(rop.OOSEND, varargs,
                                             descr=jitcode.calldescr)
            if vi:
                globaldata.blackhole_virtualizable = vi.null_vable
            return res
        else:
            # when tracing, this bytecode causes the subfunction to be entered
            f = self.metainterp.newframe(jitcode)
            f.setup_call(varargs)
            return True

    @arguments("bytecode", "varargs")
    def opimpl_call(self, callee, varargs):
        return self.perform_call(callee, varargs)

    @arguments("descr", "varargs")
    def opimpl_residual_call(self, calldescr, varargs):
        return self.execute_with_exc(rop.CALL, varargs, descr=calldescr)

    @arguments("descr", "varargs")
    def opimpl_recursive_call(self, calldescr, varargs):
        if self.metainterp.staticdata.options.inline:
            num_green_args = self.metainterp.staticdata.num_green_args
            portal_code = self.metainterp.staticdata.portal_code
            greenkey = varargs[1:num_green_args + 1]
            if self.metainterp.staticdata.state.can_inline_callable(greenkey):
                self.metainterp.in_recursion += 1
                return self.perform_call(portal_code, varargs[1:])
        return self.execute_with_exc(rop.CALL, varargs, descr=calldescr)

    @arguments("descr", "varargs")
    def opimpl_residual_call_noexception(self, calldescr, varargs):
        if not we_are_translated():
            self.metainterp._debug_history.append(['call',
                                                  varargs[0], varargs[1:]])
        self.execute(rop.CALL, varargs, descr=calldescr)

    @arguments("descr", "varargs")
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
        if cpu.is_oo:
            key = box.getobj()
        else:
            key = box.getaddr(cpu)
        jitcode = indirectcallset.bytecode_for_address(key)
        f = self.metainterp.newframe(jitcode)
        f.setup_call(varargs)
        return True

    @arguments("orgpc", "methdescr", "varargs")
    def opimpl_oosend(self, pc, methdescr, varargs):
        objbox = varargs[0]
        clsbox = self.cls_of_box(objbox)
        if isinstance(objbox, Box):
            self.generate_guard(pc, rop.GUARD_CLASS, objbox, [clsbox])
        oocls = ootype.cast_from_object(ootype.Class, clsbox.getobj())
        jitcode = methdescr.get_jitcode_for_class(oocls)
        return self.perform_call(jitcode, varargs)

    @arguments("box")
    def opimpl_strlen(self, str):
        self.execute(rop.STRLEN, [str])

    @arguments("box")
    def opimpl_unicodelen(self, str):
        self.execute(rop.UNICODELEN, [str])

    @arguments("box", "box")
    def opimpl_strgetitem(self, str, index):
        self.execute(rop.STRGETITEM, [str, index])

    @arguments("box", "box")
    def opimpl_unicodegetitem(self, str, index):
        self.execute(rop.UNICODEGETITEM, [str, index])

    @arguments("box", "box", "box")
    def opimpl_strsetitem(self, str, index, newchar):
        self.execute(rop.STRSETITEM, [str, index, newchar])

    @arguments("box", "box", "box")
    def opimpl_unicodesetitem(self, str, index, newchar):
        self.execute(rop.UNICODESETITEM, [str, index, newchar])

    @arguments("box")
    def opimpl_newstr(self, length):
        self.execute(rop.NEWSTR, [length])

    @arguments("box")
    def opimpl_newunicode(self, length):
        self.execute(rop.NEWUNICODE, [length])

    @arguments("descr", "varargs")
    def opimpl_residual_oosend_canraise(self, methdescr, varargs):
        return self.execute_with_exc(rop.OOSEND, varargs, descr=methdescr)

    @arguments("descr", "varargs")
    def opimpl_residual_oosend_noraise(self, methdescr, varargs):
        self.execute(rop.OOSEND, varargs, descr=methdescr)

    @arguments("descr", "varargs")
    def opimpl_residual_oosend_pure(self, methdescr, boxes):
        self.execute(rop.OOSEND_PURE, boxes, descr=methdescr)

#    @arguments("box", "box")
#    def opimpl_oostring_char(self, obj, base):
#        self.execute(rop.OOSTRING_CHAR, [obj, base])
#
#    @arguments("box", "box")
#    def opimpl_oounicode_unichar(self, obj, base):
#        self.execute(rop.OOUNICODE_UNICHAR, [obj, base])

    @arguments("orgpc", "box")
    def opimpl_guard_value(self, pc, box):
        constbox = self.implement_guard_value(pc, box)
        self.make_result_box(constbox)

    @arguments("orgpc", "box")
    def opimpl_guard_class(self, pc, box):
        clsbox = self.cls_of_box(box)
        if isinstance(box, Box):
            self.generate_guard(pc, rop.GUARD_CLASS, box, [clsbox])
        self.make_result_box(clsbox)

##    @arguments("orgpc", "box", "builtin")
##    def opimpl_guard_builtin(self, pc, box, builtin):
##        self.generate_guard(pc, "guard_builtin", box, [builtin])

##    @arguments("orgpc", "box", "builtin")
##    def opimpl_guard_len(self, pc, box, builtin):
##        intbox = self.metainterp.cpu.execute_operation(
##            'len', [builtin.len_func, box], 'int')
##        self.generate_guard(pc, "guard_len", box, [intbox])

    @arguments("box")
    def opimpl_keepalive(self, box):
        pass     # xxx?

    def generate_merge_point(self, pc, varargs):
        if self.metainterp.is_blackholing():
            raise self.metainterp.staticdata.ContinueRunningNormally(varargs)
        num_green_args = self.metainterp.staticdata.num_green_args
        for i in range(num_green_args):
            varargs[i] = self.implement_guard_value(pc, varargs[i])

    @arguments("orgpc")
    def opimpl_can_enter_jit(self, pc):
        # Note: when running with a BlackHole history, this 'can_enter_jit'
        # may be completely skipped by the logic that replaces perform_call
        # with rop.CALL.  But in that case, no-one will check the flag anyway,
        # so it's fine.
        if self.metainterp.in_recursion:
            from pypy.jit.metainterp.warmspot import CannotInlineCanEnterJit
            raise CannotInlineCanEnterJit()
        self.metainterp.seen_can_enter_jit = True

    @arguments("orgpc")
    def opimpl_jit_merge_point(self, pc):
        self.generate_merge_point(pc, self.env)
        if DEBUG > 0:
            self.debug_merge_point()
        if self.metainterp.seen_can_enter_jit:
            self.metainterp.seen_can_enter_jit = False
            self.metainterp.reached_can_enter_jit(self.env)

    def debug_merge_point(self):
        # debugging: produce a DEBUG_MERGE_POINT operation
        num_green_args = self.metainterp.staticdata.num_green_args
        greenkey = self.env[:num_green_args]
        sd = self.metainterp.staticdata
        loc = sd.state.get_location_str(greenkey)
        constloc = self.metainterp.cpu.ts.conststr(loc)
        self.metainterp.history.record(rop.DEBUG_MERGE_POINT,
                                       [constloc], None)

    @arguments("jumptarget")
    def opimpl_setup_exception_block(self, exception_target):
        self.exception_target = exception_target

    @arguments()
    def opimpl_teardown_exception_block(self):
        self.exception_target = -1

    @arguments("constbox", "jumptarget")
    def opimpl_goto_if_exception_mismatch(self, vtableref, next_exc_target):
        assert isinstance(self.exception_box, Const)    # XXX
        cpu = self.metainterp.cpu
        ts = self.metainterp.cpu.ts
        if not ts.subclassOf(cpu, self.exception_box, vtableref):
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

    @arguments()
    def opimpl_not_implemented(self):
        raise NotImplementedError

    # ------------------------------

    def setup_call(self, argboxes):
        if not we_are_translated():
            check_args(*argboxes)
        self.pc = 0
        self.env = argboxes
        if not we_are_translated():
            self.metainterp._debug_history[-1][-1] = argboxes

    def setup_resume_at_op(self, pc, exception_target, env):
        if not we_are_translated():
            check_args(*env)
        self.pc = pc
        self.exception_target = exception_target
        self.env = env
        if DEBUG >= 2:
            values = ' '.join([box.repr_rpython() for box in self.env])
            log('setup_resume_at_op  %s:%d [%s] %d' % (self.jitcode.name,
                                                       self.pc, values,
                                                       self.exception_target))

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
            staticdata = self.metainterp.staticdata
            stop = staticdata.opcode_implementations[op](self, pc)
            #self.metainterp.most_recent_mp = None
            if stop:
                break

    def generate_guard(self, pc, opnum, box, extraargs=[]):
        if isinstance(box, Const):    # no need for a guard
            return
        metainterp = self.metainterp
        if metainterp.is_blackholing():
            return
        saved_pc = self.pc
        self.pc = pc
        resumebuilder = resume.ResumeDataBuilder()
        if metainterp.staticdata.virtualizable_info is not None:
            resumebuilder.generate_boxes(metainterp.virtualizable_boxes)
        for frame in metainterp.framestack:
            resumebuilder.generate_frame_info(frame.jitcode, frame.pc,
                                              frame.exception_target)
            resumebuilder.generate_boxes(frame.env)
        if box is not None:
            moreargs = [box] + extraargs
        else:
            moreargs = list(extraargs)
        guard_op = metainterp.history.record(opnum, moreargs, None)
        resumedescr = compile.ResumeGuardDescr(
            metainterp.history, len(metainterp.history.operations)-1)
        liveboxes = resumebuilder.finish(resumedescr)
        op = history.ResOperation(rop.FAIL, liveboxes, None, descr=resumedescr)
        guard_op.suboperations = [op]
        metainterp.attach_debug_info(guard_op)
        self.pc = saved_pc
        return guard_op

    def implement_guard_value(self, pc, box):
        if isinstance(box, Box):
            promoted_box = box.constbox()
            self.generate_guard(pc, rop.GUARD_VALUE, box, [promoted_box])
            self.metainterp.replace_box(box, promoted_box)
            return promoted_box
        else:
            return box     # no promotion needed, already a Const

    def cls_of_box(self, box):
        return self.metainterp.cpu.ts.cls_of_box(self.metainterp.cpu, box)

    @specialize.arg(1)
    def execute(self, opnum, argboxes, descr=None):
        resbox = self.metainterp.execute_and_record(opnum, argboxes, descr)
        if resbox is not None:
            self.make_result_box(resbox)

    @specialize.arg(1)
    def execute_with_exc(self, opnum, argboxes, descr=None):
        self.execute(opnum, argboxes, descr)
        if not we_are_translated():
            self.metainterp._debug_history.append(['call',
                                                  argboxes[0], argboxes[1:]])
        return self.metainterp.handle_exception()

# ____________________________________________________________

class MetaInterpStaticData(object):
    virtualizable_info = None

    def __init__(self, portal_graph, graphs, cpu, stats, options,
                 optimizer=None, profile=None, warmrunnerdesc=None):
        self.portal_graph = portal_graph
        self.cpu = cpu
        self.stats = stats
        self.options = options
        if cpu.logger_cls is not None:
            options.logger_noopt = cpu.logger_cls()

        RESULT = portal_graph.getreturnvar().concretetype
        self.result_type = history.getkind(RESULT)

        self.opcode_implementations = []
        self.opcode_names = []
        self.opname_to_index = {}
        if optimizer is None:
            from pypy.jit.metainterp import optimize as optimizer
        self.optimize_loop = optimizer.optimize_loop
        self.optimize_bridge = optimizer.optimize_bridge

        if profile is not None:
            self.profiler = profile()
        else:
            from pypy.jit.metainterp.jitprof import EmptyProfiler
            self.profiler = EmptyProfiler()

        self.warmrunnerdesc = warmrunnerdesc
        self._op_goto_if_not = self.find_opcode('goto_if_not')

        optmodule = self.optimize_loop.__module__
        optmodule = optmodule.split('.')[-1]
        backendmodule = self.cpu.__module__
        backendmodule = backendmodule.split('.')[-2]
        self.jit_starting_line = 'JIT starting (%s, %s)' % (optmodule,
                                                            backendmodule)

    def _freeze_(self):
        return True

    def finish_setup(self):
        warmrunnerdesc = self.warmrunnerdesc
        if warmrunnerdesc is not None:
            self.num_green_args = warmrunnerdesc.num_green_args
            self.state = warmrunnerdesc.state
        else:
            self.num_green_args = 0
            self.state = None
        self.globaldata = MetaInterpGlobalData(self)

    def _setup_once(self):
        """Runtime setup needed by the various components of the JIT."""
        if not self.globaldata.initialized:
            self._setup_class_sizes()
            self.cpu.setup_once()
            log(self.jit_starting_line)
            if not self.profiler.initialized:
                self.profiler.start()
                self.profiler.initialized = True
            self.globaldata.initialized = True
            if self.options.logger_noopt is not None:
                self.options.logger_noopt.create_log('.noopt')

    def _setup_class_sizes(self):
        class_sizes = {}
        for vtable, sizedescr in self._class_sizes:
            if not self.cpu.is_oo:
                vtable = llmemory.cast_ptr_to_adr(vtable)
                vtable = self.cpu.cast_adr_to_int(vtable)
            else:
                vtable = ootype.cast_to_object(vtable)
            class_sizes[vtable] = sizedescr
        self.cpu.set_class_sizes(class_sizes)

    def generate_bytecode(self, policy):
        self._codewriter = codewriter.CodeWriter(self, policy)
        self.portal_code = self._codewriter.make_portal_bytecode(
            self.portal_graph)
        self._class_sizes = self._codewriter.class_sizes

    # ---------- construction-time interface ----------

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

# ____________________________________________________________

class MetaInterpGlobalData(object):
    def __init__(self, staticdata):
        self._debug_history = []
        self.initialized = False
        #
        state = staticdata.state
        if state is not None:
            self.unpack_greenkey = state.unwrap_greenkey
            self.compiled_merge_points = r_dict(state.comparekey,state.hashkey)
                # { (greenargs): [MergePoints] }
        else:
            self.compiled_merge_points = {}    # for tests only; not RPython
            self.unpack_greenkey = tuple
        if staticdata.virtualizable_info:
            self.blackhole_virtualizable = staticdata.virtualizable_info.null_vable

# ____________________________________________________________

class MetaInterp(object):
    in_recursion = 0
    def __init__(self, staticdata):
        self.staticdata = staticdata
        self.cpu = staticdata.cpu
        if not we_are_translated():
            self._debug_history = staticdata.globaldata._debug_history

    def is_blackholing(self):
        return isinstance(self.history, history.BlackHole)

    def newframe(self, jitcode):
        if not we_are_translated():
            self._debug_history.append(['enter', jitcode, None])
        f = MIFrame(self, jitcode)
        self.framestack.append(f)
        return f

    def finishframe(self, resultbox):
        frame = self.framestack.pop()
        if frame.jitcode is self.staticdata.portal_code:
            self.in_recursion -= 1
        if not we_are_translated():
            self._debug_history.append(['leave', frame.jitcode, None])
        if self.framestack:
            if resultbox is not None:
                self.framestack[-1].make_result_box(resultbox)
            return True
        else:
            if not self.is_blackholing():
                self.compile_done_with_this_frame(resultbox)
            sd = self.staticdata
            if sd.result_type == 'void':
                assert resultbox is None
                raise sd.DoneWithThisFrameVoid()
            elif sd.result_type == 'int':
                raise sd.DoneWithThisFrameInt(resultbox.getint())
            elif sd.result_type == 'ptr':
                raise sd.DoneWithThisFramePtr(resultbox.getptr_base())
            elif self.cpu.is_oo and sd.result_type == 'obj':
                raise sd.DoneWithThisFrameObj(resultbox.getobj())
            else:
                assert False

    def finishframe_exception(self, exceptionbox, excvaluebox):
        # detect and propagate some exceptions early:
        #  - AssertionError
        #  - all subclasses of JitException
        if we_are_translated():
            from pypy.jit.metainterp.warmspot import JitException
            e = self.cpu.ts.get_exception_obj(excvaluebox)
            if isinstance(e, JitException) or isinstance(e, AssertionError):
                raise Exception, e
        #
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
        if not self.is_blackholing():
            self.compile_exit_frame_with_exception(excvaluebox)
        if self.cpu.is_oo:
            raise self.staticdata.ExitFrameWithExceptionObj(excvaluebox.getobj())
        else:
            raise self.staticdata.ExitFrameWithExceptionPtr(excvaluebox.getptr_base())

    def raise_overflow_error(self):
        etype, evalue = self.cpu.get_overflow_error()
        return self.finishframe_exception(
            self.cpu.ts.get_exception_box(etype),
            self.cpu.ts.get_exc_value_box(evalue))

    def raise_zero_division_error(self):
        etype, evalue = self.cpu.get_zero_division_error()
        return self.finishframe_exception(
            self.cpu.ts.get_exception_box(etype),
            self.cpu.ts.get_exc_value_box(evalue))

    def create_empty_history(self):
        self.history = history.History(self.cpu)
        if self.staticdata.stats is not None:
            self.staticdata.stats.history = self.history

    def _all_constants(self, boxes):
        for box in boxes:
            if not isinstance(box, Const):
                return False
        return True

    @specialize.arg(1)
    def execute_and_record(self, opnum, argboxes, descr=None):
        history.check_descr(descr)
        # residual calls require attention to keep virtualizables in-sync.
        # CALL_PURE doesn't need it because so far 'promote_virtualizable'
        # as an operation is enough to make the called function non-pure.
        require_attention = (opnum == rop.CALL or opnum == rop.OOSEND)
        if require_attention:
            self.before_residual_call()
        # execute the operation
        resbox = executor.execute(self.cpu, opnum, argboxes, descr)
        if require_attention:
            require_attention = self.after_residual_call()
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
            op = self.history.record(opnum, argboxes, resbox, descr)
            self.attach_debug_info(op)
        if require_attention:
            self.after_generate_residual_call()
        return resbox

    def attach_debug_info(self, op):
        if (not we_are_translated() and op is not None
            and getattr(self, 'framestack', None)):
            op.pc = self.framestack[-1].pc
            op.name = self.framestack[-1].jitcode.name

    def _interpret(self):
        # Execute the frames forward until we raise a DoneWithThisFrame,
        # a ContinueRunningNormally, or a GenerateMergePoint exception.
        if not we_are_translated():
            history.log.event('ENTER' + self.history.extratext)
            self.staticdata.stats.enter_count += 1
        elif DEBUG:
            debug_print('~~~ ENTER', self.history.extratext)
        try:
            while True:
                self.framestack[-1].run_one_step()
        finally:
            if self.is_blackholing():
                self.staticdata.profiler.end_blackhole()
            else:
                self.staticdata.profiler.end_tracing()
            if not we_are_translated():
                history.log.event('LEAVE' + self.history.extratext)
            elif DEBUG:
                debug_print('~~~ LEAVE', self.history.extratext)

    def interpret(self):
        self.in_recursion = 0
        if we_are_translated():
            self._interpret()
        else:
            try:
                self._interpret()
            except:
                import sys
                if sys.exc_info()[0] is not None:
                    history.log.info(sys.exc_info()[0].__name__)
                raise

    def compile_and_run_once(self, *args):
        log('Switching from interpreter to compiler')
        original_boxes = self.initialize_state_from_start(*args)
        self.current_merge_points = [(original_boxes, 0)]
        self.resumekey = compile.ResumeFromInterpDescr(original_boxes)
        self.extra_rebuild_operations = -1
        self.seen_can_enter_jit = False
        try:
            self.interpret()
            assert False, "should always raise"
        except GenerateMergePoint, gmp:
            return self.designate_target_loop(gmp)

    def handle_guard_failure(self, exec_result, key):
        self.initialize_state_from_guard_failure(exec_result)
        assert isinstance(key, compile.ResumeGuardDescr)
        top_history = key.find_toplevel_history()
        source_loop = top_history.source_link
        assert isinstance(source_loop, history.TreeLoop)
        original_boxes = source_loop.greenkey + top_history.inputargs
        self.current_merge_points = [(original_boxes, 0)]
        self.resumekey = key
        self.seen_can_enter_jit = False
        guard_op = key.get_guard_op()
        try:
            self.prepare_resume_from_failure(guard_op.opnum)
            self.interpret()
            assert False, "should always raise"
        except GenerateMergePoint, gmp:
            return self.designate_target_loop(gmp)

    def forget_consts(self, boxes, startindex=0):
        for i in range(startindex, len(boxes)):
            box = boxes[i]
            if isinstance(box, Const):
                constbox = box
                box = constbox.clonebox()
                boxes[i] = box
                self.history.record(rop.SAME_AS, [constbox], box)

    def reached_can_enter_jit(self, live_arg_boxes):
        self.forget_consts(live_arg_boxes, self.staticdata.num_green_args)
        live_arg_boxes = live_arg_boxes[:]
        if self.staticdata.virtualizable_info is not None:
            # we use ':-1' to remove the last item, which is the virtualizable
            # itself
            self.forget_consts(self.virtualizable_boxes)
            live_arg_boxes += self.virtualizable_boxes[:-1]
        # Called whenever we reach the 'can_enter_jit' hint.
        # First, attempt to make a bridge:
        # - if self.resumekey is a ResumeGuardDescr, it starts from a guard
        #   that failed;
        # - if self.resumekey is a ResumeFromInterpDescr, it starts directly
        #   from the interpreter.
        self.compile_bridge(live_arg_boxes)
        # raises in case it works -- which is the common case, hopefully,
        # at least for bridges starting from a guard.

        # Search in current_merge_points for original_boxes with compatible
        # green keys, representing the beginning of the same loop as the one
        # we end now. 
       
        for j in range(len(self.current_merge_points)-1, -1, -1):
            original_boxes, start = self.current_merge_points[j]
            assert len(original_boxes) == len(live_arg_boxes)
            for i in range(self.staticdata.num_green_args):
                box1 = original_boxes[i]
                box2 = live_arg_boxes[i]
                if not box1.equals(box2):
                    break
            else:
                # Found!  Compile it as a loop.
                if j > 0:
                    # clean up, but without shifting the end of the list
                    # (that would make 'history_guard_index' invalid)
                    for i in range(start):
                        self.history.operations[i] = None
                else:
                    assert start == 0
                    if self.extra_rebuild_operations >= 0:
                        # The history only starts at a bridge, not at the
                        # full loop header.  Complete it as a full loop by
                        # inserting a copy of the operations from the old
                        # loop branch before the guard that failed.
                        start = self.extra_rebuild_operations
                        assert start >= 0
                        # clean up, but without shifting the end of the list
                        for i in range(start):
                            self.history.operations[i] = None
                        compile.prepare_loop_from_bridge(self, self.resumekey)
                loop = self.compile(original_boxes, live_arg_boxes, start)
                raise GenerateMergePoint(live_arg_boxes, loop)

        # Otherwise, no loop found so far, so continue tracing.
        start = len(self.history.operations)
        self.current_merge_points.append((live_arg_boxes, start))

    def designate_target_loop(self, gmp):
        loop = gmp.target_loop
        num_green_args = self.staticdata.num_green_args
        residual_args = self.get_residual_args(loop,
                                               gmp.argboxes[num_green_args:])
        history.set_future_values(self.cpu, residual_args)
        self.clean_up_history()
        return loop

    def clean_up_history(self):
        # Clear the BoxPtrs used in self.history, at the end.  The
        # purpose of this is to clear the boxes that are also used in
        # the TreeLoop just produced.  After this, there should be no
        # reference left to temporary values in long-living BoxPtrs.
        # A note about recursion: setting to NULL like this should be
        # safe, because ResumeGuardDescr.restore_patched_boxes should
        # save and restore all the boxes that are also used by callers.
        if self.history.inputargs is not None:
            for box in self.history.inputargs:
                self.cpu.ts.clean_box(box)
        lists = [self.history.operations]
        while lists:
            for op in lists.pop():
                if op is None:
                    continue
                if op.result is not None:
                    self.cpu.ts.clean_box(op.result)
                if op.suboperations is not None:
                    lists.append(op.suboperations)
                if op.optimized is not None:
                    lists.append(op.optimized.suboperations)
                    if op.optimized.result is not None:
                        self.cpu.ts.clean_box(op.optimized.result)

    def prepare_resume_from_failure(self, opnum):
        if opnum == rop.GUARD_TRUE:     # a goto_if_not that jumps only now
            self.framestack[-1].follow_jump()
        elif opnum == rop.GUARD_FALSE:     # a goto_if_not that stops jumping
            self.framestack[-1].dont_follow_jump()
        elif opnum == rop.GUARD_NO_EXCEPTION or opnum == rop.GUARD_EXCEPTION:
            self.handle_exception()
        elif opnum == rop.GUARD_NO_OVERFLOW:   # an overflow now detected
            self.raise_overflow_error()

    def compile(self, original_boxes, live_arg_boxes, start):
        num_green_args = self.staticdata.num_green_args
        self.history.inputargs = original_boxes[num_green_args:]
        greenkey = original_boxes[:num_green_args]
        glob = self.staticdata.globaldata
        greenargs = glob.unpack_greenkey(greenkey)
        old_loops = glob.compiled_merge_points.setdefault(greenargs, [])
        self.history.record(rop.JUMP, live_arg_boxes[num_green_args:], None)
        loop = compile.compile_new_loop(self, old_loops, greenkey, start)
        assert loop is not None
        if not we_are_translated():
            loop._call_history = self._debug_history
        return loop

    def compile_bridge(self, live_arg_boxes):
        num_green_args = self.staticdata.num_green_args
        greenkey = live_arg_boxes[:num_green_args]
        glob = self.staticdata.globaldata
        greenargs = glob.unpack_greenkey(greenkey)
        try:
            old_loops = glob.compiled_merge_points[greenargs]
        except KeyError:
            return
        self.history.record(rop.JUMP, live_arg_boxes[num_green_args:], None)
        target_loop = compile.compile_new_bridge(self, old_loops,
                                                 self.resumekey)
        if target_loop is not None:   # raise if it *worked* correctly
            raise GenerateMergePoint(live_arg_boxes, target_loop)
        self.history.operations.pop()     # remove the JUMP

    def compile_done_with_this_frame(self, exitbox):
        self.gen_store_back_in_virtualizable()
        # temporarily put a JUMP to a pseudo-loop
        sd = self.staticdata
        if sd.result_type == 'void':
            assert exitbox is None
            exits = []
            loops = compile.loops_done_with_this_frame_void
        elif sd.result_type == 'int':
            exits = [exitbox]
            loops = compile.loops_done_with_this_frame_int
        elif sd.result_type == 'ptr':
            exits = [exitbox]
            loops = compile.loops_done_with_this_frame_ptr
        elif sd.cpu.is_oo and sd.result_type == 'obj':
            exits = [exitbox]
            loops = compile.loops_done_with_this_frame_obj
        else:
            assert False
        self.history.record(rop.JUMP, exits, None)
        target_loop = compile.compile_new_bridge(self, loops, self.resumekey)
        assert target_loop is loops[0]

    def compile_exit_frame_with_exception(self, valuebox):
        self.gen_store_back_in_virtualizable()
        # temporarily put a JUMP to a pseudo-loop
        self.history.record(rop.JUMP, [valuebox], None)
        if self.cpu.is_oo:
            loops = compile.loops_exit_frame_with_exception_obj
        else:
            loops = compile.loops_exit_frame_with_exception_ptr
        target_loop = compile.compile_new_bridge(self, loops, self.resumekey)
        assert target_loop is loops[0]

    def get_residual_args(self, loop, args):
        if loop.specnodes is None:     # it is None only for tests
            return args
        assert len(loop.specnodes) == len(args)
        expanded_args = []
        for i in range(len(loop.specnodes)):
            specnode = loop.specnodes[i]
            specnode.extract_runtime_data(self.cpu, args[i], expanded_args)
        return expanded_args

    def _initialize_from_start(self, original_boxes, num_green_args, *args):
        if args:
            from pypy.jit.metainterp.warmspot import wrap
            box = wrap(self.cpu, args[0], num_green_args > 0)
            original_boxes.append(box)
            self._initialize_from_start(original_boxes, num_green_args-1,
                                        *args[1:])

    def initialize_state_from_start(self, *args):
        self.staticdata._setup_once()
        self.staticdata.profiler.start_tracing()
        self.create_empty_history()
        num_green_args = self.staticdata.num_green_args
        original_boxes = []
        self._initialize_from_start(original_boxes, num_green_args, *args)
        # ----- make a new frame -----
        self.framestack = []
        f = self.newframe(self.staticdata.portal_code)
        f.pc = 0
        f.env = original_boxes[:]
        self.initialize_virtualizable(original_boxes)
        return original_boxes

    def initialize_state_from_guard_failure(self, guard_failure):
        # guard failure: rebuild a complete MIFrame stack
        resumedescr = guard_failure.descr
        assert isinstance(resumedescr, compile.ResumeGuardDescr)
        warmrunnerstate = self.staticdata.state
        must_compile = warmrunnerstate.must_compile_from_failure(resumedescr)
        if must_compile:
            guard_op = resumedescr.get_guard_op()
            suboperations = guard_op.suboperations
            if suboperations[-1] is not guard_failure:
                must_compile = False
                log("ignoring old version of the guard")
            else:
                self.history = history.History(self.cpu)
                extra = len(suboperations) - 1
                assert extra >= 0
                for i in range(extra):
                    self.history.operations.append(suboperations[i])
                self.extra_rebuild_operations = extra
        if must_compile:
            self.staticdata.profiler.start_tracing()
        else:
            self.staticdata.profiler.start_blackhole()
            self.history = history.BlackHole(self.cpu)
            # the BlackHole is invalid because it doesn't start with
            # guard_failure.key.guard_op.suboperations, but that's fine
        self.rebuild_state_after_failure(resumedescr, guard_failure.args)

    def initialize_virtualizable(self, original_boxes):
        vinfo = self.staticdata.virtualizable_info
        if vinfo is not None:
            virtualizable_box = original_boxes[vinfo.index_of_virtualizable]
            virtualizable = vinfo.unwrap_virtualizable_box(virtualizable_box)
            # The field 'virtualizable_boxes' is not even present
            # if 'virtualizable_info' is None.  Check for that first.
            self.virtualizable_boxes = vinfo.read_boxes(self.cpu,
                                                        virtualizable)
            original_boxes += self.virtualizable_boxes
            self.virtualizable_boxes.append(virtualizable_box)
            self.initialize_virtualizable_enter()

    def initialize_virtualizable_enter(self):
        # Switched from the interpreter (case 1 in the comment in
        # virtualizable.py) to tracing mode (case 2): force vable_rti to NULL.
        vinfo = self.staticdata.virtualizable_info
        virtualizable_box = self.virtualizable_boxes[-1]
        virtualizable = vinfo.unwrap_virtualizable_box(virtualizable_box)
        vinfo.clear_vable_rti(virtualizable)

    def before_residual_call(self):
        vinfo = self.staticdata.virtualizable_info
        if vinfo is not None:
            virtualizable_box = self.virtualizable_boxes[-1]
            virtualizable = vinfo.unwrap_virtualizable_box(virtualizable_box)
            vinfo.tracing_before_residual_call(virtualizable)

    def after_residual_call(self):
        vinfo = self.staticdata.virtualizable_info
        if vinfo is not None:
            virtualizable_box = self.virtualizable_boxes[-1]
            virtualizable = vinfo.unwrap_virtualizable_box(virtualizable_box)
            if vinfo.tracing_after_residual_call(virtualizable):
                # This is after the residual call is done, but before it
                # is actually generated.  We first generate a store-
                # everything-back, *without actually performing it now*
                # as it contains the old values (before the call)!
                self.gen_store_back_in_virtualizable_no_perform()
                return True    # must call after_generate_residual_call()
        # xxx don't call after_generate_residual_call() or
        # in the case of blackholing abuse it to resynchronize
        return self.is_blackholing()

    def after_generate_residual_call(self):
        # Called after generating a residual call, and only if
        # after_residual_call() returned True, i.e. if code in the residual
        # call causes the virtualizable to escape.  Reload the modified
        # fields of the virtualizable.
        self.gen_load_fields_from_virtualizable()

    def handle_exception(self):
        etype = self.cpu.get_exception()
        evalue = self.cpu.get_exc_value()
        assert bool(etype) == bool(evalue)
        self.cpu.clear_exception()
        frame = self.framestack[-1]
        if etype:
            exception_box = self.cpu.ts.get_exception_box(etype)
            exc_value_box = self.cpu.ts.get_exc_value_box(evalue)
            op = frame.generate_guard(frame.pc, rop.GUARD_EXCEPTION,
                                      None, [exception_box])
            if op:
                op.result = exc_value_box
            return self.finishframe_exception(exception_box, exc_value_box)
        else:
            frame.generate_guard(frame.pc, rop.GUARD_NO_EXCEPTION, None, [])
            return False

    def handle_overflow_error(self):
        frame = self.framestack[-1]
        if self.cpu._overflow_flag:
            self.cpu._overflow_flag = False
            frame.generate_guard(frame.pc, rop.GUARD_OVERFLOW, None, [])
            return self.raise_overflow_error()
        else:
            frame.generate_guard(frame.pc, rop.GUARD_NO_OVERFLOW, None, [])
            return False

    def rebuild_state_after_failure(self, resumedescr, newboxes):
        if not we_are_translated():
            self._debug_history.append(['guard_failure', None, None])
        vinfo = self.staticdata.virtualizable_info
        resumereader = resume.ResumeDataReader(resumedescr, newboxes, self)
        if vinfo is not None:
            self.virtualizable_boxes = resumereader.consume_boxes()
            # just jumped away from assembler (case 4 in the comment in
            # virtualizable.py) into tracing (case 2); check that vable_rti
            # is and stays NULL.
            virtualizable_box = self.virtualizable_boxes[-1]
            virtualizable = vinfo.unwrap_virtualizable_box(virtualizable_box)
            assert not virtualizable.vable_rti
            self.synchronize_virtualizable()
            #
        self.framestack = []
        while resumereader.has_more_frame_infos():
            jitcode, pc, exception_target = resumereader.consume_frame_info()
            env = resumereader.consume_boxes()
            f = self.newframe(jitcode)
            f.setup_resume_at_op(pc, exception_target, env)

    def check_synchronized_virtualizable(self):
        if not we_are_translated():
            vinfo = self.staticdata.virtualizable_info
            virtualizable_box = self.virtualizable_boxes[-1]
            virtualizable = vinfo.unwrap_virtualizable_box(virtualizable_box)
            vinfo.check_boxes(virtualizable, self.virtualizable_boxes)

    def synchronize_virtualizable(self):
        vinfo = self.staticdata.virtualizable_info
        virtualizable_box = self.virtualizable_boxes[-1]
        virtualizable = vinfo.unwrap_virtualizable_box(virtualizable_box)
        vinfo.write_boxes(virtualizable, self.virtualizable_boxes)

    def gen_load_fields_from_virtualizable(self):
        vinfo = self.staticdata.virtualizable_info
        if vinfo is not None:
            vbox = self.virtualizable_boxes[-1]
            for i in range(vinfo.num_static_extra_boxes):
                fieldbox = self.execute_and_record(rop.GETFIELD_GC, [vbox],
                                        descr=vinfo.static_field_descrs[i])
                self.virtualizable_boxes[i] = fieldbox
            i = vinfo.num_static_extra_boxes
            virtualizable = vinfo.unwrap_virtualizable_box(vbox)
            for k in range(vinfo.num_arrays):
                abox = self.execute_and_record(rop.GETFIELD_GC, [vbox],
                                         descr=vinfo.array_field_descrs[k])
                for j in range(vinfo.get_array_length(virtualizable, k)):
                    itembox = self.execute_and_record(rop.GETARRAYITEM_GC,
                                                      [abox, ConstInt(j)],
                                            descr=vinfo.array_descrs[k])
                    self.virtualizable_boxes[i] = itembox
                    i += 1
            assert i + 1 == len(self.virtualizable_boxes)

    def gen_store_back_in_virtualizable(self):
        vinfo = self.staticdata.virtualizable_info
        if vinfo is not None:
            # xxx only write back the fields really modified
            vbox = self.virtualizable_boxes[-1]
            for i in range(vinfo.num_static_extra_boxes):
                fieldbox = self.virtualizable_boxes[i]
                self.execute_and_record(rop.SETFIELD_GC, [vbox, fieldbox],
                                        descr=vinfo.static_field_descrs[i])
            i = vinfo.num_static_extra_boxes
            virtualizable = vinfo.unwrap_virtualizable_box(vbox)
            for k in range(vinfo.num_arrays):
                abox = self.execute_and_record(rop.GETFIELD_GC, [vbox],
                                         descr=vinfo.array_field_descrs[k])
                for j in range(vinfo.get_array_length(virtualizable, k)):
                    itembox = self.virtualizable_boxes[i]
                    i += 1
                    self.execute_and_record(rop.SETARRAYITEM_GC,
                                            [abox, ConstInt(j), itembox],
                                            descr=vinfo.array_descrs[k])
            assert i + 1 == len(self.virtualizable_boxes)

    def gen_store_back_in_virtualizable_no_perform(self):
        vinfo = self.staticdata.virtualizable_info
        # xxx only write back the fields really modified
        vbox = self.virtualizable_boxes[-1]
        for i in range(vinfo.num_static_extra_boxes):
            fieldbox = self.virtualizable_boxes[i]
            self.history.record(rop.SETFIELD_GC, [vbox, fieldbox], None,
                                descr=vinfo.static_field_descrs[i])
        i = vinfo.num_static_extra_boxes
        virtualizable = vinfo.unwrap_virtualizable_box(vbox)
        for k in range(vinfo.num_arrays):
            abox = vinfo.BoxArray()
            self.history.record(rop.GETFIELD_GC, [vbox], abox,
                                descr=vinfo.array_field_descrs[k])
            for j in range(vinfo.get_array_length(virtualizable, k)):
                itembox = self.virtualizable_boxes[i]
                i += 1
                self.history.record(rop.SETARRAYITEM_GC,
                                    [abox, ConstInt(j), itembox],
                                    None,
                                    descr=vinfo.array_descrs[k])
        assert i + 1 == len(self.virtualizable_boxes)

    def replace_box(self, oldbox, newbox):
        for frame in self.framestack:
            boxes = frame.env
            for i in range(len(boxes)):
                if boxes[i] is oldbox:
                    boxes[i] = newbox
        if self.staticdata.virtualizable_info is not None:
            boxes = self.virtualizable_boxes
            for i in range(len(boxes)):
                if boxes[i] is oldbox:
                    boxes[i] = newbox


class GenerateMergePoint(Exception):
    def __init__(self, args, target_loop):
        assert target_loop is not None
        self.argboxes = args
        self.target_loop = target_loop
