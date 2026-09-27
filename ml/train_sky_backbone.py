#!/usr/bin/env python3
"""
Sky/celestial classifier on an ImageNet-pretrained backbone (experiment).

Same data, split, preprocessing, losses and ONNX contract as
train_sky_classifier.py, so a run here is directly comparable with the
production from-scratch CNN and its ONNX file loads through ml.sky_classifier
unchanged. What differs is the feature extractor: a torchvision ResNet18 or
EfficientNet-B0 with ImageNet weights, first conv collapsed to one channel.

Why: after the 2026-09-27 relabel, validation loss rose (0.51 -> 0.72) while
the from-scratch model stayed at ~70% on frames it had never seen. Thin cloud
against a clear sky is a texture judgement, and 1,600 frames is not enough to
learn texture features from nothing. ImageNet features already have them.

Usage:
    python ml/train_sky_backbone.py                       # resnet18, 30 epochs
    python ml/train_sky_backbone.py --arch efficientnet_b0
    python ml/train_sky_backbone.py --no-pretrained       # ablation: same net, random init

Outputs land in --output-dir (default ml/models/_backbone_sweep/<arch>/, gitignored).
Score the ONNX against the production model on the same held-out split with the
per-split evaluator used in the 2026-09 retrain session.
"""
import argparse
import copy
import random
import sys
import time
import warnings
from collections import Counter
from datetime import datetime
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
from torch.amp import GradScaler, autocast
from torch.utils.data import DataLoader

sys.path.insert(0, str(Path(__file__).parent.parent))
from ml.sky_backbone_net import ARCHS, BackboneSkyNet  # noqa: E402
from ml.sky_dataset import SKY_CONDITIONS, SKY_TO_IDX, SkyDataset  # noqa: E402
from ml.train_sky_classifier import SEED, _split_samples, load_dataset  # noqa: E402

warnings.filterwarnings('ignore', category=DeprecationWarning, module='torch.onnx')

# Same multi-task weighting as the production trainer, so loss numbers compare.
LOSS_WEIGHTS = {'sky': 1.0, 'stars': 0.5, 'density': 0.3, 'moon': 0.5}

def _sky_class_weights(train_samples, device):
    """sqrt-softened inverse frequency, mean 1 — as in train_sky_classifier."""
    counts = Counter(SKY_TO_IDX[s['sky_condition']] for s in train_samples)
    n = len(SKY_CONDITIONS)
    raw = [(len(train_samples) / (n * max(counts.get(i, 0), 1))) ** 0.5 for i in range(n)]
    mean = sum(raw) / n
    return torch.tensor([w / mean for w in raw], dtype=torch.float32, device=device)


def _batch_loss(model, batch, crit, device, use_amp):
    images = batch['image'].to(device, non_blocking=True)
    metadata = batch['metadata'].to(device, non_blocking=True)
    sky = batch['sky_condition'].to(device, non_blocking=True)
    stars = batch['stars_visible'].to(device, non_blocking=True)
    density = batch['star_density'].to(device, non_blocking=True)
    moon = batch['moon_visible'].to(device, non_blocking=True)
    with autocast('cuda', enabled=use_amp):
        sky_logits, stars_logit, dens, moon_logit = model(images, metadata)
        loss = (LOSS_WEIGHTS['sky'] * crit['sky'](sky_logits, sky)
                + LOSS_WEIGHTS['stars'] * crit['stars'](stars_logit.squeeze(-1), stars)
                + LOSS_WEIGHTS['density'] * crit['density'](dens.squeeze(-1), density)
                + LOSS_WEIGHTS['moon'] * crit['moon'](moon_logit.squeeze(-1), moon))
    return loss, (sky_logits, stars_logit, moon_logit), (sky, stars, moon)


