from pypy.jit.metainterp.specnode import SpecNode, NotSpecNode, ConstantSpecNode
from pypy.jit.metainterp.specnode import AbstractVirtualStructSpecNode
from pypy.jit.metainterp.specnode import VirtualInstanceSpecNode
from pypy.jit.metainterp.specnode import VirtualArraySpecNode
from pypy.jit.metainterp.specnode import VirtualStructSpecNode
from pypy.jit.metainterp.resoperation import rop, ResOperation
from pypy.jit.metainterp.optimizeutil import _findall
from pypy.rlib.objectmodel import we_are_translated
from optimizer import *


class AbstractVirtualValue(OptValue):
    _attrs_ = ('optimizer', 'keybox', 'source_op', '_cached_vinfo')
    box = None
    level = LEVEL_NONNULL
    _cached_vinfo = None

    def __init__(self, optimizer, keybox, source_op=None):
        self.optimizer = optimizer
        self.keybox = keybox   # only used as a key in dictionaries
        self.source_op = source_op  # the NEW_WITH_VTABLE/NEW_ARRAY operation
                                    # that builds this box

    def get_key_box(self):
        if self.box is None:
            return self.keybox
        return self.box

    def force_box(self):
        if self.box is None:
            self.optimizer.forget_numberings(self.keybox)
            self._really_force()
        return self.box

    def make_virtual_info(self, modifier, fieldnums):
        vinfo = self._cached_vinfo
        if vinfo is not None and vinfo.equals(fieldnums):
            return vinfo
        vinfo = self._make_virtual(modifier)
        vinfo.set_content(fieldnums)
        self._cached_vinfo = vinfo
        return vinfo

    def _make_virtual(self, modifier):
        raise NotImplementedError("abstract base")

    def _really_force(self):
        raise NotImplementedError("abstract base")

def get_fielddescrlist_cache(cpu):
    if not hasattr(cpu, '_optimizeopt_fielddescrlist_cache'):
        result = descrlist_dict()
        cpu._optimizeopt_fielddescrlist_cache = result
        return result
    return cpu._optimizeopt_fielddescrlist_cache
get_fielddescrlist_cache._annspecialcase_ = "specialize:memo"

class AbstractVirtualStructValue(AbstractVirtualValue):
    _attrs_ = ('_fields', '_cached_sorted_fields')

    def __init__(self, optimizer, keybox, source_op=None):
        AbstractVirtualValue.__init__(self, optimizer, keybox, source_op)
        self._fields = {}
        self._cached_sorted_fields = None

    def getfield(self, ofs, default):
        return self._fields.get(ofs, default)

    def setfield(self, ofs, fieldvalue):
        assert isinstance(fieldvalue, OptValue)
        self._fields[ofs] = fieldvalue

    def _really_force(self):
        assert self.source_op is not None
        # ^^^ This case should not occur any more (see test_bug_3).
        #
        newoperations = self.optimizer.newoperations
        newoperations.append(self.source_op)
        self.box = box = self.source_op.result
        #
        iteritems = self._fields.iteritems()
        if not we_are_translated(): #random order is fine, except for tests
            iteritems = list(iteritems)
            iteritems.sort(key = lambda (x,y): x.sort_key())
        for ofs, value in iteritems:
            if value.is_null():
                continue
            subbox = value.force_box()
            op = ResOperation(rop.SETFIELD_GC, [box, subbox], None,
                              descr=ofs)
            newoperations.append(op)
        self._fields = None

    def _get_field_descr_list(self):
        _cached_sorted_fields = self._cached_sorted_fields
        if (_cached_sorted_fields is not None and
            len(self._fields) == len(_cached_sorted_fields)):
            lst = self._cached_sorted_fields
        else:
            lst = self._fields.keys()
            sort_descrs(lst)
            cache = get_fielddescrlist_cache(self.optimizer.cpu)
            result = cache.get(lst, None)
            if result is None:
                cache[lst] = lst
            else:
                lst = result
            # store on self, to not have to repeatedly get it from the global
            # cache, which involves sorting
            self._cached_sorted_fields = lst
        return lst

    def get_args_for_fail(self, modifier):
        if self.box is None and not modifier.already_seen_virtual(self.keybox):
            # checks for recursion: it is False unless
            # we have already seen the very same keybox
            lst = self._get_field_descr_list()
            fieldboxes = [self._fields[ofs].get_key_box() for ofs in lst]
            modifier.register_virtual_fields(self.keybox, fieldboxes)
            for ofs in lst:
                fieldvalue = self._fields[ofs]
                fieldvalue.get_args_for_fail(modifier)


