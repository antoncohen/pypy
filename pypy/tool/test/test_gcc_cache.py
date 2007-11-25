
from pypy.tool.gcc_cache import *
from pypy.tool.udir import udir
import md5
from pypy.translator.tool.cbuild import ExternalCompilationInfo

def test_gcc_exec():
    f = udir.join("x.c")
    f.write("""
    #include <stdio.h>
    int main()
    {
       printf("3\\n");
       return 0;
    }
    """)
    # remove cache
    try:
        cache_dir.join(md5.md5(f.read()).hexdigest()).remove()
    except:
        pass
    assert build_executable_cache([f], ExternalCompilationInfo()) == "3\n"
    assert build_executable_cache([f], ExternalCompilationInfo(), compiler_exe="xxx") == "3\n"

def test_gcc_ask():
    f = udir.join("y.c")
    f.write("""
    int main()
    {
      return 0;
    }
    """)
    try:
        cache_dir.join(md5.md5(f.read()).hexdigest()).remove()
    except:
        pass
    assert try_compile_cache([f], ExternalCompilationInfo())
    assert try_compile_cache([f], ExternalCompilationInfo(), compiler_exe="xxx")
