import os
import time
from tqdm import tqdm

import numpy as np
import mindspore as ms
from mindspore import save_checkpoint
from mindspore.train.callback._callback import Callback, _handle_loss
from mindocr.utils.visualize import show_img, draw_bboxes, show_imgs, recover_image
from mindocr.utils.recorder import PerfRecorder

__all__ = ['Evaluator', 'EvalSaveCallback']


class Evaluator:
    """
    Args:
        metric:
    """
    def __init__(self, network, loss_fn=None, postprocessor=None, metrics=None, visualize=False, verbose=False,
                 **kwargs):
        self.net = network
        self.postprocessor = postprocessor
        self.metrics = metrics
        self.visualize = visualize
        self.verbose = verbose
        eval_loss = False
        if loss_fn is not None:
            eval_loss = True
            self.loss_fn = loss_fn
        # TODO: add support for computing evaluation loss
        assert eval_loss == False, 'not impl'

    def eval(self, dataloader, num_keys_to_net=1, num_keys_of_labels=None):
        """
        Args:
            dataloader (Dataset): data iterator which generates tuple of Tensor defined by the transform pipeline and 'output_keys'
        """
        eval_res = {}

        self.net.set_train(False)
        self.net.phase = 'eval'
        iterator = dataloader.create_tuple_iterator(num_epochs=1, output_numpy=False, do_copy=False)
        for m in self.metrics:
            m.clear()

        # debug
        # for param in self.net.get_parameters():
        #    print(param.name, param.value().sum())

        for i, data in tqdm(enumerate(iterator), total=dataloader.get_dataset_size()):
            # start = time.time()
            # TODO: if network input is not just an image.
            # assume the first element is image
            img = data[0]  # ms.Tensor(batch[0])
            gt = data[1:]  # ground truth,  (polys, ignore_tags) for det,

            # TODO: in graph mode, the output dict is somehow converted to tuple. Is the order of in tuple the same as dict binary, thresh, thres_binary? to check
            # {'binary':, 'thresh: ','thresh_binary': } for text detect; {'head_out': } for text rec
            net_preds = self.net(img)
            # net_inputs = data[:num_keys_to_net]
            # gt = data[num_keys_to_net:] # ground truth
            # preds = self.net(*net_inputs) # head output is dict. for text det {'binary', ...},  for text rec, {'head_out': }
            # print('net predictions', preds)

            if self.postprocessor is not None:
                preds = self.postprocessor(net_preds)  # {'polygons':, 'scores':} for text det

            # print('GT polys:', gt[0])

            # metric internal update
            for m in self.metrics:
                m.update(preds, gt)

            if self.verbose:
                if isinstance(net_preds, tuple):
                    print('pred binary map:', net_preds[0].shape, net_preds[0].max(), net_preds[0].min())
                    print('thresh binary map:', net_preds[2].shape, net_preds[2].max(), net_preds[2].min())
                else:
                    print('pred binary map:', net_preds['binary'].shape, net_preds['binary'].max(),
                          net_preds['binary'].min())
                    print('thresh binary map:', net_preds['thresh_binary'].shape, net_preds['thresh_binary'].max(),
                          net_preds['binary'].min())
                print('pred polys:', preds['polygons'])

            if self.visualize:
                img = img[0].asnumpy()
                assert ('polys' in preds) or ('polygons' in preds), 'Only support detection'
                gt_img_polys = draw_bboxes(recover_image(img), gt[0].asnumpy())
                pred_img_polys = draw_bboxes(recover_image(img), preds['polygons'].asnumpy())
                show_imgs([gt_img_polys, pred_img_polys], show=False, save_path=f'results/det_vis/gt_pred_{i}.png')

        # TODO: loss, add loss val to eval_res
        for m in self.metrics:
            res_dict = m.eval()
            eval_res.update(res_dict)
        # fps = total_frame / total_time

        # TODO: set_train outside
        self.net.set_train(True)
        self.net.phase = 'train'

        return eval_res

    # TODO: add checkpoint save manager for better modulation


