#!/usr/bin/env python
""" A parser for debug output from x86 backend. used to derive
new tests from crashes
"""

import autopath
import sys, py, re

def count_indent(s):
    indent = 0
    for char in s:
        if char != " ":
            break
        indent += 1
    return indent

class EndOfBlock(Exception):
    pass

class OpContainer(object):
    def __init__(self):
        self.operations = []

    def add(self, element):
        self.operations.append(element)

    def get_operations(self):
        return self.operations

    def get_display_text(self):
        return str(self)

    def iter_operations(self):
        for op in self.operations:
            if isinstance(op, Operation):
                yield op

class Loop(OpContainer):

    def __repr__(self):
        return "Loop"

class Block(OpContainer):

    def __repr__(self):
        return "Block"

class BaseOperation(object):

    def is_guard(self):
        return False

class Comment(BaseOperation):
    def __init__(self, text):
        self.text = text
        self.args = []
        self.result = None

    def __repr__(self):
        return "Comment: %r" % (self.text,)

class ByteCodeRef(Comment):
    def __init__(self, text):
        Comment.__init__(self, text)
        self.address = int(text.rsplit('#')[1])

class Operation(BaseOperation):
    def __init__(self, opname, args, result=None, descr=None):
        self.opname = opname
        self.args   = args
        self.result = result
        self.descr = descr

    def __repr__(self):
        str_args = [str(arg) for arg in self.args]
        if self.result is None:
            return "%s(%s)" % (self.opname, str_args)
        return "%s = %s(%s)" % (self.result, self.opname, str_args)

class GuardOperation(Operation):

    @property
    def suboperations(self):
        return self.subblock.operations

    def is_guard(self):
        return True

class AbstractValue(object):

    is_box = True

    def __init__(self, iden, value):
        self.value = int(value)
        self.iden = iden

    def __repr__(self):
        klass = self.__class__.__name__
        return "%s%s(%s)" % (klass, self.iden, self.value)

    def __str__(self):
        klass = self.__class__.__name__
        return '%s%s' % (klass, self.iden)

    @property
    def pretty(self):
        return "%s%s" % (self._var_prefix, self.iden)

class Box(AbstractValue):
    pass

class BoxInt(Box):
    _var_prefix = "i"

class BoxAddr(Box):
    pass

class BoxRef(Box):
    _var_prefix = "r"

class Const(AbstractValue):

    @property
    def pretty(self):
        return "%s(REPLACE!!!)" % (self.__class__.__name__,)

class ConstInt(Const):

    @property
    def pretty(self):
        return str(self.value)

class ConstAddr(Const):
    pass

class ConstRef(Const):
    pass

box_map = {
    'b' : {
        'i' : BoxInt,
        'a' : BoxAddr,
        'r' : BoxRef
        },
    'c' : {
        'i' : ConstInt,
        'a' : ConstAddr,
        'r' : ConstRef
        },
}


_arg_finder = re.compile(r"(..)\((\d+),(-?\d+)\)")

