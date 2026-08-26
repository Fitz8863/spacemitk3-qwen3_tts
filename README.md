# Qwen3-TTS on SpaceMIT K3

这是一个面向 **SpacemiT K3 / riscv64** 的 Qwen3-TTS 0.6B 部署目录。项目包含：

- K3 上运行的优化版 `llama-server` runtime；
- Qwen3-TTS 模型配置和启动脚本；
- OpenAI 风格的 HTTP 语音合成接口；
- 交互式/单次低延迟分段流式播放客户端；
- 多个 speaker embedding 预设；
- 将 WAV/MP3/FLAC 参考录音转换成 K3 所需 `.spk.bin` 的离线脚本；本地 Base checkpoint 可只读取 speaker encoder 权重。

> 当前仓库不提交模型目录中的 ONNX/GGUF 权重和默认运行时 `.spk.bin`，也不提交运行时生成的 WAV、日志和 PID 文件。模型大文件需要在 K3 板端单独准备；`voice_presets/` 中已提交的预设可以直接复制使用。

## 当前验证环境

| 项目 | 值 |
| --- | --- |
| 目标板 | SpaceMIT K3，riscv64 |
| 系统 | Bianbu Linux（板端实际版本以 `cat /etc/os-release` 为准） |
| TTS 模型 | Qwen3-TTS 0.6B |
| 播放 | 24 kHz、16-bit、mono、PCM（通过 aplay 直接播放） |
| 服务 | `http://127.0.0.1:18080` |
| Runtime | SpaceMIT 优化版 `llama-server`，当前构建标识 `787e5fcf` |
| ORT | SpaceMIT ONNX Runtime / EP |
| 音色配置 | `qwen3-tts-0.6b/config.json` 中的 `tts_model.speaker_file`；文件格式为 raw `float32[1024]` |

## 目录结构

```text
.
├── README.md
├── AGENTS.md
├── start_server.sh
├── stop_server.sh
├── run_interactive.sh
├── qwen3_tts_interactive.py
├── qwen3-tts-0.6b/
│   ├── config.json
│   ├── manifest.json
│   ├── anke.spk.bin          # real_time 分支已包含；其他分支按配置准备
│   ├── *.gguf              # 板端准备；默认被 .gitignore 忽略
│   └── *.onnx              # 板端准备；默认被 .gitignore 忽略
├── runtime/bin/               # riscv64 预编译 runtime
├── voice_embeddings/
│   ├── extract_speaker_embedding.py
│   └── README.md
└── voice_presets/
    ├── README.md
    ├── manifest.json
    └── embeddings/*.spk.bin
```

## 一、准备环境

### 0. 设置本地仓库和开发板连接信息

以下命令不依赖某台机器的固定目录。先在开发机上克隆仓库，并把 `BOARD_SSH` 改成你自己的 SSH 用户和主机（也可以使用 `~/.ssh/config` 中的别名）：

```bash
git clone https://github.com/Fitz8863/spacemitk3-qwen3_tts.git
cd spacemitk3-qwen3_tts
export BOARD_SSH="user@your-k3-host"
```

本文后续在开发机执行的命令默认从当前仓库根目录运行，在 K3 板端执行的命令使用 `~/qwen3-tts`。如果你把仓库克隆到其他目录，只需要先 `cd` 到该目录；如果板端项目目录不同，请把示例中的 `~/qwen3-tts` 统一替换为你的目录。

### 1. 在 K3 板端准备系统依赖

```bash
ssh "$BOARD_SSH"
sudo apt update
sudo apt install -y curl ca-certificates file ffmpeg
```

确认架构和编译工具：

```bash
uname -m
cmake --version
riscv64-linux-gnu-g++ --version || true
```

当前服务优先使用板端系统中的：

```text
/usr/lib/libonnxruntime.so.1
/usr/lib/libspacemit_ep.so
```

如果 ORT 安装在其他目录，可以通过 `QWEN3_TTS_ORT_LIB_DIR` 指定。

### 2. 获取模型权重和模型文件

GitHub 仓库**不会提交**模型目录中的以下大文件：

```text
qwen3-tts-0.6b/*.gguf
qwen3-tts-0.6b/*.onnx
qwen3-tts-0.6b/*.bin
```

`real_time` 分支中的 `qwen3-tts-0.6b/anke.spk.bin` 是随分支提供的 4096 字节音色预设，便于直接使用；其他模型大文件仍需另外下载。如果切换到其他分支或使用自定义音色，请确保 `config.json` 中 `speaker_file` 指向的文件存在。

这是有意的：这些文件体积较大，而且模型权重、参考音色和运行时有各自的许可证及分发条款。克隆 GitHub 仓库后，必须按照下面的步骤另外下载并放到 K3 板端。

#### 2.1 K3 部署应下载哪个模型仓库

当前 K3 runtime 使用的是 SpaceMIT 已经转换好的 split-runtime 模型包：

