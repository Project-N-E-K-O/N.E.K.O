"""Build the deterministic optional TSE release; never bundle weights in NEKO.

Run with uv run --no-sync python scripts/package_tse_models.py --help.
Only the reviewed three prototype artifacts are accepted. The output manifest is
application-owned; a manifest supplied inside an imported ZIP is never trusted.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import zipfile


REVISION = "real-tse-causal-onnx-v1"
UPSTREAM_COMMIT = "2a540977a348fbaa92e623210505430e2cec608d"
FILES = {
    "tse_stateful_fp32.onnx": (75778515, "bce77f0146c5ff01b0fc62a500b8dd94553212373b235639e8436d7be3d809a6"),
    "tse_ecapa_fp32.onnx": (24897704, "6eb9e96eed042cc59b875631deb448ea8b11a7b40918a8424d0aa0161be70e99"),
    "tse_frontend_constants.npz": (84346, "312c4f3e526452fa8a83b2246f23e5b3581199bc36c0548e1c2be0ba87f7db4b"),
}
README = """# NEKO optional causal target-speaker extraction

Conversion version: real-tse-causal-onnx-v1
REAL-TSE variant: spk_emb_causal_100; sample rate: 16000 Hz, mono.
Runtime: ONNX Runtime CPU, FP32, opset 17, batch 1.

Contents: separator, matching WeSpeaker ECAPA, frozen frontend constants,
bundle metadata, source notices and the Apache 2.0 license text.
No training optimizer state, pVAD model, pVAD ECAPA, recordings or test fixtures.

The separator consumes real/imaginary spectra with 257 bins plus an unnormalized
192-dimensional speaker embedding and persistent hidden/cell states. Use only
NEKO's matching frontend and enrollment version. SpeechBrain/pVAD embeddings are
not interchangeable with this model. Downloading does not enable extraction;
complete compatible enrollment and enable it before a new microphone session.

Processing happens on the NEKO device. Extracted audio follows the existing ASR
upload settings. Separation can distort speech or retain other speakers; it is
not identity authorization. Real-room quality and ordinary laptop performance
still require acceptance testing; numerical compatibility alone is insufficient.
"""
NOTICE = f"""REAL-TSE source: https://github.com/REAL-TSE/wesep-real-tse
Fixed commit: {UPSTREAM_COMMIT}
Official checkpoint variant: spk_emb_causal_100
Checkpoint SHA-256: 42b73219eefefcbba4b5da0dcb89dce292359c9f93b1a951c7b6d196da2155b3
Checkpoint distribution: linked from the fixed upstream README at
https://github.com/REAL-TSE/wesep-real-tse/blob/{UPSTREAM_COMMIT}/README.md

WeSpeaker source: https://github.com/wenet-e2e/wespeaker
Fixed commit: 8f53b6485d9f88a207bd17e7f8dba899495ec794

The inspected REAL-TSE tse_bsrnn_spk.py identifies Apache-2.0:
Copyright (c) 2025 Ke Zhang (kylezhang1118@gmail.com).
The inspected WeSpeaker ECAPA implementation identifies Apache-2.0:
Copyright (c) 2021 Zhengyang Chen (chenzhengyang117@gmail.com)
              2022 Hongji Wang (jijijiang77@gmail.com)
              2023 Bing Han (hanbing97@sjtu.edu.cn)
              2024 Zhengyang Chen (chenzhengyang117@gmail.com).
Its implementation credits https://github.com/lawlict/ECAPA-TDNN.

Conversion changes: isolate the causal separator, expose recurrent states,
export the matching embedding encoder, and freeze frontend constants for NumPy.
The weights are unchanged from the identified checkpoint.

