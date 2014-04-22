import py
from rpython.tool.udir import udir

testdir = udir.ensure('test_buffer', dir=1)


class TestBuffer:
    def test_buffer_w(self):
        space = self.space
        w_hello = space.wrap('hello world')
        buf = space.buffer_w(w_hello, space.BUF_SIMPLE)
        assert buf.getlength() == 11
        assert buf.as_str() == 'hello world'
        assert buf.getslice(1, 6, 1, 5) == 'ello '
        assert space.buffer_w(space.newbuffer(buf), space.BUF_SIMPLE) is buf
        assert space.bufferstr_w(w_hello) == 'hello world'
        assert space.bufferstr_w(space.newbuffer(space.buffer_w(w_hello, space.BUF_SIMPLE))) == 'hello world'
        space.raises_w(space.w_TypeError, space.buffer_w, space.wrap(5), space.BUF_SIMPLE)

    def test_file_write(self):
        space = self.space
        w_buffer = space.newbuffer(space.buffer_w(space.wrap('hello world'), space.BUF_SIMPLE))
        filename = str(testdir.join('test_file_write'))
        space.appexec([w_buffer, space.wrap(filename)], """(buffer, filename):
            f = open(filename, 'wb')
            f.write(buffer)
            f.close()
        """)
        f = open(filename, 'rb')
        data = f.read()
        f.close()
        assert data == 'hello world'

    def test_unicode(self):
        space = self.space
        s = space.bufferstr_w(space.wrap(u'hello'))
        assert type(s) is str
        assert s == 'hello'
        space.raises_w(space.w_UnicodeEncodeError,
                       space.bufferstr_w, space.wrap(u'\xe9'))


# Note: some app-level tests for buffer are in objspace/std/test/test_memoryview.py.
