"""RoBERTa-style cross-encoder training and scoring utilities."""

from __future__ import annotations

import csv
import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from diracdata_v2.retrieval.column_cards import ColumnCard


@dataclass
class TransformersColumnReranker:
    model_path: Path | str
    max_length: int = 256
    local_files_only: bool = True

    def __post_init__(self) -> None:
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        self._tokenizer = AutoTokenizer.from_pretrained(
            str(self.model_path),
            local_files_only=self.local_files_only,
        )
        self._model = AutoModelForSequenceClassification.from_pretrained(
            str(self.model_path),
            local_files_only=self.local_files_only,
        )
        self._model.eval()

    def score(self, *, query: str, candidates: list[ColumnCard]) -> list[float]:
        import torch

        if not candidates:
            return []
        texts = [(query, card.text) for card in candidates]
        with torch.no_grad():
            encoded = self._tokenizer(
                [left for left, _ in texts],
                [right for _, right in texts],
                padding=True,
                truncation=True,
                max_length=self.max_length,
                return_tensors="pt",
            )
            logits = self._model(**encoded).logits
            if logits.shape[-1] == 1:
                return logits[:, 0].tolist()
            return torch.softmax(logits, dim=-1)[:, 1].tolist()


def train_roberta_reranker(
    *,
    pairs_path: Path,
    output_dir: Path,
    model_name: str = "distilroberta-base",
    local_files_only: bool = True,
    max_length: int = 256,
    epochs: int = 1,
    batch_size: int = 8,
    learning_rate: float = 2e-5,
    validation_fraction: float = 0.1,
    seed: int = 13,
    max_train_rows: int | None = None,
) -> dict[str, Any]:
    import torch
    from torch.utils.data import DataLoader
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    rows = _read_pair_rows(pairs_path)
    if max_train_rows is not None:
        rows = _sample_rows(rows, limit=max_train_rows, seed=seed)
    if not rows:
        raise ValueError("No training rows found.")
    train_rows, validation_rows = _split_rows(rows, validation_fraction=validation_fraction, seed=seed)
    tokenizer = AutoTokenizer.from_pretrained(model_name, local_files_only=local_files_only)
    model = AutoModelForSequenceClassification.from_pretrained(
        model_name,
        num_labels=2,
        local_files_only=local_files_only,
    )
    train_loader = DataLoader(
        _PairDataset(rows=train_rows, tokenizer=tokenizer, max_length=max_length),
        batch_size=batch_size,
        shuffle=True,
    )
    validation_loader = DataLoader(
        _PairDataset(rows=validation_rows, tokenizer=tokenizer, max_length=max_length),
        batch_size=batch_size,
        shuffle=False,
    )
    optimizer = torch.optim.AdamW(model.parameters(), lr=learning_rate)
    model.train()
    history = []
    for epoch in range(max(1, epochs)):
        total_loss = 0.0
        for batch in train_loader:
            optimizer.zero_grad()
            labels = batch.pop("labels")
            result = model(**batch, labels=labels)
            result.loss.backward()
            optimizer.step()
            total_loss += float(result.loss.detach().cpu())
        history.append(
            {
                "epoch": epoch + 1,
                "train_loss": total_loss / max(1, len(train_loader)),
                "validation_accuracy": _accuracy(model=model, loader=validation_loader),
            }
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(output_dir)
    tokenizer.save_pretrained(output_dir)
    manifest = {
        "model_name": model_name,
        "pairs_path": str(pairs_path),
        "output_dir": str(output_dir),
        "train_rows": len(train_rows),
        "validation_rows": len(validation_rows),
        "epochs": epochs,
        "batch_size": batch_size,
        "learning_rate": learning_rate,
        "history": history,
    }
    (output_dir / "training_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return manifest


class _PairDataset:
    def __init__(self, *, rows: list[dict[str, str]], tokenizer: Any, max_length: int) -> None:
        self._rows = rows
        self._tokenizer = tokenizer
        self._max_length = max_length

    def __len__(self) -> int:
        return len(self._rows)

    def __getitem__(self, index: int) -> dict[str, Any]:
        row = self._rows[index]
        encoded = self._tokenizer(
            row["query"],
            row["candidate_text"],
            padding="max_length",
            truncation=True,
            max_length=self._max_length,
            return_tensors="pt",
        )
        item = {key: value.squeeze(0) for key, value in encoded.items()}
        import torch

        item["labels"] = torch.tensor(int(row["label"]), dtype=torch.long)
        return item


def _read_pair_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _split_rows(
    rows: list[dict[str, str]],
    *,
    validation_fraction: float,
    seed: int,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    by_case: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        by_case.setdefault(str(row.get("case_id") or ""), []).append(row)
    case_ids = sorted(by_case)
    random.Random(seed).shuffle(case_ids)
    validation_count = max(1, int(len(case_ids) * max(0.0, min(0.5, validation_fraction))))
    validation_ids = set(case_ids[:validation_count])
    train_rows = [row for case_id in case_ids if case_id not in validation_ids for row in by_case[case_id]]
    validation_rows = [row for case_id in case_ids if case_id in validation_ids for row in by_case[case_id]]
    return train_rows, validation_rows


def _sample_rows(rows: list[dict[str, str]], *, limit: int, seed: int) -> list[dict[str, str]]:
    if limit <= 0 or len(rows) <= limit:
        return rows
    by_label: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        by_label.setdefault(str(row.get("label") or "0"), []).append(row)
    rng = random.Random(seed)
    labels = sorted(by_label)
    sampled: list[dict[str, str]] = []
    remaining = limit
    for index, label in enumerate(labels):
        label_rows = list(by_label[label])
        rng.shuffle(label_rows)
        if index == len(labels) - 1:
            take = remaining
        else:
            take = min(len(label_rows), max(1, round(limit * len(label_rows) / len(rows))))
        sampled.extend(label_rows[:take])
        remaining -= take
    rng.shuffle(sampled)
    return sampled[:limit]


def _accuracy(*, model: Any, loader: Any) -> float:
    if len(loader) == 0:
        return 0.0
    import torch

    model.eval()
    correct = 0
    total = 0
    with torch.no_grad():
        for batch in loader:
            labels = batch.pop("labels")
            logits = model(**batch).logits
            predictions = torch.argmax(logits, dim=-1)
            correct += int((predictions == labels).sum().item())
            total += int(labels.numel())
    model.train()
    return correct / max(1, total)
