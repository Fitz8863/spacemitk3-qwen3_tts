#!/usr/bin/env python3
"""Qwen3-TTS K3 interactive WAV generator.

Each submitted text is synthesized and atomically saved to the fixed file
wav-output/output.wav below the project directory. No audio player is launched.
"""

from __future__ import annotations

import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import wave
from dataclasses import dataclass
from pathlib import Path

HOST = os.getenv("QWEN3_TTS_HOST", "127.0.0.1")
PORT = int(os.getenv("QWEN3_TTS_PORT", "18080"))
BASE_URL = f"http://{HOST}:{PORT}"
SPEECH_URL = f"{BASE_URL}/v1/audio/speech"
MAX_CHARS = max(8, int(os.getenv("QWEN3_TTS_CHUNK_CHARS", "32")))
PROJECT_ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = PROJECT_ROOT / "wav-output"
OUTPUT_FILE = OUTPUT_DIR / "output.wav"


@dataclass
class AudioChunk:
    wav: bytes
    synth_seconds: float
    audio_seconds: float | None
    rtf: float | None


def health_ok() -> bool:
    try:
        with urllib.request.urlopen(f"{BASE_URL}/health", timeout=2) as response:
            return response.status == 200
    except Exception:
        return False


def split_text(text: str) -> list[str]:
    """Split at natural punctuation; never send an unfinished fragment to TTS."""
    text = re.sub(r"\s+", " ", text.strip())
    if not text:
        return []

    sentences = [part.strip() for part in re.findall(r".*?(?:[。！？!?；;\n]+|$)", text) if part.strip()]
    chunks: list[str] = []
    terminal = re.compile(r"[。！？!?；;，,：:.]$")

    def finish(part: str) -> str:
        part = part.strip()
        if not part or terminal.search(part):
            return part
        # An explicit terminator helps Qwen3-TTS emit EOS reliably.
        return part + ("。" if re.search(r"[\u3400-\u9fff]", part) else ".")

    for sentence in sentences:
        if len(sentence) <= MAX_CHARS:
            chunks.append(finish(sentence))
            continue

        # Prefer commas/colons as low-latency clause boundaries. The delimiter is retained.
        natural = [part.strip() for part in re.findall(r".*?(?:[，,：:]+|$)", sentence) if part.strip()]
        if len(natural) > 1:
            chunks.extend(finish(part) for part in natural if finish(part))
            continue

        # Last resort for a very long unpunctuated sentence: split Chinese by characters
        # and English by complete words, then add a terminator to every chunk.
        if " " in sentence:
            current: list[str] = []
            current_len = 0
            for word in sentence.split():
                extra = len(word) + (1 if current else 0)
                if current and current_len + extra > MAX_CHARS:
                    chunks.append(finish(" ".join(current)))
                    current = [word]
                    current_len = len(word)
                else:
                    current.append(word)
                    current_len += extra
            if current:
                chunks.append(finish(" ".join(current)))
        else:
            chunks.extend(finish(sentence[i : i + MAX_CHARS]) for i in range(0, len(sentence), MAX_CHARS))
    return chunks


def synthesize(text: str) -> AudioChunk:
    body = json.dumps(
        {
            "model": "qwen3-tts",
            "input": text,
            "voice": "default",
            "response_format": "wav",
            "speed": 1.0,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    request = urllib.request.Request(
        SPEECH_URL,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            wav = response.read()
            headers = response.headers
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc
    elapsed = time.monotonic() - started

    def header_float(name: str) -> float | None:
        value = headers.get(name)
        try:
            return float(value) if value is not None else None
        except ValueError:
            return None

    if len(wav) < 44 or wav[:4] != b"RIFF" or wav[8:12] != b"WAVE":
        raise RuntimeError("服务端没有返回有效的 WAV 音频")
    return AudioChunk(
        wav=wav,
        synth_seconds=elapsed,
        audio_seconds=header_float("X-TTS-Audio-Seconds"),
        rtf=header_float("X-TTS-RTF"),
    )


def output_path() -> Path:
    """Return the fixed output path, creating its project-local directory."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    return OUTPUT_FILE


def save_speech(text: str) -> Path:
    chunks = split_text(text)
    if not chunks:
        raise ValueError("输入文字为空")

    started = time.monotonic()
    destination = output_path()
    temporary = destination.with_name(f".{destination.name}.part")
    expected_format: tuple[int, int, int, str] | None = None
    total_frames = 0

    print(f"已分成 {len(chunks)} 段，开始合成……", flush=True)
    try:
        with wave.open(str(temporary), "wb") as writer:
            for index, chunk_text in enumerate(chunks, start=1):
                audio = synthesize(chunk_text)
                with wave.open(io.BytesIO(audio.wav), "rb") as reader:
                    current_format = (
                        reader.getnchannels(),
                        reader.getsampwidth(),
                        reader.getframerate(),
                        reader.getcomptype(),
                    )
                    if current_format[3] != "NONE":
                        raise RuntimeError(f"不支持的 WAV 压缩格式：{current_format[3]}")
                    if expected_format is None:
                        expected_format = current_format
                        writer.setnchannels(current_format[0])
                        writer.setsampwidth(current_format[1])
                        writer.setframerate(current_format[2])
                        writer.setcomptype("NONE", "not compressed")
                    elif current_format != expected_format:
                        raise RuntimeError(
                            f"第 {index} 段 WAV 格式与第一段不一致："
                            f"{current_format} != {expected_format}"
                        )
                    frames = reader.readframes(reader.getnframes())
                    total_frames += reader.getnframes()
                    writer.writeframesraw(frames)

                print(
                    f"第 {index}/{len(chunks)} 段合成完成：{audio.synth_seconds:.2f} 秒",
                    flush=True,
                )

        os.replace(temporary, destination)
    except Exception:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise

    if expected_format is None:
        raise RuntimeError("没有生成音频")
    duration = total_frames / expected_format[2]
    elapsed = time.monotonic() - started
    print(f"保存成功：{destination}", flush=True)
    print(f"音频时长：{duration:.2f} 秒；本轮耗时：{elapsed:.2f} 秒", flush=True)
    return destination


def main() -> int:
    if not health_ok():
        print("Qwen3-TTS 服务未就绪，请先运行 ./start_server.sh", file=sys.stderr)
        return 1

    if len(sys.argv) > 1:
        try:
            save_speech(" ".join(sys.argv[1:]))
            return 0
        except Exception as exc:
            print(f"合成或保存失败：{exc}", file=sys.stderr)
            return 1

    print("Qwen3-TTS 交互式 WAV 生成模式")
    print("- 输入中文、英文或中英混合文字，回车后生成 WAV")
    print("- 每次输入覆盖同一个 WAV 文件")
    print(f"- 输出文件: {OUTPUT_FILE}")
    print("- 不自动播放；输入 quit / exit / 退出 可结束")
    print(f"- 分段上限: {MAX_CHARS} 字符")

    while True:
        try:
            text = input("\n请输入文字> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not text:
            continue
        if text.lower() in {"quit", "exit"} or text == "退出":
            break
        try:
            save_speech(text)
        except KeyboardInterrupt:
            print("\n本轮已中断", file=sys.stderr)
        except Exception as exc:
            print(f"合成或保存失败：{exc}", file=sys.stderr)
    print("已退出。后台服务仍在运行；停止服务请执行 ./stop_server.sh")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