- **K3 推理模型包**：[`SpacemiT/Qwen3-TTS-0.6B`](https://huggingface.co/SpacemiT/Qwen3-TTS-0.6B)
- **上游 Base 模型**：[`Qwen/Qwen3-TTS-12Hz-0.6B-Base`](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-0.6B-Base)

K3 推理时应优先使用 `SpacemiT/Qwen3-TTS-0.6B`。它已经按照 SpaceMIT runtime 的方式拆分为 ONNX、GGUF 和 speaker embedding 文件，不能把上游 Hugging Face Base checkpoint 原样复制到 `qwen3-tts-0.6b/` 后直接启动。

上游 `Qwen/Qwen3-TTS-12Hz-0.6B-Base` 主要用于开发机上的参考音频音色提取和 Python 端 voice cloning，具体见[第六节](#六从-wavmp3flac-克隆音色)。它不是当前 K3 `llama-server` 直接读取的文件布局。

SpaceMIT 模型仓库当前列出的 K3 运行所需文件如下：

| SpaceMIT 下载文件名 | 放入本项目后的文件名 | 用途 |
| --- | --- | --- |
| `Qwen3-TTS-0.6B-tokenizer.gguf` | `tokenizer.gguf` | tokenizer 元数据 |
| `Qwen3-TTS-0.6B-text-embed-proj.fp32.onnx` | `text-embed-proj.onnx` | 文本 embedding/projection |
| `Qwen3-TTS-0.6B-codec-decoder-t50.dynq.onnx` | `codec-decoder-t50-q.onnx` | codec decoder |
| `Qwen3-TTS-0.6B-talker-q8_0.gguf` | `talker-q8_0.gguf` | talker |
| `Qwen3-TTS-0.6B-code-predictor-q4_0.gguf` | `code-predictor-q4_0.gguf` | code predictor |
| `Qwen3-TTS-0.6B-aux.gguf` | `qwen3-tts-aux.gguf` | runtime auxiliary tensors |
| `default.spk.bin` | `default.spk.bin` | 默认 speaker embedding |

项目中的 `config.json`、`manifest.json` 和 `SHA256SUMS` 已经随仓库提供，不要用下载包中的 `configs/K3/config.json` 直接覆盖项目配置；项目配置包含当前脚本和 runtime 使用的文件名。

#### 2.2 推荐方式：在开发机下载，再复制到 K3

推荐在有稳定网络的 x86_64 Linux 开发机或服务器下载，再通过 `scp` 复制到板端。先安装 Hugging Face CLI：

```bash
python3 -m pip install -U huggingface_hub
```

使用 `hf download` 下载 K3 模型包中的全部文件到临时目录：

```bash
mkdir -p /tmp/Qwen3-TTS-0.6B
hf download SpacemiT/Qwen3-TTS-0.6B \
  --include \
  'Qwen3-TTS-0.6B-tokenizer.gguf' \
  'Qwen3-TTS-0.6B-text-embed-proj.fp32.onnx' \
  'Qwen3-TTS-0.6B-codec-decoder-t50.dynq.onnx' \
  'Qwen3-TTS-0.6B-talker-q8_0.gguf' \
  'Qwen3-TTS-0.6B-code-predictor-q4_0.gguf' \
  'Qwen3-TTS-0.6B-aux.gguf' \
  'default.spk.bin' \
  --local-dir /tmp/Qwen3-TTS-0.6B
```

如果系统没有 `hf` 命令，可以使用旧版兼容命令：

```bash
huggingface-cli download SpacemiT/Qwen3-TTS-0.6B \
  --include 'Qwen3-TTS-0.6B-*' 'default.spk.bin' \
  --local-dir /tmp/Qwen3-TTS-0.6B
```

下载完成后，先检查临时目录中的文件：

```bash
find /tmp/Qwen3-TTS-0.6B -maxdepth 1 -type f -printf '%f: %s bytes\n' | sort
```

然后复制到板端临时目录：

```bash
ssh "$BOARD_SSH" 'mkdir -p ~/qwen3-tts/.model-download'
scp /tmp/Qwen3-TTS-0.6B/{Qwen3-TTS-0.6B-tokenizer.gguf,Qwen3-TTS-0.6B-text-embed-proj.fp32.onnx,Qwen3-TTS-0.6B-codec-decoder-t50.dynq.onnx,Qwen3-TTS-0.6B-talker-q8_0.gguf,Qwen3-TTS-0.6B-code-predictor-q4_0.gguf,Qwen3-TTS-0.6B-aux.gguf,default.spk.bin} \
  "${BOARD_SSH}:~/qwen3-tts/.model-download/"
```

在板端将官方文件名映射为本项目配置使用的文件名：

```bash
ssh "$BOARD_SSH"
cd ~/qwen3-tts
mkdir -p qwen3-tts-0.6b

install -m 0644 .model-download/Qwen3-TTS-0.6B-tokenizer.gguf \
  qwen3-tts-0.6b/tokenizer.gguf
install -m 0644 .model-download/Qwen3-TTS-0.6B-text-embed-proj.fp32.onnx \
  qwen3-tts-0.6b/text-embed-proj.onnx
install -m 0644 .model-download/Qwen3-TTS-0.6B-codec-decoder-t50.dynq.onnx \
  qwen3-tts-0.6b/codec-decoder-t50-q.onnx
install -m 0644 .model-download/Qwen3-TTS-0.6B-talker-q8_0.gguf \
  qwen3-tts-0.6b/talker-q8_0.gguf
install -m 0644 .model-download/Qwen3-TTS-0.6B-code-predictor-q4_0.gguf \
  qwen3-tts-0.6b/code-predictor-q4_0.gguf
install -m 0644 .model-download/Qwen3-TTS-0.6B-aux.gguf \
  qwen3-tts-0.6b/qwen3-tts-aux.gguf
install -m 0644 .model-download/default.spk.bin \
  qwen3-tts-0.6b/default.spk.bin
```

> 如果已经准备好自定义的 `anke.spk.bin` 或其他音色文件，不要用下载包中的 `default.spk.bin` 覆盖自定义文件。默认音色和自定义音色都应放在 `qwen3-tts-0.6b/`，再通过 `config.json` 的 `speaker_file` 选择。

#### 2.3 直接在 K3 板端下载

如果 K3 可以访问 Hugging Face，也可以直接在板端下载。先确认 `hf` 命令可用：

```bash
ssh "$BOARD_SSH"
python3 -m pip install --user -U huggingface_hub
```

然后在项目目录外的临时目录下载，避免把官方长文件名直接混入项目模型目录：

```bash
cd ~/qwen3-tts
mkdir -p .model-download
hf download SpacemiT/Qwen3-TTS-0.6B \
  --include \
  'Qwen3-TTS-0.6B-tokenizer.gguf' \
  'Qwen3-TTS-0.6B-text-embed-proj.fp32.onnx' \
  'Qwen3-TTS-0.6B-codec-decoder-t50.dynq.onnx' \
  'Qwen3-TTS-0.6B-talker-q8_0.gguf' \
  'Qwen3-TTS-0.6B-code-predictor-q4_0.gguf' \
  'Qwen3-TTS-0.6B-aux.gguf' \
  'default.spk.bin' \
  --local-dir .model-download
```

执行上一小节中的 `install` 命令完成文件名映射。

如果板端网络无法访问 Hugging Face，使用 2.2 节的“开发机下载 + `scp`”方式；不要反复在板端执行不完整下载。

#### 2.4 下载后检查模型文件

在 K3 板端执行：

```bash
cd ~/qwen3-tts

required=(
  tokenizer.gguf
  text-embed-proj.onnx
  codec-decoder-t50-q.onnx
  talker-q8_0.gguf
  code-predictor-q4_0.gguf
  qwen3-tts-aux.gguf
  default.spk.bin
)

for f in "${required[@]}"; do
  test -s "qwen3-tts-0.6b/$f" || {
    echo "missing or empty: qwen3-tts-0.6b/$f" >&2
    exit 1
  }
done

stat -c '%n: %s bytes' qwen3-tts-0.6b/*.{gguf,onnx,bin}
sha256sum -c qwen3-tts-0.6b/SHA256SUMS
```

`sha256sum -c` 全部通过后，再启动服务：

```bash
./start_server.sh
curl -fsS http://127.0.0.1:18080/health
echo
```

`SHA256SUMS` 对应的是项目重命名后的文件名。如果你替换了 talker、codec、speaker 或其他模型文件，不要继续沿用旧的哈希值；应重新生成校验值并在部署记录中标注来源、版本和修改原因。

#### 2.5 哪些文件不需要从模型仓库下载

以下文件已经由本项目管理，通常不需要从模型仓库重新下载：

```text
runtime/bin/llama-server
runtime/bin/libggml*.so*
runtime/bin/libllama*.so*
runtime/bin/libmtmd.so*
start_server.sh
stop_server.sh
run_interactive.sh
qwen3-tts-0.6b/config.json
qwen3-tts-0.6b/manifest.json
```

其中 `runtime/bin/` 是针对 K3/riscv64 的预编译 runtime；它和模型权重是两套不同的东西。若需要从源码重新编译或更换 runtime，请看[第三节：从源码编译 runtime](#三从源码编译-runtime)。

## 二、直接使用仓库中的预编译 runtime

这是当前 K3 最推荐的方式。先把项目放到板端约定目录：

```bash
ssh "$BOARD_SSH" 'mkdir -p ~/qwen3-tts'
rsync -a --delete \
  --exclude='qwen3-tts-0.6b/*.gguf' \
  --exclude='qwen3-tts-0.6b/*.onnx' \
  ./ "${BOARD_SSH}:~/qwen3-tts/"
```

然后在板端补齐模型大文件：

```bash
ssh "$BOARD_SSH"
cd ~/qwen3-tts
chmod +x start_server.sh stop_server.sh run_interactive.sh
./start_server.sh
curl -fsS http://127.0.0.1:18080/health
```

启动脚本会自动选择：

1. `QWEN3_TTS_ORT_LIB_DIR`；
2. 项目内旧版 ORT 目录（如果存在）；
3. `/usr/lib`；
4. `/usr/local/lib`。

也可以显式指定：

```bash
QWEN3_TTS_ORT_LIB_DIR=/usr/lib ./start_server.sh
```

如果启动失败，先看动态库和日志：

```bash
file runtime/bin/llama-server
LD_LIBRARY_PATH=/usr/lib:runtime/bin \
  ldd runtime/bin/llama-server | grep -E 'not found|onnxruntime|spacemit|llama|ggml'
tail -200 llama-server.log
```

`runtime/bin/llama-server` 是 riscv64 ELF，不能在 x86_64 开发机上直接运行；开发机只能进行 Python 客户端检查或交叉编译。

## 三、从源码编译 runtime

### 重要说明

当前仓库提交的是已经编译好的 K3 runtime，而不是 SpaceMIT 定制版 `llama.cpp` 完整源码。普通上游 `llama.cpp` 编译出来的 `llama-server` 不一定包含本项目所需的：

- `--media-backend smt`；
- `--smt-config-dir`；
- Qwen3-TTS 的 ONNX/codec/talker 组合；
- SpaceMIT ONNX Runtime Execution Provider；
- K3 的 riscv64 优化和线程配置。

所以不能只从上游仓库执行一次普通 CMake，就宣称能够复现当前二进制。必须取得与当前 runtime 对应的 SpaceMIT fork、TTS patch 和 ORT SDK，并确认源码版本与 `787e5fcf` 对齐。

### A. 在 K3 板上原生编译

准备与当前 runtime 对应的 SpaceMIT 源码目录，例如：

```bash
ssh "$BOARD_SSH"
cd ~/src
# 将 SpaceMIT 定制版 llama.cpp 源码放到此处
cd llama.cpp

git checkout 787e5fcf
cmake -S . -B build-k3 \
  -DCMAKE_BUILD_TYPE=Release \
  -DGGML_NATIVE=ON \
  -DLLAMA_BUILD_SERVER=ON
cmake --build build-k3 --target llama-server -j"$(nproc)"
```

如果 SpaceMIT 的源码使用了不同的 CMake 选项，以该源码的 README/CMake 输出为准。编译完成后先检查：

```bash
file build-k3/bin/llama-server
```

输出应当是 riscv64 ELF。不要把 x86_64 的开发机二进制复制到 K3。

### B. 在 x86_64 开发机交叉编译

准备 riscv64 交叉工具链和对应 sysroot，然后创建 CMake toolchain 文件：

```cmake
# /tmp/k3-riscv64-toolchain.cmake
set(CMAKE_SYSTEM_NAME Linux)
set(CMAKE_SYSTEM_PROCESSOR riscv64)
set(CMAKE_C_COMPILER riscv64-linux-gnu-gcc)
set(CMAKE_CXX_COMPILER riscv64-linux-gnu-g++)
# 按实际 SDK 修改：
# set(CMAKE_SYSROOT /path/to/k3-sysroot)
```

执行：

```bash
cmake -S /path/to/spacemit-llama.cpp -B /tmp/qwen3-tts-build \
  -DCMAKE_TOOLCHAIN_FILE=/tmp/k3-riscv64-toolchain.cmake \
  -DCMAKE_BUILD_TYPE=Release \
  -DGGML_NATIVE=OFF \
  -DLLAMA_BUILD_SERVER=ON
cmake --build /tmp/qwen3-tts-build --target llama-server -j"$(nproc)"
```

交叉编译时要同时准备目标板上的：

```text
libonnxruntime.so.1
libspacemit_ep.so
libggml*.so
libllama*.so
libmtmd*.so
```

如果源码还依赖 SpaceMIT 专用头文件或静态库，需把 SDK 的 include/lib 路径通过 `CMAKE_PREFIX_PATH`、`CMAKE_INCLUDE_PATH`、`CMAKE_LIBRARY_PATH` 或源码项目规定的变量传入。

### C. 安装自编译 runtime

不要直接覆盖旧 runtime。先另建目录验证：

```bash
mkdir -p runtime-k3-test/bin runtime-k3-test/lib
cp /path/to/build-k3/bin/llama-server runtime-k3-test/bin/
cp /path/to/build-k3/lib/*.so* runtime-k3-test/lib/ 2>/dev/null || true
```

用环境变量试运行：

```bash
QWEN3_TTS_RUNTIME="$PWD/runtime-k3-test" \
QWEN3_TTS_ORT_LIB_DIR=/usr/lib \
./start_server.sh
```

确认健康检查和一次合成都成功后，再将 `runtime-k3-test` 复制为正式的 `runtime/`。失败时保留 `llama-server.log`，不要删除旧版本，便于回滚。

## 四、启动服务和生成语音

启动/停止：

```bash
./start_server.sh
curl -fsS http://127.0.0.1:18080/health
./stop_server.sh
```

直接调用 OpenAI 风格接口：

```bash
curl -f http://127.0.0.1:18080/v1/audio/speech \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "qwen3-tts",
    "input": "你好，这是 Qwen3-TTS。",
    "voice": "default",
    "response_format": "wav",
    "speed": 1.0
  }' \
  -o wav-output/output.wav
```

交互客户端：

```bash
./run_interactive.sh
./run_interactive.sh '你好，这是一次低延迟播放测试。'
QWEN3_TTS_CHUNK_CHARS=24 ./run_interactive.sh '这里是一段较长的文本。'
```

当前默认行为是**低延迟直接播放，不保存 WAV**：每段 HTTP WAV 返回后立即解包为 PCM，首段就绪就启动一个长生命周期的 `aplay`，后续分段继续生成并直接送入同一个播放器。若只做合成测速而不播放：

```bash
./run_interactive.sh --no-play '这次只合成、不播放。'
```

可调的低延迟参数：

```text
QWEN3_TTS_CHUNK_CHARS=24             # 分段越短，首播通常越早，但段间空隙风险越高
QWEN3_TTS_STREAM_LOW_LATENCY=1       # 默认；首个完整分段返回后立即播放
QWEN3_TTS_STREAM_LOW_LATENCY=0       # 保守模式；按 RTF 估算后再预缓冲
QWEN3_TTS_PLAYBACK_BUFFER_US=250000   # ALSA/PipeWire 播放缓冲，默认 250 ms
QWEN3_TTS_PLAYBACK_PERIOD_US=30000    # 播放周期，默认 30 ms
QWEN3_TTS_PLAYBACK_START_DELAY_US=0   # 播放启动额外延时，默认 0
QWEN3_TTS_PLAYBACK_QUEUE_SEGMENTS=2   # 客户端最多排队的完整分段数
```

播放缓冲已经从开发板默认约 500 ms 降到约 250 ms，周期约 30 ms。这样比原实现更低延迟，同时给板端 PipeWire 桥接保留一定抗 underrun 余量。继续减小可能降低延迟，但更容易触发 ALSA/PipeWire underrun，出现爆音或短暂停顿；如果板端音频设备不稳定，可临时恢复：

```bash
QWEN3_TTS_PLAYBACK_BUFFER_US=400000 QWEN3_TTS_PLAYBACK_PERIOD_US=50000 ./run_interactive.sh
```

服务端目前仍是“每段文本完整生成后返回”，不是逐 PCM 帧真流式。因此首播下限仍然是**第一段的完整生成耗时**；仅修改 Python 客户端无法在第一段 WAV 返回前播放。当前低延迟模式优先缩短首播，接受在 RTF 大于 1 时后续分段之间可能出现短暂停顿。要做到单段内部边生成边播放，需要继续改造 C++ talker/codec 回调和 HTTP 传输协议。

## 实时性和 RTF

交互客户端现在会在每段合成完成后打印 RTF，并在播放结束后打印总 RTF。例如：

```text
第 1/1 段合成完成：耗时 3.17 秒；音频时长 2.72 秒；RTF=1.17（越小越实时）
开始低延迟播放：首段已就绪，已缓存 2.72 秒音频；后续分段将边生成边播放
播放完成。
音频时长：2.72 秒；合成耗时：3.18 秒；播放结束耗时：5.90 秒；首播延迟：3.18 秒
本轮 RTF：1.17（服务端统计；越小越实时；RTF<1 表示快于实时）
```

### RTF 的定义

实时因子（Real-Time Factor，RTF）定义为：

```text
RTF = 生成耗时 / 生成音频时长
```

因此：

| RTF | 含义 |
| ---: | --- |
| `< 1.0` | 生成速度快于播放速度，可以实时生成 |
| `= 1.0` | 大约与播放速度相同 |
| `> 1.0` | 生成速度慢于播放速度，不能在生成过程中完全追上实时播放 |

RTF 越小越实时。RTF `1.20` 表示生成 1 秒音频大约需要 1.20 秒；RTF `0.80` 表示生成 1 秒音频大约需要 0.80 秒。

### 客户端打印的数据来源

Qwen3-TTS 服务端在 WAV HTTP 响应中提供以下统计头：

```text
X-TTS-Audio-Seconds
X-TTS-Wall-Seconds
X-TTS-RTF
X-TTS-Segments
```

客户端优先使用服务端的 `X-TTS-RTF`。多段文本的“本轮 RTF”按所有分段的服务端 wall time 和音频时长合计计算，而不是简单平均每段 RTF。这样更能反映整轮请求的实际生成速度。

如果连接到旧版服务端、响应中没有 `X-TTS-RTF`，客户端会使用 HTTP 请求耗时除以音频时长作为后备估计，并标记为“客户端测量”。这个后备值包含网络和 HTTP 往返时间；在本机 `127.0.0.1` 调用时影响通常较小，远程调用时不能与服务端纯生成 RTF 直接等价。

### 查看 RTF

交互模式：

```bash
cd ~/qwen3-tts
./run_interactive.sh
```

单次模式也会打印 RTF：

```bash
./run_interactive.sh '你好，这是一次实时性测试。'
```

如果只想直接查看 HTTP 响应头：

```bash
curl -sS -D - -o /tmp/qwen3-tts-test.wav \
  http://127.0.0.1:18080/v1/audio/speech \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "qwen3-tts",
    "input": "你好，这是一次实时性测试。",
    "voice": "default",
    "response_format": "wav",
    "speed": 1.0
  }'
```

### 2026 年 8 月 25 日 K3 实测结果

测试环境：

```text
板端：SpaceMIT K3 / riscv64
服务：127.0.0.1:18080
Runtime：runtime/bin/llama-server
SpaceMIT ORT：/usr/lib/libonnxruntime.so.1 + /usr/lib/libspacemit_ep.so
输出：24 kHz、16-bit、mono WAV
```

本次测试使用当前板端运行配置：

```text
frontend_threads=2
codec_threads=4
talker_threads=4
SPACEMIT_EP_INTRA_THREAD_NUM=4
SPACEMIT_EP_INTER_THREAD_NUM=1
主进程 CPU affinity=0-7
```

每种文本连续请求 3 次，结果如下；服务端 RTF 取 3 次结果的中位数：

| 测试文本 | 文本长度 | 音频时长 | 服务端 RTF（3 次） | 中位数 |
| --- | ---: | ---: | --- | ---: |
| 短句 | 13 字符 | 2.72 秒 | 1.172 / 1.177 / 1.211 | 1.177 |
| 中等句 | 45 字符 | 8.92 秒 | 1.111 / 1.132 / 1.123 | 1.123 |
| 长句 | 81 字符 | 13.12 秒 | 1.082 / 1.090 / 1.096 | 1.090 |

结论：当前配置下，服务端 RTF 约为 `1.09-1.18`。短句受模型初始化、请求和分段固定开销影响更明显；文本变长后固定开销被摊薄，RTF 下降，但当前仍略大于 `1.0`，所以不能认为已经达到严格意义上的实时生成。

2026 年 8 月 26 日本次流式回归又测得：短句服务端 RTF `1.188`，两段中等文本服务端 RTF `1.212`；因此客户端默认使用 `QWEN3_TTS_STREAM_RTF_HINT=1.20`，再乘以 `QWEN3_TTS_STREAM_RTF_SAFETY=1.05` 作为预缓冲保护值。实际每轮仍优先使用响应头中的服务端 RTF 动态修正。

这次测试证明的是服务端 HTTP 请求级生成速度，不是逐 PCM 帧流式速度。当前服务仍然是“整段生成完成后返回 WAV”，Python 客户端现在默认在第一段完整返回后立即播放，并一边播放一边生成后续分段；若要在单个分段内部边解码边播放，仍需改造 C++ talker/codec 回调和 HTTP 传输协议。

### 影响 RTF 的主要因素

可以按下面顺序调优并重新测试：

1. 保持 `sample_rate=24000`、`max_frames=160` 和 `max_prefill=128` 不变，先建立可比较基线；
2. 调整 `SPACEMIT_EP_INTRA_THREAD_NUM`，例如比较 `4` 和 `8`；
3. 在不造成线程争抢的前提下比较 `codec_threads=4/8`、`talker_threads=4/8`；
4. 使用同一段文本连续测试至少 3 次，报告中位数，不要只看一次结果；
5. 同时记录 CPU affinity、EP worker 数量、模型音色和 runtime/ORT 版本。

增大线程数不保证 RTF 一定下降；如果线程池争抢、内存压力或驱动调度开销增加，RTF 可能变差。

## CPU、A100/X100 核与 SpaceMIT EP 线程配置

本节区分三个容易混淆的概念：

1. **Linux CPU 编号/亲和性**：由 `taskset` 和 `/proc/<pid>/task/<tid>/status` 表示；
2. **SpaceMIT EP worker 线程数**：由模型 `config.json` 中的 `SPACEMIT_EP_INTRA_THREAD_NUM` 等参数控制；
3. **实际 AI Core/算力单元调度**：由 SpaceMIT EP 和驱动完成，线程数或 `taskset` 不能单独证明某个算子实际使用了多少 AI Core。

### K3 上的 CPU 编号

当前这块 K3 板实测有 16 个 Linux CPU 编号：

```text
0-7   = X100
8-15  = A100
```

可在板端确认：

```bash
ssh "$BOARD_SSH"
lscpu
lscpu -e=CPU,CORE,SOCKET,NODE,ONLINE
cat /sys/devices/system/cpu/online
```

当前 runtime 启动日志还会打印类似信息：

```text
num_cores: 16
num_perfer_cores: 8
cpu_mask: ff00
aicpu_id_offset: 8
```

其中 `cpu_mask: ff00` 对应的首选范围是 Linux CPU `8-15`。这表示 runtime/EP 的默认首选 A100 范围，不等于已经通过用户配置选择了某几个固定 A100。

### 当前 Qwen3-TTS 的线程配置

配置文件：

```text
qwen3-tts-0.6b/config.json
```

当前相关配置：

```json
{
  "frontend_threads": 2,
  "codec_threads": 4,
  "talker_threads": 4,
  "ep_config": {
    "SPACEMIT_EP_INTRA_THREAD_NUM": "4",
    "SPACEMIT_EP_INTER_THREAD_NUM": "1"
  }
}
```

参数含义：

| 参数 | 作用 |
| --- | --- |
| `frontend_threads` | 文本前处理和前端阶段线程数 |
| `codec_threads` | codec 解码阶段线程数 |
| `talker_threads` | talker 阶段线程数 |
| `SPACEMIT_EP_INTRA_THREAD_NUM` | SpaceMIT EP 的内部并行线程数 |
| `SPACEMIT_EP_INTER_THREAD_NUM` | SpaceMIT EP 图间并行线程数 |

`SPACEMIT_EP_INTRA_THREAD_NUM=4` 只表示启动 4 个 EP worker，不表示选择 A100 `12-15`。当前 Qwen3-TTS runtime 没有在 `config.json` 中暴露类似下面的固定核列表配置：

```json
{
  "SPACEMIT_EP_AFFINITY": "12;13;14;15"
}
```

因此保持线程数为 4 时，当前默认分配通常是 A100 `8-11`。修改线程配置后必须重启服务：

```bash
python3 - <<'PY'
import json
from pathlib import Path

p = Path('qwen3-tts-0.6b/config.json')
data = json.loads(p.read_text())
tts = data['tts_model']
tts['frontend_threads'] = 2
tts['codec_threads'] = 4
tts['talker_threads'] = 4
tts['ep_config']['SPACEMIT_EP_INTRA_THREAD_NUM'] = '4'
tts['ep_config']['SPACEMIT_EP_INTER_THREAD_NUM'] = '1'
p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
PY

./stop_server.sh
./start_server.sh
```

### 使用全部 8 个 A100

如果希望 EP 申请全部 8 个首选 A100，可以将内部线程数改为 8：

```bash
python3 - <<'PY'
import json
from pathlib import Path

p = Path('qwen3-tts-0.6b/config.json')
data = json.loads(p.read_text())
data['tts_model']['ep_config']['SPACEMIT_EP_INTRA_THREAD_NUM'] = '8'
p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
PY

./stop_server.sh
./start_server.sh
```

这表示申请 A100 `8-15` 的 8 个 EP worker，**不是只使用后四个 A100**。如果机器上已经有旧的 `llama-server`，必须先用本项目的 `stop_server.sh` 停止，避免第二个实例报：

```text
Not enough available AI cores for the thread pool
```

### 只使用后四个 A100：当前版本的临时方法

当前预编译 Qwen3-TTS runtime 没有提供持久化的 A100 核列表接口。服务已经启动并且 EP worker 仍显示为单核 `8`、`9`、`10`、`11` 时，可以将这四个 worker 一一绑定到 `12`、`13`、`14`、`15`：

```bash
cd ~/qwen3-tts
pid="$(cat llama-server.pid)"
kill -0 "$pid"

for source_cpu in 8 9 10 11; do
    target_cpu=$((source_cpu + 4))
    for status in /proc/"$pid"/task/*/status; do
        tid="${status%/status}"
        tid="${tid##*/}"
        affinity="$(awk '/^Cpus_allowed_list:/{print $2}' "$status")"
        if [[ "$affinity" == "$source_cpu" ]]; then
            echo "TID=$tid: CPU $source_cpu -> CPU $target_cpu"
            taskset -pc "$target_cpu" "$tid"
            break
        fi
    done
done
```

检查结果：

```bash
for status in /proc/"$pid"/task/*/status; do
    tid="${status%/status}"
    tid="${tid##*/}"
    affinity="$(awk '/^Cpus_allowed_list:/{print $2}' "$status")"
    case "$affinity" in
        12|13|14|15)
            echo "TID=$tid affinity=$affinity"
            ;;
    esac
done
```

注意：这种方式只修改**当前进程**。重启服务后，runtime 可能重新把四个 worker 放回 `8-11`。如果需要每次启动自动固定到 `12-15`，需要在 `start_server.sh` 中增加启动后自动识别 EP worker 并执行绑核的逻辑，或者重新编译暴露 affinity 配置的 SpaceMIT runtime。

### X100 CPU 亲和性

如果只想限制 `llama-server` 的普通 Linux CPU 调度范围，可以使用 `taskset`：

```bash
# 允许主进程和普通 CPU 线程使用全部 X100
cd ~/qwen3-tts
taskset -c 0-7 ./start_server.sh
```

也可以选择指定的 X100 子集：

```bash
taskset -c 0,2,4,6 ./start_server.sh
```

这里的 `taskset` 主要约束启动进程继承的 Linux CPU affinity，**不等价于选择 AI Core**。SpaceMIT EP worker 可能在初始化时使用自己的 A100 affinity；要确认实际结果，必须检查每个线程的 `Cpus_allowed_list`。

某些 SSH 登录会话本身已经被限制在 CPU `0-7`。可以先查看：

```bash
taskset -pc $$
```

如果输出是：

```text
current affinity list: 0-7
```

那么在这个 Shell 中直接执行：

```bash
taskset -c 12-15 ./start_server.sh
```

可能会得到：

```text
taskset: failed to set pid ... affinity: Invalid argument
```

这不是 Qwen3-TTS 配置文件错误，而是当前 Shell/cpuset 不允许把自身扩展到 `12-15`。此时应使用上面的 EP worker 临时绑核方法，或通过系统级 service/cpuset 配置放开对应 CPU。

### 验证线程和核是否生效

先确认服务 PID 和健康状态：

```bash
cd ~/qwen3-tts
pid="$(cat llama-server.pid)"
ps -o pid,ppid,pgid,sid,stat,psr,pcpu,args -p "$pid"
curl -fsS http://127.0.0.1:18080/health
echo
```

查看主进程允许使用的 CPU：

```bash
taskset -pc "$pid"
grep -E 'Threads|Cpus_allowed_list' "/proc/$pid/status"
```

查看所有线程当前运行 CPU 和允许的 CPU：

```bash
ps -T -p "$pid" -o spid,psr,pcpu,stat,comm

for status in /proc/"$pid"/task/*/status; do
    tid="${status%/status}"
    tid="${tid##*/}"
    printf 'TID=%s ' "$tid"
    grep 'Cpus_allowed_list' "$status"
done
```

查看 SpaceMIT EP 是否加载：

```bash
grep -E 'libonnxruntime|libspacemit_ep' \
    "/proc/$pid/maps" | awk '{print $6}' | sort -u
```

需要注意：

- `Cpus_allowed_list` 证明的是 Linux 线程 affinity；
- `SPACEMIT_EP_INTRA_THREAD_NUM` 证明的是 EP 线程配置；
- `libspacemit_ep.so` 出现在 maps 中证明加载了 SpaceMIT EP；
- 这些证据不能单独证明所有算子都在 AI Core 上执行，也不能仅凭线程数推断实际使用了几个硬件 AI Core；
- 若需要 AI Core 利用率、调度数量或算子级归属，需要 SpaceMIT 驱动日志、profile 或设备计数器。

### 服务启动和停止的 PID 注意事项

启动脚本会把 PID 写入：

```text
llama-server.pid
```

但如果启动过程中遇到旧服务已经占用端口、或新进程初始化失败，旧版本脚本可能把已经退出的新 PID 写入 PID 文件。当前 `stop_server.sh` 已改进为：

- 校验 PID 对应的完整 `llama-server` 命令行；
- PID 文件失效或丢失时扫描当前项目的实际 Qwen3-TTS 进程；
- 优先向 `setsid` 创建的服务进程组发送 `SIGTERM`；
- 超时后只对已确认的目标进程发送 `SIGKILL`；
- 确认所有目标退出后才删除 PID 文件。

推荐始终使用：

```bash
./stop_server.sh
```

不要使用：

```bash
killall llama-server
pkill -f llama-server
```

因为这些命令可能误杀其他项目的服务。

## 五、音色格式和更换方式

### 5.1 音色文件和配置关系

当前服务启动时会读取模型目录中的 `config.json`，再根据下面的配置加载一个固定的 speaker embedding：

```json
{
  "tts_model": {
    "speaker_file": "default.spk.bin"
  }
}
```

`speaker_file` 是相对于模型目录的文件名。假设项目目录是：

```text
~/qwen3-tts
```

那么配置：

```json
"speaker_file": "anke.spk.bin"
```

实际读取的文件就是：

```text
~/qwen3-tts/qwen3-tts-0.6b/anke.spk.bin
```

`.spk.bin` 文件必须满足：

```text
raw little-endian float32[1024]
4096 bytes
```

它不是 WAV 文件，不能直接把 `.wav` 改名为 `.spk.bin` 使用。可以先用仓库中的音色预设，或者按照[第六节](#六从-wavmp3flac-克隆音色)从参考录音提取。

仓库中已经提供的预设位于：

```text
voice_presets/embeddings/
├── anke.spk.bin
├── qwen_clone.spk.bin
├── qwen_clone_1.spk.bin
└── qwen_clone_2.spk.bin
```

查看某个文件是否符合格式：

```bash
stat -c '%n: %s bytes' voice_presets/embeddings/*.spk.bin
```

每个文件应显示 `4096 bytes`。

> 重要：音色 embedding 是服务启动时加载的。只复制文件或修改 `config.json`，不会改变已经运行的 `llama-server`；修改后必须重启 TTS 服务。

### 5.2 推荐流程：切换到一个新音色

下面以 `anke.spk.bin` 为例。命令分为“开发机”和“K3 板端”两部分，请不要把两个环境的路径混用。

#### 第一步：从开发机复制音色到 K3 板端

在本地开发机的仓库目录执行：

```bash
# 在开发机的仓库根目录执行
scp voice_presets/embeddings/anke.spk.bin \
  "${BOARD_SSH}:~/qwen3-tts/qwen3-tts-0.6b/anke.spk.bin"
```

在板端确认文件已经到位：

```bash
ssh "$BOARD_SSH"
cd ~/qwen3-tts
stat -c '%n: %s bytes' qwen3-tts-0.6b/anke.spk.bin
```

应显示：

```text
qwen3-tts-0.6b/anke.spk.bin: 4096 bytes
```

如果使用其他预设，只需要替换上面命令中的文件名。例如切换到 `qwen_clone.spk.bin`：

```bash
# 开发机执行
scp voice_presets/embeddings/qwen_clone.spk.bin \
  "${BOARD_SSH}:~/qwen3-tts/qwen3-tts-0.6b/qwen_clone.spk.bin"
```

#### 第二步：在板端备份当前配置

```bash
cd ~/qwen3-tts

cp -p qwen3-tts-0.6b/config.json \
  "qwen3-tts-0.6b/config.json.before-voice-$(date +%Y%m%d-%H%M%S)"
```

先查看当前实际音色：

```bash
python3 - <<'PY'
import json
from pathlib import Path

p = Path('qwen3-tts-0.6b/config.json')
data = json.loads(p.read_text(encoding='utf-8'))
print(data['tts_model']['speaker_file'])
PY
```

#### 第三步：修改 `speaker_file`

推荐使用 Python 修改 JSON，不要用容易误替换其他字段的复杂 `sed` 命令：

```bash
python3 - <<'PY'
import json
from pathlib import Path

p = Path('qwen3-tts-0.6b/config.json')
data = json.loads(p.read_text(encoding='utf-8'))
data['tts_model']['speaker_file'] = 'anke.spk.bin'
p.write_text(
    json.dumps(data, ensure_ascii=False, indent=2) + '\n',
    encoding='utf-8',
)
PY
```

确认配置和目标文件都正确：

```bash
grep -n 'speaker_file' qwen3-tts-0.6b/config.json
test -f qwen3-tts-0.6b/anke.spk.bin
test "$(stat -c '%s' qwen3-tts-0.6b/anke.spk.bin)" -eq 4096
```

应看到：

```text
"speaker_file": "anke.spk.bin"
```

如果这里提示文件不存在，先回到上一步重新执行 `scp`。如果文件大小不是 `4096`，说明复制的不是有效 speaker embedding，不能继续启动测试。

#### 第四步：重启 TTS 服务

```bash
cd ~/qwen3-tts

./stop_server.sh
./start_server.sh
```

`start_server.sh` 会等待健康检查通过。也可以手动确认：

```bash
curl -fsS http://127.0.0.1:18080/health
echo
```

正常结果：

```json
{"status":"ok"}
```

检查当前服务对应的进程和最近启动日志：

```bash
pid="$(cat llama-server.pid)"
ps -fp "$pid"
tail -n 80 llama-server.log
```

不要使用 `killall` 之类的宽泛命令；`stop_server.sh` 只根据本项目的 `llama-server.pid` 停止对应服务。

#### 第五步：进入交互模式测试

```bash
cd ~/qwen3-tts
./run_interactive.sh
```

看到输入提示后输入文本并回车，例如：

```text
安可音色测试，妈妈说过，只要相信，故事就会给人力量。
```

输入下面任意一个词退出：

```text
退出
quit
exit
```

也可以不进入持续交互，直接做一次单句测试：

```bash
./run_interactive.sh '这是安可音色的单句测试。'
```

较长文本可以降低每段长度，减少单段生成失败或截断的概率：

```bash
QWEN3_TTS_CHUNK_CHARS=24 ./run_interactive.sh \
  '妈妈说过，只要相信，故事就会给人力量。所以每次想到这些故事的时候，安卡都觉得心里暖融融的。'
```

`run_interactive.sh` 在发现服务未运行时会自动调用 `start_server.sh`，但**音色配置变更后仍建议明确执行一次 `./stop_server.sh && ./start_server.sh`**，确保旧进程已经退出并重新加载新的 embedding。

### 5.3 验证播放

当前交互客户端默认只播放，不自动创建或覆盖 WAV 文件：

```bash
cd ~/qwen3-tts
timeout 40s ./run_interactive.sh '这是安可音色的低延迟播放测试。'
```

预期日志包含：

```text
开始低延迟播放：首段已就绪
播放完成。
首播延迟：... 秒
```

确认播放器退出且没有临时 WAV：

```bash
pgrep -af '[a]play' || true
find ~/qwen3-tts/wav-output -maxdepth 1 -type f -name '.output.wav.part' -print
```

如果要保留 HTTP 返回的 WAV，可继续直接使用 `curl -o` 保存；交互客户端本身不会保存。

### 5.4 切换回默认音色

如果之前保存了带时间戳的备份，可以先列出备份：

```bash
cd ~/qwen3-tts
ls -lt qwen3-tts-0.6b/config.json.before-voice-*
```

选择需要恢复的备份文件，例如：

```bash
cp -p qwen3-tts-0.6b/config.json.before-voice-20260825-120000 \
  qwen3-tts-0.6b/config.json
```

如果没有备份，也可以直接设置回默认文件名：

```bash
python3 - <<'PY'
import json
from pathlib import Path

p = Path('qwen3-tts-0.6b/config.json')
data = json.loads(p.read_text(encoding='utf-8'))
data['tts_model']['speaker_file'] = 'default.spk.bin'
p.write_text(
    json.dumps(data, ensure_ascii=False, indent=2) + '\n',
    encoding='utf-8',
)
PY
```

恢复后同样必须重启服务：

```bash
./stop_server.sh
./start_server.sh
curl -fsS http://127.0.0.1:18080/health
echo
```

### 5.5 通过 HTTP 接口测试

`voice: "default"` 目前只是兼容 OpenAI 风格接口的字段，并不是请求级音色选择器。服务会使用启动时从 `speaker_file` 加载的音色：

```bash
curl -f http://127.0.0.1:18080/v1/audio/speech \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "qwen3-tts",
    "input": "这是通过 HTTP 接口进行的音色测试。",
    "voice": "default",
    "response_format": "wav",
    "speed": 1.0
  }' \
  -o wav-output/output.wav
```

如果要验证不同音色，流程仍然是：修改 `config.json` → 重启服务 → 再调用接口。不能只修改请求中的 `voice` 值。

### 5.6 音色替换没有生效时的检查顺序

按以下顺序检查，不要只根据文件扩展名判断：

```bash
cd ~/qwen3-tts

# 1. 看配置实际指向哪个文件
grep -n 'speaker_file' qwen3-tts-0.6b/config.json

# 2. 看文件是否存在且大小正确
voice="$(python3 - <<'PY'
import json
print(json.load(open('qwen3-tts-0.6b/config.json'))['tts_model']['speaker_file'])
PY
)"
stat -c '%n: %s bytes' "qwen3-tts-0.6b/$voice"

# 3. 确认服务健康并取得当前 PID
curl -fsS http://127.0.0.1:18080/health
echo
cat llama-server.pid

# 4. 查看重启后的日志
tail -n 100 llama-server.log
```

常见原因：

1. 只复制了新的 `.spk.bin`，没有修改 `config.json`；
2. 修改了 `config.json`，但没有重启已经运行的 `llama-server`；
3. 文件放在项目根目录，而不是 `qwen3-tts-0.6b/` 模型目录；
4. `speaker_file` 写成了绝对路径或错误的相对路径；
5. 文件不是 raw `float32[1024]`，大小不是 `4096` 字节；
6. 实际启动的不是当前项目目录中的服务，或者使用了另一份 `QWEN3_TTS_MODEL` 配置。

确认服务已经停止并重新启动后，再进行交互测试：

```bash
./stop_server.sh
./start_server.sh
./run_interactive.sh '音色重新加载测试。'
```

当前 K3 HTTP 服务尚未实现上传 `ref_audio`/`ref_text` 后在线提取音色；生成 `.spk.bin` 后，必须按照本节流程部署并重启服务。

## 六、从 WAV/MP3/FLAC 克隆音色

详细说明见 [`voice_embeddings/README.md`](voice_embeddings/README.md)。快速示例：

```bash
python voice_embeddings/extract_speaker_embedding.py \
  --input '/path/to/reference.wav' \
  --output voice_presets/embeddings/my_voice.spk.bin \
  --model /path/to/Qwen3-TTS-12Hz-0.6B-Base \
  --ref-text '参考音频的逐字稿' \
  --device cpu \
  --dtype float32 \
  --overwrite
```

这个步骤要在支持 PyTorch/Qwen3-TTS 的开发机或服务器上运行。推荐把 `Qwen/Qwen3-TTS-12Hz-0.6B-Base` 下载到本地目录后使用；脚本只加载 speaker encoder，通常不需要完整 talker/codec 推理。当前 K3 HTTP 服务本身还没有实现上传 `ref_audio`/`ref_text` 后在线提取；生成 `.spk.bin` 后，再按上一节部署。

仓库只提交已提取的 `.spk.bin` 和元数据，不提交任何原始参考录音。

## 七、已下载音色预设

见 [`voice_presets/README.md`](voice_presets/README.md)。当前目录包含：

- 用户提供的 `anke.spk.bin`；
- Qwen3-TTS 官方示例 `clone.wav`、`clone_1.wav` 生成的两个独立 0.6B Base speaker embedding；`clone_2.wav` 当前下载内容与 `clone.wav` 相同，作为可追溯别名保留；
- 每个文件对应的输入来源和 SHA-256 元数据。

官方 CustomVoice 模型另有 `Vivian`、`Serena`、`Uncle_Fu`、`Dylan`、`Eric`、`Ryan`、`Aiden`、`Ono_Anna`、`Sohee` 等预置 speaker，但它们存储在 CustomVoice checkpoint 的 embedding 表中，不是官方单独发布的 `.spk.bin`。当前 K3 服务集成的是 Base 路线的固定 speaker 文件，因此本仓库先采用可验证的 Base 参考音频提取结果。

## 八、故障排查

### 服务启动失败

```bash
cat llama-server.log
test -f /usr/lib/libspacemit_ep.so
ldd runtime/bin/llama-server | grep 'not found' || true
```

### `.spk.bin` 尺寸错误

```bash
stat -c '%s' path/to/voice.spk.bin
```

必须是 `4096`。若是 WAV 文件，即使扩展名为 `.bin` 也不能使用。

### 音色替换后没有生效

确认：

```bash
grep speaker_file qwen3-tts-0.6b/config.json
./stop_server.sh
./start_server.sh
tail -50 llama-server.log
```

speaker 文件在服务启动时加载，修改后必须重启。

### 合成内容异常或截断

先减少单次文本长度：

```bash
QWEN3_TTS_CHUNK_CHARS=24 ./run_interactive.sh '较长文本'
```

并检查中文标点、文本是否包含未闭合的特殊字符。

## 许可证和声音授权

本仓库脚本和文档按仓库现有许可证管理；Qwen3-TTS、SpaceMIT runtime、ONNX Runtime、模型权重和参考音频可能有各自的许可证。分发前请分别保留对应许可证文件。

声音克隆只应使用本人声音或已获得明确授权的录音。模型许可证不自动授予某个真人、角色或配音演员声音的使用权，也不允许将合成声音用于冒充、欺骗或侵犯他人权益的场景。
