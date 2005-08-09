"""PyPy Translator Frontend

The Translator is a glue class putting together the various pieces of the
translation-related code.  It can be used for interactive testing of the
translator; see pypy/bin/translator.py.
"""
import autopath, os, sys, types

from pypy.objspace.flow.model import *
from pypy.translator.simplify import simplify_graph
from pypy.translator.gensupp import uniquemodulename
from pypy.translator.tool.cbuild import make_module_from_pyxstring
from pypy.translator.tool.cbuild import make_module_from_c
from pypy.objspace.flow import FlowObjSpace


class Translator:

    def __init__(self, func=None, verbose=False, simplifying=True,
                 do_imports_immediately=True,
                 builtins_can_raise_exceptions=False):
        self.entrypoint = func
        self.verbose = verbose
        self.simplifying = simplifying
        self.builtins_can_raise_exceptions = builtins_can_raise_exceptions
        self.do_imports_immediately = do_imports_immediately
        self.clear()

    def clear(self):
        """Clear all annotations and all flow graphs."""
        self.annotator = None
        self.rtyper = None
        self.flowgraphs = {}  # {function: graph}
        self.functions = []   # the keys of self.flowgraphs, in creation order
        self.callgraph = {}   # {opaque_tag: (caller, callee)}
        self.frozen = False   # when frozen, no more flowgraphs can be generated
        #self.concretetypes = {}  # see getconcretetype()
        #self.ctlist = []         #  "
        if self.entrypoint:
            self.getflowgraph()

    def __getstate__(self):
        # try to produce things a bit more ordered
        return self.entrypoint, self.functions, self.__dict__

    def __setstate__(self, args):
        assert len(args) == 3
        self.__dict__.update(args[2])
        assert args[0] is self.entrypoint and args[1] is self.functions

    def getflowgraph(self, func=None, called_by=None, call_tag=None):
        """Get the flow graph for a function (default: the entry point)."""
        func = func or self.entrypoint
        if not isinstance(func, types.FunctionType):
            raise Exception, "getflowgraph() expects a function, got %s" % func
        try:
            graph = self.flowgraphs[func]
        except KeyError:
            if self.verbose:
                print 'getflowgraph (%s:%d) %s' % (
                    func.func_globals.get('__name__', '?'),
                    func.func_code.co_firstlineno,
                    func.__name__),
                sys.stdout.flush()
            assert not self.frozen
            space = FlowObjSpace()
            space.builtins_can_raise_exceptions = self.builtins_can_raise_exceptions
            space.do_imports_immediately = self.do_imports_immediately
            graph = space.build_flow(func)
            if self.simplifying:
                simplify_graph(graph, self.simplifying)
            if self.verbose:
                print
            self.flowgraphs[func] = graph
            self.functions.append(func)
            try:
                import inspect
                graph.func = func
                graph.source = inspect.getsource(func)
            except IOError:
                pass  # e.g. when func is defined interactively
        if called_by:
            self.callgraph[called_by, func, call_tag] = called_by, func
        return graph

    def gv(self, func=None):
        """Shows the control flow graph for a function (default: all)
        -- requires 'dot' and 'gv'."""
        import os
        from pypy.translator.tool.make_dot import make_dot, make_dot_graphs
        if func is None:
            # show the graph of *all* functions at the same time
            graphs = []
            for func in self.functions:
                graph = self.getflowgraph(func)
                graphs.append((graph.name, graph))
            dest = make_dot_graphs(self.entrypoint.__name__, graphs)
        else:
            graph = self.getflowgraph(func)
            dest = make_dot(graph.name, graph)
        os.system('gv %s' % str(dest))

    def view(self):
        """Shows the control flow graph with annotations if computed.
        Requires 'dot' and pygame."""
        from pypy.translator.tool.graphpage import FlowGraphPage
        FlowGraphPage(self).display()

    def viewcg(self):
        """Shows the whole call graph and the class hierarchy, based on
        the computed annotations."""
        from pypy.translator.tool.graphpage import TranslatorPage
        TranslatorPage(self).display()

    def simplify(self, func=None, passes=True):
        """Simplifies the control flow graph (default: for all functions)."""
        if func is None:
            for func in self.flowgraphs.keys():
                self.simplify(func)
        else:
            graph = self.getflowgraph(func)
            simplify_graph(graph, passes)
            
    def annotate(self, input_args_types, func=None, policy=None):
        """annotate(self, input_arg_types[, func]) -> Annotator

        Provides type information of arguments. Returns annotator.
        """
        func = func or self.entrypoint
        if self.annotator is None:
            from pypy.translator.annrpython import RPythonAnnotator
            self.annotator = RPythonAnnotator(self, policy=policy)
        graph = self.getflowgraph(func)
        self.annotator.build_types(graph, input_args_types, func)
        return self.annotator

    def checkgraphs(self):
        for graph in self.flowgraphs.itervalues():
            checkgraph(graph)

    def specialize(self, **flags):
        if self.annotator is None:
            raise ValueError("you need to call annotate() first")
        if self.rtyper is not None:
            raise ValueError("cannot specialize() several times")
        from pypy.rpython.rtyper import RPythonTyper
        self.rtyper = RPythonTyper(self.annotator)
        self.rtyper.specialize(**flags)

    def backend_optimizations(self):
        from pypy.translator.backendoptimization import backend_optimizations
        for graph in self.flowgraphs.values():
            backend_optimizations(graph)

    def source(self, func=None):
        """Returns original Python source.
        
        Returns <interactive> for functions written during the
        interactive session.
        """
        func = func or self.entrypoint
        graph = self.getflowgraph(func)
        return getattr(graph, 'source', '<interactive>')

    def pyrex(self, input_arg_types=None, func=None):
        """pyrex(self[, input_arg_types][, func]) -> Pyrex translation

        Returns Pyrex translation. If input_arg_types is provided,
        returns type annotated translation. Subsequent calls are
        not affected by this.
        """
        from pypy.translator.pyrex.genpyrex import GenPyrex
        return self.generatecode(GenPyrex, input_arg_types, func)

    def cl(self, input_arg_types=None, func=None):
        """cl(self[, input_arg_types][, func]) -> Common Lisp translation
        
        Returns Common Lisp translation. If input_arg_types is provided,
        returns type annotated translation. Subsequent calls are
        not affected by this.
        """
        from pypy.translator.gencl import GenCL
        return self.generatecode(GenCL, input_arg_types, func)

    def c(self):
        """c(self) -> C (CPython) translation
        
        Returns C (CPython) translation.
        """
        from pypy.translator.c import genc
        from cStringIO import StringIO
        f = StringIO()
        database, ignored = genc.translator2database(self)
        genc.gen_readable_parts_of_main_c_file(f, database)
        return f.getvalue()

    def llvm(self):
        """llvm(self) -> LLVM translation
        
        Returns LLVM translation.
        """
        from pypy.translator.llvm2 import genllvm
        if self.annotator is None:
            raise ValueError, "function has to be annotated."
        gen = genllvm.GenLLVM(self)
        return str(gen.compile())
    
    def generatecode(self, gencls, input_arg_types, func):
        if input_arg_types is None:
            ann = self.annotator
        else:
            from pypy.translator.annrpython import RPythonAnnotator
            ann = RPythonAnnotator(self)
        if func is None:
            codes = [self.generatecode1(gencls, input_arg_types,
                                        self.entrypoint, ann)]
            for func in self.functions:
                if func is not self.entrypoint:
                    code = self.generatecode1(gencls, None, func, ann,
                                              public=False)
                    codes.append(code)
        else:
            codes = [self.generatecode1(gencls, input_arg_types, func, ann)]
        code = self.generateglobaldecl(gencls, func, ann)
        if code:
            codes.insert(0, code)
        return '\n\n#_________________\n\n'.join(codes)

    def generatecode1(self, gencls, input_arg_types, func, ann, public=True):
        graph = self.getflowgraph(func)
        g = gencls(graph)
        g.by_the_way_the_function_was = func   # XXX
        if input_arg_types is not None:
            ann.build_types(graph, input_arg_types, func)
        if ann is not None:
            g.setannotator(ann)
        return g.emitcode(public)

    def generateglobaldecl(self, gencls, func, ann):
        graph = self.getflowgraph(func)
        g = gencls(graph)
        if ann is not None:
            g.setannotator(ann)
        return g.globaldeclarations()

    def pyrexcompile(self):
        """Returns compiled function, compiled using Pyrex.
        """
        from pypy.tool.udir import udir
        name = self.entrypoint.func_name
        pyxcode = self.pyrex()
        mod = make_module_from_pyxstring(name, udir, pyxcode)
        return getattr(mod, name)

    def ccompile(self, really_compile=True, standalone=False):
        """Returns compiled function (living in a new C-extension module), 
           compiled using the C generator.
        """
        if self.annotator is not None:
            self.frozen = True
        cbuilder = self.cbuilder(standalone=standalone)
        c_source_filename = cbuilder.generate_source()
        if not really_compile: 
            return c_source_filename
        cbuilder.compile()
        if standalone:
            return cbuilder.executable_name
        cbuilder.import_module()    
        return cbuilder.get_entry_point()

    def cbuilder(self, standalone=False):
        from pypy.translator.c import genc
        if standalone:
            return genc.CStandaloneBuilder(self)
        else:
            return genc.CExtModuleBuilder(self)

    def llvmcompile(self, really_compile=True, standalone=False, optimize=True):
        """llvmcompile(self, really_compile=True, standalone=False, optimize=True) -> LLVM translation
        
        Returns LLVM translation with or without optimization.
        """
        from pypy.translator.llvm2 import genllvm
        if self.annotator is None:
            raise ValueError, "function has to be annotated."
        self.frozen = True
        return genllvm.genllvm(self, really_compile=really_compile, standalone=standalone, optimize=optimize)

        #self.frozen = True
        #if standalone:
        #    builder = genllvm.LLVMStandaloneBuilder(self, optimize=optimize)
        #else:
        #    builder = genllvm.LLVMExtModuleBuilder(self, optimize=optimize)
        #source_filename = builder.generate_source()
        #if not really_compile:
        #    return source_filename
        #builder.compile()
        #if standalone:
        #    return builder.executable_name
        #builder.import_module()
        #return builder.get_entry_point()

    def call(self, *args):
        """Calls underlying Python function."""
        return self.entrypoint(*args)

    def dis(self, func=None):
        """Disassembles underlying Python function to bytecodes."""
        from dis import dis
        dis(func or self.entrypoint)

##    def consider_call(self, ann, func, args):
##        graph = self.getflowgraph(func)
##        ann.addpendingblock(graph.startblock, args)
##        result_var = graph.getreturnvar()
##        try:
##            return ann.binding(result_var)
##        except KeyError:
##            # typical case for the 1st call, because addpendingblock() did
##            # not actually start the analysis of the called function yet.
##            return impossiblevalue

##    def getconcretetype(self, cls, *args):
##        "DEPRECATED.  To be removed"
##        # Return a (cached) 'concrete type' object attached to this translator.
##        # Concrete types are what is put in the 'concretetype' attribute of
##        # the Variables and Constants of the flow graphs by typer.py to guide
##        # the code generators.
##        try:
##            return self.concretetypes[cls, args]
##        except KeyError:
##            result = self.concretetypes[cls, args] = cls(self, *args)
##            self.ctlist.append(result)
##            return result
