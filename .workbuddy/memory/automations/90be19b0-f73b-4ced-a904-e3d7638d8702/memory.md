# 自动化执行记忆 · 《超级DOGE · 金融一分钟》晚间档

## 2026-11-18 12:42（晚间档 / pm）
- 期目录：`episodes/2026-11-18-pm`（早间为 `2026-11-18-gold-vs-rates`，两者互不冲突）
- 选题：seed @YaoXiao46714（隔夜美股+费半大跌 / SemiAnalysis 下修 AI 资本开支），评分 17.1
- 标题：AI 芯片股跌的不是业绩，是资本开支；角度走「AI 资本开支 → 算力链订单 → 估值倍数」，与早间「黄金 vs 实际利率」错开
- 结果：78.3s / 10.4MB / 10 句，`status` 显示已出片
- 首跑 90.2s 超 90s 上限（10 句各 8.4–10.3s，数字串拖长 TTS），压缩每句至 34–45 字后降到 78.3s —— 经验：口播里连续大额数字（万亿/千亿）念得慢，一条最多放一组
- ima「视频号」(ima_kb_0eJCBmd6)：md + 口播 mp3 两个文件三步走（create_media → ima_put.py → add_knowledge）全部成功，duplicate=REPLACE
- create_media 返回体顶层就是 `media_id` / `upload_url`，可直接原样写 /tmp/ima_job.json 交给 ima_put.py
- 未做：视频号「发表」需人工，成片与封面留在本地

## 2026-11-18 13:27（晚间档 / pm 重跑）
- 期目录 `episodes/2026-11-18-pm` 已存在 → 按规则不覆盖，只做自检 + 同步
- 自检通过：已出片 78.3s / 10.4MB / 10 句 / episode.json 无 TODO
- ima 重跑幂等成功：2/2（md + 口播 mp3），DUPLICATE_NAME_STRATEGY_REPLACE
- 资料库「视频号」：md 已存在（同名 doc 节点，跳过）；video 主文件与无语音 fallback **均「did not pass content moderation」**，封面 jpg 同样被拦；按规则各只试两次即停，不换码率、不改文案
- 教训：`space_api.py` 不在项目内，在 `/Users/dzp/.workbuddy/plugins/cache/workbuddy-builtin/skill-library/<ver>/space_api.py`，需用该绝对路径调用
- 现象待观察：同一条目此前（12:47）md 可过审入库，本次 mp4/jpg 连续被拦，疑似视频号素材审核对画面/语音更严，非项目侧可解

## 2026-11-18 17:00（晚间档 / pm 第三次触发）
- 期目录 `episodes/2026-11-18-pm` 仍存在 → 跳过 1–3 步，只做自检 + 同步（未重跑 new/build）
- 自检通过：已出片 78.3s / 10.43MB / 10 句 / 无 status 字段 / 无 TODO
- ima 3/3 步骤全通，2 文件（md 8634B + mp3 1258621B）均 REPLACE 入库
- 资料库「视频号」：list-nodes 查到已有同名文案 doc 节点 `WqKzFJ5bZd0xkcgfx1klgA` → 跳过，重跑无重复；video 主文件 + 无语音 fallback + cover.jpg **三者全部 content moderation 被拦**，各按上限两次内尝试后停止
- 结论：mp4/jpg 被拦已成稳定现象（12:47 md 可过、13:27 与 17:00 的 mp4/jpg 均被拦），判定为平台侧对视频/图片素材的审核限制，产线侧无法解决，需人工直接发视频号
- 有效路径：`space_api.py` 实际版本目录为 `skill-library/5.11.3-wb.43806934.gecace0f7.h4094f2d5641e/`
