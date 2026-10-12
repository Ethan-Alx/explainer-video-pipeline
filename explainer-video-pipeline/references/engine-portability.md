# 原生草稿引擎与迁移说明

本目录提供原生写入与独立读回检查代码。主题、旁白长度、字幕数量、分镜数量、IP、生图渠道和旧样稿 ID 均不是运行前提。心理学、历史、植物或动物主题共用同一引擎；事实和视觉准确性由对应项目审核负责。

## 运行边界

- 引擎在本机生成剪映草稿 JSON 和独立素材，不操控剪映界面、不自动联网、不调用付费生图 API。
- 图片、透明 PNG、原生文字、原字幕、旁白、音效独立轨道。不得拿合成兼容测试音当成正式音效。
- 有 X 运镜的剪辑同时写入同时间节点的 Y 通道，Y 即使恒定也保留；已有非恒定 Y 不改成常数。本规则来自旧机的实播对照，新机仍需实播。
- 共享注册 PNG 保留整张 RGBA 画布，同组尺寸和摆放相同；不会把组织层逐张透明边裁切后重心错位。
- 短淡出使用本地透明度关键帧，滑动/遮挡使用本地素材和关键帧；无线上转场资源下载依赖。
- 动态 X/Y、图层、字幕、图片哈希、时长、图层碰撞、素材引用和声画事件对时由结构检查覆盖。结构通过不代表审美、科学准确性、剪映字体渲染、实际平滑度或听感通过。
- 包内免费兼容样例使用程序绘制的几何物体；成片应使用本频道实际批准的成图与连续样本。

## 依赖与路径

使用 Python 3.12 或以上，推荐当前Codex bundled runtime或本片独立环境，不使用已废弃分享包解释器。以下相对命令均在本技能根目录执行（含requirements.txt和scripts的explainer-video-pipeline目录），不是任意工作区根；或将脚本/requirements路径改为已核实的绝对路径。执行：

```powershell
python -m pip install -r requirements.txt
python scripts/pipeline.py doctor
```

`vendor/` 保留 Apache-2.0 的 pyJianYingDraft 0.3.0 项目验证快照与完整许可。库初始化移除了 Windows GUI 控制器的自动导入，草稿写入不使用 GUI 自动控制。未附带 Python 环境、用户素材、凭证、浏览器会话或 FFmpeg 大文件。

Windows 字体按 `WINDIR/Fonts` 发现，优先微软雅黑，再尝试 Arial/Segoe UI；也可设置 `EXPLAINER_VIDEO_FONT` 指向本机已安装 TTF/OTF/TTC。`doctor` 会报告字体名及“中文心”三个汉字的实际字形是否不同；这不证明完整 Unicode 覆盖，缺字时应指定真实 CJK 字体。该字体用于保守文本边界测量，原生编辑器仍须实际核查。工具箱不再绑定作者的盘符、用户目录或字体路径。

`doctor` 实际检查 FFmpeg 的发现来源和 `-version` 输出，默认标记为可选，缺失不会让纯图片/WAV 草稿检查失败。首镜是视频且需要提取封面，或后续要求本地代理视频/导出时需要 FFmpeg；可设置 `EXPLAINER_VIDEO_FFMPEG` 或使用 PATH 中的 `ffmpeg`。需要把它作为必需依赖检查时运行 `python scripts/pipeline.py doctor --require-video-tools`。这一检查不证明剪映原生导出已运行。

## 从真实输入建立项目

下面以心理学频道为例；是否添加--no-subtitles按本人的字幕偏好，示例不强迫无字幕。已有项目只续做不init，已有档案用自己的配置。

```powershell
python scripts/pipeline.py init --project "D:/Projects/Episode01" --name "My Episode" --audio "D:/Inputs/voice.mp3" --srt "D:/Inputs/captions.srt" --manuscript "D:/Inputs/script.txt"
python scripts/project_memory.py init --project "D:/Projects/Episode01" --profile psychology
```

默认 1920×1080、30 fps，可传 `--width --height --fps`。原始文件按字节复制并保存 SHA，不改变原 SRT 词句和时间。字幕最多两行，英文按词间空白选择断点，中文等 CJK 字幕允许在合法字间换行并避开常见行首关闭标点。原生读回会区分中文排版换行与英语词间空白，不把额外空格塞入中文词句。字体和两行宽度仍装不下时构建明确失败，不偷偷删词或缩小字体。