class VirtualValue(AbstractVirtualStructValue):
    level = LEVEL_KNOWNCLASS

    def __init__(self, optimizer, known_class, keybox, source_op=None):
        AbstractVirtualStructValue.__init__(self, optimizer, keybox, source_op)
        assert isinstance(known_class, Const)
        self.known_class = known_class

    def _make_virtual(self, modifier):
        fielddescrs = self._get_field_descr_list()
        return modifier.make_virtual(self.known_class, fielddescrs)

class VStructValue(AbstractVirtualStructValue):

    def __init__(self, optimizer, structdescr, keybox, source_op=None):
        AbstractVirtualStructValue.__init__(self, optimizer, keybox, source_op)
        self.structdescr = structdescr

    def _make_virtual(self, modifier):
        fielddescrs = self._get_field_descr_list()
        return modifier.make_vstruct(self.structdescr, fielddescrs)

class VArrayValue(AbstractVirtualValue):

    def __init__(self, optimizer, arraydescr, size, keybox, source_op=None):
        AbstractVirtualValue.__init__(self, optimizer, keybox, source_op)
        self.arraydescr = arraydescr
        self.constvalue = optimizer.new_const_item(arraydescr)
        self._items = [self.constvalue] * size

    def getlength(self):
        return len(self._items)

    def getitem(self, index):
        res = self._items[index]
        return res

    def setitem(self, index, itemvalue):
        assert isinstance(itemvalue, OptValue)
        self._items[index] = itemvalue

    def _really_force(self):
        assert self.source_op is not None
        newoperations = self.optimizer.newoperations
        newoperations.append(self.source_op)
        self.box = box = self.source_op.result
        for index in range(len(self._items)):
            subvalue = self._items[index]
            if subvalue is not self.constvalue:
                if subvalue.is_null():
                    continue
                subbox = subvalue.force_box()
                op = ResOperation(rop.SETARRAYITEM_GC,
                                  [box, ConstInt(index), subbox], None,
                                  descr=self.arraydescr)
                newoperations.append(op)

    def get_args_for_fail(self, modifier):
        if self.box is None and not modifier.already_seen_virtual(self.keybox):
            # checks for recursion: it is False unless
            # we have already seen the very same keybox
            itemboxes = []
            for itemvalue in self._items:
                itemboxes.append(itemvalue.get_key_box())
            modifier.register_virtual_fields(self.keybox, itemboxes)
            for itemvalue in self._items:
                if itemvalue is not self.constvalue:
                    itemvalue.get_args_for_fail(modifier)

    def _make_virtual(self, modifier):
        return modifier.make_varray(self.arraydescr)

class __extend__(SpecNode):
    def setup_virtual_node(self, optimizer, box, newinputargs):
        raise NotImplementedError
    def teardown_virtual_node(self, optimizer, value, newexitargs):
        raise NotImplementedError

class __extend__(NotSpecNode):
    def setup_virtual_node(self, optimizer, box, newinputargs):
        newinputargs.append(box)
    def teardown_virtual_node(self, optimizer, value, newexitargs):
        newexitargs.append(value.force_box())

class __extend__(ConstantSpecNode):
    def setup_virtual_node(self, optimizer, box, newinputargs):
        optimizer.make_constant(box, self.constbox)
    def teardown_virtual_node(self, optimizer, value, newexitargs):
        pass

class __extend__(AbstractVirtualStructSpecNode):
    def setup_virtual_node(self, optimizer, box, newinputargs):
        vvalue = self._setup_virtual_node_1(optimizer, box)
        for ofs, subspecnode in self.fields:
            subbox = optimizer.new_box(ofs)
            subspecnode.setup_virtual_node(optimizer, subbox, newinputargs)
            vvaluefield = optimizer.getvalue(subbox)
            vvalue.setfield(ofs, vvaluefield)
    def _setup_virtual_node_1(self, optimizer, box):
        raise NotImplementedError
    def teardown_virtual_node(self, optimizer, value, newexitargs):
        assert value.is_virtual()
        for ofs, subspecnode in self.fields:
            subvalue = value.getfield(ofs, optimizer.new_const(ofs))
            subspecnode.teardown_virtual_node(optimizer, subvalue, newexitargs)

class __extend__(VirtualInstanceSpecNode):
    def _setup_virtual_node_1(self, optimizer, box):
        return optimizer.make_virtual(self.known_class, box)

class __extend__(VirtualStructSpecNode):
    def _setup_virtual_node_1(self, optimizer, box):
        return optimizer.make_vstruct(self.typedescr, box)

