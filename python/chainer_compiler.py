import chainer
import os
import sys
import tempfile

import ch2o
import chainer_compiler_core


def _is_array(v):
    return not isinstance(v, (list, tuple, range, dict))


def _flatten(xs):
    if _is_array(xs):
        return [xs]

    o = []
    for x in xs:
        if _is_array(x):
            o.append(x)
        else:
            o.extend(_flatten(x))
    return o


def _flatten_structured(xs, tmpl):
    o = []
    for x, t in zip(xs, tmpl):
        if _is_array(t):
            assert _is_array(x)
            o.append(x)
        else:
            assert not _is_array(x), '%s vs %s' % (x, t)
            if len(x) == len(t):
                o.extend(_flatten_structured(x, t))
            elif len(x) == 0:
                o.extend([None] * len(t))
            else:
                raise RuntimeError('%s vs %s' % (x, t))
    return o


def _unflatten(xs, tmpl, i=0):
    o = []
    for t in tmpl:
        if _is_array(t):
            o.append(xs[i])
            i += 1
        else:
            no, i = _unflatten(xs, t, i)
            o.append(no)
    return type(tmpl)(o), i


def _from_var(v, device):
    if v.is_array():
        return device.send(v.array())
    return [_from_var(x, device) for x in v.sequence()]


class RunCompiledModelTwoPhase(chainer.function_node.FunctionNode):

    def __init__(self, compiled_model, input_tmpl):
        self.fwd_input_names = compiled_model.fwd_input_names
        self.fwd_output_names = compiled_model.fwd_output_names
        self.bwd_input_names = compiled_model.bwd_input_names
        self.bwd_output_names = compiled_model.bwd_output_names
        self.param_names = compiled_model.param_names
        self.fwd = compiled_model.fwd
        self.bwd = compiled_model.bwd
        self.num_outputs = len(compiled_model.orig_output_names)
        self.input_tmpl = input_tmpl
        self.num_inputs = len(_flatten(input_tmpl))
        self.chainerx_device_name = None

    def _to_var(self, v):
        if _is_array(v):
            if isinstance(v, chainer.Variable):
                v = v.array
            v = chainer.backend.to_chx(v)
            if self.chainerx_device_name is None:
                self.chainerx_device_name = v.device
            else:
                assert self.chainerx_device_name == v.device
            return chainer_compiler_core.value(v)
        return chainer_compiler_core.value([self._to_var(a) for a in v])

    def forward(self, args):
        flat_inputs = args[:self.num_inputs]
        param_values = args[self.num_inputs:]
        device = chainer.backend.get_device_from_array(*flat_inputs)
        inputs, i = _unflatten(flat_inputs, self.input_tmpl)
        assert i == len(flat_inputs)

        entire_inputs = {}
        assert len(self.fwd_input_names) == len(inputs)
        for name, value in zip(self.fwd_input_names, inputs):
            entire_inputs[name] = self._to_var(value)
        assert len(self.param_names) == len(param_values)
        for name, value in zip(self.param_names, param_values):
            entire_inputs[name] = self._to_var(value)

        with chainer.using_device(self.chainerx_device_name):
            outputs = self.fwd.run(entire_inputs)
        outputs_and_retained = []
        for name in self.fwd_output_names:
            outputs_and_retained.append(outputs[name])

        self.retained = outputs_and_retained[self.num_outputs:]
        # TODO(hamaji): Do not hold actual arrays.
        self.nested_outputs = []
        for output in outputs_and_retained[:self.num_outputs]:
            self.nested_outputs.append(_from_var(output, device))
        flat_outputs = _flatten(self.nested_outputs)
        print(flat_outputs)
        return tuple(flat_outputs)

    def unflatten_outputs(self, flat_outputs):
        outputs, _ = _unflatten(flat_outputs, self.nested_outputs)
        return outputs

    def backward(self, indexes, flat_gys):
        device = chainer.backend.get_device_from_array(flat_gys[0].array)
        gys, _ = _unflatten(flat_gys, self.nested_outputs)
        retained = self.retained
        gys = [self._to_var(gy) for gy in gys]
        values = gys + retained

        del self.retained
        del self.nested_outputs

        inputs = {}
        assert len(self.bwd_input_names) == len(values)
        for name, value in zip(self.bwd_input_names, values):
            inputs[name] = value

        with chainer.using_device(self.chainerx_device_name):
            outputs = self.bwd.run(inputs)
        gxs = []
        assert len(self.input_tmpl) == len(self.fwd_input_names)
        for name, tmpl in zip(self.fwd_input_names, self.input_tmpl):
            grad_name = 'grad_out@' + name
            if grad_name in outputs:
                gx = _from_var(outputs[grad_name], device)
                if _is_array(tmpl):
                    gxs.append(gx)
                else:
                    assert len(gx) == len(tmpl)
                    gxs.extend(_flatten_structured(gx, tmpl))
            else:
                gxs.extend([None] * len(_flatten(tmpl)))

        for name in self.param_names:
            grad_name = 'grad_out@' + name
            if grad_name in outputs:
                gx = _from_var(outputs[grad_name], device)
                gxs.append(gx)
            else:
                gxs.extend([None])

        gxs = tuple(None if gx is None else chainer.Variable(gx) for gx in gxs)
        return gxs


