#!/usr/bin/env python
""" A parser for debug output from x86 backend. used to derive
new tests from crashes
"""

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

class Block(object):
    def __init__(self):
        self.operations = []

    def add(self, element):
        self.operations.append(element)

class Comment(object):
    def __init__(self, text):
        self.text = text

class Operation(object):
    def __init__(self, opname, args, result=None, descr=None):
        self.opname = opname
        self.args   = args
        self.result = result
        self.descr = descr

    def __repr__(self):
        if self.result is None:
            return "%s(%s)" % (self.opname, self.args)
        return "%s = %s(%s)" % (self.result, self.opname, self.args)

class GuardOperation(Operation):

    @property
    def suboperations(self):
        return self.subblock.operations

class AbstractValue(object):

    def __init__(self, value):
        self.value = int(value)

class Box(AbstractValue):
    pass

class BoxInt(Box):
    pass

class BoxAddr(Box):
    pass

class BoxPtr(Box):
    pass

class Const(AbstractValue):
    pass

class ConstInt(Const):
    pass

class ConstAddr(Const):
    pass

class ConstPtr(Const):
    pass

box_map = {
    'b' : {
        'i' : BoxInt,
        'a' : BoxAddr,
        'p' : BoxPtr
        },
    'c' : {
        'i' : ConstInt,
        'a' : ConstAddr,
        'p' : ConstPtr
        },
}


_arg_finder = re.compile(r"(..)\((\d+),(\d+)\)")

class Parser(object):

    current_indentation = 0

    def parse(self, fname):
        self.current_block = Block()
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
             self.current_block = Block()
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
            node = klass(value)
            self.boxes[key] = node
        assert node.__class__ is box_map[tp_info[0]][tp_info[1]]
        return node

    def parse_result(self, result):
        return result

    def parse_inputargs(self, inputargs):
        return inputargs

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
        if line.startswith('LOOP END'):
            raise EndOfBlock()
        if line.startswith('LOOP'):
            _, inputargs = line.split(" ")
            self.current_block.inputargs = self.parse_inputargs(inputargs)
            return i + 1
        if line.startswith('END'):
            raise EndOfBlock()
        if line.startswith('#'):
            self.current_block.add(Comment(line[1:]))
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
            if lines[i + 1].strip().startswith('BEGIN'):
                self.current_indentation += 2
                guard_op = GuardOperation(opname, args)
                self.current_block.add(guard_op)
                return self.parse_block(lines, i + 2, guard_op)
            marker, result = lines[i + 1].strip().split(" ")
            assert marker == '=>'
            result, = self._parse_boxes(result)
            self.current_block.add(Operation(opname, args, result, descr))
            return i + 2
        else:
            self.current_block.add(Operation(opname, args, descr=descr))
            return i + 1

    def _parse(self, lines, i):
        while True:
            try:
                indentation = count_indent(lines[i])
                if indentation == self.current_indentation:
                    i = self.parse_next_instruction(lines, i)
                else:
                    xxx
            except EndOfBlock:
                return i + 1

