# Qwen3-TTS on SpaceMIT K3

这是一个面向 **SpacemiT K3 / riscv64** 的 Qwen3-TTS 0.6B 部署目录。项目包含：

- K3 上运行的优化版 `llama-server` runtime；
- Qwen3-TTS 模型配置和启动脚本；
- OpenAI 风格的 HTTP 语音合成接口；
- 交互式/单次生成 WAV 的客户端；
- 多个 speaker embedding 预设；
- 将 WAV/MP3/FLAC 参考录音转换成 K3 所需 `.spk.bin` 的离线脚本；本地 Base checkpoint 可只读取 speaker encoder 权重。

> 当前仓库不提交大模型的 ONNX/GGUF 权重，也不提交运行时生成的 WAV、日志和 PID 文件。大文件需要在 K3 板端单独准备。

## 当前验证环境

| 项目 | 值 |
| --- | --- |
| 目标板 | SpaceMIT K3，riscv64 |
| 系统 | Bianbu Linux（板端实际版本以 `cat /etc/os-release` 为准） |
| TTS 模型 | Qwen3-TTS 0.6B |
| 输出 | 24 kHz、16-bit、mono、PCM WAV |
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
│   ├── default.spk.bin       # 板端准备；默认被 .gitignore 忽略
│   ├── *.gguf                # 板端准备；默认被 .gitignore 忽略
│   └── *.onnx                # 板端准备；默认被 .gitignore 忽略
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

### 1. 在 K3 板端准备系统依赖

```bash
ssh spacemit-k3
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

GitHub 仓库**不会提交**以下大文件：

```text
*.gguf
*.onnx
*.spk.bin
```

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
ssh spacemit-k3 'mkdir -p /home/spacemit/projects/qwen3-tts/.model-download'
scp /tmp/Qwen3-TTS-0.6B/{Qwen3-TTS-0.6B-tokenizer.gguf,Qwen3-TTS-0.6B-text-embed-proj.fp32.onnx,Qwen3-TTS-0.6B-codec-decoder-t50.dynq.onnx,Qwen3-TTS-0.6B-talker-q8_0.gguf,Qwen3-TTS-0.6B-code-predictor-q4_0.gguf,Qwen3-TTS-0.6B-aux.gguf,default.spk.bin} \
  spacemit-k3:/home/spacemit/projects/qwen3-tts/.model-download/
```

在板端将官方文件名映射为本项目配置使用的文件名：

```bash
ssh spacemit-k3
cd /home/spacemit/projects/qwen3-tts
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
ssh spacemit-k3
python3 -m pip install --user -U huggingface_hub
```

然后在项目目录外的临时目录下载，避免把官方长文件名直接混入项目模型目录：

```bash
cd /home/spacemit/projects/qwen3-tts
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
cd /home/spacemit/projects/qwen3-tts

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
ssh spacemit-k3 'mkdir -p /home/spacemit/projects'
rsync -a --delete \
  --exclude='qwen3-tts-0.6b/*.gguf' \
  --exclude='qwen3-tts-0.6b/*.onnx' \
  --exclude='qwen3-tts-0.6b/*.bin' \
  ./ spacemit-k3:/home/spacemit/projects/qwen3-tts/
```

然后在板端补齐模型大文件：

```bash
ssh spacemit-k3
cd /home/spacemit/projects/qwen3-tts
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
ssh spacemit-k3
cd /home/spacemit/src
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
# set(CMAKE_SYSROOT /opt/k3-sysroot)
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
./run_interactive.sh '你好，这是一次单句合成。'
QWEN3_TTS_CHUNK_CHARS=24 ./run_interactive.sh '这里是一段较长的文本。'
```

输出固定为：

```text
wav-output/output.wav
```

服务端目前是“每段文本完整生成后返回”，不是逐 PCM 帧真流式；客户端会把分段 WAV 的 PCM 合并成一个有效 WAV 容器。

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
/home/spacemit/projects/qwen3-tts
```

那么配置：

```json
"speaker_file": "anke.spk.bin"
```

实际读取的文件就是：

```text
/home/spacemit/projects/qwen3-tts/qwen3-tts-0.6b/anke.spk.bin
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
cd /home/heweijie/spacemit-k3-dev/projects/qwen3-tts

scp voice_presets/embeddings/anke.spk.bin \
  spacemit-k3:/home/spacemit/projects/qwen3-tts/qwen3-tts-0.6b/anke.spk.bin
```

在板端确认文件已经到位：

```bash
ssh spacemit-k3
cd /home/spacemit/projects/qwen3-tts
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
  spacemit-k3:/home/spacemit/projects/qwen3-tts/qwen3-tts-0.6b/qwen_clone.spk.bin
```

#### 第二步：在板端备份当前配置

```bash
cd /home/spacemit/projects/qwen3-tts

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
cd /home/spacemit/projects/qwen3-tts

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
cd /home/spacemit/projects/qwen3-tts
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

### 5.3 验证生成结果

当前交互客户端不会自动播放，也不会按时间戳生成多个 WAV。每次成功合成都会原子覆盖：

```text
/home/spacemit/projects/qwen3-tts/wav-output/output.wav
```

生成后检查格式、时长和文件大小：

```bash
python3 - <<'PY'
from pathlib import Path
import wave

p = Path('/home/spacemit/projects/qwen3-tts/wav-output/output.wav')
print('path =', p.resolve())
print('size =', p.stat().st_size, 'bytes')
with wave.open(str(p), 'rb') as w:
    print('channels =', w.getnchannels())
    print('sample_width =', w.getsampwidth())
    print('sample_rate =', w.getframerate())
    print('frames =', w.getnframes())
    print('seconds =', round(w.getnframes() / w.getframerate(), 3))
PY
```

预期格式为：

```text
channels = 1
sample_width = 2
sample_rate = 24000
```

如需在开发机试听，可以把文件复制回来：

```bash
# 在开发机执行
scp spacemit-k3:/home/spacemit/projects/qwen3-tts/wav-output/output.wav \
  ./anke-test-output.wav
```

### 5.4 切换回默认音色

如果之前保存了带时间戳的备份，可以先列出备份：

```bash
cd /home/spacemit/projects/qwen3-tts
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
cd /home/spacemit/projects/qwen3-tts

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
  --model /data/models/Qwen3-TTS-12Hz-0.6B-Base \
  --ref-text '参考音频的逐字稿' \
  --device cpu \
  --dtype float32 \
  --overwrite
```

这个步骤要在支持 PyTorch/Qwen3-TTS 的开发机或服务器上运行。推荐把 `Qwen/Qwen3-TTS-12Hz-0.6B-Base` 下载到本地目录后使用；脚本只加载 speaker encoder，通常不需要完整 talker/codec 推理。当前 K3 HTTP 服务本身还没有实现上传 `ref_audio`/`ref_text` 后在线提取；生成 `.spk.bin` 后，再按上一节部署。

本次用户提供的音频已作为 `anke` 音色的提取输入。原始录音不提交到仓库，只提交提取结果和哈希/模型元数据。

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