# class ModelSavor()

class EvalSaveCallback(Callback):
    """
    Callbacks for evaluation while training

    Args:
        network (nn.Cell): network (without loss)
        loader (Dataset): dataloader
        saving_config (dict):
    """
    def __init__(self,
                 network,
                 loader,
                 loss_fn=None,
                 postprocessor=None,
                 metrics=None,
                 rank_id=None,
                 ckpt_save_dir='./',
                 main_indicator='hmean',
                 ):
        self.rank_id = rank_id
        if rank_id in [None, 0]:
            self.network = network
            self.net_evaluator = Evaluator(network, loss_fn, postprocessor, metrics)
            self.loader_eval = loader
            self.ckpt_save_dir = ckpt_save_dir
            if not os.path.exists(ckpt_save_dir):
                os.makedirs(ckpt_save_dir)
            self.main_indicator = main_indicator
            self.best_perf = -1
            self._losses = []

    # def __enter__(self):
    #    pass

    # def __exit__(self, *exc_args):
    #    pass

    def on_train_step_begin(self, run_context):
        self.step_start_time = time.time()

    def on_train_step_end(self, run_context):
        """
        Print training loss at the end of step.

        Args:
            run_context (RunContext): Context of the train running.
        """
        cb_params = run_context.original_args()
        loss = _handle_loss(cb_params.net_outputs)
        cur_epoch = cb_params.cur_epoch_num
        data_sink_mode = cb_params.dataset_sink_mode
        cur_step_in_epoch = (cb_params.cur_step_num - 1) % cb_params.batch_num + 1

        # TODO: need to stop gradient here ?
        self._losses.append(loss.asnumpy())

    def on_train_epoch_begin(self, run_context):
        """
        Called before each epoch beginning.
        Args:
            run_context (RunContext): Include some information of the model.
        """
        self._losses = []
        self.epoch_start_time = time.time()

    def on_train_epoch_end(self, run_context):
        """
        Called after each training epoch end.

        Args:
            run_context (RunContext): Include some information of the model.
        """
        cb_params = run_context.original_args()
        loss = cb_params.net_outputs
        cur_epoch = cb_params.cur_epoch_num
        epoch_time = (time.time() - self.epoch_start_time)
        train_loss = np.average(self._losses)

        # TODO: add lr print 
        '''
        optimizer = cb_params.optimizer
        step = optimizer.global_step
        if optimizer.dynamic_lr:
            cur_lr = optimizer.learning_rate(step - 1)[0].asnumpy()
        else:
            cur_lr = optimizer.learning_rate.asnumpy()
        loss = self._get_loss(cb_params)
        '''

        print(
            f"Epoch: {cur_epoch}, "
            f"loss:{train_loss:.5f}, time:{epoch_time:.3f}s"
        )

        if self.rank_id in [0, None]:
            # evaluate
            measures = self.net_evaluator.eval(self.loader_eval)
            print('Performance: ', measures)

            perf = measures[self.main_indicator]
            if perf > self.best_perf:
                self.best_perf = perf
                save_checkpoint(self.network, os.path.join(self.ckpt_save_dir, 'best.ckpt'))
                print(f'=> best {self.main_indicator}: {perf}, checkpoint saved.')
            # record results
            if cur_epoch == 1:
                metric_names = ['loss'] + list(measures.keys()) + ['epoch_time']
                self.rec = PerfRecorder(self.ckpt_save_dir, metric_names=metric_names)
            self.rec.add(cur_epoch, train_loss, *list(measures.values()), epoch_time)

    def on_train_end(self, run_context):
        if self.rank_id in [0, None]:
            self.rec.save_curves()  # save performance curve figure
            print(f'=> best {self.main_indicator}: {self.best_perf} \nTraining completed!')
