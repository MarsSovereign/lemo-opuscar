# mars/：Mars PC 专用层（Windows 原生 + 本地工具 + OpenMontage）

这个文件夹只放我们自己加的东西，不改上游的 `core/`、`styles/`。同步上游更新时一般不会冲突。

全部脚本只用 Python 标准库（`tts_local.py`、`gen_images.py`、`media.py` 生成音频或调用 sheet 时会用库的 `.venv`）。Windows、macOS、Linux 都能用。

## 给 agent：在这台 PC 上做片，把上游指南里的命令换成这些

| 上游指南写的 | 在 Mars PC 上用 |
|---|---|
| `sh plugin/skills/lemo-opuscar/scripts/setup.sh deps [voice] [music]` | `python mars/setup.py deps [voice] [music]` |
| `sh tools/fetch.sh …` | `python mars/fetch.py …`（参数一样） |
| `.venv/bin/python …` | `.venv\Scripts\python.exe …`（或直接用 `mars/` 下的脚本，它们会自己找） |
| 每支片子的 `build.sh` | `python mars/build.py <片子文件夹>`（不用写 build.sh） |
| `sh core/render/mux.sh …` | `python mars/mux.py …`（参数一样） |
| `core/tts/tts.py` / `tts_zh.py` 配音 | `python mars/tts_local.py lines.json voices/`（本地 IndexTTS2.5 / CosyVoice3，输出格式一样） |
| `core/tts/asr_check.py` 核对 | `python mars/asr.py lines.json voices/`（用 PC 上的 Whisper 模型） |
| 配乐只能写采样/合成 | 也可以用 MiniMax Music3：`python mars/comfy.py run mars/profiles/minimax_music3.json …`，再用 `mars/media.py wav` 转换 |
| — | AI 出图当画面素材：`python mars/gen_images.py` |
| — | H3 / LongCat 视频片段放进画面：`python mars/media.py frames` + 页面里 `/mars/web/clip.js` |

`node core/render/still.mjs`、`video.mjs`、`events.mjs`、`readcheck.mjs` 在 Windows 上原样能用，命令不变。

其余照上游指南做：风格和故事的规矩（`AGENTS.md`）、导演（`DIRECTOR.md`）、技术（`TECHNIQUE.md`）都不变。

## 第一次装（PC 上做一次）

1. 系统里要有：Node 20+、ffmpeg 7+（Windows 用 gyan.dev 或 BtbN 的 full 版，放进 PATH）、Python 3.11–3.13 或 uv。
2. 设一次 UTF-8，否则 Windows 用 GBK 读中文 JSON 会出错（`mars/` 的脚本会自己设，但直接跑 `core/` 的脚本需要）：
   ```bat
   setx PYTHONUTF8 1
   ```
3. 在库目录里：
   ```bat
   python mars\setup.py deps voice music
   ```
   镜像：先设 `PIP_INDEX_URL`（华为云）、`npm_config_registry`。
4. 复制 `mars\config.example.json` 为 `mars\config.json`，按这台机器改（这个文件不进 git）：

   | 键 | 默认 | 说明 |
   |---|---|---|
   | `tts_url` | `http://127.0.0.1:9880` | OpenMontage 的本地配音服务 |
   | `tts_voice` | 空 | 默认音色名（服务 `LOCAL_TTS_VOICES_DIR` 里的文件名） |
   | `comfy_url` | `http://127.0.0.1:8189` | 我们的 ComfyUI（8188 是 Mars 自己的） |
   | `whisper_model` | 空 | 本地 faster-whisper 模型文件夹（`H:\win-ai\whisper` 下 large-v3-turbo 的实际路径） |
   | `whisper_python` | 空 | 已装 faster-whisper 的解释器；空 = 库的 `.venv` |
   | `openmontage_dir` | 空 | `H:\win-ai\OpenMontage` |

   环境变量优先：`LEMO_TTS_URL`、`LEMO_TTS_VOICE`、`LEMO_COMFY_URL`、`WHISPER_MODEL`、`OPENMONTAGE_DIR`、`LEMO_FFMPEG`、`LEMO_FFPROBE`。
5. 自检：
   ```bat
   python mars\setup.py
   .venv\Scripts\python.exe -m unittest discover -s mars\tests
   ```

## 各工具

### `build.py`：一条命令出片
```bat
python mars\build.py films\<名字> [--size 1080x1920] [--fps 24] [--grain 0] [--workers 3] [--from video]
```
步骤：events → 逐帧渲染 → 片子自己的 `mix.py`（环境里有 `LIB`、`FILM`）→ `mux.py`（−14 LUFS）。
设置也可以写在片子文件夹的 `film.json` 里：`{"name","fps","workers","size","grain","q","mix"}`。哪步失败就用 `--from <那一步>` 接着跑。

### `mux.py`：`mux.sh` 的移植
参数、检查（坏音频、截断的 WAV、无声）、响度和颜色选项都和 `mux.sh` 一致。上游改了 `mux.sh` 要同步改这里。

