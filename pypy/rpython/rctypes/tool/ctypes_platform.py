#! /usr/bin/env python

import os, py
import ctypes
from pypy.translator.tool.cbuild import build_executable
from pypy.tool.udir import udir

# ____________________________________________________________
#
# Helpers for simple cases

def getstruct(name, c_header_source, interesting_fields):
    class CConfig:
        _header_ = c_header_source
        STRUCT = Struct(name, interesting_fields)
    return configure(CConfig)['STRUCT']

def getsimpletype(name, c_header_source, ctype_hint=ctypes.c_int):
    class CConfig:
        _header_ = c_header_source
        TYPE = SimpleType(name, ctype_hint)
    return configure(CConfig)['TYPE']

def getconstantinteger(name, c_header_source):
    class CConfig:
        _header_ = c_header_source
        CONST = ConstantInteger(name)
    return configure(CConfig)['CONST']

def getdefined(macro, c_header_source):
    class CConfig:
        _header_ = c_header_source
        DEFINED = Defined(macro)
    return configure(CConfig)['DEFINED']

# ____________________________________________________________
#
# General interface

def configure(CConfig):
    """Examine the local system by running the C compiler.
    The CConfig class contains CConfigEntry attribues that describe
    what should be inspected; configure() returns a dict mapping
    names to the results.
    """
    entries = []
    for key in dir(CConfig):
        value = getattr(CConfig, key)
        if isinstance(value, CConfigEntry):
            entries.append((key, value))

    filepath = uniquefilepath()
    f = filepath.open('w')
    print >> f, C_HEADER
    print >> f
    print >> f, CConfig._header_    # mandatory
    print >> f
    for key, entry in entries:
        print >> f, 'void dump_section_%s(void) {' % (key,)
        for line in entry.prepare_code():
            if line and line[0] != '#':
                line = '\t' + line
            print >> f, line
        print >> f, '}'
        print >> f

    print >> f, 'int main(void) {'
    for key, entry in entries:
        print >> f, '\tprintf("-+- %s\\n");' % (key,)
        print >> f, '\tdump_section_%s();' % (key,)
        print >> f, '\tprintf("---\\n");'
    print >> f, '\treturn 0;'
    print >> f, '}'
    f.close()

    include_dirs = getattr(CConfig, '_include_dirs_', [])
    infolist = list(run_example_code(filepath, include_dirs))
    assert len(infolist) == len(entries)

    result = {}
    for info, (key, entry) in zip(infolist, entries):
        result[key] = entry.build_result(info)
    return result

# ____________________________________________________________


class CConfigEntry(object):
    "Abstract base class."


class Struct(CConfigEntry):
    """An entry in a CConfig class that stands for an externally
    defined structure.
    """
    def __init__(self, name, interesting_fields):
        self.name = name
        self.interesting_fields = interesting_fields

    def prepare_code(self):
        yield 'typedef %s ctypesplatcheck_t;' % (self.name,)
        yield 'typedef struct {'
        yield '    char c;'
        yield '    ctypesplatcheck_t s;'
        yield '} ctypesplatcheck2_t;'
        yield ''
        yield 'ctypesplatcheck_t s;'
        yield 'dump("align", offsetof(ctypesplatcheck2_t, s));'
        yield 'dump("size",  sizeof(ctypesplatcheck_t));'
        for fieldname, fieldtype in self.interesting_fields:
            yield 'dump("fldofs %s", offsetof(ctypesplatcheck_t, %s));'%(
                fieldname, fieldname)
            yield 'dump("fldsize %s",   sizeof(s.%s));' % (
                fieldname, fieldname)
            if fieldtype in integer_class:
                yield 's.%s = 0; s.%s = ~s.%s;' % (fieldname,
                                                   fieldname,
                                                   fieldname)
                yield 'dump("fldunsigned %s", s.%s > 0);' % (fieldname,
                                                             fieldname)

    def build_result(self, info):
        alignment = 1
        layout = [None] * info['size']
        for fieldname, fieldtype in self.interesting_fields:
            offset = info['fldofs '  + fieldname]
            size   = info['fldsize ' + fieldname]
            sign   = info.get('fldunsigned ' + fieldname, False)
            if (size, sign) != size_and_sign(fieldtype):
                fieldtype = fixup_ctype(fieldtype, fieldname, (size, sign))
            layout_addfield(layout, offset, fieldtype, fieldname)
            alignment = max(alignment, ctypes.alignment(fieldtype))

        # try to enforce the same alignment as the one of the original
        # structure
        if alignment < info['align']:
            choices = [ctype for ctype in alignment_types
                             if ctypes.alignment(ctype) == info['align']]
            assert choices, "unsupported alignment %d" % (info['align'],)
            choices = [(ctypes.sizeof(ctype), i, ctype)
                       for i, ctype in enumerate(choices)]
            csize, _, ctype = min(choices)
            for i in range(0, info['size'] - csize + 1, info['align']):
                if layout[i:i+csize] == [None] * csize:
                    layout_addfield(layout, i, ctype, '_alignment')
                    break
            else:
                raise AssertionError("unenforceable alignment %d" % (
                    info['align'],))

        n = 0
        for i, cell in enumerate(layout):
            if cell is not None:
                continue
            layout_addfield(layout, i, ctypes.c_char, '_pad%d' % (n,))
            n += 1

        # build the ctypes Structure
        seen = {}
        fields = []
        for cell in layout:
            if cell in seen:
                continue
            fields.append((cell.name, cell.ctype))
            seen[cell] = True

        class S(ctypes.Structure):
            _fields_ = fields
        name = self.name
        if name.startswith('struct '):
            name = name[7:]
        S.__name__ = name
        return S


