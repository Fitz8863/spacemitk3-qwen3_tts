# Codex Project Context: Qwen3-TTS on SpaceMIT K3

此文件供新的 Codex 会话快速接手。开始工作时先读本文档，再用实时进程、文件和日志验证状态。

## 当前用户需求（2026-08-22 更新）

当前交互程序的行为是：

```text
输入文字 -> 回车 -> K3 合成 -> 原子覆盖 wav-output/output.wav -> 不播放
```

固定输出文件：

```text
/home/spacemit/projects/qwen3-tts/wav-output/output.wav
```

输出位置与启动程序时的 Shell 工作目录无关。每次成功合成后覆盖上一份 WAV，不创建时间戳文件。

旧的板端 `aplay` 自动播放模式已停用。不要把当前实现描述成“不落盘”或“自动播放”。历史播放版备份已移出项目目录，当前仓库只保留不播放的 WAV 保存实现。

## 环境

```text
SSH 别名:       spacemit-k3
用户:           spacemit
主机:           spacemit-spacemitk3picoitx
架构:           riscv64
项目:           /home/spacemit/projects/qwen3-tts
服务:           http://127.0.0.1:18080
```

接手检查：

```bash
ssh spacemit-k3
cd /home/spacemit/projects/qwen3-tts
pwd
cat AGENTS.md
curl -fsS http://127.0.0.1:18080/health || true
pid="$(cat llama-server.pid 2>/dev/null || true)"
[[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" && ps -fp "$pid" || true
tail -n 30 llama-server.log
```

PID 会变化，不能把文档中的历史 PID 当成当前 PID。

## 关键文件

```text
/home/spacemit/projects/qwen3-tts/
├── AGENTS.md
├── README.md
├── start_server.sh
├── stop_server.sh
├── run_interactive.sh
├── qwen3_tts_interactive.py
├── qwen3-tts-0.6b/
├── runtime/bin/llama-server
├── .gitignore
└── wav-output/output.wav  # 运行时生成，不提交
```

不要破坏其他项目：

```text
/home/spacemit/projects/tts
/home/spacemit/projects/sm-sdk
```

## 使用方法

交互生成：

```bash
cd /home/spacemit/projects/qwen3-tts
./run_interactive.sh
```

单次生成：

```bash
./run_interactive.sh '你好，这是 Qwen3 TTS。'
```

无论从哪个目录启动，输出都固定在项目目录：

```bash
cd /tmp
/home/spacemit/projects/qwen3-tts/run_interactive.sh '仍保存到项目的 wav-output/output.wav。'
```

调整分段长度：

```bash
QWEN3_TTS_CHUNK_CHARS=24 ./run_interactive.sh
```

退出交互：输入 `quit`、`exit` 或 `退出`。

## WAV 保存实现

代码位于 `qwen3_tts_interactive.py`：

- `split_text()`：按自然标点或长度分段；英文避免截断单词；缺少结束标点时自动补齐，以降低 `frame limit without EOS` 风险。
- `synthesize()`：请求 `POST /v1/audio/speech`，每段返回完整 WAV 字节。
- `output_path()`：创建项目内的 `wav-output` 目录，并返回固定的 `output.wav` 路径。
- `save_speech()`：解析每段 WAV，校验声道数、采样位宽、采样率和压缩格式，再把 PCM frame 写进同一个 WAV 容器。

不要使用二进制 `wav1 + wav2` 直接拼接；每段都有 RIFF/WAV 头，直接拼接会产生无效或只播放第一段的文件。

保存过程：

1. 在 `wav-output` 中创建隐藏的 `.output.wav.part`。
2. 逐段合成并将 PCM frame 写入一个 WAV writer。
3. 所有分段成功后使用 `os.replace()` 原子改为最终 `.wav`。
4. 任何异常都会尝试删除 `.part`，避免留下看似完整的损坏文件。

当前输出应为：

```text
24000 Hz
16-bit
mono
PCM/uncompressed WAV
```

实际验证时必须用 `wave`、`file` 或 `ffprobe` 检查，不要仅根据扩展名判断。

## 模型与 Runtime

模型目录：

```text
/home/spacemit/projects/qwen3-tts/qwen3-tts-0.6b
```

主要文件：

```text
text-embed-proj.onnx
codec-decoder-t50-q.onnx
talker-q8_0.gguf
code-predictor-q4_0.gguf
qwen3-tts-aux.gguf
tokenizer.gguf
default.spk.bin
config.json
```

默认优化 Runtime：

```text
/home/spacemit/projects/qwen3-tts/runtime/bin/llama-server
version: 3 (787e5fc)
```

`runtime/bin/` 只保留当前 Qwen3-TTS 服务实际加载的优化版 `llama-server` 及其 `libggml*`、`libllama*`、`libmtmd` 共享库。

