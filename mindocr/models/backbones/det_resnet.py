from typing import Tuple, List
from mindspore import Tensor
from mindcv.models.resnet import ResNet, Bottleneck, default_cfgs
from mindcv.models.utils import load_pretrained
from ._registry import register_backbone, register_backbone_class

__all__ = ['DetResNet', 'det_resnet50']


@register_backbone_class
class DetResNet(ResNet):
    def __init__(self, block, layers, **kwargs):
        super().__init__(block, layers, **kwargs)
        del self.pool, self.classifier  # remove the original header to avoid confusion
        # self.out_indices = out_indices
        self.out_channels = [ch * block.expansion for ch in [64, 128, 256, 512]]

    def construct(self, x: Tensor) -> List[Tensor]:
        x = self.conv1(x)
        x = self.bn1(x)
        x = self.relu(x)
        x = self.max_pool(x)
        ''' 
        ftrs = []
        for i, layer in enumerate([self.layer1, self.layer2,  self.layer3, self.layer4]):
            x = layer(x)
            if i in self.out_indices:
                ftrs.append(x)
                self.out_channels.append()
        '''
        x1 = self.layer1(x)
        x2 = self.layer2(x1)
        x3 = self.layer3(x2)
        x4 = self.layer4(x3)

        return [x1, x2, x3, x4]

    # def construct(self, x: Tensor) -> List[Tensor]:
    #    return self.forward_features(x)


# TODO: load pretrained weight in build_backbone or use a unify wrapper to load

@register_backbone
def det_resnet50(pretrained: bool = True, **kwargs):
    model = DetResNet(Bottleneck, [3, 4, 6, 3], **kwargs)

    # load pretrained weights
    if pretrained:
        default_cfg = default_cfgs['resnet50']
        load_pretrained(model, default_cfg)

    return model
