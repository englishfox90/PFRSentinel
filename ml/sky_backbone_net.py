#!/usr/bin/env python3
"""
Sky/celestial classifier network on an ImageNet-pretrained torchvision trunk.

Model definition only, shared by the trainer (ml/train_sky_backbone.py) and the
.pth branch of ml.sky_classifier. Production inference goes through the ONNX
export, which carries the input normalisation inside the graph, so this module
is never imported by the shipped app.
"""
import torch
import torch.nn as nn
import torch.nn.functional as F
import torchvision

from ml.sky_dataset import SKY_CONDITIONS

ARCHS = ('resnet18', 'efficientnet_b0')

# ImageNet statistics collapsed to one grey channel. Applied inside the model so
# the [0, 1] arcsinh-stretched input contract of SkyClassifier stays unchanged.
GREY_MEAN = 0.449
GREY_STD = 0.226


def _collapse_to_one_channel(conv: nn.Conv2d) -> nn.Conv2d:
    """Replace an RGB stem conv with a 1-channel one carrying the summed weights.

    Summing the three input filters is exactly the response the RGB conv would
    give to a grey image replicated across channels, so the pretrained features
    survive the change.
    """
    new = nn.Conv2d(1, conv.out_channels, conv.kernel_size, conv.stride,
                    conv.padding, bias=conv.bias is not None)
    with torch.no_grad():
        new.weight.copy_(conv.weight.sum(dim=1, keepdim=True))
        if conv.bias is not None:
            new.bias.copy_(conv.bias)
    return new


class BackboneSkyNet(nn.Module):
    """Pretrained CNN trunk + the production model's metadata fusion and heads.

    Same forward signature and outputs as SkyClassifierCNN:
    (image, metadata) -> (sky_logits, stars_logit, density, moon_logit).
    """

    def __init__(self, arch: str = 'resnet18', metadata_features: int = 6,
                 pretrained: bool = True):
        super().__init__()
        self.arch = arch
        if arch == 'resnet18':
            weights = torchvision.models.ResNet18_Weights.IMAGENET1K_V1 if pretrained else None
            net = torchvision.models.resnet18(weights=weights)
            net.conv1 = _collapse_to_one_channel(net.conv1)
            feat_dim = net.fc.in_features
            net.fc = nn.Identity()
        elif arch == 'efficientnet_b0':
            weights = torchvision.models.EfficientNet_B0_Weights.IMAGENET1K_V1 if pretrained else None
            net = torchvision.models.efficientnet_b0(weights=weights)
            net.features[0][0] = _collapse_to_one_channel(net.features[0][0])
            feat_dim = net.classifier[1].in_features
            net.classifier = nn.Identity()
        else:
            raise ValueError(f"unknown arch {arch!r}; choose from {ARCHS}")
        self.backbone = net
        self.register_buffer('mean', torch.tensor(GREY_MEAN))
        self.register_buffer('std', torch.tensor(GREY_STD))

        self.dropout = nn.Dropout(0.3)
        self.fc_image = nn.Linear(feat_dim, 256)
        self.fc_meta = nn.Linear(metadata_features, 32)
        self.fc_fusion = nn.Linear(256 + 32, 128)
        self.head_sky = nn.Linear(128, len(SKY_CONDITIONS))
        self.head_stars = nn.Linear(128, 1)
        self.head_density = nn.Linear(128, 1)
        self.head_moon = nn.Linear(128, 1)

    def head_parameters(self):
        for name, p in self.named_parameters():
            if not name.startswith('backbone.'):
                yield p

    def forward(self, image, metadata):
        x = (image - self.mean) / self.std
        f = self.backbone(x)
        f = F.relu(self.fc_image(self.dropout(f)))
        m = F.relu(self.fc_meta(metadata))
        z = self.dropout(F.relu(self.fc_fusion(torch.cat([f, m], dim=1))))
        # Density is a 0-1 value, sigmoid'd in the graph like SkyClassifierCNN:
        # ml.sky_classifier and services.ml_service read it as-is, unclamped.
        # The stars and moon heads stay logits — those callers apply the sigmoid.
        density = torch.sigmoid(self.head_density(z))
        return self.head_sky(z), self.head_stars(z), density, self.head_moon(z)
