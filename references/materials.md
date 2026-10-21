# 基础材质流程

## 工具与入口

`scripts/auto_materials.py` 的 `convert_materials` 默认选择 `original_file`，转交 `original_materials.py` 计划并绑定原始图片。`prepare_model` 的材质阶段使用相同默认值。`bake` 为显式可选策略，由纹理 worker 处理图像；两种策略均通过实际 FnMaterial 主纹理槽验证。

综合入口自动使用骨架工作副本。只处理已有的明确工作网格时：

```python
from pathlib import Path
import runpy
materials = runpy.run_path(str(Path(SKILL_DIR)/'scripts/auto_materials.py'))
prep = runpy.run_path(str(Path(SKILL_DIR)/'scripts/prepare_model.py'))
report = materials['convert_materials'](
    WORK_MESH_NAMES, OUTPUT_DIR, work_copy=True,
    mmd_package=ACTIVE_MMD_PACKAGE, texture_policy='original_file')
validation = prep['validate_materials'](WORK_MESH_NAMES, mmd_package=ACTIVE_MMD_PACKAGE)
```

网格必须已复制且 data 单用户、Object Mode、Data-linked 槽，并有有效 UV。单独处理材质不代表源骨架已准备好。执行前重新读取材质、图像内容指纹和槽，变化时拒绝旧计划。保持材质槽顺序、共享关系和面分配，不合并网格或重排面。

## 默认：直接使用原始贴图文件

从启用 MToon 的有效主图、已有正式 MMD 主图，或有效 Blender 输出的基础色图片定位原图。已有烘焙材质优先读取其保存的源图元数据，不把处理中间图片默认为原图。相对路径通过图片的 library 与当前 `.blend` 位置解析，确认文件存在，再绑定为绝对路径；不能任意搜索同名文件替换来源。

对工作网格建立独立材质，并从同一原文件创建独立图片数据块，写入 `FnMaterial.create_texture` 建立的 `mmd_base_tex`。这样无需修改源材质、共享图片路径或原始图片文件。保持槽顺序、面分配和 root 内共享关系，必要时同步 Material Morph 的材质引用；失败回退。

扩散色固定 `(1,1,1)`，反射色固定 `(0,0,0)`，环境色固定 `(0.5,0.5,0.5)`，轮廓关闭，Sphere/Toon 不分配。保留明确的材质 Alpha、双面及原图片 Alpha。默认不合成 shader 颜色因子、独立透明遮罩、Mapping 或其他着色效果，不宣称与原 shader 视觉等价，报告记录这些边界。

文件缺失、纯色/Generated 图片等没有原始文件的材质，明确报告具体项；不能静默切换为烘焙或猜其他贴图。用户明确选择 `texture_policy='bake'` 后才生成纯色/处理图片。路径和颜色字段必须在重复运行时重新验证；损坏的已准备材质在独立副本修复。

## 可选：texture_policy='bake'

以下颜色因子、UV 重采样、遮罩合成与图像提取规则仅适用于显式选择的烘焙策略。调用 `convert_materials(..., texture_policy='bake', texture_python=HOST_PYTHON)`；低层 `plan_materials/execute_plan` 也是这个可选处理路径，不能用于替代默认原图绑定。


| 源数据 | 处理方法 |
|---|---|
| 启用 MToon 的主图/颜色 | 读 VRM 插件有效结构化属性；不猜显示节点或节点名 |
| 主图 RGB × 颜色因子 | 转到线性 RGB 相乘，输出 sRGB PNG；目标 Diffuse 默认白，避免重复乘色 |
| 无主图的纯色 | 生成小尺寸纯色主图，保留统一目标色约定 |
| OPAQUE | 输出图 Alpha=1，材质 Alpha=1 |
| MASK | 比较贴图 Alpha×因子与 cutoff，烘焙二值遮罩；材质 Alpha=1 |
| BLEND | 保留贴图 Alpha；因子只写材质 Alpha |
| 双面 | 写 MMD 双面标志，并同步 Blender 背面剔除 |
| 扩散色 Diffuse | 跨角色默认固定 (1,1,1)，源颜色因子烘焙进主图，不保留插件默认 0.8 |
| 反射色 Specular | 跨角色默认固定 (0,0,0) |
| 环境色 Ambient | 跨角色默认固定 (0.5,0.5,0.5)，不保留插件默认 0.4 |
| 轮廓 | 始终关闭、宽度为零，不处理遮罩，不写顶点描边权重 |
| Shade / Toon / MatCap / Sphere / Rim | 不生成、不挂接，源不受影响；报告说明省略 |
| Emission / Normal / 动态 UV / PBR | 记录不能表达的部分；不添加外部 shader 补偿 |