接着按照本片已批准的分镜与画风写 `storyboard.json`，每镜给 `scene_id`、完整 `prompt`、`cue_start`/`cue_end` 和必要的相机、前景、保护区设计。原 cue 必须连续恰好归属一次。相邻 cue 间的空隙由对应主画面连续覆盖，末镜保留到原音频尾。

```powershell
python scripts/compile_storyboard.py compile --project "D:/Projects/Episode01" --storyboard storyboard.json
python scripts/compile_storyboard.py bind-images --plan "D:/Projects/Episode01/production_plan.json" --queue "D:/Projects/Episode01/image_jobs.json" --output "D:/Projects/Episode01/production_plan.ready.json"
```

`bind-images` 读取实际任务记录而不是提交生图；它要求任务已真实完成、下载文件与冻结哈希对应。制作任务记录由真实生图工作流提供，不按下载顺序猜镜号。也可以在已经校验来源映射的 ready 计划中直接绑定实际路径与 SHA。

生活隐喻需明确标作隐喻，科学模型和事实图解需自己的来源与审核记录。通用脚本 `science_check.py` 只检查账本关联和声明状态，不替代事实审核：

```powershell
python scripts/science_check.py --plan "D:/Projects/Episode01/production_plan.ready.json" --report "D:/Projects/Episode01/science-review.json"
```

## 编辑与校验

```powershell
python scripts/pipeline.py validate --plan "D:/Projects/Episode01/production_plan.ready.json" --report "D:/Projects/Episode01/plan-check.json"
python scripts/pipeline.py build --plan "D:/Projects/Episode01/production_plan.ready.json" --output "D:/Deliverables/Episode01_v1"
python scripts/pipeline.py validate --draft "D:/Deliverables/Episode01_v1"
```

新版本必须给新输出目录。`build` 拒绝覆盖已有草稿。文件检查会拒绝缺失源文件、SHA 改变、时间缺口、越框、未经说明的重叠、字幕危险区、共享注册层尺寸不一致、缺 Y 的 X 动画和声画事件对时偏离。

同一静态源图跨镜无说明重复默认拒绝。若确有机制解释采用同一底图，须提供与实际 PNG、布局、时间和状态匹配的 `mechanism_registry_file`，且新状态确实有新的对象几何关系。单纯缩放、换字和换颜色不算新的机制状态。

全景→特写→回到全景、明确的语义回看或有意对照可使用另一类例外：在**每次重复的当前镜头**写 `reuse_review`，提供非空理由、审核者、准确的原图 SHA、关联镜号/时间范围，以及实际审阅证据文件及 SHA。记录只对这些镜头和这些源像素有效；更换素材或时间范围后必须更新审核。脚本将其单独报告为 `reviewed_semantic_reuses`，始终保留 `new_mechanism_state: false`，不会把推拉镜头包装成机制新状态。工具只核验记录与证据字节，无法代替审核者的审美判断。

```json
{
  "reuse_review": {
    "status": "reviewed",
    "kind": "return_to_establishing",
    "reason": "After the object detail, return to the room to explain its relationship to the whole scene.",
    "reviewer": "actual reviewer name or recorded role",
    "scope": {
      "shot_ids": ["S001", "S003"], "start_us": 0, "end_us": 12000000,
      "source_sha256": "SHA256_OF_THE_ACTUAL_SHARED_SOURCE_IMAGE"
    },
    "evidence_file": "review/S001_S003_actual_contact.png",
    "evidence_sha256": "SHA256_OF_THE_ACTUAL_REVIEW_EVIDENCE_FILE"
  }
}
```

`kind` 可为 `detail_cutaway`、`return_to_establishing`、`semantic_recap` 或 `deliberate_comparison`。示例 SHA 必须换成真实值，不得照抄占位符。证据会随草稿复制，迁移后继续验证。长镜头是否需要更多细节图仍要项目节奏审核；不得把构建通过当作镜头设计通过。

PNG 前景的关键字段：

```json
{
  "id": "MG_tissue", "type": "png", "asset": "mg/tissue.png",
  "start_us": 1000000, "end_us": 4000000, "layer": 1,
  "purpose": "show the selected structure", "bbox_px": [1000, 200, 1450, 700],
  "content_fit": "shared_registered_canvas_no_independent_alpha_fit",
  "preserve_registered_canvas": true, "registration_group": "one_cross_section",
  "animation": {"entry_us": 150000, "exit_us": 100000, "move_y_px": 0}
}
```

