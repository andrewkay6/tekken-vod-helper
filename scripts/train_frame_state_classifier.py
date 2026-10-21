import argparse
import json
import random
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    args = _parse_args()
    try:
        import torch
        from torch import nn
        from torch.utils.data import DataLoader, random_split
        from torchvision import datasets, models, transforms
    except ImportError:
        print(
            "Optional ML packages are not installed. Install them in a separate training environment:\n"
            "  pip install torch torchvision",
            file=sys.stderr,
        )
        return 2

    random.seed(args.seed)
    torch.manual_seed(args.seed)
    device = torch.device("cuda" if torch.cuda.is_available() and not args.cpu else "cpu")

    image_transforms = transforms.Compose(
        [
            transforms.Resize((224, 224)),
            transforms.RandomHorizontalFlip(p=0.0),
            transforms.ToTensor(),
            transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
        ]
    )
    dataset = datasets.ImageFolder(args.dataset_dir, transform=image_transforms)
    if len(dataset.classes) < 2:
        raise SystemExit("Dataset needs at least two label folders.")
    if len(dataset) < 20:
        raise SystemExit("Dataset is too small to train usefully. Add more labeled frames first.")

    validation_count = max(1, int(len(dataset) * args.validation_fraction))
    training_count = len(dataset) - validation_count
    train_set, validation_set = random_split(
        dataset,
        [training_count, validation_count],
        generator=torch.Generator().manual_seed(args.seed),
    )
    train_loader = DataLoader(train_set, batch_size=args.batch_size, shuffle=True)
    validation_loader = DataLoader(validation_set, batch_size=args.batch_size, shuffle=False)

    model = _build_model(args.model, len(dataset.classes), models, nn)
    model.to(device)

    optimizer = torch.optim.AdamW(model.parameters(), lr=args.learning_rate)
    criterion = nn.CrossEntropyLoss()
    best_accuracy = 0.0
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    labels_path = output_dir / "labels.json"
    labels_path.write_text(json.dumps(dataset.classes, indent=2), encoding="utf-8")

    for epoch in range(1, args.epochs + 1):
        train_loss, train_accuracy = _run_epoch(model, train_loader, criterion, optimizer, device)
        validation_loss, validation_accuracy = _run_epoch(model, validation_loader, criterion, None, device)
        print(
            "epoch {}/{} train_loss={:.4f} train_acc={:.3f} val_loss={:.4f} val_acc={:.3f}".format(
                epoch,
                args.epochs,
                train_loss,
                train_accuracy,
                validation_loss,
                validation_accuracy,
            ),
            flush=True,
        )
        if validation_accuracy >= best_accuracy:
            best_accuracy = validation_accuracy
            torch.save(
                {
                    "model": args.model,
                    "classes": dataset.classes,
                    "state_dict": model.state_dict(),
                    "validation_accuracy": validation_accuracy,
                },
                output_dir / "frame_state_classifier.pt",
            )

    if args.export_onnx:
        checkpoint = torch.load(output_dir / "frame_state_classifier.pt", map_location=device)
        model.load_state_dict(checkpoint["state_dict"])
        model.eval()
        dummy = torch.randn(1, 3, 224, 224, device=device)
        torch.onnx.export(
            model,
            dummy,
            str(output_dir / "frame_state_classifier.onnx"),
            input_names=["image"],
            output_names=["logits"],
            dynamic_axes={"image": {0: "batch"}, "logits": {0: "batch"}},
            opset_version=17,
        )
    print("Saved classifier artifacts to {}".format(output_dir))
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train an optional frame-state classifier from labeled frame folders.")
    parser.add_argument("--dataset-dir", required=True, help="Folder containing one subfolder per state label.")
    parser.add_argument("--output-dir", default=str(REPO_ROOT / "backtests" / "models" / "frame_state"))
    parser.add_argument("--model", choices=["resnet18", "mobilenet_v3_small"], default="mobilenet_v3_small")
    parser.add_argument("--epochs", type=int, default=8)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--learning-rate", type=float, default=0.0003)
    parser.add_argument("--validation-fraction", type=float, default=0.2)
    parser.add_argument("--seed", type=int, default=1337)
    parser.add_argument("--cpu", action="store_true")
    parser.add_argument("--export-onnx", action="store_true")
    return parser.parse_args()


def _build_model(name, class_count, models, nn):
    if name == "resnet18":
        weights = models.ResNet18_Weights.DEFAULT
        model = models.resnet18(weights=weights)
        model.fc = nn.Linear(model.fc.in_features, class_count)
        return model
    weights = models.MobileNet_V3_Small_Weights.DEFAULT
    model = models.mobilenet_v3_small(weights=weights)
    model.classifier[-1] = nn.Linear(model.classifier[-1].in_features, class_count)
    return model


def _run_epoch(model, loader, criterion, optimizer, device):
    is_training = optimizer is not None
    model.train(is_training)
    total_loss = 0.0
    correct = 0
    total = 0
    try:
        import torch
    except ImportError:
        raise RuntimeError("torch is required")

    context = torch.enable_grad() if is_training else torch.no_grad()
    with context:
        for images, labels in loader:
            images = images.to(device)
            labels = labels.to(device)
            if is_training:
                optimizer.zero_grad()
            logits = model(images)
            loss = criterion(logits, labels)
            if is_training:
                loss.backward()
                optimizer.step()
            total_loss += loss.item() * images.size(0)
            predictions = logits.argmax(dim=1)
            correct += (predictions == labels).sum().item()
            total += images.size(0)
    if total == 0:
        return 0.0, 0.0
    return total_loss / float(total), correct / float(total)


if __name__ == "__main__":
    raise SystemExit(main())