class SimpleType(CConfigEntry):
    """An entry in a CConfig class that stands for an externally
    defined simple numeric type.
    """
    def __init__(self, name, ctype_hint=ctypes.c_int):
        self.name = name
        self.ctype_hint = ctype_hint

    def prepare_code(self):
        yield 'typedef %s ctypesplatcheck_t;' % (self.name,)
        yield ''
        yield 'ctypesplatcheck_t x;'
        yield 'dump("size",  sizeof(ctypesplatcheck_t));'
        if self.ctype_hint in integer_class:
            yield 'x = 0; x = ~x;'
            yield 'dump("unsigned", x > 0);'

    def build_result(self, info):
        size = info['size']
        sign = info.get('unsigned', False)
        ctype = self.ctype_hint
        if (size, sign) != size_and_sign(ctype):
            ctype = fixup_ctype(ctype, self.name, (size, sign))
        return ctype


class ConstantInteger(CConfigEntry):
    """An entry in a CConfig class that stands for an externally
    defined integer constant.
    """
    def __init__(self, name):
        self.name = name

    def prepare_code(self):
        yield 'if ((%s) < 0) {' % (self.name,)
        yield '    long long x = (long long)(%s);' % (self.name,)
        yield '    printf("value: %lld\\n", x);'
        yield '} else {'
        yield '    unsigned long long x = (unsigned long long)(%s);' % (
                        self.name,)
        yield '    printf("value: %llu\\n", x);'
        yield '}'

    def build_result(self, info):
        return info['value']


class Defined(CConfigEntry):
    """A boolean, corresponding to an #ifdef.
    """
    def __init__(self, macro):
        self.macro = macro
        self.name = macro

    def prepare_code(self):
        yield '#ifdef %s' % (self.macro,)
        yield 'dump("defined", 1);'
        yield '#else'
        yield 'dump("defined", 0);'
        yield '#endif'

    def build_result(self, info):
        return bool(info['defined'])

# ____________________________________________________________
#
# internal helpers

def uniquefilepath(LAST=[0]):
    i = LAST[0]
    LAST[0] += 1
    return udir.join('ctypesplatcheck_%d.c' % i)

alignment_types = [
    ctypes.c_short,
    ctypes.c_int,
    ctypes.c_long,
    ctypes.c_float,
    ctypes.c_double,
    ctypes.c_char_p,
    ctypes.c_void_p,
    ctypes.c_longlong,
    ctypes.c_wchar,
    ctypes.c_wchar_p,
    ]

integer_class = [ctypes.c_byte,     ctypes.c_ubyte,
                 ctypes.c_short,    ctypes.c_ushort,
                 ctypes.c_int,      ctypes.c_uint,
                 ctypes.c_long,     ctypes.c_ulong,
                 ctypes.c_longlong, ctypes.c_ulonglong,
                 ]
float_class = [ctypes.c_float, ctypes.c_double]

class Field(object):
    def __init__(self, name, ctype):
        self.name = name
        self.ctype = ctype
    def __repr__(self):
        return '<field %s: %s>' % (self.name, self.ctype)

def layout_addfield(layout, offset, ctype, prefix):
    size = ctypes.sizeof(ctype)
    name = prefix
    i = 0
    while name in layout:
        i += 1
        name = '%s_%d' % (prefix, i)
    field = Field(name, ctype)
    for i in range(offset, offset+size):
        assert layout[i] is None, "%s overlaps %r" % (fieldname, layout[i])
        layout[i] = field
    return field

def size_and_sign(ctype):
    return (ctypes.sizeof(ctype),
            ctype in integer_class and ctype(-1).value > 0)

def fixup_ctype(fieldtype, fieldname, expected_size_and_sign):
    for typeclass in [integer_class, float_class]:
        if fieldtype in typeclass:
            for ctype in typeclass:
                if size_and_sign(ctype) == expected_size_and_sign:
                    return ctype
    raise TypeError("conflicting field type %r for %r" % (fieldtype,
                                                          fieldname))


C_HEADER = """
#include <stdio.h>
#include <stddef.h>   /* for offsetof() */

void dump(char* key, int value) {
    printf("%s: %d\\n", key, value);
}
"""

def run_example_code(filepath, include_dirs=[]):
    executable = build_executable([filepath], include_dirs=include_dirs)
    output = py.process.cmdexec(executable)
    section = None
    for line in output.splitlines():
        line = line.strip()
        if line.startswith('-+- '):      # start of a new section
            section = {}
        elif line == '---':              # section end
            assert section is not None
            yield section
            section = None
        elif line:
            assert section is not None
            key, value = line.split(': ')
            section[key] = int(value)

# ____________________________________________________________

def get_python_include_dir():
    from distutils import sysconfig
    gcv = sysconfig.get_config_vars()
    return gcv['INCLUDEPY']

if __name__ == '__main__':
    doc = """Example:
    
       ctypes_platform.py  -h sys/types.h  -h netinet/in.h
                           'struct sockaddr_in'
                           sin_port  c_int
    """
    import sys, getopt
    opts, args = getopt.gnu_getopt(sys.argv[1:], 'h:')
    if not args:
        print >> sys.stderr, doc
    else:
        assert len(args) % 2 == 1
        headers = []
        for opt, value in opts:
            if opt == '-h':
                headers.append('#include <%s>' % (value,))
        name = args[0]
        fields = []
        for i in range(1, len(args), 2):
            ctype = getattr(ctypes, args[i+1])
            fields.append((args[i], ctype))

        S = getstruct(name, '\n'.join(headers), fields)

        for key, value in S._fields_:
            print key, value