The fixed REAL-TSE checkout has no root LICENSE and the inspected checkpoint
distribution has no separately identified model-weight license. These source
code notices are not an assertion of additional weight redistribution rights.
Confirm the upstream model distribution terms before public publication.
No upstream endorsement is implied.
"""


def digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def build_release(source: Path, output: Path, apache_license: Path) -> dict:
    source, output = Path(source), Path(output)
    output.mkdir(parents=True, exist_ok=True)
    for name, (size, digest) in FILES.items():
        path = source / name
        if path.stat().st_size != size or digest_file(path) != digest:
            raise ValueError(f"unreviewed prototype artifact: {name}")
    license_text = apache_license.read_text(encoding="utf-8").replace("\r\n", "\n")
    if "Apache License" not in license_text or "TERMS AND CONDITIONS" not in license_text:
        raise ValueError("expected full Apache 2.0 license")
    bundle = {
        "resource_revision": REVISION,
        "model_revision": FILES["tse_ecapa_fp32.onnx"][1],
        "upstream_commit": UPSTREAM_COMMIT,
        "preprocessing_revision": "wespeaker-kaldi-fbank-v1",
        "reference_method": "mean_raw_3_segments_v1",
        "sample_rate": 16000,
        "embedding_dimension": 192,
        "embedding_l2_normalized": False,
        "files": {name: {"bytes": size, "sha256": digest} for name, (size, digest) in FILES.items()},
    }
    documents = {
        "bundle.json": (json.dumps(bundle, indent=2, sort_keys=True) + "\n").encode(),
        "README.md": README.encode(),
        "NOTICE.txt": NOTICE.encode(),
        "LICENSE-APACHE-2.0.txt": license_text.encode(),
    }
    archive_path = output / f"{REVISION}.zip"
    file_manifest = {}
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name in sorted(set(FILES) | set(documents)):
            info = zipfile.ZipInfo(name, date_time=(2026, 9, 8, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            if name in documents:
                data = documents[name]
                archive.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
                size, digest = len(data), hashlib.sha256(data).hexdigest()
            else:
                # Supplying ZipInfo bypasses ZipFile's default level.
                info._compresslevel = 9
                with archive.open(info, "w") as target, (source / name).open("rb") as origin:
                    for block in iter(lambda: origin.read(1024 * 1024), b""):
                        target.write(block)
                size, digest = FILES[name]
            file_manifest[name] = {"bytes": size, "sha256": digest}
    manifest = {
        "schema": 1,
        "resource_revision": REVISION,
        "model_id": "real-tse-wespeaker-ecapa",
        "model_revision": FILES["tse_ecapa_fp32.onnx"][1],
        "upstream_commit": UPSTREAM_COMMIT,
        "preprocessing_revision": bundle["preprocessing_revision"],
        "reference_method": bundle["reference_method"],
        "embedding_dimension": 192,
        "sample_rate": 16000,
        "archive": {"filename": archive_path.name, "bytes": archive_path.stat().st_size,
                    "sha256": digest_file(archive_path)},
        "source": None,
        "files": file_manifest,
        "onnx": {
            "tse_stateful_fp32.onnx": {
                "external_data": False,
                "inputs": {"spectrum": [1, 2, 257, "frames"], "embedding": [1, 192],
                           "hidden": [6, 1, 32, 256], "cell": [6, 1, 32, 256]},
                "outputs": {"estimated": ["Concatestimated_dim_0", 2, 257, "frames"],
                            "next_hidden": [6, 1, "Concatnext_hidden_dim_2", 256],
                            "next_cell": [6, 1, "Concatnext_cell_dim_2", 256]},
                "probe_outputs": {"estimated": [1, 2, 257, 1],
                                  "next_hidden": [6, 1, 32, 256], "next_cell": [6, 1, 32, 256]},
            },
            "tse_ecapa_fp32.onnx": {
                "external_data": False,
                "inputs": {"fbank": [1, "frames", 80]},
                "outputs": {"embedding": [1, 192]},
                "probe_outputs": {"embedding": [1, 192]},
            },
        },
    }
    (output / "release_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="Directory with reviewed three exported files")
    parser.add_argument("--output", type=Path, required=True, help="Output outside the application source tree")
    parser.add_argument("--apache-license", type=Path, required=True, help="Full upstream Apache 2.0 license text")
    parser.add_argument("--write-manifest", type=Path, help="Explicitly update the trusted application release manifest")
    args = parser.parse_args()
    repository = Path(__file__).resolve().parents[1]
    if args.output.resolve().is_relative_to(repository):
        parser.error("Model release artifacts must be generated outside the application source tree")
    result = build_release(args.source, args.output, args.apache_license)
    if args.write_manifest:
        args.write_manifest.parent.mkdir(parents=True, exist_ok=True)
        args.write_manifest.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"archive": result["archive"], "output": str(args.output)}, indent=2))


if __name__ == "__main__":
    main()