class Parser(object):

    current_indentation = 0

    def parse(self, fname):
        self.current_block = Loop()
        self.blockstack = []
        self.boxes = {}
        data = py.path.local(fname).read()
        lines = data.splitlines()
        i = 0
        length = len(lines)
        loops = []
        while i < length:
             i = self._parse(lines, i)
             loops.append(self.current_block)
             self.boxes = {}
             self.current_block = Loop()
        assert not self.blockstack
        return loops

    def _parse_boxes(self, box_string):
        boxes = []
        for info, iden, value in _arg_finder.findall(box_string):
            box = self.get_box(iden, info, value)
            boxes.append(self.get_box(int(iden), info, value))
        return boxes

    def get_box(self, key, tp_info, value):
        try:
            node = self.boxes[key]
        except KeyError:
            box_type, tp = tp_info
            klass = box_map[box_type][tp]
            node = klass(key, value)
            self.boxes[key] = node
        assert node.__class__ is box_map[tp_info[0]][tp_info[1]]
        return node

    def parse_result(self, result):
        return result

    def parse_block(self, lines, start, guard_op):
        self.blockstack.append(self.current_block)
        block = Block()
        guard_op.subblock = block
        self.current_block = block
        res = self._parse(lines, start)
        self.current_block = self.blockstack.pop()
        self.current_indentation -= 2
        return res

    def parse_next_instruction(self, lines, i):
        line = lines[i].strip()
        if not line:
            return i + 1
        if line.startswith('LOOP END'):
            raise EndOfBlock()
        if line.startswith('LOOP'):
            _, inputargs = line.split(" ")
            self.current_block.inputargs = self._parse_boxes(inputargs)
            return i + 1
        if line.startswith('END'):
            raise EndOfBlock()
        has_hash = line.startswith('#')
        if has_hash or line.startswith('<'):
            if has_hash:
                line = line.lstrip("#")
            if line.startswith('<code '):
                self.current_block.add(ByteCodeRef(line))
            else:
                self.current_block.add(Comment(line))
            return i + 1
        descr = None
        if " " in line:
            # has arguments
            opname, args_string = line.split(" ")
            args = self._parse_boxes(args_string)
            bracket = args_string.find("[")
            if bracket != -1:
                assert args_string[-1] == "]"
                descr = eval(args_string[bracket:])
        else:
            opname = line
            args = []
        _, opname = opname.split(":")
        if lines[i + 1].startswith(" " * (self.current_indentation + 2)):
            # Could be the beginning of a guard or the result of an
            # operation. (Or the result of a guard operation.)
            result = self._parse_guard(lines, i + 1, opname, args)
            if result != -1:
                return result
            # If there's not a BEGIN line, there must be a result.
            op_result = self._parse_result(lines[i + 1])
            # BEGIN might appear after the result.  guard_exception has a
            # result.
            result = self._parse_guard(lines, i + 2, opname, args, op_result)
            if result != -1:
                return result
            # Definitely not a guard.
            self.current_block.add(Operation(opname, args, op_result, descr))
            return i + 2
        else:
            self.current_block.add(Operation(opname, args, descr=descr))
            return i + 1

    def _parse_guard(self, lines, i, opname, args, op_result=None):
        if lines[i].lstrip().startswith('BEGIN'):
            self.current_indentation += 2
            guard_op = GuardOperation(opname, args, op_result)
            self.current_block.add(guard_op)
            return self.parse_block(lines, i + 1, guard_op)
        return -1

    def _parse_result(self, line):
        line = line.strip()
        marker, result = line.split(" ")
        assert marker == "=>"
        result, = self._parse_boxes(result)
        return result

    def _parse(self, lines, i):
        try:
            while True:
                i = self.parse_next_instruction(lines, i)
        except EndOfBlock:
            assert i < len(lines)
            return i + 1
        else:
            raise AssertionError("shouldn't happen (python bug????)")


def _write_operations(ops, level):
    def write(stuff):
        print " " * level + stuff
    for op in (op for op in ops if not isinstance(op, Comment)):
        args = [arg.pretty for arg in op.args]
        if op.descr:
            args.append("descr=%r" % (op.descr,))
        args_string = ", ".join(args)
        op_string = "%s(%s)" % (op.opname, args_string)
        if op.is_guard():
            write(op_string)
            _write_operations(op.suboperations, level + 4)
        else:
            if op.result is None:
                write(op_string)
            else:
                write("%s = %s" % (op.result.pretty, op_string))


def convert_to_oparse(loops):
    if len(loops) > 1:
        print >> sys.stderr, "there's more than one loop in that file!"
        sys.exit(1)
    loop, = loops
    print "[%s]" % (", ".join(arg.pretty for arg in loop.inputargs),)
    _write_operations(loop.operations, 0)


if __name__ == "__main__":
    from pypy.jit.metainterp.graphpage import display_loops
    if len(sys.argv) != 3:
        print >> sys.stderr, "usage: (convert | show) file"
        sys.exit(2)
    operation = sys.argv[1]
    fn = sys.argv[2]
    parser = Parser()
    loops = parser.parse(fn)
    if operation == "convert":
        convert_to_oparse(loops)
    elif operation == "show":
        display_loops(loops)
    else:
        print >> sys.stderr, "invalid operation"
        sys.exit(2)
    sys.exit(0)