注册组中的所有层必须使用相同 RGBA 尺寸、相同布局框。`bbox_px` 指画布布局框，不是每层的有色像素范围。经过设计审核的同组重叠需要显式 `allow_overlap_with`；缺省情况下重叠会报错。原生文字不支持此引擎中的 alpha 曲线，采用时间段显隐及位置/缩放，不伪造文字透明度功能。

## 搬家与安装

草稿里会保存原始媒体副本、制作后的媒体、源 SRT/音频、计划快照和清单。正常交付必须传整个目录。剪映 JSON 中仍需绝对路径，因此直接改目录名后要显式重写路径：

```powershell
python scripts/pipeline.py relocate --draft "D:/Deliverables/Episode01_v1" --output "E:/Transfer/Episode01_v1"
python scripts/pipeline.py validate --draft "E:/Transfer/Episode01_v1"
```

`relocate` 复制到新目录、重写路径并复核，保留原目录。如果已经从另一台机器直接复制过来，该命令根据草稿记录的旧根路径和完整媒体副本修复新副本，然后独立检查全部原生内容；旧机器无需在线。不要直接把含旧盘符的 JSON 丢进剪映目录。

确认本机真实草稿库和索引路径后，先预检：

```powershell
python scripts/pipeline.py install --draft "D:/Deliverables/Episode01_v1" --library "E:/MyJianyingDrafts" --index "C:/Actual/Projects/com.lveditor.draft/root_meta_info.json" --name "My Episode v1" --dry-run
```

`--dry-run` 不创建文件、不改索引、不要求关闭正在编辑的工程；输出计划路径和校验结果。正式导入使用完全相同参数去掉 `--dry-run`，需要剪映保存关闭。它生成新 ID、新目录，保存旧索引备份，在写入前再检查编辑器进程及索引是否发生变化；拒绝同名、覆盖、路径越界和写入期间的索引竞争。它不打开剪映，也不会假称已经在首页看见。

## 免费兼容样例与维护测试

```powershell
python scripts/make_compat_sample.py --project "D:/Projects/Compatibility01" --build
python tests/portable_smoke.py --workspace "D:/Tests/Smoke01"
python tests/reuse_and_cjk.py --workspace "D:/Tests/ReuseAndCJK01"
```

第一条生成 12 秒、三字幕、三张不同几何图、两个共享注册层、一个原生标签、一条低音量测试音和短转场。无网络、无生图积分，手动实播后按实际记录通过/失败。若目标编辑器仍抖动，保留失败结果，不能直接复用旧电脑的通过标记。

第二条实际执行 CLI：doctor、build、两类 validate、迁移、把源项目临时改名隐藏后迁移校验、修复带旧根路径的直接复制草稿、删除 Y 通道的负测、暂时隐藏素材的负测、已有目标拒绝覆盖、假索引 dry-run。测试只操作一个新建的测试目录，验证 dry-run 前后所有文件哈希不变，不触碰真实剪映索引。报告保留 `native_visual_verified: false`，测试通过不会把这字段改为 true。

第三条要求本机有真实 CJK 字体，补测有实际记录的回看允许、无记录回看拒绝、证据改变拒绝、回看证据随草稿迁移、42 字中文在两行内完整构建，以及缺少可选/必需 FFmpeg 的不同诊断。


## V7 标注与物料机制

V7 核心包含源像素锚点、父级仿射变换、真实字形和透明轮廓检查、精确元素对与时段的接触例外，以及离源—接触—留存—累积状态。使用前读 [对应契约](v7-annotation-and-material.md)。这是可执行结构约束，仍须看实际叠加状态与连续动作。

## 7.1新增能力

`init --no-subtitles`保持原SRT并保存`render_subtitles:false`；通用默认仍支持有字幕频道。需要无旁白字幕的频道按自己的档案关闭显示，结构校验同时报告visible cues与source cues。`compile_storyboard`可接受明确布尔策略。

原生文字可设置`native_font_file`、`native_font_sha256`与`font_file`；字体文件复制到`media/fonts`，文字材料嵌套JSON的路径随relocate/install重写，迁移比较核字节哈希。CTA的`artwork_provenance.source_file`同样打包核字节，不按路径字符串误报。

全局`--compact`减少返回日志；配合`--result-report`保存任意命令的完整结果，validate也可用其`--report`。精简只影响输出，不跳过检查。