@torch.no_grad()
def evaluate(model, loader, crit, device, use_amp):
    """Mean loss plus sky / stars / moon accuracy and a per-class sky tally."""
    model.eval()
    total_loss = 0.0
    n = 0
    sky_ok = stars_ok = moon_ok = 0
    per_class = {c: [0, 0] for c in SKY_CONDITIONS}
    for batch in loader:
        loss, (sky_logits, stars_logit, moon_logit), (sky, stars, moon) = _batch_loss(
            model, batch, crit, device, use_amp)
        total_loss += loss.item()
        sky_pred = sky_logits.argmax(dim=1)
        sky_ok += (sky_pred == sky).sum().item()
        stars_ok += ((torch.sigmoid(stars_logit.squeeze(-1)) > 0.5).float() == stars).sum().item()
        moon_ok += ((torch.sigmoid(moon_logit.squeeze(-1)) > 0.5).float() == moon).sum().item()
        for t, p in zip(sky.tolist(), sky_pred.tolist()):
            per_class[SKY_CONDITIONS[t]][1] += 1
            per_class[SKY_CONDITIONS[t]][0] += int(t == p)
        n += sky.size(0)
    return {
        'loss': total_loss / max(len(loader), 1),
        'sky': sky_ok / n, 'stars': stars_ok / n, 'moon': moon_ok / n,
        'per_class': per_class, 'n': n,
    }


def export_onnx(model, image_size, path, device):
    model.eval()
    dummy_image = torch.randn(1, 1, image_size, image_size, device=device)
    dummy_meta = torch.randn(1, 6, device=device)
    torch.onnx.export(
        model, (dummy_image, dummy_meta), str(path),
        input_names=['image', 'metadata'],
        output_names=['sky_condition', 'stars_visible', 'star_density', 'moon_visible'],
        dynamic_axes={'image': {0: 'batch'}, 'metadata': {0: 'batch'},
                      'sky_condition': {0: 'batch'}, 'stars_visible': {0: 'batch'},
                      'star_density': {0: 'batch'}, 'moon_visible': {0: 'batch'}},
        opset_version=18, dynamo=False,
    )