图片因子不是把 VRM shader 数值直接塞进同名 PMX 字段。Alpha 不做 sRGB 解码。UV 变换在主图重采样，支持 repeat/clamp/mirrored repeat；UV 超出单位 tile 且带非默认变换/sampler 时拒绝自动处理，避免把单 tile 烘焙误当成全 UV 域等价。独立 sampler 及跨贴图 UV 集不由 PMX 基础材质表达，需检查实际 UV 范围与采样约定。超 LDR 的裁切在报告中列为近似。静态重采样不意味着保留动态 UV。

未启用 MToon 时，由 `blender_material_source.py` 从唯一活动输出追踪实际连线，支持：

- 图片 Color 直接接 Surface：以该图片作主图，不读取断开的 Principled 常量或其他未生效图片。原 Color-to-Surface 未使用图片 Alpha 时，按实际连线保留 OPAQUE，不擅自推断透明。
- Principled：常量/直接图片的 Base Color 与常量、图片 Alpha 或灰度不透明度图。
- 单位强度 Emission：保留颜色或图片作为主图，光照效果省略。
- 一个白色 Transparent 分支与一个颜色分支的 Mix Shader：将有效 Factor 的灰度 RGB/Alpha 烘焙进主图 Alpha；分支相反时反转遮罩。彩色 Factor 不猜测隐式灰度转换，拒绝并要求显式适配；断开的 Mix 不影响当前主图。

图片支持普通 UV 或命名 UV，以及单层静态 POINT Mapping 的二维位移/缩放/Z 旋转。命名 UV 必须等于每个使用网格的活动 UV，避免 Blender 用 UVnew、PMX 却导出 UVold。复杂 shader 混合、嵌套透明网络、Generated/Object 坐标、动画 Mapping、其他 projection/sampler 仍需适配，不任意选择节点或摊平 shader。

在显式烘焙策略下，Generated、浮点或未保存编辑图像提取当前像素为 NPY，避免 Image.copy/save 重载旧图或重复颜色变换；干净图片优先提取文件/packed 原字节。源贴图不覆盖，每次写唯一 `run-…` 目录。

## 重复运行、回退与例外

新材质保存 `pmx_prep_signature`、源设置、期望 Alpha/双面/主图引用，并保留源材质的可读 MMD 日文名。再次运行跳过已有签名，不重复烘焙。跳过不代表字段未被人工修改，仍调用 `validate_materials`。不自动覆盖人工调过的已准备材质。

失败恢复已替换的槽和材质 Morph 引用，清理本次创建的材质与无用户图片；保留诊断文件。轮廓权重从未修改，不需要恢复。

MToon 轮廓预览修改器在综合骨架副本禁用；识别出的 outline helper 材质跳过并报告。若辅助材质仍被真实面引用，材质验证失败，需在工作副本确认其用途后整理；不按名字批量删除几何或把保留的辅助材质当成完成。

已有 MMD 材质 Morph 的指针在同一工作 root 内更新；若只处理部分网格会改坏根内其他使用者，拒绝执行。引用更新不能证明 Morph 视觉等价。VRM expression 到 PMX Morph 的完整迁移不在本阶段。

原图策略只生成路径/颜色与映射报告，不复制或生成纹理文件；可选烘焙产物包括源图片副本、处理主图、worker 输入/结果、`material-conversion-report.json` 和源→目标材质映射。脚本返回 `converted_pending_validation` 仅说明转换执行完成；实际材质验证通过才能交接。

## 防止手工导出后全白

Blender 能显示图片，不代表 mmd_tools 导出器能识别它。材质必须经 `FnMaterial.create_texture` 建立正式 MMD 主图节点，再由相同活动插件的 `FnMaterial.get_texture()` 回读 `type='IMAGE'` 和有效 `image`；`validate_materials` 已检查这一点。无材质转换标记、无导出器可读主图或仅有任意 Image Texture 节点都不能通过。

默认综合入口必须处理骨架和材质。只有用户明确选择只做骨架时使用 `parts='rig'`，结果为 `rig_prepared_materials_pending`；材质异常不能通过退回只做骨架而宣称整个任务完成。已有工作骨架缺材质时，直接补转换工作网格的材质，保留源共享材质和骨架。

用户后续手工导出时，应选报告中的工作模型 root，并开启纹理复制，让原始贴图随 PMX 保存；只有明确采用烘焙策略时复制处理后的贴图。若输出 PMX 的 texture 表为空、各材质 texture 索引均为 -1，说明主图引用没有进入文件；这与路径失效是不同问题。扩散 0.8/环境 0.4 往往表明仍是插件默认材质。对此先核对工作模型、材质阶段和真实导出器槽，不把问题归因于 PMX Editor 配色，也不通过提高颜色补救缺失贴图。

本 skill 的正常准备流程仍不生成 PMX；维护测试可在内存中调用实际导出器材质写入函数，验证贴图索引，避免把只检查普通节点的测试当成导出兼容证明。
