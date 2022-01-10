import itertools

import numpy as np
import torch
from torch import nn
from torch.nn import functional as nnf, Flatten
from torch.optim import AdamW
from torchvision.datasets import MNIST, CIFAR10

from lib.logger import Logger
from lib.plotter import run_with_plotter
from quantized.binary import BLinear, BSign, BConv2d
from quantized.quant_bits import QLinear

DEVICE = "cuda"


def build_network(i_s: int, i_c: int, o_c: int, clamp_sign_grad: bool, clamp_weight_grad: bool):
    return nn.Sequential(
        BSign(clamp_sign_grad),

        BConv2d(i_c, 8, 3, clamp_weight_grad),
        BSign(clamp_sign_grad),

        BConv2d(8, 8, 3, clamp_weight_grad),
        BSign(clamp_sign_grad),
        Flatten(),

        BLinear(i_s * i_s * 8, o_c, clamp_weight_grad),
    )


def eval(network, x, y_target):
    y = network(x) / 4
    loss = nnf.cross_entropy(y, y_target)
    acc = (torch.argmax(y, dim=1) == y_target).sum() / len(y_target)
    return loss, acc, y


def dataset_to_tensors(dataset):
    if isinstance(dataset, MNIST):
        x = dataset.data.unsqueeze(1).float() / 255
        y = dataset.targets
    elif isinstance(dataset, CIFAR10):
        x = torch.tensor(np.moveaxis(dataset.data, 3, 1), dtype=torch.float) / 255
        y = torch.tensor(dataset.targets, dtype=torch.int64)
    else:
        assert False, f"Unknown dataset type '{dataset}'"

    return x.to(DEVICE), y.to(DEVICE)


def sample(data, batch_size):
    x, y = data
    assert len(x) == len(y)
    i = torch.randint(len(x), (batch_size,))
    return x[i], y[i]


def log_param_scale(logger: Logger, name: str, param):
    logger.log("scale", f"{name} min", param.min())
    logger.log("scale", f"{name} mean", param.mean())
    logger.log("scale", f"{name} max", param.max())


def train(network, opt, batch_size, max_batch_count, train_data, test_data, plotter):
    logger = Logger()

    for bi in itertools.count():
        if max_batch_count is not None and bi >= max_batch_count:
            break

        plotter.block_while_paused()
        logger.start_batch()

        network.eval()
        test_x, test_y_target = sample(train_data, batch_size)
        test_loss, test_acc, test_y = eval(network, test_x, test_y_target)

        logger.log("act", "output min", test_y.min())
        logger.log("act", "output max", test_y.max())
        logger.log("act", "output mean", test_y.mean())
        logger.log("act", "output std", test_y.std())

        network.train()
        train_x, train_y_target = sample(test_data, batch_size)
        train_loss, train_acc, _ = eval(network, train_x, train_y_target)

        opt.zero_grad(set_to_none=True)
        train_loss.backward()
        opt.step()

        logger.log("acc", "test", test_acc.item())
        logger.log("acc", "train", train_acc.item())
        logger.log("loss", "test", test_loss.item())
        logger.log("loss", "train", train_loss.item())

        for (mi, module) in enumerate(network.modules()):
            if isinstance(module, BLinear):
                log_param_scale(logger, f"{mi} w", module.weight)
            if isinstance(module, QLinear):
                log_param_scale(logger, f"{mi} w", module.linear.weight)
                if module.linear.bias is not None:
                    log_param_scale(logger, f"{mi} b", module.linear.bias)
            if isinstance(module, nn.Linear):
                log_param_scale(logger, f"{mi} w", module.weight)
                if module.bias is not None:
                    log_param_scale(logger, f"{mi} b", module.bias)

        plotter.update(logger)

    return logger


def main(plotter):
    train_data = dataset_to_tensors(CIFAR10("../ignored/data", train=True, download=True))
    test_data = dataset_to_tensors(CIFAR10("../ignored/data", train=False, download=True))

    (_, c_i, _, s_i) = train_data[0].shape
    o_c = train_data[1].max() + 1
    print(f"Image shape {c_i}x{s_i}x{s_i}, {o_c} categories")

    batch_size = 256
    max_batch_count = None

    network = build_network(s_i, c_i, o_c, False, False)
    network.to(DEVICE)

    # opt = SGD(network.parameters(), lr=1e-5, momentum=0.9, weight_decay=1e-3)
    opt = AdamW(network.parameters(), weight_decay=1e-3)

    train(network, opt, batch_size, max_batch_count, train_data, test_data, plotter)


if __name__ == '__main__':
    run_with_plotter(main)
