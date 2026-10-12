# 分镜作者格式与真实任务绑定

本文件对应包内 `compile_storyboard.py` 的实际字段。用现有 `plan.json` 作为源音频/字幕/画幅底座，写 `storyboard.json` 后编译为 `production_plan.json`。一张图可以覆盖相邻几个 cue；若同一句中需要细节切换，编译后在生产计划中设计更细的主窗口或前景状态，仍保留原字幕文字与时间。

## 最小作者对象

```json
{
  "shots": [
    {
      "scene_id": "S001",
      "cue_start": 1,
      "cue_end": 2,
      "visual_purpose": "本句需要看懂的具体动作或关系",
      "technique_ids": ["object-closeup"],
      "prompt": "这里应为本镜完整提示词，包含主体、动作、环境、构图、画风和实际需要的限制。不要把此占位句拿去生图。",
      "reference_files": [],
      "camera": {"motion": "static"},
      "incoming_transition": {"type": "cut", "duration_us": 0}
    }
  ],
  "mg": [],
  "sfx": [],
  "methods_adopted": []
}
```

按真实字幕总数继续添加镜头：`scene_id` 为不重复的 S001、S002…；`cue_start`/`cue_end` 从1开始，连续且每条 cue 恰好归属一次。示例只有两条 cue，不能直接套进任意长片。`visual_purpose` 和 `technique_ids` 用于解释取舍，不是科学正确性的证明。

固定 IP 出现时，`reference_files` 记录实际文件，例如 `{"file_path":"references/host.png","purpose":"identity","character_id":"host"}`；风格图单独记录 `purpose:"style"`。编译器检查文件存在并冻结 SHA。主持人不出场时无需强加身份图；模板没有身份参考不等于可省略含固定 IP 镜头的绑定。

顶层 `mg` 和 `sfx` 接受引擎真实字段，时间统一为整数微秒。PNG注册画布、文字与音效写法见 [引擎说明](engine-portability.md) 和可实际执行的 `scripts/make_compat_sample.py`。技术小样的几何图和测试音只说明格式，不能作为正式美术/音效素材。

## 编译与绑定

```powershell
python scripts/compile_storyboard.py compile --project PROJECT --storyboard storyboard.json
python scripts/compile_storyboard.py bind-images --plan PROJECT/production_plan.json --queue PROJECT/image_jobs.json --output PROJECT/production_plan.ready.json
```

编译会保存每镜完整提示 SHA、真实参考 SHA、精确时间轴、分组提示和覆盖报告。`--replace` 仅用于尚未执行生图的计划修订；已有提交或下载的队列需要新修订，不能覆盖后重置任务状态。

`image_jobs.json` 的顶层是 `jobs` 数组，每条对应唯一 `scene_id`，绑定器消费的字段如下（内容由实际任务产生）：

```json
{
  "scene_id": "S001",
  "status": "downloaded",
  "prompt": "必须为实际提交的完整原文",
  "prompt_sha256": "实际原文UTF-8的SHA256",
  "reference_files": [],
  "output": {"file_path": "images/S001.png", "sha256": "真实下载文件SHA256"},
  "provider": "实际渠道",
  "actual_model": "实际选择的模型",
  "actual_task_id": "真实任务或资产关联ID",
  "visual_review": {"status": "pending", "notes": []}
}
```

上述中文占位值不是可通过检查的哈希或真实证据。必须用真实文件和实际执行记录填充。绑定器要求 `status:"downloaded"`；审图状态另写 `visual_review`，绑定状态另存当前计划路径，不把 `status` 改成 `reviewed` 或 `bound` 后继续调用该绑定器。提示原文/参考/下载SHA不一致会拒绝绑定。绑定文件存在仅证明文件对应，仍需实际查看角色、物件、布局与科学关系。

绑定后给真实图片标记保护区与审核状态，再设计 MG 和音效。不要在没有实际图时预先填 `protection.reviewed:true`。科学模型关联自己的来源账本；生活隐喻明确写成解释方式，不能制造脑扫描或研究数字。