SpaceMIT ORT 默认使用板端系统安装的 `spacemit-onnxruntime`：

```text
/usr/lib/libonnxruntime.so.1
/usr/lib/libspacemit_ep.so
```

如需使用其他 ORT，可设置 `QWEN3_TTS_ORT_LIB_DIR=/path/to/lib`；旧的项目内 ORT 目录已不再作为默认依赖。

确认实际加载：

```bash
pid="$(cat /home/spacemit/projects/qwen3-tts/llama-server.pid)"
grep -E 'libonnxruntime|libspacemit_ep' "/proc/$pid/maps" | awk '{print $6}' | sort -u
```

模型配置关键值：

```text
sample_rate:                    24000
frontend_threads:               2
codec_threads:                  4
talker_threads:                 4
SPACEMIT_EP_INTRA_THREAD_NUM:    4
SPACEMIT_EP_INTER_THREAD_NUM:    1
```

## 音色能力边界

当前加载：

```text
qwen3-tts-0.6b/default.spk.bin
```

Runtime 读取的 speaker 文件格式是 raw `float32[1024]`，即 4096 字节。模型底座属于支持 voice cloning 的 Qwen3-TTS Base 路线，但当前 K3 HTTP 服务没有实现直接上传 `ref_audio`/`ref_text` 并现场提取音色。不要把“底层模型支持克隆”错误表述为“当前接口已能直接上传录音克隆”。

## 流式能力边界

当前不是逐 PCM 真流式。服务端行为是：

```text
每段文本 -> 完整生成该段 WAV -> 返回完整 HTTP 响应
```

交互程序现在也不播放，只把各段 PCM 合并保存为一个文件。若未来实现真流式，需要修改 C++ talker/codec 回调和传输接口，不能仅改 Python 播放/保存层。

## CPU、EP 线程和 AI Core

- `taskset` 只控制 Linux CPU 亲和性。
- `SPACEMIT_EP_INTRA_THREAD_NUM` 是 EP 线程配置。
- 这两者都不能直接证明使用了几个 A100/AI Core。

若要声明 AI Core 使用数量，需要设备级调度日志、profile 或计数器。`/proc/<pid>/task/*/status` 只能证明 CPU 亲和性。

## 服务管理

```bash
cd /home/spacemit/projects/qwen3-tts
./start_server.sh
curl -fsS http://127.0.0.1:18080/health
./stop_server.sh
tail -n 100 llama-server.log
```

默认只绑定 `127.0.0.1`。远程访问优先用 SSH 隧道，不要无必要暴露到局域网。

## 修改和验证规则

1. 先确认绝对路径和当前工作目录。
2. 修改前读实际代码、配置、服务日志和进程 maps。
3. 使用短文本和 `timeout` 做有界测试。
4. 只清理本项目准确 PID，不使用宽泛 `killall`。
5. 修改交互代码后至少验证：
   - `python3 -m py_compile qwen3_tts_interactive.py`
   - `/health` 正常
   - 单段中文生成成功
   - 多段文本合并成功
   - 输出固定为项目内 `wav-output/output.wav`
   - 连续生成会覆盖旧文件且目录内不累积时间戳 WAV
   - WAV 是 24 kHz、16-bit、mono、PCM
   - 没有残留 `.part`
   - 代码没有调用 `aplay`
6. 不要把“ORT 能运行”“加载 SpaceMIT EP”“全图使用 AI Core”混为一谈。
7. 向用户报告真实生成路径、文件大小、音频时长和耗时。

## 快速回归

从项目目录交互生成：

```bash
ssh spacemit-k3 "cd /home/spacemit/projects/qwen3-tts && printf '%s\n%s\n' '你好，保存测试。' '退出' | timeout 40s ./run_interactive.sh"
```

从其他目录验证固定保存位置：

```bash
ssh spacemit-k3 "cd /tmp && timeout 40s /home/spacemit/projects/qwen3-tts/run_interactive.sh '固定输出目录测试。'"
```

检查最近 WAV：

```bash
python3 - <<'PY'
from pathlib import Path
import wave
p = Path('/home/spacemit/projects/qwen3-tts/wav-output/output.wav')
with wave.open(str(p), 'rb') as w:
    print(p.resolve())
    print('channels=', w.getnchannels())
    print('sample_width=', w.getsampwidth())
    print('sample_rate=', w.getframerate())
    print('frames=', w.getnframes())
    print('seconds=', w.getnframes() / w.getframerate())
PY
```

检查临时文件：

```bash
find /home/spacemit/projects/qwen3-tts/wav-output -maxdepth 1 -type f -name '.output.wav.part' -print
```
