# x-finance-daily

把 X 上关注的金融账号内容，变成视频号能直接发的竖屏口播视频。

数据不来自爬虫、不登录 X —— 直接读 [x2ding](https://github.com/dzp181/x2ding) 已经抓好的 ntfy 主题。

```
x2ding（GitHub Actions 每 10 分钟抓 X）
    └─ ntfy 主题 x-<handle>
         └─ fetch.py      拉 48 小时推文 → data/
              └─ curate.py    一级：词表粗筛（阈值 + 账号限流）→ 70 条左右
                   └─ cluster.py   二级：聚成事件簇 → data/shortlist/<日期>-<档>.md
                        └─ episode.json   模型判断选题（写进 picked_reason）、写口播词
                             └─ build_episode.py  TTS + 出图 + 合成 → video.mp4 / cover.png
                                  └─ render_docs.py   → script.md / caption.md
```

选题分两级，不是因为流程好看，是因为单条打分做不到「比较事件」：

1. **一级｜词表粗筛**（`curate.py`）：加分词按 `sqrt` 衰减并封顶，长贴不能靠堆术语刷分；加时效衰减、账号权重、刷屏惩罚、数字密度；每账号最多 6 条进池。
2. **二级｜事件聚类 + 模型判断**（`cluster.py` + 对话里的模型）：近乎同文的转载合并成一个事件（只列一条代表、标出覆盖几个账号），再由模型按「能不能讲清一个机制 / 数字能不能核实 / 是否与近期撞题」挑一个。

### 受众契合（v3 / v3.1）

频道发小红书和视频号，看的人是 30+、有自己见解、不缺信息也不缺观点。他们不缺「发生了什么」，缺的是「这桩交易的结构是什么、这笔账怎么算、跟我有什么关系」。所以粗筛除了术语密度，还看形态：

| 形态 | 判据 | 权重 |
| --- | --- | --- |
| 拆账 | 招股书、定价、解禁、锁定期、配售、调仓、纳入 | +0.85/词，封顶 4.0 |
| 算账 | 数字 ≥3 且有「对应/折算/成本/倍/口径」这类关系词 | +2.6 |
| 一手 | **规则层**：招股书、13F、文件号、监管、证监会、BIS、法案、交易所、路演 | +0.6/词，封顶 2.0 |
| 个股 | **个股层**：电话会、业绩会、财报、问答、调研；且交易结构 <2、无规则层词 | −3.0（形态再 −2.5） |
| 背离 | 同一消息两个资产反应不同（反而/却 + ≥2 个资产词） | +1.5 |
| 映射 | A股、汇率、房贷、黄金、通胀、消费 | +0.7/词，封顶 2.2 |
| 盘面 | 有涨跌幅/点位/成交额，却没有结构、算账、一手信源 | −4.0 |
| 观点 | 抄底、梭哈、目标价、必涨、加仓减仓 | −3.0/词 |
| 清单 | 栏目词（热点/盘点/晚报）+ ≥3 个条目，且 >500 字 | −2.5 |

**一手分两层（v3.1）**：规则层（招股书、13F、监管文件、政策原文）讲的是可复用的规则，加分；个股层（某家公司的财报电话会）讲的是那一家公司，降权。受众明确不喜欢「分析个股」，而旧版把两者同等加分——一篇拆得很细的财报长文会在「一手 + 算账 + 机制词」上全拿高分，把机制题挤下去。个股层刻意不含「纪要」「指引」：FOMC 会议纪要、央行指引属于宏观规则层，会被误伤。豁免条件是「交易结构 ≥2 或有规则层词」，让「财报里讲招股/解禁/监管文件」这类仍有复用价值的内容留下来。

配套的两条约束：`boost` 系数从 2.4 降到 1.9、封顶从 9 降到 6.5，术语密度不再是主导项；>36 小时且没有结果词（收盘/公告/确认/首日）的推文判为「事件前推演」，扣 1.8。

清单里每条事件都带 `形态` 和 `最新`（簇内最新一条距今多久）。**代表推文可能写于事件发生之前，建稿前必须看最新那条**——11-20 那期就是这么救回来的：种子推文还是按 78 美元做的打新测算，事件其实已经 83 美元定价、首日走完了。

同一主体（Anthropic、英伟达、腾讯…）的事件在清单里会标「另见第 N 个」并扣 1.2 分，避免同一个故事换个账号写就占两个位置。


## 为什么这么设计

- **不碰账号**：抓取完全复用已有 ntfy 订阅，不拿你的 X cookie，也不走任何私有接口。
- **不碰发布**：`publish.py` 只填不点。金融内容一旦发错，删不掉传播，最后一下必须是人点。
- **内容只讲机制，不讲方向**：不预测涨跌、不给点位、不推荐标的，片尾固定免责声明。视频号对荐股类内容限流和处罚都很重。

## 目录

```
config.json              抓取窗口、评分词表、画面尺寸、频道名、免责声明
scripts/run.py           编排入口：shortlist / new / build / status / all
scripts/fetch.py         读 x2ding 的 watchlist，从 ntfy 拉推文，落 data/
scripts/curate.py        一级粗筛：词表打分（封顶 + 时效 + 账号权重 + 受众契合）
scripts/cluster.py       二级聚类：按事件合并 + 标形态/最新/同主体，输出 data/shortlist/
scripts/build_episode.py episode.json → video.mp4 + cover.png（本地 TTS，无网络）
scripts/render_docs.py   episode.json → script.md + caption.md
scripts/pack_ima.py      一期 → outbox/（ima 能收的 md + mp3 + manifest）
scripts/ima_put.py       执行 create_media 给的 presigned PUT
scripts/push_draft.sh    入口：把一期存进视频号草稿箱（清 PYTHONPATH + 指定浏览器目录）
scripts/push_draft.py    组描述/标题/封面，调 vendor 里的 tencent uploader（is_draft=True）
scripts/setup_sau.sh     一次性装 social-auto-upload 运行环境
scripts/publish.py       bsk 半自动填发布页（iframe 里点不中，已被 push_draft 取代）
vendor/social-auto-upload/  第三方视频号 uploader（gitignore，可重建）
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
| `run.py shortlist` | 抓 48h → 粗筛 → 事件聚类 → 写 `data/shortlist/<日期>-<档>.md`（给模型读的选题清单） |
| `run.py new --cluster 3` | 用第 3 个事件建草稿（**推荐路径**），`--reason` 可写上选题理由 |
| `run.py new --pick 3` | 旧路径：直接按单条推文排序选第 3 名 |
| `run.py new --offline` | 不联网，直接用 `data/raw` 里最新的缓存 |
| `run.py build` | 最新一期 → `video.mp4` / `cover.png` / `script.md` / `caption.md`（默认 `--crf 16`） |
| `run.py build <slug>` | 指定一期 |
| `run.py status` | 每期进度：草稿 / 待出片 / 已出片，附时长体积 |
| `run.py all` | new + build，草稿没补齐会自动停下 |
| `run.py sync` | 打包成 ima 能收的形态（文案 md + 口播 mp3）到 `outbox/` |

草稿不完整时 `build` 会拒绝出片并告诉你缺什么（`scenes` 为空、还有 `TODO`、某句缺 `caption`），
不会产出半成品视频。

### 也可以直接在对话里说

项目里带了一个技能 `x-finance-daily`（`.workbuddy/skills/x-finance-daily/SKILL.md`），
下次在 WorkBuddy 里说 **「生成今天这期」** 就会自动走完：选题 → 写文案 → 出片 → 给发布清单。
文案规范和红线（只讲机制、不给点位、数字必须有出处）都写在那份技能里，不用每次重复交代。

### 定时自动（每天 10:00 / 17:00）

两条 WorkBuddy 定时任务，无人值守跑完「选题 → 文案 → 出片 → 同步 ima → 存视频号草稿」：

| 档位 | 时间 | 目录 |
| --- | --- | --- |
| 早间 | 每天 10:00 | `episodes/<date>-am` |
| 晚间 | 每天 17:00 | `episodes/<date>-pm` |

`run.py new` 按当前小时自动决定 `-am` / `-pm`，同一天两期不会互相覆盖；
已做过的推文（`episode.json` 的 `seed.url` + `sources.md` 里的链接）会被自动排除，
晚间不会撞早间的题。

跑完产出：

```
episodes/2026-11-19-am/
  episode.json  video.mp4  cover.png  script.md  caption.md  sources.md  publish.md
```

**同步到 ima**：目标知识库是「视频号」（`ima_kb_0eJCBmd6`），每期两条：

```
2026-11-19 早间 | <标题>.md          ← 发布文案 + 口播全文 + 分镜 + 资料出处
2026-11-19 早间 | <标题> · 口播音频.mp3
```

视频文件**进不了 ima**——`create_media` 的类型白名单里没有 mp4，`video/mp4` 和
`application/mp4` 都返回「不支持的 content_type」。所以成片留在本地，发布时手动上传视频号。

上传是三步：`create_media`（拿 presigned_url）→ `scripts/ima_put.py`（PUT 到 COS）→
`add_knowledge`（入库）。中间那次 PUT 是纯 HTTP，脚本能做；前后两步只能走 MCP 工具。

### 同步资料库「视频号」文件夹

ima 收不了 mp4，所以成片另外往 **WorkBuddy 资料库** 里放一份：

- 空间 `UUGDZxshAy0VnBbed3UzX9`（个人空间根）
- 文件夹「视频号」`LcZOtSS6zEcGIta7NkIK5y`

每期三件，文件名都带日期和档位：

```
2026-11-19 早间 | <标题>.mp4           归档版（≤4MB，高清原片留本地）
2026-11-19 早间 | <标题> · 封面.jpg     160KB 左右
2026-11-19 早间 | <标题>.md            文案 + 口播 + 分镜 + 来源
```

**审核是这条路上唯一的变数。** 实测（2026-11-18）：

| 文件 | 结果 |
| --- | --- |
| 文案 md | 一直能过 |
| 封面 png 1.6MB | 被拦（`did not pass content moderation`） |
| 封面 jpg 160KB | 过了 |
| 成片 11.1MB | 被拦 |
| 成片 6.6 / 4.3MB（黄金那期） | 过了 |
| 成片 3.5MB（芯片那期，带语音） | 被拦 |
| 同一文件去掉音轨 3.7MB | 过了 |
| 同一文件去掉音轨 2.7MB | 又被拦 |

两条结论：**体积越大越容易被拦**（4MB 以内稳一些）；**语音内容会被 ASR 判**——
带"个股 + 财报 + 指引"这类表述容易触发，去掉音轨就没问题。同一个文件重复传，
结果还会飘，所以别拿它当可强制的产出。

脚本给的兜底：`pack_ima.py` 除了有声归档版，还会顺手做一份 `_归档_无语音.mp4`；
自动化里先传有声版，被拦就换无语音版试一次，再不过就跳过——只记一行「未过审」，不硬试。

### 存进视频号草稿箱（不点发表）

发布页那张表单在 iframe 里，bsk 的 click 会被「目标被遮挡」拦死——这条路走不通。
改用 [dreammis/social-auto-upload](https://github.com/dreammis/social-auto-upload)：它用
patchright 遍历所有 frame 找 `input[type=file]` 再 `set_input_files`，能过；而且原生支持
`is_draft=True`，最后点的是「保存草稿」而不是「发表」。

一次性安装（约 500MB，落在项目内并已 gitignore）：

```bash
bash scripts/setup_sau.sh          # 克隆 vendor、建 .venv-sau(3.12)、装 chromium 到 .browsers
bash scripts/push_draft.sh --login # 扫码一次，cookie 存 vendor/cookies/tencent_doge.json
```

之后每期：

```bash
bash scripts/push_draft.sh                 # 最新一期
bash scripts/push_draft.sh 2026-11-19-am   # 指定一期
bash scripts/push_draft.sh <期> --dry-run  # 只打印将要提交的内容
bash scripts/push_draft.sh <期> --show     # 弹出浏览器，盯着它跑
```

它会自动完成：上传 `video.mp4` → 描述（首行标题句 + 正文 + 话题）→ 短标题 →
3:4 封面 `cover.png` → 视频标注「含AI生成内容」→ 点「保存草稿」。

它**不会**做的两件事：**点发表**（留给你），以及**清空位置**——如果上一次发布留过
定位，草稿里可能带着，发表前自己确认一下。

跑不起来时先看这两点：

- 必须用 `scripts/push_draft.sh` 入口。WorkBuddy 注入的 `PYTHONPATH` 里有 sandbox
  `sitecustomize`，会让三方库的 `mkdir(exist_ok=True)` 抛 `EEXIST`，脚本靠 `env -u PYTHONPATH` 绕开。
- 浏览器必须装在项目内 `.browsers`（`PLAYWRIGHT_BROWSERS_PATH`），装到 `~/Library`
  会被沙箱的 safe-delete 拦在最后一步 `__dirlock` 上。

cookie 失效时会明确报「cookie 已失效」，重跑 `--login` 扫一次即可。

### 推送到微信（暂缓，未接入自动化）

`scripts/notify.py` 能把一期的文案、封面、成片推到微信（自建应用可推视频，群机器人只能推文字 + 封面），
入口 `python3 scripts/run.py push [期目录]`，凭据填 `config.json` 的 `notify.wecom`。

**当前没接进定时任务**，原因：整条产线（ffmpeg 渲染、edge-tts、素材）都跑在本机，电脑不开机时连片都出不来，
推送通道再稳也没内容可推。要真正做到「手机随时能收」，得先把产线搬上常开的服务器，参见下面「云端化」。

在那之前，成品的云端副本已经有两份，手机端可看：ima「视频号」知识库、资料库「视频号」文件夹。

### 云端化（待评估）

若要让产线脱离本机，思路是搬到常开的机器（现有腾讯云 `112.74.95.74` 已在跑 bnapp 的刷新 cron）：
装 python3 + ffmpeg + edge-tts，放一份 assets，用 cron 定时跑 `run.py`，出片后由服务器直接推送。
需要解决的三件事：素材同步（3.5GB）、选题抓取要走代理、以及 WorkBuddy 侧的 ima / 资料库 / LibraryUpload
都是 MCP 工具，服务器上没有，得改成用 API key 走 HTTP。工作量不小，先不动。

### 分步跑（调试用）

```bash
pip install pillow imageio-ffmpeg edge-tts

python3 scripts/fetch.py                          # 拉 48 小时推文
python3 scripts/curate.py --top 20 --detail       # 一级粗筛（--detail 看分数构成）
python3 scripts/cluster.py --top 12               # 二级聚类 → data/shortlist/
python3 scripts/run.py new --cluster 1            # 用第 1 个事件建草稿
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
