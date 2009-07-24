import py
from pypy.rpython.lltypesystem import rclass
from pypy.rpython.ootypesystem import ootype
from pypy.rlib.objectmodel import instantiate
from pypy.jit.metainterp.test.test_resume import MyMetaInterp
from pypy.jit.metainterp.test.test_optimizefindnode import (LLtypeMixin,
                                                            OOtypeMixin,
                                                            BaseTest)
from pypy.jit.metainterp.optimizeopt import optimize_loop_1
from pypy.jit.metainterp.history import AbstractDescr, ConstInt
from pypy.jit.metainterp import resume, executor, compile
from pypy.jit.metainterp.resoperation import rop, opname
from pypy.jit.metainterp.test.oparser import parse

# ____________________________________________________________

def equaloplists(oplist1, oplist2, remap={}):
    print '-'*20, 'Comparing lists', '-'*20
    for op1, op2 in zip(oplist1, oplist2):
        txt1 = str(op1)
        txt2 = str(op2)
        while txt1 or txt2:
            print '%-39s| %s' % (txt1[:39], txt2[:39])
            txt1 = txt1[39:]
            txt2 = txt2[39:]
        assert op1.opnum == op2.opnum
        assert len(op1.args) == len(op2.args)
        for x, y in zip(op1.args, op2.args):
            assert x == remap.get(y, y)
        assert op1.result == remap.get(op2.result, op2.result)
        assert op1.descr == op2.descr
        if op1.suboperations:
            assert equaloplists(op1.suboperations, op2.suboperations, remap)
    assert len(oplist1) == len(oplist2)
    print '-'*57
    return True

def test_equaloplists():
    ops = """
    [i0]
    i1 = int_add(i0, 1)
    guard_true(i1)
        i2 = int_add(i1, 1)
        fail(i2)
    jump(i1)
    """
    loop1 = parse(ops)
    loop2 = parse(ops)
    loop3 = parse(ops.replace("i2 = int_add", "i2 = int_sub"))
    assert equaloplists(loop1.operations, loop2.operations)
    py.test.raises(AssertionError,
                   "equaloplists(loop1.operations, loop3.operations)")

def test_equaloplists_remap():
    ops1 = """
    [i0]
    i1 = int_add(i0, 1)
    guard_true(i1)
        i2 = int_add(i1, 1)
        fail(i2)
    jump(i1)
    """
    ops2 = """
    [i3]
    i1 = int_add(i3, 1)
    guard_true(i1)
        i5 = int_add(i1, 1)
        fail(i5)
    jump(i1)
    """
    loop1 = parse(ops1)
    loop2 = parse(ops2)
    py.test.raises(AssertionError,
                   "equaloplists(loop1.operations, loop2.operations)")
    i0 = loop1.inputargs[0]
    i3 = loop2.inputargs[0]
    i2 = loop1.operations[1].suboperations[0].result
    i5 = loop2.operations[1].suboperations[0].result
    assert equaloplists(loop1.operations, loop2.operations,
                        {i3: i0, i5: i2})

# ____________________________________________________________

