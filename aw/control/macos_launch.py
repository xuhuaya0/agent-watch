"""Open a URL in one explicit app bundle, without putting chat text in argv."""
import ctypes as c
from contextlib import ExitStack
import os


class LaunchSpec(c.Structure):
    _fields_ = [('appURL', c.c_void_p), ('itemURLs', c.c_void_p),
                ('passThruParams', c.c_void_p), ('launchFlags', c.c_uint32),
                ('asyncRefCon', c.c_void_p)]


def open_url(bundle, url):
    core = c.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
    launch = c.CDLL('/System/Library/Frameworks/CoreServices.framework/CoreServices')
    pointer = c.c_void_p

    def function(name, argtypes, result=pointer):
        fn = getattr(core, name)
        fn.argtypes, fn.restype = argtypes, result
        return fn

    release = function('CFRelease', [pointer], None)
    file_url = function('CFURLCreateFromFileSystemRepresentation', [pointer, c.c_char_p, c.c_long, c.c_bool])
    string = function('CFStringCreateWithCString', [pointer, c.c_char_p, c.c_uint32])
    url_from_string = function('CFURLCreateWithString', [pointer, pointer, pointer])
    array = function('CFArrayCreate', [pointer, c.POINTER(pointer), c.c_long, pointer])
    dispatch = launch.LSOpenFromURLSpec
    dispatch.argtypes, dispatch.restype = [c.POINTER(LaunchSpec), pointer], c.c_int32

    with ExitStack() as stack:
        def own(ref):
            if not ref:
                raise OSError('Unable to prepare native URL')
            stack.callback(release, ref)
            return ref

        path = os.fsencode(bundle)
        app = own(file_url(None, path, len(path), True))
        string_ref = own(string(None, url.encode('utf-8'), 0x08000100))  # UTF-8
        item = own(url_from_string(None, string_ref, None))
        items = own(array(None, (pointer*1)(item), 1, None))
        spec = LaunchSpec(app, items, None, 0x101, None)  # open; omit OS recents
        if dispatch(c.byref(spec), None) != 0:
            raise OSError('Native application could not open the draft')
