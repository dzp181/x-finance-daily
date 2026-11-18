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

## 用法

```bash
pip install pillow imageio-ffmpeg edge-tts

python3 scripts/fetch.py                          # 拉 48 小时推文
python3 scripts/curate.py --top 20                # 看候选
# 手动新建 episodes/<date>-<slug>/episode.json
python3 scripts/build_episode.py 2026-11-18-gold-vs-rates --theme ink
python3 scripts/render_docs.py 2026-11-18-gold-vs-rates
python3 scripts/publish.py 2026-11-18-gold-vs-rates --fill
```

### 主题与字幕

`--theme ink`（深墨，默认）或 `--theme paper`（米白）。两版会输出成
`video.mp4` / `video-paper.mp4`，封面同理，方便直接对比。

字幕默认烧进画面，时间轴来自 edge-tts 的 boundary 事件：

- 中文音色返回的是 `SentenceBoundary`（整句），英文才是 `WordBoundary`（逐词）
- 两种都收，然后均摊到字符级时间轴，再按标点和 12 字上限切成字幕行
- 字幕位置固定 `MarginV=400`，避开视频号底部的标题和按钮

`--no-subs` 可以关掉字幕。字体走 `PingFang SC`，由 libass 解析，不需要额外装字体。

### 背景为什么带颗粒

纯渐变在 8-bit H.264 下会出横向色带（每 100px 跳一档）。渲染时叠一层零均值高斯噪声抖动，
`ink` 用 σ=6，`paper` 用 σ=2 —— 浅色底噪点太强会显脏。

### 换成自己的声音

`build_episode.py` 默认用 macOS 的 `Tingting` 音色。想用自己的声音：

1. 按 `script.md` 的时间表逐句录，一句一个文件，`s0.m4a` … `s9.m4a`
2. 丢进 `episodes/<slug>/build/`
3. `python3 scripts/build_episode.py <slug> --skip-tts`

（`--skip-tts` 复用的是 `build/s*.wav`；先用 ffmpeg 把 m4a 转成 44.1k 单声道 wav。）

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
  "voice": "Tingting",
  "rate": 178,
  "scenes": [{ "caption": "画面大字", "text": "这一句的口播词" }]
}
```

`caption` 里用 `\n` 显式换行。排版会优先保住每一行不折断，宁可缩字号也不硬拆。

---

## 数字人主播（实验）

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
