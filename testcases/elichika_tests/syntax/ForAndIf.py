# coding: utf-8

import chainer
import chainer.functions as F


class LazyInit(chainer.Chain):
    def forward(self, x):
        y = None
        for i in range(x):
            if y is None:
                y = 42
            y += i
        return y


class LazySelfInit(chainer.Chain):
    def __init__(self):
        super(LazySelfInit, self).__init__()
        self.y = None

    def forward(self, x):
        for i in range(x):
            if self.y is None:
                self.y = 42
            self.y += i
        return self.y


# ======================================


from chainer_compiler.elichika import testtools
import numpy as np


def main():
    testtools.generate_testcase(LazyInit(), [5], subname='lazy_init')

    testtools.generate_testcase(LazySelfInit, [5], subname='lazy_self_init')


if __name__ == '__main__':
    main()
