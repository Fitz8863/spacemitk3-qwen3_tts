# Qwen3-TTS on SpaceMIT K3

## 交互生成 WAV

```bash
cd /home/spacemit/projects/qwen3-tts
./run_interactive.sh
```

输入中文、英文或中英混合文字并回车。每次生成固定保存到：

```text
/home/spacemit/projects/qwen3-tts/wav-output/output.wav
```

下一次输入会覆盖上一次的 `output.wav`，目录中不会按时间戳累积文件。程序不调用 `aplay`，不会自动播放。

退出时输入：

```text
quit
exit
退出
```

## 单次生成

```bash
cd /home/spacemit/projects/qwen3-tts
./run_interactive.sh '你好，这是 Qwen3 TTS。'
```

即使从其他工作目录调用脚本，输出仍固定在项目的 `wav-output/output.wav`：

```bash
cd /tmp
/home/spacemit/projects/qwen3-tts/run_interactive.sh '输出位置仍然固定。'
```

## 长文本

长文本会分段合成，再把各段 PCM 合并到一个有效 WAV 容器。默认分段上限为 32 字符：

```bash
QWEN3_TTS_CHUNK_CHARS=24 ./run_interactive.sh
```

写入时先生成：

```text
/home/spacemit/projects/qwen3-tts/wav-output/.output.wav.part
```

全部成功后通过原子替换覆盖 `output.wav`。如果合成失败，临时文件会被清理，已有的 `output.wav` 不受影响。

## 服务管理

```bash
cd /home/spacemit/projects/qwen3-tts
./start_server.sh
curl -fsS http://127.0.0.1:18080/health
./stop_server.sh
tail -f llama-server.log
```

默认服务地址：`http://127.0.0.1:18080`。

## 当前实现

- 模型：Qwen3-TTS 0.6B，支持中文、英文和中英混合。
- Runtime：SpaceMIT `llama.cpp` 共享线程池优化提交 `787e5fcf`。
- ORT/EP：SpaceMIT ORT 2.0.6。
- 输出：24 kHz、16-bit、单声道、PCM WAV。
- 音色：`qwen3-tts-0.6b/default.spk.bin`。
- 服务端仍是每段完整生成后返回，不是逐 PCM 帧真流式。


## 运行时依赖与目录整理

本项目是面向 SpacemiT K3 的 riscv64 部署目录：

- `runtime/bin/`：当前 Qwen3-TTS 服务实际使用的优化版 `llama-server` 和共享库。
- `qwen3-tts-0.6b/`：模型配置和板端模型文件；大模型文件默认不提交到 GitHub，需在板端单独准备。
- SpaceMIT ONNX Runtime / EP：优先使用系统安装的 `spacemit-onnxruntime`（`/usr/lib/libonnxruntime.so.1`、`/usr/lib/libspacemit_ep.so`）。
- 如需指定其他 ORT 目录，可设置 `QWEN3_TTS_ORT_LIB_DIR=/path/to/lib`。
- `llama-server.log`、`llama-server.pid`、`wav-output/output.wav` 和临时 `.part` 文件均为运行时文件，不提交到仓库。

当前板端已验证系统 ORT 与 Qwen3-TTS Runtime 可以正常加载；服务进程的实际库路径可用以下命令确认：

```bash
pid="$(cat /home/spacemit/projects/qwen3-tts/llama-server.pid)"
grep -E 'libonnxruntime|libspacemit_ep|libggml|libllama|libmtmd' \
  "/proc/$pid/maps" | awk '{print $6}' | sort -u
```
