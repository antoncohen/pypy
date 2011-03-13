from pypy.jit.metainterp.optimizeopt.optimizer import Optimizer
from pypy.jit.metainterp.optimizeopt.rewrite import OptRewrite
from pypy.jit.metainterp.optimizeopt.intbounds import OptIntBounds
from pypy.jit.metainterp.optimizeopt.virtualize import OptVirtualize
from pypy.jit.metainterp.optimizeopt.heap import OptHeap
from pypy.jit.metainterp.optimizeopt.string import OptString
from pypy.jit.metainterp.optimizeopt.unroll import optimize_unroll, OptInlineShortPreamble

def optimize_loop_1(metainterp_sd, loop, unroll=True,
                    inline_short_preamble=True, retraced=False):
    """Optimize loop.operations to remove internal overheadish operations. 
    """
    opt_str = OptString()
    optimizations = [OptIntBounds(),
                     OptRewrite(),
                     OptVirtualize(),
                     opt_str,
                     OptHeap(),
                    ]
    if inline_short_preamble:
        optimizations = [OptInlineShortPreamble(retraced)] +  optimizations
        
    if metainterp_sd.jit_ffi:
        from pypy.jit.metainterp.optimizeopt.fficall import OptFfiCall
        optimizations = optimizations + [
                     OptFfiCall(),
                    ]

    if unroll:
        opt_str.enabled = False # FIXME: Workaround to disable string optimisation
                                # during preamble but to keep it during the loop
        optimize_unroll(metainterp_sd, loop, optimizations)
    else:
        optimizer = Optimizer(metainterp_sd, loop, optimizations)
        optimizer.propagate_all_forward()

def optimize_bridge_1(metainterp_sd, bridge, inline_short_preamble=True,
                      retraced=False):
    """The same, but for a bridge. """
    optimize_loop_1(metainterp_sd, bridge, False, inline_short_preamble,
                    retraced)