def train(args):
    random.seed(SEED)
    np.random.seed(SEED)
    torch.manual_seed(SEED)

    pier, _ = load_dataset(Path(args.data_dir))
    train_s, val_s, test_s = _split_samples(pier, 0.15)
    print(f"\nSplit: {len(train_s)} train / {len(val_s)} val / {len(test_s)} test (pier, roof open)")
    print("Train sky:", dict(Counter(s['sky_condition'] for s in train_s)))

    print("\nPreloading...")
    train_ds = SkyDataset(train_s, image_size=args.image_size, augment=True, preload=True)
    val_ds = SkyDataset(val_s, image_size=args.image_size, augment=False, preload=True)
    test_ds = SkyDataset(test_s, image_size=args.image_size, augment=False, preload=True)
    train_loader = DataLoader(train_ds, batch_size=args.batch_size, shuffle=True, pin_memory=True)
    val_loader = DataLoader(val_ds, batch_size=args.batch_size, shuffle=False, pin_memory=True)
    test_loader = DataLoader(test_ds, batch_size=args.batch_size, shuffle=False, pin_memory=True)

    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    use_amp = device.type == 'cuda'
    model = BackboneSkyNet(args.arch, pretrained=not args.no_pretrained).to(device)
    n_params = sum(p.numel() for p in model.parameters())
    print(f"\n{args.arch} ({'ImageNet' if not args.no_pretrained else 'random init'}), "
          f"{n_params:,} params, image_size={args.image_size}, device={device}")

    crit = {
        'sky': nn.CrossEntropyLoss(weight=_sky_class_weights(train_s, device)),
        'stars': nn.BCEWithLogitsLoss(),
        'moon': nn.BCEWithLogitsLoss(),
        'density': nn.MSELoss(),
    }
    # Pretrained trunk gets a tenth of the head learning rate: it already knows
    # edges and texture, and a full-rate update at the start would wipe that out
    # before the randomly initialised heads have settled.
    backbone_lr = args.lr if args.no_pretrained else args.lr * 0.1
    optimizer = torch.optim.AdamW([
        {'params': model.backbone.parameters(), 'lr': backbone_lr},
        {'params': list(model.head_parameters()), 'lr': args.lr},
    ], weight_decay=0.01)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=args.epochs)
    scaler = GradScaler('cuda', enabled=use_amp)

    best = {'loss': float('inf'), 'epoch': 0, 'state': None}
    t0 = time.time()
    print(f"\nTraining {args.epochs} epochs, batch {args.batch_size}, "
          f"lr {args.lr} (backbone {backbone_lr}), backbone frozen for {args.freeze_epochs}")
    for epoch in range(args.epochs):
        model.train()
        frozen = epoch < args.freeze_epochs
        for p in model.backbone.parameters():
            p.requires_grad_(not frozen)
        train_loss = 0.0
        for batch in train_loader:
            optimizer.zero_grad(set_to_none=True)
            loss, _, _ = _batch_loss(model, batch, crit, device, use_amp)
            scaler.scale(loss).backward()
            scaler.step(optimizer)
            scaler.update()
            train_loss += loss.item()
        train_loss /= len(train_loader)
        scheduler.step()

        val = evaluate(model, val_loader, crit, device, use_amp)
        marker = ''
        if val['loss'] < best['loss']:
            best = {'loss': val['loss'], 'epoch': epoch + 1,
                    'state': copy.deepcopy(model.state_dict())}
            marker = ' *'
        print(f"Epoch {epoch + 1:3d}/{args.epochs}: train {train_loss:.4f}  val {val['loss']:.4f}  "
              f"sky {val['sky'] * 100:.1f}%  stars {val['stars'] * 100:.1f}%  "
              f"moon {val['moon'] * 100:.1f}%  [{time.time() - t0:.0f}s]{marker}", flush=True)

    model.load_state_dict(best['state'])
    out_dir = Path(args.output_dir) / args.arch
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = out_dir / 'sky_classifier_v1'

    test = evaluate(model, test_loader, crit, device, use_amp)
    print(f"\n{'=' * 60}\nHeld-out test (best epoch {best['epoch']}, val loss {best['loss']:.4f})")
    print(f"  Sky {test['sky'] * 100:.1f}%  " + "  ".join(
        f"{c} {k}/{m}" for c, (k, m) in test['per_class'].items()))
    print(f"  Stars {test['stars'] * 100:.1f}%  Moon {test['moon'] * 100:.1f}%  (n={test['n']})")

    torch.save({
        'model_state_dict': best['state'], 'arch': args.arch, 'image_size': args.image_size,
        'metadata_features': 6, 'sky_conditions': SKY_CONDITIONS, 'pretrained': not args.no_pretrained,
        'trained_at': datetime.now().isoformat(), 'best_epoch': best['epoch'],
        'test': {k: v for k, v in test.items() if k != 'per_class'},
    }, stem.with_suffix('.pth'))
    export_onnx(model, args.image_size, stem.with_suffix('.onnx'), device)
    print(f"\nSaved {stem.with_suffix('.pth')} and {stem.with_suffix('.onnx')}")


def main():
    parser = argparse.ArgumentParser(description="Sky classifier on a pretrained backbone")
    parser.add_argument("--data-dir", default=r"D:\Pier Camera ML Data")
    parser.add_argument("--output-dir", default="ml/models/_backbone_sweep")
    parser.add_argument("--arch", choices=ARCHS, default='resnet18')
    parser.add_argument("--no-pretrained", action="store_true",
                        help="Random init — the ablation that shows whether ImageNet weights matter")
    parser.add_argument("--image-size", type=int, default=384)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--epochs", type=int, default=30)
    parser.add_argument("--lr", type=float, default=1e-3, help="Head LR; backbone gets 0.1x when pretrained")
    parser.add_argument("--freeze-epochs", type=int, default=2,
                        help="Epochs with the backbone frozen while the heads settle")
    train(parser.parse_args())


if __name__ == "__main__":
    main()
