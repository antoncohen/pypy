
from pypy.jit.tool.otherviewer import splitloops, FinalBlock, Block,\
     split_one_loop, postprocess

def preparse(data):
    return "\n".join([i.strip() for i in data.split("\n") if i.strip()])

class TestSplitLoops(object):
    def test_no_of_loops(self):
        data = [preparse("""
        # Loop 0 : loop with 39 ops
        debug_merge_point('')
        guard_class(p4, 141310752, descr=<Guard5>) [p0, p1]
        p60 = getfield_gc(p4, descr=<GcPtrFieldDescr 16>)
        guard_nonnull(p60, descr=<Guard6>) [p0, p1]
        """), preparse("""
        # Loop 1 : loop with 46 ops
        p21 = getfield_gc(p4, descr=<GcPtrFieldDescr 16>)
        """)]
        loops = splitloops(data)
        assert len(loops) == 2

    def test_split_one_loop(self):
        real_loops = [FinalBlock(preparse("""
        p21 = getfield_gc(p4, descr=<GcPtrFieldDescr 16>)
        guard_class(p4, 141310752, descr=<Guard51>) [p0, p1]
        """), None), FinalBlock(preparse("""
        p60 = getfield_gc(p4, descr=<GcPtrFieldDescr 16>)
        guard_nonnull(p60, descr=<Guard5>) [p0, p1]
        """), None)]
        split_one_loop(real_loops, 'Guard5', 'extra')
        assert isinstance(real_loops[1], Block)
        assert real_loops[1].content.endswith('p1]')
        assert real_loops[1].left.content == ''
        assert real_loops[1].right.content.startswith('guard_nonnull')

    def test_postparse(self):
        real_loops = [FinalBlock("debug_merge_point('<code object _runCallbacks, file '/tmp/x/twisted-trunk/twisted/internet/defer.py', line 357> #40 POP_TOP')", None)]
        postprocess(real_loops)
        assert real_loops[0].content.startswith("_runCallbacks, file '/tmp/x/twisted-trunk/twisted/internet/defer.py', line 357")
