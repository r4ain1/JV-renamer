# JAV Renamer Skill

自动化提取影片番号与泛品类资源（AI换脸 / 国产传媒 / 里番3D / 欧美），聚合 JavBus + r18.dev 多源官方元数据，支持外部正文上下文融合（如 Telegram 消息正文 `_context.json`）与多模态视觉抽帧识别（按固定间隔或均匀抽帧评估场景、特色、剧情及出镜明星），批量规范重命名并输出索引清单。

> 当前版本：`v2.2.0`

## 适用场景
- 各种网盘、本地磁盘中杂乱命名的番号视频文件整理。
- 去除各种发布组/论坛杂质前缀（如 `[Thz.la]`、`hhd800.com@`、`3xplanet_` 等）。
- Telegram / 网盘抓取的无语义命名视频（`protected_content_*`、`6月26日(4)`、`[杂项]` 等）结合正文上下文或抽帧视觉识别自动命名。
- 统一重命名为标准化格式：`[番号] 中文标题 [女优] [标签1·标签2·标签3].ext` 或对应泛品类模板，并自动同步重命名同名外挂字幕。

## 文件结构
- `SKILL.md`: 技能定义与执行规范说明
- `scripts/jav_rename.py`: 核心自动化流水线脚本（支持标准 JAV 刮削、泛品类 LLM 命名、`_context.json` 上下文注入与 `--vision-fallback` 视觉兜底）
- `scripts/vision_rename.py`: 无语义/`[杂项]` 视频抽帧视觉识别脚本（支持 `--interval` 按秒间隔抽帧、`--frames` 均匀抽帧、无 Pillow 时自动回退 `ffmpeg tile` 拼图）
- `scripts/recursive_rename.py`: 递归批量处理多级子目录的封装脚本

## 使用方法

```bash
# 预览模式（仅展示前后对照，不改动文件）
python scripts/jav_rename.py --dir "D:\Videos"

# 确认无误后执行
python scripts/jav_rename.py --dir "D:\Videos" --apply

# 结合外部上下文（如 TG 消息正文 _context.json）+ 无语义/[杂项] 自动抽帧视觉兜底
python scripts/jav_rename.py --dir "D:\Videos" --context-json "_context.json" --vision-fallback --vision-interval 15 --apply

# 单独对无语义或已归入 [杂项] 的视频按间隔抽帧做视觉识别
python scripts/vision_rename.py --dir "D:\Videos" --interval 15 --max-frames 12 --include-misc --apply
```

## 版本历史
- `v2.2.0`: 支持外部正文上下文融合（`--context-json`）、`jav_rename.py` 自动视觉兜底（`--vision-fallback`）、`vision_rename.py` 固定间隔抽帧（`--interval`）与场景/特色/剧情/明星多维评估、`--include-misc` 重扫，以及无 Pillow 环境下纯 `ffmpeg tile` 拼图回退。
- `v2.1.0`: 新增无语义文件视觉识别（`vision_rename.py`）、损坏视频标记与递归批处理文档。
- `v2.0.0`: 合并泛品类（AI换脸 / 国产传媒 / 欧美 / 里番3D）处理流水线与字幕同步重命名。
