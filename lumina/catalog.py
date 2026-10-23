"""Every model Lumina can download, with where it comes from, its size and its license.

Sizes are the published file sizes (bytes) and are shown before anything downloads.
"""

from __future__ import annotations

import platform
from dataclasses import dataclass, field
from pathlib import Path

from .config import BIN, MODELS

# 25 European languages covered by Parakeet TDT 0.6B v3 and Canary 1B v2
EU25 = [
    "bg", "hr", "cs", "da", "nl", "en", "et", "fi", "fr", "de", "el", "hu", "it",
    "lv", "lt", "mt", "pl", "pt", "ro", "ru", "sk", "sl", "es", "sv", "uk",
]

WHISPER_LANGS = [
    "af", "am", "ar", "as", "az", "ba", "be", "bg", "bn", "bo", "br", "bs", "ca", "cs", "cy", "da", "de",
    "el", "en", "es", "et", "eu", "fa", "fi", "fo", "fr", "gl", "gu", "ha", "haw", "he", "hi", "hr", "ht",
    "hu", "hy", "id", "is", "it", "ja", "jw", "ka", "kk", "km", "kn", "ko", "la", "lb", "ln", "lo", "lt",
    "lv", "mg", "mi", "mk", "ml", "mn", "mr", "ms", "mt", "my", "ne", "nl", "nn", "no", "oc", "pa", "pl",
    "ps", "pt", "ro", "ru", "sa", "sd", "si", "sk", "sl", "sn", "so", "sq", "sr", "su", "sv", "sw", "ta",
    "te", "tg", "th", "tk", "tl", "tr", "tt", "uk", "ur", "uz", "vi", "yi", "yo", "zh", "yue",
]

# Kokoro voices per language (prefix letter of the voice id)
KOKORO_LANGS = {"en-us": "a", "en-gb": "b", "es": "e", "fr-fr": "f", "hi": "h", "it": "i", "ja": "j",
                "pt-br": "p", "cmn": "z"}

LLAMA_BUILD = "b11823"
EG2_STT = "embeddinggemma-2-stt"


@dataclass
class Item:
    id: str
    kind: str  # embedding | stt | vad | llm | runtime | tts
    label: str
    size: int  # bytes
    license: str
    source: str  # human readable source (repo or URL)
    repo: str = ""  # Hugging Face repo
    files: list[str] = field(default_factory=list)  # files inside the repo
    urls: list[tuple[str, str]] = field(default_factory=list)  # (url, local name) for non-HF downloads
    languages: list[str] = field(default_factory=list)
    note: str = ""
    ram_gb: float = 0
    archive: bool = False  # urls are archives to extract

    @property
    def path(self) -> Path:
        return (BIN if self.kind == "runtime" else MODELS) / self.id

    def public(self) -> dict:
        return {
            "id": self.id, "kind": self.kind, "label": self.label, "size": self.size,
            "license": self.license, "source": self.source, "languages": self.languages,
            "note": self.note, "ram_gb": self.ram_gb,
        }


def _llama_asset() -> str | None:
    sysname, arch = platform.system(), platform.machine().lower()
    arm = arch in ("arm64", "aarch64")
    if sysname == "Darwin":
        return f"llama-{LLAMA_BUILD}-bin-macos-{'arm64' if arm else 'x64'}.tar.gz"
    if sysname == "Linux":
        return f"llama-{LLAMA_BUILD}-bin-ubuntu-{'arm64' if arm else 'x64'}.tar.gz"
    if sysname == "Windows":
        return f"llama-{LLAMA_BUILD}-bin-win-cpu-{'arm64' if arm else 'x64'}.zip"
    return None


