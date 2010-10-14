from pypy.interpreter.mixedmodule import MixedModule
import sys

class Module(MixedModule):

    appleveldefs = {
        }

    interpleveldefs = {
        'DEFAULT_BUFFER_SIZE': 'space.wrap(interp_io.DEFAULT_BUFFER_SIZE)',
        'BlockingIOError': 'interp_io.W_BlockingIOError',
        '_IOBase': 'interp_io.W_IOBase',
        '_RawIOBase': 'interp_io.W_RawIOBase',
        '_BufferedIOBase': 'interp_io.W_BufferedIOBase',
        '_TextIOBase': 'interp_io.W_TextIOBase',

        'FileIO': 'interp_fileio.W_FileIO',
        'BytesIO': 'interp_io.W_BytesIO',
        'StringIO': 'interp_stringio.W_StringIO',
        'BufferedReader': 'interp_io.W_BufferedReader',
        'BufferedWriter': 'interp_io.W_BufferedWriter',
        'BufferedRWPair': 'interp_io.W_BufferedRWPair',
        'BufferedRandom': 'interp_io.W_BufferedRandom',
        'TextIOWrapper': 'interp_io.W_TextIOWrapper',

        'open': 'interp_io.open',
        'UnsupportedOperation': 'space.w_None',
        'IncrementalNewlineDecoder': 'space.w_None',
        }
