# x-finance-daily

把 X 上关注的金融账号内容，变成视频号能直接发的竖屏口播视频。

数据不来自爬虫、不登录 X —— 直接读 [x2ding](https://github.com/dzp181/x2ding) 已经抓好的 ntfy 主题。

```
x2ding（GitHub Actions 每 10 分钟抓 X）
    └─ ntfy 主题 x-<handle>
         └─ fetch.py      拉 48 小时推文 → data/
              └─ curate.py    打分排序 → 候选清单
                   └─ episode.json   人工/模型定选题、写口播词
                        └─ build_episode.py  TTS + 出图 + 合成 → video.mp4 / cover.png
                             └─ render_docs.py   → script.md / caption.md
                                  └─ publish.py   bsk 填进视频号发布页（停在「发表」前）
```

## 为什么这么设计

- **不碰账号**：抓取完全复用已有 ntfy 订阅，不拿你的 X cookie，也不走任何私有接口。
- **不碰发布**：`publish.py` 只填不点。金融内容一旦发错，删不掉传播，最后一下必须是人点。
- **内容只讲机制，不讲方向**：不预测涨跌、不给点位、不推荐标的，片尾固定免责声明。视频号对荐股类内容限流和处罚都很重。

## 目录

```
config.json              抓取窗口、评分词表、画面尺寸、频道名、免责声明
scripts/run.py           编排入口：new / build / status / all
scripts/fetch.py         读 x2ding 的 watchlist，从 ntfy 拉推文，落 data/
scripts/curate.py        打分排序，输出候选
scripts/build_episode.py episode.json → video.mp4 + cover.png（本地 TTS，无网络）
scripts/render_docs.py   episode.json → script.md + caption.md
scripts/publish.py       半自动填视频号发布页
episodes/<date>-<slug>/  一期一个目录
```

一期的产物：

| 文件 | 说明 |
| --- | --- |
| `episode.json` | 唯一事实源：标题、逐句口播词、画面大字、描述、话题 |
| `script.md` | 分镜脚本，带时间码，可照着自己录 |
| `caption.md` | 发布文案（标题 / 描述 / 话题） |
| `sources.md` | 每个数字对应的原始推文 |
| `video.mp4` | 1080×1920，H.264 + AAC |
| `cover.png` | 1080×1440（3:4） |

## 用法（下次只跑这两条）

```bash
cd x-finance-daily

python3 scripts/run.py                      # ① 抓取 + 打分 + 建今天这一期的草稿
# ② 补 episodes/<date>-<slug>/episode.json 里的 title / scenes[]（这步是唯一要动脑的）
python3 scripts/run.py build --crf 16       # ③ 出片 + 出文案，产物路径直接打印出来
```

`run.py` 只做编排，日志、检查、产物清单都在里面：

| 命令 | 作用 |
| --- | --- |
| `run.py` / `run.py new` | 抓 48h 推文 → 打分 → 打印前 8 名候选 → 建草稿（`episode.json` + `sources.md` 骨架） |
| `run.py new --pick 3 --slug gold` | 选第 3 名候选，目录名后缀改成 `gold` |
| `run.py new --offline` | 不联网，直接用 `data/raw` 里最新的缓存 |
| `run.py build` | 最新一期 → `video.mp4` / `cover.png` / `script.md` / `caption.md`（默认 `--crf 16`） |
| `run.py build <slug>` | 指定一期 |
| `run.py status` | 每期进度：草稿 / 待出片 / 已出片，附时长体积 |
| `run.py all` | new + build，草稿没补齐会自动停下 |

草稿不完整时 `build` 会拒绝出片并告诉你缺什么（`scenes` 为空、还有 `TODO`、某句缺 `caption`），
不会产出半成品视频。

### 也可以直接在对话里说

项目里带了一个技能 `x-finance-daily`（`.workbuddy/skills/x-finance-daily/SKILL.md`），
下次在 WorkBuddy 里说 **「生成今天这期」** 就会自动走完：选题 → 写文案 → 出片 → 给发布清单。
文案规范和红线（只讲机制、不给点位、数字必须有出处）都写在那份技能里，不用每次重复交代。

### 分步跑（调试用）

```bash
pip install pillow imageio-ffmpeg edge-tts

python3 scripts/fetch.py                          # 拉 48 小时推文
python3 scripts/curate.py --top 20                # 看候选
python3 scripts/build_episode.py <slug> --crf 16  # 只出片
python3 scripts/render_docs.py <slug>             # 只出文档
# 发布：见 episodes/<slug>/publish.md
```

`--crf 16` 是给上传用的画质档。默认 20 只够本地看——视频号会对上传文件再压一次，
暗背景渐变在二次压缩下最容易出色带，所以源文件要给足码率（当前 1.0 Mbps，11 MB / 76 秒）。

`scripts/publish.py` 是早期写的浏览器半自动填写脚本，当前流程改手动发布，留作备用。

### 主题与字幕

`--theme ink`（深墨，默认）落地为 `video.mp4` / `cover.png`，其他主题输出带后缀，方便对比。

`ink` 主题的底图是 `assets/bg/ink-frost.png`——一张极暗的磨砂墙面加右上侧光，
不是纯渐变。想换背景：

```bash
# 1) 生成一张 9:16 的底图（可灵文生图 / 本地出图都行），放进 assets/bg/src/
# 2) 处理：去水印 → 裁 9:16 → 底部压暗，输出成品
.venv-lipsync/bin/python scripts/prep_bg.py assets/bg/src/xxx.png assets/bg/ink-frost.png

# 3) 临时换一版不落地（对比用）
python3 scripts/build_episode.py <slug> --bg assets/bg/ink-beam.png
```

`prep_bg.py` 三件事的动机：生成图右下角自带「AI生成」水印，暗底上直接用会露出来，
所以做 inpaint；原图是 2:3、成片要 9:16，按「顶部对齐」裁——光源在右上，裁底部才不伤光；
底部再叠 30% 垂直压暗，给烧字幕留对比度。

字幕默认烧进画面，时间轴来自 edge-tts 的 boundary 事件：

- 中文音色返回的是 `SentenceBoundary`（整句），英文才是 `WordBoundary`（逐词）
- 两种都收，然后均摊到字符级时间轴，再按标点和 12 字上限切成字幕行
- 字幕位置固定 `MarginV=400`，避开视频号底部的标题和按钮

`--no-subs` 可以关掉字幕。字体走 `PingFang SC`，由 libass 解析，不需要额外装字体。

### 背景为什么带颗粒

纯渐变在 8-bit H.264 下会出横向色带（每 100px 跳一档）。没有背景图时（`paper` 主题）
渲染会叠一层零均值高斯噪声抖动，`ink` 用 σ=6，`paper` 用 σ=2 —— 浅色底噪点太强会显脏。

用背景图时不额外加噪，图本身自带颗粒，再做 resize 反而会糊。

### 换成自己的声音

音色写在 `episode.json` 的 `tts` 字段，当前是 `edge` 引擎的 `zh-CN-XiaoxiaoNeural`（+10%）：

```json
"tts": { "engine": "edge", "voice": "zh-CN-XiaoxiaoNeural", "rate": "+10%" }
```

`engine` 填 `say` 就退回 macOS 离线音色（`voice` 填 `Tingting`，`rate` 填数字如 178）。
合成结果按（文本 + 音色 + 语速）缓存在 `episodes/<slug>/.cache/`，改文字才重新请求。

想用真人录音：

1. 按 `script.md` 的时间表逐句录，一句一个文件，`s0.m4a` … `s9.m4a`
2. 用 ffmpeg 转成 44.1k 单声道 wav，覆盖 `build/s0.wav` … `build/s9.wav`
3. 先跑一次 `--keep` 生成 `build/` 目录，再替换 wav

## episode.json 字段

```jsonc
{
  "slug": "gold-vs-rates",
  "date": "2026-11-18",
  "title": "内部用标题",
  "cover_caption": "封面大字，用 \\n 控制换行",
  "subtitle": "封面副标题",
  "short_title": "发布用标题",
  "tags": ["黄金", "利率"],
  "description": "发布页描述，含免责声明",
  "topic": "金融科普",
  "tts": { "engine": "edge", "voice": "zh-CN-XiaoxiaoNeural", "rate": "+10%" },
  "scenes": [{ "caption": "画面大字", "text": "这一句的口播词" }]
}
```

`caption` 里用 `\n` 显式换行。排版会优先保住每一行不折断，宁可缩字号也不硬拆。

---

## 数字人主播（实验，当前未启用）

> 结论先说：**这条路验证过，效果不达发布标准，已回到「静态大字 + 烧字幕」的图片版。**
> 原因是 Wav2Lip 的嘴部输出固定 96×96，放大到人脸尺寸约 6.6 倍，嘴周必然有一圈糊；
> 静态帧看着还行，连续播放就是恐怖谷。下面代码保留，等换 MuseTalk 或走可灵时复用接口。

让一张静态人像按口播音频说话，用于替代纯文字卡片。

```bash
bash scripts/lipsync/setup.sh                 # 建 .venv-lipsync + 拉模型，约 700MB
.venv-lipsync/bin/python scripts/lipsync/render.py \
  --image assets/avatar/host.png \
  --audio vo16k.wav \
  --out episodes/<slug>/avatar/talk.mp4 \
  --fps 25
```

M1 上约 30fps 渲染（1080×1920），80 秒成片约 1 分钟。

### ⚠️ 许可限制，务必先读

Wav2Lip 开源版**明确禁止商用**，原文：

> All results from this open-source code ... should only be used for research/academic/personal
> purposes only ... **any form of commercial use is strictly prohibited**.

带导流、带货、接广的视频号内容属于商用。所以这套脚本**只用于效果验证**，
不要直接拿来跑要发布的成片。可商用的替代：

| 方案 | 许可 | 质量 | 成本 |
| --- | --- | --- | --- |
| 可灵 Omni-Ref 音频驱动 | 商业授权 | 最好，口型与表情自然 | 按秒计费 |
| MuseTalk（腾讯） | MIT | 好于 Wav2Lip，需 GPU | 免费 |
| LivePortrait | MIT | 表情好，口型一般 | 免费 |

### 实现要点

- 人脸检测换成 OpenCV YuNet，用 5 点关键点重新算人脸框。直接用检测框会把
  头发、额头、脖子一起卷进 96×96，全部重建成糊的一团。
- 只替换嘴部椭圆区域，上半脸（眼睛、额头、皮肤纹理）保留原图。整块替换会
  出现「半张脸清晰、半张脸糊」的接缝。
- LAB 空间做颜色匹配，且只用被替换区域内的像素统计——用整张脸的均值去校准
  嘴周一小块，会把它拉向全脸平均色，反而出现色差。
- mel 特征自己实现（`mel.py`），不走 librosa，规避 numba 在 Python 3.10 上的
  安装问题。参数必须与训练一致：16k / n_fft 800 / hop 200 / 80 mel / Slaney 归一化。