class BaseTestOptimizeOpt(BaseTest):

    def assert_equal(self, optimized, expected):
        assert len(optimized.inputargs) == len(expected.inputargs)
        remap = {}
        for box1, box2 in zip(optimized.inputargs, expected.inputargs):
            assert box1.__class__ == box2.__class__
            remap[box2] = box1
        assert equaloplists(optimized.operations,
                            expected.operations,
                            remap)

    def optimize_loop(self, ops, spectext, optops, boxkinds=None, **values):
        loop = self.parse(ops, boxkinds=boxkinds)
        loop.setvalues(**values)
        loop.specnodes = self.unpack_specnodes(spectext)
        assert loop.operations[-1].opnum == rop.JUMP
        loop.operations[-1].jump_target = loop
        #
        optimize_loop_1(self.cpu, loop)
        #
        expected = self.parse(optops, boxkinds=boxkinds)
        self.assert_equal(loop, expected)

    def test_simple(self):
        ops = """
        [i]
        i0 = int_sub(i, 1)
        guard_value(i0, 0)
          fail(i0)
        jump(i)
        """
        self.optimize_loop(ops, 'Not', ops, i0=0)

    def test_constant_propagate(self):
        ops = """
        []
        i0 = int_add(2, 3)
        i1 = int_is_true(i0)
        guard_true(i1)
          fail()
        i2 = bool_not(i1)
        guard_false(i2)
          fail()
        guard_value(i0, 5)
          fail()
        jump()
        """
        expected = """
        []
        jump()
        """
        self.optimize_loop(ops, '', expected, i0=5, i1=1, i2=0)

    def test_constfold_all(self):
        for op in range(rop.INT_ADD, rop.BOOL_NOT+1):
            try:
                op = opname[op]
            except KeyError:
                continue
            ops = """
            []
            i1 = %s(3, 2)
            jump()
            """ % op.lower()
            expected = """
            []
            jump()
            """
            self.optimize_loop(ops, '', expected)

    # ----------

    def test_remove_guard_class_1(self):
        ops = """
        [p0]
        guard_class(p0, ConstClass(node_vtable))
          fail()
        guard_class(p0, ConstClass(node_vtable))
          fail()
        jump(p0)
        """
        expected = """
        [p0]
        guard_class(p0, ConstClass(node_vtable))
          fail()
        jump(p0)
        """
        self.optimize_loop(ops, 'Not', expected)

    def test_remove_guard_class_2(self):
        ops = """
        [i0]
        p0 = new_with_vtable(ConstClass(node_vtable))
        escape(p0)
        guard_class(p0, ConstClass(node_vtable))
          fail()
        jump(i0)
        """
        expected = """
        [i0]
        p0 = new_with_vtable(ConstClass(node_vtable))
        escape(p0)
        jump(i0)
        """
        self.optimize_loop(ops, 'Not', expected)

    def test_remove_consecutive_guard_value_constfold(self):
        ops = """
        [i0]
        guard_value(i0, 0)
          fail()
        i1 = int_add(i0, 1)
        guard_value(i1, 1)
          fail()
        i2 = int_add(i1, 2)
        jump(i2)
        """
        expected = """
        [i0]
        guard_value(i0, 0)
            fail()
        jump(3)
        """
        self.optimize_loop(ops, 'Not', expected, i0=0, i1=1, i2=3)

    def test_ooisnull_oononnull_1(self):
        ops = """
        [p0]
        guard_class(p0, ConstClass(node_vtable))
          fail()
        i0 = oononnull(p0)
        guard_true(i0)
          fail()
        i1 = ooisnull(p0)
        guard_false(i1)
          fail()
        jump(p0)
        """
        expected = """
        [p0]
        guard_class(p0, ConstClass(node_vtable))
          fail()
        jump(p0)
        """
        self.optimize_loop(ops, 'Not', expected, i0=1, i1=0)

    def test_ooisnull_oononnull_2(self):
        py.test.skip("less important")
        ops = """
        [p0]
        i0 = oononnull(p0)         # p0 != NULL
        guard_true(i0)
          fail()
        i1 = ooisnull(p0)
        guard_false(i1)
          fail()
        jump(p0)
        """
        expected = """
        [p0]
        i0 = oononnull(p0)
        guard_true(i0)
          fail()
        jump(p0)
        """
        self.optimize_loop(ops, 'Not', expected, i0=1, i1=0)

    def test_oois_1(self):
        ops = """
        [p0]
        guard_class(p0, ConstClass(node_vtable))
          fail()
        i0 = ooisnot(p0, NULL)
        guard_true(i0)
          fail()
        i1 = oois(p0, NULL)
        guard_false(i1)
          fail()
        i2 = ooisnot(NULL, p0)
        guard_true(i0)
          fail()
        i3 = oois(NULL, p0)
        guard_false(i1)
          fail()
        jump(p0)
        """
        expected = """
        [p0]
        guard_class(p0, ConstClass(node_vtable))
          fail()
        jump(p0)
        """
        self.optimize_loop(ops, 'Not', expected, i0=1, i1=0, i2=1, i3=0)

    def test_nonnull_1(self):
        ops = """
        [p0]
        setfield_gc(p0, 5, descr=valuedescr)     # forces p0 != NULL
        i0 = ooisnot(p0, NULL)
        guard_true(i0)
          fail()
        i1 = oois(p0, NULL)
        guard_false(i1)
          fail()
        i2 = ooisnot(NULL, p0)
        guard_true(i0)
          fail()
        i3 = oois(NULL, p0)
        guard_false(i1)
          fail()
        i4 = oononnull(p0)
        guard_true(i4)
          fail()
        i5 = ooisnull(p0)
        guard_false(i5)
          fail()
        jump(p0)
        """
        expected = """
        [p0]
        setfield_gc(p0, 5, descr=valuedescr)
        jump(p0)
        """
        self.optimize_loop(ops, 'Not', expected,
                           i0=1, i1=0, i2=1, i3=0, i4=1, i5=0)

    def test_const_guard_value(self):
        ops = """
        []
        i = int_add(5, 3)
        guard_value(i, 8)
            fail()
        jump()
        """
        expected = """
        []
        jump()
        """
        self.optimize_loop(ops, '', expected, i=8)

    def test_constptr_guard_value(self):
        ops = """
        []
        p1 = escape()
        guard_value(p1, ConstPtr(myptr))
            fail()
        jump()
        """
        self.optimize_loop(ops, '', ops, p1=self.nodebox.value,
                           boxkinds={'myptr': self.nodebox.value})

    # ----------

    def test_fold_guard_no_exception(self):
        ops = """
        [i]
        guard_no_exception()
            fail()
        i1 = int_add(i, 3)
        guard_no_exception()
            fail()
        i2 = call(i1)
        guard_no_exception()
            fail(i1, i2)
        guard_no_exception()
            fail()
        i3 = call(i2)
        jump(i1)       # the exception is considered lost when we loop back
        """
        expected = """
        [i]
        i1 = int_add(i, 3)
        i2 = call(i1)
        guard_no_exception()
            fail(i1, i2)
        i3 = call(i2)
        jump(i1)
        """
        self.optimize_loop(ops, 'Not', expected)

    # ----------

    def test_virtual_1(self):
        ops = """
        [i, p0]
        i0 = getfield_gc(p0, descr=valuedescr)
        i1 = int_add(i0, i)
        setfield_gc(p0, i1, descr=valuedescr)
        jump(i, p0)
        """
        expected = """
        [i, i2]
        i1 = int_add(i2, i)
        jump(i, i1)
        """
        self.optimize_loop(ops, 'Not, Virtual(node_vtable, valuedescr=Not)',
                           expected)

    def test_virtual_2(self):
        ops = """
        [i, p0]
        i0 = getfield_gc(p0, descr=valuedescr)
        i1 = int_add(i0, i)
        p1 = new_with_vtable(ConstClass(node_vtable))
        setfield_gc(p1, i1, descr=valuedescr)
        jump(i, p1)
        """
        expected = """
        [i, i2]
        i1 = int_add(i2, i)
        jump(i, i1)
        """
        self.optimize_loop(ops, 'Not, Virtual(node_vtable, valuedescr=Not)',
                           expected)

    def test_virtual_oois(self):
        ops = """
        [p0, p1, p2]
        i1 = oononnull(p0)
        guard_true(i1)
          fail()
        i2 = ooisnull(p0)
        guard_false(i2)
          fail()
        i3 = ooisnot(p0, NULL)
        guard_true(i3)
          fail()
        i4 = oois(p0, NULL)
        guard_false(i4)
          fail()
        i5 = ooisnot(NULL, p0)
        guard_true(i5)
          fail()
        i6 = oois(NULL, p0)
        guard_false(i6)
          fail()
        i7 = ooisnot(p0, p1)
        guard_true(i7)
          fail()
        i8 = oois(p0, p1)
        guard_false(i8)
          fail()
        i9 = ooisnot(p0, p2)
        guard_true(i9)
          fail()
        i10 = oois(p0, p2)
        guard_false(i10)
          fail()
        i11 = ooisnot(p2, p1)
        guard_true(i11)
          fail()
        i12 = oois(p2, p1)
        guard_false(i12)
          fail()
        jump(p0, p1, p2)
        """
        expected = """
        [p2]
        # all constant-folded :-)
        jump(p2)
        """
        self.optimize_loop(ops, '''Virtual(node_vtable),
                                   Virtual(node_vtable),
                                   Not''',
                           expected,
                           i1=1, i2=0, i3=1, i4=0, i5=1, i6=0,
                           i7=1, i8=0, i9=1, i10=0, i11=1, i12=0)
        #
        # to be complete, we also check the no-opt case where most comparisons
        # are not removed.  The exact set of comparisons removed depends on
        # the details of the algorithm...
        expected2 = """
        [p0, p1, p2]
        i1 = oononnull(p0)
        guard_true(i1)
          fail()
        i2 = ooisnull(p0)
        guard_false(i2)
          fail()
        i3 = oononnull(p0)
        guard_true(i3)
          fail()
        i4 = ooisnull(p0)
        guard_false(i4)
          fail()
        i5 = oononnull(p0)
        guard_true(i5)
          fail()
        i6 = ooisnull(p0)
        guard_false(i6)
          fail()
        i7 = ooisnot(p0, p1)
        guard_true(i7)
          fail()
        i8 = oois(p0, p1)
        guard_false(i8)
          fail()
        i9 = ooisnot(p0, p2)
        guard_true(i9)
          fail()
        i10 = oois(p0, p2)
        guard_false(i10)
          fail()
        i11 = ooisnot(p2, p1)
        guard_true(i11)
          fail()
        i12 = oois(p2, p1)
        guard_false(i12)
          fail()
        jump(p0, p1, p2)
        """
        self.optimize_loop(ops, 'Not, Not, Not', expected2)

    def test_virtual_default_field(self):
        ops = """
        [p0]
        i0 = getfield_gc(p0, descr=valuedescr)
        guard_value(i0, 0)
          fail()
        p1 = new_with_vtable(ConstClass(node_vtable))
        # the field 'value' has its default value of 0
        jump(p1)
        """
        expected = """
        [i]
        guard_value(i, 0)
          fail()
        jump(0)
        """
        # the 'expected' is sub-optimal, but it should be done by another later
        # optimization step.  See test_find_nodes_default_field() for why.
        self.optimize_loop(ops, 'Virtual(node_vtable, valuedescr=Not)',
                           expected, i0=0)

    def test_virtual_3(self):
        ops = """
        [i]
        p1 = new_with_vtable(ConstClass(node_vtable))
        setfield_gc(p1, i, descr=valuedescr)
        i0 = getfield_gc(p1, descr=valuedescr)
        i1 = int_add(i0, 1)
        jump(i1)
        """
        expected = """
        [i]
        i1 = int_add(i, 1)
        jump(i1)
        """
        self.optimize_loop(ops, 'Not', expected)

    def test_virtual_4(self):
        ops = """
        [i0, p0]
        guard_class(p0, ConstClass(node_vtable))
          fail()
        i1 = getfield_gc(p0, descr=valuedescr)
        i2 = int_sub(i1, 1)
        i3 = int_add(i0, i1)
        p1 = new_with_vtable(ConstClass(node_vtable))
        setfield_gc(p1, i2, descr=valuedescr)
        jump(i3, p1)
        """
        expected = """
        [i0, i1]
        i2 = int_sub(i1, 1)
        i3 = int_add(i0, i1)
        jump(i3, i2)
        """
        self.optimize_loop(ops, 'Not, Virtual(node_vtable, valuedescr=Not)',
                           expected)

    def test_virtual_5(self):
        ops = """
        [i0, p0]
        guard_class(p0, ConstClass(node_vtable))
          fail()
        i1 = getfield_gc(p0, descr=valuedescr)
        i2 = int_sub(i1, 1)
        i3 = int_add(i0, i1)
        p2 = new_with_vtable(ConstClass(node_vtable2))
        setfield_gc(p2, i1, descr=valuedescr)
        p1 = new_with_vtable(ConstClass(node_vtable))
        setfield_gc(p1, i2, descr=valuedescr)
        setfield_gc(p1, p2, descr=nextdescr)
        jump(i3, p1)
        """
        expected = """
        [i0, i1, i1bis]
        i2 = int_sub(i1, 1)
        i3 = int_add(i0, i1)
        jump(i3, i2, i1)
        """
        self.optimize_loop(ops,
            '''Not, Virtual(node_vtable,
                            valuedescr=Not,
                            nextdescr=Virtual(node_vtable2,
                                              valuedescr=Not))''',
                           expected)

    def test_nonvirtual_1(self):
        ops = """
        [i]
        p1 = new_with_vtable(ConstClass(node_vtable))
        setfield_gc(p1, i, descr=valuedescr)
        i0 = getfield_gc(p1, descr=valuedescr)
        i1 = int_add(i0, 1)
        escape(p1)
        escape(p1)
        jump(i1)
        """
        expected = """
        [i]
        i1 = int_add(i, 1)
        p1 = new_with_vtable(ConstClass(node_vtable))
        setfield_gc(p1, i, descr=valuedescr)
        escape(p1)
        escape(p1)
        jump(i1)
        """
        self.optimize_loop(ops, 'Not', expected)

    def test_nonvirtual_2(self):
        ops = """
        [i, p0]
        i0 = getfield_gc(p0, descr=valuedescr)
        escape(p0)
        i1 = int_add(i0, i)
        p1 = new_with_vtable(ConstClass(node_vtable))
        setfield_gc(p1, i1, descr=valuedescr)
        jump(i, p1)
        """
        expected = ops
        self.optimize_loop(ops, 'Not, Not', expected)

    def test_nonvirtual_later(self):
        ops = """
        [i]
        p1 = new_with_vtable(ConstClass(node_vtable))
        setfield_gc(p1, i, descr=valuedescr)
        i1 = getfield_gc(p1, descr=valuedescr)
        escape(p1)
        i2 = getfield_gc(p1, descr=valuedescr)
        i3 = int_add(i1, i2)
        jump(i3)
        """
        expected = """
        [i]
        p1 = new_with_vtable(ConstClass(node_vtable))
        setfield_gc(p1, i, descr=valuedescr)
        escape(p1)
        i2 = getfield_gc(p1, descr=valuedescr)
        i3 = int_add(i, i2)
        jump(i3)
        """
        self.optimize_loop(ops, 'Not', expected)

    def test_getfield_gc_pure_1(self):
        ops = """
        [i]
        p1 = new_with_vtable(ConstClass(node_vtable))
        setfield_gc(p1, i, descr=valuedescr)
        i1 = getfield_gc_pure(p1, descr=valuedescr)
        jump(i1)
        """
        expected = """
        [i]
        jump(i)
        """
        self.optimize_loop(ops, 'Not', expected)

    def test_getfield_gc_pure_2(self):
        ops = """
        [i]
        i1 = getfield_gc_pure(ConstPtr(myptr), descr=valuedescr)
        jump(i1)
        """
        expected = """
        [i]
        jump(5)
        """
        self.optimize_loop(ops, 'Not', expected, i1=5,
                           boxkinds={'myptr': self.nodebox.value})

    def test_getfield_gc_nonpure_2(self):
        ops = """
        [i]
        i1 = getfield_gc(ConstPtr(myptr), descr=valuedescr)
        jump(i1)
        """
        expected = ops
        self.optimize_loop(ops, 'Not', expected, i1=5,
                           boxkinds={'myptr': self.nodebox.value})

    # ----------

    def make_fail_descr(self):
        class FailDescr(compile.ResumeGuardDescr):
            args_seen = []
            def _oparser_uses_descr(self, oparse, args):
                # typically called twice, before and after optimization
                if len(self.args_seen) == 0:
                    builder = resume.ResumeDataBuilder()
                    builder.generate_boxes(args)
                    liveboxes = builder.finish(fdescr)
                    assert liveboxes == args
                self.args_seen.append((args, oparse))
        #
        fdescr = instantiate(FailDescr)
        self.fdescr = fdescr
        self.namespace['fdescr'] = fdescr

    def _verify_fail_args(self, boxes, oparse, text):
        import re
        r = re.compile(r"\bwhere\s+(\w+)\s+is a\s+(\w+)")
        parts = list(r.finditer(text))
        ends = [match.start() for match in parts] + [len(text)]
        #
        virtuals = {}
        for match, end in zip(parts, ends[1:]):
            pvar = match.group(1)
            cls_vtable = self.namespace[match.group(2)]
            fieldstext = text[match.end():end]
            virtuals[pvar] = (cls_vtable, None, fieldstext)
        #
        def _variables_equal(box, varname, strict):
            if varname not in virtuals:
                if strict:
                    assert box == oparse.getvar(varname)
                else:
                    assert box.value == oparse.getvar(varname).value
            else:
                cls_vtable, resolved, fieldstext = virtuals[varname]
                if not self.cpu.is_oo:
                    assert box.getptr(rclass.OBJECTPTR).typeptr == cls_vtable
                else:
                    root = ootype.cast_from_object(ootype.ROOT, box.getobj())
                    assert ootype.classof(root) == cls_vtable
                if resolved is not None:
                    assert resolved.value == box.value
                else:
                    virtuals[varname] = cls_vtable, box, fieldstext
        #
        basetext = text[:ends[0]]
        varnames = [s.strip() for s in basetext.split(',')]
        assert len(boxes) == len(varnames)
        for box, varname in zip(boxes, varnames):
            _variables_equal(box, varname, strict=True)
        #
        for match in parts:
            pvar = match.group(1)
            cls_vtable, resolved, fieldstext = virtuals[pvar]
            assert resolved is not None
            for fieldtext in fieldstext.split(','):
                fieldtext = fieldtext.strip()
                if not fieldtext:
                    continue
                fieldname, fieldvalue = fieldtext.split('=')
                fielddescr = self.namespace[fieldname.strip()]
                fieldbox = executor.execute(self.cpu,
                                            rop.GETFIELD_GC,
                                            [resolved],
                                            descr=fielddescr)
                _variables_equal(fieldbox, fieldvalue.strip(), strict=False)

    def check_expanded_fail_descr(self, expectedtext):
        fdescr = self.fdescr
        args, oparse = fdescr.args_seen[-1]
        reader = resume.ResumeDataReader(fdescr, args, MyMetaInterp(self.cpu))
        boxes = reader.consume_boxes()
        self._verify_fail_args(boxes, oparse, expectedtext)

    def test_expand_fail_1(self):
        self.make_fail_descr()
        ops = """
        [i1, i3]
        # first rename i3 into i4
        p1 = new_with_vtable(ConstClass(node_vtable))
        setfield_gc(p1, i3, descr=valuedescr)
        i4 = getfield_gc(p1, descr=valuedescr)
        #
        i2 = int_add(10, 5)
        guard_true(i1)
            fail(i2, i4, descr=fdescr)
        jump(i1, i4)
        """
        expected = """
        [i1, i3]
        guard_true(i1)
            fail(i3, descr=fdescr)
        jump(1, i3)
        """
        self.optimize_loop(ops, 'Not, Not', expected, i1=1, i2=15)
        self.check_expanded_fail_descr('15, i3')

    def test_expand_fail_2(self):
        self.make_fail_descr()
        ops = """
        [i1, i2]
        p1 = new_with_vtable(ConstClass(node_vtable))
        setfield_gc(p1, i2, descr=valuedescr)
        setfield_gc(p1, p1, descr=nextdescr)
        guard_true(i1)
            fail(p1, descr=fdescr)
        jump(i1, i2)
        """
        expected = """
        [i1, i2]
        guard_true(i1)
            fail(i2, descr=fdescr)
        jump(1, i2)
        """
        self.optimize_loop(ops, 'Not, Not', expected, i1=1)
        self.check_expanded_fail_descr('''ptr
            where ptr is a node_vtable, valuedescr=i2
            ''')

    def test_expand_fail_3(self):
        self.make_fail_descr()
        ops = """
        [i1, i2, i3, p3]
        p1 = new_with_vtable(ConstClass(node_vtable))
        p2 = new_with_vtable(ConstClass(node_vtable))
        setfield_gc(p1, 1, descr=valuedescr)
        setfield_gc(p1, p2, descr=nextdescr)
        setfield_gc(p2, i2, descr=valuedescr)
        setfield_gc(p2, p3, descr=nextdescr)
        guard_true(i1)
            fail(p1, i3, descr=fdescr)
        jump(i2, i1, i3, p3)
        """
        expected = """
        [i1, i2, i3, p3]
        guard_true(i1)
            fail(i3, i2, p3, descr=fdescr)
        jump(i2, 1, i3, p3)
        """
        self.optimize_loop(ops, 'Not, Not, Not, Not', expected, i1=1)
        self.check_expanded_fail_descr('''p1, i3
            where p1 is a node_vtable, valuedescr=1, nextdescr=p2
            where p2 is a node_vtable, valuedescr=i2, nextdescr=p3
            ''')

    def test_expand_fail_4(self):
        for arg in ['p1', 'p1,i2', 'i2,p1', 'p1,p2', 'p2,p1',
                    'p1,p2,i2', 'p1,i2,p2', 'p2,p1,i2',
                    'p2,i2,p1', 'i2,p1,p2', 'i2,p2,p1']:
            self.make_fail_descr()
            ops = """
            [i1, i2, i3]
            p1 = new_with_vtable(ConstClass(node_vtable))
            setfield_gc(p1, i3, descr=valuedescr)
            i4 = getfield_gc(p1, descr=valuedescr)   # copy of i3
            p2 = new_with_vtable(ConstClass(node_vtable))
            setfield_gc(p1, i2, descr=valuedescr)
            setfield_gc(p1, p2, descr=nextdescr)
            setfield_gc(p2, i2, descr=valuedescr)
            guard_true(i1)
                fail(i4, %s, i3, descr=fdescr)
            jump(i1, i2, i3)
            """
            expected = """
            [i1, i2, i3]
            guard_true(i1)
                fail(i3, i2, descr=fdescr)
            jump(1, i2, i3)
            """
            self.optimize_loop(ops % arg, 'Not, Not, Not', expected, i1=1)
            self.check_expanded_fail_descr('''i3, %s, i3
                where p1 is a node_vtable, valuedescr=i2, nextdescr=p2
                where p2 is a node_vtable, valuedescr=i2''' % arg)


    def test_expand_fail_5(self):
        self.make_fail_descr()
        ops = """
        [i1, i2, i3, i4]
        p1 = new_with_vtable(ConstClass(node_vtable))
        p2 = new_with_vtable(ConstClass(node_vtable))
        setfield_gc(p1, i4, descr=valuedescr)
        setfield_gc(p1, p2, descr=nextdescr)
        setfield_gc(p2, i2, descr=valuedescr)
        setfield_gc(p2, p1, descr=nextdescr)      # a cycle
        guard_true(i1)
            fail(p1, i3, p2, i4, descr=fdescr)
        jump(i2, i1, i3, i4)
        """
        expected = """
        [i1, i2, i3, i4]
        guard_true(i1)
            fail(i3, i4, i2, descr=fdescr)
        jump(i2, 1, i3, i4)
        """
        self.optimize_loop(ops, 'Not, Not, Not, Not', expected, i1=1)
        self.check_expanded_fail_descr('''p1, i3, p2, i4
            where p1 is a node_vtable, valuedescr=i4, nextdescr=p2
            where p2 is a node_vtable, valuedescr=i2, nextdescr=p1
            ''')

    def test_expand_fail_6(self):
        self.make_fail_descr()
        ops = """
        [p0, i0, i1]
        guard_true(i0)
            fail(p0, descr=fdescr)
        p1 = new_with_vtable(ConstClass(node_vtable))
        setfield_gc(p1, i1, descr=valuedescr)
        jump(p1, i1, i1)
        """
        expected = """
        [i1b, i0, i1]
        guard_true(i0)
            fail(i1b, descr=fdescr)
        jump(i1, i1, i1)
        """
        self.optimize_loop(ops, '''Virtual(node_vtable, valuedescr=Not),
                                   Not, Not''', expected, i0=1)
        self.check_expanded_fail_descr('''p0
            where p0 is a node_vtable, valuedescr=i1b
            ''')


class TestLLtype(BaseTestOptimizeOpt, LLtypeMixin):
    pass

class TestOOtype(BaseTestOptimizeOpt, OOtypeMixin):
    pass
