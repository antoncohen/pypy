"""
A temporary file that invokes translation of PyPy with the JIT enabled.
"""

import py, os

from pypy.objspace.std import Space
from pypy.config.translationoption import set_opt_level
from pypy.config.pypyoption import get_pypy_config, set_pypy_opt_level
from pypy.objspace.std import multimethod
from pypy.rpython.annlowlevel import llhelper, llstr, hlstr
from pypy.rpython.lltypesystem.rstr import STR
from pypy.rpython.lltypesystem import lltype
from pypy.interpreter.pycode import PyCode

config = get_pypy_config(translating=True)
config.translation.backendopt.inline_threshold = 0
set_opt_level(config, level='1')
config.objspace.compiler = 'ast'
config.objspace.nofaking = True
config.objspace.allworkingmodules = False
config.objspace.usemodules.pypyjit = True
config.objspace.usemodules._weakref = False
config.objspace.usemodules._sre = False
config.translation.rweakref = False # XXX
set_pypy_opt_level(config, level='0')
config.objspace.std.multimethods = 'mrd'
config.objspace.std.builtinshortcut = True
config.objspace.opcodes.CALL_LIKELY_BUILTIN = True
config.objspace.std.withrangelist = True
multimethod.Installer = multimethod.InstallerVersion2
print config

import sys, pdb

space = Space(config)
w_dict = space.newdict()


def readfile(filename):
    fd = os.open(filename, os.O_RDONLY, 0)
    blocks = []
    while True:
        data = os.read(fd, 4096)
        if not data:
            break
        blocks.append(data)
    os.close(fd)
    return ''.join(blocks)

def read_code():
    from pypy.module.marshal.interp_marshal import dumps
    
    source = readfile('pypyjit_demo.py')
    ec = space.getexecutioncontext()
    code = ec.compiler.compile(source, '?', 'exec', 0)
    return llstr(space.str_w(dumps(space, code, space.wrap(2))))

FPTR = lltype.Ptr(lltype.FuncType([], lltype.Ptr(STR)))
read_code_ptr = llhelper(FPTR, read_code)

def entry_point():
    from pypy.module.marshal.interp_marshal import loads
    code = loads(space, space.wrap(hlstr(read_code_ptr())))
    assert isinstance(code, PyCode)
    code.exec_code(space, w_dict, w_dict)

def test_run_translation():
    from pypy.translator.goal.ann_override import PyPyAnnotatorPolicy
    from pypy.rpython.test.test_llinterp import get_interpreter

    # first annotate, rtype, and backendoptimize PyPy
    try:
        interp, graph = get_interpreter(entry_point, [], backendopt=True,
                                        config=config,
                                        policy=PyPyAnnotatorPolicy(space))
    except Exception, e:
        print '%s: %s' % (e.__class__, e)
        pdb.post_mortem(sys.exc_info()[2])
        raise

    # parent process loop: spawn a child, wait for the child to finish,
    # print a message, and restart
    while True:
        child_pid = os.fork()
        if child_pid == 0:
            break
        try:
            os.waitpid(child_pid, 0)
        except KeyboardInterrupt:
            pass
        print '-' * 79
        print 'Child process finished, press Enter to restart...'
        try:
            raw_input()
        except KeyboardInterrupt:
            x = raw_input("are you sure? (y/n)")
            if x == 'y':
                raise
            # otherwise continue

    from pypy.jit.tl.pypyjit_child import run_child
    run_child(globals(), locals())


if __name__ == '__main__':
    import sys
    if len(sys.argv) > 1:
        # debugging: run the code directly
        entry_point()
    else:
        test_run_translation()
