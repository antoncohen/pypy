from __future__ import generators

from types import FunctionType, ClassType
from pypy.tool.ansi_print import ansi_log 
from pypy.annotation import model as annmodel
from pypy.annotation.model import pair
from pypy.annotation.bookkeeper import Bookkeeper
from pypy.objspace.flow.model import Variable, Constant
from pypy.objspace.flow.model import SpaceOperation, FunctionGraph
from pypy.objspace.flow.model import last_exception, checkgraph
import py
log = py.log.Producer("annrpython") 
py.log.setconsumer("annrpython", ansi_log) 

class AnnotatorError(Exception):
    pass


class RPythonAnnotator:
    """Block annotator for RPython.
    See description in doc/translation/annotation.txt."""

    def __init__(self, translator=None, policy = None):
        self.translator = translator
        self.pendingblocks = {}  # map {block: function}
        self.bindings = {}       # map Variables to SomeValues
        self.annotated = {}      # set of blocks already seen
        self.added_blocks = None # see processblock() below
        self.links_followed = {} # set of links that have ever been followed
        self.notify = {}        # {block: {positions-to-reflow-from-when-done}}
        # --- the following information is recorded for debugging only ---
        # --- and only if annotation.model.DEBUG is kept to True
        self.why_not_annotated = {} # {block: (exc_type, exc_value, traceback)}
                                    # records the location of BlockedInference
                                    # exceptions that blocked some blocks.
        self.blocked_functions = {} # set of functions that have blocked blocks
        self.bindingshistory = {}# map Variables to lists of SomeValues
        self.binding_caused_by = {}     # map Variables to position_keys
               # records the caller position that caused bindings of inputargs
               # to be updated
        self.binding_cause_history = {} # map Variables to lists of positions
                # history of binding_caused_by, kept in sync with
                # bindingshistory
        self.reflowcounter = {}
        self.return_bindings = {} # map return Variables to functions
        # --- end of debugging information ---
        self.bookkeeper = Bookkeeper(self)
        self.frozen = False
        # user-supplied annotation logic for functions we don't want to flow into
        if policy is None:
            from pypy.annotation.policy import AnnotatorPolicy
            self.policy = AnnotatorPolicy()
        else:
            self.policy = policy

    def __getstate__(self):
        attrs = """translator pendingblocks bindings annotated links_followed
        notify bookkeeper frozen policy""".split()
        ret = self.__dict__.copy()
        for key, value in ret.items():
            if key not in attrs:
                assert type(value) is dict, (
                    "%r is not dict. please update %s.__getstate__" %
                    (key, self.__class__.__name__))
                ret[key] = {}
        return ret

    def _register_returnvar(self, flowgraph, func):
        if annmodel.DEBUG:
            self.return_bindings[flowgraph.getreturnvar()] = func

    #___ convenience high-level interface __________________

    def getflowgraph(self, func, called_by=None, call_tag=None):        
        flowgraph = self.translator.getflowgraph(func, called_by=called_by, call_tag=call_tag)
        self._register_returnvar(flowgraph, func)
        return flowgraph
        

    def build_types(self, func_or_flowgraph, input_arg_types, func=None):
        """Recursively build annotations about the specific entry point."""
        if isinstance(func_or_flowgraph, FunctionGraph):
            flowgraph = func_or_flowgraph
            checkgraph(flowgraph)
            self._register_returnvar(flowgraph, func)
        else:
            func = func_or_flowgraph
            if self.translator is None:
                from pypy.translator.translator import Translator
                self.translator = Translator(func, simplifying=True)
                self.translator.annotator = self
            flowgraph = self.getflowgraph(func)
        # make input arguments and set their type
        input_arg_types = list(input_arg_types)
        nbarg = len(flowgraph.getargs())
        if len(input_arg_types) != nbarg: 
            raise TypeError("flowgraph %s expects %d args, got %d" %(       
                            flowgraph.name, nbarg, len(input_arg_types)))
        inputcells = []
        for t in input_arg_types:
            if not isinstance(t, annmodel.SomeObject):
                t = self.bookkeeper.valueoftype(t)
            inputcells.append(t)
        
        # register the entry point
        self.addpendingblock(func, flowgraph.startblock, inputcells)
        # recursively proceed until no more pending block is left
        self.complete()
        return self.binding(flowgraph.getreturnvar(), extquery=True)

    def gettype(self, variable):
        """Return the known type of a control flow graph variable,
        defaulting to 'object'."""
        if isinstance(variable, Constant):
            return type(variable.value)
        elif isinstance(variable, Variable):
            cell = self.bindings.get(variable)
            if cell:
                return cell.knowntype
            else:
                return object
        else:
            raise TypeError, ("Variable or Constant instance expected, "
                              "got %r" % (variable,))

    def getuserclasses(self):
        """Return a set of known user classes."""
        return self.bookkeeper.userclasses

    def getuserclassdefinitions(self):
        """Return a list of ClassDefs."""
        return self.bookkeeper.userclasseslist

    def getuserattributes(self, cls):
        """Enumerate the attributes of the given user class, as Variable()s."""
        clsdef = self.bookkeeper.userclasses[cls]
        for attr, s_value in clsdef.attrs.items():
            v = Variable(name=attr)
            self.bindings[v] = s_value
            yield v

    def getpbcaccesssets(self):
        """Return mapping const obj -> PBCAccessSet"""
        return self.bookkeeper.pbc_maximal_access_sets

    def getpbccallables(self):
        """Return mapping callable -> {(ClassDef|None, callable): True...},
 
        The tuples are indices in getpbcfamilies returned mapping
        """
        return self.bookkeeper.pbc_callables
    
    def getpbccallfamilies(self):
        """Return mapping (ClassDef|None, callable) -> PBCCallFamily"""
        return self.bookkeeper.pbc_maximal_call_families

    #___ medium-level interface ____________________________

    def addpendingblock(self, fn, block, cells, called_from=None):
        """Register an entry point into block with the given input cells."""
        assert self.translator is None or fn in self.translator.flowgraphs
        assert not self.frozen
        for a in cells:
            assert isinstance(a, annmodel.SomeObject)
        if block not in self.annotated:
            self.bindinputargs(fn, block, cells, called_from)
        else:
            self.mergeinputargs(fn, block, cells, called_from)
        if not self.annotated[block]:
            self.pendingblocks[block] = fn

    def complete(self):
        """Process pending blocks until none is left."""
        while self.pendingblocks:
            block, fn = self.pendingblocks.popitem()
            self.processblock(fn, block)
        if False in self.annotated.values():
            if annmodel.DEBUG:
                for block in self.annotated:
                    if self.annotated[block] is False:
                        fn = self.why_not_annotated[block][1].break_at[0]
                        self.blocked_functions[fn] = True
                        import traceback
                        log.ERROR('-+' * 30)
                        log.ERROR('BLOCKED block at :' +
                                  self.whereami(self.why_not_annotated[block][1].break_at))
                        log.ERROR('because of:')
                        for line in traceback.format_exception(*self.why_not_annotated[block]):
                            log.ERROR(line)
                        log.ERROR('-+' * 30)

            raise AnnotatorError('%d blocks are still blocked' %
                                 self.annotated.values().count(False))
        # make sure that the return variables of all graphs is annotated
        if self.translator is not None:
            if self.added_blocks is not None:
                newgraphs = [self.translator.flowgraphs[self.annotated[block]]
                             for block in self.added_blocks]
                newgraphs = dict.fromkeys(newgraphs)
            else:
                newgraphs = self.translator.flowgraphs.itervalues() #all of them
            for graph in newgraphs:
                v = graph.getreturnvar()
                if v not in self.bindings:
                    self.setbinding(v, annmodel.SomeImpossibleValue())
        # policy-dependent computation
        self.policy.compute_at_fixpoint(self)

    def binding(self, arg, extquery=False):
        "Gives the SomeValue corresponding to the given Variable or Constant."
        if isinstance(arg, Variable):
            try:
                return self.bindings[arg]
            except KeyError:
                if extquery:
                    return None
                else:
                    raise
        elif isinstance(arg, Constant):
            #if arg.value is undefined_value:   # undefined local variables
            #    return annmodel.SomeImpossibleValue()
            assert not arg.value is last_exception
            return self.bookkeeper.immutablevalue(arg.value)
        else:
            raise TypeError, 'Variable or Constant expected, got %r' % (arg,)

    def ondegenerated(self, what, s_value, where=None, called_from=None):
        if self.policy.allow_someobjects:
            return
        msglines = ["annotation of %r degenerated to SomeObject()" % (what,)]
        try:
            position_key = where or self.bookkeeper.position_key
        except AttributeError:
            pass
        else:
            msglines.append(".. position: %s" % (self.whereami(position_key),))
        if called_from is not None:
            msglines.append(".. called from %r" % (called_from,))
            if hasattr(called_from, '__module__'):
                msglines[-1] += " from module %r"% (called_from.__module__,)
        if s_value.origin is not None:
            msglines.append(".. SomeObject() origin: %s" % (
                self.whereami(s_value.origin),))
        raise AnnotatorError('\n'.join(msglines))        

    def setbinding(self, arg, s_value, called_from=None, where=None):
        if arg in self.bindings:
            assert s_value.contains(self.bindings[arg])
            # for debugging purposes, record the history of bindings that
            # have been given to this variable
            if annmodel.DEBUG:
                history = self.bindingshistory.setdefault(arg, [])
                history.append(self.bindings[arg])
                cause_history = self.binding_cause_history.setdefault(arg, [])
                cause_history.append(self.binding_caused_by[arg])

        degenerated = annmodel.isdegenerated(s_value)

        if degenerated:
            self.ondegenerated(arg, s_value, where=where, called_from=called_from)

        self.bindings[arg] = s_value
        if annmodel.DEBUG:
            if arg in self.return_bindings:
                log.event("%s -> %s" % 
                    (self.whereami((self.return_bindings[arg], None, None)), 
                     s_value)) 

            if arg in self.return_bindings and degenerated:
                self.warning("result degenerated to SomeObject",
                             (self.return_bindings[arg],None, None))
                
            self.binding_caused_by[arg] = called_from
        # XXX make this line available as a debugging option
        ##assert not (s_value.__class__ == annmodel.SomeObject and s_value.knowntype == object) ## debug


    def warning(self, msg, pos=None):
        if pos is None:
            try:
                pos = self.bookkeeper.position_key
            except AttributeError:
                pos = '?'
        if pos != '?':
            pos = self.whereami(pos)
 
        log.WARNING("%s/ %s" % (pos, msg))


    #___ interface for annotator.bookkeeper _______

    def recursivecall(self, func, whence, inputcells): # whence = position_key|callback taking the annotator, graph 
        if isinstance(whence, tuple):
            parent_fn, parent_block, parent_index = position_key = whence
        else:
            parent_fn = position_key = None
        graph = self.getflowgraph(func, parent_fn, position_key)
        # self.notify[graph.returnblock] is a dictionary of call
        # points to this func which triggers a reflow whenever the
        # return block of this graph has been analysed.
        callpositions = self.notify.setdefault(graph.returnblock, {})
        if whence is not None:
            if callable(whence):
                def callback():
                    whence(self, graph)
            else:
                callback = whence
            callpositions[callback] = True

        # generalize the function's input arguments
        self.addpendingblock(func, graph.startblock, inputcells, position_key)

        # get the (current) return value
        v = graph.getreturnvar()
        try:
            return self.bindings[v]
        except KeyError: 
            # the function didn't reach any return statement so far.
            # (some functions actually never do, they always raise exceptions)
            return annmodel.SomeImpossibleValue()

    def reflowfromposition(self, position_key):
        fn, block, index = position_key
        self.reflowpendingblock(fn, block)


    #___ simplification (should be moved elsewhere?) _______

    # it should be!
    # now simplify_calls is moved to transform.py.
    # i kept reverse_binding here for future(?) purposes though. --sanxiyn

    def reverse_binding(self, known_variables, cell):
        """This is a hack."""
        # In simplify_calls, when we are trying to create the new
        # SpaceOperation, all we have are SomeValues.  But SpaceOperations take
        # Variables, not SomeValues.  Trouble is, we don't always have a
        # Variable that just happens to be bound to the given SomeValue.
        # A typical example would be if the tuple of arguments was created
        # from another basic block or even another function.  Well I guess
        # there is no clean solution, short of making the transformations
        # more syntactic (e.g. replacing a specific sequence of SpaceOperations
        # with another one).  This is a real hack because we have to use
        # the identity of 'cell'.
        if cell.is_constant():
            return Constant(cell.const)
        else:
            for v in known_variables:
                if self.bindings[v] is cell:
                    return v
            else:
                raise CannotSimplify

    def simplify(self, block_subset=None):
        # Generic simplifications
        from pypy.translator import transform
        transform.transform_graph(self, block_subset=block_subset)
        from pypy.translator import simplify 
        if block_subset is None:
            graphs = self.translator.flowgraphs.values()
        else:
            graphs = {}
            for block in block_subset:
                fn = self.annotated.get(block)
                if fn in self.translator.flowgraphs:
                    graphs[self.translator.flowgraphs[fn]] = True
        for graph in graphs:
            simplify.eliminate_empty_blocks(graph)


    #___ flowing annotations in blocks _____________________

    def processblock(self, fn, block):
        # Important: this is not called recursively.
        # self.flowin() can only issue calls to self.addpendingblock().
        # The analysis of a block can be in three states:
        #  * block not in self.annotated:
        #      never seen the block.
        #  * self.annotated[block] == False:
        #      the input variables of the block are in self.bindings but we
        #      still have to consider all the operations in the block.
        #  * self.annotated[block] == True or <original function object>:
        #      analysis done (at least until we find we must generalize the
        #      input variables).

        #print '* processblock', block, cells
        if annmodel.DEBUG:
            self.reflowcounter.setdefault(block, 0)
            self.reflowcounter[block] += 1
        self.annotated[block] = fn or True
        try:
            self.flowin(fn, block)
        except BlockedInference, e:
            self.annotated[block] = False   # failed, hopefully temporarily
        except Exception, e:
            # hack for debug tools only
            if not hasattr(e, '__annotator_block'):
                setattr(e, '__annotator_block', block)
            raise

        # The dict 'added_blocks' is used by rpython.annlowlevel to
        # detect which are the new blocks that annotating an additional
        # small helper creates.
        if self.added_blocks is not None:
            self.added_blocks[block] = True

    def reflowpendingblock(self, fn, block):
        assert not self.frozen
        self.pendingblocks[block] = fn
        assert block in self.annotated
        self.annotated[block] = False  # must re-flow

    def bindinputargs(self, fn, block, inputcells, called_from=None, where=None):
        # Create the initial bindings for the input args of a block.
        assert len(block.inputargs) == len(inputcells)
        for a, cell in zip(block.inputargs, inputcells):
            self.setbinding(a, cell, called_from, where=where)
        self.annotated[block] = False  # must flowin.

    def mergeinputargs(self, fn, block, inputcells, called_from=None):
        # Merge the new 'cells' with each of the block's existing input
        # variables.
        oldcells = [self.binding(a) for a in block.inputargs]
        unions = [annmodel.unionof(c1,c2) for c1, c2 in zip(oldcells,inputcells)]
        # if the merged cells changed, we must redo the analysis
        if unions != oldcells:
            self.bindinputargs(fn, block, unions, called_from, where=(fn, block, None))

    def whereami(self, position_key):
        fn, block, i = position_key
        mod = getattr(fn, '__module__', None)
        if mod is None:
            mod = '?'
        name = getattr(fn, '__name__', None)
        if name is not None:
            firstlineno = fn.func_code.co_firstlineno
        else:
            name = 'UNKNOWN'
            firstlineno = -1
        blk = ""
        if block:
            at = block.at()
            if at:
                blk = " block"+at
        opid=""
        if i is not None:
            opid = " op=%d" % i
        return "(%s:%d) %s%s%s" % (mod, firstlineno, name, blk, opid)

    def flowin(self, fn, block):
        #print 'Flowing', block, [self.binding(a) for a in block.inputargs]
        try:
            for i in range(len(block.operations)):
                try:
                    self.bookkeeper.enter((fn, block, i))
                    self.consider_op(block.operations[i])
                finally:
                    self.bookkeeper.leave()

        except BlockedInference, e:
            if annmodel.DEBUG:
                import sys
                self.why_not_annotated[block] = sys.exc_info()

            if (e.op is block.operations[-1] and
                block.exitswitch == Constant(last_exception)):
                # this is the case where the last operation of the block will
                # always raise an exception which is immediately caught by
                # an exception handler.  We then only follow the exceptional
                # branches.
                exits = [link for link in block.exits
                              if link.exitcase is not None]

            elif e.op.opname in ('simple_call', 'call_args'):
                # XXX warning, keep the name of the call operations in sync
                # with the flow object space.  These are the operations for
                # which it is fine to always raise an exception.  We then
                # swallow the BlockedInference and that's it.
                return

            else:
                # other cases are problematic (but will hopefully be solved
                # later by reflowing).  Throw the BlockedInference up to
                # processblock().
                raise
        else:
            # dead code removal: don't follow all exits if the exitswitch
            # is known
            exits = block.exits
            if isinstance(block.exitswitch, Variable):
                s_exitswitch = self.bindings[block.exitswitch]
                if s_exitswitch.is_constant():
                    exits = [link for link in exits
                                  if link.exitcase == s_exitswitch.const]

        # mapping (exitcase, variable) -> s_annotation
        # that can be attached to booleans, exitswitches
        knowntypedata = getattr(self.bindings.get(block.exitswitch),
                                "knowntypedata", {})

        # filter out those exceptions which cannot
        # occour for this specific, typed operation.
        if block.exitswitch == Constant(last_exception):
            op = block.operations[-1]
            if op.opname in annmodel.BINARY_OPERATIONS:
                arg1 = self.binding(op.args[0])
                arg2 = self.binding(op.args[1])
                binop = getattr(pair(arg1, arg2), op.opname, None)
                can_only_throw = getattr(binop, "can_only_throw", None)
            elif op.opname in annmodel.UNARY_OPERATIONS:
                arg1 = self.binding(op.args[0])
                unop = getattr(arg1, op.opname, None)
                can_only_throw = getattr(unop, "can_only_throw", None)
            else:
                can_only_throw = None

            if can_only_throw is not None:
                candidates = can_only_throw
                candidate_exits = exits
                exits = []
                for link in candidate_exits:
                    case = link.exitcase
                    if case is None:
                        exits.append(link)
                        continue
                    covered = [c for c in candidates if issubclass(c, case)]
                    if covered:
                        exits.append(link)
                        candidates = [c for c in candidates if c not in covered]

        for link in exits:
            self.links_followed[link] = True
            import types
            in_except_block = False

            last_exception_var = link.last_exception # may be None for non-exception link
            last_exc_value_var = link.last_exc_value # may be None for non-exception link
            
            if isinstance(link.exitcase, (types.ClassType, type)) \
                   and issubclass(link.exitcase, Exception):
                assert last_exception_var and last_exc_value_var
                last_exc_value_object = self.bookkeeper.valueoftype(link.exitcase)
                last_exception_object = annmodel.SomeObject()
                last_exception_object.knowntype = type
                if isinstance(last_exception_var, Constant):
                    last_exception_object.const = last_exception_var.value
                last_exception_object.is_type_of = [last_exc_value_var]

                if isinstance(last_exception_var, Variable):
                    self.setbinding(last_exception_var, last_exception_object)
                if isinstance(last_exc_value_var, Variable):
                    self.setbinding(last_exc_value_var, last_exc_value_object)

                last_exception_object = annmodel.SomeObject()
                last_exception_object.knowntype = type
                if isinstance(last_exception_var, Constant):
                    last_exception_object.const = last_exception_var.value
                #if link.exitcase is Exception:
                #    last_exc_value_object = annmodel.SomeObject()
                #else:
                last_exc_value_vars = []
                in_except_block = True

            cells = []
            renaming = {}
            for a,v in zip(link.args,link.target.inputargs):
                renaming.setdefault(a, []).append(v)
            for a,v in zip(link.args,link.target.inputargs):
                if a == last_exception_var:
                    assert in_except_block
                    cells.append(last_exception_object)
                elif a == last_exc_value_var:
                    assert in_except_block
                    cells.append(last_exc_value_object)
                    last_exc_value_vars.append(v)
                else:
                    cell = self.binding(a)
                    if (link.exitcase, a) in knowntypedata:
                        knownvarvalue = knowntypedata[(link.exitcase, a)]
                        if not knownvarvalue.contains(cell) and \
                           cell.contains(knownvarvalue): # sanity check
                            cell = knownvarvalue

                    if hasattr(cell,'is_type_of'):
                        renamed_is_type_of = []
                        for v in cell.is_type_of:
                            new_vs = renaming.get(v,[])
                            renamed_is_type_of += new_vs
                        newcell = annmodel.SomeObject()
                        if cell.knowntype == type:
                            newcell.knowntype = type
                        if cell.is_constant():
                            newcell.const = cell.const
                        cell = newcell
                        cell.is_type_of = renamed_is_type_of

                    if hasattr(cell, 'knowntypedata'):
                        renamed_knowntypedata = {}
                        for (value, v), s in cell.knowntypedata.items():
                            new_vs = renaming.get(v, [])
                            for new_v in new_vs:
                                renamed_knowntypedata[value, new_v] = s
                        assert isinstance(cell, annmodel.SomeBool)
                        newcell = annmodel.SomeBool()
                        if cell.is_constant():
                            newcell.const = cell.const
                        cell = newcell
                        cell.knowntypedata = renamed_knowntypedata

                    cells.append(cell)

            if in_except_block:
                last_exception_object.is_type_of = last_exc_value_vars

            self.addpendingblock(fn, link.target, cells)
        if block in self.notify:
            # reflow from certain positions when this block is done
            for callback in self.notify[block]:
                if isinstance(callback, tuple):
                    self.reflowfromposition(callback) # callback is a position
                else:
                    callback()


    #___ creating the annotations based on operations ______

    def consider_op(self, op):
        argcells = [self.binding(a) for a in op.args]
        consider_meth = getattr(self,'consider_op_'+op.opname,
                                None)
        if not consider_meth:
            raise Exception,"unknown op: %r" % op

        # let's be careful about avoiding propagated SomeImpossibleValues
        # to enter an op; the latter can result in violations of the
        # more general results invariant: e.g. if SomeImpossibleValue enters is_
        #  is_(SomeImpossibleValue, None) -> SomeBool
        #  is_(SomeInstance(not None), None) -> SomeBool(const=False) ...
        # boom -- in the assert of setbinding()
        for arg in argcells:
            if isinstance(arg, annmodel.SomeImpossibleValue):
                raise BlockedInference(self, op)
        resultcell = consider_meth(*argcells)
        if resultcell is None:
            resultcell = annmodel.SomeImpossibleValue()  # no return value
        elif resultcell == annmodel.SomeImpossibleValue():
            raise BlockedInference(self, op) # the operation cannot succeed
        assert isinstance(resultcell, annmodel.SomeObject)
        assert isinstance(op.result, Variable)
        self.setbinding(op.result, resultcell)  # bind resultcell to op.result

    def _registeroperations(loc):
        # All unary operations
        for opname in annmodel.UNARY_OPERATIONS:
            exec """
def consider_op_%s(self, arg, *args):
    return arg.%s(*args)
""" % (opname, opname) in globals(), loc
        # All binary operations
        for opname in annmodel.BINARY_OPERATIONS:
            exec """
def consider_op_%s(self, arg1, arg2, *args):
    return pair(arg1,arg2).%s(*args)
""" % (opname, opname) in globals(), loc

    _registeroperations(locals())
    del _registeroperations

    # XXX "contains" clash with SomeObject method
    def consider_op_contains(self, seq, elem):
        self.bookkeeper.count("contains", seq)
        return seq.op_contains(elem)

    def consider_op_newtuple(self, *args):
        return annmodel.SomeTuple(items = args)

    def consider_op_newlist(self, *args):
        return self.bookkeeper.newlist(*args)

    def consider_op_newdict(self, *args):
        assert len(args) % 2 == 0
        items_s = []
        for i in range(0, len(args), 2):
            items_s.append((args[i], args[i+1]))
        return self.bookkeeper.newdict(*items_s)

    def consider_op_newslice(self, start, stop, step):
        self.bookkeeper.count('newslice', start, stop, step)
        return annmodel.SomeSlice(start, stop, step)


class CannotSimplify(Exception):
    pass


class BlockedInference(Exception):
    """This exception signals the type inference engine that the situation
    is currently blocked, and that it should try to progress elsewhere."""

    def __init__(self, annotator, op):
        self.annotator = annotator
        try:
            self.break_at = annotator.bookkeeper.position_key
        except AttributeError:
            self.break_at = None
        self.op = op

    def __repr__(self):
        if not self.break_at:
            break_at = "?"
        else:
            break_at = self.annotator.whereami(self.break_at)
        return "<BlockedInference break_at %s [%s]>" %(break_at, self.op)

    __str__ = __repr__