class RunCompiledModelOnePhase(chainer.function_node.FunctionNode):

    def __init__(self, compiled_model, input_tmpl):
        self.fwd_input_names = compiled_model.fwd_input_names
        self.fwd_output_names = compiled_model.fwd_output_names
        self.param_names = compiled_model.param_names
        self.fwd_bwd = compiled_model.fwd_bwd
        self.input_tmpl = input_tmpl
        self.num_inputs = len(_flatten(input_tmpl))
        self.chainerx_device_name = None

    def _to_var(self, v):
        # TODO(mkusumoto): Stop copy&paste from two phase implementation
        if _is_array(v):
            if isinstance(v, chainer.Variable):
                v = v.array
            v = chainer.backend.to_chx(v)
            if self.chainerx_device_name is None:
                self.chainerx_device_name = v.device
            else:
                assert self.chainerx_device_name == v.device
            return chainer_compiler_core.value(v)
        return chainer_compiler_core.value([self._to_var(a) for a in v])

    def forward(self, args):
        flat_inputs = args[:self.num_inputs]
        param_values = args[self.num_inputs:]
        device = chainer.backend.get_device_from_array(*flat_inputs)
        inputs, i = _unflatten(flat_inputs, self.input_tmpl)
        assert i == len(flat_inputs)

        entire_inputs = {}
        assert len(self.fwd_input_names) == len(inputs)
        for name, value in zip(self.fwd_input_names, inputs):
            entire_inputs[name] = self._to_var(value)
        assert len(self.param_names) == len(param_values)
        for name, value in zip(self.param_names, param_values):
            entire_inputs[name] = self._to_var(value)

        with chainer.using_device(self.chainerx_device_name):
            entire_outputs = self.fwd_bwd.run(entire_inputs)
        print(entire_outputs['grad_out@param_l1_b'])
        forward_outputs = [entire_outputs[name] for name
                           in self.fwd_output_names]

        # TODO(hamaji): Do not hold actual arrays.
        self.nested_outputs = [_from_var(output, device) for output
                               in forward_outputs]
        flat_outputs = _flatten(self.nested_outputs)

        self._summarize_gradients(entire_outputs, device)

        return tuple(flat_outputs)

    def unflatten_outputs(self, flat_outputs):
        outputs, _ = _unflatten(flat_outputs, self.nested_outputs)
        return outputs

    def _summarize_gradients(self, entire_outputs, device):
        # TODO(mkusumoto): Stop copy&paste from one phase impl
        gxs = []
        assert len(self.input_tmpl) == len(self.fwd_input_names)
        for name, tmpl in zip(self.fwd_input_names, self.input_tmpl):
            grad_name = 'grad_out@' + name
            if grad_name in entire_outputs:
                gx = _from_var(entire_outputs[grad_name], device)
                if _is_array(tmpl):
                    gxs.append(gx)
                else:
                    assert len(gx) == len(tmpl)
                    gxs.extend(_flatten_structured(gx, tmpl))
            else:
                gxs.extend([None] * len(_flatten(tmpl)))

        for name in self.param_names:
            grad_name = 'grad_out@' + name
            if grad_name in entire_outputs:
                gx = _from_var(entire_outputs[grad_name], device)
                gxs.append(gx)
            else:
                gxs.extend([None])

        gxs = tuple(None if gx is None else gx for gx in gxs)
        self.retained_grads = gxs

    def backward(self, indexes, flat_gys):
        # print([type(g)
        # for g in self.retained_grads])
        return tuple(None if g is None else chainer.Variable(g)
                     for g in self.retained_grads)