### `tts_local.py`：本地配音
- `lines.json` 每行 `{"id","text","say"?,"voice"?,"speed"?,"take"?}`。
  - `say` 是实际读的字（数字写成汉字、多音字用 `<行|HANG2>`）。
  - `take` 改个值就会重读这一句（服务没有种子参数）。
- 输出 `<id>.wav`（24 kHz 单声道，去掉首尾静音）和 `dur.json`，和上游配音工具完全一样。
- 读过的句子缓存在 `voices/.cache/`。`--redo id1,id2` 只重读这几句。
- 服务没起或模型没加载完：退出码 2，并提示怎么起服务。
- 同一时间只起一个模型；跑 H3、LongCat 前先停掉配音服务，避免抢显存。

### `asr.py`：读音核对
等于 `core/tts/asr_check.py` 加上本机的 Whisper 模型，默认 `--lang zh`。退出码 1 时，用 `tts_local.py --redo` 或换一个 `take` 重读。

### `comfy.py`：调 ComfyUI（出图、配乐、H3）
```bat
python mars\comfy.py check
python mars\comfy.py slots <导出的API版.json>
python mars\comfy.py run mars\profiles\qwen21_t2i.json --out films\x\plates --name s01 --set prompt="……" --set seed=42
```
- 工作流必须是"导出 (API)"格式。
- **profile** 文件把节点编号翻译成名字（prompt、seed、width…），片子里不写死节点编号。
- 参考图、参考音频会自动上传到 ComfyUI 的 input 文件夹。
- 每次输出旁边都写一个 `<name>.json`，记下用过的参数和种子，可以复现。

**profile 怎么做**（每个工作流做一次）：
1. 在 ComfyUI 里"导出 (API)"。
2. 运行 `python mars\comfy.py slots 那个文件`。
3. 照它给的建议，把 `mars/profiles/*.example.json` 里的 `<id>` 换成真实节点编号。
4. 存成同名、不带 `.example` 的文件。

这些文件可以提交，云端就能看到真实的节点编号。

已有模板：`qwen21_t2i`、`qwen21_edit`、`minimax_music3`、`h3_i2v`。

### `gen_images.py`：一次出整片的画面素材
```bat
python mars\gen_images.py mars\profiles\qwen21_t2i.json shots.json films\x\plates
```
- `shots.json` = `[{"id","prompt","seed"?,"width"?,"height"?,…}]`。
- 中断了用同一条命令接着跑，已出过的会跳过。
- `--redo` 用同一个种子重出，`--reseed` 换种子重出。
- 出完会生成审阅用的 `_sheet.jpg`。
- 图里不要让模型写字。字幕、标签、数字都由页面画。

### `media.py` + `web/clip.js`：视频片段按帧嵌入
```bat
python mars\media.py frames h3_s04.mp4 films\x\clips\s04 --size 1920x1080
python mars\media.py wav music3.mp3 films\x\music\score.wav
```
- 视频片段先拆成 24 fps 的 JPG 序列。有声音的话，另外导出 `audio.wav` 给 `mix.py` 用。
- 页面里这样用：
  ```js
  import { loadClip } from '/mars/web/clip.js';
  const s04 = await loadClip('clips/s04/');
  // 在 render(t) 里：
  s04.draw(g, t - 12.0, 0, 0, 1920, 1080);
  ```
  这样任何一帧都能单独、准确地画出来。
- 不要在页面里直接用 `<video>`：它是异步跳帧的，会出现错帧。

## 和 OpenMontage 的关系

OpenMontage（`MarsSovereign/OpenMontage`，分支 `claude/fervent-ptolemy-9kwks7`）新增了第四种渲染方式 `render_runtime: "lemo-opuscar"`：
- 工具 `lemo_opuscar_compose`：
  - `doctor` 检查环境；
  - `styles` 列出风格；
  - `stills` 出审阅帧和拼图；
  - `render` 调用这里的 `mars/build.py` 出片。
- 需要在 OpenMontage 的 `.env` 里设 `LEMO_OPUSCAR_HOME=<这个库的路径>`。
- 在 `edit_decisions` 里写 `lemo_opuscar: {film_dir, style, fps?, size?, grain?}`，`video_compose` 就会把渲染交给它，渲染完照常做最终自检。
- 片子文件夹建议放在 `projects/<id>/lemo/<片名>/`。
- 配音可以走 OpenMontage 的 `local_zh_tts`，也可以直接用 `tts_local.py`：两者是同一个服务。
- agent 的用法写在 OpenMontage 的 `.agents/skills/lemo-opuscar/SKILL.md`。

## 还没做（第二阶段）

批量生产：把常用风格做成 `content.json` 驱动的模板，热点文案直接生成 `content.json` 和 `lines.json`，中间不需要 agent。等上面这些在 PC 上跑稳了再做。
