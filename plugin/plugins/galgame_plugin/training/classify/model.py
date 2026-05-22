from __future__ import annotations

import torch
from torch import nn


FEATURE_DIM = 576


def conv_bn(in_ch: int, out_ch: int, stride: int = 1, kernel: int = 3) -> nn.Sequential:
    padding = kernel // 2
    return nn.Sequential(
        nn.Conv2d(in_ch, out_ch, kernel, stride, padding, bias=False),
        nn.BatchNorm2d(out_ch),
        nn.Hardswish(inplace=True),
    )


def conv_1x1(in_ch: int, out_ch: int) -> nn.Sequential:
    return nn.Sequential(
        nn.Conv2d(in_ch, out_ch, 1, bias=False),
        nn.BatchNorm2d(out_ch),
    )


class InvertedResidual(nn.Module):
    def __init__(self, in_ch: int, out_ch: int, expand_ratio: int, stride: int = 1) -> None:
        super().__init__()
        hidden_dim = in_ch * expand_ratio
        self.use_residual = stride == 1 and in_ch == out_ch
        layers: list[nn.Module] = []
        if expand_ratio != 1:
            layers.extend([conv_1x1(in_ch, hidden_dim), nn.Hardswish(inplace=True)])
        layers.extend(
            [
                nn.Conv2d(hidden_dim, hidden_dim, 3, stride, 1, groups=hidden_dim, bias=False),
                nn.BatchNorm2d(hidden_dim),
                nn.Hardswish(inplace=True),
                nn.Conv2d(hidden_dim, out_ch, 1, bias=False),
                nn.BatchNorm2d(out_ch),
            ]
        )
        self.conv = nn.Sequential(*layers)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        if self.use_residual:
            return x + self.conv(x)
        return self.conv(x)


class GameScreenCNN(nn.Module):
    """Lightweight CNN for galgame screen classification."""

    def __init__(self, num_classes: int = 11, dropout: float = 0.2) -> None:
        super().__init__()
        self.features = self._build_features()
        self.pool = nn.AdaptiveAvgPool2d(1)
        self.classifier = nn.Sequential(
            nn.Linear(FEATURE_DIM, 1024),
            nn.Hardswish(inplace=True),
            nn.Dropout(dropout),
            nn.Linear(1024, num_classes),
        )
        self._init_weights()

    @staticmethod
    def _build_features() -> nn.Sequential:
        return nn.Sequential(
            conv_bn(3, 16, stride=2),
            InvertedResidual(16, 16, expand_ratio=1, stride=2),
            InvertedResidual(16, 24, expand_ratio=4, stride=2),
            InvertedResidual(24, 24, expand_ratio=3, stride=1),
            InvertedResidual(24, 40, expand_ratio=3, stride=2),
            InvertedResidual(40, 40, expand_ratio=3, stride=1),
            InvertedResidual(40, 96, expand_ratio=6, stride=2),
            conv_1x1(96, FEATURE_DIM),
            nn.Hardswish(inplace=True),
        )

    def _init_weights(self) -> None:
        for module in self.modules():
            if isinstance(module, nn.Conv2d):
                nn.init.kaiming_normal_(module.weight, mode="fan_out")
                if module.bias is not None:
                    nn.init.zeros_(module.bias)
            elif isinstance(module, nn.BatchNorm2d):
                nn.init.ones_(module.weight)
                nn.init.zeros_(module.bias)
            elif isinstance(module, nn.Linear):
                nn.init.normal_(module.weight, 0, 0.01)
                nn.init.zeros_(module.bias)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = self.features(x)
        x = self.pool(x)
        x = x.flatten(1)
        return self.classifier(x)