class __extend__(VirtualArraySpecNode):
    def setup_virtual_node(self, optimizer, box, newinputargs):
        vvalue = optimizer.make_varray(self.arraydescr, len(self.items), box)
        for index in range(len(self.items)):
            subbox = optimizer.new_box_item(self.arraydescr)
            subspecnode = self.items[index]
            subspecnode.setup_virtual_node(optimizer, subbox, newinputargs)
            vvalueitem = optimizer.getvalue(subbox)
            vvalue.setitem(index, vvalueitem)
    def teardown_virtual_node(self, optimizer, value, newexitargs):
        assert value.is_virtual()
        for index in range(len(self.items)):
            subvalue = value.getitem(index)
            subspecnode = self.items[index]
            subspecnode.teardown_virtual_node(optimizer, subvalue, newexitargs)

class OptVirtualize(Optimization):
    "Virtualize objects until they escape."

    def setup(self, virtuals):
        if not virtuals:
            return
        
        inputargs = self.optimizer.loop.inputargs
        specnodes = self.optimizer.loop.token.specnodes
        assert len(inputargs) == len(specnodes)
        newinputargs = []
        for i in range(len(inputargs)):
            specnodes[i].setup_virtual_node(self, inputargs[i], newinputargs)
        self.optimizer.loop.inputargs = newinputargs

    def make_virtual(self, known_class, box, source_op=None):
        vvalue = VirtualValue(self.optimizer, known_class, box, source_op)
        self.make_equal_to(box, vvalue)
        return vvalue

    def make_varray(self, arraydescr, size, box, source_op=None):
        vvalue = VArrayValue(self.optimizer, arraydescr, size, box, source_op)
        self.make_equal_to(box, vvalue)
        return vvalue

    def make_vstruct(self, structdescr, box, source_op=None):
        vvalue = VStructValue(self.optimizer, structdescr, box, source_op)
        self.make_equal_to(box, vvalue)
        return vvalue

    def optimize_JUMP(self, op):
        orgop = self.optimizer.loop.operations[-1]
        exitargs = []
        target_loop_token = orgop.descr
        assert isinstance(target_loop_token, LoopToken)
        specnodes = target_loop_token.specnodes
        assert len(op.args) == len(specnodes)
        for i in range(len(specnodes)):
            value = self.getvalue(op.args[i])
            specnodes[i].teardown_virtual_node(self, value, exitargs)
        op.args = exitargs[:]
        self.emit_operation(op)

    def optimize_VIRTUAL_REF(self, op):
        indexbox = op.args[1]
        #
        # get some constants
        vrefinfo = self.optimizer.metainterp_sd.virtualref_info
        c_cls = vrefinfo.jit_virtual_ref_const_class
        descr_virtual_token = vrefinfo.descr_virtual_token
        descr_virtualref_index = vrefinfo.descr_virtualref_index
        #
        # Replace the VIRTUAL_REF operation with a virtual structure of type
        # 'jit_virtual_ref'.  The jit_virtual_ref structure may be forced soon,
        # but the point is that doing so does not force the original structure.
        op = ResOperation(rop.NEW_WITH_VTABLE, [c_cls], op.result)
        vrefvalue = self.make_virtual(c_cls, op.result, op)
        tokenbox = BoxInt()
        self.emit_operation(ResOperation(rop.FORCE_TOKEN, [], tokenbox))
        vrefvalue.setfield(descr_virtual_token, self.getvalue(tokenbox))
        vrefvalue.setfield(descr_virtualref_index, self.getvalue(indexbox))

    def optimize_VIRTUAL_REF_FINISH(self, op):
        # Set the 'forced' field of the virtual_ref.
        # In good cases, this is all virtual, so has no effect.
        # Otherwise, this forces the real object -- but only now, as
        # opposed to much earlier.  This is important because the object is
        # typically a PyPy PyFrame, and now is the end of its execution, so
        # forcing it now does not have catastrophic effects.
        vrefinfo = self.optimizer.metainterp_sd.virtualref_info
        # op.args[1] should really never point to null here
        # - set 'forced' to point to the real object
        op1 = ResOperation(rop.SETFIELD_GC, op.args, None,
                          descr = vrefinfo.descr_forced)
        self.optimize_SETFIELD_GC(op1)
        # - set 'virtual_token' to TOKEN_NONE
        args = [op.args[0], ConstInt(vrefinfo.TOKEN_NONE)]
        op1 = ResOperation(rop.SETFIELD_GC, args, None,
                      descr = vrefinfo.descr_virtual_token)
        self.optimize_SETFIELD_GC(op1)
        # Note that in some cases the virtual in op.args[1] has been forced
        # already.  This is fine.  In that case, and *if* a residual
        # CALL_MAY_FORCE suddenly turns out to access it, then it will
        # trigger a ResumeGuardForcedDescr.handle_async_forcing() which
        # will work too (but just be a little pointless, as the structure
        # was already forced).

    def optimize_GETFIELD_GC(self, op):
        value = self.getvalue(op.args[0])
        if value.is_virtual():
            # optimizefindnode should ensure that fieldvalue is found
            assert isinstance(value, AbstractVirtualValue)
            fieldvalue = value.getfield(op.descr, None)
            assert fieldvalue is not None
            self.make_equal_to(op.result, fieldvalue)
        else:
            value.ensure_nonnull()
            ###self.heap_op_optimizer.optimize_GETFIELD_GC(op, value)
            self.emit_operation(op)

    # note: the following line does not mean that the two operations are
    # completely equivalent, because GETFIELD_GC_PURE is_always_pure().
    optimize_GETFIELD_GC_PURE = optimize_GETFIELD_GC

    def optimize_SETFIELD_GC(self, op):
        value = self.getvalue(op.args[0])
        fieldvalue = self.getvalue(op.args[1])
        if value.is_virtual():
            value.setfield(op.descr, fieldvalue)
        else:
            value.ensure_nonnull()
            ###self.heap_op_optimizer.optimize_SETFIELD_GC(op, value, fieldvalue)
            self.emit_operation(op)

    def optimize_NEW_WITH_VTABLE(self, op):
        self.make_virtual(op.args[0], op.result, op)

    def optimize_NEW(self, op):
        self.make_vstruct(op.descr, op.result, op)

    def optimize_NEW_ARRAY(self, op):
        sizebox = self.get_constant_box(op.args[0])
        if sizebox is not None:
            # if the original 'op' did not have a ConstInt as argument,
            # build a new one with the ConstInt argument
            if not isinstance(op.args[0], ConstInt):
                op = ResOperation(rop.NEW_ARRAY, [sizebox], op.result,
                                  descr=op.descr)
            self.make_varray(op.descr, sizebox.getint(), op.result, op)
        else:
            ###self.optimize_default(op)
            self.emit_operation(op)

    def optimize_ARRAYLEN_GC(self, op):
        value = self.getvalue(op.args[0])
        if value.is_virtual():
            self.make_constant_int(op.result, value.getlength())
        else:
            value.ensure_nonnull()
            ###self.optimize_default(op)
            self.emit_operation(op)

    def optimize_GETARRAYITEM_GC(self, op):
        value = self.getvalue(op.args[0])
        if value.is_virtual():
            indexbox = self.get_constant_box(op.args[1])
            if indexbox is not None:
                itemvalue = value.getitem(indexbox.getint())
                self.make_equal_to(op.result, itemvalue)
                return
        value.ensure_nonnull()
        ###self.heap_op_optimizer.optimize_GETARRAYITEM_GC(op, value)
        self.emit_operation(op)

    # note: the following line does not mean that the two operations are
    # completely equivalent, because GETARRAYITEM_GC_PURE is_always_pure().
    optimize_GETARRAYITEM_GC_PURE = optimize_GETARRAYITEM_GC

    def optimize_SETARRAYITEM_GC(self, op):
        value = self.getvalue(op.args[0])
        if value.is_virtual():
            indexbox = self.get_constant_box(op.args[1])
            if indexbox is not None:
                value.setitem(indexbox.getint(), self.getvalue(op.args[2]))
                return
        value.ensure_nonnull()
        ###self.heap_op_optimizer.optimize_SETARRAYITEM_GC(op, value, fieldvalue)
        self.emit_operation(op)

    def optimize_ARRAYCOPY(self, op):
        source_value = self.getvalue(op.args[2])
        dest_value = self.getvalue(op.args[3])
        source_start_box = self.get_constant_box(op.args[4])
        dest_start_box = self.get_constant_box(op.args[5])
        length = self.get_constant_box(op.args[6])
        if (source_value.is_virtual() and source_start_box and dest_start_box
            and length and dest_value.is_virtual()):
            # XXX optimize the case where dest value is not virtual,
            #     but we still can avoid a mess
            source_start = source_start_box.getint()
            dest_start = dest_start_box.getint()
            for index in range(length.getint()):
                val = source_value.getitem(index + source_start)
                dest_value.setitem(index + dest_start, val)
            return
        if length and length.getint() == 0:
            return # 0-length arraycopy
        descr = op.args[0]
        assert isinstance(descr, AbstractDescr)
        self.emit_operation(ResOperation(rop.CALL, op.args[1:], op.result,
                                         descr))

    def propagate_forward(self, op):
        opnum = op.opnum
        for value, func in optimize_ops:
            if opnum == value:
                func(self, op)
                break
        else:
            self.emit_operation(op)

optimize_ops = _findall(OptVirtualize, 'optimize_')
