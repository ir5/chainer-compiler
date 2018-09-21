import os


class TestCase(object):

    def __init__(self, dirname, name, rtol=None, fail=False,
                 skip_shape_inference=False,
                 always_retain_in_stack=False):
        self.dirname = dirname
        self.name = name
        self.rtol = rtol
        self.fail = fail
        self.skip_shape_inference = skip_shape_inference
        self.always_retain_in_stack = always_retain_in_stack
        self.test_dir = os.path.join(self.dirname, self.name)
        self.args = None
        self.is_backprop = 'backprop_' in name
