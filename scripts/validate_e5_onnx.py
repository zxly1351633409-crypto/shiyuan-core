from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np


def normalize(value: np.ndarray) -> np.ndarray:
    return value / np.clip(np.linalg.norm(value, axis=1, keepdims=True), 1e-9, None)


def mean_pool(hidden: np.ndarray, mask: np.ndarray) -> np.ndarray:
    expanded = mask[..., None].astype(np.float32)
    return normalize((hidden * expanded).sum(axis=1) / np.clip(expanded.sum(axis=1), 1e-9, None))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-model", type=Path, required=True)
    parser.add_argument("--onnx-model", type=Path, required=True)
    parser.add_argument("--onnx-file", default="model.onnx")
    args = parser.parse_args()

    import onnxruntime as ort
    import torch
    from tokenizers import Tokenizer
    from transformers import AutoModel, AutoTokenizer

    values = ["query: 以前为什么放弃自研破碎", "passage: 废弃自研表面裂纹，改为 Cashew 实体细胞引擎。"]
    reference_tokenizer = AutoTokenizer.from_pretrained(args.source_model, local_files_only=True)
    reference_model = AutoModel.from_pretrained(args.source_model, local_files_only=True)
    reference_model.eval()
    reference_inputs = reference_tokenizer(values, padding=True, truncation=True, max_length=512, return_tensors="pt")
    with torch.inference_mode():
        reference_hidden = reference_model(**reference_inputs).last_hidden_state.cpu().numpy()
    reference = mean_pool(reference_hidden, reference_inputs["attention_mask"].cpu().numpy())

    tokenizer = Tokenizer.from_file(str(args.onnx_model / "tokenizer.json"))
    tokenizer.enable_truncation(max_length=512)
    tokenizer.enable_padding(pad_id=tokenizer.token_to_id("<pad>"), pad_token="<pad>")
    encoded = tokenizer.encode_batch(values)
    input_ids = np.asarray([item.ids for item in encoded], dtype=np.int64)
    attention_mask = np.asarray([item.attention_mask for item in encoded], dtype=np.int64)
    session = ort.InferenceSession(str(args.onnx_model / args.onnx_file), providers=["CPUExecutionProvider"])
    hidden = session.run(None, {"input_ids": input_ids, "attention_mask": attention_mask})[0]
    candidate = mean_pool(hidden, attention_mask)
    similarities = np.sum(reference * candidate, axis=1)
    print(json.dumps({
        "ok": bool(np.all(similarities > 0.99999)),
        "cosine_similarity": [round(float(item), 8) for item in similarities],
        "max_abs_diff": round(float(np.max(np.abs(reference - candidate))), 8),
    }))


if __name__ == "__main__":
    main()