class CompiledModel(chainer.Chain):

    def __init__(self, model, inputs, translator='ch2o',
                 backprop_two_phase=True, dump_onnx=False):
        super(CompiledModel, self).__init__()
        with self.init_scope():
            self.mc = model
        self.translator = translator
        self.backprop_two_phase = backprop_two_phase
        self.dump_onnx = dump_onnx

        self.compiled = False
        self.param_names = None
        self.param_values = None
        if inputs is not None:
            self.compile(inputs)

    def compile(self, inputs):
        if self.translator == 'ch2o':
            xmodel = ch2o.compile_model(self.mc, inputs)
            f = tempfile.NamedTemporaryFile(delete=False)
            f.write(xmodel.SerializeToString())
            f.close()
            del xmodel
        elif self.translator == 'onnx_chainer':
            import onnx_chainer
            f = tempfile.NamedTemporaryFile(delete=False)
            onnx_chainer.export(self.mc, inputs, filename=f)
            f.close()
        else:
            raise NotImplementedError('Unsupported translator:',
                                      self.translator)

        graph = chainer_compiler_core.load(f.name)
        os.unlink(f.name)

        self.orig_output_names = graph.output_names()

        if self.backprop_two_phase:
            fwd_graph, bwd_graph = graph.backward_to(
                graph.input_names() + graph.param_names())
            if self.dump_onnx:
                sys.stderr.write('=== vvv forward vvv ===\n' +
                                 fwd_graph.dump() +
                                 '\n=== ^^^ forward ^^^ ===\n')
                sys.stderr.write('=== vvv backward vvv ===\n' +
                                 bwd_graph.dump() +
                                 '\n=== ^^^ backward ^^^ ===\n')
        else:
            fwd_graph = graph
            if self.dump_onnx:
                sys.stderr.write('=== vvv forward vvv ===\n' +
                                 fwd_graph.dump() +
                                 '\n=== ^^^ forward ^^^ ===\n')

        assert graph.input_names() == fwd_graph.input_names()
        self.fwd_input_names = fwd_graph.input_names()
        self.fwd_output_names = fwd_graph.output_names()
        if self.backprop_two_phase:
            self.bwd_input_names = bwd_graph.input_names()
            self.bwd_output_names = bwd_graph.output_names()
            # TODO(hamaji): Revive shape inference.
            self.fwd = fwd_graph.compile(skip_inference=True)
            self.bwd = bwd_graph.compile(skip_inference=True)
        else:
            self.fwd_bwd = fwd_graph.compile(backprop=True,
                                             skip_inference=True)
        self.param_names = fwd_graph.param_names()

        self.compiled = True

    def forward(self, *args):
        if not self.compiled:
            outputs = self.mc(*args)
            self.compile(args)
            return outputs

        if self.param_values is None:
            assert self.param_names is not None
            params = dict(self.mc.namedparams())
            if self.translator == 'onnx_chainer':
                params = {'param' + key.replace('/', '_'): value for key, value
                          in params.items()}
            self.param_values = []
            for name in self.param_names:
                assert name in params
                self.param_values.append(params[name])

        inputs = list(args)
        flat_inputs = _flatten(inputs)
        if self.backprop_two_phase:
            runner = RunCompiledModelTwoPhase(self, inputs)
        else:
            runner = RunCompiledModelOnePhase(self, inputs)
        outputs = runner.apply(flat_inputs + self.param_values)
        outputs = runner.unflatten_outputs(outputs)
        outputs = outputs[:len(self.orig_output_names)]
        if len(outputs) == 1:
            outputs = outputs[0]
        return outputs


def compile(model, inputs=None, **kwargs):
    return CompiledModel(model, inputs, **kwargs)