def _items() -> list[Item]:
    items = [
        Item(
            "embeddinggemma-2", "embedding", "EmbeddingGemma 2 (text, 8-bit)", 314_220_093 + 32_170_510,
            "Apache-2.0", "onnx-community/embeddinggemma-2-ONNX", repo="onnx-community/embeddinggemma-2-ONNX",
            files=["onnx/model_quantized.onnx", "onnx/model_quantized.onnx_data", "tokenizer.json",
                   "tokenizer_config.json", "config.json"],
            note="768-dim multilingual embeddings for search over your files and meetings.", ram_gb=0.6,
        ),
        Item(
            "silero-vad", "vad", "Silero VAD", 2_327_524, "MIT", "istupakov/silero-vad-onnx",
            repo="istupakov/silero-vad-onnx", files=["silero_vad.onnx"],
            note="Detects when someone is speaking.",
        ),
        Item(
            "parakeet-v3", "stt", "Parakeet TDT 0.6B v3 (8-bit)", 652_183_999 + 18_202_004 + 139_764 + 93_939,
            "CC-BY-4.0", "nvidia/parakeet-tdt-0.6b-v3 via istupakov/parakeet-tdt-0.6b-v3-onnx",
            repo="istupakov/parakeet-tdt-0.6b-v3-onnx",
            files=["encoder-model.int8.onnx", "decoder_joint-model.int8.onnx", "nemo128.onnx", "vocab.txt",
                   "?config.json"],
            languages=EU25, ram_gb=1.5,
            note="Fast on any CPU. 25 European languages; detects the language by itself.",
        ),
        Item(
            "canary-1b-v2", "stt", "Canary 1B v2 (8-bit)", 859_078_138 + 170_040_374 + 208_022,
            "CC-BY-4.0", "nvidia/canary-1b-v2 via istupakov/canary-1b-v2-onnx",
            repo="istupakov/canary-1b-v2-onnx",
            files=["encoder-model.int8.onnx", "decoder-model.int8.onnx", "vocab.txt", "?config.json"],
            languages=EU25, ram_gb=2.5,
            note="Same 25 languages, but you choose the language, so it never guesses wrong.",
        ),
    ]
    items.append(Item(
        "embeddinggemma-2-stt", "stt", "EmbeddingGemma 2 speech (experimental)",
        589_273_696 + 386_726_384 + 1_737_588, "Apache-2.0",
        "mlboydaisuke/EmbeddingGemma-2-Transcribe-LiteRT (EmbeddingGemma 2 audio encoder + reader)",
        repo="mlboydaisuke/EmbeddingGemma-2-Transcribe-LiteRT",
        files=["eg2_audio_f16safe_fp16w_w30.tflite", "reader_w30.tflite", "vocab.txt"],
        languages=["en"], ram_gb=3.0,
        note="Community speech reader on top of EmbeddingGemma 2's audio tower. English only, no punctuation, "
             "about 4 % WER on read speech. Needs the ai-edge-litert package.",
    ))
    whisper = {
        "tiny": ("Systran/faster-whisper-tiny", 75_538_270, 0.4),
        "base": ("Systran/faster-whisper-base", 145_217_532, 0.5),
        "small": ("Systran/faster-whisper-small", 483_546_902, 1.0),
        "medium": ("Systran/faster-whisper-medium", 1_527_906_378, 2.5),
        "large-v3-turbo": ("mobiuslabsgmbh/faster-whisper-large-v3-turbo", 1_617_824_864, 3.0),
    }
    for size, (repo, nbytes, ram) in whisper.items():
        items.append(Item(
            f"whisper-{size}", "stt", f"Whisper {size}", nbytes, "MIT", repo, repo=repo,
            files=["model.bin", "config.json", "tokenizer.json", "vocabulary.*", "?preprocessor_config.json"],
            languages=WHISPER_LANGS, ram_gb=ram,
            note="99 languages. Use it for languages outside the European 25 or for very small machines (tiny).",
        ))
    gemma = {
        "e2b": ("E2B", 2_841_481_184, 4, "Runs on most laptops with 8 GB of memory."),
        "e4b": ("E4B", 4_590_807_392, 6, "Better notes; wants 12 GB of memory or more."),
        "12b": ("12B", 7_219_673_216, 10, "Best quality; wants 16 GB of memory and a recent GPU or Apple Silicon."),
    }
    for key, (name, nbytes, ram, note) in gemma.items():
        repo = f"ggml-org/gemma-4-{name}-it-GGUF"
        items.append(Item(
            f"gemma-4-{key}", "llm", f"Gemma 4 {name} (4-bit)", nbytes, "Apache-2.0", repo, repo=repo,
            files=[f"gemma-4-{name}-it-Q4_0.gguf"], ram_gb=ram, note=note,
        ))
    asset = _llama_asset()
    if asset:
        items.append(Item(
            "llama-server", "runtime", f"llama.cpp server ({LLAMA_BUILD})", 16_000_000, "MIT",
            "github.com/ggml-org/llama.cpp",
            urls=[(f"https://github.com/ggml-org/llama.cpp/releases/download/{LLAMA_BUILD}/{asset}", asset)],
            archive=True, note="Runs the built-in Gemma 4 model on this computer.",
        ))
    base = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
    items.append(Item(
        "kokoro", "tts", "Kokoro 82M (8-bit)", 92_361_271 + 28_214_398, "Apache-2.0", "hexgrad/Kokoro-82M",
        urls=[(f"{base}/kokoro-v1.0.int8.onnx", "kokoro-v1.0.int8.onnx"), (f"{base}/voices-v1.0.bin", "voices-v1.0.bin")],
        languages=["en", "es", "fr", "it", "pt", "ja", "zh", "hi"],
        note="Natural voice to read notes and answers aloud. Optional; the system voice works without it.",
    ))
    return items


CATALOG: dict[str, Item] = {i.id: i for i in _items()}


def installed(item_id: str) -> bool:
    item = CATALOG.get(item_id)
    if not item:
        return False
    return (item.path / ".complete").exists()


def stt_choice(engine: str, language: str, whisper_size: str) -> str:
    """Which speech model to use for an engine setting and a language setting.

    "embeddinggemma" is never picked by "auto": it is an experimental English-only option.
    "auto" picks Parakeet when the language is automatic, Canary when a European language is fixed
    (Canary obeys the language you choose; Parakeet always detects it), and Whisper otherwise.
    """
    if engine == "parakeet":
        return "parakeet-v3"
    if engine == "canary":
        return "canary-1b-v2"
    if engine == "whisper":
        return f"whisper-{whisper_size}"
    if engine == "embeddinggemma":
        return EG2_STT
    if language in ("", "auto"):
        return "parakeet-v3"
    if language in EU25:
        return "canary-1b-v2"
    return f"whisper-{whisper_size}"


def languages_for(engine: str, whisper_size: str = "small") -> list[str]:
    if engine in ("parakeet", "canary"):
        return EU25
    if engine == "whisper":
        return WHISPER_LANGS
    if engine == "embeddinggemma":
        return ["en"]
    return WHISPER_LANGS  # auto can route any language to Whisper
