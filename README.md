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
| 默认音色 | `qwen3-tts-0.6b/default.spk.bin`，raw `float32[1024]` |

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

### 2. 获取模型文件

将模型文件放到：

```text
/home/spacemit/projects/qwen3-tts/qwen3-tts-0.6b/
```

目录至少需要：

```text
config.json
manifest.json
tokenizer.gguf
text-embed-proj.onnx
codec-decoder-t50-q.onnx
talker-q8_0.gguf
code-predictor-q4_0.gguf
qwen3-tts-aux.gguf
default.spk.bin
```

模型文件体积较大，不通过普通 Git 提交。准备后检查：

```bash
cd /home/spacemit/projects/qwen3-tts
for f in qwen3-tts-0.6b/{tokenizer.gguf,text-embed-proj.onnx,codec-decoder-t50-q.onnx,talker-q8_0.gguf,code-predictor-q4_0.gguf,qwen3-tts-aux.gguf,default.spk.bin}; do
    test -f "$f" || { echo "missing: $f"; exit 1; }
done
cat qwen3-tts-0.6b/SHA256SUMS
```

`SHA256SUMS` 中的大文件校验项应与实际下载来源对应；若模型被重新量化或替换，不要继续沿用旧校验值。

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

当前配置：

```json
{
  "tts_model": {
    "speaker_file": "default.spk.bin"
  }
}
```

`.spk.bin` 必须是：

```text
raw little-endian float32[1024]
4096 bytes
```

将新音色放到模型目录并修改配置：

```bash
cp voice_presets/embeddings/anke.spk.bin qwen3-tts-0.6b/anke.spk.bin
python3 - <<'PY'
import json
from pathlib import Path
p = Path('qwen3-tts-0.6b/config.json')
data = json.loads(p.read_text())
data['tts_model']['speaker_file'] = 'anke.spk.bin'
p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
PY
./stop_server.sh
./start_server.sh
```

当前请求中的 `voice: "default"` 是兼容字段，不会在多个 `.spk.bin` 文件之间自动选择。真正的请求级多音色需要后续扩展服务端映射。

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
