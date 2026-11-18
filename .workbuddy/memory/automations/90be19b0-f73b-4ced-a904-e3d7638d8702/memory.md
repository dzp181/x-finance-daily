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
